"""Offline tests for app term-sheet edits, rows and cross-check replay."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quantproof.app import demo

STEM = "jpm_424b2-38582"


def test_term_rows_statuses_and_exact_quotes():
    note = demo.load_note(STEM)
    rows = demo.term_rows(note)
    by_path = {r["field"]: r for r in rows}
    assert by_path["downside.gearing"]["status"] == "needs confirmation"
    assert by_path["issuer"]["status"] == "verified"
    assert "JPMorgan Chase Financial Company LLC" in by_path["issuer"]["quote"]
    assert len(rows) == 78


def test_user_edit_is_labelled_and_original_untouched():
    note = demo.load_note(STEM)
    edited_ts, edited, errors = demo.apply_user_edits(
        note.termsheet, {"downside.level": "0.65"}
    )
    assert errors == []
    assert edited == {"downside.level"}
    assert edited_ts.downside.level.value == 0.65
    assert edited_ts.downside.level.citation is None
    assert note.termsheet.downside.level.value == 0.7  # not mutated

    edited_note = demo.DemoNote(
        stem=STEM, termsheet=edited_ts, filing_text=note.filing_text,
        checks=note.checks, unverified=note.unverified,
    )
    rows = {r["field"]: r for r in demo.term_rows(edited_note, edited)}
    assert rows["downside.level"]["status"] == "user-entered"
    assert rows["downside.level"]["value"] == "0.65"


def test_bad_edit_is_reported_not_applied():
    note = demo.load_note(STEM)
    edited_ts, edited, errors = demo.apply_user_edits(
        note.termsheet,
        {"pricing_date": "not-a-date", "no.such.field": "1"},
    )
    assert edited == set()
    assert len(errors) == 2
    assert edited_ts.pricing_date.value == note.termsheet.pricing_date.value


def test_recorded_crosscheck_agrees_for_38582_only():
    note = demo.load_note(STEM)
    assert demo.recorded_crosscheck_diffs(STEM, note.termsheet) == []
    other = demo.load_note("jpm_424b2-ea0308498")
    assert demo.recorded_crosscheck_diffs(other.stem, other.termsheet) is None


def test_synthesized_note_uses_registry_proof_and_passes_gate():
    note = demo.load_note("jpm_424b2-ea0306095")
    defaults = demo.DEFAULT_INPUTS[note.stem]
    market = demo.build_market(
        note.termsheet,
        rate=defaults["rate"], spread=defaults["spread"],
        vols=defaults["vols"], dividends=defaults["dividends"],
        rho=defaults["rho"],
    )
    battery = demo.run_proof_battery(note, note.termsheet, market)
    assert battery["kind"] == "synthesized"
    assert battery["passed"] is True
    names = [r["name"] for r in battery["results"]]
    assert "filing_examples" in names
    assert names[-1] == "quotes"
