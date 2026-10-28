"""Summarise saved market-audit results into rows, aggregates and reports.

This module is pure Python (``json``, ``csv``, ``statistics``, ``re`` and
``datetime`` only).  It has no network access and performs no pricing maths
of its own: every number it outputs is computed only from the saved rows
under ``data/market_audit/`` written by ``scripts/market_audit.py``.

The saved per-note result dict shape is defined by
``quantproof.report.audit_run.audit_note`` (see that module for the full
shape).  In brief, each ``results/<accession>.json`` contains a ``meta``
dict (issuer group, accession, filing URL/date and, when the outer audit
script recorded them, model cost/timing), top-level extracted values
(``issuer``, ``pricing_date``, ``cusip``, ``underlyings``, ``structure``,
``issue_price``, ``estimated_value``, ``notional``), a ``status`` and
``reason``, a ``quotes`` dict (``checked``/``verified``), a ``crosscheck``
dict (``agreed``/``diffs``), a ``battery`` value, a ``fair_value`` dict
(``low``, ``high`` and ``base`` with ``price``/``std_error``), a
``loss_odds`` dict (``prob_loss``, from the separate real-world
simulation), a ``fields`` list (every extracted field with ``value``,
``quote`` and ``verified``), and timing (``seconds``).

Language: neutral and factual, per AGENTS.md rule 8.  "Cost to investor"
is the bank's own disclosed figure: issue price minus the bank's
estimated value.  This module gives no investment advice.

Reference: AGENTS.md rules 5, 5a and 8; project brief section 6.
"""

from __future__ import annotations

import csv
import json
import re
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

# Words that must never appear in user-facing audit output (AGENTS.md
# rule 8).  "worst-of" is the name of a payoff structure and is allowed;
# see check_neutral.
BANNED_WORDS: tuple[str, ...] = (
    "rip-off",
    "greed",
    "overpriced",
    "worst",
    "best",
    "cheap",
    "expensive",
    "buy",
    "sell",
)

_ASSUMPTIONS_SENTENCE = (
    "Volatilities, dividends, correlation, rate and credit spread are "
    "stated assumptions, not market data as of each pricing date."
)

_CROSSCHECK_NOTE = (
    "Cross-check note: each extraction was compared with a second "
    "extraction by a different model version, and any differing fields "
    "are recorded in the saved results. This is a weaker independence "
    "check than two genuinely different model services, and the audit "
    "outputs should be read with that limitation stated."
)

_NOT_ADVICE_SENTENCE = (
    "This is a risk analysis under stated assumptions, not investment advice."
)


def _meta(result: dict) -> dict:
    """Return the result's ``meta`` dict, or an empty dict."""
    meta = result.get("meta")
    return meta if isinstance(meta, dict) else {}


def _first(result: dict, meta: dict, *keys: str) -> Any:
    """First non-None value among ``keys``, checking result then meta."""
    for source in (result, meta):
        for key in keys:
            value = source.get(key)
            if value is not None:
                return value
    return None


def _sum_calls(result: dict, field: str) -> float | None:
    """Sum ``field`` over a recorded ``calls`` list, if one is present."""
    calls = result.get("calls")
    if not isinstance(calls, list) or not calls:
        return None
    total = 0.0
    found = False
    for call in calls:
        if isinstance(call, dict) and call.get(field) is not None:
            total += float(call[field])
            found = True
    return total if found else None


def load_results(audit_dir: Path = Path("data/market_audit")) -> list[dict]:
    """Load every ``results/*.json`` under ``audit_dir``.

    Returns the parsed result dicts sorted by (issuer_group, accession),
    using the same field lookup as :func:`note_row` so the ordering is
    stable even when a value lives in ``meta`` rather than at top level.
    A missing ``results`` directory gives an empty list.
    """
    results_dir = Path(audit_dir) / "results"
    loaded: list[dict] = []
    if results_dir.is_dir():
        for path in sorted(results_dir.glob("*.json")):
            with path.open(encoding="utf-8") as handle:
                loaded.append(json.load(handle))

    def sort_key(result: dict) -> tuple[str, str]:
        meta = _meta(result)
        issuer_group = _first(result, meta, "issuer_group") or ""
        accession = _first(result, meta, "accession") or ""
        return (str(issuer_group), str(accession))

    return sorted(loaded, key=sort_key)


