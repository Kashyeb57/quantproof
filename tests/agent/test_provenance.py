"""Offline, deterministic tests for quantproof.agent.provenance.

No network and no model call is made: the end-to-end test uses the saved
JPM 38582 filing and recorded extraction fixtures, exactly as
tests/scripts/test_analyse.py builds its fast result.
"""

from __future__ import annotations

import importlib.util
import math
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quantproof.agent.provenance import (  # noqa: E402
    ProvenanceError,
    allowed_dates,
    allowed_numbers,
    check_provenance,
    find_dates,
    find_numbers,
    require_provenance,
    verified_quotes,
)

_SCRIPT = ROOT / "scripts" / "analyse.py"
_spec = importlib.util.spec_from_file_location("analyse_script_for_provenance", _SCRIPT)
analyse_module = importlib.util.module_from_spec(_spec)
assert _spec is not None and _spec.loader is not None
_spec.loader.exec_module(analyse_module)

STEM = "jpm_424b2-38582"
FILING = ROOT / "tests" / "fixtures" / "filings" / f"{STEM}.htm"
REPLY = ROOT / "tests" / "fixtures" / "extractions" / f"{STEM}.reply.json"

SAMPLE = (
    "$907.72 ± $1.45, P(loss) 9.6%, 1,000 notes, CUSIP 48136LQH5, "
    "p05 and N2, priced November 4, 2026"
)


def test_find_numbers_values_decimals_and_percent_flags():
    mentions = find_numbers(SAMPLE)

    # The CUSIP, "p05" and "N2" are identifiers, and the day/year are part
    # of the date, so none of those digits is a standalone number.
    assert [mention.value for mention in mentions] == [907.72, 1.45, 9.6, 1000]
    assert [mention.decimals for mention in mentions] == [2, 2, 1, 0]
    assert [mention.percent for mention in mentions] == [False, False, True, False]
    assert [mention.text for mention in mentions] == ["$907.72", "$1.45", "9.6%", "1,000"]


def test_find_numbers_skips_integer_list_index():
    # The 8 in "observations[8]" is a list index in a field path: it says
    # which observation is meant, not a quantity. Only the "$8" is a claim.
    mentions = find_numbers("observations[8].x costs $8")
    assert [mention.text for mention in mentions] == ["$8"]
    assert [mention.value for mention in mentions] == [8.0]

    # The full field-path spelling from the review issue behaves the same.
    path_mentions = find_numbers("autocall.observations[8].call_amount")
    assert path_mentions == []


def test_find_dates_month_name_and_iso():
    assert find_dates(SAMPLE) == [("November 4, 2026", date(2026, 11, 4))]
    assert find_dates("priced 2026-11-04") == [("2026-11-04", date(2026, 11, 4))]


def test_rounding_rule_for_percent_source_value():
    # allowed_numbers expands a stored fraction to its percent form, so the
    # source 0.0961 supplies the candidate 9.61 (and 0.0961 itself).
    # 0.0961 * 100 is 9.610000000000001 in binary floating point, so compare
    # with math.isclose rather than float equality.
    allowed = allowed_numbers(0.0961)
    assert any(math.isclose(value, 9.61) for value in allowed)

    # "9.6%" shows 1 decimal: tolerance 0.05, |9.6 - 9.61| = 0.01 passes.
    assert check_provenance("9.6%", allowed).ok
    # "9.61%" shows 2 decimals: tolerance 0.005, exact match passes.
    assert check_provenance("9.61%", allowed).ok
    # "9.7%" is 0.09 from 9.61, outside the 1-decimal tolerance of 0.05.
    report = check_provenance("9.7%", allowed)
    assert not report.ok
    assert [mention.text for mention in report.unmatched] == ["9.7%"]


def test_rounding_rule_for_money_source_value():
    allowed = allowed_numbers(907.7234)

    assert check_provenance("$907.72", allowed).ok
    # "$908" shows 0 decimals: tolerance 0.5, |908 - 907.7234| = 0.2766 passes.
    assert check_provenance("$908", allowed).ok
    # "$907.80" shows 2 decimals: tolerance 0.005, difference 0.0766 fails.
    report = check_provenance("$907.80", allowed)
    assert not report.ok
    assert [mention.text for mention in report.unmatched] == ["$907.80"]


