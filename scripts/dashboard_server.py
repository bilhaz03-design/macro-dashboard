#!/usr/bin/env python3
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import json
import math
import sys
import time
from datetime import datetime

ROOT = Path('/Users/bobbo/Desktop/Finans Projects')
DASHBOARD_DIR = ROOT / 'dashboard'
PY_BIN = str(ROOT / '.venv' / 'bin' / 'python')
UPDATE_CMD = [PY_BIN, str(ROOT / 'scripts' / 'update_dashboard.py')]
SCAN_CMD = [PY_BIN, str(ROOT / 'scripts' / 'daily_scan.py')]
SCAN_TIMEOUT_SEC = 300

# Single-flight guard: at most one /scan in flight at a time.
import threading
_scan_lock = threading.Lock()
_scan_state = {
    'status': 'idle',
    'scan_id': None,
    'started_at': None,
    'finished_at': None,
    'returncode': None,
    'tail': [],
    'stderr_tail': [],
    'error': None,
}


class DashboardHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def _now_iso():
    return datetime.now().isoformat(timespec='seconds')


def _scan_state_snapshot_unlocked():
    return dict(_scan_state)


def _scan_state_snapshot():
    with _scan_lock:
        return _scan_state_snapshot_unlocked()


def _run_scan(scan_id):
    try:
        proc = subprocess.run(
            SCAN_CMD,
            capture_output=True,
            text=True,
            timeout=SCAN_TIMEOUT_SEC,
            cwd=str(ROOT),
        )
        tail = (proc.stdout or '').splitlines()[-20:]
        err_tail = (proc.stderr or '').splitlines()[-20:]
        if proc.returncode == 0:
            status = 'ok'
            error = None
        elif proc.returncode == 2:
            status = 'partial'
            error = None
        else:
            status = 'failed'
            error = f'scan exited {proc.returncode}'
        with _scan_lock:
            if _scan_state.get('scan_id') == scan_id:
                _scan_state.update({
                    'status': status,
                    'finished_at': _now_iso(),
                    'returncode': proc.returncode,
                    'tail': tail,
                    'stderr_tail': err_tail,
                    'error': error,
                })
    except subprocess.TimeoutExpired:
        with _scan_lock:
            if _scan_state.get('scan_id') == scan_id:
                _scan_state.update({
                    'status': 'timeout',
                    'finished_at': _now_iso(),
                    'returncode': None,
                    'error': f'scan timed out after {SCAN_TIMEOUT_SEC}s',
                })
    except Exception as e:
        with _scan_lock:
            if _scan_state.get('scan_id') == scan_id:
                _scan_state.update({
                    'status': 'failed',
                    'finished_at': _now_iso(),
                    'returncode': None,
                    'error': str(e),
                })


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            self._json_response(200, {'status': 'ok'})
            return
        if self.path == '/scan/status':
            self._json_response(200, _scan_state_snapshot())
            return
        super().do_GET()

    def do_POST(self):
        if self.path == '/update':
            import os
            expected = os.environ.get('DASHBOARD_TOKEN', '')
            token = self.headers.get('X-Dashboard-Token', '')
            # Only enforce auth if DASHBOARD_TOKEN is configured
            if expected and token != expected:
                self._text_response(401, 'Unauthorized')
                return
            try:
                subprocess.check_call(UPDATE_CMD)
                self._text_response(200, 'OK')
            except Exception as e:
                self._text_response(500, f'Update failed: {e}')
        elif self.path == '/scan':
            self._handle_scan()
        elif self.path == '/api/size':
            try:
                length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(length)
                params = json.loads(body)
            except Exception as e:
                self._json_response(400, {'error': f'Invalid JSON: {e}'})
                return

            # Doctrine §5e.2: no-stop is the operative default. A supplied stop
            # switches this endpoint into risk-based diagnostic mode.
            required = ('ticker', 'entry', 'portfolio')
            missing = [k for k in required if k not in params or params[k] in (None, '')]
            if missing:
                self._json_response(400, {'error': f'Missing fields: {missing}'})
                return

            try:
                ticker = str(params['ticker'])
                entry = float(params['entry'])
                stop_raw = params.get('stop')
                stop = float(stop_raw) if stop_raw not in (None, '') else None
                portfolio = float(params['portfolio'])
                risk_pct_raw = params.get('risk_pct')
                risk_pct = float(risk_pct_raw) if risk_pct_raw not in (None, '') else 0.01
                vol_cap_raw = params.get('vol_cap')
                vol_cap = float(vol_cap_raw) if vol_cap_raw not in (None, '') else 0.04
                var_cap_raw = params.get('var_cap')
                var_cap = float(var_cap_raw) if var_cap_raw not in (None, '') else 0.015
            except (TypeError, ValueError) as e:
                self._json_response(400, {'error': f'Invalid parameter type: {e}'})
                return

            try:
                if str(ROOT / 'scripts') not in sys.path:
                    sys.path.insert(0, str(ROOT / 'scripts'))
                from position_size import (
                    size_position,
                    size_position_conviction,
                    fetch_atr_pct,
                    get_currency,
                    get_fx_rate_to_sek,
                )

                atr = fetch_atr_pct(ticker)
                warning = None
                if math.isnan(atr):
                    warning = 'ATR not available'

                currency = get_currency(ticker)
                fx_rate = get_fx_rate_to_sek(currency)
                entry_sek = entry * fx_rate
                if stop is None:
                    result = size_position_conviction(
                        entry=entry_sek,
                        portfolio=portfolio,
                        atr20_pct=atr,
                        vol_cap_pct=vol_cap,
                    )
                    mode = 'conviction'
                    risk_label = 'Smärttröskel'
                    position_value = result.units * entry_sek
                else:
                    stop_sek = stop * fx_rate
                    atr_for_risk = 0.0 if math.isnan(atr) else atr
                    result = size_position(
                        entry=entry_sek,
                        stop=stop_sek,
                        portfolio=portfolio,
                        atr20_pct=atr_for_risk,
                        max_risk_pct=risk_pct,
                        vol_cap_pct=vol_cap,
                        var_cap_pct=var_cap,
                    )
                    mode = 'risk_based_diagnostic'
                    risk_label = 'Risk'
                    position_value = result.units * entry_sek
                payload = {
                    'units': result.units,
                    'risk_value': result.risk_value,
                    'risk_pct': result.risk_pct,
                    'atr20_pct': result.atr20_pct,
                    'daily_var_pct': 0.0 if math.isnan(result.daily_var_pct) else result.daily_var_pct,
                    'constraint': result.constraint,
                    'mode': mode,
                    'risk_label': risk_label,
                    'position_value': position_value,
                    'currency': currency,
                    'fx_rate': fx_rate,
                    'entry_sek': entry_sek,
                }
                if warning:
                    payload['warning'] = warning
                if stop is None:
                    payload['warning'] = (payload.get('warning') + ' | ' if payload.get('warning') else '') + 'Ingen hård stop; conviction-sizing enligt §5e.2'
                self._json_response(200, payload)
            except Exception as e:
                self._json_response(500, {'error': str(e)})
        else:
            self._text_response(404, 'Not found')

    def _handle_scan(self):
        with _scan_lock:
            if _scan_state.get('status') == 'running':
                self._json_response(409, _scan_state_snapshot_unlocked())
                return
            scan_id = f"scan-{int(time.time() * 1000)}"
            _scan_state.update({
                'status': 'running',
                'scan_id': scan_id,
                'started_at': _now_iso(),
                'finished_at': None,
                'returncode': None,
                'tail': [],
                'stderr_tail': [],
                'error': None,
            })
            worker = threading.Thread(target=_run_scan, args=(scan_id,), daemon=True)
            worker.start()
            payload = _scan_state_snapshot_unlocked()

        self._json_response(202, payload)

    def _json_response(self, code, data):
        body = json.dumps(data).encode()
        self._body_response(code, body, 'application/json')

    def _text_response(self, code, text):
        body = str(text).encode()
        self._body_response(code, body, 'text/plain; charset=utf-8')

    def _body_response(self, code, body, content_type):
        try:
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            # Browser refresh/abort while a scan is starting should not poison
            # the threaded server or leave the scan state locked.
            pass

    def end_headers(self):
        # Local trading terminal assets are generated/edited in place. Force the
        # browser to revalidate every load so Reload cannot run stale JS/HTML.
        self.send_header('Cache-Control', 'no-store, max-age=0')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        super().end_headers()

    def translate_path(self, path):
        # Serve files from dashboard directory and /output subtree only.
        # Reject traversal via robust Path.relative_to checks.
        from urllib.parse import unquote, urlparse

        dashboard_root = DASHBOARD_DIR.resolve()
        output_root = (ROOT / 'output').resolve()

        parsed_path = urlparse(path).path
        rel = Path(unquote(parsed_path).lstrip('/'))
        if str(rel) in ('', '.'):
            return str((dashboard_root / 'index.html').resolve())

        if rel.parts and rel.parts[0] == 'output':
            candidate = (ROOT / rel).resolve()
            try:
                candidate.relative_to(output_root)
                return str(candidate)
            except ValueError:
                return str((dashboard_root / 'index.html').resolve())

        candidate = (dashboard_root / rel).resolve()
        try:
            candidate.relative_to(dashboard_root)
            return str(candidate)
        except ValueError:
            return str((dashboard_root / 'index.html').resolve())


def main():
    server = DashboardHTTPServer(('127.0.0.1', 8000), Handler)
    print('Serving dashboard at http://127.0.0.1:8000')
    server.serve_forever()


if __name__ == '__main__':
    main()
