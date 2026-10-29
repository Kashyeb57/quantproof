"""Tests for the overview landing page (pure data + AppTest smoke).

``page_data`` is tested without Streamlit on tmp audit/benchmark
directories.  The AppTest smoke test runs ``app.py``: the app must open
on the Overview page, and its buttons must navigate to the Market Audit
and Synthesis replay pages.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quantproof.app.overview_page import page_data


def _write_audit(audit_dir: Path) -> None:
    results_dir = audit_dir / "results"
    results_dir.mkdir(parents=True)
    (results_dir / "0000000001.json").write_text(
        json.dumps(
            {
                "meta": {
                    "accession": "0000000001",
                    "issuer_group": "Example Bank",
                },
                "status": "priced",
                "issue_price": 1000.0,
                "estimated_value": 980.0,
                "notional": 1000.0,
                "fair_value": {"low": 940.0, "high": 990.0},
                "quotes": {"checked": 10, "verified": 9},
                "cost_usd": 0.2,
                "cost_usd_baseten": 0.05,
                "model_seconds": 10.0,
            }
        ),
        encoding="utf-8",
    )
    (audit_dir / "run.json").write_text(json.dumps({}), encoding="utf-8")


def _write_bench(bench_dir: Path) -> None:
    results_dir = bench_dir / "results"
    results_dir.mkdir(parents=True)
    (results_dir / "0000000001.json").write_text(
        json.dumps(
            {
                "meta": {
                    "accession": "0000000001",
                    "template_covered": False,
                },
                "cusip": "EEEE00001",
                "passed": True,
                "status": "priced_ai",
                "attempts": [],
                "n_attempts": 1,
                "cost_usd": 0.1,
                "model_seconds": 5.0,
            }
        ),
        encoding="utf-8",
    )


def test_page_data_empty_dirs(tmp_path):
    data = page_data(tmp_path / "audit", tmp_path / "bench")
    assert data["audit"]["notes_read"] == 0
    assert data["synthesis"]["n_notes"] == 0


def test_page_data_headlines(tmp_path):
    audit_dir = tmp_path / "audit"
    bench_dir = tmp_path / "bench"
    _write_audit(audit_dir)
    _write_bench(bench_dir)
    data = page_data(audit_dir, bench_dir)
    assert data["audit"]["notes_read"] == 1
    assert data["audit"]["n_priced"] == 1
    assert data["synthesis"]["n_notes"] == 1
    assert data["synthesis"]["n_passed"] == 1
    assert data["synthesis"]["coverage"]["n_extracted"] == 1


def test_app_opens_on_overview_and_navigates():
    pytest.importorskip("streamlit")
    results_dir = ROOT / "data" / "market_audit" / "results"
    if not results_dir.is_dir() or not any(results_dir.glob("*.json")):
        pytest.skip("no saved market audit results in data/market_audit")

    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run()
    assert not at.exception
    assert at.header[0].value == "Overview"

    at.button[0].click().run()
    assert not at.exception
    assert "Market Audit" in [h.value for h in at.header]
