"""Tests for models/bsm.py.

Independent checks rather than re-deriving the same formulas:
- textbook values published in Hull;
- put-call parity and no-arbitrage bounds (model-free relations);
- Greeks against finite differences of the price function;
- digitals against the limit of a call spread and the call decomposition;
- implied volatility round trips.
"""

import itertools

import numpy as np
import pytest

from quantproof.models import bsm

# Hull, Options, Futures, and Other Derivatives: the worked example for the Black-Scholes-Merton
# formulas (chapter 15 in recent editions): S=42, K=40, r=10%, sigma=20%, T=0.5.
HULL = dict(S=42.0, K=40.0, T=0.5, r=0.10, sigma=0.20)

# A grid of spots, strikes, expiries, rates, vols and dividend yields covering ITM/ATM/OTM.
GRID = list(itertools.product(
    [80.0, 100.0, 125.0],  # S
    [90.0, 100.0, 110.0],  # K
    [0.1, 1.0, 5.0],  # T
    [0.0, 0.05],  # r
    [0.1, 0.3, 0.8],  # sigma
    [0.0, 0.02],  # q
))


# --- Textbook values ----------------------------------------------------------------------------

def test_hull_d1_d2():
    d1, d2 = bsm.d1_d2(**HULL)
    # Hull reports d1 = 0.7693, d2 = 0.6278 (4 decimals), so compare to half a unit in the 4th.
    assert d1 == pytest.approx(0.7693, abs=5e-5)
    assert d2 == pytest.approx(0.6278, abs=5e-5)


def test_hull_call_and_put_prices():
    # Hull reports c = 4.76 and p = 0.81 (2 decimals): tolerance is half a cent.
    assert bsm.call_price(**HULL) == pytest.approx(4.76, abs=0.005)
    assert bsm.put_price(**HULL) == pytest.approx(0.81, abs=0.005)


# --- Model-free relations -----------------------------------------------------------------------

@pytest.mark.parametrize("S,K,T,r,sigma,q", GRID)
def test_put_call_parity(S, K, T, r, sigma, q):
    # C - P = S e^{-qT} - K e^{-rT} holds exactly; 1e-10 allows floating-point rounding only.
    lhs = bsm.call_price(S, K, T, r, sigma, q) - bsm.put_price(S, K, T, r, sigma, q)
    assert lhs == pytest.approx(S * np.exp(-q * T) - K * np.exp(-r * T), abs=1e-10)


@pytest.mark.parametrize("S,K,T,r,sigma,q", GRID)
def test_no_arbitrage_bounds(S, K, T, r, sigma, q):
    eps = 1e-12  # rounding only
    for kind in ("call", "put"):
        low, high = bsm.no_arbitrage_bounds(kind, S, K, T, r, q)
        p = bsm.price(kind, S, K, T, r, sigma, q)
        assert low - eps <= p <= high + eps


def test_price_increases_with_volatility():
    sigmas = np.linspace(0.05, 1.5, 30)
    for kind in ("call", "put"):
        prices = bsm.price(kind, 100.0, 100.0, 1.0, 0.03, sigmas, 0.01)
        assert np.all(np.diff(prices) > 0)


def test_zero_volatility_limit_is_discounted_forward_intrinsic():
    # As sigma -> 0 the stock grows deterministically at r - q, so the call is worth
    # max(S e^{-qT} - K e^{-rT}, 0). At sigma = 1e-8 the residual is far below 1e-8.
    for S, K in [(100.0, 90.0), (100.0, 110.0)]:
        expected = max(S * np.exp(-0.01) - K * np.exp(-0.05), 0.0)
        assert bsm.call_price(S, K, 1.0, 0.05, 1e-8, 0.01) == pytest.approx(expected, abs=1e-8)


# --- Greeks against finite differences ----------------------------------------------------------

def central_diff(f, x, h):
    return (f(x + h) - f(x - h)) / (2 * h)


@pytest.mark.parametrize("kind", ["call", "put"])
@pytest.mark.parametrize("S,K,T,r,sigma,q", [
    (100.0, 100.0, 1.0, 0.05, 0.2, 0.02),  # at the money
    (80.0, 100.0, 0.5, 0.03, 0.35, 0.0),  # out of the money call / in the money put
    (125.0, 100.0, 3.0, 0.01, 0.15, 0.03),  # long-dated, in the money call
])
def test_greeks_match_finite_differences(kind, S, K, T, r, sigma, q):
    g = bsm.greeks(kind, S, K, T, r, sigma, q)
    P = lambda **kw: bsm.price(kind, **{**dict(S=S, K=K, T=T, r=r, sigma=sigma, q=q), **kw})
    # Central differences have O(h^2) truncation error; with these step sizes the error is
    # around 1e-6 relative, so rel=1e-5 (abs=1e-6 for values near zero) is a safe tolerance.
    tol = dict(rel=1e-5, abs=1e-6)
    assert g["delta"] == pytest.approx(central_diff(lambda x: P(S=x), S, 1e-3), **tol)
    assert g["gamma"] == pytest.approx(
        central_diff(lambda x: bsm.delta(kind, x, K, T, r, sigma, q), S, 1e-3), **tol)
    assert g["vega"] == pytest.approx(central_diff(lambda x: P(sigma=x), sigma, 1e-5), **tol)
    assert g["rho"] == pytest.approx(central_diff(lambda x: P(r=x), r, 1e-5), **tol)
    # theta is dV/dt = -dV/dT
    assert g["theta"] == pytest.approx(-central_diff(lambda x: P(T=x), T, 1e-5), **tol)


