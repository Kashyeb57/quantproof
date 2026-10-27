"""End-to-end CLI: ingest -> extract -> verify quotes -> price -> report.

This is the I/O layer for the QuantProof pipeline. It reads a saved filing
(or downloads one with ``--url``), obtains a TermSheet either from a recorded
model reply (``--reply``, offline) or from a live extraction (``--live``,
Muse Spark, costs credits), verifies every extracted quote against the
filing text, builds user-entered market inputs, prices the note, and writes
a Markdown risk analysis and/or a JSON result.

Usage:
    python scripts/analyse.py FILING [--url URL] (--reply REPLY.json | --live)
        --rate R --spread S --vols v1,v2 --dividends q1,q2 [--corr RHO]
        [--erp 0.05] [--paths 20000] [--base-paths 100000] [--seed 0]
        [--battery] [--out report.md] [--json out.json]

Standard library + the quantproof package only. No network access happens
unless ``--url`` or ``--live`` is given. The SEC User-Agent used by
EdgarClient is read from the environment inside that client and is never
printed here.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quantproof.agent.extract import (  # noqa: E402
    ExtractionError,
    extract_termsheet,
    parse_json_reply,
    to_termsheet,
)
from quantproof.core.market import MarketInputs  # noqa: E402
from quantproof.ingest.html import read_filing  # noqa: E402
from quantproof.payoffs.worst_of import UnsupportedTermSheet, check_supported  # noqa: E402
from quantproof.risk.real_world import loss_odds, stress_heatmap  # noqa: E402
from quantproof.validation.battery import run_battery  # noqa: E402
from quantproof.validation.quotes import verify_termsheet  # noqa: E402
from quantproof.valuation.fair_value import fair_value_range  # noqa: E402

from quantproof.ingest.edgar import EdgarClient  # noqa: E402


_DIFFER_EXPLANATION = (
    "Banks value their own notes with an internal funding rate, usually below "
    "the market credit spread we discount at; flat vol with a stated skew "
    "adjustment; preliminary terms. These choices, together with the stated "
    "assumptions above, can lead to a different estimated value under these "
    "assumptions. The bank's estimated value is shown as a comparison point."
)


def _json_value(value):
    """Convert a cited value to a JSON-serialisable, report-friendly value."""
    if isinstance(value, Enum):  # first: the TermSheet enums subclass str
        return value.value
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return str(value)


def _display_value(value) -> str:
    """Format a JSON-serialisable value for the Markdown report."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, list):
        return ", ".join(_display_value(item) for item in value)
    return str(value)


def _clean_cell(text: str) -> str:
    """Make text safe for one Markdown table cell."""
    return " ".join(str(text).split()).replace("|", "/")


def _termsheet_summary(ts, checks) -> dict:
    """Build a JSON-serialisable summary of the term sheet, with quotes.

    Formula: no pricing formula; this copies cited filing values and their
    quotes from ``ts.cited_fields()`` and joins them with the quote-check
    results supplied by the caller.

    Intuition: the report may only show numbers from the term sheet (cited
    filing values) or from engine outputs, so every term shown carries the
    quote that supports it and whether that quote was verified.

    Reference: project rule 5a (every extracted term needs a verified quote).
    """
    verified_by_path: dict[str, bool] = {}
    for check in checks or []:
        path = getattr(check, "path", None)
        if path is not None:
            verified_by_path[str(path)] = bool(getattr(check, "verified", False))

    terms: list[dict] = []
    for path, cited in ts.cited_fields():
        citation = cited.citation
        quote = citation.quote if citation is not None else ""
        if path in verified_by_path:
            verified = verified_by_path[path]
        elif citation is not None:
            verified = citation.verified is True
        else:
            verified = False
        terms.append(
            {
                "path": path,
                "value": _json_value(cited.value),
                "quote": quote,
                "section": citation.section if citation is not None else None,
                "verified": bool(verified),
            }
        )

    if checks is not None:
        unverified = [str(c.path) for c in checks if not c.verified]
    else:
        unverified = list(ts.unverified_fields())

    return {
        "issuer": ts.issuer.value,
        "underlying_names": [u.name.value for u in ts.underlyings],
        "n_underlyings": ts.n_underlyings,
        "cusip": ts.cusip.value if ts.cusip is not None else None,
        "is_preliminary": bool(ts.is_preliminary.value),
        "notional": float(ts.notional.value),
        "issue_price": float(ts.issue_price.value),
        "pricing_date": _json_value(ts.pricing_date.value),
        "issue_date": _json_value(ts.issue_date.value),
        "maturity_date": _json_value(ts.maturity_date.value),
        "final_review_date": _json_value(ts.final_review_date.value),
        "estimated_value": (
            float(ts.estimated_value.value) if ts.estimated_value is not None else None
        ),
        "terms": terms,
        "unverified": unverified,
    }


