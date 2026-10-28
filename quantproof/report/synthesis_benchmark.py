"""Summarise saved synthesis-benchmark results into aggregates and reports.

This module is pure Python (standard library only).  It has no network
access and performs no pricing maths of its own: every number it outputs
is computed only from the saved rows under
``data/synthesis_benchmark/results/`` written by
``quantproof.report.synth_run.run_note`` and from the saved market-audit
rows under ``data/market_audit/`` (see
``quantproof.report.market_audit.load_results`` / ``note_row``).

Each benchmark result dict has ``meta`` (accession, issuer_group, label,
template_covered, audit_status), ``cusip``, ``structure``,
``unsupported_features``, ``issue_price``, ``estimated_value``,
``examples``, ``attempts`` (each with code, passed, error,
feedback_sent, counterexamples, cost_usd, seconds and checks),
``passed``, ``n_attempts``, ``repaired``, ``first_failing_checks``,
``status`` ("priced_ai" | "needs_review" | "synthesis_failed"),
``cost_usd`` and ``model_seconds``; when passed it also has
``fair_value`` and ``gate``, and when template-covered and passed it also
has ``template`` and ``template_examples``.

Language: neutral and factual, per AGENTS.md rule 8.  The bank's
estimated value is shown alongside our estimated value range.  This is a
risk analysis under stated assumptions, not investment advice.

Reference: AGENTS.md rules 3, 5 and 8; project brief section 6;
``quantproof.report.synth_run`` for the exact saved dict shape.
"""

from __future__ import annotations

import difflib

import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

_NOT_ADVICE_SENTENCE = (
    "This is a risk analysis under stated assumptions, not investment advice."
)


def _meta(result: dict) -> dict:
    """Return the result's ``meta`` dict, or an empty dict."""
    meta = result.get("meta")
    return meta if isinstance(meta, dict) else {}


def _accession(result: dict) -> str:
    """Accession for sorting: ``meta.accession``, else top level, else ''."""
    meta = _meta(result)
    value = meta.get("accession")
    if value is None:
        value = result.get("accession")
    return str(value) if value is not None else ""


def _n_attempts(result: dict) -> int:
    """Number of attempts, preferring the saved ``n_attempts`` field."""
    n = result.get("n_attempts")
    if n is not None:
        return int(n)
    attempts = result.get("attempts")
    return len(attempts) if isinstance(attempts, list) else 0


def _attempts(result: dict) -> list[dict]:
    """The result's attempt dicts (non-dict entries dropped)."""
    attempts = result.get("attempts")
    if not isinstance(attempts, list):
        return []
    return [a for a in attempts if isinstance(a, dict)]


def _checks(attempt: dict) -> list[dict]:
    """The attempt's check dicts (non-dict entries dropped)."""
    checks = attempt.get("checks")
    if not isinstance(checks, list):
        return []
    return [c for c in checks if isinstance(c, dict)]


def _is_template_covered(result: dict) -> bool:
    """True when ``meta.template_covered`` says a template covers the note."""
    return bool(_meta(result).get("template_covered"))


def _is_repaired(result: dict) -> bool:
    """True when the note passed after more than one attempt."""
    if result.get("repaired") is not None:
        return bool(result.get("repaired"))
    return bool(result.get("passed")) and _n_attempts(result) > 1


def _result_cost(result: dict) -> float:
    """Top-level ``cost_usd`` if present, else the sum over attempts."""
    if result.get("cost_usd") is not None:
        return float(result["cost_usd"])
    return float(
        sum(float(a.get("cost_usd") or 0.0) for a in _attempts(result))
    )


def _result_seconds(result: dict) -> float:
    """Top-level ``model_seconds`` if present, else sum over attempts."""
    if result.get("model_seconds") is not None:
        return float(result["model_seconds"])
    if result.get("seconds") is not None:
        return float(result["seconds"])
    return float(
        sum(float(a.get("seconds") or 0.0) for a in _attempts(result))
    )


def load_results(bench_dir: Path = Path("data/synthesis_benchmark")) -> list[dict]:
    """Load every ``results/*.json`` under ``bench_dir``, sorted by accession.

    A missing ``results`` directory gives an empty list.  No network, no
    pricing: the saved JSON dicts are returned exactly as written.
    """
    results_dir = Path(bench_dir) / "results"
    loaded: list[dict] = []
    if results_dir.is_dir():
        for path in sorted(results_dir.glob("*.json")):
            with path.open(encoding="utf-8") as handle:
                loaded.append(json.load(handle))
    return sorted(loaded, key=_accession)


