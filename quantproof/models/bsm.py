"""Black-Scholes-Merton building blocks: vanilla and digital options, Greeks, implied volatility.

Model: under the risk-neutral measure the underlying follows geometric Brownian motion
    dS = (r - q) S dt + sigma S dW,
with constant risk-free rate r, dividend yield q and volatility sigma (all continuously compounded,
per year). Then ln S_T is normal and options have closed-form prices.

Notation used throughout
    S      spot price today            K      strike
    T      time to expiry in years     r      risk-free rate
    sigma  volatility                  q      dividend yield
    N(x)   standard normal CDF         n(x)   standard normal density

    d1 = [ln(S/K) + (r - q + sigma^2/2) T] / (sigma sqrt(T))
    d2 = d1 - sigma sqrt(T)

Units: vega and rho are per 1.00 change (0.01 = one vol point / 1% rate); theta is per year of
calendar time (divide by 365 for "per day").

Functions accept floats or NumPy arrays (elementwise). No AI and no network calls in this module.

Reference: J. C. Hull, *Options, Futures, and Other Derivatives*, chapters 15 and 19 (Black-Scholes-
Merton model, Greeks) and 26 (binary options).
"""

from __future__ import annotations

from typing import Literal

import numpy as np
from scipy.optimize import brentq
from scipy.special import ndtr  # standard normal CDF, N(x)

Kind = Literal["call", "put"]

_SQRT_2PI = np.sqrt(2.0 * np.pi)


def _pdf(x):
    """Standard normal density n(x) = exp(-x^2/2) / sqrt(2 pi)."""
    return np.exp(-0.5 * np.square(x)) / _SQRT_2PI


def _check(S, K, T, sigma) -> None:
    if np.any(np.asarray(S) <= 0) or np.any(np.asarray(K) <= 0):
        raise ValueError("spot and strike must be positive")
    if np.any(np.asarray(T) <= 0):
        raise ValueError("time to expiry must be positive")
    if np.any(np.asarray(sigma) <= 0):
        raise ValueError("volatility must be positive")


def _check_kind(kind: str) -> None:
    if kind not in ("call", "put"):
        raise ValueError("kind must be 'call' or 'put'")


def d1_d2(S, K, T, r, sigma, q=0.0):
    """Return (d1, d2).

    d1 = [ln(S/K) + (r - q + sigma^2/2) T] / (sigma sqrt(T)),   d2 = d1 - sigma sqrt(T).

    Intuition: N(d2) is the risk-neutral probability that the option ends in the money (S_T > K);
    N(d1) is the same probability measured with the stock as numeraire, which is why it is also
    the (dividend-adjusted) hedge ratio.
    """
    _check(S, K, T, sigma)
    vol_sqrt_t = sigma * np.sqrt(T)
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma**2) * T) / vol_sqrt_t
    return d1, d1 - vol_sqrt_t


# --- Prices -------------------------------------------------------------------------------------

def call_price(S, K, T, r, sigma, q=0.0):
    """European call: C = S e^{-qT} N(d1) - K e^{-rT} N(d2).

    Intuition: the call is "receive the stock if S_T > K" (first term, the present value of the
    stock in the states where you exercise) minus "pay K if S_T > K" (second term, K discounted
    times the risk-neutral exercise probability).
    """
    d1, d2 = d1_d2(S, K, T, r, sigma, q)
    return S * np.exp(-q * T) * ndtr(d1) - K * np.exp(-r * T) * ndtr(d2)


def put_price(S, K, T, r, sigma, q=0.0):
    """European put: P = K e^{-rT} N(-d2) - S e^{-qT} N(-d1).

    Intuition: the mirror image of the call: receive K and give up the stock in the states where
    S_T < K. Consistent with put-call parity C - P = S e^{-qT} - K e^{-rT}.
    """
    d1, d2 = d1_d2(S, K, T, r, sigma, q)
    return K * np.exp(-r * T) * ndtr(-d2) - S * np.exp(-q * T) * ndtr(-d1)


def price(kind: Kind, S, K, T, r, sigma, q=0.0):
    """European call or put price (see call_price / put_price)."""
    _check_kind(kind)
    return call_price(S, K, T, r, sigma, q) if kind == "call" else put_price(S, K, T, r, sigma, q)


def digital_call_price(S, K, T, r, sigma, q=0.0, cash=1.0):
    """Cash-or-nothing call: pays `cash` at T if S_T > K. Value = cash e^{-rT} N(d2).

    Intuition: a bet on finishing above K. Its value is the discounted risk-neutral probability of
    that event. Structured notes are built from these: "repay principal only if the index ends
    above the barrier" is a digital.
    """
    _, d2 = d1_d2(S, K, T, r, sigma, q)
    return cash * np.exp(-r * T) * ndtr(d2)


def digital_put_price(S, K, T, r, sigma, q=0.0, cash=1.0):
    """Cash-or-nothing put: pays `cash` at T if S_T < K. Value = cash e^{-rT} N(-d2).

    A digital call plus a digital put with the same strike pay `cash` for sure, so together they
    are worth cash e^{-rT}.
    """
    _, d2 = d1_d2(S, K, T, r, sigma, q)
    return cash * np.exp(-r * T) * ndtr(-d2)


# --- Greeks (vanilla options) -------------------------------------------------------------------

def delta(kind: Kind, S, K, T, r, sigma, q=0.0):
    """dV/dS. Call: e^{-qT} N(d1). Put: e^{-qT} (N(d1) - 1).

    Intuition: shares of stock to hold to hedge one option against small moves in S.
    """
    _check_kind(kind)
    d1, _ = d1_d2(S, K, T, r, sigma, q)
    disc_q = np.exp(-q * T)
    return disc_q * ndtr(d1) if kind == "call" else disc_q * (ndtr(d1) - 1.0)


