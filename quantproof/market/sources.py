"""Licence-safe historical market data, read from local files only.

Only sources whose terms allow runtime use for a public app are
supported; see docs/VOL_SOURCES.md for the full findings. The one
implemented source is the Federal Reserve Bank of St. Louis FRED graph
CSV (no key, no account), manually downloaded into ``.cache/fred/``
(one file per series, standard ``observation_date,<ID>`` format)
because FRED is unreachable from some networks. **This module never
touches the network**: a missing file is a ``SourceError`` telling the
user which file to place, never a download.

Daily closes are fetched from the local files, cached nowhere else
(the files themselves are the cache, and ``.cache/`` is gitignored, so
no raw data is ever committed) — only derived volatility/correlation
numbers are ever shown, with attribution. Tickers without a confirmed
FRED series id (Russell 2000 closes, single stocks) raise a clear
error instead of guessing.

Standard library only. No AI calls.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

DEFAULT_CACHE = Path(".cache") / "fred"

# Series id -> attribution. Only ids confirmed against the FRED
# catalogue are listed; the three volatility ids were verified to
# exist with values on every needed pricing date. Anything else
# raises SourceError.
FRED_SERIES: dict[str, tuple[str, str]] = {
    "SPX": ("SP500", "S&P Dow Jones Indices LLC via FRED"),
    "NDX": ("NASDAQ100", "Nasdaq, Inc. via FRED"),
    "VIX": ("VIXCLS", "CBOE via FRED"),
    "VXN": ("VXNCLS", "Nasdaq, Inc. via FRED"),
    "RVX": ("RVXCLS", "CBOE via FRED"),
}

# Tickers whose *closes* have a confirmed FRED series (a subset of
# FRED_SERIES: the VIX/VXN/RVX rows are volatility indices, not
# prices, and must never feed a correlation of returns).
FRED_CLOSES: dict[str, tuple[str, str]] = {
    ticker: FRED_SERIES[ticker] for ticker in ("SPX", "NDX")
}


ATTRIBUTION_NOTICE = (
    "This product uses the FRED® API but is not endorsed or certified "
    "by the Federal Reserve Bank of St. Louis."
)


class SourceError(RuntimeError):
    """A market-data lookup that cannot be honoured."""


def series_path(series_id: str, cache_dir: Path = DEFAULT_CACHE) -> Path:
    """Local graph-CSV file for ``series_id`` (manually downloaded)."""
    safe = "".join(c if c.isalnum() else "_" for c in series_id)
    return Path(cache_dir) / f"{safe}.csv"


def read_series(series_id: str, cache_dir: Path = DEFAULT_CACHE) -> list[tuple[date, float]]:
    """All ``(date, value)`` pairs from a local FRED graph CSV.

    Skips the header, missing observations (``.``) and unparseable
    rows; returns pairs ascending by date. Raises ``SourceError``
    naming the expected file when it is absent.
    """
    path = series_path(series_id, cache_dir)
    if not path.is_file():
        raise SourceError(
            f"FRED series {series_id} is not cached: place the manually "
            f"downloaded FRED graph CSV at {path} (FRED is unreachable "
            "from some networks, so this module never downloads)."
        )
    rows: list[tuple[date, float]] = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for record in csv.DictReader(handle):
            raw_day = (record.get("observation_date") or record.get("DATE") or "").strip()
            try:
                day = date.fromisoformat(raw_day)
            except ValueError:
                continue
            value = (record.get(series_id) or record.get("VALUE") or "").strip()
            if not value or value == ".":
                continue
            try:
                rows.append((day, float(value)))
            except ValueError:
                continue
    rows.sort()
    return rows


def value_on_or_before(
    series_id: str, day: date, cache_dir: Path = DEFAULT_CACHE
) -> tuple[float, date]:
    """Latest known value on or before ``day`` (weekends/holidays handled).

    Formula: the last ``(date, value)`` pair with ``date <= day``.
    Raises ``SourceError`` when the file holds nothing on or before
    ``day`` (series starts later): no forward-filling, no guessing.
    """
    rows = read_series(series_id, cache_dir)
    known = [(d, v) for d, v in rows if d <= day]
    if not known:
        raise SourceError(
            f"FRED series {series_id} has no observation on or before {day}"
        )
    return known[-1][1], known[-1][0]


class FredClient:
    """Local-file FRED reader (no network, ever).

    ``env`` exists only for the same configuration shape as the other
    clients; nothing secret is needed (the graph CSV needs no key).
    """

    def __init__(self, cache_dir: Path = DEFAULT_CACHE, **kwargs) -> None:
        if kwargs:
            raise TypeError(f"unexpected arguments: {sorted(kwargs)}")
        self._cache_dir = Path(cache_dir)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(cache_dir={self._cache_dir!r})"

    def get_closes(self, series_id: str, start: date, end: date) -> list[tuple[date, float]]:
        """Daily ``(date, close)`` pairs in ``[start, end]``, ascending.

        Read from the local graph CSV only. Raises ``SourceError``
        when the file is absent, when the window holds no closes, or
        when the window end itself is missing (a volatility window
        must end on a known trading close — a missing end is an error,
        not a silent shorter window).
        """
        rows = [
            (d, v) for d, v in read_series(series_id, self._cache_dir)
            if start <= d <= end
        ]
        if not rows:
            raise SourceError(f"FRED series {series_id} has no closes in {start}..{end}")
        if rows[-1][0] < end:
            raise SourceError(
                f"FRED series {series_id} has no close on the window end {end}; "
                f"latest available is {rows[-1][0]}"
            )
        return rows

    def value_on_or_before(self, series_id: str, day: date) -> tuple[float, date]:
        """Latest known value on or before ``day``; see module function."""
        return value_on_or_before(series_id, day, self._cache_dir)


def closes_for_ticker(
    ticker: str,
    end: date,
    *,
    window_days: int = 365,
    client: FredClient | None = None,
) -> tuple[list[tuple[date, float]], str]:
    """``((date, close) list, attribution)`` for a ticker.

    Raises ``SourceError`` naming user-entered values as the path for
    tickers without a confirmed FRED series id (Russell 2000 closes,
    single stocks): no guessing, no unofficial endpoints.
    """
    resolved = FRED_CLOSES.get(ticker.strip().upper())
    if resolved is None:
        raise SourceError(
            f"no licence-safe FRED closes series confirmed for ticker {ticker!r}; "
            "use a user-entered volatility instead "
            "(see docs/VOL_SOURCES.md)"
        )
    series_id, attribution = resolved
    start = end - _days(window_days + 10)  # slack for weekends/holidays
    rows = (client or FredClient()).get_closes(series_id, start, end)
    return rows, attribution


def _days(n: int):
    import datetime

    return datetime.timedelta(days=n)