def _passing_attempt(result: dict) -> dict | None:
    """The first attempt with ``passed`` true, or None."""
    for attempt in _attempts(result):
        if attempt.get("passed"):
            return attempt
    return None


def _check_passed(attempt: dict | None, name: str) -> bool | None:
    """Whether check ``name`` passed in ``attempt``; None if not present."""
    if attempt is None:
        return None
    for check in _checks(attempt):
        if check.get("name") == name:
            return bool(check.get("passed"))
    return None


def summarise(results: list[dict], audit_rows: list[dict]) -> dict:
    """Aggregate benchmark results and audit rows into summary numbers.

    Every number is computed only from the given saved rows.  ``catches``
    counts, per check name, the failed attempts in which that check
    failed and the notes where that check failed at least once and the
    note was later repaired.  ``coverage`` compares the audit's template
    coverage with the benchmark's proven AI-written payoffs.
    ``generalisation`` has one row per template-covered benchmark note.
    """
    results = list(results or [])
    audit_rows = list(audit_rows or [])

    n_notes = len(results)
    n_passed = sum(1 for r in results if r.get("passed"))
    attempts_per_note = [_n_attempts(r) for r in results]

    catches_counter: Counter = Counter()
    repaired_counter: Counter = Counter()
    for result in results:
        failed_names_in_note: set[str] = set()
        for attempt in _attempts(result):
            if attempt.get("passed"):
                continue
            for check in _checks(attempt):
                if not check.get("passed") and check.get("name") is not None:
                    name = str(check["name"])
                    catches_counter[name] += 1
                    failed_names_in_note.add(name)
        if _is_repaired(result):
            for name in failed_names_in_note:
                repaired_counter[name] += 1
    catches = {
        name: {
            "failed_attempts": int(catches_counter[name]),
            "repaired_notes": int(repaired_counter.get(name, 0)),
        }
        for name in sorted(catches_counter)
    }

    n_extracted = sum(
        1
        for row in audit_rows
        if row.get("status") not in ("extraction_failed", "api_error")
    )
    n_template = sum(1 for row in audit_rows if row.get("status") == "priced")
    n_ai = n_template + sum(
        1
        for r in results
        if not _is_template_covered(r) and r.get("status") == "priced_ai"
    )
    coverage = {
        "n_extracted": int(n_extracted),
        "n_template": int(n_template),
        "n_ai": int(n_ai),
        "n_ai_needs_review": int(
            sum(
                1
                for r in results
                if not _is_template_covered(r)
                and r.get("status") == "needs_review"
            )
        ),
        "n_unsupported_total": int(
            sum(1 for row in audit_rows if row.get("status") == "unsupported")
        ),
        "n_unsupported_tried": int(
            sum(1 for r in results if not _is_template_covered(r))
        ),
    }

    generalisation: list[dict] = []
    for result in results:
        if not _is_template_covered(result):
            continue
        template = result.get("template")
        if not isinstance(template, dict):
            template = {}
        template_examples = result.get("template_examples")
        if not isinstance(template_examples, dict):
            template_examples = {}
        generalisation.append(
            {
                "cusip": result.get("cusip"),
                "passed": bool(result.get("passed")),
                "diff": template.get("diff"),
                "combined_se": template.get("combined_se"),
                "within_3se": template.get("within_3se"),
                "ai_examples_passed": _check_passed(
                    _passing_attempt(result), "filing_examples"
                ),
                "template_examples_passed": template_examples.get("passed"),
                "template_examples_max_error": template_examples.get("max_error"),
            }
        )

    return {
        "n_notes": int(n_notes),
        "n_passed": int(n_passed),
        "pass_rate": (n_passed / n_notes) if n_notes else None,
        "attempts_per_note": attempts_per_note,
        "mean_attempts": (
            float(statistics.mean(attempts_per_note)) if attempts_per_note else None
        ),
        "n_first_try": int(
            sum(
                1
                for r in results
                if r.get("passed") and _n_attempts(r) == 1
            )
        ),
        "n_repaired": int(sum(1 for r in results if _is_repaired(r))),
        "n_failed": int(n_notes - n_passed),
        "total_cost_usd": float(sum(_result_cost(r) for r in results)),
        "total_model_seconds": float(sum(_result_seconds(r) for r in results)),
        "status_counts": dict(
            sorted(Counter(str(r.get("status")) for r in results).items())
        ),
        "catches": catches,
        "coverage": coverage,
        "generalisation": generalisation,
    }


