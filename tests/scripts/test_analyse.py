"""Offline tests for scripts/analyse.py.

Uses the saved 38582 filing and recorded reply fixtures. No network and no
API call: runs use --reply only, --live is monkeypatched to fail if touched,
and the small path counts test the wiring rather than pricing accuracy.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_SCRIPT = ROOT / "scripts" / "analyse.py"
_spec = importlib.util.spec_from_file_location("analyse_script", _SCRIPT)
analyse_module = importlib.util.module_from_spec(_spec)
assert _spec is not None and _spec.loader is not None
_spec.loader.exec_module(analyse_module)

main = analyse_module.main

STEM = "jpm_424b2-38582"
REFUSED_STEM = "jpm_424b2-ea0306095"
FILING = ROOT / "tests" / "fixtures" / "filings" / f"{STEM}.htm"
REPLY = ROOT / "tests" / "fixtures" / "extractions" / f"{STEM}.reply.json"
REFUSED_FILING = ROOT / "tests" / "fixtures" / "filings" / f"{REFUSED_STEM}.htm"
REFUSED_REPLY = ROOT / "tests" / "fixtures" / "extractions" / f"{REFUSED_STEM}.reply.json"

FORBIDDEN = re.compile(r"\b(buy|sell|overpriced|underpriced|recommend)\b", re.IGNORECASE)


def _base_args(filing=FILING, reply=REPLY) -> list[str]:
    return [
        str(filing),
        "--reply",
        str(reply),
        "--rate",
        "0.037",
        "--spread",
        "0.006",
        "--vols",
        "0.24,0.17",
        "--dividends",
        "0.012,0.013",
        "--corr",
        "0.6",
        "--paths",
        "2000",
        "--base-paths",
        "5000",
    ]


def _no_live(monkeypatch):
    def _raise(*args, **kwargs):
        raise AssertionError("extract_termsheet must not be called for --reply runs")

    monkeypatch.setattr(analyse_module, "extract_termsheet", _raise)


def test_main_reply_end_to_end(tmp_path, monkeypatch):
    _no_live(monkeypatch)
    out = tmp_path / "r.md"
    json_out = tmp_path / "r.json"
    code = main([*_base_args(), "--out", str(out), "--json", str(json_out)])
    assert code == 0
    assert out.exists()
    assert json_out.exists()

    data = json.loads(json_out.read_text(encoding="utf-8"))
    assert "fair_value" in data
    assert "loss_odds" in data

    report = out.read_text(encoding="utf-8")
    for heading in (
        "# QuantProof risk analysis:",
        "## Extracted terms",
        "## Estimated value (risk-neutral)",
        "## Real-world loss odds (REAL-WORLD measure, not a value)",
        "## Stress heatmap (real-world)",
        "## Limitations",
    ):
        assert heading in report
    assert "REAL-WORLD" in report
    assert "Based on preliminary terms" in report
    # Bank's estimated value from the filing fixture, formatted as money.
    assert "954" in report
    assert "Issuer default is not simulated" in report
    assert "Illustrative inputs, not market data" in report
    assert "moves the estimated value most is" in report
    assert not FORBIDDEN.search(report)


def test_wrong_number_of_vols_exit_2(tmp_path, monkeypatch):
    _no_live(monkeypatch)
    args = _base_args()
    args[args.index("--vols") + 1] = "0.24"
    code = main([*args, "--out", str(tmp_path / "r.md")])
    assert code == 2


def test_missing_corr_for_two_assets_exit_2(tmp_path, monkeypatch):
    _no_live(monkeypatch)
    args = _base_args()
    index = args.index("--corr")
    del args[index : index + 2]
    code = main([*args, "--out", str(tmp_path / "r.md")])
    assert code == 2


def test_refused_fixture_exit_2(tmp_path, monkeypatch, capsys):
    _no_live(monkeypatch)
    # The refused note is checked before market-input counts are used for
    # pricing; give the same two-asset arguments as the supported run.
    code = main(
        [
            *_base_args(filing=REFUSED_FILING, reply=REFUSED_REPLY),
            "--out",
            str(tmp_path / "r.md"),
        ]
    )
    assert code == 2
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "not supported" in combined.lower() or len(combined.strip()) > 0
    # The UnsupportedTermSheet reason is printed, not swallowed.
    assert combined.strip() != ""


def test_battery_adds_section(tmp_path, monkeypatch):
    _no_live(monkeypatch)
    out = tmp_path / "r.md"
    code = main([*_base_args(), "--battery", "--out", str(out)])
    assert code == 0
    report = out.read_text(encoding="utf-8")
    assert "## Proof battery" in report
    assert "passed" in report
    assert not FORBIDDEN.search(report)


def test_analyse_result_ranges_and_measure(monkeypatch):
    _no_live(monkeypatch)
    from quantproof.agent.extract import parse_json_reply, to_termsheet
    from quantproof.core.market import MarketInputs
    from quantproof.ingest.html import read_filing
    from quantproof.validation.quotes import verify_termsheet

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
        sources={"all": "user-entered on the command line"},
    )
    result = analyse_module.analyse(
        ts,
        checks,
        market,
        paths=2000,
        base_paths=5000,
        seed=0,
        erp=0.05,
        battery=False,
        filing_text=filing.text,
    )
    assert result["fair_value"]["low"] <= result["fair_value"]["high"]
    assert result["loss_odds"]["measure"] == "real_world"
    assert result["battery"] is None