def _check_to_dict(check) -> dict:
    """Convert a battery CheckResult (dataclass or dict-like) to a plain dict."""
    if isinstance(check, dict):
        return {
            "name": check.get("name"),
            "passed": check.get("passed"),
            "value": check.get("value"),
            "reference": check.get("reference"),
            "tolerance": check.get("tolerance"),
            "detail": check.get("detail", ""),
        }
    if is_dataclass(check):
        data = asdict(check)
        return {
            "name": data.get("name"),
            "passed": data.get("passed"),
            "value": data.get("value"),
            "reference": data.get("reference"),
            "tolerance": data.get("tolerance"),
            "detail": data.get("detail", ""),
        }
    return {
        "name": getattr(check, "name", None),
        "passed": getattr(check, "passed", None),
        "value": getattr(check, "value", None),
        "reference": getattr(check, "reference", None),
        "tolerance": getattr(check, "tolerance", None),
        "detail": getattr(check, "detail", ""),
    }


def analyse(ts, checks, market, *, paths, base_paths, seed, erp, battery: bool, filing_text) -> dict:
    """Run the pricing pipeline on an already-extracted, verified term sheet.

    Formula: fair value is the risk-neutral range from ``fair_value_range``
    (grid paths ``paths``, base paths ``base_paths``); loss odds and the
    stress grid are the separate real-world simulations from ``loss_odds``
    (``base_paths``) and ``stress_heatmap`` (``paths``), all with ``seed``.

    Intuition: value and loss odds answer different questions and use
    different measures; keeping them in separate, labelled engine calls is
    what keeps the real-world numbers from ever being read as a value.

    Reference: project brief section 6 (risk-neutral for value, a separate
    labelled real-world simulation for loss odds; fair value as a range).
    """
    fair = fair_value_range(ts, market, n_paths=paths, seed=seed, n_paths_base=base_paths)
    odds = loss_odds(
        ts, market, equity_risk_premium=erp, n_paths=base_paths, seed=seed
    )
    stress = stress_heatmap(ts, market, n_paths=paths, seed=seed)

    battery_dicts = None
    if battery:
        results = run_battery(
            ts, market, n_paths=paths, seed=seed, filing_text=filing_text
        )
        battery_dicts = [_check_to_dict(result) for result in results]

    summary = _termsheet_summary(ts, checks)
    return {
        "termsheet": summary,
        "unverified": list(summary["unverified"]),
        "fair_value": fair.to_dict(),
        "loss_odds": odds.to_dict(),
        "stress": stress.to_dict(),
        "battery": battery_dicts,
    }


