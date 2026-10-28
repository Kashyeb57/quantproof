"""Offline tests for quantproof/market/anchored.py (fake local data only).

Treasury months and FRED series are written into a tmp dir in the real
file formats; no network, no real cache, nothing committed.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quantproof.market import anchored
from quantproof.market.sources import FredClient

TREASURY = (
    "# Source: fake\n"
    'Date,"1 Yr","2 Yr","5 Yr","7 Yr"\n'
    "10/19/2026,4.00,4.20,4.60,4.80\n"
)

VIX = (
    "observation_date,VIXCLS\n"
    "2026-10-16,20.00\n"
    "2026-10-19,18.00\n"
)


def _data(tmp_path: Path) -> tuple[Path, FredClient]:
    (tmp_path / "treasury_daily_par_yield_curve_2026-10.csv").write_text(
        TREASURY, encoding="utf-8"
    )
    fred = tmp_path / "fred"
    fred.mkdir()
    (fred / "VIXCLS.csv").write_text(VIX, encoding="utf-8")
    return tmp_path, FredClient(cache_dir=fred)


def test_rate_vol_and_rho_anchored(tmp_path: Path):
    data_dir, client = _data(tmp_path)
    market, report = anchored.anchored_inputs(
        ["S&P 500 Index"],
        date(2026, 10, 19),
        date(2027, 10, 19),
        fred_client=client,
        treasury_dir=data_dir,
    )
    # 1y exactly on the 1 Yr tenor: 4.00%.
    assert market["rate"] == pytest.approx(0.04)
    assert report["rate_label"].startswith("U.S. Treasury par yield, 2026-10-19")
    # VIX 18.00 on the pricing date -> 0.18 centre, labelled implied.
    assert market["vols"] == (pytest.approx(0.18),)
    assert "stated simplification" in report["vol_labels"][0]
    assert "VIX 18.00 on 2026-10-19" in report["vol_labels"][0]
    assert market["rho"] == 0.0  # single underlying


def test_weekend_pricing_date_uses_last_trading_day(tmp_path: Path):
    data_dir, client = _data(tmp_path)
    # 2026-10-18 is a Sunday: Friday's VIX 20.00 is used, dated.
    market, report = anchored.anchored_inputs(
        ["S&P 500 Index"],
        date(2026, 10, 18),
        date(2027, 10, 18),
        fred_client=client,
        treasury_dir=data_dir,
    )
    assert market["vols"] == (pytest.approx(0.20),)
    assert "on 2026-10-16" in report["vol_labels"][0]


def test_unmapped_underlying_and_missing_rate_say_so(tmp_path: Path):
    data_dir, client = _data(tmp_path)
    market, report = anchored.anchored_inputs(
        ["The TJX Companies, Inc."],
        date(2026, 11, 2),  # no Treasury month file
        date(2029, 11, 2),
        fred_client=client,
        treasury_dir=data_dir,
    )
    assert market["rate"] == pytest.approx(0.037)
    assert "missing" in report["rate_label"]
    assert market["vols"] == (pytest.approx(0.25),)
    assert "not anchored" in report["vol_labels"][0]
    assert market["rho"] == 0.0


def test_mapping_fragments():
    assert anchored.underlying_mapping("Russell 2000® Index")[0] == "RVXCLS"
    assert anchored.underlying_mapping("Nasdaq-100 Index®")[0] == "VXNCLS"
    assert anchored.underlying_mapping("MSCI ACWI Index") is None


def test_inputs_summary_shows_missing_data():
    report = {
        "rate": 0.037,
        "rate_label": "rate missing (x): stated 0.037 used",
        "vols": [0.25],
        "rho": 0.0,
        "corr_label": "single underlying: rho 0.0",
    }
    assert "missing" in anchored.inputs_summary(report)
