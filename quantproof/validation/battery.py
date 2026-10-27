"""Proof battery: independent checks that the Monte Carlo pricer is correct.

Every check compares a Monte Carlo price from
``quantproof.payoffs.worst_of.price_note`` against an independent closed form,
a limiting case, or a monotonicity property that must hold for economic reasons.
No check calibrates anything to a bank's estimated value.

Pure Python + NumPy + SciPy. No AI and no network calls in this module.

References: J. C. Hull, *Options, Futures, and Other Derivatives*, chapters
4 (discounting), 15 and 19 (Black-Scholes-Merton), 21 (simulation) and 26
(binary / barrier options); P. Glasserman, *Monte Carlo Methods in Financial
Engineering*, chapters 2-3 (correlated simulation and standard errors).
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field

from quantproof.core.market import MarketInputs
from quantproof.core.termsheet import (
    BarrierObservation,
    Cited,
    DownsideKind,
    TermSheet,
)
from quantproof.models.bsm import (
    call_price,
    digital_call_price,
    digital_put_price,
    put_price,
)
from quantproof.models.discounting import discount_factor, year_fraction
from quantproof.payoffs.worst_of import price_note


@dataclass
class CheckResult:
    """Outcome of one proof-battery check.

    ``value`` is an engine output (a price, or the worst standard-error ratio
    for the convergence check); ``reference`` is the independent closed form or
    expected value; ``tolerance`` is the allowed ``|value - reference|``.
    ``detail`` is one plain-English line saying what was compared and which
    inputs were changed. ``data`` holds rows for charts.
    """

    name: str
    passed: bool
    value: float | None
    reference: float | None
    tolerance: float | None
    detail: str
    data: dict = field(default_factory=dict)


# --- Private helpers --------------------------------------------------------------------------


def _single_asset(ts: TermSheet, market: MarketInputs, i: int = 0) -> tuple[TermSheet, MarketInputs]:
    """Copy of ``ts`` with only underlying ``i``, and the matching one-asset market.

    Formula: no formula; this selects asset ``i``'s volatility and dividend
    yield and keeps the same as-of date, risk-free rate and credit spread.

    Intuition: closed forms exist for one asset, so several checks strip a
    worst-of note down to its first underlying.

    Reference: filing terms (underlyings); Hull chapter 15.
    """
    ts_copy = copy.deepcopy(ts)
    ts_copy.underlyings = [copy.deepcopy(ts.underlyings[i])]
    market_copy = MarketInputs.single(
        as_of=market.as_of,
        risk_free_rate=market.risk_free_rate,
        vol=float(market.vols[i]),
        dividend_yield=float(market.dividend_yields[i]),
        credit_spread=market.credit_spread,
        sources=dict(market.sources),
    )
    return ts_copy, market_copy


def _strip(
    ts: TermSheet,
    autocall=None,
    coupon=None,
    upside=None,
    kind=None,
    level=None,
    observation=None,
    gearing=None,
) -> TermSheet:
    """Copy of ``ts`` with payoff features removed or replaced.

    ``autocall``, ``coupon`` and ``upside`` are assigned exactly as passed, so
    the default ``None`` removes that feature; pass an existing feature object
    to keep it. For the downside, a non-``None`` ``kind``, ``level``,
    ``observation`` or ``gearing`` replaces that field (wrapped in
    ``Cited(...)`` with no citation, because we set it ourselves); ``None``
    leaves that downside field unchanged.

    Intuition: turning features off one at a time reduces a note to a payoff
    with a known closed form, which is what makes the check independent.

    Reference: filing terms; Hull chapter 26.
    """
    ts_copy = copy.deepcopy(ts)
    ts_copy.autocall = autocall
    ts_copy.coupon = coupon
    ts_copy.upside = upside
    if kind is not None:
        ts_copy.downside.kind = Cited(kind)
    if level is not None:
        ts_copy.downside.level = Cited(float(level))
    if observation is not None:
        ts_copy.downside.observation = Cited(observation)
    if gearing is not None:
        ts_copy.downside.gearing = Cited(float(gearing))
    return ts_copy


def _market_with(
    market: MarketInputs,
    *,
    vols=None,
    dividend_yields=None,
    correlation=None,
    credit_spread=None,
) -> MarketInputs:
    """Copy of ``market`` with the named fields replaced (inputs are not mutated)."""
    return MarketInputs(
        as_of=market.as_of,
        risk_free_rate=market.risk_free_rate,
        vols=tuple(vols) if vols is not None else market.vols,
        dividend_yields=(
            tuple(dividend_yields) if dividend_yields is not None else market.dividend_yields
        ),
        correlation=(
            tuple(tuple(row) for row in correlation)
            if correlation is not None
            else market.correlation
        ),
        credit_spread=market.credit_spread if credit_spread is None else float(credit_spread),
        sources=dict(market.sources),
    )


def _times(ts: TermSheet) -> tuple[float, float]:
    """Return (T_r, T_p): pricing date to final review date, and to maturity date."""
    pricing = ts.pricing_date.value
    t_r = year_fraction(pricing, ts.final_review_date.value)
    t_p = year_fraction(pricing, ts.maturity_date.value)
    return t_r, t_p


def _df(market: MarketInputs, t: float) -> float:
    """DF(t) = discount_factor(t, r, spread) = exp(-(r + spread) t)."""
    return float(discount_factor(t, market.risk_free_rate, market.credit_spread))


def _barrier_at_maturity_setup(
    ts: TermSheet, market: MarketInputs
) -> tuple[TermSheet, MarketInputs, float, dict[str, float]]:
    """Single-asset barrier-at-maturity note plus its closed form and components.

    Formula: payoff at maturity is ``N * (1 - (1 - W) 1{W < K})`` with
    ``(1 - W) 1{W < K} = (K - W)+ + (1 - K) 1{W < K}``, so

        value = N * DF(T_p) * (1 - exp(r T_r) * [Put(K) + (1 - K) DigitalPut(K)])

    where Put and DigitalPut are Black-Scholes prices at ``S = 1`` (performance
    units), strike ``K``, expiry ``T_r``. The factor ``exp(r T_r)`` undoes the
    ``exp(-r T_r)`` inside those prices, turning them into risk-neutral
    expectations, which are then discounted at ``T_p`` with the credit spread.

    Intuition: below the barrier the investor loses ``1 - W``; that loss is a
    put struck at ``K`` plus a digital paying the gap ``1 - K``.

    Reference: Hull chapters 15, 19 and 26.
    """
    ts_single, market_single = _single_asset(ts, market, 0)
    level = float(ts.downside.level.value)
    ts_check = _strip(
        ts_single,
        autocall=None,
        coupon=None,
        upside=None,
        kind=DownsideKind.BARRIER,
        level=level,
        observation=BarrierObservation.FINAL_ONLY,
        gearing=1.0,
    )
    t_r, t_p = _times(ts_check)
    n = float(ts_check.notional.value)
    r = float(market_single.risk_free_rate)
    sigma = float(market_single.vols[0])
    q = float(market_single.dividend_yields[0])
    df_p = _df(market_single, t_p)
    bond = n * df_p
    # Components in cash, as negative numbers for the subtracted terms.
    put_component = -n * df_p * math.exp(r * t_r) * float(put_price(1.0, level, t_r, r, sigma, q))
    digital_component = -n * df_p * math.exp(r * t_r) * (1.0 - level) * float(
        digital_put_price(1.0, level, t_r, r, sigma, q, cash=1.0)
    )
    closed_form = bond + put_component + digital_component
    components = {
        "bond": bond,
        "put": put_component,
        "digital": digital_component,
        "closed_form": closed_form,
    }
    return ts_check, market_single, closed_form, components


# --- Black-Scholes checks (no term sheet needed) ----------------------------------------------


def check_bsm_known_values() -> CheckResult:
    """Black-Scholes prices against published textbook values.

    Formula: ``C = S e^{-qT} N(d1) - K e^{-rT} N(d2)`` and
    ``P = K e^{-rT} N(-d2) - S e^{-qT} N(-d1)`` with
    ``d1 = [ln(S/K) + (r - q + sigma^2/2) T] / (sigma sqrt(T))``,
    ``d2 = d1 - sigma sqrt(T)``, evaluated at S=42, K=40, r=0.10, sigma=0.20,
    T=0.5, q=0.

    Intuition: a call is the present value of receiving the stock when it ends
    above the strike minus paying the strike in those states; the put mirrors it.

    Reference: Hull chapter 15 (worked Black-Scholes example: call 4.76, put 0.81).

    Tolerance: 0.005, i.e. half a cent of rounding in the published two-decimal
    values, as stated in the task specification.
    """
    tolerance = 0.005  # published values are rounded to 2 decimals; 0.005 is that rounding error
    call = float(call_price(42.0, 40.0, 0.5, 0.10, 0.20, 0.0))
    put = float(put_price(42.0, 40.0, 0.5, 0.10, 0.20, 0.0))
    passed = abs(call - 4.76) <= tolerance and abs(put - 0.81) <= tolerance
    return CheckResult(
        name="bsm_known_values",
        passed=bool(passed),
        value=call,
        reference=4.76,
        tolerance=tolerance,
        detail="BSM call and put at S=42, K=40, r=0.10, sigma=0.20, T=0.5 compared with 4.76 and 0.81",
        data={"call": call, "put": put, "call_reference": 4.76, "put_reference": 0.81},
    )


def check_put_call_parity() -> CheckResult:
    """Put-call parity for European options on the same strike and expiry.

    Formula: ``C - P = S e^{-qT} - K e^{-rT}``, evaluated at S=42, K=40,
    r=0.10, sigma=0.20, T=0.5, q=0.03.

    Intuition: a call minus a put pays ``S_T - K`` in every state, exactly like
    holding the (dividend-adjusted) stock and owing discounted cash ``K``.

    Reference: Hull chapter 15 (put-call parity).

    Tolerance: 1e-10, because parity is an algebraic identity of the two
    closed-form formulas, so only floating-point rounding may separate them.
    """
    tolerance = 1e-10  # algebraic identity; only floating-point rounding is allowed
    s, k, t, r, sigma, q = 42.0, 40.0, 0.5, 0.10, 0.20, 0.03
    lhs = float(call_price(s, k, t, r, sigma, q)) - float(put_price(s, k, t, r, sigma, q))
    rhs = s * math.exp(-q * t) - k * math.exp(-r * t)
    return CheckResult(
        name="put_call_parity",
        passed=bool(abs(lhs - rhs) <= tolerance),
        value=lhs,
        reference=rhs,
        tolerance=tolerance,
        detail="C - P compared with S e^{-qT} - K e^{-rT} at S=42, K=40, r=0.10, sigma=0.20, T=0.5, q=0.03",
        data={"call_minus_put": lhs, "forward_value": rhs},
    )


# --- Monte Carlo checks ------------------------------------------------------------------------


def check_determinism(ts: TermSheet, market: MarketInputs, n_paths: int, seed: int) -> CheckResult:
    """Same seed, same price: the simulation is exactly reproducible.

    Formula: ``price_note(ts, market, n_paths, seed)`` evaluated twice; the two
    prices must be identical, ``|price_1 - price_2| = 0``.

    Intuition: a fixed seed fixes every random draw, so a reported price can
    always be recomputed by a reviewer.

    Reference: Glasserman chapters 2-3 (seeded simulation).

    Tolerance: 0, because identical seeds must give bit-identical output.
    """
    tolerance = 0.0  # identical seed must reproduce the price exactly
    first = price_note(ts, market, n_paths=n_paths, seed=seed)
    second = price_note(ts, market, n_paths=n_paths, seed=seed)
    return CheckResult(
        name="determinism",
        passed=bool(first.price == second.price),
        value=float(first.price),
        reference=float(second.price),
        tolerance=tolerance,
        detail=f"price_note run twice with seed={seed}, n_paths={n_paths}; prices must be identical",
        data={"first_price": float(first.price), "second_price": float(second.price), "seed": seed},
    )


def check_barrier_removed(ts: TermSheet, market: MarketInputs, n_paths: int, seed: int) -> CheckResult:
    """Buffered payoff with buffer level 1.0 against its closed form.

    Formula: single asset, autocall/coupon/upside removed, BUFFER level 1.0,
    gearing 1.0, so the maturity payoff is ``N * min(W_T, 1)`` and

        value = N * DF(T_p) * (1 - exp(r T_r) * Put(1, 1, T_r, r, sigma, q))

    since ``min(W, 1) = 1 - (1 - W)+`` and ``exp(r T_r) * Put`` is the
    risk-neutral expectation of ``(1 - W_T)+`` at ``S = 1``.

    Intuition: with the buffer at 100% the investor gives up every gain and
    bears every loss from the first cent, i.e. is short an at-the-money put.

    Reference: Hull chapters 15 and 26.

    Tolerance: 3 standard errors of the Monte Carlo price (stated in the task:
    a correct simulation lands within 3 SE of the closed form).
    """
    ts_single, market_single = _single_asset(ts, market, 0)
    ts_check = _strip(
        ts_single,
        autocall=None,
        coupon=None,
        upside=None,
        kind=DownsideKind.BUFFER,
        level=1.0,
        gearing=1.0,
    )
    t_r, t_p = _times(ts_check)
    n = float(ts_check.notional.value)
    r = float(market_single.risk_free_rate)
    sigma = float(market_single.vols[0])
    q = float(market_single.dividend_yields[0])
    closed_form = n * _df(market_single, t_p) * (
        1.0 - math.exp(r * t_r) * float(put_price(1.0, 1.0, t_r, r, sigma, q))
    )
    result = price_note(ts_check, market_single, n_paths=n_paths, seed=seed)
    se = float(result.std_error or 0.0)
    tolerance = 3.0 * se  # 3 Monte Carlo standard errors, per the task specification
    return CheckResult(
        name="barrier_removed",
        passed=bool(abs(result.price - closed_form) <= tolerance),
        value=float(result.price),
        reference=float(closed_form),
        tolerance=float(tolerance),
        detail="single asset, autocall/coupon/upside removed, BUFFER level 1.0 gearing 1.0; MC vs N*min(W,1) closed form",
        data={"mc": float(result.price), "se": se, "closed_form": float(closed_form)},
    )


def check_barrier_at_maturity(
    ts: TermSheet, market: MarketInputs, n_paths: int, seed: int
) -> CheckResult:
    """Barrier-at-maturity payoff against its put-plus-digital closed form.

    Formula: payoff ``N * (1 - (1 - W) 1{W < K})`` with
    ``(1 - W) 1{W < K} = (K - W)+ + (1 - K) 1{W < K}``, hence

        value = N * DF(T_p) * (1 - exp(r T_r) * [Put(K) + (1 - K) DigitalPut(K)])

    at ``S = 1``, expiry ``T_r`` (see ``_barrier_at_maturity_setup``).

    Intuition: principal is repaid unless the asset ends below the barrier, in
    which case the investor loses ``1 - W``: a put plus a digital for the gap.

    Reference: Hull chapters 15, 19 and 26.

    Tolerance: 3 standard errors of the Monte Carlo price.
    """
    ts_check, market_single, closed_form, components = _barrier_at_maturity_setup(ts, market)
    result = price_note(ts_check, market_single, n_paths=n_paths, seed=seed)
    se = float(result.std_error or 0.0)
    tolerance = 3.0 * se  # 3 Monte Carlo standard errors, per the task specification
    data = dict(components)
    data["mc"] = float(result.price)
    data["se"] = se
    return CheckResult(
        name="barrier_at_maturity",
        passed=bool(abs(result.price - closed_form) <= tolerance),
        value=float(result.price),
        reference=float(closed_form),
        tolerance=float(tolerance),
        detail="single asset, autocall/coupon/upside removed, BARRIER at downside level FINAL_ONLY gearing 1; MC vs put + digital closed form",
        data=data,
    )


def check_never_callable(ts: TermSheet, market: MarketInputs, n_paths: int, seed: int) -> CheckResult:
    """Note with the autocall trigger at 1e6 against barrier plus digital coupons.

    Formula: the maturity leg is the barrier-at-maturity closed form, and each
    coupon observation adds

        amount * DF(t_pay) * exp(r t_rev) * DigitalCall(1, B, t_rev, r, sigma, q, cash=1)

    where ``B`` is the coupon barrier and ``amount`` is cash per note (never
    times ``N``); ``exp(r t_rev) * DigitalCall`` is ``Q(W(t_rev) >= B)``.

    Intuition: a trigger of 1e6 can never be reached, so the note certainly
    survives to maturity and every coupon is an independent digital bet.

    Reference: Hull chapters 15 and 26 (binary options in structured notes).

    Tolerance: 3 standard errors of the Monte Carlo price.

    Applies only when the note has an autocall or a coupon (checked by
    ``run_battery``); the features changed are named in ``detail``.
    """
    ts_single, market_single = _single_asset(ts, market, 0)
    level = float(ts.downside.level.value)
    ts_check = copy.deepcopy(ts_single)
    changed: list[str] = []
    if ts_check.autocall is not None:
        ts_check.autocall.trigger = Cited(1e6)
        changed.append("autocall trigger set to 1e6 (never reached)")
    if ts_check.coupon is not None:
        ts_check.coupon.memory = Cited(False)
        changed.append("coupon memory set to False")
    ts_check.upside = None
    changed.append("upside removed")
    ts_check.downside.kind = Cited(DownsideKind.BARRIER)
    ts_check.downside.level = Cited(level)
    ts_check.downside.observation = Cited(BarrierObservation.FINAL_ONLY)
    ts_check.downside.gearing = Cited(1.0)
    changed.append("downside set to BARRIER at the term-sheet level, FINAL_ONLY, gearing 1")

    _, _, closed_form, components = _barrier_at_maturity_setup(ts, market)
    r = float(market_single.risk_free_rate)
    sigma = float(market_single.vols[0])
    q = float(market_single.dividend_yields[0])
    pricing = ts_check.pricing_date.value
    coupon_value = 0.0
    if ts_check.coupon is not None:
        amount = float(ts_check.coupon.amount.value)  # cash per note, never times N
        barrier = float(ts_check.coupon.barrier.value)
        for obs in ts_check.coupon.observations:
            t_rev = year_fraction(pricing, obs.review_date.value)
            t_pay = year_fraction(pricing, obs.payment_date.value)
            coupon_value += (
                amount
                * _df(market_single, t_pay)
                * math.exp(r * t_rev)
                * float(digital_call_price(1.0, barrier, t_rev, r, sigma, q, cash=1.0))
            )
    closed_form = closed_form + coupon_value

    result = price_note(ts_check, market_single, n_paths=n_paths, seed=seed)
    se = float(result.std_error or 0.0)
    tolerance = 3.0 * se  # 3 Monte Carlo standard errors, per the task specification
    data = dict(components)
    data.update({"coupon": coupon_value, "mc": float(result.price), "se": se,
                 "closed_form": float(closed_form)})
    return CheckResult(
        name="never_callable",
        passed=bool(abs(result.price - closed_form) <= tolerance),
        value=float(result.price),
        reference=float(closed_form),
        tolerance=float(tolerance),
        detail="single asset; " + "; ".join(changed) + "; MC vs barrier closed form plus digital coupons",
        data=data,
    )


def check_zero_coupon_no_downside(
    ts: TermSheet, market: MarketInputs, n_paths: int, seed: int
) -> CheckResult:
    """Barrier at 1e-6 is never breached, so the note is a zero-coupon bond.

    Formula: single asset, autocall/coupon/upside removed, BARRIER level 1e-6,
    gearing 1; the barrier cannot be reached in practice, so

        value = N * DF(T_p).

    Intuition: with no coupon, no upside and no realistic downside, all that
    remains is repayment of principal, discounted with the issuer's spread.

    Reference: Hull chapter 4 (discounting).

    Tolerance: max(3 SE, 1e-6 * N): the 3 SE Monte Carlo allowance, floored by
    a tiny absolute amount in case the simulated payoff is (near-)constant and
    the reported SE is (near-)zero.
    """
    ts_single, market_single = _single_asset(ts, market, 0)
    ts_check = _strip(
        ts_single,
        autocall=None,
        coupon=None,
        upside=None,
        kind=DownsideKind.BARRIER,
        level=1e-6,
        observation=BarrierObservation.FINAL_ONLY,
        gearing=1.0,
    )
    _, t_p = _times(ts_check)
    n = float(ts_check.notional.value)
    closed_form = n * _df(market_single, t_p)
    result = price_note(ts_check, market_single, n_paths=n_paths, seed=seed)
    se = float(result.std_error or 0.0)
    tolerance = max(3.0 * se, 1e-6 * n)  # 3 SE, floored at 1e-6 * N per the task specification
    return CheckResult(
        name="zero_coupon_no_downside",
        passed=bool(abs(result.price - closed_form) <= tolerance),
        value=float(result.price),
        reference=float(closed_form),
        tolerance=float(tolerance),
        detail="single asset, autocall/coupon/upside removed, BARRIER level 1e-6 (never breached); MC vs N*DF(T_p)",
        data={"mc": float(result.price), "se": se, "closed_form": float(closed_form)},
    )


def check_zero_vol(ts: TermSheet, market: MarketInputs, n_paths: int, seed: int) -> CheckResult:
    """Zero volatility makes performance deterministic: W = exp((r - q) T_r).

    Formula: with sigma = 1e-8 and ``S = 1``,
    ``W_T = exp((r - q - sigma^2/2) T_r) ~= exp((r - q) T_r)``. Case 1:
    ``q = r + 0.10`` gives ``W_T = exp(-0.10 T_r)``; when that is above the
    barrier ``K`` the payoff is ``N`` and the value is ``N * DF(T_p)`` (used
    only if ``exp(-0.10 T_r) > K``). Case 2: ``q = r + ln(1/w)/T_r`` with
    ``w = K/2`` gives ``W_T = w < K``, so the barrier payoff is ``N * w`` and
    the value is ``N * w * DF(T_p)``.

    Intuition: with no randomness there is nothing to simulate; the engine
    must reproduce the single deterministic path in both states of the world.

    Reference: Hull chapter 15 (deterministic limit of geometric Brownian motion).

    Tolerance: 1e-6 * N for each case, because the only error left is the
    1e-8 volatility actually used and floating-point rounding. ``passed``
    requires both cases (case 1 only when it applies); ``value``/``reference``
    report the second case.
    """
    ts_single, market_single = _single_asset(ts, market, 0)
    level = float(ts.downside.level.value)
    ts_check = _strip(
        ts_single,
        autocall=None,
        coupon=None,
        upside=None,
        kind=DownsideKind.BARRIER,
        level=level,
        observation=BarrierObservation.FINAL_ONLY,
        gearing=1.0,
    )
    t_r, t_p = _times(ts_check)
    n = float(ts_check.notional.value)
    r = float(market_single.risk_free_rate)
    df_p = _df(market_single, t_p)
    tolerance = 1e-6 * n  # deterministic limit; only the 1e-8 vol and rounding may differ

    cases: list[dict] = []
    all_passed = True

    w_above = math.exp(-0.10 * t_r)
    if w_above > level:
        market_case = MarketInputs.single(
            as_of=market_single.as_of,
            risk_free_rate=r,
            vol=1e-8,
            dividend_yield=r + 0.10,
            credit_spread=market_single.credit_spread,
            sources=dict(market_single.sources),
        )
        result_case = price_note(ts_check, market_case, n_paths=n_paths, seed=seed)
        expected = n * df_p
        case_passed = abs(result_case.price - expected) <= tolerance
        all_passed = all_passed and bool(case_passed)
        cases.append({"case": "above_barrier", "w": w_above, "price": float(result_case.price),
                      "reference": float(expected), "passed": bool(case_passed)})
    else:
        cases.append({"case": "above_barrier", "w": w_above, "price": None,
                      "reference": None, "passed": None,
                      "note": "skipped: exp(-0.10 T_r) is not above the barrier"})

    w = level / 2.0
    q_below = r + math.log(1.0 / w) / t_r
    market_below = MarketInputs.single(
        as_of=market_single.as_of,
        risk_free_rate=r,
        vol=1e-8,
        dividend_yield=q_below,
        credit_spread=market_single.credit_spread,
        sources=dict(market_single.sources),
    )
    result_below = price_note(ts_check, market_below, n_paths=n_paths, seed=seed)
    expected_below = n * w * df_p
    below_passed = abs(result_below.price - expected_below) <= tolerance
    all_passed = all_passed and bool(below_passed)
    cases.append({"case": "below_barrier", "w": w, "price": float(result_below.price),
                  "reference": float(expected_below), "passed": bool(below_passed)})

    return CheckResult(
        name="zero_vol",
        passed=bool(all_passed),
        value=float(result_below.price),
        reference=float(expected_below),
        tolerance=float(tolerance),
        detail="single asset, vol 1e-8, autocall/coupon/upside removed, BARRIER at downside level gearing 1; q=r+0.10 (above barrier, if applicable) and q set for W=K/2 (below barrier)",
        data={"cases": cases, "se": float(result_below.std_error or 0.0)},
    )


def check_correlation_one(ts: TermSheet, market: MarketInputs, n_paths: int, seed: int) -> CheckResult:
    """Two perfectly correlated copies of asset 0 price like one copy.

    Formula: no closed form; compare ``price_note`` on underlying 0 duplicated
    into two identical assets (same vol and dividend, correlation
    ``[[1, 1], [1, 1]]``, all other term-sheet features kept) with the
    single-asset (N=1) note with the same features.

    Intuition: the worst of two identical performances is that performance, so
    adding a perfect copy cannot change the payoff on any path.

    Reference: Hull chapter 26 (worst-of payoffs); Glasserman chapter 3.

    Tolerance: 3 * sqrt(se_1^2 + se_2^2), the standard error of the difference
    of two independent Monte Carlo prices (independent draws), times 3.

    Applies only when ``ts.n_underlyings >= 2`` (checked by ``run_battery``).
    """
    ts_one, market_one = _single_asset(ts, market, 0)
    result_one = price_note(ts_one, market_one, n_paths=n_paths, seed=seed)

    ts_two = copy.deepcopy(ts_one)
    ts_two.underlyings = [copy.deepcopy(ts_one.underlyings[0]), copy.deepcopy(ts_one.underlyings[0])]
    # All other ts features are kept from the single-asset copy, which itself
    # kept every feature of the caller's term sheet.
    market_two = MarketInputs(
        as_of=market.as_of,
        risk_free_rate=market.risk_free_rate,
        vols=(float(market.vols[0]), float(market.vols[0])),
        dividend_yields=(float(market.dividend_yields[0]), float(market.dividend_yields[0])),
        correlation=((1.0, 1.0), (1.0, 1.0)),
        credit_spread=market.credit_spread,
        sources=dict(market.sources),
    )
    result_two = price_note(ts_two, market_two, n_paths=n_paths, seed=seed + 1)

    se_one = float(result_one.std_error or 0.0)
    se_two = float(result_two.std_error or 0.0)
    tolerance = 3.0 * math.sqrt(se_one**2 + se_two**2)  # 3 SE of the difference, independent draws
    return CheckResult(
        name="correlation_one",
        passed=bool(abs(result_two.price - result_one.price) <= tolerance),
        value=float(result_two.price),
        reference=float(result_one.price),
        tolerance=float(tolerance),
        detail="underlying 0 duplicated into two identical assets with correlation [[1,1],[1,1]], all features kept; compared with the N=1 note",
        data={"single_price": float(result_one.price), "single_se": se_one,
              "duplicated_price": float(result_two.price), "duplicated_se": se_two},
    )


def check_correlation_monotonicity(
    ts: TermSheet, market: MarketInputs, n_paths: int, seed: int
) -> CheckResult:
    """Worst-of value does not fall when correlation rises.

    Formula: no closed form; price the note with uniform off-diagonal
    correlation rho in (0.0, 0.25, 0.5, 0.75, 0.95), same seed for every rho
    (common random numbers). Pass if each price >= previous price - 2 * se of
    that (the current) price.

    Intuition: higher correlation makes the underlyings fall together, so the
    worst of them is less likely to be far below the others; a short worst-of
    downside position is therefore worth more.

    Reference: Hull chapter 26 (worst-of / rainbow payoffs).

    Tolerance: 2 SE of the current price per step; common random numbers make
    price differences far more precise than the SE, so 2 SE is a lenient floor
    (as stated in the task).

    Applies only when ``ts.n_underlyings >= 2`` (checked by ``run_battery``).
    """
    n = ts.n_underlyings
    rows: list[dict] = []
    prices: list[float] = []
    ses: list[float] = []
    for rho in (0.0, 0.25, 0.5, 0.75, 0.95):
        correlation = tuple(
            tuple(1.0 if i == j else float(rho) for j in range(n)) for i in range(n)
        )
        market_rho = _market_with(market, correlation=correlation)
        result = price_note(ts, market_rho, n_paths=n_paths, seed=seed)
        se = float(result.std_error or 0.0)
        prices.append(float(result.price))
        ses.append(se)
        rows.append({"rho": float(rho), "price": float(result.price), "se": se})
    # 2 SE is a lenient floor: common random numbers make differences far more precise than SE
    passed = all(prices[k] >= prices[k - 1] - 2.0 * ses[k] for k in range(1, len(prices)))
    return CheckResult(
        name="correlation_monotonicity",
        passed=bool(passed),
        value=prices[-1],
        reference=prices[0],
        tolerance=2.0 * ses[-1],
        detail="uniform off-diagonal rho in (0.0, 0.25, 0.5, 0.75, 0.95), same seed (common random numbers); each price must be >= previous - 2 SE",
        data={"rows": rows},
    )


def check_vol_monotonicity(ts: TermSheet, market: MarketInputs, n_paths: int, seed: int) -> CheckResult:
    """Short-put notes lose value when volatility rises.

    Formula: no closed form; price with all vols scaled by (0.8, 1.0, 1.2),
    same seed for every scale. Pass if each price <= previous price + 2 * se of
    that (the current) price.

    Intuition: with no upside leg the note is short downside optionality, so
    more volatility raises the value of what the investor has sold.

    Reference: Hull chapters 19 (vega) and 26.

    Tolerance: 2 SE of the current price per step (common random numbers; a
    lenient floor, as for correlation monotonicity).

    Applies only when ``ts.upside is None`` (checked by ``run_battery``).
    """
    rows: list[dict] = []
    prices: list[float] = []
    ses: list[float] = []
    for scale in (0.8, 1.0, 1.2):
        market_scaled = _market_with(market, vols=tuple(v * scale for v in market.vols))
        result = price_note(ts, market_scaled, n_paths=n_paths, seed=seed)
        se = float(result.std_error or 0.0)
        prices.append(float(result.price))
        ses.append(se)
        rows.append({"scale": float(scale), "factor": float(scale), "price": float(result.price), "se": se})
    passed = all(prices[k] <= prices[k - 1] + 2.0 * ses[k] for k in range(1, len(prices)))
    return CheckResult(
        name="vol_monotonicity",
        passed=bool(passed),
        value=prices[-1],
        reference=prices[0],
        tolerance=2.0 * ses[-1],
        detail="all vols scaled by (0.8, 1.0, 1.2), same seed; each price must be <= previous + 2 SE (short-put note)",
        data={"rows": rows},
    )


def check_spread_monotonicity(
    ts: TermSheet, market: MarketInputs, n_paths: int, seed: int
) -> CheckResult:
    """Higher issuer credit spread strictly lowers the note's value.

    Formula: ``DF(t) = exp(-(r + s) t)``; price with credit spread increased by
    (0, 0.005, 0.01), same seed. Every cash flow is discounted harder, so the
    prices must be strictly decreasing.

    Intuition: the note is the issuer's unsecured debt; a wider spread is a
    bigger discount on every promised payment.

    Reference: Hull chapter 4 (credit spreads in discounting).

    Tolerance: none beyond strict decrease (tolerance 0); the same seed makes
    the discounting effect exact on identical paths.
    """
    rows: list[dict] = []
    prices: list[float] = []
    for added in (0.0, 0.005, 0.01):
        market_spread = _market_with(market, credit_spread=market.credit_spread + added)
        result = price_note(ts, market_spread, n_paths=n_paths, seed=seed)
        prices.append(float(result.price))
        rows.append({"spread": float(market.credit_spread + added), "added": float(added),
                     "price": float(result.price), "se": float(result.std_error or 0.0)})
    passed = all(prices[k] < prices[k - 1] for k in range(1, len(prices)))
    return CheckResult(
        name="spread_monotonicity",
        passed=bool(passed),
        value=prices[-1],
        reference=prices[0],
        tolerance=0.0,
        detail="credit spread + (0, 0.005, 0.01), same seed; prices must be strictly decreasing",
        data={"rows": rows},
    )


def check_coupon_monotonicity(
    ts: TermSheet, market: MarketInputs, n_paths: int, seed: int
) -> CheckResult:
    """A larger coupon amount strictly raises the note's value.

    Formula: no closed form; price with the coupon amount multiplied by
    (1.0, 1.5), same seed. Coupon cash flows scale up and nothing else changes,
    so the prices must be strictly increasing.

    Intuition: the investor is long the coupon leg; paying 1.5x on the same
    paths pays more on every path that pays at all.

    Reference: filing terms (contingent coupon); Hull chapter 26.

    Tolerance: none beyond strict increase (tolerance 0); the same seed makes
    the comparison on identical paths.

    Applies only when the note has a coupon (checked by ``run_battery``).
    """
    if ts.coupon is None:
        raise ValueError("coupon_monotonicity requires a coupon")
    rows: list[dict] = []
    prices: list[float] = []
    base_amount = float(ts.coupon.amount.value)
    for factor in (1.0, 1.5):
        ts_scaled = copy.deepcopy(ts)
        ts_scaled.coupon.amount = Cited(base_amount * factor)
        result = price_note(ts_scaled, market, n_paths=n_paths, seed=seed)
        prices.append(float(result.price))
        rows.append({"factor": float(factor), "amount": base_amount * factor,
                     "price": float(result.price), "se": float(result.std_error or 0.0)})
    passed = all(prices[k] > prices[k - 1] for k in range(1, len(prices)))
    return CheckResult(
        name="coupon_monotonicity",
        passed=bool(passed),
        value=prices[-1],
        reference=prices[0],
        tolerance=0.0,
        detail="coupon amount x (1.0, 1.5), same seed; prices must be strictly increasing",
        data={"rows": rows},
    )


def check_convergence(ts: TermSheet, market: MarketInputs, n_paths: int, seed: int) -> CheckResult:
    """Monte Carlo error shrinks like 1/sqrt(n) towards the closed form.

    Formula: barrier-at-maturity setup priced at
    ``n in (n_paths//16, n_paths//4, n_paths)``. Each price must lie within
    3 SE of the closed form, and each ratio ``se_{k+1} / se_k`` must lie in
    [0.4, 0.6], because quadrupling the paths should halve the SE
    (``1/sqrt(4) = 0.5``).

    Intuition: the standard error of a Monte Carlo mean falls with the square
    root of the number of paths; this checks the reported SE is honest.

    Reference: Glasserman chapters 2 and 4 (Monte Carlo standard errors).

    Tolerances: 3 SE for the prices; +-0.1 around 0.5 for the SE ratios, the
    sampling noise of the SE estimate itself (per the task). ``value`` is the
    worst ratio (furthest from 0.5), ``reference`` 0.5, ``tolerance`` 0.1.
    """
    ts_check, market_single, closed_form, _ = _barrier_at_maturity_setup(ts, market)
    counts = (n_paths // 16, n_paths // 4, n_paths)
    rows: list[dict] = []
    prices: list[float] = []
    ses: list[float] = []
    for count in counts:
        result = price_note(ts_check, market_single, n_paths=int(count), seed=seed)
        se = float(result.std_error or 0.0)
        prices.append(float(result.price))
        ses.append(se)
        rows.append({"paths": int(count), "price": float(result.price), "se": se,
                     "closed_form": float(closed_form)})
    ratios = [ses[k + 1] / ses[k] if ses[k] > 0 else float("inf") for k in range(len(ses) - 1)]
    # SE should halve when paths quadruple: 1/sqrt(4) = 0.5; +-0.1 is sampling noise of the SE estimate
    prices_ok = all(abs(prices[k] - closed_form) <= 3.0 * ses[k] for k in range(len(prices)))  # 3 SE
    ratios_ok = all(0.4 <= ratio <= 0.6 for ratio in ratios)
    worst_ratio = max(ratios, key=lambda ratio: abs(ratio - 0.5)) if ratios else 0.5
    return CheckResult(
        name="convergence",
        passed=bool(prices_ok and ratios_ok),
        value=float(worst_ratio),
        reference=0.5,
        tolerance=0.1,
        detail="barrier_at_maturity setup at n_paths//16, n_paths//4, n_paths; each price within 3 SE of closed form and each SE ratio in [0.4, 0.6]",
        data={"rows": rows, "ratios": [float(ratio) for ratio in ratios]},
    )


def check_quotes(ts: TermSheet, filing_text: str) -> CheckResult:
    """Every extracted term must be backed by a verbatim filing quote.

    Formula: no pricing formula; ``verify_termsheet(ts, filing_text)`` from
    ``quantproof.validation.quotes`` confirms each citation appears verbatim
    in the normalised filing text. Pass if every QuoteCheck is verified.

    Intuition: a number without a quote that can be found in the filing cannot
    be trusted or shown to a user silently (project rule 5a).

    Reference: project brief section 6 and AGENTS.md rule 5a (verified quotes).
    """
    # Imported here so the pricing checks above never depend on the ingest side.
    from quantproof.validation.quotes import verify_termsheet

    quote_checks = verify_termsheet(ts, filing_text)
    unverified = [c.path for c in quote_checks if not c.verified]
    verified_count = len(quote_checks) - len(unverified)
    total = len(quote_checks)
    return CheckResult(
        name="quotes",
        passed=bool(verified_count == total),
        value=float(verified_count),
        reference=float(total),
        tolerance=0.0,
        detail=f"{verified_count}/{total} verified",
        data={"unverified": unverified, "verified": verified_count, "total": total},
    )


# --- Battery runner ----------------------------------------------------------------------------


def run_battery(
    ts: TermSheet,
    market: MarketInputs,
    n_paths: int = 100_000,
    seed: int = 0,
    filing_text: str | None = None,
) -> list[CheckResult]:
    """Run every applicable proof check on derived copies of ``ts``.

    Formula: no single formula; this runs the ``check_*`` functions above, each
    of which compares ``price_note`` output with a closed form, a limiting
    case, or a monotonicity property (3 SE tolerances unless a check states
    otherwise).

    Intuition: a pricer that reproduces textbook prices, parity, deterministic
    limits, closed-form barrier values and every economic monotonicity is very
    unlikely to be wrong in a way that matters.

    Reference: Hull chapters 4, 15, 19, 21 and 26; Glasserman chapters 2-4.

    Applicability: correlation checks only if ``ts.n_underlyings >= 2``;
    never_callable only if ``ts.autocall or ts.coupon``; coupon_monotonicity
    only if ``ts.coupon``; vol_monotonicity only if ``ts.upside is None``
    (short-put notes); quotes only if ``filing_text`` is given. The caller's
    ``ts`` is never mutated: checks work on ``copy.deepcopy`` copies (via the
    helpers above) and ``price_note`` does not mutate its inputs.
    """
    results: list[CheckResult] = [
        check_bsm_known_values(),
        check_put_call_parity(),
        check_determinism(ts, market, n_paths, seed),
        check_barrier_removed(ts, market, n_paths, seed),
        check_barrier_at_maturity(ts, market, n_paths, seed),
    ]
    if ts.autocall is not None or ts.coupon is not None:
        results.append(check_never_callable(ts, market, n_paths, seed))
    results.append(check_zero_coupon_no_downside(ts, market, n_paths, seed))
    results.append(check_zero_vol(ts, market, n_paths, seed))
    if ts.n_underlyings >= 2:
        results.append(check_correlation_one(ts, market, n_paths, seed))
        results.append(check_correlation_monotonicity(ts, market, n_paths, seed))
    if ts.upside is None:
        results.append(check_vol_monotonicity(ts, market, n_paths, seed))
    results.append(check_spread_monotonicity(ts, market, n_paths, seed))
    if ts.coupon is not None:
        results.append(check_coupon_monotonicity(ts, market, n_paths, seed))
    results.append(check_convergence(ts, market, n_paths, seed))
    if filing_text is not None:
        results.append(check_quotes(ts, filing_text))
    return results
