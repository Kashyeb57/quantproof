"""Synthesized payoff pricing: terms view, sandbox cash wrapper, Monte Carlo price.

This module prices agent-written payoffs without ever executing agent code in
the host process.  The untrusted code only ever runs through
``quantproof.registry.sandbox.run_payoff`` (a subprocess with a timeout, no
network and an import allow-list); this module supplies the trusted terms
dictionary the code sees, and the trusted risk-neutral simulation and
discounting that turn its undiscounted cash amounts into a price.

Pure Python + NumPy.  No AI and no network calls in this module.

References: filing terms (the TermSheet); J. C. Hull, *Options, Futures, and
Other Derivatives*, chapters 4 (discounting), 21 (simulation) and 26 (exotic
payoffs); P. Glasserman, *Monte Carlo Methods in Financial Engineering*,
chapters 2-3 (correlated normals, antithetic variates, standard errors).
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from datetime import date

import numpy as np

from quantproof.core.market import MarketInputs
from quantproof.core.result import Measure, PricingResult
from quantproof.core.termsheet import TermSheet
from quantproof.models.discounting import discount_factor, year_fraction
from quantproof.models.montecarlo import mc_mean_and_se, simulate_performance
from quantproof.payoffs.worst_of import observation_dates
from quantproof.registry.sandbox import SandboxError, run_payoff


def payment_dates(ts: TermSheet, dates: list[date]) -> list[date]:
    """Payment date for each observation (column) date, aligned with ``dates``.

    Formula: for each date ``d`` in ``dates``: if ``d`` is an autocall review
    date, the payment date of that autocall observation; else if ``d`` is a
    coupon review date, the payment date of that coupon observation; else if
    ``d`` is the final review date, the maturity date; any other date is an
    error (observation dates are built from exactly these dates).

    Intuition: a synthesized payoff returns one cash amount per observation
    column, so each column needs exactly one date on which that cash is paid
    and therefore discounted from.  A contractual payment date set by a review
    takes precedence, and the final fixing is paid at maturity.

    Reference: filing terms (Review Dates, Payment Dates, Maturity Date).

    The term sheet is only read, never mutated.
    """
    autocall_map = {}
    if ts.autocall is not None:
        for obs in ts.autocall.observations:
            autocall_map[obs.review_date.value] = obs.payment_date.value
    coupon_map = {}
    if ts.coupon is not None:
        for obs in ts.coupon.observations:
            coupon_map[obs.review_date.value] = obs.payment_date.value
    final = ts.final_review_date.value
    maturity = ts.maturity_date.value

    result: list[date] = []
    for d in dates:
        if d in autocall_map:
            result.append(autocall_map[d])
        elif d in coupon_map:
            result.append(coupon_map[d])
        elif d == final:
            result.append(maturity)
        else:
            raise ValueError(f"observation date {d} is not a review or final date")
    return result


def terms_from_termsheet(ts: TermSheet) -> dict:
    """Plain JSON-serialisable view of the note for synthesized payoff code.

    Formula: no pricing formula; this flattens the cited term sheet into
    values only (no quotes, no citations), with every per-date list aligned
    with ``observation_dates`` so column ``k`` of the performance array, the
    cash array, ``observation_dates``, ``payment_dates`` and the amount lists
    all refer to the same review date.

    Intuition: this dictionary is the payoff code's whole view of the note.
    Keeping it values-only and JSON-serialisable means it can cross the
    sandbox boundary unchanged and the code cannot depend on anything that
    is not in here.

    Reference: filing terms; the sandbox contract in
    ``quantproof/registry/sandbox.py``.

    Keys:
      notional: float, cash per note (usually 1000).
      issue_price: float, price paid per note at issue.
      n_assets: int, number of underlyings.
      underlyings: list of str, underlying names.
      observation_dates: list of str, ISO dates, sorted; column order.
      payment_dates: list of str, ISO dates aligned with observation_dates
        (see ``payment_dates``).
      final_index: int, index of the final review date in observation_dates.
      autocall: None, or dict with ``trigger`` (float, fraction of initial)
        and ``call_amounts`` (list aligned with observation_dates; the cash
        call amount per note on that review date, or None where the date is
        not an autocall review date).
      coupon: None, or dict with ``barrier`` (float, fraction of initial),
        ``memory`` (bool) and ``amounts`` (list aligned like call_amounts;
        the coupon amount per note on that review date, or None elsewhere).
      downside: dict with ``kind`` ("barrier"/"buffer"), ``level`` (float,
        fraction of initial), ``gearing`` (float) and ``observation``
        ("final_only"/"each_review"/"daily").
      upside: None, or dict with ``leverage`` (float) and ``cap`` (float
        maximum return as a fraction, or None when uncapped).
      unsupported_features: list of str, payoff features the term-sheet
        schema could not hold (empty for template-supported notes).

    The term sheet is only read, never mutated.
    """
    dates = observation_dates(ts)
    pays = payment_dates(ts, dates)
    final = ts.final_review_date.value
    final_index = dates.index(final)

    autocall = None
    if ts.autocall is not None:
        by_date = {
            obs.review_date.value: float(obs.call_amount.value)
            for obs in ts.autocall.observations
        }
        autocall = {
            "trigger": float(ts.autocall.trigger.value),
            "call_amounts": [by_date.get(d) for d in dates],
        }

    coupon = None
    if ts.coupon is not None:
        coupon_dates = {obs.review_date.value for obs in ts.coupon.observations}
        amount = float(ts.coupon.amount.value)
        coupon = {
            "barrier": float(ts.coupon.barrier.value),
            "memory": bool(ts.coupon.memory.value),
            "amounts": [amount if d in coupon_dates else None for d in dates],
        }

    upside = None
    if ts.upside is not None:
        upside = {
            "leverage": float(ts.upside.leverage.value),
            "cap": float(ts.upside.cap.value) if ts.upside.cap is not None else None,
        }

    return {
        "notional": float(ts.notional.value),
        "issue_price": float(ts.issue_price.value),
        "n_assets": int(ts.n_underlyings),
        "underlyings": [u.name.value for u in ts.underlyings],
        "observation_dates": [d.isoformat() for d in dates],
        "payment_dates": [d.isoformat() for d in pays],
        "final_index": int(final_index),
        "autocall": autocall,
        "coupon": coupon,
        "downside": {
            "kind": ts.downside.kind.value.value,  # "barrier"/"buffer", not "DownsideKind.BARRIER"
            "level": float(ts.downside.level.value),
            "gearing": float(ts.downside.gearing.value),
            "observation": ts.downside.observation.value.value,
        },
        "upside": upside,
        "unsupported_features": [str(c.value) for c in ts.unsupported_features],
    }


def strip_unsupported(ts: TermSheet) -> TermSheet:
    """Copy of ``ts`` with ``unsupported_features`` cleared.

    Formula: ``deepcopy(ts)`` with ``unsupported_features = []``.

    Intuition: this is the template note "without the new feature".  It is
    used only as a reference on paths the new feature cannot touch (for
    example paths called before maturity, where a maturity-only feature
    never pays), so the trusted template is an exact reference there.

    Reference: filing terms; ``quantproof.payoffs.worst_of.pathwise_cashflows``.

    The caller's term sheet is never mutated.
    """
    ts_copy = copy.deepcopy(ts)
    ts_copy.unsupported_features = []
    return ts_copy


def cash_from_code(code: str) -> Callable[[np.ndarray, dict], np.ndarray]:
    """Callable cash function for agent-written payoff ``code``.

    Formula: ``cash(perf, terms) = run_payoff(code, perf, terms).cash``,
    raising ``SandboxError(result.error)`` when the sandbox result is not ok.

    Intuition: agent-written code is untrusted, so it only ever runs through
    ``registry.sandbox.run_payoff`` (subprocess, timeout, no network, import
    allow-list); this wrapper just adapts that checked result to the plain
    callable the pricing engine expects, and never swallows a failure.

    Reference: the payoff contract in ``quantproof/registry/sandbox.py``.
    """

    def _cash(perf: np.ndarray, terms: dict) -> np.ndarray:
        result = run_payoff(code, perf, terms)
        if not result.ok or result.cash is None:
            raise SandboxError(result.error or "sandbox run failed")
        return result.cash

    return _cash


def price_cashflows(
    ts: TermSheet,
    market: MarketInputs,
    cash_fn: Callable[[np.ndarray, dict], np.ndarray],
    n_paths: int = 100_000,
    seed: int = 0,
    antithetic: bool = True,
    model: str = "synthesized",
) -> PricingResult:
    """Risk-neutral Monte Carlo value of a note priced by synthesized cashflows.

    Formula: with ``dates = observation_dates(ts)`` and payment dates
    ``pay_k = payment_dates(ts, dates)``,

        times[k] = year_fraction(pricing_date, dates[k]);
        perf = simulate_performance(times, vols, dividend_yields, correlation,
                                    drifts = (r,) * N, n_paths, seed, antithetic);
        cash = cash_fn(perf, terms_from_termsheet(ts));
        DF(k) = discount_factor(year_fraction(pricing_date, pay_k), r, spread);
        pv_p = sum_k cash[p, k] * DF(k);
        mean, se = mc_mean_and_se(pv, antithetic).

    Intuition: the trusted engine owns the simulation (every asset drifts at
    the risk-free rate r under the risk-neutral measure) and the discounting
    (at r plus the issuer credit spread, because the note is the issuer's
    unsecured debt); the synthesized code only decides how much cash each
    simulated path pays on each date.  This is the same simulation as
    ``worst_of.price_note``, with the template payoff replaced by ``cash_fn``.

    Reference: filing terms; Hull chapters 4 and 21; Glasserman chapters 2-3.

    Raises ValueError if ``market.as_of`` is not the pricing date or the
    market vols do not match the number of underlyings.  Neither ``ts`` nor
    ``market`` is mutated.  The result records the full terms dictionary and
    market inputs, plus seed, number of paths and standard error.
    """
    pricing_date = ts.pricing_date.value
    if market.as_of != pricing_date:
        raise ValueError("inputs must be dated as of the pricing date")
    if len(market.vols) != ts.n_underlyings:
        raise ValueError(
            f"need {ts.n_underlyings} vols for {ts.n_underlyings} underlyings, "
            f"got {len(market.vols)}"
        )
    dates = observation_dates(ts)
    pays = payment_dates(ts, dates)
    times = [year_fraction(pricing_date, d) for d in dates]
    rate = float(market.risk_free_rate)
    spread = float(market.credit_spread)
    perf = simulate_performance(
        times,
        market.vols,
        market.dividend_yields,
        np.asarray(market.correlation, dtype=float),
        drifts=(rate,) * ts.n_underlyings,
        n_paths=n_paths,
        seed=seed,
        antithetic=antithetic,
    )
    terms = terms_from_termsheet(ts)
    cash = np.asarray(cash_fn(perf, terms), dtype=float)
    if cash.shape != (perf.shape[0], len(dates)):
        raise ValueError(
            f"cash shape {cash.shape} != expected {(perf.shape[0], len(dates))}"
        )
    dfs = np.array(
        [
            float(discount_factor(year_fraction(pricing_date, pay), rate, spread))
            for pay in pays
        ],
        dtype=float,
    )
    pv = cash @ dfs
    mean, se = mc_mean_and_se(pv, antithetic)
    return PricingResult(
        price=mean,
        measure=Measure.RISK_NEUTRAL,
        model=model,
        seed=seed,
        paths=int(n_paths),
        std_error=se,
        inputs={
            "terms": terms,
            "rate": rate,
            "spread": spread,
            "vols": market.vols,
            "dividend_yields": market.dividend_yields,
            "correlation": market.correlation,
            "n_dates": len(dates),
            "antithetic": antithetic,
        },
    )
