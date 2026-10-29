"""Tests for the saved market-audit page (pure data + AppTest smoke).

``page_data`` is tested without Streamlit on a tmp audit directory with
three hand-built saved results.  The AppTest smoke test reads the real
``data/market_audit`` and is skipped when Streamlit is unavailable or
that directory has no saved results.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quantproof.app.audit_page import page_data
from quantproof.report.market_audit import table_columns


def _result(
    accession: str,
    cusip: str,
    status: str,
    issue_price: float,
    estimated_value: float,
    fair_value: dict,
) -> dict:
    return {
        "meta": {
            "issuer_group": "Example Bank",
            "accession": accession,
            "cost_usd": 0.01,
            "model_seconds": 12.0,
        },
        "issuer": "Example Bank",
        "pricing_date": "2026-01-01",
        "cusip": cusip,
        "underlyings": ["INDEX A"],
        "structure": "autocallable",
        "issue_price": issue_price,
        "estimated_value": estimated_value,
        "notional": issue_price,
        "status": status,
        "reason": None if status == "priced" else "no template",
        "quotes": {"checked": 2, "verified": 2},
        "crosscheck": {"agreed": True, "diffs": []},
        "battery": None,
        "fair_value": fair_value,
        "loss_odds": {"prob_loss": 0.25},
        "fields": [],
        "seconds": 1.0,
    }


def _write_audit(tmp_path: Path) -> Path:
    audit_dir = tmp_path / "market_audit"
    results_dir = audit_dir / "results"
    results_dir.mkdir(parents=True)
    results = [
        # Priced, bank estimate inside our range.
        _result(
            "0000000001",
            "AAAA00001",
            "priced",
            1000.0,
            962.5,
            {"low": 940.0, "high": 980.0, "base": {"price": 960.0, "std_error": 1.0}},
        ),
        # Priced, bank estimate outside our range; a $10 note.
        _result(
            "0000000002",
            "BBBB00002",
            "priced",
            10.0,
            9.62,
            {"low": 9.0, "high": 9.5, "base": {"price": 9.25, "std_error": 0.01}},
        ),
        # Unsupported: has issue price and bank estimate but no range.
        _result(
            "0000000003",
            "CCCC00003",
            "unsupported",
            1000.0,
            950.0,
            {},
        ),
    ]
    for result in results:
        path = results_dir / f"{result['meta']['accession']}.json"
        path.write_text(json.dumps(result), encoding="utf-8")
    (audit_dir / "run.json").write_text(
        json.dumps(
            {
                "started": "2026-01-01T00:00:00",
                "finished": "2026-01-01T01:00:00",
                "assumptions": {"volatility": 0.2},
            }
        ),
        encoding="utf-8",
    )
    return audit_dir


def test_page_data_table_headers_and_chart_scaling(tmp_path):
    data = page_data(_write_audit(tmp_path))

    assert len(data["rows"]) == 3
    expected_headers = [header for _, header in table_columns()]
    assert data["table"]
    for row in data["table"]:
        assert list(row.keys()) == expected_headers
    # Table values are unformatted numbers (sortable by st.dataframe).
    assert data["table"][0]["Issue price ($)"] == 1000.0
    assert isinstance(data["table"][0]["Issue price ($)"], float)

    chart_by_cusip = {entry["cusip"]: entry for entry in data["chart"]}
    assert set(chart_by_cusip) == {"AAAA00001", "BBBB00002", "CCCC00003"}

    first = chart_by_cusip["AAAA00001"]
    assert first["issue_price_pct"] == 100.0
    # Exact scaling: 962.5 / 1000 * 100 = 96.25.
    assert first["estimate_pct"] == pytest.approx(96.25)
    assert first["range_low_pct"] == pytest.approx(94.0)
    assert first["range_high_pct"] == pytest.approx(98.0)
    assert data["rows"][0]["bank_in_range"] is True

    second = chart_by_cusip["BBBB00002"]
    # A $10 note with estimate 9.62 scales to the same axis: 96.2.
    assert second["estimate_pct"] == pytest.approx(96.2)
    assert second["range_low_pct"] == pytest.approx(90.0)
    assert second["range_high_pct"] == pytest.approx(95.0)
    assert data["rows"][1]["bank_in_range"] is False

    unsupported = chart_by_cusip["CCCC00003"]
    assert unsupported["estimate_pct"] == pytest.approx(95.0)
    assert unsupported["range_low_pct"] is None
    assert unsupported["range_high_pct"] is None


def test_page_data_empty_dir(tmp_path):
    data = page_data(tmp_path / "does_not_exist")
    assert data["rows"] == []
    assert data["table"] == []
    assert data["chart"] == []
    assert data["agg"]["notes_read"] == 0


def test_app_market_audit_page_renders():
    pytest.importorskip("streamlit")
    results_dir = ROOT / "data" / "market_audit" / "results"
    if not results_dir.is_dir() or not any(results_dir.glob("*.json")):
        pytest.skip("no saved market audit results in data/market_audit")

    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run()
    assert not at.exception

    radios = [r for r in at.sidebar.radio if r.label == "Page"]
    assert radios, "Page sidebar radio not found"
    radios[0].set_value("Market Audit").run()
    assert not at.exception
    assert "Market Audit" in [h.value for h in at.header]


def test_page_data_flagged_note_counts_in_range(tmp_path):
    audit_dir = tmp_path / "market_audit"
    results_dir = audit_dir / "results"
    results_dir.mkdir(parents=True)
    priced = _result(
        "0000000011",
        "DDDD00011",
        "priced",
        1000.0,
        962.5,
        {"low": 940.0, "high": 980.0, "base": {"price": 960.0, "std_error": 1.0}},
    )
    flagged = _result(
        "0000000012",
        "EEEE00012",
        "needs_review",
        1000.0,
        990.0,
        {"low": 940.0, "high": 980.0, "base": {"price": 960.0, "std_error": 1.0}},
    )
    for result in (priced, flagged):
        (results_dir / f"{result['meta']['accession']}.json").write_text(
            json.dumps(result), encoding="utf-8"
        )
    (audit_dir / "run.json").write_text(json.dumps({}), encoding="utf-8")
    data = page_data(audit_dir)
    rows_by_cusip = {row["cusip"]: row for row in data["rows"]}
    assert rows_by_cusip["DDDD00011"]["bank_in_range"] is True
    assert rows_by_cusip["EEEE00012"]["bank_in_range"] is False
    assert data["agg"]["n_priced_or_review"] == 2
    # Exact fraction: 1 in range of 2 priced-or-flagged.
    assert data["agg"]["share_bank_in_range"] == pytest.approx(1 / 2)
