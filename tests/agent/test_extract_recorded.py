"""The recorded live extraction of the Day 1 filing, checked against the hand-built term sheet.

`jpm_38582_termsheet` (tests/conftest.py) was typed in by hand from the filing; the recorded
reply is what Muse Spark returned for the same filing. Every value must agree, and every quote
in the reply must be found in the filing text.
"""

from pathlib import Path

import pytest

from quantproof.agent.extract import parse_json_reply, to_termsheet
from quantproof.core.termsheet import BarrierObservation, DownsideKind
from quantproof.ingest.html import read_filing
from quantproof.validation.quotes import normalise_for_match, verify_termsheet

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture(scope="module")
def filing_text() -> str:
    return read_filing((FIXTURES / "filings" / "jpm_424b2-38582.htm").read_bytes()).text


@pytest.fixture
def extracted():
    text = (FIXTURES / "extractions" / "jpm_424b2-38582.reply.json").read_text(encoding="utf-8")
    return to_termsheet(parse_json_reply(text))


def comparable(value):
    # Names differ only in typography ("Russell 2000® Index" vs "Russell 2000 Index").
    return normalise_for_match(value) if isinstance(value, str) else value


def test_every_value_matches_the_hand_built_term_sheet(extracted, jpm_38582_termsheet):
    by_hand = dict(jpm_38582_termsheet.cited_fields())
    by_model = dict(extracted.cited_fields())
    assert by_model.keys() == by_hand.keys()  # same fields present, including 17 observations
    mismatches = {p: (by_model[p].value, by_hand[p].value) for p in by_hand
                  if comparable(by_model[p].value) != comparable(by_hand[p].value)}
    assert mismatches == {}
    assert extracted.autocall.amounts_are_minimums is True
    assert extracted.underlyings[0].initial_level is None  # set on the pricing date


def test_every_model_quote_is_in_the_filing(extracted, filing_text):
    checks = verify_termsheet(extracted, filing_text)
    not_found = [(c.path, c.reason) for c in checks if c.reason != "found"]
    # The filing never states a gearing, so the 1.0 default is flagged, not verified.
    assert not_found == [("downside.gearing", "no citation")]
    assert extracted.unverified_fields() == ["downside.gearing"]


# ---------------------------------------------------------------------------
# Three additional recorded extractions (2026-10-27).
# Each case reads tests/fixtures/filings/<stem>.htm with read_filing, parses
# tests/fixtures/extractions/<stem>.reply.json with parse_json_reply +
# to_termsheet, and runs verify_termsheet. Shared quote checks are one
# parametrize; economics are one test per filing. Values were checked by hand
# against the filings. Floats are exact JSON literals so == is used (no
# tolerance needed). Dates are compared as ISO YYYY-MM-DD strings to handle
# date / datetime / Cited / str uniformly.
# ---------------------------------------------------------------------------


def _v(field):
    """Unwrap Cited (.value) or return the plain value."""
    return field.value if hasattr(field, "value") else field


def _iso(field):
    """ISO YYYY-MM-DD string for a date-like field (handles Cited)."""
    v = _v(field)
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()[:10]
    return str(v)


def _load_termsheet(stem):
    text = (FIXTURES / "extractions" / f"{stem}.reply.json").read_text(encoding="utf-8")
    return to_termsheet(parse_json_reply(text))


RECORDED_QUOTE_CASES = [
    ("jpm_424b2-ea0308498", ["dependency", "downside.gearing"]),
    ("jpm_424b2-ea0309017", []),
    ("jpm_424b2-ea0306095", []),
]


@pytest.mark.parametrize("stem,expected_unverified", RECORDED_QUOTE_CASES)
def test_recorded_quotes_verified(stem, expected_unverified):
    filing_text = read_filing((FIXTURES / "filings" / f"{stem}.htm").read_bytes()).text
    extracted = _load_termsheet(stem)
    checks = verify_termsheet(extracted, filing_text)
    not_found = [(c.path, c.reason) for c in checks if c.reason != "found"]
    # Every quote is "found" except the listed paths.
    assert {p for p, _ in not_found} == set(expected_unverified)
    by_reason = dict(not_found)
    if "downside.gearing" in by_reason:
        assert by_reason["downside.gearing"] == "no citation"
    if "dependency" in by_reason:
        # The model paraphrased the dependency, so the quote is not in the filing.
        assert by_reason["dependency"].startswith("not found")
    got = extracted.unverified_fields()
    # Accept either order (document vs alphabetical); contents must match.
    assert got == expected_unverified or got == list(reversed(expected_unverified))
    assert sorted(got) == sorted(expected_unverified)


