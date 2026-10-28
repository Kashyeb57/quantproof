"""Offline tests for the app demo logic (quantproof/app/demo.py).

No Streamlit and no UI: these test the decisions the UI renders (demo
catalogue, illustrative defaults, proof-battery gate, verdict assembly)
and that the app-sized pipeline finishes well inside the ~20 s budget.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quantproof.app import demo

STEM = "jpm_424b2-38582"


def test_catalogue_is_the_four_saved_filings():
    assert demo.NOTE_STEMS == (
        "jpm_424b2-38582",
        "jpm_424b2-ea0309017",
        "jpm_424b2-ea0308498",
        "jpm_424b2-ea0306095",
    )
    for stem in demo.NOTE_STEMS:
        assert stem in demo.DEFAULT_INPUTS


def test_38582_defaults_match_the_example_report():
    # Static stated defaults (the Treasury rate and implied vols are
    # layered on by note_defaults; tested separately with fakes).
    defaults = demo.DEFAULT_INPUTS[STEM]
    assert defaults["rate"] == 0.037
    assert defaults["spread"] == 0.006
    assert defaults["vols"] == (0.24, 0.17)
    assert defaults["dividends"] == (0.012, 0.013)
    assert defaults["rho"] == 0.6


def test_note_defaults_layer_implied_vols(monkeypatch):
    import quantproof.market.anchored as anchored_module

    def _fake(names, pricing_date, maturity_date, **kwargs):
        vols = tuple(0.2336 if "Russell" in name else 0.1856 for name in names)
        return (
            {"rate": 0.035515, "vols": vols},
            {"vol_labels": [f"{name}: implied" for name in names]},
        )

    monkeypatch.setattr(anchored_module, "anchored_inputs", _fake)
    note = demo.load_note(STEM)
    defaults = demo.note_defaults(note.stem, note.termsheet)
    assert defaults["vols"] == (0.2336, 0.1856)
    assert all("implied" in label for label in defaults["vol_labels"])


def test_maths_gate_ignores_quotes_but_not_maths_failures():
    results = [
        {"name": "determinism", "passed": True},
        {"name": "quotes", "passed": False},
    ]
    assert demo.maths_passed(results) is True
    results[0]["passed"] = False
    assert demo.maths_passed(results) is False
    assert demo.maths_passed([]) is False


def test_dominant_driver_picks_the_largest_move():
    driver = demo.dominant_driver(
        {"vol": (950.0, 860.0), "spread": (912.0, 902.0), "skew": (906.0, 855.0)}
    )
    assert driver == ("Volatility", 950.0, 860.0)
    assert demo.dominant_driver({}) is None


def test_app_pipeline_for_38582_within_budget_and_priced():
    note = demo.load_note(STEM)
    defaults = demo.DEFAULT_INPUTS[STEM]
    market = demo.build_market(
        note.termsheet,
        rate=defaults["rate"],
        spread=defaults["spread"],
        vols=defaults["vols"],
        dividends=defaults["dividends"],
        rho=defaults["rho"],
    )
    start = time.perf_counter()
    battery = demo.run_proof_battery(note, note.termsheet, market)
    result = demo.run_analysis(note, note.termsheet, market)
    elapsed = time.perf_counter() - start
    # App-sized pipeline: a few seconds locally; the bound below is the
    # ~20 s free-host budget with generous CI headroom on top.
    assert elapsed < 60.0
    assert battery["kind"] == "template"
    assert battery["passed"] is True
    verdict = demo.verdict_summary(result)
    assert verdict["issue_price"] == 1000.0
    assert verdict["bank_estimate"] == 954.0
    assert verdict["low"] < verdict["base_price"] < verdict["high"]
    assert verdict["base_std_error"] > 0  # standard error is reported
    assert verdict["driver"][0] == "Volatility"
    assert result["loss_odds"]["measure"] == "real_world"
    assert len(result["stress"]["prob_loss"]) == len(
        result["stress"]["drift_levels"]
    )