def test_methodology_constant_is_allowed():
    report = check_provenance("1.96", [], constants=(1.96,))
    assert report.ok
    assert report.checked == 1
    # Without the stated constant the same number has no source.
    assert not check_provenance("1.96", []).ok


def test_negative_number_must_match_signed_allowed_value():
    assert check_provenance("-0.05", [-0.05]).ok
    report = check_provenance("-0.05", [0.05])
    assert not report.ok
    assert [mention.text for mention in report.unmatched] == ["-0.05"]


@dataclass
class _NestedSample:
    amount: float
    count: int
    flag: bool


def test_allowed_numbers_walks_nested_containers_and_excludes_bools():
    source = {
        "values": [2, 2.5, {"inner": (3, True, False)}],
        "sample": _NestedSample(amount=4.5, count=5, flag=True),
        "fraction": 0.5,
        "label": "no numbers collected from strings",
    }

    result = allowed_numbers(source)

    # bool is a subclass of int in Python; True/False must not contribute
    # 1.0/0.0 (nor their percent expansions 100.0/0.0).
    assert all(not isinstance(value, bool) for value in result)
    assert 1.0 not in result
    assert 0.0 not in result
    assert 100.0 not in result
    for expected in (2.0, 2.5, 3.0, 4.5, 5.0, 0.5):
        assert expected in result
    # The fraction 0.5 (abs <= 1) also supplies its percent form, 50.0.
    assert 50.0 in result


def test_unmatched_date_is_reported():
    report = check_provenance("priced November 4, 2026", [], dates=set())
    assert not report.ok
    assert report.unmatched_dates == ["November 4, 2026"]
    assert report.unmatched == []

    assert check_provenance(
        "priced November 4, 2026", [], dates={date(2026, 11, 4)}
    ).ok


def test_require_provenance_raises_naming_the_unmatched_number():
    with pytest.raises(ProvenanceError) as excinfo:
        require_provenance("the value is 12.34 here", {"engine_value": 1.0})
    assert "12.34" in str(excinfo.value)

    # The same call does not raise when the number is sourced.
    require_provenance("the value is 1.00 here", {"engine_value": 1.0})


@dataclass
class _FakeCitation:
    quote: str
    verified: bool


@dataclass
class _FakeCited:
    value: object
    citation: _FakeCitation


class _FakeTermSheet:
    """Minimal stand-in with the cited_fields() interface verified_quotes uses."""

    def __init__(self, cited_fields):
        self._cited_fields = cited_fields

    def cited_fields(self):
        return list(self._cited_fields)


def test_number_in_unverified_quote_is_reported_but_not_in_verified_quote():
    quote = "the coupon is 12.34% per annum"
    text = f'Filing quote: "{quote}"'

    # An unverified quote is never returned by verified_quotes, so nothing
    # is exempted and the number inside the quote is still reported.
    unverified_ts = _FakeTermSheet(
        [("coupon", _FakeCited(0.1234, _FakeCitation(quote, verified=False)))]
    )
    assert verified_quotes(unverified_ts) == []
    report = check_provenance(text, [], quotes=verified_quotes(unverified_ts))
    assert not report.ok
    assert [mention.text for mention in report.unmatched] == ["12.34%"]

    # The same quote, once verified, is a citation checked by the quote
    # verifier rather than a claim, so its number is not reported.
    verified_ts = _FakeTermSheet(
        [("coupon", _FakeCited(0.1234, _FakeCitation(quote, verified=True)))]
    )
    assert verified_quotes(verified_ts) == [quote]
    verified_report = check_provenance(
        text, [], quotes=verified_quotes(verified_ts)
    )
    assert verified_report.ok
    assert verified_report.checked == 0
    assert verified_report.unmatched == []


