"""Offline, deterministic tests for quantproof.agent.crosscheck.

Uses the saved JPM 38582 filing and recorded extraction reply fixtures,
loaded like tests/scripts/test_analyse.py. The model is a fake chat
function returning quantproof.agent.client.ChatResult objects; no network
and no real API call is made.
"""

from __future__ import annotations

import copy
import json
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quantproof.agent import client  # noqa: E402
from quantproof.agent.client import ChatResult, MetaAPIError  # noqa: E402
from quantproof.agent.costs import Usage  # noqa: E402
from quantproof.agent.crosscheck import (  # noqa: E402
    FieldDiff,
    crosscheck,
    diff_termsheets,
    recheck,
)
from quantproof.agent.extract import Extraction, parse_json_reply, to_termsheet  # noqa: E402
from quantproof.ingest.html import read_filing  # noqa: E402
from quantproof.validation.quotes import (  # noqa: E402
    normalise_for_match,
    verify_quote,
    verify_termsheet,
)

STEM = "jpm_424b2-38582"
FILING = ROOT / "tests" / "fixtures" / "filings" / f"{STEM}.htm"
REPLY = ROOT / "tests" / "fixtures" / "extractions" / f"{STEM}.reply.json"


def _load_first_extraction():
    filing = read_filing(FILING.read_bytes())
    raw = parse_json_reply(REPLY.read_text(encoding="utf-8"))
    ts = to_termsheet(raw)
    checks = verify_termsheet(ts, filing.text)
    extraction = Extraction(termsheet=ts, checks=checks, result=None, raw=raw)
    return filing, extraction


def _changed_level(ts) -> float:
    original = float(ts.downside.level.value)
    return 0.65 if original != 0.65 else 0.60


def _verbatim_quote(filing, ts) -> str:
    """A quote that verify_quote accepts against this filing, deterministically."""
    candidate = ts.downside.level.citation.quote
    verified, _reason = verify_quote(candidate, normalise_for_match(filing.text))
    if verified:
        return candidate
    # Fallback: an exact substring of the filing text is verbatim by
    # construction, so the normalising verifier must accept it.
    text = filing.text.strip()
    assert text
    return text[:80]


def _chat_result(text: str, model: str) -> ChatResult:
    return ChatResult(
        text=text,
        model=model,
        finish_reason="stop",
        usage=Usage.from_response({}),
        cost_usd=0.0,
        seconds=0.0,
        attempts=1,
    )


def _recheck_reply(choice: str, value, quote: str) -> str:
    return json.dumps({"value": value, "quote": quote, "choice": choice})


# ---------------------------------------------------------------------------
# diff_termsheets
# ---------------------------------------------------------------------------


def test_diff_identical_termsheets_is_empty():
    _filing, first = _load_first_extraction()
    ts = first.termsheet
    assert diff_termsheets(ts, copy.deepcopy(ts)) == []


def test_diff_reports_exactly_the_changed_numeric_field_and_date():
    _filing, first = _load_first_extraction()
    ts = first.termsheet
    changed = copy.deepcopy(ts)

    object.__setattr__(changed.downside.level, "value", _changed_level(ts))
    new_maturity = ts.maturity_date.value + timedelta(days=30)
    object.__setattr__(changed.maturity_date, "value", new_maturity)

    diffs = diff_termsheets(ts, changed)
    assert {diff.path for diff in diffs} == {"downside.level", "maturity_date"}
    assert len(diffs) == 2
    assert all(diff.kind == "value" for diff in diffs)
    assert all(diff.status == "open" for diff in diffs)
    by_path = {diff.path: diff for diff in diffs}
    assert by_path["downside.level"].first == ts.downside.level.value
    assert by_path["downside.level"].second == _changed_level(ts)
    assert by_path["maturity_date"].first == ts.maturity_date.value
    assert by_path["maturity_date"].second == new_maturity


def test_diff_removed_last_autocall_observation_is_only_in_first():
    _filing, first = _load_first_extraction()
    ts = first.termsheet
    assert ts.autocall is not None
    assert len(ts.autocall.observations) >= 1

    changed = copy.deepcopy(ts)
    # Removing the LAST observation leaves every remaining observation
    # aligned, so the only disagreements are the dropped paths.
    object.__setattr__(
        changed.autocall, "observations", changed.autocall.observations[:-1]
    )

    diffs = diff_termsheets(ts, changed)
    assert diffs
    assert all(diff.kind == "only_in_first" for diff in diffs)
    assert all(diff.second is None for diff in diffs)
    assert all("autocall" in diff.path for diff in diffs)