def _counterexample_text(attempt: dict) -> str:
    """An attempt's counterexamples joined into one string."""
    counterexamples = attempt.get("counterexamples")
    if isinstance(counterexamples, dict):
        parts = [
            str(counterexamples[key])
            for key in sorted(counterexamples, key=str)
            if counterexamples[key]
        ]
        return "\n\n".join(parts)
    if counterexamples:
        return str(counterexamples)
    return ""


def showcase(results: list[dict]) -> dict | None:
    """The caught-bug worked example, or None when no note was repaired.

    The first (by accession) repaired note whose attempt 1 failed the
    "filing_examples" check; if there is no such note, the first repaired
    note by accession.  ``attempts`` carries every saved attempt dict for
    the app replay.
    """
    ordered = sorted(list(results or []), key=_accession)
    repaired = [r for r in ordered if _is_repaired(r) and _attempts(r)]

    def _attempt1_failed_filing_examples(result: dict) -> bool:
        first = _attempts(result)[0]
        return any(
            check.get("name") == "filing_examples" and not check.get("passed")
            for check in _checks(first)
        )

    chosen = None
    for result in repaired:
        if _attempt1_failed_filing_examples(result):
            chosen = result
            break
    if chosen is None and repaired:
        chosen = repaired[0]
    if chosen is None:
        return None

    attempts = _attempts(chosen)
    first = attempts[0]
    passing = _passing_attempt(chosen)
    failed_checks = [
        {"name": check.get("name"), "detail": check.get("detail")}
        for check in _checks(first)
        if not check.get("passed")
    ]
    feedback = None
    if len(attempts) > 1:
        feedback = attempts[1].get("feedback_sent")
    return {
        "cusip": chosen.get("cusip"),
        "structure": chosen.get("structure"),
        "label": _meta(chosen).get("label"),
        "wrong_code": first.get("code"),
        "failed_checks": failed_checks,
        "counterexample": _counterexample_text(first),
        "feedback": feedback,
        "fixed_code": passing.get("code") if passing is not None else None,
        "n_attempts": _n_attempts(chosen),
        "attempts": attempts,
    }


def _escape_cell(text: object) -> str:
    """Escape pipe characters so a cell cannot break the table layout."""
    return str(text).replace("|", "\\|")


def _fmt(value: Any, digits: int = 2) -> str:
    """Format a number with ``digits`` decimals; '' when missing."""
    if value is None:
        return ""
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def _fmt_bool(value: Any) -> str:
    """Format a tri-state boolean as yes/no; '' when missing."""
    if value is None:
        return ""
    return "yes" if bool(value) else "no"


