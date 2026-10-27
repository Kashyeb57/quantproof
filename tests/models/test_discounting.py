"""Tests for quantproof.models.discounting (offline, deterministic)."""

from datetime import date

import numpy as np
import pytest

from quantproof.models.discounting import discount_factor, year_fraction


def test_year_fraction_365_days_is_one() -> None:
    start = date(2024, 1, 1)
    end = date(2024, 12, 31)  # 2024 is a leap year, so this span is exactly 365 days
    assert (end - start).days == 365
    assert year_fraction(start, end) == 1.0


def test_year_fraction_end_before_start_raises() -> None:
    with pytest.raises(ValueError):
        year_fraction(date(2024, 5, 1), date(2024, 4, 30))


def test_discount_factor_scalar() -> None:
    # Exact identity D(1) = exp(-(0.05 + 0.01) * 1); approx because
    # 0.05 + 0.01 may differ from the 0.06 literal by one ulp.
    assert discount_factor(1.0, 0.05, 0.01) == pytest.approx(np.exp(-0.06))


def test_discount_factor_vector() -> None:
    t = np.array([0.0, 0.5, 1.0, 2.0])
    got = discount_factor(t, 0.05, 0.01)
    assert isinstance(got, np.ndarray)
    assert got == pytest.approx(np.exp(-0.06 * t))


def test_discount_factor_negative_time_raises() -> None:
    with pytest.raises(ValueError):
        discount_factor(-0.5, 0.05)
    with pytest.raises(ValueError):
        discount_factor(np.array([0.5, -0.1]), 0.05, 0.01)
