"""Offline tests for quantproof/market/sources.py (local files only).

The client never touches the network: series files are written into a
tmp dir in FRED graph-CSV format, and the real ``.cache/fred/`` files
are never read or written here, so no raw data can reach the repo.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quantproof.market import sources

FAKE_CSV = (
    "observation_date,SP500\n"
    "2025-11-20,100.0\n"
    "2025-11-21,101.0\n"
    "2025-11-22,.\n"
    "2025-11-24,102.0\n"
    "2025-11-25,103.0\n"
)


def _write(path: Path, text: str = FAKE_CSV) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_series_ids_include_the_three_volatility_indices():
    assert sources.FRED_SERIES["VIX"][0] == "VIXCLS"
    assert sources.FRED_SERIES["VXN"][0] == "VXNCLS"
    assert sources.FRED_SERIES["RVX"][0] == "RVXCLS"
    # Volatility indices are not prices: closes resolve only SPX/NDX.
    assert set(sources.FRED_CLOSES) == {"SPX", "NDX"}


def test_missing_file_names_the_expected_path(tmp_path: Path):
    with pytest.raises(sources.SourceError, match="SP500.csv"):
        sources.FredClient(cache_dir=tmp_path).get_closes(
            "SP500", date(2025, 11, 20), date(2025, 11, 25)
        )


def test_get_closes_parses_and_skips_missing(tmp_path: Path):
    _write(tmp_path / "SP500.csv")
    client = sources.FredClient(cache_dir=tmp_path)
    assert client.get_closes("SP500", date(2025, 11, 20), date(2025, 11, 25)) == [
        (date(2025, 11, 20), 100.0),
        (date(2025, 11, 21), 101.0),
        (date(2025, 11, 24), 102.0),
        (date(2025, 11, 25), 103.0),
    ]


def test_missing_window_end_is_an_error_not_a_short_window(tmp_path: Path):
    _write(tmp_path / "SP500.csv")
    client = sources.FredClient(cache_dir=tmp_path)
    with pytest.raises(sources.SourceError, match="no close on the window end"):
        client.get_closes("SP500", date(2025, 11, 20), date(2025, 11, 26))


def test_value_on_or_before_handles_weekends(tmp_path: Path):
    _write(tmp_path / "VIXCLS.csv", FAKE_CSV.replace("SP500", "VIXCLS"))
    client = sources.FredClient(cache_dir=tmp_path)
    # 2025-11-23 is a Sunday: the Friday close is returned, dated.
    assert client.value_on_or_before("VIXCLS", date(2025, 11, 23)) == (
        101.0,
        date(2025, 11, 21),
    )
    with pytest.raises(sources.SourceError, match="no observation on or before"):
        client.value_on_or_before("VIXCLS", date(2025, 11, 19))


def test_unknown_ticker_names_user_entered_path(tmp_path: Path):
    with pytest.raises(sources.SourceError, match="user-entered"):
        sources.closes_for_ticker("RTY", date(2025, 11, 25))
    with pytest.raises(sources.SourceError, match="user-entered"):
        sources.closes_for_ticker("AAPL", date(2025, 11, 25))


def test_known_ticker_returns_attribution(tmp_path: Path):
    _write(tmp_path / "SP500.csv")
    client = sources.FredClient(cache_dir=tmp_path)
    rows, attribution = sources.closes_for_ticker(
        "spx", date(2025, 11, 25), window_days=5, client=client
    )
    assert rows[-1] == (date(2025, 11, 25), 103.0)
    assert "FRED" in attribution
    assert "not endorsed or certified" in sources.ATTRIBUTION_NOTICE
