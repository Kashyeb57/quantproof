"""Tests for the synthesis benchmark report, export data and app page.

Offline and deterministic: hand-built saved result dicts in ``tmp_path``
(one first-try pass that is template-covered, one repaired after a
filing_examples failure, one synthesis_failed) plus three hand-built
audit rows.  No network, no API calls, no pricing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quantproof.app.synthesis_page import page_data
from quantproof.report.market_audit import check_neutral
from quantproof.report.synthesis_benchmark import (
    load_results,
    render_markdown,
    showcase,
    summarise,
)


def _check(name, passed, detail="detail"):
    return {
        "name": name,
        "passed": passed,
        "value": None,
        "reference": None,
        "tolerance": None,
        "detail": detail,
    }


def _attempt(code, passed, checks, feedback=None, counterexamples=None):
    return {
        "code": code,
        "passed": passed,
        "error": None,
        "feedback_sent": feedback,
        "counterexamples": counterexamples or {},
        "cost_usd": 0.05,
        "seconds": 5.0,
        "checks": checks,
    }


def _results() -> list[dict]:
    first_try = {
        "meta": {
            "accession": "0000000001",
            "issuer_group": "Example Bank",
            "label": "First note",
            "template_covered": True,
            "audit_status": "priced",
        },
        "cusip": "AAAA00001",
        "structure": "autocallable",
        "unsupported_features": [],
        "issue_price": 1000.0,
        "estimated_value": 960.0,
        "examples": [],
        "attempts": [
            _attempt(
                "CODE_FIRST",
                True,
                [_check("filing_examples", True), _check("bounded", True)],
            )
        ],
        "passed": True,
        "n_attempts": 1,
        "repaired": False,
        "first_failing_checks": [],
        "status": "priced_ai",
        "cost_usd": 0.10,
        "model_seconds": 10.0,
        "fair_value": {
            "low": 940.0,
            "high": 980.0,
            "base": {"price": 960.0, "std_error": 1.0, "n_paths": 20000, "seed": 7},
            "points": 12,
            "skew": "not included",
        },
        "gate": None,
        "template": {
            "template_price": 959.5,
            "template_se": 0.8,
            "ai_price": 960.0,
            "ai_se": 0.6,
            "diff": 0.5,
            "combined_se": 1.0,
            "within_3se": True,
        },
        "template_examples": {"rows": [], "max_error": 0.001, "passed": True},
    }
    repaired = {
        "meta": {
            "accession": "0000000002",
            "issuer_group": "Example Bank",
            "label": "Repaired note",
            "template_covered": False,
            "audit_status": "unsupported",
        },
        "cusip": "BBBB00002",
        "structure": "autocallable",
        "unsupported_features": [{"value": "feature x", "quote": "quote x"}],
        "issue_price": 1000.0,
        "estimated_value": 950.0,
        "examples": [],
        "attempts": [
            _attempt(
                "CODE_WRONG",
                False,
                [
                    _check("filing_examples", False, "payment mismatch"),
                    _check("bounded", False, "unbounded output"),
                ],
                counterexamples={
                    "filing_examples": "Example: final level 50, expected 500, got 1000."
                },
            ),
            _attempt(
                "CODE_FIXED",
                True,
                [_check("filing_examples", True), _check("bounded", True)],
                feedback="Fix filing_examples: payment mismatch.",
            ),
        ],
        "passed": True,
        "n_attempts": 2,
        "repaired": True,
        "first_failing_checks": ["filing_examples", "bounded"],
        "status": "priced_ai",
        "cost_usd": 0.20,
        "model_seconds": 20.0,
        "fair_value": {
            "low": 930.0,
            "high": 970.0,
            "base": {"price": 950.0, "std_error": 1.5, "n_paths": 20000, "seed": 7},
            "points": 12,
            "skew": "not included",
        },
        "gate": None,
    }
    failed = {
        "meta": {
            "accession": "0000000003",
            "issuer_group": "Example Bank",
            "label": "Failed note",
            "template_covered": False,
            "audit_status": "unsupported",
        },
        "cusip": "CCCC00003",
        "structure": "autocallable",
        "unsupported_features": [{"value": "feature y", "quote": "quote y"}],
        "issue_price": 1000.0,
        "estimated_value": 940.0,
        "examples": [],
        "attempts": [
            _attempt(
                "CODE_BAD_1",
                False,
                [_check("filing_examples", False, "mismatch 1")],
            ),
            _attempt(
                "CODE_BAD_2",
                False,
                [_check("filing_examples", False, "mismatch 2")],
                feedback="Fix filing_examples: mismatch 1.",
            ),
        ],
        "passed": False,
        "n_attempts": 2,
        "repaired": False,
        "first_failing_checks": ["filing_examples"],
        "status": "synthesis_failed",
        "cost_usd": 0.05,
        "model_seconds": 5.0,
    }
    return [first_try, repaired, failed]


def _audit_rows() -> list[dict]:
    return [
        {"status": "priced", "cusip": "AAAA00001"},
        {"status": "unsupported", "cusip": "BBBB00002"},
        {"status": "unsupported", "cusip": "CCCC00003"},
    ]


def _write_bench(tmp_path: Path) -> Path:
    bench_dir = tmp_path / "synthesis_benchmark"
    results_dir = bench_dir / "results"
    results_dir.mkdir(parents=True)
    for result in _results():
        path = results_dir / f"{result['meta']['accession']}.json"
        path.write_text(json.dumps(result), encoding="utf-8")
    return bench_dir


def test_summarise_numbers_exactly():
    summary = summarise(_results(), _audit_rows())

    assert summary["n_notes"] == 3
    assert summary["n_passed"] == 2
    assert summary["pass_rate"] == pytest.approx(2 / 3)
    assert summary["attempts_per_note"] == [1, 2, 2]
    assert summary["mean_attempts"] == pytest.approx(5 / 3)
    assert summary["n_first_try"] == 1
    assert summary["n_repaired"] == 1
    assert summary["n_failed"] == 1
    assert summary["total_cost_usd"] == pytest.approx(0.35)
    assert summary["total_model_seconds"] == pytest.approx(35.0)
    assert summary["status_counts"] == {"priced_ai": 2, "synthesis_failed": 1}

    # filing_examples failed in repaired attempt 1 and in both failed
    # attempts (3 failed attempts); only the repaired note counts as a
    # repaired note for it.  bounded failed once, in the repaired note.
    assert summary["catches"] == {
        "bounded": {"failed_attempts": 1, "repaired_notes": 1},
        "filing_examples": {"failed_attempts": 3, "repaired_notes": 1},
    }

    assert summary["coverage"] == {
        "n_extracted": 3,
        "n_template": 1,
        "n_ai": 2,
        "n_ai_needs_review": 0,
        "n_unsupported_total": 2,
        "n_unsupported_tried": 2,
    }

    assert summary["generalisation"] == [
        {
            "cusip": "AAAA00001",
            "passed": True,
            "diff": 0.5,
            "combined_se": 1.0,
            "within_3se": True,
            "ai_examples_passed": True,
            "template_examples_passed": True,
            "template_examples_max_error": 0.001,
        }
    ]


def test_load_results_sorted_by_accession(tmp_path):
    bench_dir = _write_bench(tmp_path)
    loaded = load_results(bench_dir)
    assert [r["cusip"] for r in loaded] == ["AAAA00001", "BBBB00002", "CCCC00003"]
    assert load_results(tmp_path / "missing") == []


def test_showcase_picks_repaired_note_and_codes():
    example = showcase(_results())
    assert example is not None
    assert example["cusip"] == "BBBB00002"
    assert example["structure"] == "autocallable"
    assert example["label"] == "Repaired note"
    assert example["wrong_code"] == "CODE_WRONG"
    assert example["fixed_code"] == "CODE_FIXED"
    assert example["n_attempts"] == 2
    assert [c["name"] for c in example["failed_checks"]] == [
        "filing_examples",
        "bounded",
    ]
    assert example["failed_checks"][0]["detail"] == "payment mismatch"
    assert "expected 500" in example["counterexample"]
    assert example["feedback"] == "Fix filing_examples: payment mismatch."
    assert len(example["attempts"]) == 2
    assert showcase([]) is None


def test_render_markdown_coverage_sentence_and_neutral():
    results = _results()
    summary = summarise(results, _audit_rows())
    markdown = render_markdown(results, summary)

    expected_sentence = (
        "Templates priced 1 of 3 extracted notes; with proven AI-written "
        "payoffs, 2 of 3 (2 of the 2 unsupported notes were attempted)."
    )
    assert expected_sentence in markdown
    assert "# Synthesis benchmark" in markdown
    assert "CODE_WRONG" in markdown
    assert "CODE_FIXED" in markdown
    assert "expected 500" in markdown
    assert "This is a risk analysis under stated assumptions, not investment advice." in markdown
    assert check_neutral(markdown) == []


def test_page_data_on_tmp_and_empty_dirs(tmp_path):
    bench_dir = _write_bench(tmp_path)
    data = page_data(bench_dir)
    assert data["summary"]["n_notes"] == 3
    assert data["summary"]["n_passed"] == 2
    assert data["showcase"] is not None
    assert data["showcase"]["cusip"] == "BBBB00002"

    empty = page_data(tmp_path / "does_not_exist")
    assert empty["summary"]["n_notes"] == 0
    assert empty["showcase"] is None


def test_app_synthesis_replay_page_renders():
    pytest.importorskip("streamlit")
    results_dir = ROOT / "data" / "synthesis_benchmark" / "results"
    if not results_dir.is_dir() or not any(results_dir.glob("*.json")):
        pytest.skip("no saved synthesis benchmark results in data/synthesis_benchmark")

    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run()
    assert not at.exception

    radios = [r for r in at.sidebar.radio if r.label == "Page"]
    assert radios, "Page sidebar radio not found"
    radios[0].set_value("Synthesis replay").run()
    assert not at.exception
    assert "Synthesis replay" in [h.value for h in at.header]
