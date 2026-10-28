"""Benchmark helpers for synthesized-payoff runs on one filing.

Pure orchestration of trusted code: term extraction is replayed from a
recorded audit call and quote-verified, the market is the audit's stated
assumptions, synthesis goes through
``quantproof.agent.synthesis.synthesise_payoff`` (untrusted code only ever
runs in the sandbox; project rule 4), and pricing is the trusted
risk-neutral Monte Carlo in ``quantproof.payoffs.synthesized`` /
``quantproof.valuation.fair_value``.  No new pricing maths is introduced
here; the only new arithmetic is comparing two Monte Carlo estimates and
summing template cashflows on a single deterministic example path.

The AI never states a number (project rule 3): example payments come from
the filing's own table with verified quotes, and every price comes from
an engine output.  The bank's estimated value is shown alongside ours and
is never a calibration target (rule 5).  No investment advice (rule 8).

References: project brief section 6; ``quantproof.report.audit_run`` for
the stated assumptions and plausibility gate; Hull, *Options, Futures,
and Other Derivatives*, chapters 4 and 21 (discounting, simulation).
"""

from __future__ import annotations

import itertools
import math
from dataclasses import replace
from typing import Any

import numpy as np

from quantproof.agent.costs import Usage, estimate_cost
from quantproof.agent.extract import parse_json_reply, to_termsheet
from quantproof.agent.synthesis import Attempt, extract_examples, synthesise_payoff
from quantproof.app.demo import build_market
from quantproof.core.market import MarketInputs
from quantproof.core.termsheet import TermSheet
from quantproof.payoffs.synthesized import cash_from_code, price_cashflows
from quantproof.payoffs.worst_of import observation_dates, pathwise_cashflows
from quantproof.report import audit_run
from quantproof.report.audit_run import ASSUMPTIONS, SEED, plausibility_issues
from quantproof.validation.quotes import verify_termsheet
from quantproof.validation.synth_battery import _citation_verified, _example_perf, battery_passed
from quantproof.payoffs.worst_of import price_note
from quantproof.valuation.fair_value import AssumptionGrid, shifted_market

SYNTH_MODEL = "muse-wonder-1.0"
EFFORT = "medium"
MAX_TOKENS = 32000
MAX_ATTEMPTS = 4
PRICE_PATHS = 20_000
GRID_PATHS = 4_000


def stated_market(ts: TermSheet) -> MarketInputs:
    """Market inputs from the audit's stated assumptions for ``ts``.

    Formula: no pricing formula; this duplicates exactly the market
    construction in ``audit_run.audit_note`` (vol ``ASSUMPTIONS["vol"]``
    and dividend ``ASSUMPTIONS["dividend"]`` per underlying, correlation
    ``ASSUMPTIONS["correlation"]`` for more than one underlying else
    0.0, rate and credit spread from ``ASSUMPTIONS``), with the source
    label replaced by "stated assumptions, not market data".

    Intuition: the benchmark must price on the same stated inputs as the
    audit so its numbers are comparable, and the label must not pretend
    these assumptions are market data as of the pricing date.

    Reference: ``quantproof.report.audit_run.ASSUMPTIONS``; brief section 6.
    """
    n = ts.n_underlyings
    rho = float(ASSUMPTIONS["correlation"]) if n > 1 else 0.0
    market = build_market(
        ts,
        rate=float(ASSUMPTIONS["risk_free_rate"]),
        spread=float(ASSUMPTIONS["credit_spread"]),
        vols=(float(ASSUMPTIONS["vol"]),) * n,
        dividends=(float(ASSUMPTIONS["dividend"]),) * n,
        rho=rho,
    )
    # Same construction as demo.build_market, but the provenance label must
    # say these are the audit's stated assumptions, not market data.
    market = replace(
        market, sources={"all": "stated assumptions, not market data"}
    )
    return market


def termsheet_from_audit(calls: list[dict], filing_text: str) -> TermSheet:
    """Term sheet from the first recorded audit call, quotes verified.

    Formula: no pricing formula; ``to_termsheet(parse_json_reply(
    calls[0]["text"]))`` followed by ``verify_termsheet(ts, filing_text)``
    (project rule 5a: every extracted term needs a verified quote;
    unverified fields are flagged on the citations, never used silently).

    Intuition: the benchmark replays the audit's extraction rather than
    paying for a new one, but the quote check still runs so a tampered
    or stale recording cannot slip unverified terms into pricing.

    Reference: ``quantproof.agent.extract``; ``quantproof.validation.quotes``.
    """
    ts = to_termsheet(parse_json_reply(calls[0]["text"]))
    verify_termsheet(ts, filing_text)
    return ts


