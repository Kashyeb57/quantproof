"""Offline tests for the quote verifier. No AI, no network."""

from __future__ import annotations

import copy
import enum
from datetime import date
from pathlib import Path

from quantproof.core.termsheet import (
    Autocall,
    AutocallObservation,
    BarrierObservation,
    Cited,
    Citation,
    Dependency,
    Downside,
    DownsideKind,
    TermSheet,
    Underlying,
)
from quantproof.ingest.html import read_filing
from quantproof.validation.quotes import (
    MIN_QUOTE_CHARS,
    QuoteCheck,
    date_forms,
    normalise_for_match,
    number_forms,
    percent_forms,
    value_forms,
    value_in_quote,
    verify_quote,
    verify_termsheet,
)


def test_normalise_examples() -> None:
    assert normalise_for_match("at least 9.7500% \u00d7 $1,000") == "at least 9.7500% $1 000"
    assert normalise_for_match("\u201cIndex\u201d") == "index"
    assert normalise_for_match("Russell 2000\u00ae") == "russell 2000"


def test_verify_quote_exact_found() -> None:
    filing = "The closing level of the Russell 2000 Index was 2000."
    normalised = normalise_for_match(filing)
    verified, reason = verify_quote("closing level of the Russell 2000 Index", normalised)
    assert verified is True
    assert reason == "found"


def test_verify_quote_punctuation_insensitive() -> None:
    # Filing uses curly quotes, an em dash and line breaks / extra spacing;
    # the quote uses straight quotes and a hyphen. Both normalise the same.
    filing = "The \u201cIndex\u201d \u2014 closing\nlevel   was 2000."
    normalised = normalise_for_match(filing)
    quote = 'The "Index" - closing level was 2000.'
    verified, reason = verify_quote(quote, normalised)
    assert verified is True
    assert reason == "found"


def test_verify_quote_changed_digit_not_found() -> None:
    filing = "You will receive at least 9.7500% per annum on the notes."
    normalised = normalise_for_match(filing)
    quote = "at least 9.7600% per annum on the notes"
    verified, reason = verify_quote(quote, normalised)
    assert verified is False
    # The reason must name the missing (normalised) piece.
    assert reason.startswith("not found:")
    assert "9.7600%" in reason


def test_verify_quote_too_short() -> None:
    # "70.00%" normalises to 6 chars < MIN_QUOTE_CHARS even if present.
    assert MIN_QUOTE_CHARS == 8
    filing = "The barrier is 70.00% of the initial level."
    normalised = normalise_for_match(filing)
    verified, reason = verify_quote("70.00%", normalised)
    assert verified is False
    assert reason == "too short"


def test_verify_quote_empty() -> None:
    normalised = normalise_for_match("some filing text about the notes")
    for quote in ["", "...", "\u2026"]:
        verified, reason = verify_quote(quote, normalised)
        assert verified is False
        assert reason == "empty"


def test_verify_quote_ellipsis_in_order_found() -> None:
    filing = "first passage some middle content second passage end of notes"
    normalised = normalise_for_match(filing)
    verified, reason = verify_quote("first passage ... second passage", normalised)
    assert verified is True
    assert reason == "found"


def test_verify_quote_ellipsis_reverse_order_not_found() -> None:
    filing = "first passage some middle content second passage end of notes"
    normalised = normalise_for_match(filing)
    verified, reason = verify_quote("second passage ... first passage", normalised)
    assert verified is False
    # Still rejected; the reason now says the text exists but comes before the previous piece.
    assert reason.startswith("out of order:")
    assert "first passage" in reason


def test_verify_quote_word_boundary() -> None:
    # "000 index" is a substring of "2000 index" but must not match because
    # the character before the match ("2") is not a word boundary.
    normalised = normalise_for_match("russell 2000 index")
    verified, reason = verify_quote("000 index", normalised)
    assert verified is False
    assert reason.startswith("not found:")
    # Sanity: the full token does match on boundaries.
    verified_full, _ = verify_quote("2000 index", normalised)
    assert verified_full is True


