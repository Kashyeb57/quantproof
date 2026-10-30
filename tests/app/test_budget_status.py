"""Visitor budget status: remaining session runs and live availability."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quantproof.app import live

pytest.importorskip("streamlit")


def test_budget_status_counts_down_and_stays_available():
    status = live.budget_status(
        extractions_used=1, spent_today=0.25, cap=5.0,
        spent_total=1.0, total_cap=20.0,
    )
    assert status["remaining"] == 2
    assert status["session_limit"] == 3
    assert status["available"] is True
    assert status["reason"] == ""


def test_budget_status_reports_each_blocking_reason():
    status = live.budget_status(
        extractions_used=3, spent_today=5.0, cap=5.0,
        spent_total=20.0, total_cap=20.0,
    )
    assert status["remaining"] == 0
    assert status["available"] is False
    assert "session limit reached" in status["reason"]
    assert "daily cap reached" in status["reason"]
    assert "lifetime cap reached" in status["reason"]


def test_budget_line_recomputes_from_guard_state_on_rerun():
    # The post-run refresh is a rerun: the budget line is recomputed
    # from the session guard and the cost log on every render, so a
    # guard that has used 2 runs must immediately show 1 left.
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run()
    at.session_state["live_guard"] = live.LiveGuard(extractions_used=2)
    [r for r in at.sidebar.radio if r.label == "Page"][0].set_value(
        "Note analysis"
    ).run()
    at.radio[0].set_value("Live mode").run()
    assert not at.exception
    rendered = "\n".join(str(el.value) for el in at.markdown)
    assert "Live runs remaining in this session: 1 of 3" in rendered


def test_budget_line_adds_meta_and_baseten_spend(monkeypatch, tmp_path):
    from streamlit.testing.v1 import AppTest

    # Both provider logs live under .cache; the budget line must show
    # their sum, so Baseten spend can never hide from the caps.
    monkeypatch.chdir(tmp_path)
    _write_cost_log(tmp_path / ".cache" / "meta_calls.jsonl", 4.50)
    _write_cost_log(tmp_path / ".cache" / "baseten_calls.jsonl", 0.40)

    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run()
    assert not at.exception
    [r for r in at.sidebar.radio if r.label == "Page"][0].set_value(
        "Note analysis"
    ).run()
    at.radio[0].set_value("Live mode").run()
    assert not at.exception
    rendered = "\n".join(str(el.value) for el in at.markdown)
    # Dollar signs are escaped in rendered markdown (Streamlit LaTeX).
    assert "recorded or estimated usage \\$4.90 of \\$5.00 daily limit (Meta + Baseten combined)" in rendered


def _write_cost_log(path, cost_usd):
    import datetime
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    path.write_text(
        json.dumps({"time": f"{stamp}T00:00:00+00:00", "cost_usd": cost_usd}),
        encoding="utf-8",
    )


def test_app_shows_remaining_runs_in_live_mode(monkeypatch, tmp_path):
    from streamlit.testing.v1 import AppTest

    # The cost log path is relative (.cache/meta_calls.jsonl): run from an
    # empty directory so real local spend cannot make "today" look capped.
    monkeypatch.chdir(tmp_path)

    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run()
    assert not at.exception
    [r for r in at.sidebar.radio if r.label == "Page"][0].set_value(
        "Note analysis"
    ).run()
    at.radio[0].set_value("Live mode").run()
    assert not at.exception
    rendered = "\n".join(str(el.value) for el in at.markdown)
    assert "Live runs remaining in this session: 3 of 3" in rendered
    assert "Live mode is available today" in rendered
    assert live.LIVE_RUN_NOTICE in rendered
