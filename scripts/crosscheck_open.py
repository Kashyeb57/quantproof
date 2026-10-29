"""Re-run the audit cross-check with Muse Spark Open (Baseten credits only).

For each saved note that reached the cross-check, replay its recorded
first extraction offline ($0 Meta) and run a fresh second extraction plus
rechecks through Baseten under a hard --budget-usd cap (default 15).
The first extraction is replayed, never re-extracted, so no Meta credits
are spent here.

Updates each result's crosscheck (keeping previous_crosscheck), adds the
Baseten leg cost, and splices the new calls into calls/<acc>.calls.json
(backup .prev-open.json) so scripts/market_audit.py --replay stays
offline-exact. Re-run market_audit_export.py afterwards.

Usage:
    python scripts/crosscheck_open.py [--budget-usd 15] [--only ACC ...]
        [--force] [--out data/market_audit]
"""

from __future__ import annotations

import argparse
import datetime
import functools
import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quantproof.agent import baseten  # noqa: E402
from quantproof.agent.crosscheck import crosscheck  # noqa: E402
from quantproof.agent.extract import (  # noqa: E402
    Extraction,
    parse_json_reply,
    to_termsheet,
)
from quantproof.ingest.html import read_filing  # noqa: E402
from quantproof.report import analysis  # noqa: E402
from quantproof.report.audit_run import FIRST_MODEL, OPEN_MODEL, Recorder  # noqa: E402
from quantproof.report.synth_run import BudgetExceeded, BudgetGuard  # noqa: E402
from quantproof.validation.quotes import verify_termsheet  # noqa: E402

BASETEN_COST_LOG = Path(".cache") / "baseten_calls.jsonl"


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _splice_calls(old_calls: list[dict], new_baseten_calls: list[dict]) -> list[dict]:
    """First-extraction recording plus the fresh Baseten leg, in order.

    Only the FIRST_MODEL extraction call is kept: a stale second
    extraction (same purpose, other model) would break offline replay,
    which matches recorded models in order.
    """
    first = [
        c
        for c in old_calls
        if c.get("purpose") == "extract" and c.get("model") == FIRST_MODEL
    ][:1]
    fresh = [c for c in new_baseten_calls if c.get("model") == OPEN_MODEL]
    return first + fresh