def _load_filing_text() -> str:
    path = (
        Path(__file__).resolve().parents[1].joinpath("fixtures", "filings", "jpm_424b2-38582.htm")
    )
    return read_filing(path.read_bytes()).text


def test_verify_termsheet_real_filing(jpm_38582_termsheet) -> None:
    ts = jpm_38582_termsheet
    filing_text = _load_filing_text()
    expected_paths = [p for p, _ in ts.cited_fields()]
    checks = verify_termsheet(ts, filing_text)
    # One check per cited field, in order.
    assert [c.path for c in checks] == expected_paths
    assert all(isinstance(c, QuoteCheck) for c in checks)
    cited_by_path = dict(ts.cited_fields())
    unverified = ts.unverified_fields()
    for check in checks:
        cited = cited_by_path[check.path]
        if cited.citation is None:
            assert check.quote is None
            assert check.verified is False
            assert check.reason == "no citation"
            assert check.path in unverified
        else:
            assert check.verified is True
            assert check.reason == "found"
            assert cited.citation.verified is True
            assert check.path not in unverified


def test_verify_termsheet_tampered_quote(jpm_38582_termsheet) -> None:
    ts = copy.deepcopy(jpm_38582_termsheet)
    filing_text = _load_filing_text()
    tampered_path = "underlyings[0].name"
    cited_by_path = dict(ts.cited_fields())
    assert tampered_path in cited_by_path
    assert cited_by_path[tampered_path].citation is not None
    cited_by_path[tampered_path].citation.quote = (
        "this tampered quote does not appear anywhere in the filing xyzzy 999999"
    )
    checks = verify_termsheet(ts, filing_text)
    by_path = {c.path: c for c in checks}
    tampered_check = by_path[tampered_path]
    assert tampered_check.verified is False
    assert tampered_check.reason.startswith("not found:")
    assert cited_by_path[tampered_path].citation.verified is False
    assert tampered_path in ts.unverified_fields()
    # Every other cited field still passes (or keeps its "no citation" state).
    for path, check in by_path.items():
        if path == tampered_path:
            continue
        cited = cited_by_path[path]
        if cited.citation is None:
            assert check.reason == "no citation"
            assert check.verified is False
        else:
            assert check.verified is True, path
            assert check.reason == "found", path
            assert cited.citation.verified is True


def test_sentence_final_period_does_not_block_a_match() -> None:
    # The filing says "... was 6,672.41." and the model's quote stops before the period.
    normalised = normalise_for_match("The S&P 500 Index on November 17, 2025 was 6,672.41. We")
    assert verify_quote("Index on November 17, 2025 was 6,672.41", normalised) == (True, "found")


def test_period_inside_a_number_is_part_of_the_token() -> None:
    # "at least 9" must not pass for a filing that says "at least 9.75%".
    normalised = normalise_for_match("pays at least 9.75% per annum")
    verified, reason = verify_quote("pays at least 9", normalised)
    assert verified is False and reason.startswith("not found:")
    # Same on the left: "75% per annum" is not a token boundary inside "9.75%".
    assert verify_quote("75% per annum", normalised)[0] is False


def test_four_dots_count_as_an_ellipsis() -> None:
    normalised = normalise_for_match("The notes are not bank deposits. They are not insured.")
    assert verify_quote("The notes are not bank deposits.... not insured", normalised) == (True, "found")


def test_number_forms_examples() -> None:
    forms_954 = number_forms(954.0)
    assert "954" in forms_954
    assert "954.0" in forms_954
    assert "954.00" in forms_954
    assert "954.000000" in forms_954
    forms_large = number_forms(2341.377)
    assert "2,341.377" in forms_large
    assert "2341.377" in forms_large
    assert "2,341.3770" in forms_large
    assert "2341.3770" in forms_large
    # 2341.377 needs 3 decimals, so integer forms must be absent.
    assert "2,341" not in forms_large
    assert "2341" not in forms_large
    forms_1000 = number_forms(1000.0)
    assert "1,000" in forms_1000
    assert "1000" in forms_1000