def test_call_put_greek_relations():
    args = (100.0, 105.0, 2.0, 0.04, 0.25, 0.015)
    _, K, T, r, _, q = args
    # Differentiating parity C - P = S e^{-qT} - K e^{-rT}:
    # by S: delta_call - delta_put = e^{-qT};  by r: rho_call - rho_put = K T e^{-rT}.
    assert bsm.delta("call", *args) - bsm.delta("put", *args) == pytest.approx(np.exp(-q * T))
    assert bsm.rho("call", *args) - bsm.rho("put", *args) == pytest.approx(K * T * np.exp(-r * T))


# --- Digital (cash-or-nothing) options ----------------------------------------------------------

@pytest.mark.parametrize("S,K,T,r,sigma,q", GRID)
def test_digital_call_plus_put_is_a_zero_coupon_bond(S, K, T, r, sigma, q):
    total = bsm.digital_call_price(S, K, T, r, sigma, q) + bsm.digital_put_price(S, K, T, r, sigma, q)
    assert total == pytest.approx(np.exp(-r * T), abs=1e-12)


@pytest.mark.parametrize("S,K,T,r,sigma,q", [
    (100.0, 100.0, 1.0, 0.05, 0.2, 0.02),
    (90.0, 110.0, 2.0, 0.02, 0.4, 0.0),
    (130.0, 100.0, 0.25, 0.04, 0.15, 0.01),
])
def test_digital_is_the_limit_of_a_call_spread(S, K, T, r, sigma, q):
    # Long a call at K - h, short a call at K + h, scaled by 1/(2h), pays ~1 if S_T > K.
    # The spread equals -dC/dK; with h = 1e-3 the O(h^2) error is ~1e-7, so rel=1e-5.
    h = 1e-3
    spread = (bsm.call_price(S, K - h, T, r, sigma, q) - bsm.call_price(S, K + h, T, r, sigma, q)) / (2 * h)
    assert bsm.digital_call_price(S, K, T, r, sigma, q) == pytest.approx(spread, rel=1e-5)


def test_call_decomposes_into_asset_digital_minus_cash_digitals():
    # C = (asset-or-nothing call) - K x (cash-or-nothing call), with asset-or-nothing = S e^{-qT} N(d1).
    S, K, T, r, sigma, q = 100.0, 95.0, 1.5, 0.03, 0.3, 0.01
    d1, _ = bsm.d1_d2(S, K, T, r, sigma, q)
    from scipy.stats import norm
    asset_or_nothing = S * np.exp(-q * T) * norm.cdf(d1)
    expected = asset_or_nothing - K * bsm.digital_call_price(S, K, T, r, sigma, q)
    assert bsm.call_price(S, K, T, r, sigma, q) == pytest.approx(expected, abs=1e-12)


def test_digital_cash_scales_linearly():
    one = bsm.digital_put_price(100.0, 70.0, 5.0, 0.04, 0.25, 0.012)
    assert bsm.digital_put_price(100.0, 70.0, 5.0, 0.04, 0.25, 0.012, cash=1000.0) == pytest.approx(1000 * one)


# --- Implied volatility -------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["call", "put"])
@pytest.mark.parametrize("sigma", [0.05, 0.12, 0.25, 0.5, 1.0, 2.0])
@pytest.mark.parametrize("K", [70.0, 100.0, 140.0])
def test_implied_vol_round_trip(kind, sigma, K):
    S, T, r, q = 100.0, 1.0, 0.03, 0.01
    p = bsm.price(kind, S, K, T, r, sigma, q)
    vega = bsm.vega(S, K, T, r, sigma, q)
    iv = bsm.implied_vol(p, kind, S, K, T, r, q)
    if vega < 1e-6:
        # Deep in/out of the money at low vol the price is flat in sigma (vega ~ 1e-11), so many
        # sigmas give the same price and sigma itself is not identifiable. What must still hold
        # is that the solved sigma reproduces the price; 1e-10 is rounding-level.
        assert bsm.price(kind, S, K, T, r, iv, q) == pytest.approx(p, abs=1e-10)
    else:
        # Brent's xtol is 1e-12; 1e-8 leaves room for the price's own rounding error / vega.
        assert iv == pytest.approx(sigma, abs=1e-8)


def test_implied_vol_hull_example():
    p = bsm.call_price(**HULL)
    assert bsm.implied_vol(p, "call", S=42.0, K=40.0, T=0.5, r=0.10) == pytest.approx(0.20, abs=1e-10)


def test_implied_vol_rejects_arbitrage_prices():
    S, K, T, r = 100.0, 100.0, 1.0, 0.05
    with pytest.raises(ValueError, match="no-arbitrage"):
        bsm.implied_vol(S + 1.0, "call", S, K, T, r)  # more than the stock itself
    with pytest.raises(ValueError, match="no-arbitrage"):
        bsm.implied_vol(1.0, "call", S, K, T, r)  # below forward intrinsic S - K e^{-rT} ~ 4.88


# --- Input validation ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [dict(S=0.0), dict(K=-1.0), dict(T=0.0), dict(sigma=0.0)])
def test_rejects_invalid_inputs(bad):
    args = {**HULL, **bad}
    with pytest.raises(ValueError):
        bsm.call_price(**args)


def test_rejects_unknown_kind():
    with pytest.raises(ValueError, match="kind"):
        bsm.price("straddle", **HULL)


def test_vectorised_over_strikes():
    strikes = np.array([90.0, 100.0, 110.0])
    prices = bsm.call_price(100.0, strikes, 1.0, 0.05, 0.2)
    assert prices.shape == (3,)
    assert np.all(np.diff(prices) < 0)  # calls get cheaper as the strike rises