class BudgetExceeded(RuntimeError):
    """The worst-case cost of the next chat call would exceed the budget."""


class BudgetGuard:
    """Chat wrapper that refuses calls whose worst case exceeds a budget.

    Formula: before each call, the worst-case cost is
    ``estimate_cost(model, Usage(input_tokens=ceil(total message chars
    / 4), output_tokens=max_tokens))``; if ``spent + worst > budget``
    the call is refused with :class:`BudgetExceeded` and the wrapped
    callable is never invoked.  Otherwise the call goes through and the
    returned ``ChatResult.cost_usd`` is added to ``spent_usd``.

    Intuition: token counts are unknown before the call, so the guard
    budgets against the most the call could cost (full ``max_tokens``
    of output) rather than discovering an overrun afterwards.

    Reference: ``quantproof.agent.costs.estimate_cost``.
    """

    def __init__(self, chat, budget_usd, spent_usd=0.0) -> None:
        """Wrap ``chat`` with a total budget of ``budget_usd`` dollars."""
        self._chat = chat
        self.chat = chat
        self.budget_usd = float(budget_usd)
        self.spent_usd = float(spent_usd)
        self.refused = False  # set when a call is refused (callers may swallow the raise)

    def __call__(self, messages, **kw):
        """Call the wrapped chat if its worst-case cost fits the budget.

        Raises :class:`BudgetExceeded` (message carries spent, worst-case
        and budget figures) without calling the wrapped callable when
        ``spent_usd + worst > budget_usd``.
        """
        model = kw.get("model") or SYNTH_MODEL
        total_chars = 0
        for message in messages or []:
            if isinstance(message, dict):
                content = message.get("content", "")
            else:
                content = getattr(message, "content", "")
            total_chars += len(str(content or ""))
        max_tokens = kw.get("max_tokens") or 0
        usage = Usage(
            input_tokens=math.ceil(total_chars / 4),
            output_tokens=int(max_tokens),
        )
        worst = float(estimate_cost(model, usage))
        if self.spent_usd + worst > self.budget_usd:
            self.refused = True
            raise BudgetExceeded(
                f"BudgetExceeded: spent {self.spent_usd:.4f} + worst-case "
                f"{worst:.4f} would exceed budget {self.budget_usd:.4f}"
            )
        result = self._chat(messages, **kw)
        self.spent_usd += float(getattr(result, "cost_usd", 0.0) or 0.0)
        return result


def synthesized_range(ts: TermSheet, market: MarketInputs, code: str, n_paths: int = GRID_PATHS, seed: int = SEED) -> dict:
    """Fair-value range for synthesized ``code`` over the assumption grid.

    Formula: for every (vol, correlation, spread, dividend) shift of
    ``AssumptionGrid()`` (correlation shift only 0.0 for a single
    underlying), price with ``price_cashflows(ts, shifted_market(...),
    cash_from_code(code), n_paths, seed)`` (risk-neutral Monte Carlo,
    issuer credit spread in discounting); ``base`` is the same price at
    the unshifted ``market`` with ``PRICE_PATHS`` paths.  ``low``/``high``
    are the min/max grid prices and ``points`` the number of grid prices.

    Intuition: fair value is reported as a range over stated assumption
    shifts (rule 5), never as a single tuned number, and never adjusted
    to match the bank's estimated value.  Common random numbers (the same
    seed at every point) keep the range about assumptions, not noise.

    Reference: brief section 6; ``quantproof.valuation.fair_value``.

    Note on skew: NO skew is included -- ``price_cashflows`` has no skew
    input -- so this range is narrower than the template's (which adds
    skew points); the result says so with ``"skew": "not included"``.
    """
    grid = AssumptionGrid()
    corr_shifts = (0.0,) if ts.n_underlyings == 1 else tuple(grid.correlation_shifts)
    cash_fn = cash_from_code(code)
    prices: list[float] = []
    for vol, corr, spread, dividend in itertools.product(
        grid.vol_shifts, corr_shifts, grid.spread_shifts, grid.dividend_shifts
    ):
        shifted = shifted_market(
            market,
            vol_shift=float(vol),
            correlation_shift=float(corr),
            spread_shift=float(spread),
            dividend_shift=float(dividend),
        )
        prices.append(float(price_cashflows(ts, shifted, cash_fn, n_paths=n_paths, seed=seed).price))
    base = price_cashflows(ts, market, cash_fn, n_paths=PRICE_PATHS, seed=seed)
    return {
        "low": min(prices),
        "high": max(prices),
        "base": {
            "price": float(base.price),
            "std_error": float(base.std_error),
            "n_paths": int(base.paths),
            "seed": int(base.seed),
        },
        "points": len(prices),
        "skew": "not included",
    }