def test_ea0308498_single_stock_economics():
    # Final; single stock Expand Energy, ticker EXE.
    t = _load_termsheet("jpm_424b2-ea0308498")
    assert _v(t.is_preliminary) is False
    assert len(t.underlyings) == 1
    assert _v(t.underlyings[0].ticker) == "EXE"
    assert "expand" in str(_v(t.underlyings[0].name)).lower()
    assert _v(t.downside.level) == 0.72
    assert _v(t.downside.kind) == DownsideKind.BARRIER
    assert _v(t.downside.observation) == BarrierObservation.FINAL_ONLY
    assert _v(t.coupon.barrier) == 0.72
    assert _v(t.coupon.amount) == 8.4583
    assert _v(t.coupon.memory) is False
    assert len(t.coupon.observations) == 13
    assert _iso(t.coupon.observations[0].review_date) == "2026-11-09"
    assert _iso(t.coupon.observations[-1].review_date) == "2027-11-09"
    assert _v(t.autocall.trigger) == 1.0
    assert len(t.autocall.observations) == 7
    assert _iso(t.autocall.observations[0].review_date) == "2027-04-09"
    assert [_v(o.call_amount) for o in t.autocall.observations] == [1000.0] * 7
    assert _iso(t.pricing_date) == "2026-10-09"
    assert _iso(t.issue_date) == "2026-10-15"
    assert _iso(t.final_review_date) == "2027-11-09"
    assert _iso(t.maturity_date) == "2027-11-15"
    assert _v(t.estimated_value) == 959.8
    assert t.upside is None
    assert t.unsupported_features == []


def test_ea0309017_worst_of_three_indices_economics():
    # Final; worst of S&P 500, Russell 2000, Nasdaq-100; 3 underlyings.
    t = _load_termsheet("jpm_424b2-ea0309017")
    assert _v(t.is_preliminary) is False
    assert len(t.underlyings) == 3
    assert _v(t.downside.level) == 0.825
    assert _v(t.downside.kind) == DownsideKind.BUFFER
    assert _v(t.downside.gearing) == 1.21212
    assert _v(t.downside.observation) == BarrierObservation.FINAL_ONLY
    assert _v(t.coupon.barrier) == 0.825
    assert _v(t.coupon.amount) == 9.1667
    assert _v(t.coupon.memory) is False
    assert len(t.coupon.observations) == 9
    assert _iso(t.coupon.observations[0].review_date) == "2026-11-16"
    assert _iso(t.coupon.observations[-1].review_date) == "2027-07-14"
    assert _v(t.autocall.trigger) == 1.0
    assert len(t.autocall.observations) == 7
    assert _iso(t.autocall.observations[0].review_date) == "2026-12-14"
    assert [_v(o.call_amount) for o in t.autocall.observations] == [1000.0] * 7
    assert _iso(t.pricing_date) == "2026-10-15"
    assert _iso(t.issue_date) == "2026-10-20"
    assert _iso(t.final_review_date) == "2027-07-14"
    assert _iso(t.maturity_date) == "2027-07-19"
    assert _v(t.estimated_value) == 993.2
    assert _v(t.max_selling_commission) is None


def test_ea0306095_dual_directional_economics():
    # Final; Dual Directional Buffered Return Enhanced Notes, worst of three stocks.
    t = _load_termsheet("jpm_424b2-ea0306095")
    assert _v(t.is_preliminary) is False
    assert [_v(u.ticker) for u in t.underlyings] == ["AMZN", "TSCO", "ORCL"]
    assert [_v(u.initial_level) for u in t.underlyings] == [251.19, 32.82, 150.59]
    assert _v(t.downside.level) == 0.60
    assert _v(t.downside.kind) == DownsideKind.BUFFER
    assert _v(t.downside.gearing) == 1.0
    assert _v(t.downside.observation) == BarrierObservation.FINAL_ONLY
    assert _v(t.upside.leverage) == 2.0
    assert _v(t.upside.cap) is None
    assert _v(t.autocall.trigger) == 1.0
    assert len(t.autocall.observations) == 1
    assert _iso(t.autocall.observations[0].review_date) == "2027-09-23"
    assert _iso(t.autocall.observations[0].payment_date) == "2027-09-28"
    assert _v(t.autocall.observations[0].call_amount) == 1350.0
    assert t.coupon is None
    assert _iso(t.pricing_date) == "2026-09-17"
    assert _iso(t.issue_date) == "2026-09-22"
    assert _iso(t.final_review_date) == "2028-09-18"
    assert _iso(t.maturity_date) == "2028-09-21"
    assert _v(t.estimated_value) == 980.0
    assert [f.value for f in t.unsupported_features] == ["Absolute Stock Return"]
