"""Licence-safe historical close fetcher (FRED keyless graph CSV).

Only sources whose terms allow runtime fetching for a public app are
implemented; see docs/VOL_SOURCES.md for the full findings. The one
implemented source is the Federal Reserve Bank of St. Louis FRED graph
CSV (no key, no account): daily closes are fetched at runtime, cached
under gitignored ``.cache/fred/``, and never committed — only derived
volatility/correlation numbers are ever shown, with attribution.
Tickers without a confirmed FRED series id (Russell 2000, single
stocks) raise a clear error instead of guessing.

Standard library only. No AI calls. Network happens only through the
injected ``urlopen``, so tests run offline with fakes.
"""

from __future__ import annotations

import csv
import hashlib
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

DEFAULT_CACHE = Path(".cache") / "fred"

# Ticker -> (FRED series id, attribution). Only ids confirmed against
# the FRED catalogue are listed; anything else raises SourceError.
FRED_SERIES: dict[str, tuple[str, str]] = {
    "SPX": ("SP500", "S&P Dow Jones Indices LLC via FRED"),
    "NDX": ("NASDAQ100", "Nasdaq, Inc. via FRED"),
}

ATTRIBUTION_NOTICE = (
    "This product uses the FRED® API but is not endorsed or certified "
    "by the Federal Reserve Bank of St. Louis."
)


class SourceError(RuntimeError):
    """A market-data fetch or lookup that cannot be honoured."""


def fred_csv_url(series_id: str, start: date, end: date) -> str:
    """Keyless FRED graph-CSV URL for ``series_id`` over ``[start, end]``."""
    query = urllib.parse.urlencode(
        {
            "id": series_id,
            "cosd": start.isoformat(),
            "coed": end.isoformat(),
        }
    )
    return f"https://fred.stlouisfed.org/graph/fredgraph.csv?{query}"


def cache_path(
    series_id: str, start: date, end: date, cache_dir: Path = DEFAULT_CACHE
) -> Path:
    """Cache file for one series window (safe filename, no raw data in git)."""
    digest = hashlib.sha256(
        f"{series_id}|{start.isoformat()}|{end.isoformat()}".encode("utf-8")
    ).hexdigest()[:16]
    safe = "".join(c if c.isalnum() else "_" for c in series_id)
    return Path(cache_dir) / f"{safe}_{digest}.csv"


class FredClient:
    """FRED graph-CSV client with a disk cache.

    ``env`` supplies nothing secret (the graph CSV needs no key); it is
    accepted for the same configuration shape as the other clients.
    ``urlopen`` is injected so tests run offline.
    """

    def __init__(
        self,
        cache_dir: Path = DEFAULT_CACHE,
        *,
        urlopen=urllib.request.urlopen,
        timeout_s: float = 30.0,
    ) -> None:
        self._cache_dir = Path(cache_dir)
        self._urlopen = urlopen
        self._timeout_s = float(timeout_s)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(cache_dir={self._cache_dir!r})"

    def get_closes(self, series_id: str, start: date, end: date) -> list[tuple[date, float]]:
        """Daily ``(date, close)`` pairs, ascending, from cache or FRED.

        Missing observations (``.`` in the CSV) are skipped. Raises
        ``SourceError`` when the download fails, the CSV has no usable
        rows in the window, or the requested end date itself is missing
        (a volatility window must end on a known trading close).
        """
        path = cache_path(series_id, start, end, self._cache_dir)
        if path.exists():
            text = path.read_text(encoding="utf-8")
        else:
            url = fred_csv_url(series_id, start, end)
            try:
                with self._urlopen(url, timeout=self._timeout_s) as response:
                    raw = response.read()
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                raise SourceError(f"FRED download failed for {series_id}: {exc}") from exc
            text = raw.decode("utf-8", errors="replace")
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.parent / f"{path.name}.{os.getpid()}.tmp"
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
        rows: list[tuple[date, float]] = []
        for record in csv.DictReader(text.splitlines()):
            try:
                day = date.fromisoformat((record.get("DATE") or "").strip())
            except ValueError:
                continue
            value = (record.get(series_id) or record.get("VALUE") or "").strip()
            if not value or value == ".":
                continue
            try:
                rows.append((day, float(value)))
            except ValueError:
                continue
        rows = [(d, v) for d, v in rows if start <= d <= end]
        rows.sort()
        if not rows:
            raise SourceError(f"FRED series {series_id} has no closes in {start}..{end}")
        if rows[-1][0] != end and rows[-1][0] < end:
            # The window must end on a known close; a missing end date
            # (weekend, holiday, or not yet published) is an error, not
            # a silent shorter window.
            raise SourceError(
                f"FRED series {series_id} has no close on the window end {end}; "
                f"latest available is {rows[-1][0]}"
            )
        return rows


def closes_for_ticker(
    ticker: str,
    end: date,
    *,
    window_days: int = 365,
    client: FredClient | None = None,
) -> tuple[list[tuple[date, float]], str]:
    """``((date, close) list, attribution)`` for a ticker.

    Raises ``SourceError`` naming user-entered values as the path for
    tickers without a confirmed FRED series id (Russell 2000, single
    stocks): no guessing, no unofficial endpoints.
    """
    resolved = FRED_SERIES.get(ticker.strip().upper())
    if resolved is None:
        raise SourceError(
            f"no licence-safe FRED series confirmed for ticker {ticker!r}; "
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



