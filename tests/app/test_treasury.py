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


def test_note_defaults_use_the_treasury_rate_for_38582(monkeypatch):
    # Pin the implied-vol layer to fail: the Treasury rate (committed
    # CSV) must still apply while vols fall back to stated, so this
    # passes with or without the local FRED cache.
    import quantproof.market.anchored as anchored_module
    from quantproof.market.sources import SourceError

    def _no_fred(names, pricing_date, maturity_date, **kwargs):
        raise SourceError("no FRED here")

    monkeypatch.setattr(anchored_module, "anchored_inputs", _no_fred)
    note = demo.load_note("jpm_424b2-38582")
    defaults = demo.note_defaults(note.stem, note.termsheet)
    assert defaults["rate"] == pytest.approx(0.035515, abs=1e-6)
    assert defaults["rate_label"].startswith("U.S. Treasury par yield, 2025-11-25")
    # The other inputs stay stated illustrative assumptions.
    assert defaults["vols"] == (0.24, 0.17)
    assert defaults["dividends"] == (0.012, 0.013)
    assert defaults["rho"] == 0.6


def test_month_routing_and_missing_month_says_so(tmp_path: Path):
    import shutil

    for month in ("2026-09", "2026-10"):
        shutil.copy(
            ROOT / "docs" / "data" / f"treasury_daily_par_yield_curve_{month}.csv",
            tmp_path / f"treasury_daily_par_yield_curve_{month}.csv",
        )
    rate, label = demo.treasury_par_rate_for(
        date(2026, 10, 19), date(2028, 10, 19), tmp_path
    )
    assert 0.02 < rate < 0.06
    assert label.startswith("U.S. Treasury par yield, 2026-10-19")
    with pytest.raises(ValueError, match="no Treasury curve file for 2026-11"):
        demo.treasury_par_rate_for(date(2026, 11, 2), date(2028, 11, 2), tmp_path)


def test_missing_curve_date_raises():
    with pytest.raises(ValueError, match="no Treasury curve row"):
        demo.treasury_par_rate(date(2025, 1, 1), date(2030, 1, 1))
