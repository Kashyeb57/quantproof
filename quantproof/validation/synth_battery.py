"""Proof battery for agent-synthesized payoffs.

A synthesized payoff enters the registry only after passing every check here.
Each check is independent of the generated code's own logic: static safety in
the sandbox, determinism on fixed simulated paths, output sanity against the
trusted autocall logic, the filing's own hypothetical payment table (only
where the citation is verified verbatim), an exact comparison with the
trusted template on called paths, and Monte Carlo convergence.

Pure Python + NumPy.  No AI and no network calls in this module.  Agent code
only ever runs through ``registry.sandbox.run_payoff`` (via
``payoffs.synthesized.cash_from_code``).

References: filing terms and hypothetical payment tables; J. C. Hull,
*Options, Futures, and Other Derivatives*, chapters 21 and 26; P. Glasserman,
*Monte Carlo Methods in Financial Engineering*, chapters 2-4.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

import numpy as np

from quantproof.core.market import MarketInputs
from quantproof.core.termsheet import Cited, TermSheet
from quantproof.models.discounting import year_fraction
from quantproof.models.montecarlo import simulate_performance
from quantproof.payoffs.synthesized import (
    cash_from_code,
    price_cashflows,
    strip_unsupported,
    terms_from_termsheet,
)
from quantproof.payoffs.worst_of import observation_dates, pathwise_cashflows
from quantproof.registry.sandbox import SandboxError, static_check
from quantproof.validation.battery import CheckResult
from quantproof.validation.quotes import (
    _find_bounded,
    normalise_for_match,
    percent_forms,
    value_in_quote,
    verify_quote,
)


@dataclass
class PayoutExample:
    """One row of the filing's hypothetical payment table.

    ``worst_final`` is the worst underlying's final value divided by its
    initial level (e.g. 1.65 for +65%); ``payment`` is the cited payment at
    maturity per note from the filing's table.
    """

    worst_final: float
    payment: Cited[float]


# --- Private helpers --------------------------------------------------------------------------


def _simulate_perf(
    ts: TermSheet, market: MarketInputs, n_paths: int, seed: int
) -> np.ndarray:
    """Risk-neutral simulated performance, exactly as in ``price_cashflows``.

    Formula: ``simulate_performance`` at the observation dates with drifts
    ``(r,) * N`` (risk-neutral), antithetic sampling, fixed ``seed``.

    Intuition: checks that compare two runs must share identical paths, so
    the simulation is done once here and handed to the sandbox as data.

    Reference: Hull chapter 21; Glasserman chapters 2-3.
    """
    pricing_date = ts.pricing_date.value
    dates = observation_dates(ts)
    times = [year_fraction(pricing_date, d) for d in dates]
    rate = float(market.risk_free_rate)
    return simulate_performance(
        times,
        market.vols,
        market.dividend_yields,
        np.asarray(market.correlation, dtype=float),
        drifts=(rate,) * ts.n_underlyings,
        n_paths=n_paths,
        seed=seed,
        antithetic=True,
    )


def _run_code(code: str, ts: TermSheet, perf: np.ndarray) -> tuple[np.ndarray | None, str | None]:
    """Run synthesized code on ``perf`` through the sandbox; (cash, error)."""
    try:
        cash = cash_from_code(code)(perf, terms_from_termsheet(ts))
    except SandboxError as exc:
        return None, str(exc)
    except Exception as exc:  # A failed run is a failed check, never a crash.
        return None, f"{type(exc).__name__}: {exc}"
    return np.asarray(cash, dtype=float), None


def _trusted_first_call(ts: TermSheet, perf: np.ndarray) -> np.ndarray:
    """First trusted autocall review index per path, or -1 when never called.

    Formula: with ``W(t) = min_i perf[:, t, i]``, the first autocall review
    index ``k`` (including the final review date when it is also an autocall
    review date, as ``worst_of`` does) with ``W(k) >= trigger``.

    Intuition: this is the trusted template's call decision, recomputed here
    from the term sheet, so generated cash can be checked against it without
    trusting the generated code's own call logic.

    Reference: filing terms; ``quantproof.payoffs.worst_of.pathwise_cashflows``.
    """
    n_paths = int(perf.shape[0])
    first = np.full(n_paths, -1, dtype=np.int64)
    if ts.autocall is None:
        return first
    dates = observation_dates(ts)
    trigger = float(ts.autocall.trigger.value)
    worst = np.min(perf, axis=2)
    indices = sorted(dates.index(obs.review_date.value) for obs in ts.autocall.observations)
    for k in indices:
        newly = (first < 0) & (worst[:, k] >= trigger)
        first[newly] = k
    return first


def _example_perf(ts: TermSheet, examples: list[PayoutExample]) -> np.ndarray:
    """Deterministic performance paths realising each filing example.

    Formula: shape ``(len(examples), n_dates, N)``; every date before the
    final one is ``0.5 * min(trigger if autocall, coupon barrier if coupon,
    1.0)`` for all assets (never called, no coupon); at the final date asset
    0 is ``worst_final`` and every other asset is ``worst_final + 0.25``, so
    the worst final performance is exactly ``worst_final``.

    Intuition: the filing's table is stated in terms of the worst final
    level with the note surviving to maturity, so the paths are built to
    make exactly that happen and nothing else pay on the way.

    Reference: filing hypothetical payment table; Hull chapter 26.
    """
    dates = observation_dates(ts)
    n_dates = len(dates)
    final_index = dates.index(ts.final_review_date.value)
    candidates = [1.0]
    if ts.autocall is not None:
        candidates.append(float(ts.autocall.trigger.value))
    if ts.coupon is not None:
        candidates.append(float(ts.coupon.barrier.value))
    early = 0.5 * min(candidates)
    perf = np.full((len(examples), n_dates, ts.n_underlyings), early, dtype=float)
    for i, example in enumerate(examples):
        perf[i, final_index, 0] = float(example.worst_final)
        if ts.n_underlyings > 1:
            perf[i, final_index, 1:] = float(example.worst_final) + 0.25
    return perf


def payment_in_quote(
    payment: float, quote: str, notional: float | None = None, worst_final: float | None = None
) -> bool:
    """Whether a table-row quote states this payment at maturity.

    Accepted spellings: currency ("$1,073.40", via ``value_in_quote``) and,
    when ``notional`` is given, payment / notional as a percent of principal
    ("121.200%") or payment / notional - 1 as the total return on the note
    ("7.34%").  Percent spellings are matched cell by cell (the row split on
    spaces): the row's own final level and underlier return (from
    ``worst_final``) each use up at most one matching cell first, so a
    payment can never be "verified" by the level column of the same row
    (a 150% final level does not support a payment of 1,500), while a row
    that states the same percent twice ("110.00 10.00% 10.00%") still does.
    """
    if value_in_quote(payment, quote) is True:
        return True
    if not notional:
        return False
    # Cells keep their sign (normalise_for_match drops "-", which would let
    # a +50% level take a -50% return cell): unify minus signs, drop "+"
    # and table bars, split on whitespace.
    raw = re.sub(r"\.{3,}|…|\|", " ", quote).replace("\u2212", "-").replace("\u2013", "-")
    cells = [cell.lstrip("+").rstrip(",;") for cell in raw.split()]

    def take(fraction: float) -> bool:
        wanted = set(percent_forms(fraction))
        for i, cell in enumerate(cells):
            if cell in wanted:
                del cells[i]
                return True
        return False

    if worst_final is not None:
        take(worst_final)  # final level as a percent of initial, if written so
        take(worst_final - 1.0)  # underlier return
    share = float(payment) / float(notional)
    return take(share) or take(share - 1.0)


def _citation_verified(
    cited: Cited,
    filing_text: str | None,
    notional: float | None = None,
    worst_final: float | None = None,
) -> bool:
    """Whether a cited payment's quote is verified (see check_filing_examples).

    The quote must be in the filing and state this payment
    (:func:`payment_in_quote`).
    """
    if cited.citation is None:
        return False
    if filing_text is None:
        return cited.citation.verified is True
    found, _reason = verify_quote(cited.citation.quote, normalise_for_match(filing_text))
    return bool(found) and payment_in_quote(
        float(cited.value), cited.citation.quote, notional, worst_final
    )


def _failed(name: str, detail: str, error: str | None) -> CheckResult:
    """Failed CheckResult for a run that produced no usable cash."""
    return CheckResult(
        name=name,
        passed=False,
        value=None,
        reference=None,
        tolerance=None,
        detail=f"{detail}: {error}" if error else detail,
        data={"error": error},
    )


# --- Checks ------------------------------------------------------------------------------------


def check_static_safety(code: str) -> CheckResult:
    """Agent code must pass the sandbox static check before it ever runs.

    Formula: no pricing formula; ``registry.sandbox.static_check`` rejects
    imports outside the allow-list (numpy, scipy, math), forbidden names and
    attributes, classes, generators and private/dunder names.

    Intuition: generated code is untrusted; anything the static checker
    cannot see through is refused without execution.

    Reference: the sandbox contract in ``quantproof/registry/sandbox.py``.

    Tolerance: none; the static report must be ok.
    """
    report = static_check(code)
    detail = (
        "sandbox static check passed"
        if report.ok
        else "sandbox static check failed: " + "; ".join(report.problems)
    )
    return CheckResult(
        name="static_safety",
        passed=bool(report.ok),
        value=1.0 if report.ok else 0.0,
        reference=1.0,
        tolerance=0.0,
        detail=detail,
        data={"problems": list(report.problems)},
    )


def check_determinism(
    code: str, ts: TermSheet, market: MarketInputs, n_paths: int, seed: int
) -> CheckResult:
    """Same code on the same simulated paths must give identical cash.

    Formula: simulate ``perf`` once (risk-neutral, fixed ``seed``), run the
    code twice through the sandbox, and require the two cash arrays to be
    exactly equal, ``max |cash_1 - cash_2| = 0``.

    Intuition: a payoff is a function of the paths and the terms; any hidden
    randomness or state would make prices irreproducible.

    Reference: Glasserman chapters 2-3 (seeded simulation).

    Tolerance: 0, because the inputs are bit-identical.
    """
    tolerance = 0.0  # identical inputs must give bit-identical output
    perf = _simulate_perf(ts, market, n_paths, seed)
    first, error = _run_code(code, ts, perf)
    if error is not None or first is None:
        return _failed(
            "determinism",
            f"payoff run failed (seed={seed}, n_paths={n_paths})",
            error,
        )
    second, error = _run_code(code, ts, perf)
    if error is not None or second is None:
        return _failed(
            "determinism",
            f"second payoff run failed (seed={seed}, n_paths={n_paths})",
            error,
        )
    max_diff = float(np.max(np.abs(first - second))) if first.size else 0.0
    return CheckResult(
        name="determinism",
        passed=bool(first.shape == second.shape and np.array_equal(first, second)),
        value=max_diff,
        reference=0.0,
        tolerance=tolerance,
        detail=f"payoff run twice on the same simulated perf (seed={seed}, n_paths={n_paths}); arrays must be identical",
        data={"max_abs_diff": max_diff, "seed": seed, "n_paths": int(n_paths)},
    )


def check_output_sanity(
    code: str, ts: TermSheet, market: MarketInputs, n_paths: int, seed: int
) -> CheckResult:
    """Cash must be well-shaped, finite, non-negative and stop after a call.

    Formula: run the code on simulated ``perf``; require shape
    ``(n_paths, n_dates)``, all entries finite, ``cash >= 0``, and (when the
    note is autocallable) cash zero in every column after the trusted first
    call index from ``_trusted_first_call``.

    Intuition: a note cannot pay a negative amount, and once it has been
    called it no longer exists, so nothing may be paid afterwards -- in
    particular not a maturity amount on top of the call amount.

    Reference: filing terms; ``quantproof.payoffs.worst_of.pathwise_cashflows``.

    Tolerance: 0 for shape/finiteness/sign; post-call entries must be exactly
    zero because the trusted call logic fixes the call date path by path.
    """
    perf = _simulate_perf(ts, market, n_paths, seed)
    cash, error = _run_code(code, ts, perf)
    if error is not None or cash is None:
        return _failed(
            "output_sanity",
            f"payoff run failed (seed={seed}, n_paths={n_paths})",
            error,
        )
    dates = observation_dates(ts)
    expected_shape = (int(n_paths), len(dates))
    problems: list[str] = []
    post_call_violations = 0
    if cash.shape != expected_shape:
        problems.append(f"shape {cash.shape} != expected {expected_shape}")
    else:
        if not np.all(np.isfinite(cash)):
            problems.append("cash contains non-finite values")
        if np.any(cash < 0):
            problems.append("cash contains negative values")
        if ts.autocall is not None and np.all(np.isfinite(cash)):
            first_call = _trusted_first_call(ts, perf)
            for p in range(cash.shape[0]):
                k = int(first_call[p])
                if k >= 0 and k + 1 < cash.shape[1] and np.any(cash[p, k + 1 :] != 0.0):
                    post_call_violations += 1
            if post_call_violations:
                problems.append(
                    f"cash is non-zero after the trusted call date on "
                    f"{post_call_violations} paths"
                )
    passed = not problems
    return CheckResult(
        name="output_sanity",
        passed=bool(passed),
        value=float(post_call_violations),
        reference=0.0,
        tolerance=0.0,
        detail=(
            f"shape {expected_shape}, finite, cash >= 0, zero after trusted call date "
            f"(seed={seed}, n_paths={n_paths})"
            + ("" if passed else ": " + "; ".join(problems))
        ),
        data={
            "shape": list(cash.shape),
            "post_call_violations": int(post_call_violations),
            "problems": problems,
        },
    )


def check_filing_examples(
    code: str,
    ts: TermSheet,
    examples: list[PayoutExample],
    filing_text: str | None = None,
) -> CheckResult:
    """Generated cash must reproduce the filing's hypothetical payment table.

    Formula: build ``_example_perf`` (note survives, worst final performance
    ``worst_final``); for each verified example the cash at the final column
    must equal the cited ``payment`` and every other column must be 0.  Only
    examples whose citation is verified count: ``verify_quote`` on
    ``normalise_for_match(filing_text)`` when ``filing_text`` is given, else
    ``citation.verified is True``.  At least 3 verified examples are
    required, else the check fails with "not enough filing evidence".

    Intuition: the filing's own table is ground truth the extractor and the
    payoff author both read; a payoff that cannot reproduce it to the cent
    has misread the terms.  Unverified citations are never used silently
    (project rule 5a).

    Reference: filing hypothetical payment table; AGENTS.md rule 5a.

    Tolerance: $0.01, because the filing states payments to the cent.
    """
    tolerance = 0.01  # filing states hypothetical payments to the cent
    verified = [
        ex for ex in examples
        if _citation_verified(
            ex.payment, filing_text, notional=float(ts.notional.value), worst_final=ex.worst_final
        )
    ]
    if len(verified) < 3:
        return CheckResult(
            name="filing_examples",
            passed=False,
            value=float(len(verified)),
            reference=3.0,
            tolerance=0.0,
            detail=(
                f"not enough filing evidence: {len(verified)} verified examples, "
                "need at least 3"
            ),
            data={"rows": [], "verified": int(len(verified))},
        )
    dates = observation_dates(ts)
    final_index = dates.index(ts.final_review_date.value)
    perf = _example_perf(ts, verified)
    cash, error = _run_code(code, ts, perf)
    if error is not None or cash is None:
        return _failed(
            "filing_examples",
            "payoff run failed on the filing-example paths",
            error,
        )
    if cash.shape != perf.shape[:2]:
        return _failed(
            "filing_examples",
            f"cash shape {cash.shape} != expected {perf.shape[:2]}",
            None,
        )
    rows: list[dict] = []
    max_error = 0.0
    passed = True
    for i, example in enumerate(verified):
        expected = float(example.payment.value)
        got = float(cash[i, final_index])
        other_max = 0.0
        if cash.shape[1] > 1:
            other = np.delete(cash[i], final_index)
            other_max = float(np.max(np.abs(other))) if other.size else 0.0
        error_i = max(abs(got - expected), other_max)
        max_error = max(max_error, error_i)
        if error_i > tolerance:
            passed = False
        rows.append(
            {
                "worst_final": float(example.worst_final),
                "expected": expected,
                "got": got,
                "other_columns_max_abs": other_max,
            }
        )
    return CheckResult(
        name="filing_examples",
        passed=bool(passed),
        value=float(max_error),
        reference=0.0,
        tolerance=tolerance,
        detail=(
            f"{len(verified)} verified filing examples; cash at the final column must "
            "equal the cited payment within $0.01 and other columns must be 0"
        ),
        data={"rows": rows, "verified": int(len(verified))},
    )


def check_called_paths_match_template(
    code: str, ts: TermSheet, market: MarketInputs, n_paths: int, seed: int
) -> CheckResult:
    """On called paths the generated cash must match the trusted template.

    Formula: simulate ``perf``; the trusted call logic is the first autocall
    review index ``k`` (including the final review date if it is an autocall
    review too, as ``worst_of`` does) where ``min_i perf[p, k, i] >=
    trigger``.  On called paths, the generated row sum must equal
    ``worst_of.pathwise_cashflows(strip_unsupported(ts), dates,
    perf[called], lambda d: 1.0)`` (undiscounted totals, so the call amount
    at column ``k`` plus any coupons per the template), and columns ``> k``
    must be zero.

    Intuition: the new feature changes the maturity payoff only; called
    paths never reach maturity, so the trusted template is an exact
    reference there -- any difference is a bug in the generated code's call
    or coupon logic, not a modelling choice.

    Reference: filing terms; ``quantproof.payoffs.worst_of.pathwise_cashflows``.

    Tolerance: 1e-9 * notional on row sums (the comparison is pathwise on
    identical paths, so only floating-point rounding may differ); post-call
    columns must be exactly zero.  Only applies when ``ts.autocall`` is set.
    """
    notional = float(ts.notional.value)
    tolerance = 1e-9 * notional  # pathwise on identical paths; floating-point rounding only
    if ts.autocall is None:
        return CheckResult(
            name="called_paths_match_template",
            passed=True,
            value=0.0,
            reference=0.0,
            tolerance=tolerance,
            detail="skipped: the note has no autocall feature",
            data={"rows": [], "called_paths": 0},
        )
    perf = _simulate_perf(ts, market, n_paths, seed)
    cash, error = _run_code(code, ts, perf)
    if error is not None or cash is None:
        return _failed(
            "called_paths_match_template",
            f"payoff run failed (seed={seed}, n_paths={n_paths})",
            error,
        )
    dates = observation_dates(ts)
    if cash.shape != (int(n_paths), len(dates)):
        return _failed(
            "called_paths_match_template",
            f"cash shape {cash.shape} != expected {(int(n_paths), len(dates))}",
            None,
        )
    first_call = _trusted_first_call(ts, perf)
    # Only paths called strictly before the final review never reach the maturity payoff.
    called_mask = (first_call >= 0) & (first_call < dates.index(ts.final_review_date.value))
    called_count = int(np.sum(called_mask))
    if called_count == 0:  # no evidence is not a pass
        return CheckResult(
            name="called_paths_match_template",
            passed=False,
            value=0.0,
            reference=0.0,
            tolerance=tolerance,
            detail=f"no called paths in the simulation (seed={seed}, n_paths={n_paths})",
            data={"rows": [], "called_paths": 0},
        )
    template_pv = pathwise_cashflows(
        strip_unsupported(ts), dates, perf[called_mask], lambda d: 1.0
    )
    generated = cash[called_mask]
    ks = first_call[called_mask]
    row_sum_diff = np.abs(np.sum(generated, axis=1) - template_pv)
    max_diff = float(np.max(row_sum_diff)) if row_sum_diff.size else 0.0
    post_call_violations = 0
    for row in range(generated.shape[0]):
        k = int(ks[row])
        if k + 1 < generated.shape[1] and np.any(generated[row, k + 1 :] != 0.0):
            post_call_violations += 1
    passed = max_diff <= tolerance and post_call_violations == 0
    return CheckResult(
        name="called_paths_match_template",
        passed=bool(passed),
        value=max_diff,
        reference=0.0,
        tolerance=float(tolerance),
        detail=(
            f"{called_count} called paths (seed={seed}, n_paths={n_paths}); generated "
            "row sums must equal trusted template undiscounted totals within "
            "1e-9 * notional and columns after the call must be zero"
        ),
        data={
            "called_paths": called_count,
            "max_row_sum_diff": max_diff,
            "post_call_violations": int(post_call_violations),
        },
    )


def check_convergence(
    code: str, ts: TermSheet, market: MarketInputs, n_paths: int, seed: int
) -> CheckResult:
    """Monte Carlo standard error must shrink like 1/sqrt(n).

    Formula: ``price_cashflows`` at ``m, 4m, 16m`` paths with
    ``m = 2 * (n_paths // 32)`` and the same seed; each SE ratio
    ``se_{k+1} / se_k`` must lie in [0.4, 0.6] (quadrupling the paths should
    halve the SE: ``1/sqrt(4) = 0.5``), and consecutive prices must lie
    within 3 combined SE, ``sqrt(se_1^2 + se_2^2)``.

    Intuition: a payoff whose reported error does not shrink at the Monte
    Carlo rate, or whose price jumps by more than its own error bars when
    paths are added, cannot be trusted at any path count.

    Reference: Glasserman chapters 2 and 4 (Monte Carlo standard errors).

    Tolerances: [0.4, 0.6] around 0.5 for the SE ratios (sampling noise of
    the SE estimate itself); 3 combined SE for consecutive prices.
    """
    m = 2 * (n_paths // 32)
    if m <= 0:
        return _failed(
            "convergence",
            f"n_paths={n_paths} is too small for the convergence ladder",
            None,
        )
    counts = (m, 4 * m, 16 * m)
    rows: list[dict] = []
    prices: list[float] = []
    ses: list[float] = []
    try:
        cash_fn = cash_from_code(code)
        for count in counts:
            result = price_cashflows(
                ts, market, cash_fn, n_paths=int(count), seed=seed
            )
            prices.append(float(result.price))
            ses.append(float(result.std_error or 0.0))
            rows.append(
                {"paths": int(count), "price": float(result.price), "se": ses[-1]}
            )
    except Exception as exc:
        return _failed(
            "convergence",
            f"pricing failed (seed={seed})",
            f"{type(exc).__name__}: {exc}",
        )
    ratios = [
        ses[k + 1] / ses[k] if ses[k] > 0 else float("inf") for k in range(len(ses) - 1)
    ]
    # SE should halve when paths quadruple: 1/sqrt(4) = 0.5; [0.4, 0.6] is sampling noise of the SE estimate
    ratios_ok = all(0.4 <= ratio <= 0.6 for ratio in ratios)
    # Consecutive prices must agree within 3 combined SE: sqrt(se1^2 + se2^2)
    prices_ok = all(
        abs(prices[k + 1] - prices[k]) <= 3.0 * math.sqrt(ses[k] ** 2 + ses[k + 1] ** 2)
        for k in range(len(prices) - 1)
    )
    worst_ratio = max(ratios, key=lambda ratio: abs(ratio - 0.5)) if ratios else 0.5
    return CheckResult(
        name="convergence",
        passed=bool(ratios_ok and prices_ok),
        value=float(worst_ratio),
        reference=0.5,
        tolerance=0.1,
        detail=(
            f"price_cashflows at paths {counts} (seed={seed}); SE ratios must lie in "
            "[0.4, 0.6] and consecutive prices within 3 combined SE"
        ),
        data={"rows": rows, "ratios": [float(ratio) for ratio in ratios]},
    )


# --- Battery runner ----------------------------------------------------------------------------


def run_synth_battery(
    code: str,
    ts: TermSheet,
    market: MarketInputs,
    examples: list[PayoutExample],
    n_paths: int = 20_000,
    seed: int = 0,
    filing_text: str | None = None,
) -> list[CheckResult]:
    """Run every synthesized-payoff check; never run unsafe code.

    Formula: no single formula; this runs ``check_static_safety`` first and
    returns only that result when it fails (unsafe code is never executed),
    then determinism, output sanity, filing examples, called-paths template
    comparison (only when the note is autocallable) and convergence.

    Intuition: each check attacks a different way a generated payoff can be
    wrong -- unsafe, random, malformed, misreading the filing, misreading
    the call logic, or not converging -- so passing all of them is strong
    evidence the code prices the note it claims to price.

    Reference: filing terms; Hull chapters 21 and 26; Glasserman chapters 2-4.

    Neither ``ts`` nor ``market`` is mutated.
    """
    static = check_static_safety(code)
    if not static.passed:
        return [static]
    results: list[CheckResult] = [
        static,
        check_determinism(code, ts, market, n_paths, seed),
        check_output_sanity(code, ts, market, n_paths, seed),
        check_filing_examples(code, ts, examples, filing_text=filing_text),
    ]
    if ts.autocall is not None:
        results.append(check_called_paths_match_template(code, ts, market, n_paths, seed))
    results.append(check_convergence(code, ts, market, n_paths, seed))
    return results


def battery_passed(results: list[CheckResult]) -> bool:
    """Whether every check in a synthesized-payoff battery passed.

    Formula: ``all(r.passed for r in results)`` (an empty battery does not
    count as passed evidence, so it returns False for no results via the
    explicit non-empty guard below -- see detail).

    Intuition: a synthesized payoff enters the registry only on a clean
    sweep; there is no partial credit for unsafe or unverifiable code.

    Reference: project rule 4 (generated code enters the registry only after
    passing the full proof battery).
    """
    return bool(results) and all(result.passed for result in results)