def test_verified_cited_string_value_exempts_its_digits_but_unverified_does_not():
    name = "S&P 500® Index"
    # The quote deliberately contains no digits, so any exemption of "500"
    # in the name can only come from the cited field's string value being
    # returned by verified_quotes, not from the quote itself.
    quote = "the underlying index will be observed"
    text = f"The underlying is {name}"

    # Verified: the name is backed by its verified quote, so verified_quotes
    # returns both the quote and the name, and "500" in the name is exempt.
    verified_ts = _FakeTermSheet(
        [("underlying.name", _FakeCited(name, _FakeCitation(quote, verified=True)))]
    )
    assert verified_quotes(verified_ts) == [quote, name]
    verified_report = check_provenance(
        text, [], quotes=verified_quotes(verified_ts)
    )
    assert verified_report.ok
    assert verified_report.checked == 0
    assert verified_report.unmatched == []

    # Unverified: neither the quote nor the value is returned, so "500"
    # in the name is still reported as an unsourced number.
    unverified_ts = _FakeTermSheet(
        [("underlying.name", _FakeCited(name, _FakeCitation(quote, verified=False)))]
    )
    assert verified_quotes(unverified_ts) == []
    unverified_report = check_provenance(
        text, [], quotes=verified_quotes(unverified_ts)
    )
    assert not unverified_report.ok
    assert [mention.text for mention in unverified_report.unmatched] == ["500"]


def _fast_38582_result_and_termsheet():
    """Build the analyse result exactly as tests/scripts/test_analyse.py does."""
    from quantproof.agent.extract import parse_json_reply, to_termsheet
    from quantproof.core.market import MarketInputs
    from quantproof.ingest.html import read_filing
    from quantproof.validation.quotes import verify_termsheet

    filing = read_filing(FILING.read_bytes())
    ts = to_termsheet(parse_json_reply(REPLY.read_text(encoding="utf-8")))
    checks = verify_termsheet(ts, filing.text)
    market = MarketInputs(
        as_of=ts.pricing_date.value,
        risk_free_rate=0.037,
        vols=(0.24, 0.17),
        dividend_yields=(0.012, 0.013),
        correlation=((1.0, 0.6), (0.6, 1.0)),
        credit_spread=0.006,
        sources={"all": "user-entered on the command line"},
    )
    result = analyse_module.analyse(
        ts,
        checks,
        market,
        paths=2000,
        base_paths=5000,
        seed=0,
        erp=0.05,
        battery=False,
        filing_text=filing.text,
    )
    return result, ts


def test_rendered_38582_report_has_full_provenance():
    result, ts = _fast_38582_result_and_termsheet()
    report = analyse_module.render_report(result)

    base = result["fair_value"]["base"]
    # The report writes the interval half-width as money, computed in
    # render_report as 1.96 * SE from the engine's base std_error. That
    # derived engine value is an allowed number in its own right; the
    # bare constant 1.96 alone could not match the rounded dollar amount.
    allowed = allowed_numbers(result, ts) + [1.96 * float(base["std_error"])]

    # Methodology constants written by render_report in scripts/analyse.py:
    constants = (
        1.96,  # "(1.96 SE, ...)" label and multiplier for the 95% interval half-width
        20.0,  # the "20%" threshold in the "- P(loss > 20%):" label
        50.0,  # the "50%" threshold in the "- P(loss > 50%):" label
    )

    # The report renders verbatim quotes from the TermSheet after
    # verify_termsheet has run, so numbers inside those verified quotes
    # are citations (already checked by the quote verifier), not claims.
    # Quotes appear as render_report writes them into table cells (_clean_cell).
    quotes = [analyse_module._clean_cell(q) for q in verified_quotes(ts)]

    check = check_provenance(
        report,
        allowed,
        dates=allowed_dates(result, ts),
        constants=constants,
        quotes=quotes,
    )
    if not check.ok:
        print("unmatched numbers:", [mention.text for mention in check.unmatched])
        print("unmatched dates:", check.unmatched_dates)
    assert check.ok

    tampered = report + "\n\na 12.34% chance\n"
    tampered_check = check_provenance(
        tampered,
        allowed,
        dates=allowed_dates(result, ts),
        constants=constants,
        quotes=quotes,
    )
    assert not tampered_check.ok
    assert "12.34%" in [mention.text for mention in tampered_check.unmatched]
