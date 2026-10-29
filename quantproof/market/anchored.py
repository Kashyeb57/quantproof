"""Real market inputs for a note, anchored where a licence allows.

For each input the rule is: a dated, sourced market value when one
exists under the terms in docs/VOL_SOURCES.md, otherwise the stated
assumption with a label saying so. Nothing is ever invented:

- risk-free rate: U.S. Treasury par yield on the pricing date,
  interpolated to the maturity (Act/365); a missing month file or
  curve row falls back to the stated rate and says so in the label.
- volatility per underlying: the 30-day implied-vol index close
  (VIX/VXN/RVX) on the pricing date, or the last trading day before,
  as the grid centre for index underlyings, labelled "30-day implied
  vol applied to the note's life: a stated simplification". Single
  stocks and anything else keep the stated assumption, labelled
  "not anchored".
- correlation: the average realised pairwise correlation from cached
  FRED closes over the trailing year, wherever both legs of a pair
  have a confirmed closes series; otherwise the stated default.
- dividends and credit spread: stated assumptions (no licence-safe
  source wired).

Pure Python + NumPy; the only I/O is reading local data files.
No network, no AI calls.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from quantproof.app.demo import treasury_par_rate_for
from quantproof.market import sources
from quantproof.market.vol import pairwise_correlation, trailing_window

STATED_RATE = 0.037
STATED_SPREAD = 0.006
STATED_VOL = 0.25
STATED_DIVIDEND = 0.01
STATED_CORRELATION = 0.6
STATED_ERP = 0.05

IMPLIED_VOL_LABEL = (
    "30-day implied vol applied to the note's life: a stated simplification"
)
NOT_ANCHORED = "not anchored"

# Underlying-name fragment (upper case) -> (implied-vol series id,
# display name, closes ticker or None). Closes are only listed where a
# confirmed FRED closes series exists; volatility indices are never
# prices, so RVX has no closes leg.
UNDERLYINGS: tuple[tuple[str, str, str, str | None], ...] = (
    ("S&P 500", "VIXCLS", "VIX", "SPX"),
    ("NASDAQ-100", "VXNCLS", "VXN", "NDX"),
    ("NASDAQ 100", "VXNCLS", "VXN", "NDX"),
    ("RUSSELL 2000", "RVXCLS", "RVX", None),
)


def underlying_mapping(name: str) -> tuple[str, str, str | None] | None:
    """``(vol series id, display name, closes ticker|None)`` for an underlying.

    Matches on upper-cased name fragments (filing names carry
    registered-trade-mark suffixes). Returns None when nothing matches:
    single stocks, ETFs and exotic indices stay on stated assumptions.
    """
    upper = str(name).upper()
    for fragment, series_id, display, closes in UNDERLYINGS:
        if fragment in upper:
            return series_id, display, closes
    return None


def anchored_inputs(
    underlying_names: list[str],
    pricing_date: date,
    maturity_date: date,
    *,
    fred_client: sources.FredClient | None = None,
    treasury_dir: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(market kwargs, report)`` with real inputs where anchored.

    ``market kwargs`` holds ``rate``, ``spread``, ``vols``,
    ``dividends`` and ``rho`` ready for the market-inputs constructor.
    ``report`` holds the same numbers plus one human label per input
    (``rate_label``, ``vol_labels``, ``corr_label``) stating the
    source and date, or saying what is missing / not anchored, plus a
    ``vol_anchored`` boolean mask (True where the vol is a FRED implied-vol
    centre, for the relative grid band).
    Deterministic and offline: Treasury months and FRED files are
    local; nothing is downloaded.
    """
    client = fred_client or sources.FredClient()

    try:
        rate, rate_label = treasury_par_rate_for(
            pricing_date, maturity_date, treasury_dir
        )
    except ValueError as exc:
        rate, rate_label = STATED_RATE, f"rate missing ({exc}): stated {STATED_RATE} used"

    vols: list[float] = []
    vol_labels: list[str] = []
    closes_tickers: list[str | None] = []
    for name in underlying_names:
        mapped = underlying_mapping(name)
        if mapped is None:
            vols.append(STATED_VOL)
            vol_labels.append(f"{name}: stated {STATED_VOL} ({NOT_ANCHORED})")
            closes_tickers.append(None)
            continue
        series_id, display, closes = mapped
        try:
            value, actual = client.value_on_or_before(series_id, pricing_date)
        except sources.SourceError as exc:
            vols.append(STATED_VOL)
            vol_labels.append(f"{name}: stated {STATED_VOL} ({exc})")
            closes_tickers.append(None)
            continue
        vols.append(float(value) / 100.0)
        vol_labels.append(
            f"{name}: {IMPLIED_VOL_LABEL} ({display} {value:.2f} on "
            f"{actual.isoformat()} via FRED)"
        )
        closes_tickers.append(closes)

    n = len(underlying_names)
    if n < 2:
        rho, corr_label = 0.0, "single underlying: rho 0.0"
    else:
        pairs: list[float] = []
        for i in range(n):
            for j in range(i + 1, n):
                if closes_tickers[i] is None or closes_tickers[j] is None:
                    continue
                try:
                    first = client.get_closes(
                        sources.FRED_CLOSES[closes_tickers[i]][0],
                        pricing_date - _days(375),
                        pricing_date,
                    )
                    second = client.get_closes(
                        sources.FRED_CLOSES[closes_tickers[j]][0],
                        pricing_date - _days(375),
                        pricing_date,
                    )
                    # Returns must pair the same days: FRED drops missing
                    # values ("."), so keep only dates both series have.
                    common = {d for d, _ in first} & {d for d, _ in second}
                    pairs.append(
                        pairwise_correlation(
                            trailing_window([c for c in first if c[0] in common], pricing_date),
                            trailing_window([c for c in second if c[0] in common], pricing_date),
                        )
                    )
                except (sources.SourceError, ValueError):
                    continue
        if pairs:
            rho = float(sum(pairs) / len(pairs))
            corr_label = (
                f"average realised pairwise correlation over the trailing "
                f"year to {pricing_date.isoformat()} from cached FRED "
                f"closes ({len(pairs)} pair(s))"
            )
        else:
            rho, corr_label = (
                STATED_CORRELATION,
                f"no pair with two confirmed FRED closes series: stated {STATED_CORRELATION}",
            )

    vol_anchored = ["via FRED" in label for label in vol_labels]
    market = {
        "rate": rate,
        "spread": STATED_SPREAD,
        "vols": tuple(vols),
        "dividends": (STATED_DIVIDEND,) * n,
        "rho": rho,
    }
    report = {
        "rate": rate,
        "rate_label": rate_label,
        "vols": list(vols),
        "vol_anchored": list(vol_anchored),
        "vol_labels": vol_labels,
        "rho": rho,
        "corr_label": corr_label,
        "spread": STATED_SPREAD,
        "dividend": STATED_DIVIDEND,
        "erp": STATED_ERP,
    }
    return market, report


def inputs_summary(report: dict[str, Any]) -> str:
    """One-line row text for a note's market inputs (missing data shown)."""
    vols = "; ".join(
        f"{v:.2f}" for v in (report.get("vols") or [])
    )
    return (
        f"r {float(report.get('rate', 0.0)):.4f} ({report.get('rate_label', '')}); "
        f"vol [{vols}]; rho {float(report.get('rho', 0.0)):.2f} ({report.get('corr_label', '')})"
    )


def _days(n: int):
    import datetime

    return datetime.timedelta(days=n)
