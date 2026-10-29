"""Synthesis benchmark: AI-written payoffs for real 424B2s, proved or rejected.

Templates are DISABLED for synthesis: for every note the selected model
(--model wonder, the default, or open) writes the whole payoff, the sandbox
runs it, the proof battery checks it, and failed
checks (with a concrete counterexample) go back for up to 3 repairs.  Where
a template covers the note, its price is compared afterwards (within 3 SE)
and the template is also checked against the filing's examples.

Usage:
    python scripts/synthesis_benchmark.py --budget-usd 15                  # live, wonder (Meta credits)
    python scripts/synthesis_benchmark.py --budget-usd 15 --model open    # live, Muse Spark Open (Baseten credits)
    python scripts/synthesis_benchmark.py --replay                         # offline, $0

Inputs are the market audit's saved filings and extraction recordings
(data/market_audit); outputs go to data/synthesis_benchmark/.
"""

from __future__ import annotations

import argparse
import functools
import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quantproof.agent import client as meta_client  # noqa: E402
from quantproof.agent import baseten as baseten_client  # noqa: E402
from quantproof.ingest.html import read_filing  # noqa: E402
from quantproof.report import synth_run  # noqa: E402

AUDIT_DIR = Path("data/market_audit")
OUT_DIR = Path("data/synthesis_benchmark")
COST_LOG = Path(".cache") / "synthesis_calls.jsonl"  # apart from the app's cost log

# (accession, short structure label, template covers it?)
NOTES: list[tuple[str, str, bool]] = [
    ("0001918704-26-032448", "contingent digital return, 70% barrier", False),
    ("0001839882-26-051339", "digital return, 85% buffer", False),
    ("0001918704-26-032694", "absolute (dual-directional) return, capped", False),
    ("0001214659-26-013104", "threshold settlement amount, 85% buffer", False),
    ("0001140361-26-040730", "contingent absolute return", False),
    ("0001839882-26-049404", "minimum payment at maturity", False),
    ("0001193125-26-427716", "bearish (inverse) exposure", False),
    ("0001839882-26-052733", "principal protection, leveraged upside", False),
    ("0001213900-26-112047", "capped 1x upside with buffer (template)", True),
    ("0001193125-26-426561", "3x leveraged capped upside (template)", True),
]


# Untried unsupported notes (second round): every audit note with status
# "unsupported" that is not in NOTES above. All single-underlying except
# the worst-of-5 UBS note. Template-covered: none, so the battery alone
# decides. These run with min_examples=3: the battery needs at least 3
# worked examples as evidence, so poorer notes skip before Wonder spend.
NEW_NOTES: list[tuple[str, str]] = [
    ("0001918704-26-032658", "absolute return buffer, capped"),
    ("0001839882-26-052340", "digital return, 85% buffer"),
    ("0001839882-26-052363", "absolute reference return, capped"),
    ("0000950103-26-015439", "modified inverse exposure, capped"),
    ("0001918704-26-028367", "absolute return amount, buffer"),
    ("0001918704-26-028961", "digital return, 60% barrier"),
    ("0001918704-26-029389", "principal protection, capped upside"),
    ("0001193125-26-424207", "company redemption right, buffer"),
    ("0001193125-26-432094", "company redemption right, par barrier"),
    ("0001193125-26-432141", "absolute decline payout, autocall"),
    ("0001918704-26-031826", "contingent digital return, buffer"),
    ("0001918704-26-032189", "contingent digital return, buffer"),
    ("0001918704-26-032730", "contingent digital return, 90% barrier"),
    ("0001839882-26-049277", "excess-return index, leveraged upside"),
    ("0001839882-26-049407", "upside payment, 75% barrier"),
    ("0001839882-26-051777", "principal protection, excess-return index"),
    ("0001140361-26-040726", "threshold settlement, 87.5% buffer"),
    ("0001839882-26-052105", "maximum settlement, 85% buffer"),
    ("0001839882-26-052237", "threshold settlement, worst-of-5 buffer"),
    ("0001839882-26-052472", "threshold settlement, 85% buffer"),
    ("0001839882-26-052586", "principal protection, capped upside"),
]
NEW_MIN_EXAMPLES = 3


def _open_chat(messages, **kw):
    """Baseten chat for synthesis: Meta-only kwargs are dropped.

    ``synthesise_payoff`` passes ``reasoning_effort``, which only the
    Meta client takes; the Baseten client always returns the final
    answer (``message.content``), never the reasoning trace.
    """
    kw.pop("reasoning_effort", None)
    return baseten_client.chat(messages, **kw)


def _model_config(model: str) -> dict:
    """Chat backend, model ids and cost function for ``--model``.

    ``wonder`` (default) synthesises with Muse Wonder via the Meta API;
    ``open`` synthesises with Muse Spark Open via Baseten (key from
    ``BASETEN_API_KEY``) and logs its spend apart from Meta spend.
    """
    if model == "open":
        return {
            "synth_model": baseten_client.OPEN_MODEL,
            "examples_model": baseten_client.OPEN_MODEL,
            "chat": _open_chat,
            "log_path": baseten_client.DEFAULT_LOG,
            "cost_fn": lambda name, usage: baseten_client.estimate_cost(usage),
        }
    if model == "wonder":
        return {
            "synth_model": synth_run.SYNTH_MODEL,
            "examples_model": "muse-spark-1.3",
            "chat": meta_client.chat,
            "log_path": COST_LOG,
            "cost_fn": None,
        }
    raise ValueError(f"unknown --model {model!r}: 'wonder' or 'open'")


