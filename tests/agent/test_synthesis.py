"""Offline tests for payoff synthesis.

Uses the saved ea0306095 filing and recorded reply fixtures, exactly as in
tests/validation/test_synth_battery.py, from which REFERENCE_CODE and
EXAMPLES are imported.  Chat is a fake returning client.ChatResult: no
network and no API call.  n_paths=8_000 with a fixed seed keeps every
sandbox run deterministic while the battery still exercises real paths.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quantproof.agent import client
from quantproof.agent.costs import Usage
from quantproof.agent.extract import parse_json_reply, to_termsheet
from quantproof.agent.synthesis import (
    build_synthesis_messages,
    extract_code,
    extract_examples,
    synthesise_payoff,
)
from quantproof.core.market import MarketInputs
from quantproof.ingest.html import read_filing
from tests.validation.test_synth_battery import EXAMPLES, REFERENCE_CODE

STEM = "jpm_424b2-ea0306095"
FILING = ROOT / "tests" / "fixtures" / "filings" / f"{STEM}.htm"
REPLY = ROOT / "tests" / "fixtures" / "extractions" / f"{STEM}.reply.json"
N_PATHS = 8_000


@pytest.fixture(scope="module")
def ts():
    return to_termsheet(parse_json_reply(REPLY.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def filing():
    return read_filing(FILING.read_bytes())


@pytest.fixture(scope="module")
def market(ts):
    n = ts.n_underlyings
    correlation = tuple(
        tuple(1.0 if i == j else 0.5 for j in range(n)) for i in range(n)
    )
    return MarketInputs(
        as_of=ts.pricing_date.value,
        risk_free_rate=0.037,
        vols=(0.30, 0.35, 0.28),
        dividend_yields=(0.0, 0.01, 0.012),
        correlation=correlation,
        credit_spread=0.006,
        sources={"all": "test-entered"},
    )


def _chat_result(text, model="muse-wonder-1.0", finish_reason="stop"):
    return client.ChatResult(
        text=text,
        model=model,
        finish_reason=finish_reason,
        usage=Usage.from_response({}),
        cost_usd=0.0,
        seconds=0.0,
        attempts=1,
    )


class FakeChat:
    """Fake chat: replays queued texts/exceptions, records every call."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, messages, *, model=None, max_tokens=None, reasoning_effort=None, purpose="", **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "max_tokens": max_tokens,
                "reasoning_effort": reasoning_effort,
                "purpose": purpose,
            }
        )
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, client.ChatResult):
            return item
        return _chat_result(item, model=model or "muse-wonder-1.0")


def _all_text(messages) -> str:
    parts = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    parts.append(str(part.get("text", "")))
                else:
                    parts.append(str(part))
    return "\n".join(parts)


# --- extract_code -----------------------------------------------------------------------------


def test_extract_code_fenced_python():
    assert extract_code("```python\nx = 1\n```") == "x = 1"


def test_extract_code_plain_fence():
    assert extract_code("```\nx = 2\n```") == "x = 2"


def test_extract_code_two_blocks_takes_last():
    text = "```python\nx = 1\n```\nsome prose\n```python\ny = 2\n```"
    assert extract_code(text) == "y = 2"


def test_extract_code_no_fence_returns_text():
    assert extract_code("  x = 3  \n") == "x = 3"


# --- build_synthesis_messages -------------------------------------------------------------------


def test_build_messages_contains_feature_quote_terms_and_feedback(filing, ts):
    feedback = "PREVIOUS CODE HERE\nFailed checks to fix:\n- filing_examples: wrong payment"
    messages = build_synthesis_messages(filing.text, ts, feedback=feedback)
    text = _all_text(messages)
    # The unsupported feature's verbatim quote is in the request.
    assert ts.unsupported_features, "fixture should have an unsupported feature"
    assert ts.unsupported_features[0].citation.quote in text
    # The terms JSON is in the request.
    terms_json = json.dumps(json.loads(json.dumps(__import__("quantproof.payoffs.synthesized", fromlist=["terms_from_termsheet"]).terms_from_termsheet(ts))), indent=1)
    assert '"notional"' in text and '"unsupported_features"' in text
    assert terms_json.splitlines()[1] in text
    # The feedback text is included verbatim.
    assert "PREVIOUS CODE HERE" in text
    assert "filing_examples" in text
    # Filing text precedes the request (stable prefix for caching).
    assert text.index(filing.text) < text.index("Unsupported features")


def test_build_messages_without_feedback_has_no_feedback_section(filing, ts):
    text = _all_text(build_synthesis_messages(filing.text, ts))
    assert "Failed checks to fix" not in text


