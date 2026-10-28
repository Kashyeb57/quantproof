"""Offline tests for quantproof.report.market_audit.

Hand-built result dicts in the shape written by
``quantproof.report.audit_run.audit_note`` are saved under a temporary
``results/`` directory and loaded back with ``load_results``.  No
network, no saved fixtures, deterministic.
"""

from __future__ import annotations

import math
import csv
import json
from pathlib import Path

import pytest

from quantproof.report.market_audit import (
    aggregate,
    check_neutral,
    export_dataset,
    load_results,
    load_run,
    note_row,
    render_markdown,
    table_columns,
)


def _priced_in_range() -> dict:
    return {
        "meta": {
            "issuer_group": "Bank A",
            "accession": "0001",
            "url": "https://example.invalid/0001",
            "filing_date": "2024-01-03",
        },
        "status": "priced",
        "reason": None,
        "issuer": "Bank A, Inc.",
        "pricing_date": "2024-01-02",
        "cusip": "000000AA1",
        "underlyings": ["Index A", "Index B"],
        "structure": "worst-of 2, autocall",
        "issue_price": 1000.0,
        "estimated_value": 962.5,
        "notional": 1000.0,
        "fair_value": {
            "low": 940.0,
            "high": 985.0,
            "base": {"price": 962.0, "std_error": 1.25},
        },
        "loss_odds": {"prob_loss": 0.25},
        "quotes": {"checked": 78, "verified": 77},
        "crosscheck": {"agreed": True, "diffs": []},
        "battery": [{"name": "example check", "passed": True}],
        "fields": [
            {"path": "cusip", "value": "000000AA1", "quote": "CUSIP 000000AA1", "verified": True},
            {"path": "issuer", "value": "Bank A, Inc.", "quote": "Bank A, Inc.", "verified": True},
        ],
        "cost_usd": 0.10,
        "model_seconds": 1.5,
        "seconds": 2.0,
    }


def _priced_out_of_range() -> dict:
    result = _priced_in_range()
    result["meta"] = {
        "issuer_group": "Bank A",
        "accession": "0002",
        "url": "https://example.invalid/0002",
        "filing_date": "2024-01-04",
    }
    result["cusip"] = "000000AA2"
    result["estimated_value"] = 900.0
    result["quotes"] = {"checked": 10, "verified": 10}
    result["crosscheck"] = {
        "agreed": False,
        "diffs": [{"path": "cusip"}, {"path": "issuer"}],
    }
    result["cost_usd"] = 0.20
    result["model_seconds"] = 2.5
    result["seconds"] = 3.0
    return result


def _unsupported() -> dict:
    result = _priced_in_range()
    result["meta"] = {
        "issuer_group": "Bank B",
        "accession": "0003",
        "url": "https://example.invalid/0003",
        "filing_date": "2024-01-05",
    }
    result["issuer"] = "Bank B, Inc."
    result["status"] = "unsupported"
    result["reason"] = "structure not supported by the templates"
    result["estimated_value"] = 950.0
    result["fair_value"] = None
    result["loss_odds"] = None
    result["quotes"] = {"checked": 5, "verified": 5}
    result["crosscheck"] = {"agreed": True, "diffs": []}
    result["cost_usd"] = 0.05
    result["model_seconds"] = 0.5
    result["seconds"] = 1.0
    return result


def _extraction_failed() -> dict:
    return {
        "meta": {
            "issuer_group": "Bank B",
            "accession": "0004",
            "url": "https://example.invalid/0004",
            "filing_date": "2024-01-06",
        },
        "status": "extraction_failed",
        "reason": "no termsheet extracted",
        "termsheet": None,
        "fields": [],
        "quotes": {"checked": 0, "verified": 0},
        "crosscheck": None,
        "battery": None,
        "fair_value": None,
        "loss_odds": None,
        "estimated_value": None,
        "issue_price": None,
        "notional": None,
        "cusip": None,
        "issuer": None,
        "underlyings": [],
        "pricing_date": None,
        "structure": None,
        "seconds": 0.25,
    }


def _fake_results() -> list[dict]:
    return [
        _priced_in_range(),
        _priced_out_of_range(),
        _unsupported(),
        _extraction_failed(),
    ]