def load_run(audit_dir: Path = Path("data/market_audit")) -> dict:
    """Load ``run.json`` under ``audit_dir``; ``{}`` if it is missing."""
    path = Path(audit_dir) / "run.json"
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def note_row(result: dict) -> dict:
    """Flatten one saved audit result into a display/export row.

    ``cost_to_investor`` is the bank's own disclosed figure: issue price
    minus the bank's estimated value, both per note in dollars.  It is
    computed only when both are present, and it is never adjusted.
    ``bank_in_range`` compares the bank's estimated value with our
    risk-neutral fair-value range and is set only when priced (a range
    and an estimated value are both present).  Values that are
    unavailable are ``None``.
    """
    meta = _meta(result)

    status = result.get("status")
    issue_price = result.get("issue_price")
    estimated_value = result.get("estimated_value")

    if issue_price is not None and estimated_value is not None:
        cost_to_investor: float | None = float(issue_price) - float(estimated_value)
    else:
        cost_to_investor = None
    if cost_to_investor is not None and issue_price:
        cost_to_investor_pct: float | None = cost_to_investor / float(issue_price) * 100.0
    else:
        cost_to_investor_pct = None
    # Per $1,000 of notional, so $10 and $1,000 notes can share one median.
    notional = result.get("notional")
    if cost_to_investor is not None and notional:
        cost_per_1000: float | None = cost_to_investor / float(notional) * 1000.0
    else:
        cost_per_1000 = None

    fair_value = result.get("fair_value")
    if not isinstance(fair_value, dict):
        fair_value = {}
    base = fair_value.get("base")
    if not isinstance(base, dict):
        base = {}
    range_low = fair_value.get("low")
    range_high = fair_value.get("high")

    if (
        status == "priced"
        and range_low is not None
        and range_high is not None
        and estimated_value is not None
    ):
        bank_in_range: bool | None = float(range_low) <= float(estimated_value) <= float(range_high)
    else:
        bank_in_range = None

    loss_odds = result.get("loss_odds")
    if not isinstance(loss_odds, dict):
        loss_odds = {}
    prob_loss = loss_odds.get("prob_loss")

    quotes = result.get("quotes")
    if not isinstance(quotes, dict):
        quotes = {}
    quotes_checked = quotes.get("checked")
    quotes_verified = quotes.get("verified")
    if quotes_checked:
        quote_verification_rate: float | None = float(quotes_verified or 0) / float(quotes_checked)
    else:
        quote_verification_rate = None

    crosscheck = result.get("crosscheck")
    if not isinstance(crosscheck, dict):
        crosscheck = {}
    crosscheck_agreed = crosscheck.get("agreed") if crosscheck else None
    diffs = crosscheck.get("diffs")
    crosscheck_diffs = len(diffs) if isinstance(diffs, list) else 0

    if status == "priced":
        supported: bool | None = True
    elif status in ("unsupported", "battery_failed"):
        supported = False
    else:
        supported = None

    underlyings = result.get("underlyings")
    if isinstance(underlyings, list):
        underlyings_text: str | None = "; ".join(str(name) for name in underlyings)
    elif underlyings is not None:
        underlyings_text = str(underlyings)
    else:
        underlyings_text = None

    cost_usd = _first(result, meta, "cost_usd")
    if cost_usd is None:
        cost_usd = _sum_calls(result, "cost_usd")
    model_seconds = _first(result, meta, "model_seconds")
    if model_seconds is None:
        model_seconds = _sum_calls(result, "seconds")

    return {
        "issuer_group": _first(result, meta, "issuer_group"),
        "issuer": result.get("issuer"),
        "accession": _first(result, meta, "accession"),
        "url": _first(result, meta, "url"),
        "filing_date": _first(result, meta, "filing_date"),
        "pricing_date": result.get("pricing_date"),
        "cusip": result.get("cusip"),
        "underlyings": underlyings_text,
        "structure": result.get("structure"),
        "status": status,
        "reason": result.get("reason"),
        "issue_price": issue_price,
        "estimated_value": estimated_value,
        "notional": result.get("notional"),
        "cost_to_investor": cost_to_investor,
        "cost_to_investor_pct": cost_to_investor_pct,
        "cost_to_investor_per_1000": cost_per_1000,
        "range_low": range_low,
        "range_high": range_high,
        "base_value": base.get("price"),
        "base_std_error": base.get("std_error"),
        "bank_in_range": bank_in_range,
        "prob_loss": prob_loss,
        "quotes_checked": quotes_checked,
        "quotes_verified": quotes_verified,
        "quote_verification_rate": quote_verification_rate,
        "crosscheck_agreed": crosscheck_agreed,
        "crosscheck_diffs": crosscheck_diffs,
        "supported": supported,
        "cost_usd": cost_usd,
        "model_seconds": model_seconds,
        "seconds": result.get("seconds"),
    }