def _fmt_number(value, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def _fmt_money(value) -> str:
    if value is None:
        return "n/a"
    return f"${float(value):,.2f}"


def _fmt_percent(value) -> str:
    if value is None:
        return "n/a"
    return f"{100.0 * float(value):.2f}%"


def _fmt_prob_with_se(probability, std_error) -> str:
    if probability is None:
        return "n/a"
    if std_error is None:
        return _fmt_percent(probability)
    return f"{_fmt_percent(probability)} (SE {_fmt_percent(std_error)})"


def render_report(result: dict) -> str:
    """Render the Markdown risk analysis from a result dict.

    Formula: no pricing formula; every number below is copied from the
    result dict (engine outputs) or the term-sheet summary (cited filing
    values).

    Intuition: a fixed section order and fixed wording keep the risk-neutral
    estimated value and the real-world loss odds clearly separate, and keep
    unverified terms flagged rather than silent.

    Reference: project rules 5, 5a and 8 (provenance, verified quotes, no
    investment advice).
    """
    summary = result["termsheet"]
    fair = result["fair_value"]
    odds = result["loss_odds"]
    stress = result["stress"]
    unverified = result.get("unverified") or []

    lines: list[str] = []

    # 1. Heading.
    names = ", ".join(summary.get("underlying_names") or [])
    lines.append(f"# QuantProof risk analysis: {summary.get('issuer', '')}, {names}")
    lines.append("")
    if summary.get("is_preliminary"):
        lines.append("Based on preliminary terms.")
        lines.append("")
    if summary.get("cusip"):
        lines.append(f"CUSIP: {summary['cusip']}")
        lines.append("")
    lines.append(
        "> **Illustrative inputs, not market data:** the market inputs in this "
        "report are illustrative assumptions entered for this analysis. They "
        "are not market data, and every result below is conditional on them."
    )
    lines.append("")

    # 2. Extracted terms.
    lines.append("## Extracted terms")
    lines.append("")
    lines.append("| Term | Value | Filing quote |")
    lines.append("| --- | --- | --- |")
    for term in summary.get("terms") or []:
        lines.append(
            f"| {_clean_cell(term.get('path', ''))} "
            f"| {_clean_cell(_display_value(term.get('value')))} "
            f"| {_clean_cell(term.get('quote', ''))} |"
        )
    lines.append("")
    if unverified:
        lines.append("Flagged (unverified quote):")
        lines.append("")
        for path in unverified:
            lines.append(f"- {path}")
        lines.append("")
        lines.append(
            "The flagged fields above have no verified quote in the filing text; "
            "they are flagged here and are not used silently."
        )
        lines.append("")

    # 3. Estimated value (risk-neutral).
    lines.append("## Estimated value (risk-neutral)")
    lines.append("")
    lines.append(
        f"Estimated value range under these assumptions: "
        f"{_fmt_money(fair.get('low'))} - {_fmt_money(fair.get('high'))}"
    )
    lines.append("")
    base = fair.get("base") or {}
    base_price = base.get("price")
    base_se = base.get("std_error")
    if base_price is not None:
        se_value = float(base_se) if base_se is not None else 0.0
        lines.append(
            f"Base estimated value: {_fmt_money(base_price)} "
            f"+- {_fmt_money(1.96 * se_value)} (1.96 SE, SE = {_fmt_money(se_value)}), "
            f"seed {base.get('seed', fair.get('seed'))}, "
            f"paths {base.get('paths', fair.get('n_paths'))}."
        )
        lines.append("")
        lines.append(
            f"Grid paths per point: {fair.get('n_paths')}, seed: {fair.get('seed')}."
        )
        lines.append("")
    bank_estimate = fair.get("bank_estimate")
    if bank_estimate is None:
        bank_estimate = summary.get("estimated_value")
    lines.append("| Bank's estimated value | Issue price |")
    lines.append("| --- | --- |")
    lines.append(
        f"| {_fmt_money(bank_estimate)} | {_fmt_money(summary.get('issue_price'))} |"
    )
    lines.append("")
    position = fair.get("bank_position")
    if position is not None:
        lines.append(f"Position of the bank's estimated value: {position}.")
        lines.append("")
    elif bank_estimate is not None:
        lines.append("Position of the bank's estimated value: not stated.")
        lines.append("")

    lines.append("| Assumption | Low-shift value | High-shift value |")
    lines.append("| --- | --- | --- |")
    sensitivity_labels = {
        "vol": "Volatility",
        "correlation": "Correlation",
        "spread": "Issuer credit spread",
        "dividend": "Dividend yields",
        "skew": "Skew",
    }
    sensitivities = fair.get("sensitivities") or {}
    for name, pair in sensitivities.items():
        label = sensitivity_labels.get(str(name), str(name))
        low_value = pair[0] if pair else None
        high_value = pair[1] if len(pair) > 1 else None
        lines.append(
            f"| {_clean_cell(label)} | {_fmt_money(low_value)} | {_fmt_money(high_value)} |"
        )
    lines.append("")

    if sensitivities:
        def _move(pair) -> float:
            try:
                return abs(float(pair[1]) - float(pair[0]))
            except (TypeError, ValueError, IndexError):
                return -1.0

        dominant_name, dominant_pair = max(
            sensitivities.items(), key=lambda item: _move(item[1])
        )
        dominant_label = sensitivity_labels.get(str(dominant_name), str(dominant_name))
        dominant_low = dominant_pair[0] if dominant_pair else None
        dominant_high = dominant_pair[1] if len(dominant_pair) > 1 else None
        lines.append(
            f"The assumption that moves the estimated value most is "
            f"{dominant_label}: {_fmt_money(dominant_low)} to "
            f"{_fmt_money(dominant_high)} across its stated range."
        )
        lines.append("")

    lines.append("Assumptions:")
    lines.append("")
    for assumption in fair.get("assumptions") or []:
        lines.append(f"- {_clean_cell(assumption)}")
    lines.append("")

    lines.append("### Why our value can differ from the bank's")
    lines.append("")
    lines.append(_DIFFER_EXPLANATION)
    lines.append("")

    # 4. Real-world loss odds.
    lines.append("## Real-world loss odds (REAL-WORLD measure, not a value)")
    lines.append("")
    lines.append(
        "These figures use the REAL-WORLD measure. They are not a value and "
        "must not be read as an estimated value."
    )
    lines.append("")
    if odds.get("drift_note"):
        lines.append(f"Drift: {odds['drift_note']}")
        lines.append("")
    drifts = odds.get("drifts")
    if drifts:
        lines.append(
            "Real-world drifts: " + ", ".join(_fmt_number(d) for d in drifts)
        )
        lines.append("")
    std_errors = odds.get("std_errors") or {}
    lines.append(
        f"- P(loss): {_fmt_prob_with_se(odds.get('prob_loss'), std_errors.get('prob_loss'))}"
    )
    lines.append(
        f"- P(loss > 20%): "
        f"{_fmt_prob_with_se(odds.get('prob_loss_over_20'), std_errors.get('prob_loss_over_20'))}"
    )
    lines.append(
        f"- P(loss > 50%): "
        f"{_fmt_prob_with_se(odds.get('prob_loss_over_50'), std_errors.get('prob_loss_over_50'))}"
    )
    if odds.get("prob_called") is None:
        lines.append("- P(called early): n/a (no autocall)")
    else:
        lines.append(
            f"- P(called early): "
            f"{_fmt_prob_with_se(odds.get('prob_called'), std_errors.get('prob_called'))}"
        )
    expected_cash = odds.get("expected_cash")
    expected_se = std_errors.get("expected_cash")
    if expected_cash is not None and expected_se is not None:
        lines.append(
            f"- Expected cash: {_fmt_money(expected_cash)} (SE {_fmt_money(expected_se)})"
        )
    elif expected_cash is not None:
        lines.append(f"- Expected cash: {_fmt_money(expected_cash)}")
    quantiles = odds.get("quantiles") or {}
    lines.append(f"- p05: {_fmt_money(quantiles.get('p05'))}")
    lines.append(f"- p50: {_fmt_money(quantiles.get('p50'))}")
    lines.append(f"- p95: {_fmt_money(quantiles.get('p95'))}")
    lines.append(
        f"- Paths: {odds.get('n_paths')}, seed: {odds.get('seed')}, "
        f"measure: {odds.get('measure')}"
    )
    lines.append("")
    lines.append("Issuer default is not simulated: you can lose money if the issuer defaults.")
    lines.append("")

    # 5. Stress heatmap.
    lines.append("## Stress heatmap (real-world)")
    lines.append("")
    lines.append(
        "Cells are P(loss) as percent under the real-world measure, for each "
        "drift (rows) and vol scale (columns)."
    )
    lines.append("")
    vol_scales = stress.get("vol_scales") or []
    header = "| Drift | " + " | ".join(f"{float(v):.2f}x" for v in vol_scales) + " |"
    separator = "| --- | " + " | ".join("---" for _ in vol_scales) + " |"
    lines.append(header)
    lines.append(separator)
    drift_levels = stress.get("drift_levels") or []
    prob_rows = stress.get("prob_loss") or []
    for i, drift in enumerate(drift_levels):
        row = prob_rows[i] if i < len(prob_rows) else []
        cells = " | ".join(_fmt_percent(row[j]) if j < len(row) else "n/a" for j in range(len(vol_scales)))
        lines.append(f"| {_fmt_number(drift)} | {cells} |")
    lines.append("")

    # 6. Proof battery (only if run).
    battery = result.get("battery")
    if battery is not None:
        lines.append("## Proof battery")
        lines.append("")
        lines.append("| Name | passed | Value | Reference | Tolerance |")
        lines.append("| --- | --- | --- | --- | --- |")
        for check in battery:
            lines.append(
                f"| {_clean_cell(check.get('name', ''))} "
                f"| {check.get('passed')} "
                f"| {_fmt_number(check.get('value'), 6)} "
                f"| {_fmt_number(check.get('reference'), 6)} "
                f"| {_fmt_number(check.get('tolerance'), 6)} |"
            )
        lines.append("")

    # 7. Limitations.
    lines.append("## Limitations")
    lines.append("")
    lines.append("- Flat vol with a simple stated skew adjustment; no volatility surface.")
    lines.append("- Constant rates and constant dividends over the life of the note.")
    lines.append("- No issuer default simulation; issuer default is not simulated in the loss odds or the stress grid.")
    lines.append("- Model risk: the Monte Carlo model and its stated assumptions may not match realised outcomes.")
    lines.append("- The market inputs are the user's entered inputs; they are illustrative inputs supplied on the command line, not market data.")
    lines.append("- This is a risk analysis under these assumptions, not investment advice.")
    lines.append("")

    return "\n".join(lines)


def _parse_float_list(text: str, option: str) -> list[float]:
    """Parse a comma-separated list of floats for ``option``."""
    values: list[float] = []
    for part in str(text).split(","):
        part = part.strip()
        if not part:
            raise ValueError(f"{option} contains an empty entry")
        values.append(float(part))
    return values


def main(argv: list[str] | None = None) -> int:
    """Run ingest -> extract -> verify quotes -> price -> report; return exit code."""
    parser = argparse.ArgumentParser(
        description="QuantProof risk analysis for a structured-note filing."
    )
    parser.add_argument("filing", metavar="FILING", help="saved filing .htm path")
    parser.add_argument("--url", type=str, default=None, help="EDGAR URL to download to FILING")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--reply", type=str, default=None, help="recorded model reply JSON")
    group.add_argument("--live", action="store_true", help="live extraction (costs credits)")
    parser.add_argument("--rate", type=float, required=True)
    parser.add_argument("--spread", type=float, required=True)
    parser.add_argument("--vols", type=str, required=True, help="comma-separated vols")
    parser.add_argument("--dividends", type=str, required=True, help="comma-separated dividend yields")
    parser.add_argument("--corr", type=float, default=None)
    parser.add_argument("--erp", type=float, default=0.05)
    parser.add_argument("--paths", type=int, default=20_000)
    parser.add_argument("--base-paths", type=int, default=100_000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--battery", action="store_true")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--json", type=Path, default=None, dest="json")
    args = parser.parse_args(argv)

    filing_path = Path(args.filing)

    # Ingest.
    try:
        if args.url:
            data = EdgarClient().get(args.url)
            filing_path.parent.mkdir(parents=True, exist_ok=True)
            filing_path.write_bytes(data)
            filing = read_filing(data)
        else:
            filing = read_filing(filing_path.read_bytes())
    except Exception as error:  # download / read failures are CLI errors here
        print(f"error: could not read filing: {error}", file=sys.stderr)
        return 2

    # Extract + verify quotes.
    try:
        if args.reply:
            reply_text = Path(args.reply).read_text(encoding="utf-8")
            ts = to_termsheet(parse_json_reply(reply_text))
            checks = verify_termsheet(ts, filing.text)
        else:  # --live
            extraction = extract_termsheet(filing)
            ts = extraction.termsheet
            checks = extraction.checks
            if extraction.result is not None:
                print(f"Extraction cost: ${extraction.result.cost_usd:.4f}")
    except ExtractionError as error:
        print(str(error), file=sys.stderr)
        return 2
    except Exception as error:
        # A malformed recorded reply is reported like an extraction failure
        # only when it is an ExtractionError above; other errors in --reply
        # loading (for example a missing file) are also CLI errors.
        if args.reply:
            print(str(error), file=sys.stderr)
            return 2
        raise

    # Supported-payoff gate, before any market-input validation, so a
    # refused note always reports its UnsupportedTermSheet reason.
    try:
        check_supported(ts)
    except UnsupportedTermSheet as error:
        print(str(error), file=sys.stderr)
        return 2

    # Market inputs (user-entered on the command line).
    try:
        vols = _parse_float_list(args.vols, "--vols")
        dividends = _parse_float_list(args.dividends, "--dividends")
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    n = ts.n_underlyings
    if len(vols) != n:
        print(
            f"error: --vols must have {n} entries for {n} underlyings, got {len(vols)}",
            file=sys.stderr,
        )
        return 2
    if len(dividends) != n:
        print(
            f"error: --dividends must have {n} entries for {n} underlyings, "
            f"got {len(dividends)}",
            file=sys.stderr,
        )
        return 2
    if n >= 2:
        if args.corr is None:
            print(
                "error: --corr is required when there are 2 or more underlyings",
                file=sys.stderr,
            )
            return 2
        rho = float(args.corr)
        correlation = tuple(
            tuple(1.0 if i == j else rho for j in range(n)) for i in range(n)
        )
    else:
        correlation = ((1.0,),)

    try:
        market = MarketInputs(
            as_of=ts.pricing_date.value,
            risk_free_rate=float(args.rate),
            vols=tuple(float(v) for v in vols),
            dividend_yields=tuple(float(q) for q in dividends),
            correlation=correlation,
            credit_spread=float(args.spread),
            sources={"all": "user-entered on the command line"},
        )
    except Exception as error:
        print(f"error: invalid market inputs: {error}", file=sys.stderr)
        return 2

    # Price + report.
    try:
        result = analyse(
            ts,
            checks,
            market,
            paths=int(args.paths),
            base_paths=int(args.base_paths),
            seed=int(args.seed),
            erp=float(args.erp),
            battery=bool(args.battery),
            filing_text=filing.text,
        )
    except UnsupportedTermSheet as error:
        print(str(error), file=sys.stderr)
        return 2
    except ExtractionError as error:
        print(str(error), file=sys.stderr)
        return 2

    report = render_report(result)

    if args.out is not None:
        out_path: Path = args.out
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report, encoding="utf-8")
    else:
        print(report)

    if args.json is not None:
        json_path: Path = args.json
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
