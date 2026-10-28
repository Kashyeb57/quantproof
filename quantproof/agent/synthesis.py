"""Payoff synthesis with Muse Wonder, checked by the synthesized-payoff battery.

Muse Wonder writes payoff code for a feature no template covers.  The code
is untrusted: it is never executed in this process and enters the registry
only after ``validation.synth_battery.run_synth_battery`` passes in full
(project rule 4).  This module builds the prompts, extracts the filing's
hypothetical payment examples with verified quotes, runs the attempt loop
and, only on request and only after a pass, registers the code.

The AI never states a number (project rule 3): every payment example here
comes from the filing's own table and carries a citation whose quote is
verified verbatim before it is trusted as evidence.

Pure Python + NumPy + quantproof only.  Network access happens only through
the injected ``chat`` callable (the Meta Model API client by default).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from quantproof.agent import client, extract
from quantproof.core.termsheet import Cited, Citation, TermSheet
from quantproof.ingest.html import Filing
from quantproof.payoffs.synthesized import terms_from_termsheet
from quantproof.registry.sandbox import static_check  # noqa: F401  (re-exported for reviewers)
from quantproof.validation.battery import CheckResult
from quantproof.validation.quotes import (
    _find_bounded,
    normalise_for_match,
    percent_forms,
    verify_quote,
)
from quantproof.validation.synth_battery import (
    PayoutExample,
    payment_in_quote,
    battery_passed,
    run_synth_battery,
)

SYNTH_PROMPT: str = """You write ONE Python function for a structured note payoff.

The exact payoff contract:
- Define exactly one top-level function: def cashflows(perf, terms):
- perf has shape (n_paths, n_dates, n_assets): perf[p, k, i] is underlying i's
  level on observation date k divided by its initial level, for path p.
- Return cash with shape (n_paths, n_dates): one column per observation date,
  aligned with terms["observation_dates"].
- Returned amounts are undiscounted cash amounts per note, in the note's
  currency units (for example dollars per $1,000 note). Do not discount.
- Do not return a present value, a per-path total, or anything with a
  different shape.

Imports allowed: numpy, math, scipy.stats, scipy.special only.
No randomness, no I/O, no classes, no names starting with "_",
no getattr/eval/exec/open. Use vectorised numpy where possible.

Payoff rules you must follow:
- On an autocall review date where the note is called, pay the call amount
  for that date and pay nothing after a call: every later column for that
  path must be zero, and no maturity amount is paid on a called path.
- Coupons and other amounts follow the terms dictionary below exactly.
- At maturity follow the filing's payment-at-maturity text exactly,
  including its exact thresholds, barriers, buffers, gearing, leverage,
  caps and any feature described in the filing text supplied by the user.

