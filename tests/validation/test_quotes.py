"""Offline tests for the quote verifier. No AI, no network."""

from __future__ import annotations

import copy
from pathlib import Path

from quantproof.ingest.html import read_filing
from quantproof.validation.quotes import (
    MIN_QUOTE_CHARS,
    QuoteCheck,
    normalise_for_match,
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
    assert reason.startswith("not found:")
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
