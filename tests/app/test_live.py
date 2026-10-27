"""Offline tests for live mode (quantproof/app/live.py) with fakes.

The fake chat returns the recorded 38582 extraction reply and the fake
EDGAR client returns the saved filing bytes: no network, no API call,
no credits.  These tests prove the pipeline wiring (download -> extract
with Muse Spark 1.3 -> quote verification -> cross-check), not model
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

from quantproof.app import live

STEM = "jpm_424b2-38582"
FILING_BYTES = (ROOT / "tests" / "fixtures" / "filings" / f"{STEM}.htm").read_bytes()
REPLY_TEXT = (
    ROOT / "tests" / "fixtures" / "extractions" / f"{STEM}.reply.json"
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


def test_filing_from_url_goes_through_edgar_client():
    edgar = FakeEdgar()
    filing = live.filing_from_url("https://www.sec.gov/example.htm", edgar_client=edgar)
    assert edgar.urls == ["https://www.sec.gov/example.htm"]
    assert "JPMorgan" in filing.text


def test_filing_from_upload_parses_bytes():
    filing = live.filing_from_upload(FILING_BYTES)
    assert "JPMorgan" in filing.text


def test_extract_and_crosscheck_verifies_quotes_and_agrees():
    filing = live.filing_from_upload(FILING_BYTES)
    note = live.extract_and_crosscheck(filing, "upload", chat=fake_chat)
    assert note.model == "muse-spark-1.3"
    assert note.termsheet.cusip.value == "48136LQH5"
    # Only downside.gearing lacks a verified quote in this fixture; it is
    # flagged for the user, never priced silently.
    assert note.unverified == ["downside.gearing"]
    assert note.agreed is True
    assert note.diffs == []
    assert note.cost_usd == pytest.approx(0.10)  # two fake extractions


def test_staged_pipeline_matches_one_shot_and_progress_labels():
    filing = live.filing_from_upload(FILING_BYTES)
    extraction = live.extract_only(filing, chat=fake_chat)
    note = live.finish_live_note(filing, "upload", extraction, chat=fake_chat)
    assert note.unverified == ["downside.gearing"]
    assert note.agreed is True
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


def test_unsupported_features_come_with_their_quotes():
    from quantproof.agent.extract import parse_json_reply, to_termsheet

    reply = (
        ROOT / "tests" / "fixtures" / "extractions" / "jpm_424b2-ea0306095.reply.json"
    ).read_text(encoding="utf-8")
    ts = to_termsheet(parse_json_reply(reply))
    features = live.unsupported_features(ts)
    assert [name for name, _quote in features] == ["Absolute Stock Return"]
    assert all(quote for _name, quote in features)
