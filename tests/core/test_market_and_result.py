from datetime import date

import pytest

from quantproof.core import MarketInputs, Measure, PricingResult

AS_OF = date(2025, 11, 25)


def two_assets(rho: float) -> MarketInputs:
    return MarketInputs(AS_OF, 0.04, (0.22, 0.15), (0.012, 0.013), ((1.0, rho), (rho, 1.0)),
                        credit_spread=0.008, sources={"vols": "user-entered"})


def test_valid_two_asset_inputs():
    m = two_assets(0.8)
    assert m.n == 2
    assert m.correlation_matrix()[0, 1] == 0.8


def test_perfect_correlation_is_allowed():
    # rho = 1 is singular but valid (positive semi-definite); the proof battery needs it.
    assert two_assets(1.0).n == 2


def test_single_asset_helper():
    m = MarketInputs.single(AS_OF, 0.10, 0.20)
    assert m.n == 1 and m.correlation == ((1.0,),)


@pytest.mark.parametrize("corr, msg", [
    (((1.0, 0.5), (0.4, 1.0)), "symmetric"),
    (((0.9, 0.5), (0.5, 1.0)), "diagonal"),
    (((1.0, 1.2), (1.2, 1.0)), r"\[-1, 1\]"),
])
def test_rejects_invalid_correlation(corr, msg):
    with pytest.raises(ValueError, match=msg):
        MarketInputs(AS_OF, 0.04, (0.2, 0.2), (0.0, 0.0), corr)


def test_rejects_correlations_no_joint_distribution_can_have():
    # Each pair is a legal correlation, but A~B and B~C strongly positive with A~C strongly
    # negative is impossible: the matrix has a negative eigenvalue.
    corr = ((1.0, 0.9, -0.9), (0.9, 1.0, 0.9), (-0.9, 0.9, 1.0))
    with pytest.raises(ValueError, match="semi-definite"):
        MarketInputs(AS_OF, 0.04, (0.2,) * 3, (0.0,) * 3, corr)


def test_rejects_mismatched_lengths_and_bad_vols():
    with pytest.raises(ValueError, match="dividend"):
        MarketInputs(AS_OF, 0.04, (0.2, 0.2), (0.0,), ((1.0, 0.0), (0.0, 1.0)))
    with pytest.raises(ValueError, match="positive"):
        MarketInputs.single(AS_OF, 0.04, 0.0)


def test_closed_form_result_has_zero_width_interval():
    r = PricingResult(4.76, Measure.RISK_NEUTRAL, "bsm.call")
    assert not r.is_monte_carlo
    assert r.confidence_interval() == (4.76, 4.76)


def test_monte_carlo_result_requires_seed_paths_and_std_error():
    r = PricingResult(950.0, Measure.RISK_NEUTRAL, "mc", seed=7, paths=100_000, std_error=0.5)
    assert r.confidence_interval() == pytest.approx((950 - 1.96 * 0.5, 950 + 1.96 * 0.5))
    with pytest.raises(ValueError, match="together"):
        PricingResult(950.0, Measure.RISK_NEUTRAL, "mc", paths=100_000)


def test_result_records_package_version_and_measure():
    r = PricingResult(0.18, Measure.REAL_WORLD, "risk.loss_probability",
                      seed=1, paths=10, std_error=0.01)
    assert r.measure is Measure.REAL_WORLD
    assert r.version == "0.1.0"


def test_rejects_non_finite_price():
    with pytest.raises(ValueError, match="finite"):
        PricingResult(float("nan"), Measure.RISK_NEUTRAL, "bsm.call")