# --- extract_examples ---------------------------------------------------------------------------


def test_extract_examples_verifies_real_rows_and_flags_fabricated(filing):
    rows = [
        {"worst_final": ex.worst_final, "payment": ex.payment.value, "quote": ex.payment.citation.quote}
        for ex in EXAMPLES[:3]
    ]
    rows.append({"worst_final": 0.10, "payment": 123.00, "quote": "this row is fabricated $10.00 -90.00% $123.00"})
    reply = json.dumps({"examples": rows})
    chat = FakeChat([_chat_result(reply, model="muse-spark-1.3")])
    examples = extract_examples(filing, chat=chat)
    assert len(examples) == 4
    verified = [ex.payment.citation.verified for ex in examples]
    assert verified[:3] == [True, True, True]
    assert verified[3] is False


def test_extract_examples_non_json_reply_gives_empty(filing):
    chat = FakeChat([_chat_result("not json at all", model="muse-spark-1.3")])
    assert extract_examples(filing, chat=chat) == []


def test_extract_examples_truncated_reply_gives_empty(filing):
    reply = json.dumps({"examples": []})
    chat = FakeChat([_chat_result(reply, model="muse-spark-1.3", finish_reason="length")])
    assert extract_examples(filing, chat=chat) == []


# --- synthesise_payoff --------------------------------------------------------------------------


def test_synthesise_passes_on_first_attempt(filing, ts, market):
    chat = FakeChat(["```python\n" + REFERENCE_CODE + "\n```"])
    result = synthesise_payoff(
        filing, ts, market, examples=EXAMPLES, chat=chat, n_paths=N_PATHS
    )
    assert result.passed
    assert result.code is not None
    assert len(result.attempts) == 1
    assert result.attempts[0].passed
    assert result.registered is None
    assert result.results and all(r.passed for r in result.results)


def test_synthesise_retries_after_broken_mutant(filing, ts, market):
    mutant = REFERENCE_CODE.replace("np.absolute(r)", "0.0")
    assert mutant != REFERENCE_CODE
    chat = FakeChat(["```python\n" + mutant + "\n```", "```python\n" + REFERENCE_CODE + "\n```"])
    result = synthesise_payoff(
        filing, ts, market, examples=EXAMPLES, chat=chat, n_paths=N_PATHS
    )
    assert result.passed
    assert len(result.attempts) == 2
    assert not result.attempts[0].passed
    second_prompt = _all_text(chat.calls[1]["messages"])
    assert "filing_examples" in second_prompt


def test_synthesise_always_broken_fails_after_max_attempts(filing, ts, market):
    mutant = REFERENCE_CODE.replace("np.absolute(r)", "0.0")
    chat = FakeChat(["```python\n" + mutant + "\n```"] * 2)
    result = synthesise_payoff(
        filing, ts, market, examples=EXAMPLES, chat=chat, n_paths=N_PATHS, max_attempts=2
    )
    assert not result.passed
    assert result.code is None
    assert len(result.attempts) == 2
    assert result.registered is None


def test_synthesise_chat_error_is_attempt_not_raise(filing, ts, market):
    chat = FakeChat([client.MetaAPIError("boom"), client.MetaAPIError("boom again")])
    result = synthesise_payoff(
        filing, ts, market, examples=EXAMPLES, chat=chat, n_paths=N_PATHS, max_attempts=2
    )
    assert not result.passed
    assert len(result.attempts) == 2
    assert result.attempts[0].error is not None
    assert "boom" in result.attempts[0].error


def test_synthesise_registers_only_after_pass(filing, ts, market, tmp_path):
    chat = FakeChat(["```python\n" + REFERENCE_CODE + "\n```"])
    result = synthesise_payoff(
        filing, ts, market, examples=EXAMPLES, chat=chat, n_paths=N_PATHS,
        register=True, registry_dir=tmp_path,
    )
    assert result.passed
    assert result.registered is not None
    assert (tmp_path / f"{result.registered}.py").is_file()
    assert (tmp_path / f"{result.registered}.json").is_file()


def test_synthesise_records_model_and_purpose(filing, ts, market):
    chat = FakeChat(["```python\n" + REFERENCE_CODE + "\n```"])
    synthesise_payoff(filing, ts, market, examples=EXAMPLES, chat=chat, n_paths=N_PATHS)
    assert chat.calls, "fake chat should have been called"
    assert chat.calls[0]["model"] == "muse-wonder-1.0"
    assert chat.calls[0]["purpose"] == "synthesis"