def gamma(S, K, T, r, sigma, q=0.0):
    """d^2V/dS^2, same for calls and puts: e^{-qT} n(d1) / (S sigma sqrt(T)).

    Intuition: how fast delta changes; largest at the money near expiry, where the hedge must be
    adjusted most often.
    """
    d1, _ = d1_d2(S, K, T, r, sigma, q)
    return np.exp(-q * T) * _pdf(d1) / (S * sigma * np.sqrt(T))


def vega(S, K, T, r, sigma, q=0.0):
    """dV/dsigma, same for calls and puts: S e^{-qT} n(d1) sqrt(T)  (per 1.00 of volatility).

    Intuition: options are bets on movement, so more volatility makes both calls and puts worth
    more. (A structured note that is short a put therefore loses value when volatility rises.)
    """
    d1, _ = d1_d2(S, K, T, r, sigma, q)
    return S * np.exp(-q * T) * _pdf(d1) * np.sqrt(T)


def theta(kind: Kind, S, K, T, r, sigma, q=0.0):
    """dV/dt (calendar time; equals -dV/dT), per year.

    Call: -S e^{-qT} n(d1) sigma / (2 sqrt T) - r K e^{-rT} N(d2)  + q S e^{-qT} N(d1)
    Put:  -S e^{-qT} n(d1) sigma / (2 sqrt T) + r K e^{-rT} N(-d2) - q S e^{-qT} N(-d1)

    Intuition: the value an option loses as time passes with nothing else changing (time decay).
    The first term, common to both, is the loss of optionality.
    """
    _check_kind(kind)
    d1, d2 = d1_d2(S, K, T, r, sigma, q)
    decay = -S * np.exp(-q * T) * _pdf(d1) * sigma / (2.0 * np.sqrt(T))
    if kind == "call":
        return decay - r * K * np.exp(-r * T) * ndtr(d2) + q * S * np.exp(-q * T) * ndtr(d1)
    return decay + r * K * np.exp(-r * T) * ndtr(-d2) - q * S * np.exp(-q * T) * ndtr(-d1)


def rho(kind: Kind, S, K, T, r, sigma, q=0.0):
    """dV/dr, per 1.00 of rate. Call: K T e^{-rT} N(d2). Put: -K T e^{-rT} N(-d2).

    Intuition: a higher rate lowers the present value of the strike, which helps the call holder
    (who pays K) and hurts the put holder (who receives K).
    """
    _check_kind(kind)
    _, d2 = d1_d2(S, K, T, r, sigma, q)
    if kind == "call":
        return K * T * np.exp(-r * T) * ndtr(d2)
    return -K * T * np.exp(-r * T) * ndtr(-d2)


def greeks(kind: Kind, S, K, T, r, sigma, q=0.0) -> dict[str, float]:
    """All five Greeks for one vanilla option, as a dict (units as in the module docstring)."""
    return {
        "delta": delta(kind, S, K, T, r, sigma, q),
        "gamma": gamma(S, K, T, r, sigma, q),
        "vega": vega(S, K, T, r, sigma, q),
        "theta": theta(kind, S, K, T, r, sigma, q),
        "rho": rho(kind, S, K, T, r, sigma, q),
    }


# --- Implied volatility -------------------------------------------------------------------------

def no_arbitrage_bounds(kind: Kind, S, K, T, r, q=0.0) -> tuple[float, float]:
    """Model-free price bounds for a European option.

    Call: max(S e^{-qT} - K e^{-rT}, 0) <= C <= S e^{-qT}
    Put:  max(K e^{-rT} - S e^{-qT}, 0) <= P <= K e^{-rT}

    Intuition: a call is worth at least its forward intrinsic value and never more than the stock;
    a put never more than the discounted strike. Any price outside breaks arbitrage-free pricing.
    """
    _check_kind(kind)
    fwd_s, fwd_k = S * np.exp(-q * T), K * np.exp(-r * T)
    if kind == "call":
        return max(fwd_s - fwd_k, 0.0), fwd_s
    return max(fwd_k - fwd_s, 0.0), fwd_k


def implied_vol(option_price: float, kind: Kind, S: float, K: float, T: float, r: float,
                q: float = 0.0, lo: float = 1e-6, hi: float = 5.0, xtol: float = 1e-12) -> float:
    """The volatility sigma that makes the Black-Scholes price equal `option_price`.

    Solved with Brent's method on f(sigma) = BS(sigma) - price over [lo, hi]. A root exists and is
    unique because the price rises strictly with sigma (vega > 0) from the lower no-arbitrage bound
    (sigma -> 0) to the upper bound (sigma -> infinity). Brent needs no derivative, so it stays
    reliable deep out of the money where vega is almost zero and Newton's method can diverge.

    Raises ValueError if the price is outside the no-arbitrage bounds or the bracket.
    Reference: R. P. Brent, *Algorithms for Minimization without Derivatives* (1973), ch. 4.
    """
    _check(S, K, T, 1.0)
    low, high = no_arbitrage_bounds(kind, S, K, T, r, q)
    if not low < option_price < high:
        raise ValueError(f"price {option_price} outside no-arbitrage bounds ({low:.6g}, {high:.6g})")

    def f(sigma: float) -> float:
        return float(price(kind, S, K, T, r, sigma, q)) - option_price

    if f(lo) > 0 or f(hi) < 0:
        raise ValueError(f"implied volatility outside the search bracket [{lo}, {hi}]")
    return brentq(f, lo, hi, xtol=xtol)
