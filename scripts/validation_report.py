"""Build docs/VALIDATION.md and its SVG charts from the filing fixtures.

This is the I/O layer for ``quantproof.validation.report``: it loads the
saved filing fixtures, builds illustrative market inputs, runs the proof
battery, writes charts and the Markdown report, prints a one-line summary
per note, and exits 1 if any non-quotes check failed.

The market inputs below are illustrative assumptions for testing the
engine, not market data and not a valuation (project rules 5 and 8).

Usage: python scripts/validation_report.py [--paths 100000] [--seed 0]
       [--generated YYYY-MM-DD] [--out-dir docs]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quantproof.agent.extract import to_termsheet  # noqa: E402
from quantproof.core.market import MarketInputs  # noqa: E402
from quantproof.ingest.html import read_filing  # noqa: E402
from quantproof.payoffs.worst_of import UnsupportedTermSheet, check_supported  # noqa: E402
from quantproof.registry import store  # noqa: E402
from quantproof.validation.battery import check_quotes, run_battery  # noqa: E402
from quantproof.validation.synth_battery import battery_passed, run_synth_battery  # noqa: E402
from quantproof.validation.report import (  # noqa: E402
    render_markdown,
    svg_convergence,
    svg_decomposition,
    svg_series,
)

# Fixture stem -> illustrative inputs (vols, dividend yields, correlation).
# Correlation for 38582 is the single rho 0.6; for ea0308498 (one asset) the
# 1x1 identity; ea0306095 gets any inputs because it is refused before use.
_SPECS: dict[str, dict] = {
    "jpm_424b2-38582": {
        "vols": (0.24, 0.17),
        "dividend_yields": (0.012, 0.013),
        "correlation": ((1.0, 0.6), (0.6, 1.0)),
    },
    "jpm_424b2-ea0309017": {
        "vols": (0.17, 0.24, 0.21),
        "dividend_yields": (0.013, 0.012, 0.007),
        "correlation": ((1.0, 0.8, 0.9), (0.8, 1.0, 0.75), (0.9, 0.75, 1.0)),
    },
    "jpm_424b2-ea0308498": {
        "vols": (0.35,),
        "dividend_yields": (0.01,),
        "correlation": ((1.0,),),
    },
    "jpm_424b2-ea0306095": {
        "vols": (0.30, 0.35, 0.28),
        "dividend_yields": (0.0, 0.01, 0.012),
        "correlation": ((1.0, 0.5, 0.5), (0.5, 1.0, 0.5), (0.5, 0.5, 1.0)),
    },
}

_SOURCES = {"all": "illustrative inputs for the proof battery, not market data"}


_SYNTH_PATHS_CAP = 20_000  # the registered synth battery ran at 20,000 paths


def _recorded_examples(stem: str, filing) -> list:
    """Filing payout examples for ``stem`` from its recorded live-run replies.

    Offline: replays the saved ``extract_examples`` reply in
    ``tests/fixtures/synthesis/<stem>.calls.json`` through a fake chat (the
    same pattern as tests/registry/test_registered_payoffs.py). Returns []
    when no recorded reply exists; the synth battery then fails its
    filing-examples check for lack of evidence and the note stays refused.
    """
    calls_path = ROOT / "tests" / "fixtures" / "synthesis" / f"{stem}.calls.json"
    if not calls_path.exists():
        return []
    try:
        from types import SimpleNamespace

        from quantproof.agent import synthesis

        records = json.loads(calls_path.read_text(encoding="utf-8"))
        if not isinstance(records, list):
            return []
        text = None
        finish = "stop"
        for record in records:
            if isinstance(record, dict) and record.get("purpose") == "extract_examples":
                if isinstance(record.get("text"), str) and record["text"]:
                    text = record["text"]
                    finish = record.get("finish_reason") or "stop"
                break
        if not text:
            return []

        def fake_chat(messages, **_kwargs):
            return SimpleNamespace(text=text, finish_reason=finish)

        return synthesis.extract_examples(filing, chat=fake_chat)
    except Exception:
        return []


def _market(ts, spec: dict) -> MarketInputs:
    """Illustrative MarketInputs as of the pricing date (rate/spread fixed by the task)."""
    n = ts.n_underlyings
    vols = tuple(spec["vols"][:n]) if len(spec["vols"]) >= n else tuple(spec["vols"]) + (0.20,) * (n - len(spec["vols"]))
    qs = tuple(spec["dividend_yields"][:n]) if len(spec["dividend_yields"]) >= n else tuple(spec["dividend_yields"]) + (0.01,) * (n - len(spec["dividend_yields"]))
    if n == 1:
        corr = ((1.0,),)
    elif len(spec["correlation"]) == n:
        corr = tuple(tuple(float(x) for x in row) for row in spec["correlation"])
    else:
        corr = tuple(tuple(1.0 if i == j else 0.6 for j in range(n)) for i in range(n))
    return MarketInputs(
        as_of=ts.pricing_date.value,
        risk_free_rate=0.037,
        vols=tuple(float(v) for v in vols),
        dividend_yields=tuple(float(q) for q in qs),
        correlation=corr,
        credit_spread=0.006,
        sources=dict(_SOURCES),
    )


def termsheet_summary(ts) -> str:
    """One-paragraph plain-English summary of the extracted terms (task spec)."""
    names = ", ".join(u.name.value for u in ts.underlyings)
    features: list[str] = []
    if ts.autocall is not None:
        features.append(f"autocall on {len(ts.autocall.observations)} dates (trigger {ts.autocall.trigger.value})")
    else:
        features.append("no autocall")
    if ts.coupon is not None:
        features.append(
            f"coupon amount {ts.coupon.amount.value} with memory={ts.coupon.memory.value} "
            f"(barrier {ts.coupon.barrier.value}, {len(ts.coupon.observations)} observations)"
        )
    else:
        features.append("no coupon")
    if ts.upside is not None:
        cap = ts.upside.cap.value if ts.upside.cap is not None else None
        features.append(f"upside leverage {ts.upside.leverage.value} cap {cap}")
    else:
        features.append("no upside")
    d = ts.downside
    features.append(
        f"downside kind {d.kind.value.value} level {d.level.value} observation {d.observation.value.value} gearing {d.gearing.value}"
    )
    text = (
        f"Issuer: {ts.issuer.value}. Underlyings ({ts.n_underlyings}): {names}. "
        f"Features: {'; '.join(features)}. "
        f"Pricing date: {ts.pricing_date.value.isoformat()}; "
        f"maturity date: {ts.maturity_date.value.isoformat()}; "
        f"preliminary: {ts.is_preliminary.value}."
    )
    if ts.estimated_value is not None:
        text += f" bank's estimated value: ${float(ts.estimated_value.value):.2f} (comparison only)."
    return text


def market_summary(market: MarketInputs) -> str:
    """Summarise the illustrative inputs (clearly labelled, not market data)."""
    corr = "; ".join(", ".join(f"{float(x):.2f}" for x in row) for row in market.correlation)
    return (
        f"Illustrative market inputs as of {market.as_of.isoformat()}: risk-free rate "
        f"{market.risk_free_rate}, credit spread {market.credit_spread}, vols {market.vols}, "
        f"dividend yields {market.dividend_yields}, correlation [{corr}]. "
        "Source: illustrative inputs for the proof battery, not market data."
    )


def main(argv: list[str] | None = None) -> int:
    """Run the battery on every fixture, write the report, return the exit code."""
    parser = argparse.ArgumentParser(description="Build the validation report.")
    parser.add_argument("--paths", type=int, default=100_000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--generated", type=str, default=date.today().isoformat())
    parser.add_argument("--out-dir", type=Path, default=Path("docs"))
    args = parser.parse_args(argv)

    out_dir: Path = args.out_dir
    if not out_dir.is_absolute():
        # Relative to the repo root when it looks like the default/docs, but
        # tests pass an absolute tmp dir; a plain relative path is honoured
        # relative to the repo root so the script works from anywhere.
        out_dir = (ROOT / out_dir) if not out_dir.exists() and out_dir.parent == Path(".") else out_dir
    charts_dir = out_dir / "validation"
    charts_dir.mkdir(parents=True, exist_ok=True)

    notes: list[dict] = []
    any_failed = False

    for stem, spec in _SPECS.items():
        extraction_path = ROOT / "tests" / "fixtures" / "extractions" / f"{stem}.reply.json"
        filing_path = ROOT / "tests" / "fixtures" / "filings" / f"{stem}.htm"
        with extraction_path.open(encoding="utf-8") as fh:
            ts = to_termsheet(json.load(fh))
        filing = read_filing(filing_path.read_bytes())
        filing_text = filing.text
        market = _market(ts, spec)

        note: dict = {
            "label": stem,
            "file": stem,
            "termsheet_summary": termsheet_summary(ts),
            "market_summary": market_summary(market),
            "results": None,
            "refused": None,
            "charts": {},
        }
        results = None
        try:
            check_supported(ts)
        except UnsupportedTermSheet as error:
            registered_name = store.find_for(ts)
            synth_results = None
            if registered_name is not None:
                code, _metadata = store.load(registered_name)
                candidate = run_synth_battery(
                    code,
                    ts,
                    market,
                    _recorded_examples(stem, filing),
                    n_paths=min(args.paths, _SYNTH_PATHS_CAP),
                    seed=args.seed,
                    filing_text=filing_text,
                )
                if battery_passed(candidate):
                    synth_results = candidate
            if synth_results is None:
                if registered_name is None:
                    note["refused"] = str(error)
                else:
                    note["refused"] = (
                        f"{error}; registered payoff {registered_name} did not "
                        "pass the re-run synthesized-payoff battery"
                    )
                notes.append(note)
                print(f"{stem}: refused: {note['refused']}")
                continue
            results = synth_results + [check_quotes(ts, filing_text)]
            note["termsheet_summary"] = (
                str(note["termsheet_summary"])
                + f" Priced with the registered agent-written payoff "
                f"{registered_name}, which passed the synthesized-payoff "
                "proof battery in the registry."
            )

        results = results or run_battery(
            ts, market, n_paths=args.paths, seed=args.seed, filing_text=filing_text
        )
        note["results"] = results
        by_name = {r.name: r for r in results}

        def _write_chart(suffix: str, svg: str) -> None:
            filename = f"{stem}_{suffix}.svg"
            (charts_dir / filename).write_text(svg, encoding="utf-8")
            note["charts"][suffix] = f"validation/{filename}"

        if "convergence" in by_name and by_name["convergence"].data.get("rows"):
            _write_chart("convergence", svg_convergence(by_name["convergence"].data["rows"]))
        if "barrier_at_maturity" in by_name and by_name["barrier_at_maturity"].data:
            _write_chart("decomposition", svg_decomposition(by_name["barrier_at_maturity"].data))
        if "correlation_monotonicity" in by_name and by_name["correlation_monotonicity"].data.get("rows"):
            _write_chart(
                "correlation",
                svg_series(
                    by_name["correlation_monotonicity"].data["rows"],
                    "rho",
                    "price",
                    "Correlation monotonicity",
                    "correlation (rho)",
                ),
            )
        if "vol_monotonicity" in by_name and by_name["vol_monotonicity"].data.get("rows"):
            _write_chart(
                "vol",
                svg_series(
                    by_name["vol_monotonicity"].data["rows"],
                    "scale",
                    "price",
                    "Volatility monotonicity",
                    "vol scale",
                ),
            )

        n_passed = sum(1 for r in results if r.passed)
        failed = [r.name for r in results if not r.passed and r.name != "quotes"]
        if failed:
            any_failed = True
        print(f"{stem}: {n_passed}/{len(results)} checks passed; failed (excluding quotes): {failed or 'none'}")
        notes.append(note)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "VALIDATION.md").write_text(
        render_markdown(notes, generated=args.generated), encoding="utf-8"
    )
    return 1 if any_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