def test_percent_forms_examples() -> None:
    forms_70 = percent_forms(0.70)
    assert "70%" in forms_70
    assert "70.0%" in forms_70
    assert "70.00%" in forms_70
    forms_975 = percent_forms(0.0975)
    assert "9.75%" in forms_975
    assert "9.750%" in forms_975
    assert "9.7500%" in forms_975
    # Needs 2 decimals, so 1-decimal and integer forms must be absent.
    assert "9.7%" not in forms_975
    assert "10%" not in forms_975


def test_date_forms_examples() -> None:
    forms = date_forms(date(2026, 12, 2))
    expected = {
        "December 2, 2026",
        "December 02, 2026",
        "Dec. 2, 2026",
        "Dec 2, 2026",
        "2 December 2026",
        "2026-12-02",
        "12/2/2026",
        "12/02/2026",
    }
    for text in expected:
        assert text in forms


def test_value_in_quote_barrier_percent() -> None:
    quote = "Barrier Amount: With respect to each Index, 70.00% of its Initial Value"
    assert value_in_quote(0.70, quote) is True
    assert value_in_quote(0.75, quote) is False


def test_value_in_quote_review_dates() -> None:
    assert value_in_quote(date(2026, 12, 2), "Review Dates*:") is False
    assert (
        value_in_quote(date(2026, 12, 2), "Review Dates*: December 2, 2026, February 25, 2027")
        is True
    )


def test_value_in_quote_call_amount_per_notional() -> None:
    quote = "first Review Date: at least 9.7500% \u00d7 $1,000"
    assert value_in_quote(1097.5, quote, notional=1000.0, per_notional=True) is True
    # Without per_notional the premium-percent spelling does not apply.
    assert value_in_quote(1097.5, quote) is False
    assert value_in_quote(1097.5, quote, notional=1000.0, per_notional=False) is False


def test_value_in_quote_approx_954() -> None:
    assert value_in_quote(954.0, "approximately $954.00 per $1,000") is True


def test_value_in_quote_ticker_vs_index() -> None:
    assert value_in_quote("Russell 2000 Index", "Bloomberg ticker: RTY") is False
    assert (
        value_in_quote("Russell 2000 Index", "The Russell 2000\u00ae Index (Bloomberg ticker: RTY)")
        is True
    )


class _Colour(enum.Enum):
    RED = 1


def test_value_in_quote_bool_enum_none() -> None:
    assert value_in_quote(True, "anything True here") is None
    assert value_in_quote(False, "anything False here") is None
    assert value_in_quote(_Colour.RED, "RED colour quote") is None
    assert value_forms(True) is None
    assert value_forms(_Colour.RED) is None


def test_value_in_quote_at_least_9_boundary() -> None:
    # "9" must not match inside "9.75%" (period is part of the token).
    assert value_in_quote(9.0, "at least 9.75%") is False
    # Sanity: the exact number does match.
    assert value_in_quote(9.75, "at least 9.75%") is True


def test_value_in_quote_ellipsis_does_not_break_support() -> None:
    assert value_in_quote(0.70, "Barrier Amount ... 70.00% of its Initial Value") is True
    assert value_in_quote(0.70, "Barrier Amount \u2026 70.00% of its Initial Value") is True


def test_verify_termsheet_value_not_in_quote(jpm_38582_termsheet) -> None:
    ts = copy.deepcopy(jpm_38582_termsheet)
    filing_text = _load_filing_text()
    normalised_filing = normalise_for_match(filing_text)
    cited_by_path = dict(ts.cited_fields())
    try:
        notional_val = ts.notional.value  # type: ignore[union-attr]
    except AttributeError:
        notional_val = None
    target_path: str | None = None
    target_value = None
    replacement_quote: str | None = None
    for path, cited in cited_by_path.items():
        if cited.citation is None:
            continue
        val = getattr(cited, "value", None)
        if value_forms(val) is None:
            continue
        per = path.endswith(".call_amount") or path == "coupon.amount"
        if value_in_quote(val, cited.citation.quote, notional=notional_val, per_notional=per) is not True:
            continue
        for other_path, other_cited in cited_by_path.items():
            if other_path == path or other_cited.citation is None:
                continue
            other_quote = other_cited.citation.quote
            verified_other, _ = verify_quote(other_quote, normalised_filing)
            if not verified_other:
                continue
            if value_in_quote(val, other_quote, notional=notional_val, per_notional=per) is False:
                target_path = path
                target_value = val
                replacement_quote = other_quote
                break
        if target_path is not None:
            break
    assert target_path is not None, "could not find suitable field pair for value-support test"
    assert replacement_quote is not None
    cited_by_path[target_path].citation.quote = replacement_quote
    checks = verify_termsheet(ts, filing_text)
    by_path = {c.path: c for c in checks}
    check = by_path[target_path]
    assert check.verified is False
    assert check.reason.startswith("value not in quote:")
    assert cited_by_path[target_path].citation.verified is False
    assert target_path in ts.unverified_fields()