def _wall_seconds(run: dict) -> float | None:
    """Run duration from ``started``/``finished`` (numbers or ISO text)."""
    started = run.get("started")
    finished = run.get("finished")
    if started is None or finished is None:
        return None
    if isinstance(started, (int, float)) and isinstance(finished, (int, float)):
        return float(finished) - float(started)
    try:
        start_dt = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
        finish_dt = datetime.fromisoformat(str(finished).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (finish_dt - start_dt).total_seconds()


def aggregate(rows: list[dict], run: dict) -> dict:
    """Aggregate note rows into summary numbers, computed from the rows.

    Shares are ``None`` when their denominator is 0.  Medians use
    :func:`statistics.median` over the rows where the value is present
    and are ``None`` when no row has it.
    """
    if not isinstance(run, dict):
        run = {}

    notes_read = len(rows)
    notes_extracted = sum(
        1 for row in rows if row.get("status") not in ("extraction_failed", "api_error")
    )

    def _total(field: str) -> float:
        return float(sum(float(row[field]) for row in rows if row.get(field) is not None))

    costs = [
        float(row["cost_to_investor_per_1000"])
        for row in rows
        if row.get("cost_to_investor_per_1000") is not None
    ]
    costs_pct = [
        float(row["cost_to_investor_pct"])
        for row in rows
        if row.get("cost_to_investor_pct") is not None
    ]

    n_with_estimate = sum(1 for row in rows if row.get("estimated_value") is not None)
    n_priced = sum(1 for row in rows if row.get("status") == "priced")
    share_priced = (n_priced / notes_extracted) if notes_extracted else None

    n_bank_in_range = sum(1 for row in rows if row.get("bank_in_range") is True)
    share_bank_in_range = (n_bank_in_range / n_priced) if n_priced else None

    total_checked = sum(int(row["quotes_checked"]) for row in rows if row.get("quotes_checked"))
    total_verified = sum(int(row["quotes_verified"]) for row in rows if row.get("quotes_verified"))
    quote_rate = (total_verified / total_checked) if total_checked else None

    status_counts = dict(sorted(Counter(str(row.get("status")) for row in rows).items()))
    issuers = sorted({str(row["issuer_group"]) for row in rows if row.get("issuer_group")})

    return {
        "notes_read": notes_read,
        "notes_extracted": notes_extracted,
        "total_cost_usd": _total("cost_usd"),
        "total_model_seconds": _total("model_seconds"),
        "wall_seconds": _wall_seconds(run),
        "median_cost_to_investor": statistics.median(costs) if costs else None,
        "median_cost_to_investor_pct": statistics.median(costs_pct) if costs_pct else None,
        "n_with_estimate": n_with_estimate,
        "n_priced": n_priced,
        "share_priced": share_priced,
        "n_bank_in_range": n_bank_in_range,
        "share_bank_in_range": share_bank_in_range,
        "quote_verification_rate": quote_rate,
        "status_counts": status_counts,
        "issuers": issuers,
        "skipped": run.get("skipped"),
        "assumptions": run.get("assumptions"),
    }


def table_columns() -> list[tuple[str, str]]:
    """Ordered (row key, display header) pairs for the per-note table."""
    return [
        ("issuer_group", "Issuer"),
        ("cusip", "CUSIP"),
        ("underlyings", "Underlyings"),
        ("structure", "Structure"),
        ("issue_price", "Issue price ($)"),
        ("estimated_value", "Bank's estimated value ($)"),
        ("cost_to_investor", "Cost to investor ($, bank's figure)"),
        ("range_low", "Our range low ($)"),
        ("range_high", "Our range high ($)"),
        ("prob_loss", "Real-world P(loss)"),
        ("quote_verification_rate", "Quotes verified"),
        ("status", "Status"),
        ("reason", "Reason"),
    ]


_MONEY_KEYS = {"issue_price", "estimated_value", "cost_to_investor", "range_low", "range_high"}
_FRACTION_PCT_1DP_KEYS = {"prob_loss"}
_FRACTION_PCT_KEYS = {"quote_verification_rate"}


def _format_cell(key: str, value: Any) -> str:
    """Format one table cell; ``None`` becomes an empty string."""
    if value is None:
        return ""
    if key in _MONEY_KEYS:
        return f"{float(value):.2f}"
    if key in _FRACTION_PCT_1DP_KEYS:
        return f"{float(value) * 100:.1f}%"
    if key in _FRACTION_PCT_KEYS:
        return f"{float(value) * 100:.1f}%"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _escape_cell(text: str) -> str:
    """Escape pipe characters so a cell cannot break the table layout."""
    return text.replace("|", "\\|")


def _format_money(value: Any) -> str:
    return "" if value is None else f"{float(value):.2f}"


def _format_pct_fraction(value: Any, digits: int = 1) -> str:
    return "" if value is None else f"{float(value) * 100:.{digits}f}%"


def _format_pct_points(value: Any) -> str:
    """Format a value already expressed in percent (e.g. 3.75 -> 3.8%)."""
    return "" if value is None else f"{float(value):.1f}%"


def render_markdown(rows: list[dict], agg: dict) -> str:
    """Render a short neutral report from note rows and their aggregate.

    The bank's estimated value is shown alongside our range for every
    priced note.  Fair value is a range under the stated assumptions;
    P(loss) is from the separate labelled real-world simulation.
    """
    lines: list[str] = []
    lines.append("# Market audit")
    lines.append("")
    lines.append(
        "Each row is one saved audit result. Our estimated fair value is "
        "shown as a range, and the bank's own estimated value is shown "
        "alongside it for every note."
    )
    lines.append("")
    lines.append("## Aggregate")
    lines.append("")
    lines.append(f"- Notes read: {agg.get('notes_read')}")
    lines.append(f"- Notes extracted: {agg.get('notes_extracted')}")
    lines.append(f"- Notes priced: {agg.get('n_priced')}")
    lines.append(f"- Share priced: {_format_pct_fraction(agg.get('share_priced'))}")
    lines.append(f"- Notes with a bank estimated value: {agg.get('n_with_estimate')}")
    lines.append(
        f"- Median cost to investor (per $1,000 notional, bank's figure): "
        f"{_format_money(agg.get('median_cost_to_investor'))}"
    )
    lines.append(
        f"- Median cost to investor (%, bank's figure): "
        f"{_format_pct_points(agg.get('median_cost_to_investor_pct'))}"
    )
    lines.append(
        f"- Bank estimated value within our range (priced notes): "
        f"{agg.get('n_bank_in_range')} of {agg.get('n_priced')} "
        f"({_format_pct_fraction(agg.get('share_bank_in_range'))})"
    )
    lines.append(
        f"- Quote verification rate: "
        f"{_format_pct_fraction(agg.get('quote_verification_rate'))}"
    )
    total_cost = agg.get("total_cost_usd")
    lines.append(f"- Total model cost (USD): {'' if total_cost is None else f'{float(total_cost):.4f}'}")
    total_model_seconds = agg.get("total_model_seconds")
    lines.append(
        f"- Total model seconds: "
        f"{'' if total_model_seconds is None else f'{float(total_model_seconds):.1f}'}"
    )
    issuers = agg.get("issuers") or []
    lines.append(f"- Issuers: {', '.join(str(name) for name in issuers)}")
    status_counts = agg.get("status_counts") or {}
    if status_counts:
        counts_text = ", ".join(
            f"{status}: {count}" for status, count in sorted(status_counts.items())
        )
        lines.append(f"- Status counts: {counts_text}")
    skipped = agg.get("skipped")
    if skipped is not None:
        lines.append(f"- Skipped: {skipped}")
    lines.append("")
    lines.append("## Per-note results")
    lines.append("")
    columns = table_columns()
    lines.append("| " + " | ".join(header for _, header in columns) + " |")
    lines.append("| " + " | ".join("---" for _ in columns) + " |")
    for row in rows:
        cells = [
            _escape_cell(_format_cell(key, row.get(key))) for key, _ in columns
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("## Assumptions")
    lines.append("")
    assumptions = agg.get("assumptions")
    if isinstance(assumptions, dict):
        for key in sorted(assumptions):
            lines.append(f"- {key}: {assumptions[key]}")
    elif assumptions is not None:
        lines.append(f"- {assumptions}")
    lines.append("")
    lines.append(_ASSUMPTIONS_SENTENCE)
    lines.append("")
    lines.append(_CROSSCHECK_NOTE)
    lines.append("")
    lines.append(_NOT_ADVICE_SENTENCE)
    lines.append("")
    return "\n".join(lines)


def export_dataset(results: list[dict], out_dir: Path) -> tuple[Path, Path]:
    """Write ``notes.csv`` and ``notes.json`` for the saved results.

    The CSV has one row per note with the :func:`note_row` keys as its
    columns, in that order.  The JSON is a list of dicts with the flat
    ``row``, the extracted ``fields`` (every field with value, quote and
    verified flag), the ``crosscheck`` and the proof ``battery`` from the
    saved result.  Output is deterministic: results are ordered as by
    :func:`load_results`, JSON uses ``indent=2`` with sorted keys, and
    the CSV is opened with ``newline=""``.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ordered = sorted(
        results,
        key=lambda result: (
            str(_first(result, _meta(result), "issuer_group") or ""),
            str(_first(result, _meta(result), "accession") or ""),
        ),
    )
    rows = [note_row(result) for result in ordered]

    csv_path = out_dir / "notes.csv"
    fieldnames = list(rows[0].keys()) if rows else list(note_row({}).keys())
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    payload = [
        {
            "row": row,
            "fields": result.get("fields"),
            "crosscheck": result.get("crosscheck"),
            "battery": result.get("battery"),
        }
        for result, row in zip(ordered, rows, strict=True)
    ]
    json_path = out_dir / "notes.json"
    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")

    return csv_path, json_path


def check_neutral(text: str) -> list[str]:
    """Return banned words found in ``text``.

    Matching is case-insensitive and whole-word.  The structure label
    "worst-of" is allowed: occurrences of that exact hyphenated label are
    removed before the whole-word search, so "worst-of 2" does not flag
    "worst" while a standalone "worst" does.
    """
    cleaned = re.sub(r"worst-of", " ", str(text), flags=re.IGNORECASE)
    found: list[str] = []
    for word in BANNED_WORDS:
        pattern = r"\b" + re.escape(word) + r"\b"
        if re.search(pattern, cleaned, flags=re.IGNORECASE):
            found.append(word)
    return found