def _run() -> dict:
    return {
        "started": 1000.0,
        "finished": 1012.5,
        "skipped": 1,
        "assumptions": {"vol": 0.25, "correlation": 0.6},
    }


@pytest.fixture()
def audit_dir(tmp_path: Path) -> Path:
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    for result in _fake_results():
        accession = result["meta"]["accession"]
        (results_dir / f"{accession}.json").write_text(
            json.dumps(result), encoding="utf-8"
        )
    (tmp_path / "run.json").write_text(json.dumps(_run()), encoding="utf-8")
    return tmp_path


def test_load_results_sorted_and_load_run(audit_dir: Path) -> None:
    results = load_results(audit_dir)
    assert [r["meta"]["accession"] for r in results] == ["0001", "0002", "0003", "0004"]
    assert load_run(audit_dir)["skipped"] == 1
    assert load_run(audit_dir / "missing") == {}


def test_note_row_maths_in_range() -> None:
    row = note_row(_priced_in_range())
    assert row["issuer_group"] == "Bank A"
    assert row["issuer"] == "Bank A, Inc."
    assert row["underlyings"] == "Index A; Index B"
    # 1000 - 962.5 = 37.5 exactly; 37.5 / 1000 * 100 = 3.75 exactly.
    assert row["cost_to_investor"] == 37.5
    assert row["cost_to_investor_pct"] == 3.75
    assert row["range_low"] == 940.0
    assert row["range_high"] == 985.0
    assert row["base_value"] == 962.0
    assert row["base_std_error"] == 1.25
    assert row["bank_in_range"] is True
    assert row["prob_loss"] == 0.25
    assert row["quotes_checked"] == 78
    assert row["quotes_verified"] == 77
    assert row["quote_verification_rate"] == pytest.approx(77 / 78)
    assert row["crosscheck_agreed"] is True
    assert row["crosscheck_diffs"] == 0
    assert row["supported"] is True


def test_note_row_bank_out_of_range() -> None:
    row = note_row(_priced_out_of_range())
    assert row["cost_to_investor"] == 100.0
    assert row["cost_to_investor_pct"] == 10.0
    assert row["bank_in_range"] is False
    assert row["crosscheck_agreed"] is False
    assert row["crosscheck_diffs"] == 2
    assert row["supported"] is True


def test_note_row_none_handling() -> None:
    row = note_row(_extraction_failed())
    assert row["cost_to_investor"] is None
    assert row["cost_to_investor_pct"] is None
    assert row["range_low"] is None
    assert row["range_high"] is None
    assert row["base_value"] is None
    assert row["base_std_error"] is None
    assert row["bank_in_range"] is None
    assert row["prob_loss"] is None
    # checked is 0, so the rate is None rather than a division by zero.
    assert row["quote_verification_rate"] is None
    assert row["crosscheck_agreed"] is None
    assert row["crosscheck_diffs"] == 0
    assert row["supported"] is None
    assert row["cost_usd"] is None
    assert row["underlyings"] == ""

    unsupported = note_row(_unsupported())
    assert unsupported["supported"] is False
    assert unsupported["bank_in_range"] is None
    assert unsupported["cost_to_investor"] == 50.0


def test_aggregate(audit_dir: Path) -> None:
    rows = [note_row(r) for r in load_results(audit_dir)]
    agg = aggregate(rows, load_run(audit_dir))
    assert agg["notes_read"] == 4
    assert agg["notes_extracted"] == 3
    # Exact fractions: 2 priced of 3 extracted; 1 in range of 2 priced.
    assert agg["n_priced"] == 2
    assert agg["share_priced"] == pytest.approx(2 / 3)
    assert agg["n_bank_in_range"] == 1
    assert agg["share_bank_in_range"] == pytest.approx(1 / 2)
    assert agg["n_with_estimate"] == 3
    # Costs present: 37.5, 100.0, 50.0 -> median 50.0; pcts 3.75, 10, 5 -> 5.0.
    assert agg["median_cost_to_investor"] == 50.0
    assert agg["median_cost_to_investor_pct"] == 5.0
    # (77 + 10 + 5 + 0) verified / (78 + 10 + 5 + 0) checked.
    assert agg["quote_verification_rate"] == pytest.approx(92 / 93)
    assert agg["status_counts"] == {
        "extraction_failed": 1,
        "priced": 2,
        "unsupported": 1,
    }
    assert agg["issuers"] == ["Bank A", "Bank B"]
    assert agg["total_cost_usd"] == pytest.approx(0.35)
    assert agg["total_model_seconds"] == pytest.approx(4.5)
    assert agg["wall_seconds"] == 12.5
    assert agg["skipped"] == 1
    assert agg["assumptions"] == {"vol": 0.25, "correlation": 0.6}