def test_diff_casefold_and_whitespace_only_string_change_is_no_diff():
    _filing, first = _load_first_extraction()
    ts = first.termsheet
    changed = copy.deepcopy(ts)

    original = ts.issuer.value
    object.__setattr__(changed.issuer, "value", f"  {original.upper()}\t ")

    assert diff_termsheets(ts, changed) == []


# ---------------------------------------------------------------------------
# recheck
# ---------------------------------------------------------------------------


def _one_open_diff(ts):
    changed = copy.deepcopy(ts)
    object.__setattr__(changed.downside.level, "value", _changed_level(ts))
    diffs = diff_termsheets(ts, changed)
    assert len(diffs) == 1
    assert diffs[0].path == "downside.level"
    assert diffs[0].status == "open"
    return diffs


def test_recheck_resolves_second_with_verified_quote_and_does_not_mutate():
    filing, first = _load_first_extraction()
    ts = first.termsheet
    diffs = _one_open_diff(ts)
    diff = diffs[0]
    quote = _verbatim_quote(filing, ts)
    calls = []

    def fake_chat(messages, *, model, max_tokens=4000, purpose="", **_kwargs):
        calls.append({"model": model, "purpose": purpose})
        return _chat_result(_recheck_reply("second", diff.second, quote), model)

    results = recheck(filing, diffs, chat=fake_chat)

    assert len(results) == 1
    assert results[0] is not diff
    assert results[0].status == "resolved_second"
    assert results[0].quote == quote
    assert calls == [{"model": "muse-spark-1.3", "purpose": "recheck"}]
    # Input diffs are not mutated.
    assert diff.status == "open"
    assert diff.quote is None
    assert diff.note == ""


def test_recheck_fabricated_quote_is_unresolved_and_does_not_mutate():
    filing, first = _load_first_extraction()
    ts = first.termsheet
    diffs = _one_open_diff(ts)
    diff = diffs[0]
    fabricated = "This fabricated sentence zzqq 987654 is not in the filing."

    def fake_chat(messages, *, model, max_tokens=4000, purpose="", **_kwargs):
        return _chat_result(_recheck_reply("second", diff.second, fabricated), model)

    results = recheck(filing, diffs, chat=fake_chat)

    assert results[0].status == "unresolved"
    assert results[0].quote is None
    assert "not verified" in results[0].note
    assert diff.status == "open"
    assert diff.quote is None


def test_recheck_chat_error_is_unresolved_without_raising():
    filing, first = _load_first_extraction()
    diffs = _one_open_diff(first.termsheet)
    diff = diffs[0]

    def failing_chat(messages, *, model, max_tokens=4000, purpose="", **_kwargs):
        raise MetaAPIError("simulated recheck failure")

    results = recheck(filing, diffs, chat=failing_chat)

    assert results[0].status == "unresolved"
    assert "simulated recheck failure" in results[0].note
    assert diff.status == "open"
    assert diff.quote is None


# ---------------------------------------------------------------------------
# crosscheck
# ---------------------------------------------------------------------------


def test_crosscheck_identical_second_extraction_agrees_and_records_model():
    filing, first = _load_first_extraction()
    recorded_reply = REPLY.read_text(encoding="utf-8")
    models = []

    def fake_chat(messages, *, model, max_tokens=16000, purpose="", **_kwargs):
        models.append(model)
        return _chat_result(recorded_reply, model)

    result = crosscheck(filing, first, chat=fake_chat)

    assert result.agreed is True
    assert result.second is not None
    assert result.diffs == []
    assert result.needs_user == []
    assert result.error is None
    assert result.second_model == "muse-spark-1.2"
    # The second (independent) extraction is the only call, on muse-spark-1.2;
    # with no diffs there is no recheck call.
    assert models == ["muse-spark-1.2"]


