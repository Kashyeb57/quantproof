"""Tests for quantproof.models.montecarlo (offline, deterministic seeds)."""

import numpy as np
import pytest

from quantproof.models import bsm
from quantproof.models.montecarlo import (
    correlation_factor,
    mc_mean_and_se,
    simulate_performance,
)


def test_correlation_factor_reproduces_3x3() -> None:
    corr = np.array(
        [[1.0, 0.5, 0.2], [0.5, 1.0, 0.3], [0.2, 0.3, 1.0]]
    )  # strictly diagonally dominant, hence positive definite
    factor = correlation_factor(corr)
    np.testing.assert_allclose(factor @ factor.T, corr, rtol=0, atol=1e-12)


def test_correlation_factor_singular_all_ones() -> None:
    # Correlation 1 makes the matrix singular, so Cholesky fails and the eigen
    # fallback must still reproduce it.
    corr = np.ones((2, 2))
    factor = correlation_factor(corr)
    np.testing.assert_allclose(factor @ factor.T, corr, rtol=0, atol=1e-12)


def test_correlation_factor_rejects_non_psd() -> None:
    # Determinant = 1 - 0.81 - 0.81 < 0, so one eigenvalue is negative.
    bad = np.array([[1.0, 0.9, 0.9], [0.9, 1.0, 0.0], [0.9, 0.0, 1.0]])
    with pytest.raises(ValueError):
        correlation_factor(bad)


def test_correlation_factor_rejects_bad_diagonal() -> None:
    bad = np.array([[1.0, 0.5], [0.5, 0.9]])
    with pytest.raises(ValueError):
        correlation_factor(bad)


def test_shape_and_reproducibility() -> None:
    times = [0.5, 1.0]
    vols = [0.2, 0.3]
    divs = [0.01, 0.02]
    drifts = [0.05, 0.05]
    corr = [[1.0, 0.6], [0.6, 1.0]]
    first = simulate_performance(times, vols, divs, corr, drifts, 1000, seed=123)
    assert first.shape == (1000, 2, 2)
    assert np.all(first > 0)
    second = simulate_performance(times, vols, divs, corr, drifts, 1000, seed=123)
    np.testing.assert_array_equal(first, second)  # same seed -> identical draws
    third = simulate_performance(times, vols, divs, corr, drifts, 1000, seed=124)
    assert not np.array_equal(first, third)  # different seed -> different draws


def test_antithetic_pairs_mirror() -> None:
    # With -z as the partner, diffusion cancels and the pair sums to twice drift.
    times = np.array([0.5, 1.0])
    vols = np.array([0.2, 0.3])
    divs = np.array([0.01, 0.02])
    drifts = np.array([0.05, 0.04])
    corr = np.array([[1.0, 0.6], [0.6, 1.0]])
    n_paths = 6
    out = simulate_performance(times, vols, divs, corr, drifts, n_paths, seed=99,
                               antithetic=True)
    log_r = np.log(out)
    drift = drifts - divs - 0.5 * vols**2
    for i in range(n_paths // 2):
        for k, t in enumerate(times):
            # Exact cancellation up to floating-point error, hence atol 1e-12.
            np.testing.assert_allclose(
                log_r[i, k, :] + log_r[i + n_paths // 2, k, :],
                2.0 * drift * t,
                rtol=0,
                atol=1e-12,
            )


@pytest.mark.parametrize("antithetic", [True, False])
def test_expectation_matches_theory(antithetic: bool) -> None:
    # E[R(T)] = exp((mu - q) T) for GBM; check within 4 SE (about 99.993%
    # coverage under the CLT, so a fixed seed passes deterministically barring
    # a ~6e-5 event).
    vols = [0.2, 0.3]
    divs = [0.02, 0.01]
    drifts = [0.05, 0.05]
    corr = [[1.0, 0.6], [0.6, 1.0]]
    out = simulate_performance([1.0], vols, divs, corr, drifts, 200_000,
                               seed=12345, antithetic=antithetic)
    for j in range(2):
        mean, se = mc_mean_and_se(out[:, 0, j], antithetic=antithetic)
        theory = np.exp((drifts[j] - divs[j]) * 1.0)
        assert abs(mean - theory) <= 4 * se


def test_vol_and_sample_correlation() -> None:
    # With n = 200k, SE(std) ~= theory / sqrt(2n) (~0.00016 relative), so a 1%
    # relative tolerance is about 60 SEs; SE(corr) ~= (1 - rho^2)/sqrt(n)
    # ~= 0.0014, so abs tol 0.01 is about 7 SEs. Both pass deterministically.
    vols = [0.2, 0.3]
    divs = [0.02, 0.01]
    drifts = [0.05, 0.05]
    corr = [[1.0, 0.6], [0.6, 1.0]]
    out = simulate_performance([1.0], vols, divs, corr, drifts, 200_000,
                               seed=777, antithetic=False)
    log_r = np.log(out[:, 0, :])
    for j in range(2):
        sample_std = float(np.std(log_r[:, j], ddof=1))
        theory_std = vols[j] * np.sqrt(1.0)
        assert sample_std == pytest.approx(theory_std, rel=0.01)
    sample_corr = float(np.corrcoef(log_r[:, 0], log_r[:, 1])[0, 1])
    assert sample_corr == pytest.approx(0.6, abs=0.01)


def test_correlation_one_gives_identical_columns() -> None:
    times = [0.5, 1.0]
    vols = [0.2, 0.2]
    divs = [0.01, 0.01]
    drifts = [0.05, 0.05]
    corr = [[1.0, 1.0], [1.0, 1.0]]
    out = simulate_performance(times, vols, divs, corr, drifts, 1000, seed=5)
    np.testing.assert_allclose(out[:, :, 0], out[:, :, 1], rtol=0, atol=1e-12)


def test_european_call_against_bsm() -> None:
    # Risk-neutral MC value versus the closed-form BSM price; 3 SE is about
    # 99.7% coverage, so the fixed seed below must land inside (fails with
    # probability ~0.003 by chance; the seed was chosen in this suite).
    s0, strike, maturity = 100.0, 100.0, 1.0
    rate, div, vol = 0.05, 0.02, 0.2
    n_paths = 200_000
    out = simulate_performance([maturity], [vol], [div], [[1.0]], [rate],
                               n_paths, seed=20240501, antithetic=True)
    payoffs = np.maximum(s0 * out[:, 0, 0] - strike, 0.0)
    disc = np.exp(-rate * maturity) * payoffs
    mean, se = mc_mean_and_se(disc, antithetic=True)
    benchmark = float(bsm.call_price(s0, strike, maturity, rate, vol, div))
    assert abs(mean - benchmark) <= 3 * se


def test_mc_mean_and_se_handmade_antithetic() -> None:
    # Pairs are (1, 3) and (3, 1): pair means [2, 2], so mean 2 and se 0.
    mean, se = mc_mean_and_se(np.array([1.0, 3.0, 3.0, 1.0]), antithetic=True)
    assert mean == pytest.approx(2.0)
    assert se == pytest.approx(0.0)
