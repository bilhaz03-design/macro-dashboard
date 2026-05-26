import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from mlpb_prime_utils import PrimeThresholds, prime_tier, qt_prime_lite, qt_quality


def test_qt_prime_lite_outputs_full_framework_fields():
    idx = pd.date_range("2024-01-01", periods=180, freq="B")
    close = pd.Series(np.linspace(100, 130, len(idx)) + np.sin(np.arange(len(idx)) / 4), index=idx)

    out = qt_prime_lite(close, PrimeThresholds(z_len=60, short_mad_len=20, z_mom_len=3, percentile_len=90))

    for col in [
        "QT_Z",
        "QT_Z_DELTA",
        "QT_STRETCH_PCTILE",
        "QT_ABS_STRETCH_PCTILE",
        "QT_PHASE",
        "QT_WAIT_SCORE",
        "QT_WAIT_LABEL",
        "QT_CONFIRMATION",
        "QT_POST_ENTRY_STATE",
    ]:
        assert col in out.columns
    assert out["QT_PHASE"].dropna().iloc[-1] in {
        "EARLY_FALLING",
        "STABILIZING",
        "REPAIRING",
        "SUPPORTED_PULLBACK",
        "CHASING",
        "FADING",
        "NEUTRAL",
    }


def test_qt_prime_lite_marks_warmup_rows_unknown():
    idx = pd.date_range("2024-01-01", periods=80, freq="B")
    close = pd.Series(np.linspace(100, 120, len(idx)), index=idx)

    out = qt_prime_lite(close, PrimeThresholds(z_len=60, short_mad_len=20, z_mom_len=3, percentile_len=70))

    assert out["QT_PHASE"].iloc[0] == "UNKNOWN"
    assert out["QT_WAIT_LABEL"].iloc[0] == "UNKNOWN"
    assert np.isnan(out["QT_WAIT_SCORE"].iloc[0])


def test_prime_tier_requires_qt_phase_and_wait_for_a_plus():
    base = {
        "action_label": "WATCH_PULLBACK",
        "score": 92,
        "setup": "MLPB50",
        "warnings": [],
        "blocks": ["Manual major-news check required.", "Manual Nordnet spread/depth check required."],
        "visual": {"grade": "CLEAN"},
        "hist": {"nextopen_mae21_p10": -0.12},
    }

    assert prime_tier(**base, qt={"label": "QT_SUPPORT", "phase": "REPAIRING", "wait_score": 24}) == "A_PLUS_TRADE_CANDIDATE"
    assert prime_tier(**base, qt={"label": "QT_SUPPORT", "phase": "CHASING", "wait_score": 78}) == "A_WATCH"


def test_qt_quality_blocks_chasing_high_wait_value():
    result = qt_quality({
        "qt_z": 2.1,
        "qt_z_delta": 0.2,
        "qt_stretch_pctile": 0.95,
        "qt_abs_stretch_pctile": 0.98,
        "qt_phase": "CHASING",
        "qt_wait_score": 78,
        "qt_wait_label": "HIGH_WAIT_VALUE",
    })

    assert result["label"] == "QT_BLOCK"
    assert "waiting has high value" in result["reasons"]
