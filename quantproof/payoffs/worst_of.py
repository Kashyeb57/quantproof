"""Worst-of payoff template: autocallable / coupon note with barrier or buffer downside.

One template covers the worst-of autocallable (call premiums or contingent coupons)
and the worst-of buffered / barrier note at maturity. N = 1 is a single-asset note.

Pure Python + NumPy. No AI and no network calls in this module.

References: filing terms (the TermSheet); J. C. Hull, *Options, Futures, and Other
Derivatives*, chapter 26 (exotic options, barrier payoffs); P. Glasserman,
*Monte Carlo Methods in Financial Engineering* (risk-neutral simulation).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import numpy as np

from quantproof.core.market import MarketInputs
from quantproof.core.result import Measure, PricingResult
from quantproof.core.termsheet import BarrierObservation, DownsideKind, TermSheet
from quantproof.models.discounting import discount_factor, year_fraction
from quantproof.models.montecarlo import mc_mean_and_se, simulate_performance


class UnsupportedTermSheet(ValueError):
    """Raised when a TermSheet has features this template cannot price."""


def check_supported(ts: TermSheet) -> None:
    """Raise UnsupportedTermSheet if this template cannot price ``ts``.

    Formula: supported iff ``unsupported_features`` is empty and
    ``downside.observation`` is not DAILY.

    Intuition: the template only knows coupon / autocall / barrier-or-buffer
    features; anything the extractor could not fit in the schema, and daily
    barrier monitoring, need a different pricer.

    Reference: filing terms; Hull chapter 26 (barrier monitoring frequency).

    Raises UnsupportedTermSheet with a message naming the reason.
    """
    if ts.unsupported_features:
        values = [c.value for c in ts.unsupported_features]
        raise UnsupportedTermSheet(f"unsupported features: {values}")
    obs = ts.downside.observation.value
    if obs == BarrierObservation.DAILY:
        raise UnsupportedTermSheet(
            "daily barrier observation (DAILY) is not supported; "
            "only FINAL_ONLY and EACH_REVIEW are supported"
        )


def observation_dates(ts: TermSheet) -> list[date]:
    """Sorted unique union of coupon review dates, autocall review dates, final review date.

    Formula: dates = sort(unique({coupon reviews} union {autocall reviews} union {final})).

    Intuition: the simulation only needs the worst-of level on dates where
    something can happen (a coupon, a call, or the final fixing).

    Reference: filing terms (Review Dates, final Review Date).

    Raises ValueError if any review date is after the final review date.
    """
    final = ts.final_review_date.value
    collected: set[date] = {final}
    if ts.coupon is not None:
        for o in ts.coupon.observations:
            collected.add(o.review_date.value)
    if ts.autocall is not None:
        for o in ts.autocall.observations:
            collected.add(o.review_date.value)
    for d in collected:
        if d > final:
            raise ValueError(f"review date {d} is after final review date {final}")
    return sorted(collected)


def pathwise_cashflows(
    ts: TermSheet,
    dates: list[date],
    perf: np.ndarray,
    discount: Callable[[date], float],
) -> np.ndarray:
    """Present value of every cash flow on each path, in currency per note.

    Formula: W(t) = min_i R_i(t) with R_i = S_i / S_i(0). For each path keep
    ``alive`` (not yet called) and ``missed`` (coupons missed, for memory).
    Walk the dates in order; on each date, while alive:

    1. coupon (if t is a coupon review date): if W >= coupon.barrier pay
       amount * (1 + missed if memory else 1) at that observation's payment_date,
       reset missed to 0, else missed += 1. (amount is cash per note, e.g. $8.4583.)
    2. autocall (if t is an autocall review date): if W >= trigger pay
       call_amount (cash per note, e.g. $1,097.50) at that payment_date; the path is no
       longer alive.
       On a coupon note call_amount is principal only; the coupon was paid in step 1.
    3. downside breach (EACH_REVIEW only): breached |= W < downside.level.

    At the final review date, paths still alive get the maturity payment at
    maturity_date with W_T = W(final), N = notional, L = level, g = gearing:

    - W_T > 1 and upside set: N * (1 + min(leverage * (W_T - 1), cap));
      no cap when cap is None.
    - BARRIER, FINAL_ONLY: W_T >= L -> N (or upside); else
      N * max(1 + g * (W_T - 1), 0).
    - BARRIER, EACH_REVIEW: breached (any date including final) and W_T < 1 ->
      N * max(1 + g * (W_T - 1), 0), else N (or upside).
    - BUFFER: W_T >= L -> N (or upside); else N * max(1 - g * (L - W_T), 0).

    Intuition: a worst-of note only looks at the weakest asset; the loop tracks
    one boolean per path (still alive?) and one counter (how many coupons were
    skipped?) so memory coupons and knock-in barriers fall out naturally.

    Reference: filing terms; Hull chapter 26 (barrier and worst-of payoffs).

    Vectorised over paths with numpy boolean masks (loop over dates only).
    """
    perf_arr = np.asarray(perf, dtype=float)
    if perf_arr.ndim != 3:
        raise ValueError("perf must have shape (n_paths, len(dates), n_assets)")
    if perf_arr.shape[1] != len(dates):
        raise ValueError("perf second dimension must equal len(dates)")
    if perf_arr.shape[2] != ts.n_underlyings:
        raise ValueError("perf third dimension must equal number of underlyings")

    notional = float(ts.notional.value)
    downside_level = float(ts.downside.level.value)
    downside_gearing = float(ts.downside.gearing.value)
    downside_kind = ts.downside.kind.value
    downside_obs = ts.downside.observation.value
    if downside_obs == BarrierObservation.DAILY:
        raise UnsupportedTermSheet(
            "daily barrier observation (DAILY) is not supported in pathwise_cashflows"
        )

    final_date = ts.final_review_date.value
    if final_date not in dates:
        raise ValueError("dates must contain the final review date")
    final_index = dates.index(final_date)

    # Review date -> observation for each leg (at most one per leg per date).
    coupon_map = {}
    if ts.coupon is not None:
        for o in ts.coupon.observations:
            coupon_map[o.review_date.value] = o
    autocall_map = {}
    if ts.autocall is not None:
        for o in ts.autocall.observations:
            autocall_map[o.review_date.value] = o

    coupon_barrier = float(ts.coupon.barrier.value) if ts.coupon is not None else 0.0
    coupon_amount = float(ts.coupon.amount.value) if ts.coupon is not None else 0.0
    coupon_memory = bool(ts.coupon.memory.value) if ts.coupon is not None else False
    call_trigger = float(ts.autocall.trigger.value) if ts.autocall is not None else 0.0

    worst = np.min(perf_arr, axis=2)  # shape (n_paths, n_dates)
    alive = np.ones(perf_arr.shape[0], dtype=bool)
    missed = np.zeros(perf_arr.shape[0], dtype=np.int64)
    breached = np.zeros(perf_arr.shape[0], dtype=bool)
    pv = np.zeros(perf_arr.shape[0], dtype=float)

    for j, d in enumerate(dates):
        w = worst[:, j]
        if d in coupon_map:
            obs = coupon_map[d]
            pay_date = obs.payment_date.value
            df = float(discount(pay_date))
            cond = w >= coupon_barrier
            pay_mask = alive & cond
            if coupon_memory:
                amounts = coupon_amount * (1 + missed)
                pv[pay_mask] += amounts[pay_mask] * df
            else:
                pv[pay_mask] += coupon_amount * df
            # Update missed counts for paths still alive.
            missed[alive & cond] = 0
            missed[alive & (~cond)] += 1
        if d in autocall_map:
            obs = autocall_map[d]
            pay_date = obs.payment_date.value
            call_amount = float(obs.call_amount.value)
            call_mask = alive & (w >= call_trigger)
            if np.any(call_mask):
                df_call = float(discount(pay_date))
                pv[call_mask] += call_amount * df_call
                alive[call_mask] = False
        if downside_kind == DownsideKind.BARRIER and downside_obs == BarrierObservation.EACH_REVIEW:
            # Only paths still alive can reach maturity, so only they need breach flags.
            still = alive
            breached[still] = breached[still] | (w[still] < downside_level)

    # Maturity payment for paths never called.
    df_mat = float(discount(ts.maturity_date.value))
    w_t = worst[:, final_index]
    remaining = alive.copy()
    if ts.upside is not None:
        leverage = float(ts.upside.leverage.value)
        cap = float(ts.upside.cap.value) if ts.upside.cap is not None else None
        up_mask = alive & (w_t > 1.0)
        if np.any(up_mask):
            excess = leverage * (w_t[up_mask] - 1.0)
            if cap is not None:
                excess = np.minimum(excess, cap)
            pv[up_mask] += notional * (1.0 + excess) * df_mat
        remaining = alive & (~up_mask)

    if downside_kind == DownsideKind.BARRIER and downside_obs == BarrierObservation.FINAL_ONLY:
        above = remaining & (w_t >= downside_level)
        below = remaining & (w_t < downside_level)
        pv[above] += notional * df_mat
        if np.any(below):
            pv[below] += (
                notional * np.maximum(1.0 + downside_gearing * (w_t[below] - 1.0), 0.0) * df_mat
            )
    elif downside_kind == DownsideKind.BARRIER and downside_obs == BarrierObservation.EACH_REVIEW:
        loss_mask = remaining & breached & (w_t < 1.0)
        else_mask = remaining & (~(breached & (w_t < 1.0)))
        pv[else_mask] += notional * df_mat
        if np.any(loss_mask):
            pv[loss_mask] += (
                notional
                * np.maximum(1.0 + downside_gearing * (w_t[loss_mask] - 1.0), 0.0)
                * df_mat
            )
    elif downside_kind == DownsideKind.BUFFER:
        above = remaining & (w_t >= downside_level)
        below = remaining & (w_t < downside_level)
        pv[above] += notional * df_mat
        if np.any(below):
            pv[below] += (
                notional
                * np.maximum(1.0 - downside_gearing * (downside_level - w_t[below]), 0.0)
                * df_mat
            )
    else:
        raise UnsupportedTermSheet(f"unsupported downside configuration: {downside_kind}")

    return pv


def price_note(
    ts: TermSheet,
    market: MarketInputs,
    n_paths: int = 100_000,
    seed: int = 0,
    antithetic: bool = True,
) -> PricingResult:
    """Risk-neutral Monte Carlo value of a worst-of note.

    Formula: times[k] = year_fraction(pricing_date, dates[k]);
    perf = simulate_performance(times, vols, dividend_yields, correlation,
    drifts = (r,) * N, n_paths, seed, antithetic);
    discount(d) = discount_factor(year_fraction(pricing_date, d), r, spread);
    pv = pathwise_cashflows(ts, dates, perf, discount);
    mean, se = mc_mean_and_se(pv, antithetic).

    Intuition: under the risk-neutral measure every asset drifts at the funding
    rate r (minus dividends); discounting at r plus the issuer spread turns the
    average discounted payoff into the note's fair value as unsecured debt.

    Reference: filing terms; Hull chapters 4 and 21 (discounting, simulation);
    Glasserman chapters 2-3 (correlated normals, antithetic variates).

    Note: drifts are r (risk-neutral, for value); real-world loss odds are a
    separate, labelled simulation in risk/ (Day 5).
    """
    check_supported(ts)
    pricing_date = ts.pricing_date.value
    if market.as_of != pricing_date:
        raise ValueError("inputs must be dated as of the pricing date")
    if len(market.vols) != ts.n_underlyings:
        raise ValueError(
            f"need {ts.n_underlyings} vols for {ts.n_underlyings} underlyings, "
            f"got {len(market.vols)}"
        )
    dates = observation_dates(ts)
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

    def _discount(d: date) -> float:
        return float(discount_factor(year_fraction(pricing_date, d), rate, spread))

    pv = pathwise_cashflows(ts, dates, perf, _discount)
    mean, se = mc_mean_and_se(pv, antithetic)
    return PricingResult(
        price=mean,
        measure=Measure.RISK_NEUTRAL,
        model="montecarlo.worst_of_note",
        seed=seed,
        paths=int(n_paths),
        std_error=se,
        inputs={
            "rate": rate,
            "spread": spread,
            "vols": market.vols,
            "dividend_yields": market.dividend_yields,
            "correlation": market.correlation,
            "n_dates": len(dates),
            "antithetic": antithetic,
        },
    )
