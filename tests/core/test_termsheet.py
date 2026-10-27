from dataclasses import replace
from datetime import date

import pytest

from quantproof.core import Cited, Dependency


def test_jpm_filing_builds(jpm_38582_termsheet):
    ts = jpm_38582_termsheet
    assert ts.n_underlyings == 2
    assert ts.dependency.value is Dependency.WORST_OF
    assert ts.is_preliminary.value is True
    assert len(ts.autocall.observations) == 17
    # The filing's stated minimums: first and last call amounts.
    assert ts.autocall.observations[0].call_amount.value == pytest.approx(1097.50)
    assert ts.autocall.observations[-1].call_amount.value == pytest.approx(1487.50)
    # Final review date coincides with the last autocall observation.
    assert ts.autocall.observations[-1].review_date.value == ts.final_review_date.value


def test_every_field_is_reported_unverified_until_the_verifier_runs(jpm_38582_termsheet):
    ts = jpm_38582_termsheet
    paths = [p for p, _ in ts.cited_fields()]
    assert "issuer" in paths
    assert "underlyings[1].reference_level" in paths
    assert "autocall.observations[16].call_amount" in paths
    assert "downside.level" in paths
    # Nothing has been verified yet, so every cited field is flagged.
    assert ts.unverified_fields() == paths


def test_verified_fields_drop_out_and_uncited_fields_stay_flagged(jpm_38582_termsheet):
    ts = jpm_38582_termsheet
    for _, cited in ts.cited_fields():
        cited.citation.verified = True
    assert ts.unverified_fields() == []
    ts.issuer.citation.verified = False
    ts.cusip = Cited("48136LQH5")  # a value with no citation at all
    assert set(ts.unverified_fields()) == {"issuer", "cusip"}


def test_rejects_no_underlyings(jpm_38582_termsheet):
    with pytest.raises(ValueError, match="at least one underlying"):
        replace(jpm_38582_termsheet, underlyings=[])


def test_rejects_dates_out_of_order(jpm_38582_termsheet):
    with pytest.raises(ValueError, match="maturity"):
        replace(jpm_38582_termsheet, maturity_date=Cited(date(2025, 1, 1)))


def test_rejects_barrier_given_as_percent_instead_of_fraction(jpm_38582_termsheet):
    ts = jpm_38582_termsheet
    bad = replace(ts.downside, level=Cited(70.0))
    with pytest.raises(ValueError, match="fraction"):
        replace(ts, downside=bad)


def test_rejects_unsorted_autocall_dates(jpm_38582_termsheet):
    ts = jpm_38582_termsheet
    obs = list(reversed(ts.autocall.observations))
    with pytest.raises(ValueError, match="increasing"):
        replace(ts, autocall=replace(ts.autocall, observations=obs))