def test_reordered_passages_are_reported_as_out_of_order() -> None:
    # Day 2 live run: the model joined two real passages in reverse document order.
    normalised = normalise_for_match("Call Premium Amount is set below. Later: equal to $1,000 plus it.")
    verified, reason = verify_quote("equal to $1,000 plus it ... Call Premium Amount is set", normalised)
    assert verified is False and reason.startswith("out of order:")


def _small_termsheet(
    *,
    downside_kind: DownsideKind,
    downside_level: float,
    downside_quote: str,
    trigger: float | None = None,
    trigger_quote: str | None = None,
) -> tuple[TermSheet, str]:
    """Build a minimal one-underlying TermSheet plus a filing text containing every quote.

    Every non-target quote contains its own value, so only the target level/trigger
    determines its check. Filing text is the join of all quotes, so quote presence passes.
    """
    def _c(value, quote: str) -> Cited:
        return Cited(value, Citation(quote, "Key Terms"))

    autocall = None
    if trigger is not None:
        assert trigger_quote is not None
        autocall = Autocall(
            trigger=_c(trigger, trigger_quote),
            observations=[
                AutocallObservation(
                    review_date=_c(date(2027, 6, 2), "Review Date: 2027-06-02 single valuation date here"),
                    payment_date=_c(date(2027, 6, 7), "Payment Date: 2027-06-07 final settlement payment here"),
                    call_amount=_c(1000.0, "Call Amount: $1,000 per note if called early feature"),
                )
            ],
        )
    ts = TermSheet(
        issuer=_c("Test Bank LLC", "Issuer: Test Bank LLC offering documents for notes"),
        is_preliminary=_c(False, "This pricing supplement sets forth final terms for the notes"),
        underlyings=[
            Underlying(
                name=_c("Test Index", "Underlying: Test Index linked notes performance"),
                ticker=_c("TST", "Ticker TST for the underlying asset class here"),
            )
        ],
        dependency=_c(Dependency.WORST_OF, "Payments based on the worst-of performance of the underlyings"),
        notional=_c(1000.0, "Notional per note: $1,000 payable at maturity date"),
        issue_price=_c(1000.0, "Issue price: $1,000 per note on settlement date here"),
        pricing_date=_c(date(2025, 6, 2), "Pricing Date: 2025-06-02 as stated in terms here"),
        issue_date=_c(date(2025, 6, 5), "Issue Date: 2025-06-05 settlement of the notes here"),
        maturity_date=_c(date(2027, 6, 7), "Maturity Date: 2027-06-07 final payment date here"),
        final_review_date=_c(date(2027, 6, 2), "Final Review Date: 2027-06-02 valuation date here"),
        downside=Downside(
            kind=_c(downside_kind, "Buffered downside with protection feature described here"),
            level=_c(downside_level, downside_quote),
            observation=_c(BarrierObservation.FINAL_ONLY, "observed on the Final Review Date only for this note"),
        ),
        autocall=autocall,
        unsupported_features=[],
        missing_or_ambiguous=[],
    )
    filing_text = "\n".join(
        c.citation.quote for _, c in ts.cited_fields() if c.citation is not None
    )
    return ts, filing_text


def test_buffer_amount_supports_complement_level() -> None:
    ts, filing_text = _small_termsheet(
        downside_kind=DownsideKind.BUFFER,
        downside_level=0.60,
        downside_quote="Buffer Amount: 40.00% of the Initial Level protection here",
    )
    by_path = {c.path: c for c in verify_termsheet(ts, filing_text)}
    assert by_path["downside.level"].verified is True