def template_comparison(ts: TermSheet, market: MarketInputs, code: str) -> dict:
    """Template price versus synthesized-code price on the same market.

    Formula: ``template = price_note(ts, market, PRICE_PATHS, SEED)`` and
    ``ai = price_cashflows(ts, market, cash_from_code(code), PRICE_PATHS,
    SEED)``; ``diff = ai_price - template_price`` and the combined
    standard error is ``sqrt(template_se**2 + ai_se**2)``; the two agree
    ``within_3se`` when ``abs(diff) <= 3 * combined_se``.

    Intuition: two Monte Carlo estimates of (nearly) the same payoff
    should differ by only Monte Carlo noise; 3 combined standard errors
    is the tolerance.  The same seed makes the two estimates closer than
    independent ones, so the 3-SE test stays conservative.

    Reference: Glasserman, *Monte Carlo Methods in Financial Engineering*,
    chapters 2-3 (standard errors); tolerance justified as 3 standard
    errors of the difference.
    """
    template = price_note(ts, market, PRICE_PATHS, SEED)
    ai = price_cashflows(ts, market, cash_from_code(code), PRICE_PATHS, SEED)
    template_price = float(template.price)
    ai_price = float(ai.price)
    template_se = float(template.std_error)
    ai_se = float(ai.std_error)
    diff = ai_price - template_price
    combined_se = math.sqrt(template_se**2 + ai_se**2)
    return {
        "template_price": template_price,
        "template_se": template_se,
        "ai_price": ai_price,
        "ai_se": ai_se,
        "diff": float(diff),
        "combined_se": float(combined_se),
        "within_3se": bool(abs(diff) <= 3 * combined_se),
    }


def template_examples(ts: TermSheet, examples, filing_text: str) -> dict:
    """Template payoff checked against the filing's verified examples.

    Formula: for each example whose citation is verified
    (``_citation_verified``), build its deterministic performance array
    (``_example_perf``), take ``pathwise_cashflows(ts,
    observation_dates(ts), perf, lambda d: 1.0)`` summed per path
    (undiscounted, discount factor 1.0), and compare that total with the
    filing's stated ``expected`` payment.  ``max_error`` is the largest
    absolute difference and the check passes when it is at most 0.01.

    Intuition: the filing states payments to the cent, so a template
    that reproduces its own hypothetical table must match within a
    cent; this is the same evidence the synthesis battery uses.

    Reference: filing hypothetical payment table; tolerance 0.01 because
    the filing states cents.
    """
    dates = observation_dates(ts)
    rows: list[dict[str, Any]] = []
    notional = float(ts.notional.value)
    verified = [ex for ex in examples if _citation_verified(ex.payment, filing_text, notional)]
    if not verified:
        return {"rows": [], "max_error": None, "passed": False}
    perf = _example_perf(ts, verified)
    # Discount factor 1.0: undiscounted cash per path; on these surviving
    # paths only the maturity payment is non-zero.
    totals = np.asarray(pathwise_cashflows(ts, dates, perf, lambda d: 1.0), dtype=float)
    if totals.ndim == 2:
        totals = totals.sum(axis=1)
    for example, got in zip(verified, totals):
        expected = float(example.payment.value)
        got = float(got)
        rows.append(
            {
                "worst_final": float(example.worst_final),
                "expected": expected,
                "got": got,
            }
        )
    max_error = max((abs(r["got"] - r["expected"]) for r in rows), default=None)
    return {
        "rows": rows,
        "max_error": float(max_error) if max_error is not None else None,
        "passed": bool(max_error is not None and max_error <= 0.01),
    }


