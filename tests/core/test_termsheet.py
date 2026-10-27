from dataclasses import replace
from datetime import date

import pytest

from quantproof.core import Cited, ContingentCoupon, CouponObservation, Dependency


def _valid_coupon() -> ContingentCoupon:
    """Two monthly coupon observations with valid barrier and amount."""
    return ContingentCoupon(
        barrier=Cited(0.7),
        amount=Cited(25.0),
        memory=Cited(False),
        observations=[
            CouponObservation(
                review_date=Cited(date(2026, 6, 2)),
                payment_date=Cited(date(2026, 6, 5)),
            ),
            CouponObservation(
                review_date=Cited(date(2026, 9, 2)),
                payment_date=Cited(date(2026, 9, 5)),
            ),
        ],
    )


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


def test_rejects_coupon_with_no_observations(jpm_38582_termsheet):
    ts = jpm_38582_termsheet
    bad = replace(_valid_coupon(), observations=[])
    with pytest.raises(ValueError, match="at least one observation"):
        replace(ts, coupon=bad)


def test_rejects_coupon_review_dates_not_strictly_increasing(jpm_38582_termsheet):
    ts = jpm_38582_termsheet
    dup = CouponObservation(
        review_date=Cited(date(2026, 6, 2)),
        payment_date=Cited(date(2026, 6, 6)),
    )
    bad = replace(_valid_coupon(), observations=[_valid_coupon().observations[0], dup])
    with pytest.raises(ValueError, match="increasing"):
        replace(ts, coupon=bad)


def test_rejects_coupon_payment_before_review(jpm_38582_termsheet):
    ts = jpm_38582_termsheet
    bad_obs = CouponObservation(
        review_date=Cited(date(2026, 6, 2)),
        payment_date=Cited(date(2026, 6, 1)),
    )
    bad = replace(_valid_coupon(), observations=[bad_obs])
    with pytest.raises(ValueError, match="payment date before review"):
        replace(ts, coupon=bad)


def test_rejects_coupon_nonpositive_amount(jpm_38582_termsheet):
    ts = jpm_38582_termsheet
    bad = replace(_valid_coupon(), amount=Cited(0.0))
    with pytest.raises(ValueError, match="positive"):
        replace(ts, coupon=bad)


def test_rejects_coupon_barrier_outside_unit_interval(jpm_38582_termsheet):
    ts = jpm_38582_termsheet
    with pytest.raises(ValueError, match="fraction"):
        replace(ts, coupon=replace(_valid_coupon(), barrier=Cited(0.0)))
    with pytest.raises(ValueError, match="fraction"):
        replace(ts, coupon=replace(_valid_coupon(), barrier=Cited(1.5)))