The terms dictionary (the function's second argument) has exactly these keys:
  notional: float, cash per note (usually 1000).
  issue_price: float, price paid per note at issue.
  n_assets: int, number of underlyings.
  underlyings: list of str, underlying names.
  observation_dates: list of str, ISO dates, sorted; column order.
  payment_dates: list of str, ISO dates aligned with observation_dates.
  final_index: int, index of the final review date in observation_dates.
  autocall: None, or dict with "trigger" (float, fraction of initial)
    and "call_amounts" (list aligned with observation_dates; the cash
    call amount per note on that review date, or None where the date is
    not an autocall review date).
  coupon: None, or dict with "barrier" (float, fraction of initial),
    "memory" (bool) and "amounts" (list aligned like call_amounts;
    the coupon amount per note on that review date, or None elsewhere).
  downside: dict with "kind" ("barrier"/"buffer"), "level" (float,
    fraction of initial), "gearing" (float) and "observation"
    ("final_only"/"each_review"/"daily").
  upside: None, or dict with "leverage" (float) and "cap" (float
    maximum return as a fraction, or None when uncapped).
  unsupported_features: list of str, payoff features the term-sheet
    schema could not hold (empty for template-supported notes).

Reply with exactly one ```python code block and nothing else."""

EXAMPLES_PROMPT: str = """Find the filing's hypothetical payment-at-maturity examples.

They are usually a table (titled for example "Hypothetical Payment at
Maturity" or "What Is the Total Return on the Notes at Maturity"), and
sometimes worked examples written as sentences ("If the underlier
appreciates 5%, investors will receive $1,075 per security").  Use the
table when there is one; otherwise use the sentences, one example each, with
the sentence copied verbatim as the quote.

Reply with JSON only, in exactly this form:
{"examples": [{"worst_final": <final value / initial value, e.g. 1.65>, "payment": <payment at maturity per note>, "quote": "<the table row verbatim, cells separated by single spaces>"}]}

Include every row of the table. worst_final is the final value divided by
the initial value (hypothetical tables use an initial value of $100.00, so
a final value of $165.00 is 1.65). payment is the payment at maturity per
note stated in that row. quote is the whole table row copied verbatim,
with cells separated by single spaces. Do not compute, infer or guess any
number: copy each row as the filing states it, except one conversion: when
the table states the payment as a percentage of the principal amount, give
payment in currency per note (121.200% of a $1,000 note is 1212.0). Reply
JSON only."""


@dataclass
class Attempt:
    """One synthesis attempt: the code tried, its battery results, its fate."""

    code: str
    results: list[CheckResult] = field(default_factory=list)
    passed: bool = False
    error: str | None = None
    feedback_sent: str | None = None
    counterexamples: dict[str, str] = field(default_factory=dict)
    cost_usd: float = 0.0
    seconds: float = 0.0


def counterexample(r: CheckResult) -> str | None:
    """Concrete failing input(s) for a failed check, or None.

    Formula: no pricing formula; this renders the diagnostic ``data``
    already computed by the battery for check ``r`` into a short text
    naming the concrete input on which the code disagreed, so the next
    synthesis attempt sees WHERE it is wrong, not only that it failed.

    Intuition: a check name plus a pass/fail detail rarely tells the
    model which payment is wrong; one filing row (``final/initial``,
    filing payment, code payment) or one mismatched path does.

    Reference: ``quantproof.validation.synth_battery`` check ``data``.

    Never raises: missing or malformed ``data`` gives ``data["error"]``
    when present, else None.
    """
    try:
        data = getattr(r, "data", None) or {}
        if not isinstance(data, dict):
            return None
        name = getattr(r, "name", None)
        if name == "filing_examples":
            rows = data.get("rows")
            if not rows:
                return getattr(r, "detail", None) or None
            lines: list[str] = []
            for row in rows:
                try:
                    worst_final = float(row["worst_final"])
                    expected = float(row["expected"])
                    got = float(row["got"])
                    other = float(row.get("other_columns_max_abs") or 0.0)
                except (KeyError, TypeError, ValueError, AttributeError):
                    continue
                if abs(got - expected) > 0.01 or other > 0.01:
                    lines.append(
                        f"final/initial {worst_final:.4f}: the filing pays "
                        f"{expected:.2f}, your code paid {got:.2f} at maturity "
                        f"(max other column {other:.2f})"
                    )
            return "\n".join(lines) if lines else None
        if name == "called_paths_match_template":
            rows = data.get("rows") or []
            lines = []
            for row in list(rows)[:5]:
                if isinstance(row, dict):
                    lines.append(", ".join(f"{k}={v}" for k, v in row.items()))
                else:
                    lines.append(str(row))
            return "\n".join(lines) if lines else (data.get("error") or None)
        if name == "output_sanity":
            problems = data.get("problems")
            if problems:
                return "; ".join(str(p) for p in problems)
            return data.get("error") or None
        if name == "determinism":
            if "max_abs_diff" in data:
                return f"max abs diff {data['max_abs_diff']}"
            return data.get("error") or None
        if name == "convergence":
            if "ratios" in data:
                return f"ratios {data['ratios']}"
            if "ratio" in data:
                return f"ratios {data['ratio']}"
            return data.get("error") or None
        if name == "static_safety":
            problems = data.get("problems")
            if problems:
                if isinstance(problems, (list, tuple)):
                    return "; ".join(str(p) for p in problems)
                return str(problems)
            return data.get("error") or None
        return data.get("error") or None
    except Exception:
        return None


@dataclass
class SynthesisResult:
    """Outcome of :func:`synthesise_payoff` across all attempts."""

    code: str | None
    passed: bool
    attempts: list[Attempt] = field(default_factory=list)
    examples: list[PayoutExample] = field(default_factory=list)
    registered: str | None = None

    @property
    def results(self) -> list[CheckResult]:
        """The last attempt's battery results, or [] when there are none."""
        return self.attempts[-1].results if self.attempts else []


def _format_feedback(feedback: object) -> str:
    """Render feedback (string, Attempt, mapping, checks, or pairs) as text."""
    if feedback is None:
        return ""
    if isinstance(feedback, str):
        return feedback
    # An Attempt (or anything with code/results/error attributes).
    if hasattr(feedback, "code") or hasattr(feedback, "results") or hasattr(feedback, "error"):
        lines: list[str] = []
        error = getattr(feedback, "error", None)
        if error:
            lines.append(f"Previous attempt failed: {error}")
        code = getattr(feedback, "code", "")
        if code:
            lines.append("Previous code:\n```python\n" + str(code) + "\n```")
        results = getattr(feedback, "results", None) or []
        failed = [r for r in results if not getattr(r, "passed", False)]
        if failed:
            lines.append("Failed checks to fix:")
            for r in failed:
                lines.append(f"- {r.name}: {r.detail}")
        return "\n".join(lines)
    if isinstance(feedback, dict):
        lines = []
        if feedback.get("error"):
            lines.append(f"Previous attempt failed: {feedback['error']}")
        if feedback.get("code"):
            lines.append("Previous code:\n```python\n" + str(feedback["code"]) + "\n```")
        checks = feedback.get("results") or feedback.get("failed") or feedback.get("checks") or []
        if checks:
            lines.append("Failed checks to fix:")
            lines.append(_format_feedback(list(checks)))
        return "\n".join(lines)
    if isinstance(feedback, (list, tuple)):
        # A (code, results) pair.
        if (
            len(feedback) == 2
            and isinstance(feedback[0], str)
            and isinstance(feedback[1], (list, tuple))
        ):
            lines = ["Previous code:\n```python\n" + feedback[0] + "\n```", "Failed checks to fix:"]
            lines.append(_format_feedback(list(feedback[1])))
            return "\n".join(lines)
        lines = []
        for item in feedback:  # type: ignore[union-attr]
            if hasattr(item, "name") and hasattr(item, "detail"):
                if not getattr(item, "passed", True):
                    lines.append(f"- {item.name}: {item.detail}")
                else:
                    lines.append(f"- {item.name}: {item.detail}")
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                lines.append(f"- {item[0]}: {item[1]}")
            else:
                lines.append(str(item))
        return "\n".join(lines)
    return str(feedback)


def _evidence_missing(r: CheckResult) -> bool:
    """True for a filing-examples failure caused by too few verified rows."""
    data = getattr(r, "data", None) or {}
    return r.name == "filing_examples" and int(data.get("verified", 3)) < 3


def build_synthesis_messages(filing_text, ts, feedback=None) -> list[dict]:
    """Build synthesis chat messages: system, filing text, then the request.

    The filing text precedes the request (stable prefix for caching).  The
    request lists every unsupported feature (value + quote), the full terms
    dictionary as JSON via ``terms_from_termsheet``, and, when ``feedback``
    is given, the previous code and the failed checks (name + detail) to fix.
    """
    feature_lines: list[str] = []
    for feature in ts.unsupported_features:
        feature_lines.append(f"- {feature.value}")
        quote = feature.citation.quote if feature.citation is not None else ""
        if quote:
            feature_lines.append(f"  Quote: {quote}")
    if not feature_lines:
        feature_lines.append("- (none recorded on the term sheet)")

    request_parts = [
        "Write the payoff for the structured note in the filing text above.",
        "",
        "Unsupported features (features no template covers):",
        *feature_lines,
        "",
        "Terms (JSON):",
        json.dumps(terms_from_termsheet(ts), indent=1),
    ]
    feedback_text = _format_feedback(feedback) if feedback is not None else ""
    if feedback_text:
        request_parts += [
            "",
            "Your previous attempt did not pass. Fix the problems below and "
            "reply with the corrected single ```python code block.",
            feedback_text,
        ]
    request_parts += [
        "",
        "Reply with exactly one ```python code block and nothing else.",
    ]

    user_content = "Filing text:\n" + filing_text + "\n\n" + "\n".join(request_parts)
    return [
        {"role": "system", "content": SYNTH_PROMPT},
        {"role": "user", "content": user_content},
    ]