def _crosscheck_with_one_differing_field(choice: str):
    filing, first = _load_first_extraction()
    ts = first.termsheet
    first_value = float(ts.downside.level.value)
    second_value = _changed_level(ts)
    quote = _verbatim_quote(filing, ts)

    raw_second = copy.deepcopy(first.raw)
    raw_second["downside"]["level"]["value"] = second_value
    second_reply = json.dumps(raw_second)
    chosen_value = first_value if choice == "first" else second_value
    models = []

    def fake_chat(messages, *, model, max_tokens=16000, purpose="", **_kwargs):
        models.append(model)
        if purpose == "recheck":
            return _chat_result(_recheck_reply(choice, chosen_value, quote), model)
        return _chat_result(second_reply, model)

    result = crosscheck(filing, first, chat=fake_chat)
    return result, models


def test_crosscheck_recheck_picking_first_needs_no_user():
    result, models = _crosscheck_with_one_differing_field("first")

    assert result.second is not None
    assert result.error is None
    assert result.agreed is False
    assert len(result.diffs) == 1
    assert result.diffs[0].path == "downside.level"
    assert result.diffs[0].status == "resolved_first"
    assert result.needs_user == []
    # Second extraction on muse-spark-1.2, then the recheck model.
    assert models == ["muse-spark-1.2", "muse-spark-1.3"]


def test_crosscheck_recheck_picking_second_needs_user():
    result, _models = _crosscheck_with_one_differing_field("second")

    assert result.second is not None
    assert result.error is None
    assert len(result.diffs) == 1
    assert result.diffs[0].status == "resolved_second"
    assert [diff.path for diff in result.needs_user] == ["downside.level"]


def test_crosscheck_non_json_second_reply_sets_error_without_raising():
    filing, first = _load_first_extraction()
    models = []

    def fake_chat(messages, *, model, max_tokens=16000, purpose="", **_kwargs):
        models.append(model)
        return _chat_result("this is not JSON at all", model)

    result = crosscheck(filing, first, chat=fake_chat)

    assert result.second is None
    assert result.error
    assert result.diffs == []
    assert result.needs_user == []
    assert result.agreed is False
    assert result.first is first
    assert models == ["muse-spark-1.2"]


def test_crosscheck_uses_second_chat_for_second_extraction():
    filing, first = _load_first_extraction()
    reply_text = REPLY.read_text(encoding="utf-8")
    seen = []

    def main_chat(messages, **kwargs):
        raise AssertionError("main chat must not be used")

    def second_chat(messages, *, model, purpose="", **_kwargs):
        seen.append((model, purpose))
        return _chat_result(reply_text, model)

    result = crosscheck(
        filing,
        first,
        chat=main_chat,
        second_model="second-model",
        second_chat=second_chat,
        recheck_model="recheck-model",
        recheck_chat=second_chat,
    )
    assert result.agreed is True
    assert result.second_model == "second-model"
    assert ("second-model", "extract") in seen


OTHER_REPLY = ROOT / "tests" / "fixtures" / "extractions" / "jpm_424b2-ea0308498.reply.json"


def test_crosscheck_recheck_goes_to_recheck_chat_on_disagreement():
    filing, first = _load_first_extraction()
    other_text = OTHER_REPLY.read_text(encoding="utf-8")
    seen = []

    def main_chat(messages, **kwargs):
        raise AssertionError("main chat must not be used")

    def second_chat(messages, *, model, purpose="", **_kwargs):
        seen.append(("second", model, purpose))
        return _chat_result(other_text, model)

    def recheck_chat(messages, *, model, purpose="", **_kwargs):
        seen.append(("recheck", model, purpose))
        return _chat_result(_recheck_reply("neither", None, ""), model)

    result = crosscheck(
        filing,
        first,
        chat=main_chat,
        second_model="s2",
        second_chat=second_chat,
        recheck_model="r2",
        recheck_chat=recheck_chat,
    )
    assert result.agreed is False
    assert result.diffs
    assert ("second", "s2", "extract") in seen
    assert ("recheck", "r2", "recheck") in seen


def test_fielddiff_defaults_are_open_without_quote():
    diff = FieldDiff(path="downside.level", first=0.7, second=0.65, kind="value")
    assert diff.status == "open"
    assert diff.quote is None
    assert diff.note == ""
