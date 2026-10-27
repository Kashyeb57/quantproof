"""Discounting for structured-note valuation: year fractions and discount factors.

All functions are pure Python + NumPy. No AI and no network calls in this module.

Reference: J. C. Hull, *Options, Futures, and Other Derivatives*, chapter 4
(interest rates, day counts and discounting).
"""

from __future__ import annotations

from datetime import date

import numpy as np


def year_fraction(start: date, end: date) -> float:
    """Year fraction between two dates on an Act/365 Fixed basis.

    Formula: y = (end - start).days / 365.

    Intuition: accruals and continuous discounting need time in years; Act/365 Fixed
    simply counts calendar days and divides by a fixed 365-day year.

    Raises ValueError if end < start.
    """
    if end < start:
        raise ValueError("end date must not be before start date")
    return (end - start).days / 365.0


def discount_factor(
    t: float | np.ndarray, r: float, credit_spread: float = 0.0
) -> float | np.ndarray:
    """Discount factor for time t: D(t) = exp(-(r + s) t).

    Intuition: the note is the issuer's unsecured debt, so its cash flows are
    discounted at the risk-free rate r plus the issuer's credit spread s. A larger
    spread means a larger discount rate and a lower present value.

    Raises ValueError if any t < 0.
    """
    t_arr = np.asarray(t, dtype=float)
    if np.any(t_arr < 0):
        raise ValueError("times must be non-negative")
    result = np.exp(-(r + credit_spread) * t_arr)
    if result.ndim == 0:
        return float(result)
    return result
