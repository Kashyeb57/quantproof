"""Offline tests for registered, battery-proved synthesized payoffs.

The ``synthesized_46661pwm5`` payoff was registered from a live Muse Wonder
run for the ea0306095 dual-directional note.  Its recorded replies are in
``tests/fixtures/synthesis/jpm_424b2-ea0306095.calls.json``; replaying them
through a fake chat keeps these tests offline and deterministic.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quantproof.agent import synthesis
from quantproof.agent.extract import parse_json_reply, to_termsheet
from quantproof.ingest.html import read_filing
from quantproof.registry import store
from quantproof.validation.synth_battery import check_filing_examples

STEM = "jpm_424b2-ea0306095"
NAME = "synthesized_46661pwm5"
FILING = ROOT / "tests" / "fixtures" / "filings" / f"{STEM}.htm"
REPLY = ROOT / "tests" / "fixtures" / "extractions" / f"{STEM}.reply.json"
CALLS = ROOT / "tests" / "fixtures" / "synthesis" / f"{STEM}.calls.json"


def _recorded_reply(purpose: str) -> str:
    """Recorded reply text for ``purpose`` from the saved live-run calls."""
    data = json.loads(CALLS.read_text(encoding="utf-8"))
    if isinstance(data, dict) and purpose in data:
        records = [{**data[purpose], "purpose": purpose}] if isinstance(data[purpose], dict) else [
            {"text": data[purpose], "purpose": purpose}
        ]
    elif isinstance(data, dict):
        records = data.get("calls") or data.get("records") or [data]
    else:
        records = data
    for record in records:
        if not isinstance(record, dict):
            continue
        if record.get("purpose") not in (purpose, None) and purpose not in str(record.get("purpose")):
            continue
        if record.get("purpose") is None and purpose != "synthesis":
            continue
        for key in ("text", "reply", "response", "content", "output_text"):
            if isinstance(record.get(key), str) and record[key]:
                return record[key]
        result = record.get("result")
        if isinstance(result, dict) and isinstance(result.get("text"), str):
            return result["text"]
    raise AssertionError(f"no recorded {purpose!r} reply in {CALLS}")


@pytest.fixture(scope="module")
def ts():
    return to_termsheet(parse_json_reply(REPLY.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def filing():
    return read_filing(FILING.read_bytes())


@pytest.fixture(scope="module")
def registered():
    return store.load(NAME)


def test_every_registered_payoff_loads_and_passed():
    names = store.list_registered()
    assert names, "expected at least one registered payoff"
    for name in names:
        code, metadata = store.load(name)  # load() verifies the SHA-256.
        assert code.strip()
        assert metadata["passed"] is True


def test_recorded_synthesis_code_is_the_registered_code(registered):
    code, _metadata = registered
    assert synthesis.extract_code(_recorded_reply("synthesis")) == code


def test_registered_code_reproves_against_filing_examples(ts, filing, registered):
    code, _metadata = registered

    def fake_chat(messages, **_kwargs):
        return SimpleNamespace(
            text=_recorded_reply("extract_examples"), finish_reason="stop"
        )

    examples = synthesis.extract_examples(filing, chat=fake_chat)
    verified = [e for e in examples if e.payment.citation.verified]
    assert len(verified) >= 3

    result = check_filing_examples(code, ts, examples, filing.text)
    assert result.passed, result.detail


def test_find_for_returns_registered_name(ts):
    assert store.find_for(ts) == NAME