def _first_extraction(calls: list[dict], filing_text: str) -> Extraction | None:
    """Replayed first extraction, or None when the recording has none."""
    extract_calls = [
        c
        for c in calls
        if c.get("purpose") == "extract" and c.get("model") == FIRST_MODEL
    ]
    if not extract_calls:
        return None
    raw = parse_json_reply(extract_calls[0].get("text", ""))
    ts = to_termsheet(raw)
    verify_termsheet(ts, filing_text)
    return Extraction(termsheet=ts, checks=[], result=None, raw=raw)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--budget-usd", type=float, default=15.0)
    parser.add_argument("--only", nargs="+", metavar="ACCESSION")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--out", type=Path, default=Path("data/market_audit"))
    args = parser.parse_args(argv)

    if baseten.api_key() is None:
        print("BASETEN_API_KEY is not set; nothing to do")
        return 2

    out = Path(args.out)
    budget = float(args.budget_usd)
    spent = 0.0
    done = 0
    agreed = 0
    total_diffs = 0
    disagreements: list[str] = []

    for result_path in sorted((out / "results").glob("*.json")):
        old = json.loads(result_path.read_text(encoding="utf-8"))
        meta = old.get("meta") or {}
        accession = str(meta.get("accession") or result_path.stem)
        if args.only and accession not in args.only:
            continue
        if old.get("previous_crosscheck") is not None and not args.force:
            print(f"{accession}: already re-crossed, skipped (use --force)")
            continue
        if not isinstance(old.get("crosscheck"), dict):
            print(f"{accession}: never reached the cross-check, skipped")
            continue
        calls_path = out / "calls" / f"{accession}.calls.json"
        if not calls_path.is_file():
            print(f"{accession}: no recorded calls, skipped")
            continue
        filing = read_filing(
            gzip.decompress((out / "filings" / f"{accession}.htm.gz").read_bytes())
        )
        old_calls = json.loads(calls_path.read_text(encoding="utf-8"))
        try:
            first = _first_extraction(old_calls, filing.text)
        except Exception as exc:  # a bad recording is a skipped note, not a crash
            print(f"{accession}: first-extraction replay failed ({exc}), skipped")
            continue
        if first is None:
            print(f"{accession}: no recorded extraction, skipped")
            continue

        recorder = Recorder(
            functools.partial(baseten.chat, log_path=BASETEN_COST_LOG)
        )
        guard = BudgetGuard(
            recorder,
            budget,
            spent_usd=spent,
            cost_fn=lambda model, usage: baseten.estimate_cost(usage),
        )
        try:
            cc = crosscheck(
                filing,
                first,
                chat=guard,
                second_model=OPEN_MODEL,
                second_chat=guard,
                recheck_model=OPEN_MODEL,
                recheck_chat=guard,
            )
        except BudgetExceeded as exc:
            print(f"{accession}: stopped by the budget guard ({exc})")
            break
        cost = sum(float(c.get("cost_usd", 0.0) or 0.0) for c in recorder.calls)
        spent += cost
        seconds = sum(float(c.get("seconds", 0.0) or 0.0) for c in recorder.calls)
        open_paths = [
            d.path for d in cc.diffs if d.status not in ("resolved_first",)
        ]
        total_diffs += len(cc.diffs)
        if cc.agreed:
            agreed += 1
        else:
            disagreements.append(
                f"{accession} ({old.get('cusip')}: "
                + (", ".join(open_paths[:8]) + ("..." if len(open_paths) > 8 else ""))
                + ")"
            )
        result = dict(old)
        result["previous_crosscheck"] = old["crosscheck"]
        result["crosscheck"] = {
            "second_model": cc.second_model,
            "recheck_model": OPEN_MODEL,
            "agreed": bool(cc.agreed),
            "error": cc.error,
            "diffs": [
                {
                    "path": d.path,
                    "first": analysis._json_value(d.first),
                    "second": analysis._json_value(d.second),
                    "status": d.status,
                }
                for d in cc.diffs
            ],
        }
        result["cost_usd_baseten"] = float(
            old.get("cost_usd_baseten", 0.0) or 0.0
        ) + cost
        result["cost_usd"] = float(old.get("cost_usd", 0.0) or 0.0) + cost
        result["model_seconds"] = float(
            old.get("model_seconds", 0.0) or 0.0
        ) + seconds
        new_calls = _splice_calls(old_calls, recorder.calls)
        prev = calls_path.with_name(f"{accession}.calls.prev-open.json")
        if not prev.exists():
            prev.write_text(json.dumps(old_calls, indent=2), encoding="utf-8")
        _write_json(calls_path, new_calls)
        _write_json(result_path, result)
        done += 1
        print(
            f"{accession}: agreed={cc.agreed} diffs={len(cc.diffs)} "
            f"${cost:.4f} (total ${spent:.2f} of ${budget:.2f})"
        )

    run_path = out / "run.json"
    if run_path.is_file():
        run = json.loads(run_path.read_text(encoding="utf-8"))
        run["crosscheck_open"] = {
            "second_model": OPEN_MODEL,
            "recheck_model": OPEN_MODEL,
            "budget_usd": budget,
            "spent_usd": spent,
            "notes": done,
            "agreed": agreed,
            "finished": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        _write_json(run_path, run)

    print(f"Re-crossed {done} note(s); agreed {agreed}; spent ${spent:.2f}.")
    print(f"Disagreements ({len(disagreements)}):")
    for line in disagreements:
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
