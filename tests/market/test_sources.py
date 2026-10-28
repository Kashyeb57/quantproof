"""Offline tests for quantproof/market/sources.py (fake network only).

The urlopen callable is faked, so no bytes ever leave the machine; the
cache directory is a tmp dir, so no raw data can reach the repo.
"""

from __future__ import annotations

import io
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quantproof.market import sources

FAKE_CSV = (
    "DATE,SP500\n"
    "2025-11-20,100.0\n"
    "2025-11-21,101.0\n"
    "2025-11-22,.\n"
    "2025-11-24,102.0\n"
    "2025-11-25,103.0\n"
)


def _fake_urlopen(expected_url_part: str, seen: list):
    def fake(url, timeout=None):
        seen.append(url)
        assert expected_url_part in url
        assert "fredgraph.csv" in url

        class Reply:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return FAKE_CSV.encode("utf-8")

        return Reply()

    return fake


def test_fred_url_format_and_cache_path(tmp_path: Path):
    url = sources.fred_csv_url("SP500", date(2025, 11, 20), date(2025, 11, 25))
    assert url.startswith("https://fred.stlouisfed.org/graph/fredgraph.csv?")
    assert "id=SP500" in url
    path = sources.cache_path("SP500", date(2025, 11, 20), date(2025, 11, 25), tmp_path)
    assert path.parent == tmp_path
    assert path.suffix == ".csv"


def test_get_closes_parses_skips_missing_and_caches(tmp_path: Path):
    seen: list = []
    client = sources.FredClient(
        cache_dir=tmp_path, urlopen=_fake_urlopen("id=SP500", seen)
    )
    rows = client.get_closes("SP500", date(2025, 11, 20), date(2025, 11, 25))
    assert rows == [
        (date(2025, 11, 20), 100.0),
        (date(2025, 11, 21), 101.0),
        (date(2025, 11, 24), 102.0),
        (date(2025, 11, 25), 103.0),
    ]
    assert len(seen) == 1
    # Second call is served from the cache file: a failing urlopen is
    # never touched.
    def _boom(url, timeout=None):
        raise AssertionError("must not fetch twice")

    cached = sources.FredClient(cache_dir=tmp_path, urlopen=_boom).get_closes(
        "SP500", date(2025, 11, 20), date(2025, 11, 25)
    )
    assert cached == rows


def test_missing_window_end_is_an_error_not_a_short_window(tmp_path: Path):
    seen: list = []
    client = sources.FredClient(
        cache_dir=tmp_path, urlopen=_fake_urlopen("id=SP500", seen)
    )
    with pytest.raises(sources.SourceError, match="no close on the window end"):
        client.get_closes("SP500", date(2025, 11, 20), date(2025, 11, 26))


def test_download_failure_is_a_source_error(tmp_path: Path):
    import urllib.error

    def _fail(url, timeout=None):
        raise urllib.error.URLError("offline")

    with pytest.raises(sources.SourceError, match="download failed"):
        sources.FredClient(cache_dir=tmp_path, urlopen=_fail).get_closes(
            "SP500", date(2025, 11, 20), date(2025, 11, 25)
        )


def test_unknown_ticker_names_user_entered_path(tmp_path: Path):
    with pytest.raises(sources.SourceError, match="user-entered"):
        sources.closes_for_ticker("RTY", date(2025, 11, 25))
    with pytest.raises(sources.SourceError, match="user-entered"):
        sources.closes_for_ticker("AAPL", date(2025, 11, 25))


def test_known_ticker_returns_attribution(tmp_path: Path):
    seen: list = []
    client = sources.FredClient(
        cache_dir=tmp_path, urlopen=_fake_urlopen("id=SP500", seen)
    )
    rows, attribution = sources.closes_for_ticker(
        "spx", date(2025, 11, 25), window_days=5, client=client
    )
    assert rows[-1] == (date(2025, 11, 25), 103.0)
    assert "FRED" in attribution
    assert "not endorsed or certified" in sources.ATTRIBUTION_NOTICE
