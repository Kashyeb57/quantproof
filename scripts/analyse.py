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
from quantproof.report.analysis import _clean_cell, analyse, render_report  # noqa: E402,F401
from quantproof.validation.quotes import verify_termsheet  # noqa: E402

from quantproof.ingest.edgar import EdgarClient  # noqa: E402


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
