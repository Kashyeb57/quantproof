"""Market audit CLI: the only place with network access for the audit.

Usage:
    python scripts/market_audit.py [--max-notes 45] [--days 60]
        [--budget-usd 12] [--per-major 6] [--per-other 3]
        [--replay] [--out data/market_audit]

Live mode (default) searches SEC EDGAR full text for recent 424B2
pricing supplements across the issuers below, keeps those with a
disclosed estimated value, and runs each through
``quantproof.report.audit_run.audit_note`` (extract with Muse Spark
1.3, verify quotes, cross-check, price where supported).  Every model
reply and every result is saved under the output directory, so the
whole audit replays offline at zero cost with ``--replay`` (same code
path, a ``Replay`` chat instead of the live client).

Budget guard (hard): before each note, the worst-case cost of a full
live run for that filing (``live.estimate_live_cost``: two extractions
plus three re-checks at max output) is added to this run's recorded
spend; if that would exceed ``--budget-usd`` the run stops.  The SEC
User-Agent is read from the environment inside ``EdgarClient`` and is
never printed here.
"""

from __future__ import annotations

import argparse
import datetime
import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quantproof.agent import client as meta_client  # noqa: E402
from quantproof.app import live  # noqa: E402
from quantproof.ingest.edgar import EdgarClient, FilingRef  # noqa: E402
from quantproof.ingest.html import read_filing  # noqa: E402
from quantproof.report.audit_run import (  # noqa: E402
    ASSUMPTIONS,
    Recorder,
    Replay,
    audit_note,
    has_estimated_value,
    is_preliminary_text,
    select_candidates,
)

# Ordered: group -> EDGAR CIK for the EFTS `ciks` filter.  These are the
# parent companies; finance subsidiaries are co-filers and appear in the
# same hits.
ISSUERS: dict[str, int] = {
    "JPMorgan": 19617,
    "Goldman Sachs": 886982,
    "Morgan Stanley": 895421,
    "Citigroup": 831001,
    "Bank of America": 70858,
    "UBS": 1114446,
    "Wells Fargo": 72971,
    "Royal Bank of Canada": 1000275,
    "Bank of Montreal": 927971,
    "Toronto-Dominion": 947263,
    "Bank of Nova Scotia": 9631,
    "HSBC": 83246,
    "CIBC": 1045520,
}
MAJORS: tuple[str, ...] = tuple(list(ISSUERS)[:4])

# Search returns more hits than a group may keep; filters below reject
# some, so candidates are over-fetched and each group's hit list is
# walked until its quota is met or the hits run out.
OVER_FETCH = 10


def is_424b2_document(text: str) -> bool:
    """Whether a filing's header marks it as a Rule 424(b)(2) document.

    The EDGAR index already says the form is 424B2; this checks the text
    agrees.  Wider than the app's ``live.is_pricing_supplement`` (which
    also wants the words "pricing supplement"): some issuers' final terms
    open with "Filed Pursuant to Rule 424(b)(2)" and no such title.
    """
    head = text[:3000].upper()
    return "424B2" in head or "424(B)(2)" in head


