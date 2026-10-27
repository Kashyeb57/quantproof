"""Tests for quantproof.agent.extract. Offline; the API is never called."""

import json
from datetime import date
from pathlib import Path

import pytest

from quantproof.agent import extract
from quantproof.agent.client import ChatResult
from quantproof.agent.costs import Usage
from quantproof.agent.extract import (
    ExtractionError,
    build_messages,
    extract_termsheet,
    parse_json_reply,
    to_termsheet,
)
from quantproof.core.termsheet import BarrierObservation, Dependency, DownsideKind
from quantproof.ingest.html import Filing


def _reply() -> dict:
    """Fresh valid reply: single-underlying buffered note, coupon, no autocall."""
    return {
        "issuer": {
            "value": "JPMorgan Chase Financial Company LLC",
            "quote": "Issuer: JPMorgan Chase Financial Company LLC",
            "section": "Cover",
        },
        "guarantor": {
            "value": "JPMorgan Chase & Co.",
            "quote": "Guarantor: JPMorgan Chase & Co.",
            "section": "Cover",
        },
        "cusip": {"value": "48132TAA1", "quote": "CUSIP: 48132TAA1", "section": "Cover"},
        "is_preliminary": {
            "value": False,
            "quote": "This pricing supplement sets forth final terms",
            "section": "Cover",
        },
        "underlyings": [
            {
                "name": {
                    "value": "S&P 500 Index",
                    "quote": "Underlying: S&P 500 Index (SPX)",
                    "section": "Key Terms",
                },
                "ticker": {
                    "value": "SPX",
                    "quote": "Underlying: S&P 500 Index (SPX)",
                    "section": "Key Terms",
                },
                "initial_level": {
                    "value": 6000.0,
                    "quote": "Initial Level: 6000.00",
                    "section": "Key Terms",
                },
                "reference_level": None,
                "reference_date": None,
            }
        ],
        "dependency": {
            "value": "worst_of",
            "quote": "Payments based on the worst-of performance",
            "section": "Key Terms",
        },
        "notional": {"value": 1000, "quote": "Notional per note: $1,000", "section": "Key Terms"},
        "issue_price": {
            "value": 1000,
            "quote": "Issue price: $1,000 per note",
            "section": "Key Terms",
        },
        "pricing_date": {
            "value": "2025-06-02",
            "quote": "Pricing Date: 2025-06-02",
            "section": "Key Terms",
        },
        "issue_date": {
            "value": "2025-06-05",
            "quote": "Issue Date: 2025-06-05",
            "section": "Key Terms",
        },
        "maturity_date": {
            "value": "2027-06-07",
            "quote": "Maturity Date: 2027-06-07",
            "section": "Key Terms",
        },
        "final_review_date": {
            "value": "2027-06-02",
            "quote": "Final Review Date: 2027-06-02",
            "section": "Key Terms",
        },
        "downside": {
            "kind": {
                "value": "buffer",
                "quote": "Buffered downside with Buffer Amount 90.00%",
                "section": "Key Terms",
            },
            "level": {
                "value": 0.9,
                "quote": "Buffer Amount 90.00% of Initial Level",
                "section": "Key Terms",
            },
            "observation": {
                "value": "final_only",
                "quote": "observed on the Final Review Date only",
                "section": "Key Terms",
            },
            "gearing": None,
        },
        "autocall": None,
        "coupon": {
            "barrier": {
                "value": 0.7,
                "quote": "Coupon Barrier Amount 70.00% of Initial Level",
                "section": "Key Terms",
            },
            "amount": {
                "value": 25.0,
                "quote": "Contingent coupon of $25.00 per $1,000 note",
                "section": "Key Terms",
            },
            "memory": {
                "value": False,
                "quote": "no memory feature for missed coupons",
                "section": "Key Terms",
            },
        },
        "estimated_value": {
            "value": 940.0,
            "quote": "Estimated value of $940.00 per note",
            "section": "Cover",
        },
        "estimated_value_floor": None,
        "max_selling_commission": {
            "value": 30.0,
            "quote": "Maximum selling commission of $30.00",
            "section": "Cover",
        },
        "missing_or_ambiguous": [],
    }