def _load(accession: str):
    filing = read_filing(gzip.decompress((AUDIT_DIR / "filings" / f"{accession}.htm.gz").read_bytes()))
    audit_calls = json.loads((AUDIT_DIR / "calls" / f"{accession}.calls.json").read_text(encoding="utf-8"))
    audit_result = json.loads((AUDIT_DIR / "results" / f"{accession}.json").read_text(encoding="utf-8"))
    return filing, audit_calls, audit_result


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def _meta(accession: str, label: str, covered: bool, audit_result: dict, min_examples: int = 0,
          model: str = synth_run.SYNTH_MODEL) -> dict:
    return {**audit_result["meta"], "label": label, "template_covered": covered,
            "audit_status": audit_result.get("status"),
            "min_examples": min_examples, "model": model}


def _plan() -> list:
    """(accession, label, template_covered, min_examples) for every note."""
    planned = [(acc, label, covered, 0) for acc, label, covered in NOTES]
    planned += [(acc, label, False, NEW_MIN_EXAMPLES) for acc, label in NEW_NOTES]
    return planned


def live(out: Path, budget_usd: float, only: list[str] | None, model: str = "wonder") -> int:
    """Run each note live under a hard budget; resume skips finished notes."""
    cfg = _model_config(model)
    # The budget covers this model's own spend: earlier results from the
    # other backend do not eat into it (Baseten and Meta bill apart).
    spent = 0.0
    for _saved_path in (out / "results").glob("*.json"):
        _saved = json.loads(_saved_path.read_text(encoding="utf-8"))
        if _saved.get("model", synth_run.SYNTH_MODEL) == cfg["synth_model"]:
            spent += float(_saved.get("cost_usd", 0.0))
    for accession, label, covered, min_examples in _plan():
        if only and accession not in only:
            continue
        result_path = out / "results" / f"{accession}.json"
        if result_path.exists():
            print(f"{accession}: already done, not re-run")
            continue
        filing, audit_calls, audit_result = _load(accession)
        recorder = synth_run.ErrorRecorder(functools.partial(cfg["chat"], log_path=cfg["log_path"]))
        guard = synth_run.BudgetGuard(recorder, budget_usd, spent_usd=spent, cost_fn=cfg["cost_fn"])
        try:
            result = synth_run.run_note(
                filing, audit_calls, _meta(accession, label, covered, audit_result, min_examples, cfg["synth_model"]),
                chat=guard, template_covered=covered, min_examples=min_examples,
                synth_model=cfg["synth_model"], examples_model=cfg["examples_model"])
        except synth_run.BudgetExceeded as exc:
            spent += sum(float(c.get("cost_usd", 0.0) or 0.0) for c in recorder.calls)
            _write_json(out / "partial" / f"{accession}.calls.json", recorder.calls)
            print(f"{accession}: stopped by the budget guard ({exc}); spent ${spent:.2f}")
            break
        except Exception:
            # Keep what was paid for, then stop: a crash is a bug to fix, not a result.
            _write_json(out / "partial" / f"{accession}.calls.json", recorder.calls)
            raise
        cost = sum(float(c.get("cost_usd", 0.0) or 0.0) for c in recorder.calls)
        spent += cost
        result["cost_usd"] = cost
        result["model_seconds"] = sum(float(c.get("seconds", 0.0) or 0.0) for c in recorder.calls)
        synth_run.fill_attempt_costs(result, recorder.calls)
        _write_json(out / "calls" / f"{accession}.calls.json", recorder.calls)
        _write_json(result_path, result)
        print(f"{accession} {label}: {result['status']} after {result['n_attempts']} "
              f"attempt(s), ${cost:.2f} (total ${spent:.2f} of ${budget_usd:.2f})")
    print(f"Spent ${spent:.2f} of ${budget_usd:.2f}.")
    return 0


def replay(out: Path) -> int:
    """Re-run every saved note from its recordings: no network, $0."""
    covered_by = {acc: (label, covered, min_examples) for acc, label, covered, min_examples in _plan()}
    for result_path in sorted((out / "results").glob("*.json")):
        old = json.loads(result_path.read_text(encoding="utf-8"))
        accession = old["meta"]["accession"]
        label, covered, min_examples = covered_by[accession]
        filing, audit_calls, audit_result = _load(accession)
        calls = json.loads((out / "calls" / f"{accession}.calls.json").read_text(encoding="utf-8"))
        chat = synth_run.ErrorReplay(calls)
        saved_model = old.get("model", synth_run.SYNTH_MODEL)
        saved_examples = old.get("examples_model", "muse-spark-1.3")
        result = synth_run.run_note(filing, audit_calls, _meta(accession, label, covered, audit_result, min_examples, saved_model),
                                    chat=chat, template_covered=covered, min_examples=min_examples,
                                    synth_model=saved_model, examples_model=saved_examples)
        bad = [a["error"] for a in result["attempts"] if a.get("error") and "ReplayError" in a["error"]]
        if chat.remaining or bad:
            raise RuntimeError(f"replay of {accession} diverged: {chat.remaining} unused call(s) {bad}")
        result["cost_usd"] = old.get("cost_usd", 0.0)
        result["model_seconds"] = old.get("model_seconds", 0.0)
        synth_run.fill_attempt_costs(result, calls)
        _write_json(result_path, result)
        print(f"replayed {accession}: {result['status']}")
    print("Replay spent $0.00 (no network).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--budget-usd", type=float, default=15.0)
    parser.add_argument("--replay", action="store_true")
    parser.add_argument("--model", choices=("wonder", "open"), default="wonder",
                        help="'wonder' (default, Meta API) or 'open' (Muse Spark Open via Baseten)")
    parser.add_argument("--only", nargs="+", metavar="ACCESSION")
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args(argv)
    if args.replay:
        return replay(args.out)
    return live(args.out, args.budget_usd, args.only, args.model)


if __name__ == "__main__":
    raise SystemExit(main())
