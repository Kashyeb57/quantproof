"""Offline tests for live mode (quantproof/app/live.py) with fakes.

The fake chats return the recorded 38582 extraction reply and the fake
EDGAR client returns the saved filing bytes: no network, no API call,
no credits.  These tests prove the pipeline wiring (download -> extract
with Muse Spark 1.3 -> quote verification -> cross-check with Muse
Spark Open on Baseten, or an unavailable cross-check), not model
quality.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quantproof.agent import baseten as baseten_client
from quantproof.agent.client import MetaAPIError
from quantproof.app import live

STEM = "jpm_424b2-38582"
FILING_BYTES = (ROOT / "tests" / "fixtures" / "filings" / f"{STEM}.htm").read_bytes()
REPLY_TEXT = (
    ROOT / "tests" / "fixtures" / "extractions" / f"{STEM}.reply.json"
).read_text(encoding="utf-8")
OTHER_REPLY_TEXT = (
    ROOT / "tests" / "fixtures" / "extractions" / "jpm_424b2-ea0306095.reply.json"
).read_text(encoding="utf-8")


class FakeEdgar:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def get(self, url: str) -> bytes:
        self.urls.append(url)
        return FILING_BYTES


def fake_chat(messages, **kwargs):
    return SimpleNamespace(
        text=REPLY_TEXT, finish_reason="stop", cost_usd=0.05,
        model=kwargs.get("model", live.LIVE_MODEL),
    )


def fake_open_chat(messages, **kwargs):
    return SimpleNamespace(
        text=REPLY_TEXT, finish_reason="stop", cost_usd=0.06,
        model=kwargs.get("model", live.SECOND_MODEL),
    )


def fake_disagreeing_open_chat(messages, **kwargs):
    # A complete, parseable extraction of a DIFFERENT note: every shared
    # field disagrees, so rechecks run and leftovers stay visible.
    return SimpleNamespace(
        text=OTHER_REPLY_TEXT, finish_reason="stop", cost_usd=0.06,
        model=kwargs.get("model", live.SECOND_MODEL),
    )


def test_filing_from_url_goes_through_edgar_client():
    edgar = FakeEdgar()
    filing = live.filing_from_url("https://www.sec.gov/example.htm", edgar_client=edgar)
    assert edgar.urls == ["https://www.sec.gov/example.htm"]
    assert "JPMorgan" in filing.text


def test_filing_from_upload_parses_bytes():
    filing = live.filing_from_upload(FILING_BYTES)
    assert "JPMorgan" in filing.text


def test_extract_and_crosscheck_names_both_models_and_splits_costs():
    filing = live.filing_from_upload(FILING_BYTES)
    note = live.extract_and_crosscheck(
        filing, "upload", chat=fake_chat, second_chat=fake_open_chat
    )
    assert note.model == "muse-spark-1.3"
    assert note.second_model == baseten_client.OPEN_MODEL
    assert note.second_model == live.SECOND_MODEL
    assert note.termsheet.cusip.value == "48136LQH5"
    # Only downside.gearing lacks a verified quote in this fixture; it is
    # flagged for the user, never priced silently.
    assert note.unverified == ["downside.gearing"]
    assert note.agreed is True
    assert note.diffs == []
    assert note.crosscheck_error is None
    assert note.cost_usd == pytest.approx(0.05)  # Meta first extraction
    assert note.baseten_cost_usd == pytest.approx(0.06)  # Baseten second


def test_staged_pipeline_matches_one_shot_and_progress_labels():
    filing = live.filing_from_upload(FILING_BYTES)
    extraction = live.extract_only(filing, chat=fake_chat)
    note = live.finish_live_note(
        filing, "upload", extraction, chat=fake_chat, second_chat=fake_open_chat
    )
    assert note.unverified == ["downside.gearing"]
    assert note.agreed is True
    assert note.second_model == live.SECOND_MODEL
    assert [step for step in live.PROGRESS_STEPS] == [
        "Downloading the filing",
        "Checking it is a 424B2 pricing supplement",
        "Extracting terms with Muse Spark 1.3",
        "Verifying quotes against the filing",
        "Cross-checking with a second extraction",
        "Done",
    ]
    assert "1-2 minutes" in live.LIVE_RUN_NOTICE


def test_live_mode_refuses_any_other_model():
    filing = live.filing_from_upload(FILING_BYTES)
    with pytest.raises(ValueError, match="muse-spark-1.3 only"):
        live.extract_and_crosscheck(
            filing, "upload", chat=fake_chat, model="muse-wonder-1.0"
        )
    with pytest.raises(ValueError, match="cross-checks with"):
        live.extract_and_crosscheck(
            filing,
            "upload",
            chat=fake_chat,
            second_chat=fake_open_chat,
            second_model="muse-spark-1.2",
        )


def test_missing_second_extraction_is_unavailable_never_agreement():
    filing = live.filing_from_upload(FILING_BYTES)
    note = live.extract_and_crosscheck(filing, "upload", chat=fake_chat)
    assert note.agreed is False
    assert note.diffs == []
    assert note.crosscheck_error is not None
    assert "unavailable" in note.crosscheck_error
    assert note.second_model == live.SECOND_MODEL
    assert note.baseten_cost_usd == pytest.approx(0.0)
    # The first extraction still stands on its own verified quotes.
    assert note.termsheet.cusip.value == "48136LQH5"


def test_baseten_failure_is_unavailable_never_agreement():
    filing = live.filing_from_upload(FILING_BYTES)

    def missing_key_chat(messages, **kwargs):
        raise MetaAPIError(
            "BASETEN_API_KEY is not set", status=None, detail="missing key"
        )

    note = live.extract_and_crosscheck(
        filing, "upload", chat=fake_chat, second_chat=missing_key_chat
    )
    assert note.agreed is False
    assert note.diffs == []
    assert "unavailable" in note.crosscheck_error
    assert note.baseten_cost_usd == pytest.approx(0.0)


def test_incomplete_second_extraction_is_unavailable():
    filing = live.filing_from_upload(FILING_BYTES)

    def cutoff_chat(messages, **kwargs):
        return SimpleNamespace(
            text='{"cusip": "48136LQH5"',
            finish_reason="length",
            cost_usd=0.06,
            model=kwargs.get("model", live.SECOND_MODEL),
        )

    note = live.extract_and_crosscheck(
        filing, "upload", chat=fake_chat, second_chat=cutoff_chat
    )
    assert note.agreed is False
    assert note.diffs == []
    assert "unavailable" in note.crosscheck_error


def test_disagreements_stay_visible_and_rechecks_are_bounded():
    filing = live.filing_from_upload(FILING_BYTES)
    recheck_calls = []
    counting_meta = _counting_chat(fake_chat, recheck_calls)
    note = live.extract_and_crosscheck(
        filing, "upload", chat=counting_meta, second_chat=fake_disagreeing_open_chat
    )
    assert note.agreed is False
    assert note.crosscheck_error is None
    assert len(note.diffs) > live.RECHECK_ALLOWANCE
    # At most RECHECK_ALLOWANCE rechecks ran; every other disagreement
    # stays open for the user (needs_user shows non-first-wins).
    assert len(recheck_calls) == live.RECHECK_ALLOWANCE
    assert all(diff.status != "resolved_first" for diff in note.diffs)
    assert {diff.status for diff in note.diffs} <= {"open", "unresolved", "resolved_second"}


def _counting_chat(inner, recheck_calls):
    def chat(messages, **kwargs):
        if kwargs.get("purpose") == "recheck":
            recheck_calls.append(kwargs.get("model"))
        return inner(messages, **kwargs)

    return chat


def test_baseten_configured_reads_env_mapping_only():
    assert live.baseten_configured({"BASETEN_API_KEY": "k"}) is True
    assert live.baseten_configured({"BASETEN_API_KEY": "  "}) is False
    assert live.baseten_configured({}) is False


def test_open_chat_binds_baseten_log_without_calling():
    chat = live.open_chat(env={"BASETEN_API_KEY": "k"})
    assert chat.keywords["log_path"] == baseten_client.DEFAULT_LOG
    assert chat.keywords["env"] == {"BASETEN_API_KEY": "k"}


def test_baseten_spend_helpers_read_a_tmp_log(tmp_path):
    import datetime
    import json

    log = tmp_path / "baseten_calls.jsonl"
    today = datetime.datetime.now(datetime.timezone.utc).date()
    old = today - datetime.timedelta(days=2)
    rows = [
        {"time": f"{today}T00:00:01+00:00", "cost_usd": 0.06},
        {"time": f"{today}T12:00:00+00:00", "cost_usd": 0.04},
        {"time": f"{old}T00:00:00+00:00", "cost_usd": 10.0},
        {"not": "a cost line"},
    ]
    log.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    assert live.baseten_daily_spend(log) == pytest.approx(0.10)
    assert live.baseten_total_spend(log) == pytest.approx(10.10)
    missing = tmp_path / "missing.jsonl"
    assert live.baseten_daily_spend(missing) == 0.0
    assert live.baseten_total_spend(missing) == 0.0


def test_unsupported_features_come_with_their_quotes():
    from quantproof.agent.extract import parse_json_reply, to_termsheet

    reply = (
        ROOT / "tests" / "fixtures" / "extractions" / "jpm_424b2-ea0306095.reply.json"
    ).read_text(encoding="utf-8")
    ts = to_termsheet(parse_json_reply(reply))
    features = live.unsupported_features(ts)
    assert [name for name, _quote in features] == ["Absolute Stock Return"]
    assert all(quote for _name, quote in features)
