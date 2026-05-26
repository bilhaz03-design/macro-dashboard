#!/usr/bin/env python3
"""Small Supabase REST helper for the swing terminal cloud runner.

The scanner should not need a heavy SDK just to persist JSON state. This module
uses Supabase's PostgREST API directly with either a service-role key or the
safer publishable-key + ingest-token path.
"""

from __future__ import annotations

import base64
import json
import math
import os
import socket
import time as time_module
import zlib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class SupabaseError(RuntimeError):
    """Raised when Supabase returns a non-2xx response."""


COMPRESSED_JSON_ENCODING = "zlib+base64+json"
COMPRESSED_PAYLOAD_MARKER = "__swing_terminal_encoding"
DEFAULT_COMPRESS_JSON_BYTES = 5_000_000


@dataclass(frozen=True)
class SupabaseConfig:
    url: str
    key: str
    ingest_token: str = ""

    @property
    def rest_url(self) -> str:
        return f"{self.url.rstrip('/')}/rest/v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _usable_secret(value: str) -> str:
    value = value.strip()
    if not value or value.startswith("PASTE_") or value.endswith("_HERE"):
        return ""
    return value


def config_from_env(*, required: bool = False) -> SupabaseConfig | None:
    url = _usable_secret(os.environ.get("SUPABASE_URL", ""))
    service_key = (
        _usable_secret(os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""))
        or _usable_secret(os.environ.get("SUPABASE_SERVICE_KEY", ""))
    )
    publishable_key = (
        _usable_secret(os.environ.get("SUPABASE_PUBLISHABLE_KEY", ""))
        or _usable_secret(os.environ.get("SUPABASE_ANON_KEY", ""))
    )
    ingest_token = _usable_secret(os.environ.get("SUPABASE_INGEST_TOKEN", ""))
    key = service_key or publishable_key
    if url and key:
        if service_key or ingest_token:
            return SupabaseConfig(url=url, key=key, ingest_token="" if service_key else ingest_token)
    if required:
        missing = [
            name
            for name, value in (
                ("SUPABASE_URL", url),
                ("SUPABASE_SERVICE_ROLE_KEY or SUPABASE_PUBLISHABLE_KEY+SUPABASE_INGEST_TOKEN", key and (service_key or ingest_token)),
            )
            if not value
        ]
        raise SupabaseError(f"Missing Supabase environment: {', '.join(missing)}")
    return None


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def sanitize_json_value(value: Any) -> Any:
    """Return a strict-JSON-safe copy.

    Python's json.dumps emits NaN/Infinity by default, but PostgREST rejects
    those as invalid JSON. Scanner research artifacts can legitimately contain
    non-finite intermediate values, so convert them to null before upload.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): sanitize_json_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_json_value(item) for item in value]
    return value


def _positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _should_retry_http(exc: urllib.error.HTTPError) -> bool:
    return exc.code == 429 or 500 <= exc.code <= 599


def _compress_threshold_bytes() -> int:
    raw = os.environ.get("SUPABASE_COMPRESS_JSON_BYTES")
    if raw is None:
        return DEFAULT_COMPRESS_JSON_BYTES
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_COMPRESS_JSON_BYTES
    return value if value > 0 else DEFAULT_COMPRESS_JSON_BYTES


def maybe_compress_json_payload(payload: Any) -> Any:
    """Compress large JSON artifacts before storing them in scanner_artifacts."""
    safe_payload = sanitize_json_value(payload)
    raw = json.dumps(safe_payload, ensure_ascii=False, default=_json_default, allow_nan=False).encode("utf-8")
    if len(raw) < _compress_threshold_bytes():
        return safe_payload
    compressed = zlib.compress(raw, level=6)
    return {
        COMPRESSED_PAYLOAD_MARKER: COMPRESSED_JSON_ENCODING,
        "raw_bytes": len(raw),
        "compressed_bytes": len(compressed),
        "payload_b64": base64.b64encode(compressed).decode("ascii"),
    }


def decode_json_payload(payload: Any) -> Any:
    if not (
        isinstance(payload, dict)
        and payload.get(COMPRESSED_PAYLOAD_MARKER) == COMPRESSED_JSON_ENCODING
        and isinstance(payload.get("payload_b64"), str)
    ):
        return payload
    raw = zlib.decompress(base64.b64decode(payload["payload_b64"].encode("ascii")))
    return json.loads(raw.decode("utf-8"))


def request_json(
    config: SupabaseConfig,
    method: str,
    path: str,
    *,
    query: dict[str, str] | None = None,
    body: Any | None = None,
    prefer: str | None = None,
) -> Any:
    url = f"{config.rest_url}/{path.lstrip('/')}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"

    data = None
    headers = {
        "apikey": config.key,
        "Authorization": f"Bearer {config.key}",
        "Accept": "application/json",
    }
    if config.ingest_token:
        headers["x-swing-terminal-token"] = config.ingest_token
    if body is not None:
        safe_body = sanitize_json_value(body)
        data = json.dumps(safe_body, ensure_ascii=False, default=_json_default, allow_nan=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if prefer:
        headers["Prefer"] = prefer

    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    timeout_seconds = _positive_int_env("SUPABASE_HTTP_TIMEOUT_SECONDS", 90)
    max_attempts = _positive_int_env("SUPABASE_HTTP_ATTEMPTS", 3)
    for attempt in range(1, max_attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
                raw = response.read().decode("utf-8")
            break
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            if attempt < max_attempts and _should_retry_http(exc):
                time_module.sleep(min(2 ** (attempt - 1), 8))
                continue
            raise SupabaseError(f"Supabase {method} {path} failed: {exc.code} {error_body}") from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            if attempt < max_attempts:
                time_module.sleep(min(2 ** (attempt - 1), 8))
                continue
            reason = getattr(exc, "reason", exc)
            raise SupabaseError(f"Supabase {method} {path} failed: {reason}") from exc

    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def select_rows(
    config: SupabaseConfig,
    table: str,
    filters: dict[str, str] | None = None,
    *,
    select: str = "*",
    limit: int | None = None,
) -> list[dict]:
    query = {"select": select}
    if filters:
        query.update(filters)
    if limit is not None:
        query["limit"] = str(limit)
    rows = request_json(config, "GET", table, query=query)
    return rows if isinstance(rows, list) else []


def upsert_rows(
    config: SupabaseConfig,
    table: str,
    rows: list[dict],
    *,
    on_conflict: str | None = None,
) -> list[dict]:
    if not rows:
        return []
    query = {"on_conflict": on_conflict} if on_conflict else None
    response = request_json(
        config,
        "POST",
        table,
        query=query,
        body=rows,
        prefer="resolution=merge-duplicates,return=representation",
    )
    return response if isinstance(response, list) else []


def upload_json_artifact(
    config: SupabaseConfig,
    *,
    artifact_key: str,
    kind: str,
    payload: Any,
    source_path: str | None = None,
    scan_date: str | None = None,
) -> None:
    stored_payload = maybe_compress_json_payload(payload)
    upsert_rows(
        config,
        "scanner_artifacts",
        [{
            "artifact_key": artifact_key,
            "kind": kind,
            "scan_date": scan_date,
            "updated_at": utc_now(),
            "source_path": source_path,
            "payload": stored_payload,
        }],
        on_conflict="artifact_key",
    )


def upload_json_file(
    config: SupabaseConfig,
    *,
    artifact_key: str,
    kind: str,
    path: Path,
    scan_date: str | None = None,
) -> bool:
    if not path.exists():
        return False
    payload = json.loads(path.read_text(encoding="utf-8"))
    upload_json_artifact(
        config,
        artifact_key=artifact_key,
        kind=kind,
        payload=payload,
        source_path=str(path),
        scan_date=scan_date,
    )
    return True


def upload_text_artifact(
    config: SupabaseConfig,
    *,
    artifact_key: str,
    kind: str,
    text: str,
    source_path: str | None = None,
    scan_date: str | None = None,
) -> None:
    upload_json_artifact(
        config,
        artifact_key=artifact_key,
        kind=kind,
        payload={"text": text},
        source_path=source_path,
        scan_date=scan_date,
    )


def upload_text_file(
    config: SupabaseConfig,
    *,
    artifact_key: str,
    kind: str,
    path: Path,
    scan_date: str | None = None,
) -> bool:
    if not path.exists():
        return False
    upload_text_artifact(
        config,
        artifact_key=artifact_key,
        kind=kind,
        text=path.read_text(encoding="utf-8"),
        source_path=str(path),
        scan_date=scan_date,
    )
    return True


def download_json_artifact(config: SupabaseConfig, artifact_key: str) -> Any | None:
    rows = select_rows(
        config,
        "scanner_artifacts",
        {"artifact_key": f"eq.{artifact_key}"},
        select="payload",
        limit=1,
    )
    if not rows:
        return None
    return decode_json_payload(rows[0].get("payload"))


def download_text_artifact(config: SupabaseConfig, artifact_key: str) -> str | None:
    payload = download_json_artifact(config, artifact_key)
    if isinstance(payload, dict) and isinstance(payload.get("text"), str):
        return payload["text"]
    return None


def restore_json_artifact(config: SupabaseConfig, artifact_key: str, path: Path) -> bool:
    payload = download_json_artifact(config, artifact_key)
    if payload is None:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


def restore_text_artifact(config: SupabaseConfig, artifact_key: str, path: Path) -> bool:
    text = download_text_artifact(config, artifact_key)
    if text is None:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True
