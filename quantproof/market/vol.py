"""Realised volatility and correlation from daily closes.

Method, stated once here and used everywhere: with daily closes
``P_0, ..., P_n``, log returns are ``r_t = ln(P_t / P_{t-1})``;
realised volatility is the sample standard deviation of the returns
(``ddof=1``) annualised by ``sqrt(252)``; correlation is the Pearson
correlation of two assets' log-return series. The window is the
trailing 1 year of closes ending on the pricing date by default. Every
value this module produces is labelled with this method by its callers.

Pure maths: standard library + NumPy. No network and no AI calls.

Reference: J. C. Hull, *Options, Futures, and Other Derivatives*,
chapter 20 (historical volatility as the annualised standard deviation
of log returns).
"""

from __future__ import annotations

import datetime
from datetime import date

import numpy as np

TRADING_DAYS_PER_YEAR = 252
DEFAULT_WINDOW_DAYS = 365


def log_returns(closes: list[float] | np.ndarray) -> np.ndarray:
    """Log returns ``ln(P_t / P_{t-1})`` of a close series.

    Formula: ``r_t = ln(P_t / P_{t-1})`` for ``t = 1..n``.

    Intuition: log returns add over time, so their spread measures how
    much the price actually moved — the raw material of volatility.

    Reference: Hull chapter 20.
    """
    prices = np.asarray(closes, dtype=float)
    if prices.size < 2:
        raise ValueError("need at least 2 closes for one return")
    if np.any(~np.isfinite(prices)) or np.any(prices <= 0):
        raise ValueError("closes must be finite and positive")
    return np.log(prices[1:] / prices[:-1])


def realised_vol(
    closes: list[float] | np.ndarray,
    *,
    trading_days: int = TRADING_DAYS_PER_YEAR,
) -> float:
    """Annualised realised volatility of a close series.

    Formula: ``std(r, ddof=1) * sqrt(trading_days)`` with ``r`` the log
    returns. At least two returns (three closes) are needed: one return
    has no measurable spread.

    Intuition: the sample standard deviation of daily moves, scaled up
    to a year on the assumption that variance grows with time.

    Reference: Hull chapter 20 (historical volatility).
    """
    returns = log_returns(closes)
    if returns.size < 2:
        raise ValueError("need at least 3 closes for realised volatility")
    return float(np.std(returns, ddof=1) * np.sqrt(float(trading_days)))


def pairwise_correlation(
    closes_a: list[float] | np.ndarray,
    closes_b: list[float] | np.ndarray,
) -> float:
    """Pearson correlation of two assets' log-return series.

    Formula: the Pearson correlation of the two log-return vectors,
    which must have equal length. Raises if either return series has
    zero variance (a correlation of a constant is undefined) or there
    are fewer than two returns.
    """
    ra = log_returns(closes_a)
    rb = log_returns(closes_b)
    if ra.shape != rb.shape:
        raise ValueError("return series must have equal length")
    if ra.size < 2:
        raise ValueError("need at least 3 closes per asset for correlation")
    if float(np.std(ra)) == 0.0 or float(np.std(rb)) == 0.0:
        raise ValueError("correlation is undefined for a constant series")
    return float(np.corrcoef(ra, rb)[0, 1])


def correlation_matrix(series: list[list[float] | np.ndarray]) -> np.ndarray:
    """Correlation matrix of several close series (symmetric, unit diagonal).

    Formula: entry ``[i, j]`` is ``pairwise_correlation(series[i],
    series[j])``; the diagonal is exactly 1.0.
    """
    n = len(series)
    if n < 1:
        raise ValueError("need at least one series")
    out = np.eye(n)
    for i in range(n):
        for j in range(i + 1, n):
            rho = pairwise_correlation(series[i], series[j])
            out[i, j] = out[j, i] = rho
    return out


def trailing_window(
    dated_closes: list[tuple[date, float]],
    end: date,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> list[float]:
    """Closes in the trailing window ending on ``end`` (inclusive).

    Formula: keep ``(d, p)`` with ``end - window_days <= d <= end``,
    sorted ascending by date; returns the prices only. Raises if fewer
    than 3 closes land in the window (the minimum ``realised_vol``
    needs).
    """
    kept = sorted(
        (d, p)
        for d, p in dated_closes
        if datetime.timedelta(days=0) <= end - d <= datetime.timedelta(days=window_days)
    )
    if len(kept) < 3:
        raise ValueError(
            f"only {len(kept)} closes in the trailing {window_days}-day window "
            f"ending {end.isoformat()}; need at least 3"
        )
    return [float(p) for _d, p in kept]


def vol_grid(
    centre: float,
    *,
    half_range: float = 0.05,
    floor: float = 0.01,
) -> tuple[float, float, float]:
    """``(low, centre, high)`` volatility grid around a realised value.

    Formula: ``(max(centre - half_range, floor), centre, centre +
    half_range)``. A range, not a point: the realised value anchors the
    centre and the assumption grid varies around it.
    """
    return (max(float(centre) - float(half_range), float(floor)), float(centre), float(centre) + float(half_range))


def corr_grid(
    centre: float,
    *,
    half_range: float = 0.25,
) -> tuple[float, float, float]:
    """``(low, centre, high)`` correlation grid, clipped to (-1, 1).

    Formula: ``centre +/- half_range``, each clipped to ``[-0.99,
    0.99]`` so the blended matrix stays a valid correlation matrix.
    """
    lo = min(max(float(centre) - float(half_range), -0.99), 0.99)
    hi = min(max(float(centre) + float(half_range), -0.99), 0.99)
    return (lo, float(centre), hi)
