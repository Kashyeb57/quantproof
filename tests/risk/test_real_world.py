"""Tests for quantproof.risk.real_world.

Offline and deterministic: fixed seeds, saved JPM fixture, small path
counts except for the closed-form checks, where 100_000 paths are used
as specified so that 4-binomial-SE tolerances are meaningfully tight.

Every tolerance is justified in a comment next to it.
"""

from __future__ import annotations

import copy
import json
import math
from dataclasses import replace
from datetime import timedelta

import pytest

from quantproof.core.market import MarketInputs
from quantproof.core.result import Measure
from quantproof.models.discounting import year_fraction
from quantproof.risk.real_world import loss_odds, real_world_drifts, stress_heatmap
from quantproof.validation.battery import _barrier_at_maturity_setup


def _market(ts):
    """The market specified in the task for the JPM fixture."""
    return MarketInputs(
        as_of=ts.pricing_date.value,
        risk_free_rate=0.037,
        vols=(0.24, 0.17),
        dividend_yields=(0.012, 0.013),
        correlation=((1, 0.6), (0.6, 1)),
        credit_spread=0.006,
    )


def _phi(x: float) -> float:
    """Standard normal CDF via erf (exact closed form, no approximation)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


# Cache the expensive 100_000-path one-asset runs so the probability and
# expected-cash tests share the same deterministic simulation per drift.
_ONE_ASSET_CACHE: dict[float, tuple] = {}


def _one_asset_run(ts, market, mu: float):
    """Return (ts1, m1, result) for the one-asset barrier note at drift mu."""
    if mu not in _ONE_ASSET_CACHE:
        ts1, m1, _closed_form, _components = _barrier_at_maturity_setup(ts, market)
        result = loss_odds(ts1, m1, drifts=(mu,), n_paths=100_000, seed=0)
        _ONE_ASSET_CACHE[mu] = (ts1, m1, result)
    return _ONE_ASSET_CACHE[mu]


def _closed_form_probs(ts1, m1, mu: float):
    """Closed-form (prob_loss, prob_loss_over_20, prob_loss_over_50, T, K).

    For this note total cash < N iff W_T < K, cash < 0.5 N iff W_T < 0.5,
    and cash < 0.8 N iff W_T < min(K, 0.8) = 0.7 here (see module task).
    With ln W_T ~ N((mu - q - sigma^2/2) T, sigma^2 T),
    P(W_T < x) = Phi((ln x - (mu - q - sigma^2/2) T) / (sigma sqrt T)).
    """
    sigma = float(m1.vols[0])
    q = float(m1.dividend_yields[0])
    K = float(ts1.downside.level.value)
    T = year_fraction(ts1.pricing_date.value, ts1.final_review_date.value)
    mean_log = (mu - q - 0.5 * sigma**2) * T
    sd_log = sigma * math.sqrt(T)

    def prob_below(x: float) -> float:
        return _phi((math.log(x) - mean_log) / sd_log)

    prob_loss = prob_below(K)
    prob_over_20 = prob_below(min(K, 0.8))
    prob_over_50 = prob_below(0.5)
    return prob_loss, prob_over_20, prob_over_50, T, K


# ---------------------------------------------------------------------------
# Closed-form checks for the one-asset barrier note
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mu", [0.0, 0.09])
def test_loss_odds_closed_form_probabilities(jpm_38582_termsheet, mu):
    ts = jpm_38582_termsheet
    market = _market(ts)
    ts1, m1, result = _one_asset_run(ts, market, mu)
    p_loss, p_over_20, p_over_50, _T, _K = _closed_form_probs(ts1, m1, mu)

    n = result.n_paths
    assert n == 100_000
    for empirical, closed in [
        (result.prob_loss, p_loss),
        (result.prob_loss_over_20, p_over_20),
        (result.prob_loss_over_50, p_over_50),
    ]:
        # Tolerance: 4 binomial standard errors, sqrt(p(1-p)/n), evaluated
        # at the closed-form p. A Monte Carlo frequency is binomial, so
        # 4 SE is the stated multiple of its exact sampling scale.
        tol = 4.0 * math.sqrt(closed * (1.0 - closed) / n)
        assert abs(empirical - closed) <= tol


@pytest.mark.parametrize("mu", [0.0, 0.09])
def test_loss_odds_closed_form_expected_cash(jpm_38582_termsheet, mu):
    ts = jpm_38582_termsheet
    market = _market(ts)
    ts1, m1, result = _one_asset_run(ts, market, mu)

    sigma = float(m1.vols[0])
    q = float(m1.dividend_yields[0])
    K = float(ts1.downside.level.value)
    notional = float(ts1.notional.value)
    T = year_fraction(ts1.pricing_date.value, ts1.final_review_date.value)

    p_below_k, _p20, _p50, _T2, _K2 = _closed_form_probs(ts1, m1, mu)

    # E[(1-W) 1{W<K}] = (1-K) P(W<K) + E[(K-W)+], with the lognormal put
    # expectation E[(K-W)+] = K Phi(-d2) - F Phi(-d1).
    F = math.exp((mu - q) * T)
    sqrt_T = math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * sigma**2 * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    put_expectation = K * _phi(-d2) - F * _phi(-d1)
    expected_loss_fraction = (1.0 - K) * p_below_k + put_expectation
    expected_cash_closed = notional * (1.0 - expected_loss_fraction)

    # Tolerance: 4 reported Monte Carlo standard errors of expected_cash.
    # The engine reports std(total cash, ddof=1)/sqrt(n), which is the
    # sampling scale of the mean, so 4x that SE is the stated tolerance.
    tol = 4.0 * result.std_errors["expected_cash"]
    assert abs(result.expected_cash - expected_cash_closed) <= tol


# ---------------------------------------------------------------------------
# Drift behaviour and labelling on the JPM note
# ---------------------------------------------------------------------------


def test_higher_drift_lowers_prob_loss_jpm(jpm_38582_termsheet):
    ts = jpm_38582_termsheet
    market = _market(ts)
    # Same seed for both runs (common random numbers): the comparison
    # reflects the drift change, not independent simulation noise, so a
    # strict inequality with no tolerance is appropriate.
    high = loss_odds(ts, market, drifts=(0.15, 0.15), n_paths=20_000, seed=0)
    low = loss_odds(ts, market, drifts=(-0.05, -0.05), n_paths=20_000, seed=0)
    assert high.prob_loss < low.prob_loss


def test_loss_odds_labelling_defaults_and_validation(jpm_38582_termsheet):
    ts = jpm_38582_termsheet
    market = _market(ts)

    result = loss_odds(ts, market, n_paths=5_000, seed=0)

    assert result.measure is Measure.REAL_WORLD
    assert result.excludes_issuer_default is True
    assert "real-world" in result.drift_note
    # Default drifts are (r + 0.05,) * N with r = 0.037. This is exact
    # arithmetic on the stated inputs, checked via real_world_drifts and
    # via the literal tuple; no Monte Carlo tolerance is involved.
    assert result.drifts == real_world_drifts(market)
    assert result.drifts == (market.risk_free_rate + 0.05,) * 2
    assert len(result.drifts) == 2

    with pytest.raises(ValueError):
        loss_odds(ts, market, drifts=(0.05,), n_paths=5_000, seed=0)
    with pytest.raises(ValueError):
        loss_odds(ts, market, drifts=(0.05, 0.05, 0.05), n_paths=5_000, seed=0)

    wrong_date_market = replace(
        market, as_of=ts.pricing_date.value + timedelta(days=1)
    )
    with pytest.raises(ValueError):
        loss_odds(ts, wrong_date_market, n_paths=5_000, seed=0)


def test_prob_called(jpm_38582_termsheet):
    ts = jpm_38582_termsheet
    market = _market(ts)

    # One-asset note has no autocall, so prob_called must be None: an
    # exact structural check, no tolerance.
    _ts1, _m1, one_asset_result = _one_asset_run(ts, market, 0.0)
    assert one_asset_result.prob_called is None

    result = loss_odds(ts, market, n_paths=20_000, seed=0)
    assert result.prob_called is not None
    # A probability lies in [0, 1] by definition: exact bounds.
    assert 0.0 <= result.prob_called <= 1.0
    # A called note returns principal plus premium, so it is never a loss;
    # the two events are disjoint, hence prob_loss + prob_called <= 1.
    # 1e-12 allows only floating-point addition round-off.
    assert result.prob_loss + result.prob_called <= 1.0 + 1e-12


# ---------------------------------------------------------------------------
# Stress heatmap
# ---------------------------------------------------------------------------


def test_stress_heatmap_jpm(jpm_38582_termsheet):
    ts = jpm_38582_termsheet
    market = _market(ts)

    grid = stress_heatmap(
        ts,
        market,
        drift_levels=(-0.1, 0.0, 0.1),
        vol_scales=(0.75, 1.25),
        n_paths=5_000,
        seed=0,
    )

    # Shape is structural (3 drift rows x 2 vol columns): exact check.
    assert len(grid.prob_loss) == 3
    assert all(len(row) == 2 for row in grid.prob_loss)
    assert len(grid.expected_cash) == 3
    assert all(len(row) == 2 for row in grid.expected_cash)

    for row in grid.prob_loss:
        for value in row:
            # Probabilities lie in [0, 1] by definition: exact bounds.
            assert 0.0 <= value <= 1.0

    # Each column is non-increasing in drift. The same seed is used for
    # every cell (common random numbers), so monotonicity in drift is
    # checked exactly, with no Monte Carlo tolerance.
    for col in range(2):
        assert grid.prob_loss[0][col] >= grid.prob_loss[1][col]
        assert grid.prob_loss[1][col] >= grid.prob_loss[2][col]

    assert grid.measure is Measure.REAL_WORLD
    assert grid.drift_levels == (-0.1, 0.0, 0.1)
    assert grid.vol_scales == (0.75, 1.25)

    encoded = json.dumps(grid.to_dict())
    assert isinstance(encoded, str)


# ---------------------------------------------------------------------------
# No mutation
# ---------------------------------------------------------------------------


def test_real_world_functions_do_not_mutate_inputs(jpm_38582_termsheet):
    ts = jpm_38582_termsheet
    market = _market(ts)
    ts_before = copy.deepcopy(ts)
    market_before = copy.deepcopy(market)

    _ = loss_odds(ts, market, n_paths=5_000, seed=0)
    _ = stress_heatmap(
        ts,
        market,
        drift_levels=(-0.1, 0.1),
        vol_scales=(0.75, 1.25),
        n_paths=5_000,
        seed=0,
    )

    # Dataclass equality against deepcopies taken before the calls: an
    # exact no-mutation check, so no numerical tolerance is involved.
    assert ts == ts_before
    assert market == market_before