def _write_json(path: Path, data: object) -> None:
    """Write ``data`` as indented JSON, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _replay(out: Path) -> int:
    """Re-run every saved result from its recordings; no network, no key.

    For each ``<out>/results/*.json``: load the gzipped filing and the
    recorded calls, re-run ``audit_note`` with a ``Replay`` chat, check
    no recorded call is left unused, keep the recorded ``cost_usd`` /
    ``model_seconds`` from the old result (replay costs $0), and rewrite
    the result file.
    """
    results_dir = out / "results"
    replayed = 0
    for result_path in sorted(results_dir.glob("*.json")):
        old = json.loads(result_path.read_text(encoding="utf-8"))
        if old.get("status") == "api_error":
            continue  # no complete recording to replay; the saved row stands
        meta = old["meta"]
        accession = str(meta["accession"])
        filing_bytes = gzip.decompress(
            (out / "filings" / f"{accession}.htm.gz").read_bytes()
        )
        filing = read_filing(filing_bytes)
        calls = json.loads(
            (out / "calls" / f"{accession}.calls.json").read_text(encoding="utf-8")
        )
        replay = Replay(calls)
        result = audit_note(filing, chat=replay, meta=meta)
        if replay.remaining != 0:
            raise RuntimeError(
                f"replay of {accession}: {replay.remaining} recorded "
                "call(s) left unused"
            )
        result["cost_usd"] = old.get("cost_usd", 0.0)
        result["model_seconds"] = old.get("model_seconds", 0.0)
        _write_json(result_path, result)
        replayed += 1
        print(f"replayed {accession}: {result.get('status')}")
    print(f"Replayed {replayed} note(s); spend $0.00 (replay, no network).")
    return 0


def _live(args: argparse.Namespace, out: Path) -> int:
    """Live audit run: EDGAR search, budget-guarded per-note pipeline."""
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    edgar = EdgarClient()
    today = datetime.date.today()
    start_day = today - datetime.timedelta(days=int(args.days))

    refs_by_issuer: dict[str, list[FilingRef]] = {}
    for group, cik in ISSUERS.items():
        try:
            hits = edgar.search(
                "estimated value", start=start_day, end=today, cik=cik
            )
            # EFTS ranks by relevance; audit the most recent filings first.
            refs_by_issuer[group] = sorted(
                hits, key=lambda ref: ref.filing_date, reverse=True
            )
        except Exception as exc:  # one issuer failing must not stop the audit
            print(f"{group}: search failed: {exc}")
            refs_by_issuer[group] = []

    candidates = select_candidates(
        refs_by_issuer,
        majors=MAJORS,
        per_major=int(args.per_major) * OVER_FETCH,
        per_other=int(args.per_other) * OVER_FETCH,
        max_notes=int(args.max_notes) * OVER_FETCH,
    )

    skipped: list[dict] = []
    kept = 0
    kept_by_group: dict[str, int] = {}
    spent = 0.0
    stopped_reason: str | None = None

    for group, ref in candidates:
        if kept >= int(args.max_notes):
            break
        quota = int(args.per_major) if group in MAJORS else int(args.per_other)
        if kept_by_group.get(group, 0) >= quota:
            continue
        accession = ref.accession
        result_path = out / "results" / f"{accession}.json"

        # Resume: an existing result is not re-run; it counts toward
        # max_notes but its recorded cost is not this run's spend.
        if result_path.exists():
            kept += 1
            kept_by_group[group] = kept_by_group.get(group, 0) + 1
            print(f"{group} {accession}: already done, not re-run")
            continue

        raw = edgar.get(ref.url)
        filing = read_filing(raw)

        # Pre-model filters: these spend nothing.
        reason: str | None = None
        if not is_424b2_document(filing.text):
            reason = "not a 424B2 document"
        elif len(filing.text) > live.max_filing_chars():
            reason = "filing text over the size limit"
        elif is_preliminary_text(filing.text):
            reason = "preliminary pricing supplement"
        elif not has_estimated_value(filing.text):
            reason = "no estimated value disclosed"
        if reason is not None:
            skipped.append(
                {
                    "issuer_group": group,
                    "accession": accession,
                    "url": ref.url,
                    "reason": reason,
                }
            )
            print(f"{group} {accession}: skipped ({reason})")
            continue

        # Budget guard (hard): never start a note whose worst case
        # would take this run over budget.
        worst = live.estimate_live_cost(len(filing.text))
        if spent + worst > float(args.budget_usd):
            stopped_reason = (
                f"budget guard: spent ${spent:.2f} + worst-case "
                f"${worst:.2f} for {accession} would exceed budget "
                f"${float(args.budget_usd):.2f}"
            )
            print(stopped_reason)
            break

        filings_dir = out / "filings"
        filings_dir.mkdir(parents=True, exist_ok=True)
        (filings_dir / f"{accession}.htm.gz").write_bytes(gzip.compress(raw))

        meta = {
            "issuer_group": group,
            "accession": accession,
            "url": ref.url,
            "filing_date": ref.filing_date.isoformat(),
            "form": ref.form,
            "primary_document": ref.primary_document,
        }
        recorder = Recorder(meta_client.chat)
        try:
            result = audit_note(filing, chat=recorder, meta=meta)
        except Exception as exc:  # API failure: record it, keep the audit going
            result = {"meta": meta, "status": "api_error",
                      "reason": f"{type(exc).__name__}: {str(exc)[:400]}"}
        cost = sum(float(call.get("cost_usd", 0.0) or 0.0) for call in recorder.calls)
        result["cost_usd"] = cost
        result["model_seconds"] = sum(
            float(call.get("seconds", 0.0) or 0.0) for call in recorder.calls
        )
        spent += cost
        _write_json(out / "calls" / f"{accession}.calls.json", recorder.calls)
        _write_json(result_path, result)

        kept += 1
        kept_by_group[group] = kept_by_group.get(group, 0) + 1
        print(f"{group} {result.get('cusip')} {result.get('status')} ${cost:.4f}")

    _write_json(out / "skipped.json", skipped)
    run = {
        "started": started,
        "finished": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "days": int(args.days),
        "max_notes": int(args.max_notes),
        "budget_usd": float(args.budget_usd),
        "spent_usd": spent,
        "issuers": ISSUERS,
        "assumptions": ASSUMPTIONS,
        "kept": kept,
        "skipped": len(skipped),
        "stopped_reason": stopped_reason,
    }
    _write_json(out / "run.json", run)
    print(
        f"Done: kept {kept}, skipped {len(skipped)}, "
        f"spent ${spent:.2f} of ${float(args.budget_usd):.2f} budget."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run the live audit or the offline replay."""
    parser = argparse.ArgumentParser(
        description="QuantProof market audit over recent 424B2 pricing supplements."
    )
    parser.add_argument("--max-notes", type=int, default=45)
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--budget-usd", type=float, default=12)
    parser.add_argument("--per-major", type=int, default=6)
    parser.add_argument("--per-other", type=int, default=3)
    parser.add_argument("--replay", action="store_true",
                        help="replay saved recordings offline at zero cost")
    parser.add_argument("--out", type=Path, default=Path("data/market_audit"))
    args = parser.parse_args(argv)

    out = Path(args.out)
    if args.replay:
        return _replay(out)
    return _live(args, out)


if __name__ == "__main__":
    raise SystemExit(main())
