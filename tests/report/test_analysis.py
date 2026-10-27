"""Offline tests for the shared analysis logic (quantproof/report).

The CLI (scripts/analyse.py) and the app share this one implementation;
these tests pin its behaviour directly at the package level with small
path counts (wiring and wording, not pricing accuracy).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quantproof.agent.extract import parse_json_reply, to_termsheet
from quantproof.core.market import MarketInputs
from quantproof.ingest.html import read_filing
from quantproof.report.analysis import analyse, render_report, termsheet_summary
from quantproof.validation.quotes import verify_termsheet

STEM = "jpm_424b2-38582"
FILING = ROOT / "tests" / "fixtures" / "filings" / f"{STEM}.htm"
REPLY = ROOT / "tests" / "fixtures" / "extractions" / f"{STEM}.reply.json"


def _result():
    filing = read_filing(FILING.read_bytes())
    ts = to_termsheet(parse_json_reply(REPLY.read_text(encoding="utf-8")))
    checks = verify_termsheet(ts, filing.text)
    market = MarketInputs(
        as_of=ts.pricing_date.value,
        risk_free_rate=0.037,
        vols=(0.24, 0.17),
        dividend_yields=(0.012, 0.013),
        correlation=((1.0, 0.6), (0.6, 1.0)),
        credit_spread=0.006,
        sources={"all": "test-entered"},
    )
    return ts, checks, analyse(
        ts, checks, market, paths=2000, base_paths=5000, seed=0,
        erp=0.05, battery=False, filing_text=filing.text,
    )


def test_termsheet_summary_flags_only_gearing():
    ts, checks, _result_dict = _result()
    summary = termsheet_summary(ts, checks)
    assert summary["unverified"] == ["downside.gearing"]
    gearing = next(t for t in summary["terms"] if t["path"] == "downside.gearing")
    assert gearing["verified"] is False
    assert summary["estimated_value"] == 954.0


def test_analyse_range_brackets_base_and_measures_stay_separate():
    _ts, _checks, result = _result()
    fair = result["fair_value"]
    assert fair["low"] <= fair["base"]["price"] <= fair["high"]
    assert fair["bank_estimate"] == 954.0
    assert fair["bank_position"] in ("inside range", "below range", "above range")
    assert result["loss_odds"]["measure"] == "real_world"
    assert result["battery"] is None
    json.dumps(result)  # the shared result stays JSON-serialisable


def test_render_report_banner_driver_and_no_advice():
    _ts, _checks, result = _result()
    report = render_report(result)
    assert "Illustrative inputs, not market data" in report
    assert "moves the estimated value most is Volatility" in report
    assert "## Estimated value (risk-neutral)" in report
    assert "REAL-WORLD measure, not a value" in report
    assert "not investment advice" in report
    assert not re.search(r"\b(buy|sell|overpriced)\b", report, re.IGNORECASE)