def render_markdown(results: list[dict], summary: dict) -> str:
    """Render the synthesis-benchmark report from saved results only.

    ``summary`` is :func:`summarise` over the same results and the audit
    rows.  The bank's estimated value is shown alongside our estimated
    value for every note.  Neutral language only (rule 8).
    """
    results = list(results or [])
    coverage = summary.get("coverage") or {}

    check_names = sorted(
        {
            str(check["name"])
            for result in results
            for attempt in _attempts(result)
            for check in _checks(attempt)
            if check.get("name") is not None
        }
    )

    lines: list[str] = []
    lines.append("# Synthesis benchmark")
    lines.append("")
    lines.append("## Method")
    lines.append("")
    lines.append(
        "Templates are disabled for synthesis: for every note in this "
        "benchmark, Muse Wonder 1.0 writes the full payoff code from the "
        "filing text and the extracted terms.  The generated code is "
        "untrusted: it runs only in a subprocess sandbox with a timeout, "
        "no network, and an import allow-list (numpy, scipy, math)."
    )
    lines.append("")
    if check_names:
        lines.append(
            "The proof battery checks are: " + ", ".join(check_names) + "."
        )
        lines.append("")
    lines.append(
        "A failed attempt gets up to 3 repairs.  Each repair is fed the "
        "failing check name and a concrete counterexample produced by "
        "the battery, so the model sees the exact input on which its "
        "code disagreed with the filing or the stated payoff rules."
    )
    lines.append("")
    lines.append(
        "Pricing uses stated assumptions, not market data as of each "
        "pricing date.  Volatility is a flat vol under those stated "
        "assumptions and is labelled as such; no volatility skew is "
        "fitted.  Synthesized ranges exclude skew (the saved fair_value "
        "records this as \"skew\": \"not included\"), so synthesized "
        "ranges are narrower than template ranges on that point alone.  "
        "Fair value is reported as a range under the stated assumptions.  "
        "The bank's estimated value is a comparison point only and is "
        "never a calibration target."
    )
    lines.append("")
    lines.append("## Coverage")
    lines.append("")
    # This sentence is quoted verbatim by the tests and the app copy.
    lines.append(
        f"Templates priced {coverage.get('n_template')} of "
        f"{coverage.get('n_extracted')} extracted notes; with proven "
        f"AI-written payoffs, {coverage.get('n_ai')} of "
        f"{coverage.get('n_extracted')} ({coverage.get('n_unsupported_tried')} "
        f"of the {coverage.get('n_unsupported_total')} unsupported notes "
        f"were attempted)."
    )
    lines.append("")
    lines.append("## Results")
    lines.append("")
    pass_rate = summary.get("pass_rate")
    lines.append(
        f"- Notes attempted: {summary.get('n_notes')}"
    )
    lines.append(f"- Notes passed: {summary.get('n_passed')}")
    lines.append(
        "- Pass rate: "
        + ("" if pass_rate is None else f"{float(pass_rate) * 100:.1f}%")
    )
    lines.append(f"- Passed on the first attempt: {summary.get('n_first_try')}")
    lines.append(f"- Repaired after a failed attempt: {summary.get('n_repaired')}")
    lines.append(f"- Failed: {summary.get('n_failed')}")
    mean_attempts = summary.get("mean_attempts")
    lines.append(
        "- Mean attempts per note: "
        + ("" if mean_attempts is None else f"{float(mean_attempts):.2f}")
    )
    lines.append(
        f"- Total model cost (USD): {_fmt(summary.get('total_cost_usd'), 4)}"
    )
    lines.append(
        f"- Total model seconds: {_fmt(summary.get('total_model_seconds'), 1)}"
    )
    status_counts = summary.get("status_counts") or {}
    if status_counts:
        counts_text = ", ".join(
            f"{status}: {count}" for status, count in sorted(status_counts.items())
        )
        lines.append(f"- Status counts: {counts_text}")
    lines.append("")
    lines.append("## Attempts per note")
    lines.append("")
    headers = [
        "CUSIP",
        "Structure",
        "Attempts",
        "First failing checks",
        "Status",
        "Cost ($)",
        "Time (s)",
        "Base value ± SE ($)",
        "Range ($)",
        "Bank estimate ($)",
    ]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for result in sorted(results, key=_accession):
        fair_value = result.get("fair_value")
        if not isinstance(fair_value, dict):
            fair_value = {}
        base = fair_value.get("base")
        if not isinstance(base, dict):
            base = {}
        if base.get("price") is not None:
            base_text = f"{_fmt(base.get('price'))} ± {_fmt(base.get('std_error'))}"
        else:
            base_text = ""
        if fair_value.get("low") is not None and fair_value.get("high") is not None:
            range_text = f"{_fmt(fair_value.get('low'))} - {_fmt(fair_value.get('high'))}"
        else:
            range_text = ""
        first_failing = result.get("first_failing_checks")
        if isinstance(first_failing, list):
            first_failing_text = ", ".join(str(x) for x in first_failing)
        elif first_failing is not None:
            first_failing_text = str(first_failing)
        else:
            first_failing_text = ""
        cells = [
            _escape_cell(result.get("cusip") or ""),
            _escape_cell(result.get("structure") or ""),
            _escape_cell(_n_attempts(result)),
            _escape_cell(first_failing_text),
            _escape_cell(result.get("status") or ""),
            _escape_cell(_fmt(_result_cost(result), 4)),
            _escape_cell(_fmt(_result_seconds(result), 1)),
            _escape_cell(base_text),
            _escape_cell(range_text),
            _escape_cell(_fmt(result.get("estimated_value"))),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("## What the battery caught")
    lines.append("")
    catch_headers = ["Check", "Failed attempts", "Repaired notes"]
    lines.append("| " + " | ".join(catch_headers) + " |")
    lines.append("| " + " | ".join("---" for _ in catch_headers) + " |")
    catches = summary.get("catches") or {}
    for name in sorted(catches):
        entry = catches[name] or {}
        cells = [
            _escape_cell(name),
            _escape_cell(entry.get("failed_attempts", "")),
            _escape_cell(entry.get("repaired_notes", "")),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("## Generalisation to template-covered notes")
    lines.append("")
    lines.append(
        "For notes a template can also price, the AI-written payoff is "
        "compared with the template on the same stated market inputs.  "
        "The difference is shown with the combined standard error of "
        "the two Monte Carlo estimates; agreement within 3 combined "
        "standard errors is the stated tolerance (3 standard errors)."
    )
    lines.append("")
    gen_headers = [
        "CUSIP",
        "Passed",
        "Diff ($)",
        "Combined SE ($)",
        "Within 3 SE",
        "AI examples passed",
        "Template examples passed",
        "Template examples max error",
    ]
    lines.append("| " + " | ".join(gen_headers) + " |")
    lines.append("| " + " | ".join("---" for _ in gen_headers) + " |")
    for row in summary.get("generalisation") or []:
        cells = [
            _escape_cell(row.get("cusip") or ""),
            _escape_cell(_fmt_bool(row.get("passed"))),
            _escape_cell(_fmt(row.get("diff"))),
            _escape_cell(_fmt(row.get("combined_se"))),
            _escape_cell(_fmt_bool(row.get("within_3se"))),
            _escape_cell(_fmt_bool(row.get("ai_examples_passed"))),
            _escape_cell(_fmt_bool(row.get("template_examples_passed"))),
            _escape_cell(_fmt(row.get("template_examples_max_error"), 4)),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("## Worked example: a caught error and its repair")
    lines.append("")
    example = showcase(results)
    if example is None:
        lines.append(
            "No repaired note is in the saved benchmark results, so there "
            "is no worked example to show."
        )
        lines.append("")
    else:
        lines.append(
            f"Note {_escape_cell(example.get('cusip') or '')} "
            f"({ _escape_cell(example.get('structure') or '') }) passed "
            f"after {example.get('n_attempts')} attempts.  The first "
            "attempt's code was:"
        )
        lines.append("")
        lines.append("```python")
        lines.append(str(example.get("wrong_code") or ""))
        lines.append("```")
        lines.append("")
        failed_names = [
            str(entry.get("name"))
            for entry in (example.get("failed_checks") or [])
            if entry.get("name") is not None
        ]
        if failed_names:
            lines.append(
                "Failed checks on attempt 1: " + ", ".join(failed_names) + "."
            )
            lines.append("")
        for entry in example.get("failed_checks") or []:
            if entry.get("detail"):
                lines.append(
                    f"- {_escape_cell(entry.get('name') or '')}: "
                    f"{_escape_cell(entry['detail'])}"
                )
        if example.get("failed_checks"):
            lines.append("")
        if example.get("counterexample"):
            lines.append("Counterexample fed back to the model:")
            lines.append("")
            lines += ["~~~text", str(example["counterexample"]), "~~~", ""]
        if example.get("feedback"):
            # The feedback repeats the previous code (shown above); show the
            # part after it, the failed checks with their counterexamples.
            feedback = str(example["feedback"])
            marker = "Failed checks to fix:"
            if marker in feedback:
                feedback = feedback[feedback.index(marker):]
            lines.append("Feedback sent with the repair request (after the previous code):")
            lines.append("")
            lines += ["~~~text", feedback, "~~~", ""]
        if example.get("wrong_code") and example.get("fixed_code"):
            diff = difflib.unified_diff(
                str(example["wrong_code"]).splitlines(),
                str(example["fixed_code"]).splitlines(),
                "attempt 1", "repaired", lineterm="", n=1,
            )
            lines.append("What the repair changed:")
            lines.append("")
            lines += ["```diff", *diff, "```", ""]
        lines.append("The repaired code that passed the full battery was:")
        lines.append("")
        lines.append("```python")
        lines.append(str(example.get("fixed_code") or ""))
        lines.append("```")
        lines.append("")
    lines.append("## Limitations")
    lines.append("")
    lines.append(
        "- Every number here is computed only from the saved benchmark "
        "and audit rows; no number is restated from memory or from a "
        "model reply."
    )
    lines.append(
        "- Inputs are stated assumptions as of each filing's pricing "
        "date, not live market data; preliminary terms, where a filing "
        "labels them preliminary, stay labelled preliminary in the "
        "saved rows."
    )
    lines.append(
        "- Synthesized ranges exclude skew and use a flat vol, so they "
        "are not directly comparable in width with ranges that include "
        "skew points."
    )
    lines.append(
        "- A passed battery shows the code reproduced the filing's "
        "stated examples and the stated payoff rules on the tested "
        "inputs; it does not remove model risk in the stated "
        "assumptions themselves."
    )
    lines.append(
        "- Notes with status \"needs_review\" were priced and then "
        "stopped by the plausibility gate; they are not counted as "
        "priced with proven AI-written payoffs."
    )
    lines.append("")
    lines.append(_NOT_ADVICE_SENTENCE)
    lines.append("")
    return "\n".join(lines)