def _json_safe(value: Any) -> Any:
    """JSON-safe copy of ``value``: non-finite floats become None.

    Formula: no pricing formula; floats that are NaN or infinite (which
    JSON cannot represent) map to None, numpy scalars unwrap to Python
    scalars, and containers are copied recursively.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (np.floating,)):
        result = float(value)
        return result if math.isfinite(result) else None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def attempt_to_dict(a: Attempt) -> dict:
    """JSON-safe dict view of one synthesis :class:`Attempt`.

    Formula: no pricing formula; copies code/passed/error plus the new
    provenance fields (``feedback_sent``, ``counterexamples``,
    ``cost_usd``, ``seconds``) and each check's name, passed flag,
    value, reference, tolerance and detail, with non-finite floats
    mapped to None so the result is JSON-serialisable.

    Intuition: the benchmark report must show what the model was told,
    what concrete counterexample it was given, and what each attempt
    cost, without leaking NaNs into saved JSON.

    Reference: ``quantproof.agent.synthesis.Attempt``;
    ``quantproof.validation.battery.CheckResult``.
    """
    checks = []
    for r in getattr(a, "results", []) or []:
        checks.append(
            {
                "name": getattr(r, "name", None),
                "passed": bool(getattr(r, "passed", False)),
                "value": _json_safe(getattr(r, "value", None)),
                "reference": _json_safe(getattr(r, "reference", None)),
                "tolerance": _json_safe(getattr(r, "tolerance", None)),
                "detail": getattr(r, "detail", None),
            }
        )
    return {
        "code": a.code,
        "passed": bool(a.passed),
        "error": a.error,
        "feedback_sent": getattr(a, "feedback_sent", None),
        "counterexamples": _json_safe(dict(getattr(a, "counterexamples", {}) or {})),
        "cost_usd": _json_safe(float(getattr(a, "cost_usd", 0.0) or 0.0)),
        "seconds": _json_safe(float(getattr(a, "seconds", 0.0) or 0.0)),
        "checks": checks,
    }


def run_note(filing, calls_audit, meta, *, chat, template_covered: bool) -> dict:
    """Run the synthesized-payoff benchmark for one filing.

    Formula: no new pricing maths.  The term sheet is replayed and
    quote-verified (:func:`termsheet_from_audit`), the market is the
    stated assumptions (:func:`stated_market`), filing examples are
    extracted with verified quotes, and ``synthesise_payoff`` runs with
    ``SYNTH_MODEL``/``EFFORT``/``MAX_TOKENS``/``MAX_ATTEMPTS``,
    ``PRICE_PATHS`` and ``SEED``, ``register=False``.  Templates are
    DISABLED for synthesis: the model always writes the full payoff;
    the template is only used afterwards for the comparison
    (:func:`template_comparison`, :func:`template_examples`) when
    ``template_covered``.  On a pass, ``fair_value`` is
    :func:`synthesized_range` and the plausibility gate from
    ``audit_run.plausibility_issues`` decides ``status``:
    ``"priced_ai"`` when the gate is None, else ``"needs_review"``;
    on failure the status is ``"synthesis_failed"``.

    Intuition: synthesis must prove itself against the full battery on
    every note, even ones a template could price, so the benchmark
    measures the AI payoff fairly; the template then serves only as an
    independent comparison, and the gate flags implausible values for
    human review without ever adjusting assumptions to match the bank.

    Reference: brief section 6; AGENTS.md rules 3, 4, 5, 5a and 8.

    :class:`BudgetExceeded` propagates: if the synthesis loop recorded
    it as an attempt error (its chat-exception handling), it is
    re-raised here instead of being reported as a synthesis failure.
    """
    filing_text = filing.text
    ts = termsheet_from_audit(calls_audit, filing_text)
    market = stated_market(ts)
    examples = extract_examples(filing, chat=chat, notional=float(ts.notional.value))
    if getattr(chat, "refused", False):  # extract_examples swallows exceptions
        raise BudgetExceeded("budget guard refused the examples call")
    syn = synthesise_payoff(
        filing,
        ts,
        market,
        examples=examples,
        chat=chat,
        model=SYNTH_MODEL,
        reasoning_effort=EFFORT,
        max_tokens=MAX_TOKENS,
        max_attempts=MAX_ATTEMPTS,
        n_paths=PRICE_PATHS,
        seed=SEED,
        register=False,
    )
    for attempt in syn.attempts:
        if getattr(chat, "refused", False) or (
            attempt.error and "BudgetExceeded" in str(attempt.error)
        ):
            raise BudgetExceeded(str(attempt.error))

    passed = bool(syn.passed)
    n_attempts = len(syn.attempts)
    first_failing_checks: list[str] = []
    if syn.attempts:
        first_failing_checks = [
            str(r.name) for r in (syn.attempts[0].results or []) if not r.passed
        ]

    issue_price = float(ts.issue_price.value) if ts.issue_price is not None else None
    estimated_value = float(ts.estimated_value.value) if ts.estimated_value is not None else None

    result: dict[str, Any] = {
        "meta": meta,
        "cusip": ts.cusip.value if ts.cusip is not None else None,
        "issuer": ts.issuer.value if ts.issuer is not None else None,
        "structure": audit_run._structure_label(ts),
        "unsupported_features": [
            {
                "value": feature.value,
                "quote": feature.citation.quote if getattr(feature, "citation", None) is not None else "",
            }
            for feature in (ts.unsupported_features or [])
        ],
        "issue_price": issue_price,
        "estimated_value": estimated_value,
        "examples": [
            {
                "worst_final": float(example.worst_final),
                "payment": float(example.payment.value),
                "quote": example.payment.citation.quote if getattr(example.payment, "citation", None) is not None else "",
                "verified": bool(example.payment.citation.verified) if getattr(example.payment, "citation", None) is not None else False,
            }
            for example in (syn.examples if syn.examples is not None else examples or [])
        ],
        "attempts": [attempt_to_dict(a) for a in syn.attempts],
        "passed": passed,
        "n_attempts": int(n_attempts),
        "repaired": bool(passed and n_attempts > 1),
        "first_failing_checks": first_failing_checks,
    }

    if not passed:
        result["status"] = "synthesis_failed"
        return result

    fair_value = synthesized_range(ts, market, syn.code)
    gate = plausibility_issues(fair_value, issue_price, estimated_value)
    result["fair_value"] = fair_value
    result["gate"] = gate
    result["status"] = "priced_ai" if gate is None else "needs_review"
    if template_covered:
        result["template"] = template_comparison(ts, market, syn.code)
        result["template_examples"] = template_examples(
            ts, syn.examples if syn.examples is not None else examples, filing_text
        )
    return result


class ErrorRecorder(audit_run.Recorder):
    """A Recorder that also records failed calls, so replay reproduces them.

    The synthesis loop turns a chat exception into a failed attempt and
    carries on; recording the error keeps the replayed attempt sequence
    identical to the live one.
    """

    def __call__(self, messages: list[dict], **kw: Any) -> Any:
        """Record the call (or its error) and return the result or re-raise."""
        try:
            return super().__call__(messages, **kw)
        except Exception as exc:
            if isinstance(exc, BudgetExceeded):
                raise  # refused before any call: nothing happened to record
            self.calls.append(
                {
                    "purpose": kw.get("purpose"),
                    "model": kw.get("model"),
                    "error": f"{type(exc).__name__}: {str(exc)[:400]}",
                    "cost_usd": 0.0,
                    "seconds": 0.0,
                }
            )
            raise


class RecordedCallError(RuntimeError):
    """A live call failure, replayed from its recording."""


class ErrorReplay(audit_run.Replay):
    """A Replay that re-raises recorded call errors (see :class:`ErrorRecorder`)."""

    def __call__(self, messages: list[dict], *, model: str, purpose: str | None = None, **kw: Any):
        """Raise the recorded error for a failed call, else replay normally."""
        if self._calls and "error" in self._calls[0]:
            recorded = self._calls.pop(0)
            raise RecordedCallError(recorded["error"])
        return super().__call__(messages, model=model, purpose=purpose, **kw)


def fill_attempt_costs(result: dict, calls: list[dict]) -> None:
    """Set each attempt's cost and seconds from the recorded synthesis calls.

    Every attempt consumed exactly one recorded ``synthesis`` call (a reply
    or a recorded error), in order, so the i-th attempt gets the i-th
    recording's cost and time; replay itself spends $0, so the recorded
    figures are the ones reported.
    """
    synth_calls = [c for c in calls if c.get("purpose") == "synthesis"]
    for attempt, call in zip(result.get("attempts", []), synth_calls):
        attempt["cost_usd"] = float(call.get("cost_usd", 0.0) or 0.0)
        attempt["seconds"] = float(call.get("seconds", 0.0) or 0.0)
