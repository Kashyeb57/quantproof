"""Demo-mode data and logic for the Streamlit app (no Streamlit, no network).

Stage A is demo mode only: the four saved JPMorgan filings in
``tests/fixtures`` with their recorded extraction replies.  No API call is
made and no credits are spent.  This module holds every decision the UI
needs (which notes exist, their illustrative default inputs, how a
user edit is applied and labelled, whether the proof battery gates the
verdict, and how the verdict is assembled) so it can be tested without
Streamlit.  All pricing goes through ``quantproof.report.analyse``; this
module adds no pricing maths of its own.

Pure Python + quantproof.  No AI and no network calls.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from datetime import date
from enum import Enum
from pathlib import Path

from quantproof.agent.extract import parse_json_reply, to_termsheet
from quantproof.core.market import MarketInputs
from quantproof.core.termsheet import TermSheet
from quantproof.ingest.html import read_filing
from quantproof.payoffs.worst_of import UnsupportedTermSheet, check_supported
from quantproof.registry import store
from quantproof.report.analysis import analyse, termsheet_summary
from quantproof.validation.battery import check_quotes, run_battery
from quantproof.validation.quotes import verify_termsheet

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"

NOTE_STEMS: tuple[str, ...] = (
    "jpm_424b2-38582",
    "jpm_424b2-ea0309017",
    "jpm_424b2-ea0308498",
    "jpm_424b2-ea0306095",
)

# Illustrative demo defaults per note (not market data).  For 38582 these
# are the inputs of docs/examples/jpm_38582_analysis.md; for the other
# notes they are the illustrative inputs of scripts/validation_report.py.
# Correlation is a single all-pairs rho in the app (equicorrelation).
DEFAULT_INPUTS: dict[str, dict] = {
    "jpm_424b2-38582": {
        "rate": 0.037, "spread": 0.006,
        "vols": (0.24, 0.17), "dividends": (0.012, 0.013), "rho": 0.6,
    },
    "jpm_424b2-ea0309017": {
        "rate": 0.037, "spread": 0.006,
        "vols": (0.17, 0.24, 0.21), "dividends": (0.013, 0.012, 0.007),
        "rho": 0.8,
    },
    "jpm_424b2-ea0308498": {
        "rate": 0.037, "spread": 0.006,
        "vols": (0.35,), "dividends": (0.01,), "rho": 0.0,
    },
    "jpm_424b2-ea0306095": {
        "rate": 0.037, "spread": 0.006,
        "vols": (0.30, 0.35, 0.28), "dividends": (0.0, 0.01, 0.012),
        "rho": 0.5,
    },
}

# Fast demo path counts: the whole 38582 pipeline (battery + range +
# loss odds + heatmap) finishes in a few seconds at these sizes, far
# under the ~20 s budget, and the base standard error is still shown.
APP_PATHS = 4_000
APP_BASE_PATHS = 20_000
APP_BATTERY_PATHS = 4_000
APP_SEED = 0

TREASURY_CSV = ROOT / "docs" / "data" / "treasury_daily_par_yield_curve_2025-11.csv"
TREASURY_DATA_DIR = ROOT / "docs" / "data"


def treasury_par_rate_for(
    pricing_date: date, maturity_date: date, data_dir: Path | None = None
) -> tuple[float, str]:
    """Treasury par yield from the monthly curve file for the pricing date.

    Formula: same interpolation as :func:`treasury_par_rate`, reading
    ``treasury_daily_par_yield_curve_YYYY-MM.csv`` under ``data_dir``
    for the pricing date's month. Raises ``ValueError`` naming the
    missing month file or the missing curve row, so callers can say so
    in the row instead of silently substituting a rate.
    """
    directory = Path(data_dir) if data_dir is not None else TREASURY_DATA_DIR
    path = directory / f"treasury_daily_par_yield_curve_{pricing_date:%Y-%m}.csv"
    if not path.is_file():
        raise ValueError(f"no Treasury curve file for {pricing_date:%Y-%m} at {path}")
    return treasury_par_rate(pricing_date, maturity_date, path)

_TENOR_YEARS = {
    "1 Mo": 1 / 12, "1.5 Month": 1.5 / 12, "2 Mo": 2 / 12, "3 Mo": 0.25,
    "4 Mo": 4 / 12, "6 Mo": 0.5, "1 Yr": 1.0, "2 Yr": 2.0, "3 Yr": 3.0,
    "5 Yr": 5.0, "7 Yr": 7.0, "10 Yr": 10.0, "20 Yr": 20.0, "30 Yr": 30.0,
}


def treasury_par_rate(
    pricing_date: date, maturity_date: date, csv_path: Path | None = None
) -> tuple[float, str]:
    """Treasury par yield matched to a note's maturity, interpolated.

    Formula: t = (maturity - pricing).days / 365 (Act/365 year
    fraction); the rate is the linear interpolation between the two
    bracketing tenor yields on the pricing date's curve row, converted
    from percent to a fraction.  Outside the tenor range the nearest
    tenor is used.

    Intuition: the risk-free leg of discounting should be a real,
    dated market rate for the note's own horizon, not a round number;
    matching the maturity keeps the discounting honest about term.

    Reference: U.S. Treasury Daily Par Yield Curve Rates,
    home.treasury.gov (public U.S. government data), saved under
    docs/data/ with its source URL.
    """
    import csv as _csv

    path = Path(csv_path) if csv_path is not None else TREASURY_CSV
    lines = [
        line
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    rows = list(_csv.DictReader(lines))
    wanted = pricing_date.strftime("%m/%d/%Y")
    row = next((r for r in rows if r.get("Date") == wanted), None)
    if row is None:
        raise ValueError(f"no Treasury curve row for {wanted} in {path}")
    curve = sorted(
        (_TENOR_YEARS[name], float(row[name]) / 100.0)
        for name in _TENOR_YEARS
        if row.get(name) not in (None, "", "N/A")
    )
    t = (maturity_date - pricing_date).days / 365.0
    if t <= curve[0][0]:
        rate, label_tenor = curve[0][1], f"{t:.2f}y (nearest tenor)"
    elif t >= curve[-1][0]:
        rate, label_tenor = curve[-1][1], f"{t:.2f}y (nearest tenor)"
    else:
        for (t0, y0), (t1, y1) in zip(curve, curve[1:]):
            if t0 <= t <= t1:
                rate = y0 + (t - t0) / (t1 - t0) * (y1 - y0)
                label_tenor = f"{t:.2f}y interpolated between {t0:g} Yr and {t1:g} Yr"
                break
    label = f"U.S. Treasury par yield, {pricing_date.isoformat()}, {label_tenor}"
    return rate, label


def note_defaults(stem: str, ts: TermSheet) -> dict:
    """Default app inputs for a note, with the rate source labelled.

    For the 38582 note the risk-free rate is the real Treasury par
    yield for its pricing date and maturity (see
    :func:`treasury_par_rate`); volatilities, dividends and correlation
    remain stated illustrative assumptions for every note.
    """
    defaults = dict(DEFAULT_INPUTS[stem]) if stem in DEFAULT_INPUTS else {
        "rate": 0.037, "spread": 0.006,
        "vols": (0.25,) * ts.n_underlyings,
        "dividends": (0.01,) * ts.n_underlyings,
        "rho": 0.6 if ts.n_underlyings > 1 else 0.0,
    }
    defaults["rate_label"] = "risk-free rate (illustrative assumption)"
    if stem == "jpm_424b2-38582":
        try:
            rate, label = treasury_par_rate(
                ts.pricing_date.value, ts.maturity_date.value
            )
            defaults["rate"] = rate
            defaults["rate_label"] = label
        except (OSError, ValueError):
            pass
    return defaults


SENSITIVITY_LABELS = {
    "vol": "Volatility",
    "correlation": "Correlation",
    "spread": "Issuer credit spread",
    "dividend": "Dividend yields",
    "skew": "Skew",
}

def escape_dollars(text: object) -> str:
    """Escape ``$`` for Streamlit text so money is not rendered as LaTeX.

    Streamlit treats text between two ``$`` signs as a maths formula, so
    a line containing two money amounts loses its dollar signs and is
    set in a maths font.  Replacing each unescaped ``$`` with ``\\$``
    keeps the literal dollar sign.  Idempotent: an already-escaped
    ``\\$`` is left alone.
    """
    return re.sub(r"(?<!\\)\$", r"\\$", str(text))


def unescaped_dollar_count(text: object) -> int:
    """Number of ``$`` not preceded by a backslash (LaTeX delimiters)."""
    return len(re.findall(r"(?<!\\)\$", str(text)))


ISSUER_RISK_LINE = (
    "If the issuer defaults, you can lose money regardless of the index."
)
FOOTER_LINE = "Risk analysis under stated assumptions, not investment advice"
ILLUSTRATIVE_BANNER = "Illustrative inputs, not market data"


@dataclass
class DemoNote:
    """One demo note: term sheet plus its verified-quote checks."""

    stem: str
    termsheet: TermSheet
    filing_text: str
    checks: list  # QuoteCheck list from verify_termsheet
    unverified: list[str]


def note_label(stem: str, ts: TermSheet) -> str:
    """Short picker label: CUSIP plus underlying names (filing values)."""
    names = ", ".join(u.name.value for u in ts.underlyings)
    cusip = ts.cusip.value if ts.cusip is not None else "no CUSIP"
    return f"{stem} ({cusip}): {names}"


def load_note(stem: str) -> DemoNote:
    """Load a demo note from its saved filing and recorded reply (offline)."""
    filing = read_filing(
        (FIXTURES / "filings" / f"{stem}.htm").read_bytes()
    )
    ts = to_termsheet(
        parse_json_reply(
            (FIXTURES / "extractions" / f"{stem}.reply.json").read_text(
                encoding="utf-8"
            )
        )
    )
    checks = verify_termsheet(ts, filing.text)
    return DemoNote(
        stem=stem,
        termsheet=ts,
        filing_text=filing.text,
        checks=checks,
        unverified=[str(c.path) for c in checks if not c.verified],
    )


def term_rows(note: DemoNote, edited: set[str] | None = None) -> list[dict]:
    """Rows for the term-sheet table: field, value, quote, status.

    Status is ``user-entered`` for fields the user edited, else
    ``verified`` or ``needs confirmation`` from the quote checks.
    """
    edited = edited or set()
    summary = termsheet_summary(note.termsheet, note.checks)
    rows = []
    for term in summary["terms"]:
        path = str(term["path"])
        if path in edited:
            status = "user-entered"
        elif term["verified"]:
            status = "verified"
        else:
            status = "needs confirmation"
        value = term["value"]
        rows.append(
            {
                "field": path,
                "value": "" if value is None else str(value),
                "quote": str(term["quote"]),
                "status": status,
            }
        )
    return rows


def recorded_crosscheck_diffs(stem: str, ts: TermSheet) -> list | None:
    """Field diffs from the recorded second extraction, or None.

    Demo mode replays the saved cross-check reply in
    ``tests/fixtures/crosscheck/<stem>.calls.json`` (no API call): the
    second term sheet is rebuilt from that reply and diffed against the
    first.  Returns None when the note has no recorded cross-check.
    """
    calls_path = FIXTURES / "crosscheck" / f"{stem}.calls.json"
    if not calls_path.exists():
        return None
    from quantproof.agent.crosscheck import diff_termsheets

    records = json.loads(calls_path.read_text(encoding="utf-8"))
    if not isinstance(records, list) or not records:
        return None
    text = records[0].get("text")
    if not isinstance(text, str) or not text:
        return None
    second = to_termsheet(parse_json_reply(text))
    return diff_termsheets(ts, second)


_TOKEN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]")


def _get_by_path(root: object, path: str) -> object:
    obj: object = root
    for match in _TOKEN.finditer(path):
        token = match.group(1) if match.group(1) is not None else int(match.group(2))
        obj = obj[token] if isinstance(token, int) else getattr(obj, str(token))
    return obj


def _parse_like(raw: str, original: object) -> object:
    """Parse user text ``raw`` as the type of ``original`` cited value."""
    if isinstance(original, bool):
        lowered = raw.strip().lower()
        if lowered in ("true", "yes"):
            return True
        if lowered in ("false", "no"):
            return False
        raise ValueError(f"expected true or false, got {raw!r}")
    if isinstance(original, Enum):
        return type(original)(raw.strip())
    if isinstance(original, float):
        return float(raw)
    if isinstance(original, int):
        return int(raw)
    if isinstance(original, date):
        return date.fromisoformat(raw.strip())
    return raw


def apply_user_edits(
    ts: TermSheet, edits: dict[str, str]
) -> tuple[TermSheet, set[str], list[str]]:
    """Return (edited copy, edited paths, errors); the input is not mutated.

    Each edited field keeps its new value with ``citation=None`` so the
    quote verifier and the UI label it ``user-entered``, never verified.
    Unparseable edits are reported in ``errors`` and skipped.
    """
    new_ts = copy.deepcopy(ts)
    edited: set[str] = set()
    errors: list[str] = []
    originals = {path: cited for path, cited in ts.cited_fields()}
    for path, raw in edits.items():
        cited = originals.get(path)
        if cited is None:
            errors.append(f"{path}: no such field")
            continue
        try:
            value = _parse_like(raw, cited.value)
        except (ValueError, TypeError) as exc:
            errors.append(f"{path}: {exc}")
            continue
        target = _get_by_path(new_ts, path)
        target.value = value  # type: ignore[attr-defined]
        target.citation = None  # type: ignore[attr-defined]
        edited.add(path)
    return new_ts, edited, errors


def build_market(
    ts: TermSheet,
    *,
    rate: float,
    spread: float,
    vols: tuple[float, ...],
    dividends: tuple[float, ...],
    rho: float,
) -> MarketInputs:
    """MarketInputs from app inputs; correlation is one all-pairs rho."""
    n = ts.n_underlyings
    correlation = tuple(
        tuple(1.0 if i == j else float(rho) for j in range(n)) for i in range(n)
    )
    return MarketInputs(
        as_of=ts.pricing_date.value,
        risk_free_rate=float(rate),
        vols=tuple(float(v) for v in vols),
        dividend_yields=tuple(float(q) for q in dividends),
        correlation=correlation,
        credit_spread=float(spread),
        sources={"all": "illustrative inputs entered in the app, not market data"},
    )


def maths_passed(results: list[dict]) -> bool:
    """Gate for the verdict: every non-quotes (maths) check passed.

    A quotes shortfall is not a maths failure: flagged fields were shown
    to the user for confirmation in the term-sheet step.
    """
    maths = [r for r in results if r.get("name") != "quotes"]
    return bool(maths) and all(r.get("passed") for r in maths)


def _result_dict(result: object) -> dict:
    return {
        "name": result.name,  # type: ignore[attr-defined]
        "passed": bool(result.passed),  # type: ignore[attr-defined]
        "value": result.value,  # type: ignore[attr-defined]
        "reference": result.reference,  # type: ignore[attr-defined]
        "tolerance": result.tolerance,  # type: ignore[attr-defined]
        "detail": str(result.detail),  # type: ignore[attr-defined]
    }


def run_proof_battery(
    note: DemoNote,
    ts: TermSheet,
    market: MarketInputs,
    *,
    n_paths: int = APP_BATTERY_PATHS,
    seed: int = APP_SEED,
) -> dict:
    """Proof checks for the app: template battery, or registry proof.

    Template notes run ``validation.battery.run_battery`` live.  A note
    the templates refuse is shown with the synthesized-payoff battery
    recorded when its agent-written payoff entered the registry
    (``store.load`` re-verifies the file hash and the recorded pass);
    its quotes check still runs live here.
    """
    try:
        check_supported(ts)
    except UnsupportedTermSheet as error:
        name = store.find_for(ts)
        if name is None:
            return {
                "kind": "refused", "results": [], "passed": False,
                "note": str(error),
            }
        _code, metadata = store.load(name)
        results = [dict(entry) for entry in metadata.get("battery") or []]
        results.append(_result_dict(check_quotes(ts, note.filing_text)))
        return {
            "kind": "synthesized",
            "results": results,
            "passed": maths_passed(results) and bool(metadata.get("passed")),
            "note": (
                f"Agent-written payoff {name}: checks recorded when it "
                "entered the registry; file integrity re-verified on load. "
                "The quotes check above ran live in the app."
            ),
        }
    results = [
        _result_dict(r)
        for r in run_battery(
            ts, market, n_paths=n_paths, seed=seed, filing_text=note.filing_text
        )
    ]
    return {
        "kind": "template",
        "results": results,
        "passed": maths_passed(results),
        "note": "Template proof battery, run live in the app.",
    }


def run_analysis(
    note: DemoNote,
    ts: TermSheet,
    market: MarketInputs,
    *,
    paths: int = APP_PATHS,
    base_paths: int = APP_BASE_PATHS,
    seed: int = APP_SEED,
) -> dict:
    """Full verdict analysis (fair range, loss odds, heatmap) via the CLI engine."""
    return analyse(
        ts,
        note.checks,
        market,
        paths=paths,
        base_paths=base_paths,
        seed=seed,
        erp=0.05,
        battery=False,
        filing_text=note.filing_text,
    )


def dominant_driver(sensitivities: dict) -> tuple[str, float, float] | None:
    """(label, low, high) of the assumption moving the value most.

    The move of an assumption is |high-shift value - low-shift value|
    over the sensitivity pairs the engine already computed; the largest
    move wins.  No new pricing is done here.
    """
    best: tuple[str, float, float] | None = None
    best_move = -1.0
    for name, pair in (sensitivities or {}).items():
        if not pair or len(pair) < 2:
            continue
        low, high = float(pair[0]), float(pair[1])
        move = abs(high - low)
        if move > best_move:
            best_move = move
            best = (SENSITIVITY_LABELS.get(str(name), str(name)), low, high)
    return best


def verdict_summary(result: dict) -> dict:
    """Side-by-side verdict numbers, all copied from engine outputs."""
    summary = result["termsheet"]
    fair = result["fair_value"]
    base = fair.get("base") or {}
    return {
        "issue_price": summary.get("issue_price"),
        "bank_estimate": (
            fair.get("bank_estimate")
            if fair.get("bank_estimate") is not None
            else summary.get("estimated_value")
        ),
        "low": fair.get("low"),
        "high": fair.get("high"),
        "base_price": base.get("price"),
        "base_std_error": base.get("std_error"),
        "seed": fair.get("seed"),
        "grid_paths": fair.get("n_paths"),
        "base_paths": base.get("paths"),
        "bank_position": fair.get("bank_position"),
        "driver": dominant_driver(fair.get("sensitivities") or {}),
        "preliminary": bool(summary.get("is_preliminary")),
    }