FILING_TEXT = "\n".join(
    [
        "Cover",
        "Issuer: JPMorgan Chase Financial Company LLC",
        "Guarantor: JPMorgan Chase & Co.",
        "CUSIP: 48132TAA1",
        "This pricing supplement sets forth final terms",
        "Estimated value of $940.00 per note",
        "Maximum selling commission of $30.00",
        "Key Terms",
        "Underlying: S&P 500 Index (SPX)",
        "Initial Level: 6000.00",
        "Payments based on the worst-of performance",
        "Notional per note: $1,000",
        "Issue price: $1,000 per note",
        "Pricing Date: 2025-06-02",
        "Issue Date: 2025-06-05",
        "Maturity Date: 2027-06-07",
        "Final Review Date: 2027-06-02",
        "Buffered downside with Buffer Amount 90.00%",
        "Buffer Amount 90.00% of Initial Level",
        "observed on the Final Review Date only",
        "Coupon Barrier Amount 70.00% of Initial Level",
        "Contingent coupon of $25.00 per $1,000 note",
        "no memory feature for missed coupons",
    ]
)


def _filing(text: str = FILING_TEXT) -> Filing:
    return Filing(text=text, charset="utf-8", images=())


def _chat_result(text: str, finish_reason: str = "stop") -> ChatResult:
    # ChatResult fields assumed: text, model, finish_reason, usage,
    # cost_usd, seconds, attempts. Usage assumed: (input_tokens, output_tokens).
    return ChatResult(
        text=text,
        model="muse-spark-1.3",
        finish_reason=finish_reason,
        usage=Usage(input_tokens=10, output_tokens=20),
        cost_usd=0.0,
        seconds=0.0,
        attempts=1,
    )


def test_parse_json_reply_with_fence() -> None:
    reply = _reply()
    fenced = "```json\n" + json.dumps(reply) + "\n```"
    assert parse_json_reply(fenced) == reply


def test_parse_json_reply_with_leading_prose() -> None:
    reply = _reply()
    text = "Here is the JSON you asked for:\n" + json.dumps(reply) + "\nHope that helps."
    assert parse_json_reply(text) == reply


def test_parse_json_reply_garbage_raises() -> None:
    with pytest.raises(ExtractionError):
        parse_json_reply("not json at all {{{")


def test_to_termsheet_maps_every_field() -> None:
    ts = to_termsheet(_reply())
    assert ts.issuer.value == "JPMorgan Chase Financial Company LLC"
    assert isinstance(ts.issuer.value, str)
    assert ts.guarantor is not None and ts.guarantor.value == "JPMorgan Chase & Co."
    assert ts.cusip is not None and ts.cusip.value == "48132TAA1"
    assert ts.is_preliminary.value is False
    assert len(ts.underlyings) == 1
    assert ts.underlyings[0].name.value == "S&P 500 Index"
    assert ts.underlyings[0].ticker is not None and ts.underlyings[0].ticker.value == "SPX"
    assert ts.underlyings[0].initial_level is not None
    assert ts.underlyings[0].initial_level.value == 6000.0
    assert isinstance(ts.underlyings[0].initial_level.value, float)
    assert ts.underlyings[0].reference_level is None
    assert ts.underlyings[0].reference_date is None
    assert ts.dependency.value is Dependency.WORST_OF
    assert ts.notional.value == 1000.0
    assert ts.issue_price.value == 1000.0
    assert ts.pricing_date.value == date(2025, 6, 2)
    assert ts.issue_date.value == date(2025, 6, 5)
    assert ts.maturity_date.value == date(2027, 6, 7)
    assert ts.final_review_date.value == date(2027, 6, 2)
    assert ts.downside.kind.value is DownsideKind.BUFFER
    assert ts.downside.level.value == 0.9
    assert ts.downside.observation.value is BarrierObservation.FINAL_ONLY
    # Null gearing keeps the dataclass default: 1.0 with no citation.
    assert ts.downside.gearing.value == 1.0
    assert ts.downside.gearing.citation is None
    assert ts.autocall is None
    assert ts.coupon is not None
    assert ts.coupon.barrier.value == 0.7
    assert ts.coupon.amount.value == 25.0
    assert ts.coupon.memory.value is False
    assert ts.estimated_value is not None and ts.estimated_value.value == 940.0
    assert ts.estimated_value_floor is None
    assert ts.max_selling_commission is not None and ts.max_selling_commission.value == 30.0
    assert ts.missing_or_ambiguous == []


