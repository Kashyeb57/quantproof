"""Offline tests for quantproof/market/vol.py.

Synthetic geometric Brownian paths with known volatility (fixed seed):
the realised estimator must recover sigma within tolerance. No network.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quantproof.market import vol


def _gbm_closes(sigma: float, n_days: int, seed: int, s0: float = 100.0) -> list[float]:
    rng = np.random.default_rng(seed)
    dt = 1 / 252
    shocks = rng.normal(0.0, 1.0, size=n_days)
    closes = [s0]
    for z in shocks:
        closes.append(closes[-1] * float(np.exp(-0.5 * sigma**2 * dt + sigma * np.sqrt(dt) * z)))
    return closes


def test_log_returns_formula():
    np.testing.assert_allclose(vol.log_returns([100.0, 110.0, 99.0]), np.log([1.1, 0.9]))


def test_realised_vol_recovers_known_sigma():
    # Four independent 4-year paths at sigma = 0.25, averaged: the
    # estimator's 1-sigma error per path is ~1/sqrt(2*1008) ~= 2%, so
    # the 5% band on the 4-path average (~1% error) cannot depend on
    # one unlucky seed.
    estimates = [vol.realised_vol(_gbm_closes(0.25, 1008, seed=s)) for s in (7, 8, 9, 10)]
    assert float(np.mean(estimates)) == pytest.approx(0.25, rel=0.05)


def test_realised_vol_needs_spread_and_clean_input():
    with pytest.raises(ValueError):
        vol.realised_vol([100.0, 101.0])
    with pytest.raises(ValueError):
        vol.realised_vol([100.0, -1.0, 101.0])


def test_pairwise_correlation_recovers_known_rho():
    # Four independent 4-year path pairs sharing rho = 0.6 of their
    # shocks, averaged: the per-pair 1-sigma error is
    # ~(1-rho^2)/sqrt(N) ~= 0.02, so the averaged band cannot depend
    # on one unlucky draw.
    def build(z, sigma=0.20):
        dt = 1 / 252
        closes = [100.0]
        for shock in z:
            closes.append(closes[-1] * float(np.exp(-0.5 * sigma**2 * dt + sigma * np.sqrt(dt) * shock)))
        return closes

    estimates = []
    for seed in (11, 12, 13, 14):
        rng = np.random.default_rng(seed)
        z1 = rng.normal(size=1008)
        z2 = 0.6 * z1 + float(np.sqrt(1 - 0.6**2)) * rng.normal(size=1008)
        estimates.append(vol.pairwise_correlation(build(z1), build(z2)))
    assert float(np.mean(estimates)) == pytest.approx(0.6, abs=0.06)


def test_correlation_matrix_shape_and_constant_rejection():
    series = [_gbm_closes(0.2, 260, seed=k) for k in (1, 2)]
    matrix = vol.correlation_matrix(series)
    assert matrix.shape == (2, 2)
    assert matrix[0, 0] == matrix[1, 1] == 1.0
    assert matrix[0, 1] == matrix[1, 0]
    with pytest.raises(ValueError, match="constant"):
        vol.pairwise_correlation([100.0] * 10, _gbm_closes(0.2, 9, seed=3))


def test_trailing_window_selects_and_requires_three():
    base = date(2025, 11, 25)
    dated = [(base - timedelta(days=k), 100.0 + k) for k in range(400, -1, -1)]
    closes = vol.trailing_window(dated, base, window_days=365)
    assert len(closes) == 366
    assert closes[-1] == 100.0  # ends on the pricing date
    with pytest.raises(ValueError, match="at least 3"):
        vol.trailing_window(dated[:2], base)


def test_grids_centre_and_clip():
    assert vol.vol_grid(0.24) == (0.19, 0.24, 0.29)
    assert vol.vol_grid(0.02, half_range=0.05) == (0.01, 0.02, 0.07)
    assert vol.corr_grid(0.6) == (0.35, 0.6, 0.85)
    assert vol.corr_grid(0.95) == (0.7, 0.95, 0.99)
