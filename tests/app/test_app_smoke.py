"""Smoke test for the Streamlit app script (skips without Streamlit).

Uses Streamlit's AppTest harness to run ``app.py`` in-process: the term
sheet must render, and after the user confirms the terms the proof
battery and the separated verdict must appear with no exceptions.
CI installs only the dev extra, so this skips there; it runs wherever
the ``app`` extra is installed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("streamlit")


def test_app_runs_demo_flow_to_verdict():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run()
    assert not at.exception
    assert [h.value for h in at.header][:2] == ["1. Pick a note", "2. Term sheet"]

    confirm = next(c for c in at.checkbox if "I confirm" in c.label)
    confirm.set_value(True).run()
    assert not at.exception
    headers = [h.value for h in at.header]
    assert "4. Proof checks" in headers
    assert "5. Verdict" in headers
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Issue price"] == "$1,000.00"
    assert metrics["Bank's estimated value"] == "$954.00"
    assert metrics["Our estimated value range"].startswith("$")