def test_autocall_three_observations_share_quote_with_separate_citations() -> None:
    reply = _reply()
    reply["autocall"] = {
        "trigger": {
            "value": 1.0,
            "quote": "Call Trigger 100.00% of Initial Level",
            "section": "Key Terms",
        },
        "amounts_are_minimums": {
            "value": False,
            "quote": "call amounts are fixed and not minimums",
            "section": "Key Terms",
        },
        "review_dates": {
            "value": ["2026-06-02", "2026-12-02", "2027-06-02"],
            "quote": "Review Dates: 2026-06-02, 2026-12-02, 2027-06-02",
            "section": "Key Terms",
        },
        "payment_dates": {
            "value": ["2026-06-05", "2026-12-07", "2027-06-07"],
            "quote": "Payment Dates: 2026-06-05, 2026-12-07, 2027-06-07",
            "section": "Key Terms",
        },
        "call_amounts": {
            "value": [1097.5, 1150.0, 1200.0],
            "quote": "Call Amounts: $1097.50, $1150.00, $1200.00",
            "section": "Key Terms",
        },
    }
    ts = to_termsheet(reply)
    assert ts.autocall is not None
    obs = ts.autocall.observations
    assert len(obs) == 3
    assert [o.review_date.value for o in obs] == [
        date(2026, 6, 2),
        date(2026, 12, 2),
        date(2027, 6, 2),
    ]
    assert [o.payment_date.value for o in obs] == [
        date(2026, 6, 5),
        date(2026, 12, 7),
        date(2027, 6, 7),
    ]
    assert [o.call_amount.value for o in obs] == [1097.5, 1150.0, 1200.0]
    # Each observation shares its list quote but owns its Citation object.
    assert obs[0].review_date.citation is not obs[1].review_date.citation
    assert obs[0].review_date.citation.quote == "Review Dates: 2026-06-02, 2026-12-02, 2027-06-02"
    assert obs[1].review_date.citation.quote == "Review Dates: 2026-06-02, 2026-12-02, 2027-06-02"
    assert obs[0].payment_date.citation.quote == "Payment Dates: 2026-06-05, 2026-12-07, 2027-06-07"
    assert obs[2].call_amount.citation.quote == "Call Amounts: $1097.50, $1150.00, $1200.00"


def test_mismatched_lengths_bad_date_unknown_enum_reported_together() -> None:
    reply = _reply()
    reply["dependency"] = {
        "value": "best_of",
        "quote": "Payments based on the best-of performance",
        "section": "Key Terms",
    }
    reply["pricing_date"] = {
        "value": "June 2nd, 2025",
        "quote": "Pricing Date: June 2nd, 2025",
        "section": "Key Terms",
    }
    reply["autocall"] = {
        "trigger": {
            "value": 1.0,
            "quote": "Call Trigger 100.00% of Initial Level",
            "section": "Key Terms",
        },
        "amounts_are_minimums": {
            "value": False,
            "quote": "call amounts are fixed and not minimums",
            "section": "Key Terms",
        },
        "review_dates": {
            "value": ["2026-06-02", "2026-12-02", "2027-06-02"],
            "quote": "Review Dates: 2026-06-02, 2026-12-02, 2027-06-02",
            "section": "Key Terms",
        },
        "payment_dates": {
            "value": ["2026-06-05", "2026-12-07"],
            "quote": "Payment Dates: 2026-06-05, 2026-12-07",
            "section": "Key Terms",
        },
        "call_amounts": {
            "value": [1097.5, 1150.0, 1200.0],
            "quote": "Call Amounts: $1097.50, $1150.00, $1200.00",
            "section": "Key Terms",
        },
    }
    with pytest.raises(ExtractionError) as excinfo:
        to_termsheet(reply)
    joined = " ".join(excinfo.value.problems)
    # 3 standard checks: one problem per defect, all in a single error.
    assert "dependency" in joined
    assert "pricing_date" in joined
    assert "lengths differ" in joined
    assert len(excinfo.value.problems) >= 3


