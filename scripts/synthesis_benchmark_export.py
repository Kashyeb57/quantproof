"""Export the synthesis-benchmark report from saved results (offline).

Reads only the saved benchmark under ``data/synthesis_benchmark/`` and
the saved market audit under ``data/market_audit/``, renders
``docs/SYNTHESIS_BENCHMARK.md``, asserts the rendered text is neutral
(``quantproof.report.market_audit.check_neutral``), and prints the
summary.  No network calls and no API calls: every number in the report
comes from the saved rows.

Usage: ``python scripts/synthesis_benchmark_export.py [--md PATH]``
"""

from __future__ import annotations

import argparse
import re
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quantproof.report import market_audit, synthesis_benchmark

DEFAULT_MD = ROOT / "docs" / "SYNTHESIS_BENCHMARK.md"
BENCH_DIR = ROOT / "data" / "synthesis_benchmark"
AUDIT_DIR = ROOT / "data" / "market_audit"


def main(argv: list[str] | None = None) -> int:
    """Render the report, check neutrality, write it, print the summary."""
    parser = argparse.ArgumentParser(
        description="Export the synthesis benchmark report from saved results."
    )
    parser.add_argument(
        "--md",
        nargs="?",
        const=DEFAULT_MD,
        default=DEFAULT_MD,
        type=Path,
        help="Output markdown path (default: docs/SYNTHESIS_BENCHMARK.md).",
    )
    parser.add_argument("--bench-dir", type=Path, default=BENCH_DIR)
    parser.add_argument("--audit-dir", type=Path, default=AUDIT_DIR)
    args = parser.parse_args(argv)

    results = synthesis_benchmark.load_results(args.bench_dir)
    audit_rows = [
        market_audit.note_row(result)
        for result in market_audit.load_results(args.audit_dir)
    ]
    summary = synthesis_benchmark.summarise(results, audit_rows)
    markdown = synthesis_benchmark.render_markdown(results, summary)

    # Prose only: AI-written code in ``` blocks may name variables "worst"
    # (the worst-of level), which is not language about a bank.
    prose = re.sub(r"```.*?```", "", markdown, flags=re.DOTALL)
    banned = market_audit.check_neutral(prose)
    assert not banned, f"banned words in synthesis benchmark report: {banned}"

    md_path = Path(args.md)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(markdown, encoding="utf-8")

    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    print(f"Wrote {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