def test_buffer_amount_does_not_support_same_number() -> None:
    ts, filing_text = _small_termsheet(
        downside_kind=DownsideKind.BUFFER,
        downside_level=0.40,
        downside_quote="Buffer Amount: 40.00% of the Initial Level protection here",
    )
    by_path = {c.path: c for c in verify_termsheet(ts, filing_text)}
    assert by_path["downside.level"].verified is False
    assert by_path["downside.level"].reason.startswith("value not in quote:")


def test_buffer_threshold_supports_stated_percent() -> None:
    ts, filing_text = _small_termsheet(
        downside_kind=DownsideKind.BUFFER,
        downside_level=0.825,
        downside_quote="Buffer Threshold: 82.50% of its Strike Value as stated here",
    )
    by_path = {c.path: c for c in verify_termsheet(ts, filing_text)}
    assert by_path["downside.level"].verified is True


def test_initial_value_phrase_supports_trigger_one() -> None:
    ts, filing_text = _small_termsheet(
        downside_kind=DownsideKind.BUFFER,
        downside_level=0.60,
        downside_quote="Buffer Amount: 40.00% of the Initial Level protection here",
        trigger=1.0,
        trigger_quote="The notes are called if the level is greater than or equal to the Initial Value stated",
    )
    by_path = {c.path: c for c in verify_termsheet(ts, filing_text)}
    assert by_path["autocall.trigger"].verified is True


def test_percent_of_initial_does_not_support_trigger_one() -> None:
    ts, filing_text = _small_termsheet(
        downside_kind=DownsideKind.BUFFER,
        downside_level=0.60,
        downside_quote="Buffer Amount: 40.00% of the Initial Level protection here",
        trigger=1.0,
        trigger_quote="Call Trigger: 80.00% of the Initial Value as stated herein",
    )
    by_path = {c.path: c for c in verify_termsheet(ts, filing_text)}
    assert by_path["autocall.trigger"].verified is False


def test_hundred_percent_of_initial_supports_trigger_one() -> None:
    ts, filing_text = _small_termsheet(
        downside_kind=DownsideKind.BUFFER,
        downside_level=0.60,
        downside_quote="Buffer Amount: 40.00% of the Initial Level protection here",
        trigger=1.0,
        trigger_quote="Call Trigger: 100.00% of its Initial Value as stated herein",
    )
    by_path = {c.path: c for c in verify_termsheet(ts, filing_text)}
    assert by_path["autocall.trigger"].verified is True


def test_barrier_level_with_buffer_amount_quote_not_supported() -> None:
    ts, filing_text = _small_termsheet(
        downside_kind=DownsideKind.BARRIER,
        downside_level=0.60,
        downside_quote="Buffer Amount: 40.00% of the Initial Level protection here",
    )
    by_path = {c.path: c for c in verify_termsheet(ts, filing_text)}
    assert by_path["downside.level"].verified is False


def test_normalise_keeps_sign_position_minus() -> None:
    # U+2212 unifies with hyphen-minus; a minus directly before digits is
    # evidence (a sign), while hyphens and ranges still collapse to space.
    assert normalise_for_match("a loss of \u22125.2% on the notes") == (
        "a loss of -5.2% on the notes"
    )
    assert normalise_for_match("a loss of -5.2% on the notes") == (
        "a loss of -5.2% on the notes"
    )
    assert normalise_for_match("well-known 5-10% range") == "well known 5 10% range"
    assert normalise_for_match("up +5.2% on the notes") == "up 5.2% on the notes"


def test_verify_quote_plus_quote_does_not_match_minus_filing() -> None:
    # Quote "+5.2%" against filing "\u22125.2%": the signs disagree.
    filing = "The underlier return for the period was \u22125.2% on the notes."
    normalised = normalise_for_match(filing)
    verified, reason = verify_quote(
        "underlier return for the period was +5.2% on the notes", normalised
    )
    assert verified is False
    assert reason.startswith("not found:")