def extract_code(text) -> str:
    """The last ```python ... ``` block (or ``` ... ```), else the stripped text."""
    if text is None:
        return ""
    candidate = str(text)
    blocks = re.findall(r"```(?:python|py)?[ \t]*\r?\n?(.*?)```", candidate, re.DOTALL)
    if blocks:
        return blocks[-1].strip()
    return candidate.strip()


def extract_examples(
    filing, *, chat=client.chat, model="muse-spark-1.3", max_tokens=8000,
    notional: float | None = None,
) -> list[PayoutExample]:
    """Extract the filing's hypothetical payment table as verified examples.

    Each row becomes a PayoutExample whose payment citation is verified
    (``verified`` is True) only when all of: the quote is found verbatim in
    the normalised filing text; ``payment_in_quote(payment, quote, notional,
    worst_final)`` is True (currency, or percent of principal / total return);
    and the normalised quote contains the final value as
    ``f"${worst_final*100:,.2f}"`` or the return as
    ``f"{(worst_final-1)*100:.2f}%"`` (hypothetical tables use an initial
    value of $100.00).  When ``notional`` is given, tables stated in percent
    also verify: the payment may appear as ``payment / notional`` in a
    percent spelling ("121.200%" for 1212.0 on 1000) and the final value as
    ``worst_final`` in a percent spelling ("150.000%" for 1.5).  Bad rows are
    skipped, never raise.  A reply that did
    not finish with "stop" or cannot be parsed gives [] -- the battery then
    fails for lack of evidence, which is the safe outcome.
    """
    messages = [
        {"role": "system", "content": EXAMPLES_PROMPT},
        {
            "role": "user",
            "content": (
                "Filing text:\n" + filing.text + "\n\n"
                "Find the hypothetical payment-at-maturity table now. Reply JSON only."
            ),
        },
    ]
    try:
        result = chat(messages, model=model, max_tokens=max_tokens, purpose="extract_examples")
    except Exception:
        return []
    if getattr(result, "finish_reason", None) != "stop":
        return []
    try:
        raw = extract.parse_json_reply(result.text)
    except Exception:
        return []
    rows = raw.get("examples") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        return []

    normalised_filing = normalise_for_match(filing.text)
    examples: list[PayoutExample] = []
    for row in rows:
        try:
            if not isinstance(row, dict):
                continue
            worst_final_raw = row.get("worst_final")
            payment_raw = row.get("payment")
            quote = row.get("quote")
            if isinstance(worst_final_raw, bool) or isinstance(payment_raw, bool):
                continue
            worst_final = float(worst_final_raw)  # type: ignore[arg-type]
            payment = float(payment_raw)  # type: ignore[arg-type]
            if not isinstance(quote, str) or not quote.strip():
                continue
        except (TypeError, ValueError):
            continue
        found_result = verify_quote(quote, normalised_filing)
        found = bool(found_result[0]) if isinstance(found_result, tuple) else bool(found_result)
        normalised_quote = normalise_for_match(quote)
        final_str = f"${worst_final * 100:,.2f}"
        return_str = f"{(worst_final - 1) * 100:.2f}%"
        in_quote = (
            final_str in normalised_quote
            or return_str in normalised_quote
            or final_str in quote
            or return_str in quote
        )
        payment_shown = payment_in_quote(payment, quote, notional, worst_final)
        if notional:
            # Percent tables state the final level as a share of the initial
            # level ("150.000%"); prose states the underlier's move ("appreciates
            # 5%", "declines 20%": the sign is in the verb, not the number).
            in_quote = in_quote or any(
                _find_bounded(normalise_for_match(form), normalised_quote) is not None
                for form in percent_forms(worst_final) | percent_forms(abs(worst_final - 1.0))
            )
        verified = bool(found and payment_shown and in_quote)
        examples.append(
            PayoutExample(
                worst_final=worst_final,
                payment=Cited(
                    payment,
                    Citation(quote=quote, section="hypothetical payout table", verified=verified),
                ),
            )
        )
    return examples