def test_missing_required_key_reported() -> None:
    reply = _reply()
    del reply["issuer"]
    with pytest.raises(ExtractionError) as excinfo:
        to_termsheet(reply)
    assert any("issuer" in p for p in excinfo.value.problems)


def test_dates_out_of_order_surface_as_extraction_error() -> None:
    reply = _reply()
    reply["issue_date"] = {
        "value": "2025-06-01",
        "quote": "Issue Date: 2025-06-01",
        "section": "Key Terms",
    }
    with pytest.raises(ExtractionError):
        to_termsheet(reply)


def test_build_messages_puts_filing_text_first_and_images_after() -> None:
    images = [{"type": "image", "path": "diagram.png"}]
    messages = build_messages("hello filing", images)
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    content = messages[1]["content"]
    assert content[0]["type"] == "text"
    assert "hello filing" in content[0]["text"]
    assert content[1] == {"type": "image", "path": "diagram.png"}
    assert content[-1]["type"] == "text"


def test_extract_termsheet_happy_path_verifies_all_quotes() -> None:
    reply = _reply()
    seen: dict = {}

    def fake_chat(messages, *, model, max_tokens, purpose, **kwargs):
        seen["purpose"] = purpose
        seen["messages"] = messages
        return _chat_result(json.dumps(reply))

    extraction = extract_termsheet(_filing(), chat=fake_chat)
    assert seen["purpose"] == "extract"
    assert extraction.raw == reply
    # The reply leaves gearing null, so the 1.0 default is used. It has no quote in the filing,
    # so it must be flagged (rule 5a), never passed off as verified.
    assert [(c.path, c.reason) for c in extraction.checks if c.reason != "found"] == [
        ("downside.gearing", "no citation")]
    assert extraction.unverified == ["downside.gearing"]


def test_extract_termsheet_quote_missing_makes_path_unverified() -> None:
    reply = _reply()
    reply["issuer"] = {
        "value": "Nonexistent Corp XYZ",
        "quote": "Issuer: Nonexistent Corp XYZ Incorporated",
        "section": "Cover",
    }

    def fake_chat(messages, *, model, max_tokens, purpose, **kwargs):
        return _chat_result(json.dumps(reply))

    extraction = extract_termsheet(_filing(), chat=fake_chat)
    assert "issuer" in extraction.unverified
    by_path = {c.path: c for c in extraction.checks}
    assert by_path["issuer"].verified is False


def test_extract_termsheet_truncated_reply_raises_and_records_purpose() -> None:
    seen: dict = {}

    def fake_chat(messages, *, model, max_tokens, purpose, **kwargs):
        seen["purpose"] = purpose
        return _chat_result(json.dumps(_reply()), finish_reason="length")

    with pytest.raises(ExtractionError):
        extract_termsheet(_filing(), chat=fake_chat)
    assert seen["purpose"] == "extract"


def test_image_paths_use_client_image_part(tmp_path: Path, monkeypatch) -> None:
    reply = _reply()
    created = tmp_path / "diagram.png"
    created.write_bytes(b"fake")
    calls: list = []
    real_image_part = extract.client.image_part

    def spy_image_part(path):
        calls.append(path)
        return {"type": "image", "path": str(path)}

    monkeypatch.setattr(extract.client, "image_part", spy_image_part)

    def fake_chat(messages, *, model, max_tokens, purpose, **kwargs):
        assert messages[1]["content"][1] == {"type": "image", "path": str(created)}
        return _chat_result(json.dumps(reply))

    extract_termsheet(_filing(), image_paths=[created], chat=fake_chat)
    assert calls == [created]
    monkeypatch.setattr(extract.client, "image_part", real_image_part)