def test_verify_quote_unsigned_quote_does_not_match_signed_filing() -> None:
    filing = "The underlier return for the period was -5.2% on the notes."
    normalised = normalise_for_match(filing)
    verified, _ = verify_quote(
        "underlier return for the period was 5.2% on the notes", normalised
    )
    assert verified is False


def test_verify_quote_minus_matches_typographic_minus() -> None:
    # Same sign in different orthography still verifies (no false alarm).
    filing = "The underlier return for the period was \u22125.2% on the notes."
    normalised = normalise_for_match(filing)
    verified, reason = verify_quote(
        "underlier return for the period was -5.2% on the notes", normalised
    )
    assert verified is True
    assert reason == "found"


def test_verify_quote_signed_match_still_verifies() -> None:
    filing = "The underlier return for the period was -5.2% on the notes."
    normalised = normalise_for_match(filing)
    verified, reason = verify_quote(
        "underlier return for the period was -5.2% on the notes", normalised
    )
    assert verified is True
    assert reason == "found"


def test_verify_quote_hyphenated_words_still_match() -> None:
    filing = "The well-known index level was 2000 at closing time."
    normalised = normalise_for_match(filing)
    verified, reason = verify_quote(
        "well known index level was 2000 at closing", normalised
    )
    assert verified is True
    assert reason == "found"


def test_value_in_quote_sign_matrix() -> None:
    # A negative value needs a negative number in the quote and vice versa,
    # in either minus orthography.
    assert value_in_quote(-0.052, "a loss of 5.2% on the notes") is False
    assert value_in_quote(0.052, "a decline of -5.2% on the notes") is False
    assert value_in_quote(0.052, "a gain of +5.2% on the notes") is True
    assert value_in_quote(-0.052, "a loss of -5.2% on the notes") is True
    assert value_in_quote(-0.052, "a loss of \u22125.2% on the notes") is True


def test_value_in_quote_ranges_and_iso_dates_unchanged() -> None:
    # Digit-digit hyphens are ranges, not signs: they still collapse.
    assert value_in_quote(0.10, "a coupon between 5-10% per annum") is True
    assert value_in_quote(date(2026, 12, 2), "maturing on 2026-12-02 in full") is True


def test_normalise_unifies_minus_around_dollar() -> None:
    # "-$5.20" and "$-5.20" are the same evidence; plus still collapses.
    assert normalise_for_match("a loss of -$5.20 on the notes") == (
        "a loss of -5.20 on the notes"
    )
    assert normalise_for_match("a loss of $-5.20 on the notes") == (
        "a loss of -5.20 on the notes"
    )
    assert normalise_for_match("a gain of +$5.20 on the notes") == (
        "a gain of $5.20 on the notes"
    )
    assert normalise_for_match("well-known $5-$10 range") == "well known $5 $10 range"


def test_verify_quote_plus_dollars_does_not_match_minus_dollars() -> None:
    filing = "The payment at maturity was -$5.20 per note, all in."
    normalised = normalise_for_match(filing)
    verified, reason = verify_quote(
        "payment at maturity was +$5.20 per note, all in", normalised
    )
    assert verified is False
    assert reason.startswith("not found:")


def test_verify_quote_dollar_minus_placements_unify() -> None:
    filing = "The payment at maturity was -$5.20 per note, all in."
    normalised = normalise_for_match(filing)
    for quote in (
        "payment at maturity was -$5.20 per note, all in",
        "payment at maturity was $-5.20 per note, all in",
    ):
        verified, reason = verify_quote(quote, normalised)
        assert verified is True
        assert reason == "found"
    verified, _ = verify_quote(
        "payment at maturity was $5.20 per note, all in", normalised
    )
    assert verified is False


def test_value_in_quote_currency_sign_matrix() -> None:
    assert value_in_quote(5.2, "loss -$5.20 on the notes") is False
    assert value_in_quote(5.2, "price $-5.20 on the notes") is False
    assert value_in_quote(-5.2, "loss -$5.20 on the notes") is True
    assert value_in_quote(-5.2, "loss $-5.20 on the notes") is True
    assert value_in_quote(-5.2, "loss \u2212$5.20 on the notes") is True
    assert value_in_quote(5.2, "cost $5.20 on the notes") is True
