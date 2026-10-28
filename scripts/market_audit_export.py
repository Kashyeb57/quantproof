"""Export the saved market audit to CSV/JSON and a Markdown report.

Reads the saved audit under ``data/market_audit`` (or ``--audit-dir``),
writes ``notes.csv`` and ``notes.json`` (to ``--out-dir``, default the
audit directory) and ``docs/MARKET_AUDIT.md`` (or ``--md``), asserts the
Markdown passes the neutral-language check, and prints the aggregate
numbers.  No network access; every number comes from the saved rows.

Usage:
    python scripts/market_audit_export.py [--audit-dir DIR]
        [--out-dir DIR] [--md PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quantproof.report.market_audit import (  # noqa: E402
    aggregate,
    check_neutral,
    export_dataset,
    load_results,
    load_run,
    note_row,
    render_markdown,
)


def main(argv: list[str] | None = None) -> int:
    """Export the saved audit and print the aggregate numbers."""
    parser = argparse.ArgumentParser(
        description="Export the saved QuantProof market audit."
    )
    parser.add_argument(
        "--audit-dir",
        type=Path,
        default=Path("data/market_audit"),
        help="directory holding run.json and results/*.json",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="directory for notes.csv and notes.json (default: the audit directory)",
    )
    parser.add_argument(
        "--md",
        type=Path,
        default=Path("docs/MARKET_AUDIT.md"),
        help="path for the Markdown report",
    )
    args = parser.parse_args(argv)

    audit_dir: Path = args.audit_dir
    out_dir: Path = args.out_dir if args.out_dir is not None else audit_dir

    results = load_results(audit_dir)
    run = load_run(audit_dir)
    rows = [note_row(result) for result in results]
    agg = aggregate(rows, run)

    csv_path, json_path = export_dataset(results, out_dir)

    # Derived-numbers-only cache: rate, vols and correlation per note
    # with their source labels, so the anchored inputs stay reviewable
    # offline. Raw price series are never written here.
    derived = {}
    for result in results:
        inputs = result.get("market_inputs")
        if isinstance(inputs, dict) and inputs:
            meta = result.get("meta")
            key = str((meta or {}).get("accession") or result.get("accession"))
            derived[key] = inputs
    derived_path = out_dir / "market_inputs.json"
    derived_path.write_text(json.dumps(derived, indent=2, sort_keys=True), encoding="utf-8")

    markdown = render_markdown(rows, agg)
    banned = check_neutral(markdown)
    assert not banned, f"non-neutral words in Markdown report: {banned}"

    md_path: Path = args.md
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(markdown, encoding="utf-8")

    print(f"Notes read: {agg['notes_read']}")
    print(f"Notes extracted: {agg['notes_extracted']}")
    print(f"Notes priced: {agg['n_priced']}")
    print(f"Share priced: {agg['share_priced']}")
    print(f"Notes with a bank estimated value: {agg['n_with_estimate']}")
    print(f"Median cost to investor: {agg['median_cost_to_investor']}")
    print(f"Median cost to investor pct: {agg['median_cost_to_investor_pct']}")
    print(f"Bank estimated value in range: {agg['n_bank_in_range']}")
    print(f"Share bank in range: {agg['share_bank_in_range']}")
    print(f"Quote verification rate: {agg['quote_verification_rate']}")
    print(f"Total cost USD: {agg['total_cost_usd']}")
    print(f"Total model seconds: {agg['total_model_seconds']}")
    print(f"Wall seconds: {agg['wall_seconds']}")
    print(f"Status counts: {agg['status_counts']}")
    print(f"Issuers: {agg['issuers']}")
    print(f"Wrote {csv_path}")
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