def test_aggregate_empty_denominators() -> None:
    agg = aggregate([], {})
    assert agg["notes_read"] == 0
    assert agg["share_priced"] is None
    assert agg["share_bank_in_range"] is None
    assert agg["quote_verification_rate"] is None
    assert agg["median_cost_to_investor"] is None
    assert agg["median_cost_to_investor_pct"] is None
    assert agg["wall_seconds"] is None
    assert agg["skipped"] is None
    assert agg["assumptions"] is None


def test_export_dataset(audit_dir: Path, tmp_path: Path) -> None:
    results = load_results(audit_dir)
    out_dir = tmp_path / "export"
    csv_path, json_path = export_dataset(results, out_dir)
    assert csv_path.name == "notes.csv"
    assert json_path.name == "notes.json"

    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        csv_rows = list(reader)
    assert reader.fieldnames == list(note_row(_priced_in_range()).keys())
    assert len(csv_rows) == 4
    assert csv_rows[0]["accession"] == "0001"

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert len(payload) == 4
    assert payload[0]["row"]["accession"] == "0001"
    # The JSON carries the extracted fields with their quotes and flags.
    assert payload[0]["fields"][0]["quote"] == "CUSIP 000000AA1"
    assert payload[0]["fields"][0]["verified"] is True
    assert payload[0]["crosscheck"] == {"agreed": True, "diffs": []}
    assert payload[0]["battery"] == [{"name": "example check", "passed": True}]


def test_render_markdown(audit_dir: Path) -> None:
    rows = [note_row(r) for r in load_results(audit_dir)]
    markdown = render_markdown(rows, aggregate(rows, load_run(audit_dir)))
    assert (
        "Volatilities, dividends, correlation, rate and credit spread are "
        "stated assumptions, not market data as of each pricing date."
    ) in markdown
    assert (
        "This is a risk analysis under stated assumptions, not investment advice."
    ) in markdown
    assert "different model version" in markdown
    # Money at 2 decimals and P(loss) as a percentage with 1 decimal.
    assert "962.50" in markdown
    assert "25.0%" in markdown
    # The structure label "worst-of" appears and is allowed.
    assert "worst-of 2, autocall" in markdown
    assert check_neutral(markdown) == []
    headers = [header for _, header in table_columns()]
    for header in headers:
        assert header in markdown


def test_check_neutral() -> None:
    assert check_neutral("This note is overpriced.") == ["overpriced"]
    assert check_neutral("OVERPRICED") == ["overpriced"]
    assert check_neutral("worst-of 2, autocall") == []
    assert check_neutral("Worst-Of basket") == []
    assert check_neutral("a factual risk analysis") == []
    # Whole words only: "buyer" does not flag "buy".
    assert check_neutral("buyer") == []


def test_median_cost_is_per_1000_notional_across_note_sizes():
    from quantproof.report.market_audit import aggregate, note_row

    # $10 note with estimate $9.62 costs $0.38, i.e. $38 per $1,000; a
    # $1,000 note with estimate $962.50 costs $37.50.  Median of 38 and 37.5.
    small = {"status": "unsupported", "issue_price": 10.0, "estimated_value": 9.62, "notional": 10.0}
    large = {"status": "unsupported", "issue_price": 1000.0, "estimated_value": 962.5, "notional": 1000.0}
    rows = [note_row(small), note_row(large)]
    assert math.isclose(rows[0]["cost_to_investor_per_1000"], 38.0)
    assert math.isclose(aggregate(rows, {})["median_cost_to_investor"], 37.75)
