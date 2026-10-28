"""Treasury par-yield default rate: interpolation on the saved curve."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quantproof.app import demo

PRICING = date(2025, 11, 25)


def test_curve_row_for_the_pricing_date_is_the_treasury_row():
    rate, label = demo.treasury_par_rate(PRICING, date(2030, 11, 25))
    # Act/365: 1826 days (leap year 2028) is 5.0027y, just past 5 Yr.
    t = 1826 / 365.0
    assert rate == pytest.approx(0.0355 + (t - 5) / 2 * (0.0377 - 0.0355))
    assert label.startswith("U.S. Treasury par yield, 2025-11-25")


def test_interpolation_between_5y_and_7y():
    rate, label = demo.treasury_par_rate(PRICING, date(2030, 11, 29))
    # t = 1830/365 = 5.0137y; 3.55 + (0.0137/2) * (3.77 - 3.55) percent.
    assert rate == pytest.approx(0.035515, abs=1e-6)
    assert "interpolated between 5 Yr and 7 Yr" in label


def test_note_defaults_use_the_treasury_rate_for_38582():
    note = demo.load_note("jpm_424b2-38582")
    defaults = demo.note_defaults(note.stem, note.termsheet)
    assert defaults["rate"] == pytest.approx(0.035515, abs=1e-6)
    assert defaults["rate_label"].startswith("U.S. Treasury par yield, 2025-11-25")
    # The other inputs stay stated illustrative assumptions.
    assert defaults["vols"] == (0.24, 0.17)
    assert defaults["dividends"] == (0.012, 0.013)
    assert defaults["rho"] == 0.6


def test_missing_curve_date_raises():
    with pytest.raises(ValueError, match="no Treasury curve row"):
        demo.treasury_par_rate(date(2025, 1, 1), date(2030, 1, 1))