def synthesise_payoff(filing, ts, market, *, examples=None, chat=client.chat,
    model="muse-wonder-1.0", reasoning_effort="medium", max_tokens=32000, max_attempts=3,
    n_paths=20_000, seed=0, register=False, registry_dir=None) -> SynthesisResult:
    """Synthesise a payoff, prove it with the battery, optionally register it.

    Loops up to ``max_attempts``: ask the model, extract the code, run the
    full synthesized-payoff battery.  Feedback for the next attempt is the
    failed checks only (name + detail) plus the previous code.  A chat
    exception or a reply that did not finish with "stop" becomes an Attempt
    with ``error`` set and the loop continues.  Never registers code that
    did not pass, and never raises for model problems.
    """
    if examples is None:
        examples_list = extract_examples(filing, chat=chat)
    else:
        examples_list = list(examples)

    attempts: list[Attempt] = []
    feedback: str | None = None
    passed_code: str | None = None

    for _ in range(max(0, int(max_attempts))):
        messages = build_synthesis_messages(filing.text, ts, feedback=feedback)
        try:
            result = chat(
                messages,
                model=model,
                max_tokens=max_tokens,
                reasoning_effort=reasoning_effort,
                purpose="synthesis",
            )
        except Exception as exc:  # Model problems are attempts, never crashes.
            error = f"{type(exc).__name__}: {exc}"
            attempts.append(Attempt(code="", results=[], passed=False, error=error, feedback_sent=feedback))
            feedback = (
                f"Previous attempt failed: the chat call raised {error}. "
                "No code was received; write the complete payoff now."
            )
            continue

        if getattr(result, "finish_reason", None) != "stop":
            error = (
                "chat did not finish with 'stop' "
                f"(finish_reason={getattr(result, 'finish_reason', None)!r}); reply not trusted"
            )
            attempts.append(Attempt(code="", results=[], passed=False, error=error, feedback_sent=feedback, cost_usd=getattr(result, "cost_usd", 0.0), seconds=getattr(result, "seconds", 0.0)))
            feedback = (
                f"Previous attempt failed: {error}. "
                "Reply with exactly one complete ```python code block and nothing else."
            )
            continue

        code = extract_code(getattr(result, "text", "") or "")
        try:
            results = run_synth_battery(
                code, ts, market, examples_list,
                n_paths=n_paths, seed=seed, filing_text=filing.text,
            )
        except Exception as exc:  # A battery crash on model code is a failed attempt.
            error = f"{type(exc).__name__}: {exc}"
            attempts.append(Attempt(code=code, results=[], passed=False, error=error, feedback_sent=feedback, cost_usd=getattr(result, "cost_usd", 0.0), seconds=getattr(result, "seconds", 0.0)))
            feedback = (
                f"Previous attempt failed: the proof battery raised {error}.\n"
                "Previous code:\n```python\n" + code + "\n```"
            )
            continue

        passed = battery_passed(results)
        counterexamples: dict[str, str] = {}
        for r in results:
            if not r.passed:
                ce = counterexample(r)
                if ce is not None:
                    counterexamples[r.name] = ce
        attempts.append(Attempt(code=code, results=list(results), passed=bool(passed), error=None, feedback_sent=feedback, counterexamples=counterexamples, cost_usd=getattr(result, "cost_usd", 0.0), seconds=getattr(result, "seconds", 0.0)))
        if passed:
            passed_code = code
            break
        failed = [r for r in results if not r.passed]
        if all(_evidence_missing(r) for r in failed):
            # No code change can supply filing evidence: repairing would only
            # spend credits.  The note fails for lack of evidence.
            break
        feedback_lines = ["Previous code:\n```python\n" + code + "\n```", "Failed checks to fix:"]
        for r in failed:
            line = f"- {r.name}: {r.detail}"
            ce = counterexamples.get(r.name)
            if ce is not None:
                line += f"\n  Counterexample: {ce}"
            feedback_lines.append(line)
        feedback = "\n".join(feedback_lines)

    registered: str | None = None
    if passed_code is not None and register:
        # Lazy import: registry.store is trusted code and importing it here
        # avoids any import cycle at module load time.
        from quantproof.registry import store

        last_results = attempts[-1].results if attempts else []
        # Deterministic registry name: the CUSIP when present, else the features, else the issuer.
        if getattr(ts, "cusip", None) is not None:
            base_name = f"synthesized_{ts.cusip.value}"
        elif ts.unsupported_features:
            base_name = "synthesized_" + "_".join(str(f.value) for f in ts.unsupported_features)
        else:
            base_name = f"synthesized_{ts.issuer.value}"
        if registry_dir is not None:
            registered = store.register(
                base_name, passed_code, ts, last_results, model=model, directory=registry_dir
            )
        else:
            registered = store.register(base_name, passed_code, ts, last_results, model=model)

    return SynthesisResult(
        code=passed_code,
        passed=passed_code is not None,
        attempts=attempts,
        examples=examples_list,
        registered=registered,
    )
