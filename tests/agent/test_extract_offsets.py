"""Real audit replies that failed before the payment-lag / protection schema extension.

Uses the saved recordings under data/market_audit (no network, no key): the
model's first extraction reply for each note, and the saved filing.
"""

import copy
import gzip
import json
from datetime import date
from pathlib import Path

import pytest

from quantproof.agent.extract import ExtractionError, to_termsheet
from quantproof.core.calendar import add_business_days
from quantproof.ingest.html import read_filing
from quantproof.validation.quotes import verify_termsheet

AUDIT = Path(__file__).resolve().parents[2] / "data" / "market_audit"


def _reply(accession: str) -> dict:
    calls = json.loads((AUDIT / "calls" / f"{accession}.calls.json").read_text(encoding="utf-8"))
    return json.loads(calls[0]["text"])


def _filing_text(accession: str) -> str:
    return read_filing(gzip.decompress((AUDIT / "filings" / f"{accession}.htm.gz").read_bytes())).text


GS_PALANTIR = "0001193125-26-432141"  # "The fifth business day following the Call Observation Date"


def test_lag_in_payment_dates_quote_builds_payment_dates_and_verifies():
    ts = to_termsheet(_reply(GS_PALANTIR))
    obs = ts.autocall.observations
    assert [o.review_date.value for o in obs] == [date(2027, 10, 29)]
    assert obs[0].payment_date.value == date(2027, 11, 5)  # Fri + 5 business days
    checks = {c.path: c for c in verify_termsheet(ts, _filing_text(GS_PALANTIR))}
    assert checks["autocall.observations[0].payment_date"].verified is True


def test_explicit_lag_key_is_used_and_wrong_dates_fail_verification():
    data = _reply(GS_PALANTIR)
    quote = data["autocall"]["payment_dates"]["quote"]
    data["autocall"]["payment_dates"] = None
    data["autocall"]["payment_lag_business_days"] = {"value": 5, "quote": quote, "section": "x"}
    ts = to_termsheet(data)
    assert ts.autocall.observations[0].payment_date.value == date(2027, 11, 5)

    # A date the offset does not give is not supported by the offset quote.
    ts.autocall.observations[0].payment_date.value = date(2027, 11, 8)
    checks = {c.path: c for c in verify_termsheet(ts, _filing_text(GS_PALANTIR))}
    assert checks["autocall.observations[0].payment_date"].verified is False


@pytest.mark.parametrize("bad", [-1, 2.5, "five", True])
def test_bad_lag_value_is_reported(bad):
    data = _reply(GS_PALANTIR)
    data["autocall"]["payment_dates"] = None
    data["autocall"]["payment_lag_business_days"] = {"value": bad, "quote": "q", "section": "x"}
    with pytest.raises(ExtractionError) as err:
        to_termsheet(data)
    assert "autocall.payment_lag_business_days: expected a whole number >= 0" in str(err.value)


def test_null_payment_dates_without_any_lag_keeps_the_old_error():
    data = _reply(GS_PALANTIR)
    data["autocall"]["payment_dates"] = {"value": None, "quote": "the Call Payment Date", "section": "x"}
    with pytest.raises(ExtractionError) as err:
        to_termsheet(data)
    assert "autocall.payment_dates: expected list, got NoneType" in str(err.value)


def test_explicit_payment_dates_are_unchanged_by_the_extension():
    data = _reply(GS_PALANTIR)
    data["autocall"]["payment_dates"] = {
        "value": ["2027-11-05"], "quote": "November 5, 2027", "section": "x"}
    data["autocall"]["payment_lag_business_days"] = {"value": 9, "quote": "q", "section": "x"}
    ts = to_termsheet(copy.deepcopy(data))
    assert ts.autocall.observations[0].payment_date.value == date(2027, 11, 5)


@pytest.mark.parametrize(
    "accession",
    ["0001839882-26-052586", "0001918704-26-029389", "0001918704-26-031667"],
)
def test_downside_level_zero_is_principal_protection_and_unsupported(accession):
    """'if the final value is less than or equal to the initial value: $1,000' (or '$0' return)."""
    data = _reply(accession)
    ts = to_termsheet(data)
    names = [f.value for f in ts.unsupported_features]
    assert "principal protection (no loss of principal at maturity)" in names
    protection = next(f for f in ts.unsupported_features if f.value.startswith("principal"))
    # The protection is cited with the filing's own level quote (a $0 return or a
    # -100% row paying $1,000.00).
    assert protection.citation.quote == data["downside"]["level"]["quote"]
    assert any("placeholder" in m for m in ts.missing_or_ambiguous)


def test_add_business_days_matches_the_parser():
    assert add_business_days(date(2027, 10, 29), 5) == date(2027, 11, 5)
