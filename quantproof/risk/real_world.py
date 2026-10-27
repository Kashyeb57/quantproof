"""Real-world (physical measure) loss odds and stress scenarios.

This module is REAL-WORLD only and is labelled as such on every result. It is
never used for value: fair value uses the risk-neutral simulation in
valuation/. Issuer default is not simulated (see LossOdds).

Pure Python + NumPy. No AI and no network calls in this module.

References: J. C. Hull, *Options, Futures, and Other Derivatives*, chapters
14 and 21 (real-world vs risk-neutral drift, simulation); P. Glasserman,
*Monte Carlo Methods in Financial Engineering*, chapters 2-3.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import numpy as np

from quantproof.core.market import MarketInputs
from quantproof.core.result import Measure
from quantproof.core.termsheet import TermSheet
from quantproof.models.discounting import year_fraction
from quantproof.models.montecarlo import simulate_performance
from quantproof.payoffs.worst_of import check_supported, observation_dates, pathwise_cashflows


@dataclass
class LossOdds:
    """Loss odds under the real-world (physical) measure.

    Formula: total cash per path is the undiscounted sum of coupons, any call
    amount and any maturity payment; prob_loss = P(total cash < issue price),
    and similarly for losses over 20% / 50% of the issue price.

    Intuition: this answers "how often might an investor get back less than
    they paid, under a stated real-world drift?" It is not a value and must
    never be mixed with the risk-neutral fair value.

    Reference: project brief section 6.1 (risk-neutral for value, a separate
    labelled real-world simulation for loss odds).

    Note: issuer default is not simulated; ``excludes_issuer_default`` is
    always True for results produced here.
    """

    measure: Measure
    drifts: tuple[float, ...]
    drift_note: str
    prob_loss: float
    prob_loss_over_20: float
    prob_loss_over_50: float
    prob_called: float | None
    expected_cash: float
    quantiles: dict[str, float]
    std_errors: dict[str, float]
    n_paths: int
    seed: int
    excludes_issuer_default: bool = True

    def to_dict(self) -> dict:
        """Return a JSON-serialisable representation of these loss odds.

        Formula: dataclass fields are copied, tuples become lists and the
        measure enum becomes a string.

        Intuition: reports and the app need plain JSON types, not dataclasses
        or enums.

        Reference: project reporting requirements (every number labelled by
        measure).
        """
        return {
            "measure": self.measure.value,
            "drifts": list(self.drifts),
            "drift_note": self.drift_note,
            "prob_loss": self.prob_loss,
            "prob_loss_over_20": self.prob_loss_over_20,
            "prob_loss_over_50": self.prob_loss_over_50,
            "prob_called": self.prob_called,
            "expected_cash": self.expected_cash,
            "quantiles": dict(self.quantiles),
            "std_errors": dict(self.std_errors),
            "n_paths": self.n_paths,
            "seed": self.seed,
            "excludes_issuer_default": self.excludes_issuer_default,
        }


def real_world_drifts(
    market: MarketInputs, equity_risk_premium: float = 0.05
) -> tuple[float, ...]:
    """Real-world drifts: mu_i = r + equity risk premium for every asset.

    Formula: mu_i = r + equity_risk_premium for every underlying i, where
    mu_i is the total expected return per year before dividends.

    Intuition: under the real-world measure assets are expected to earn the
    risk-free rate plus a stated equity risk premium; this is an assumption
    to state, not a market price.

    Reference: Hull chapter 14 (real-world vs risk-neutral drift).
    """
    mu = float(market.risk_free_rate) + float(equity_risk_premium)
    return tuple(mu for _ in range(market.n))


def _binomial_se(p: float, n: int) -> float:
    return float(math.sqrt(p * (1.0 - p) / n)) if n > 0 else 0.0


def loss_odds(
    ts: TermSheet,
    market: MarketInputs,
    drifts: tuple[float, ...] | None = None,
    equity_risk_premium: float = 0.05,
    n_paths: int = 100_000,
    seed: int = 0,
) -> LossOdds:
    """Real-world loss odds for a note. REAL-WORLD measure; never for value.

    Formula: dates = observation_dates(ts);
    times[k] = year_fraction(pricing_date, dates[k]);
    perf = simulate_performance(times, vols, dividend_yields, correlation,
    drifts=drifts, antithetic=False);
    total cash = pathwise_cashflows(ts, dates, perf, lambda d: 1.0)
    (no discounting: the investor's nominal cash).
    prob_loss = P(total cash < issue price), with issue price
    ts.issue_price.value, and analogously for 0.8 / 0.5 times the issue
    price. prob_called is P(autocalled before the final review date),
    computed directly from the same paths: for each autocall review date
    strictly before the final review date, W = min_i perf[:, k, :], and
    called = any over those dates of W >= trigger (first-hit logic is not
    needed for "ever called"). Probability standard errors are the binomial
    SE sqrt(p (1 - p) / n); the SE of expected_cash is
    std(total cash, ddof=1) / sqrt(n).

    Intuition: simulating under a stated real-world drift and adding up the
    nominal cash an investor actually receives shows how often they lose
    money, without discounting or risk-neutral pricing entering the answer.

    Reference: Hull chapters 14 and 21; Glasserman chapters 2-3; project
    brief section 6.1.

    Note: issuer default is not simulated.

    Raises ValueError if market.as_of != ts.pricing_date.value.
    """
    check_supported(ts)
    if market.as_of != ts.pricing_date.value:
        raise ValueError("inputs must be dated as of the pricing date")

    if drifts is None:
        drifts_tuple = real_world_drifts(market, equity_risk_premium)
        drift_note = (
            f"real-world drift = r + {equity_risk_premium:.1%} equity risk premium "
            f"(r = {market.risk_free_rate:.6g})"
        )
    else:
        drifts_tuple = tuple(float(d) for d in drifts)
        drift_note = f"real-world drifts supplied directly: mu = {drifts_tuple}"

    if len(drifts_tuple) != ts.n_underlyings:
        raise ValueError(
            f"need {ts.n_underlyings} drifts for {ts.n_underlyings} underlyings, "
            f"got {len(drifts_tuple)}"
        )

    dates = observation_dates(ts)
    pricing_date = ts.pricing_date.value
    times = [year_fraction(pricing_date, d) for d in dates]

    perf = simulate_performance(
        times,
        market.vols,
        market.dividend_yields,
        np.asarray(market.correlation, dtype=float),
        drifts=drifts_tuple,
        n_paths=n_paths,
        seed=seed,
        antithetic=False,
    )

    total_cash = pathwise_cashflows(ts, dates, perf, lambda d: 1.0)
    total_cash = np.asarray(total_cash, dtype=float)
    n = int(total_cash.shape[0])
    issue_price = float(ts.issue_price.value)

    prob_loss = float(np.mean(total_cash < issue_price))
    prob_loss_over_20 = float(np.mean(total_cash < 0.8 * issue_price))
    prob_loss_over_50 = float(np.mean(total_cash < 0.5 * issue_price))

    prob_called: float | None
    if ts.autocall is None:
        prob_called = None
    else:
        final = ts.final_review_date.value
        trigger = float(ts.autocall.trigger.value)
        date_index = {d: k for k, d in enumerate(dates)}
        called = np.zeros(n, dtype=bool)
        for obs in ts.autocall.observations:
            review = obs.review_date.value
            if review < final:
                k = date_index[review]
                w = np.min(perf[:, k, :], axis=1)
                called = called | (w >= trigger)
        prob_called = float(np.mean(called)) if n > 0 else 0.0

    expected_cash = float(np.mean(total_cash)) if n > 0 else 0.0
    quantiles = {
        "p05": float(np.quantile(total_cash, 0.05)),
        "p25": float(np.quantile(total_cash, 0.25)),
        "p50": float(np.quantile(total_cash, 0.50)),
        "p75": float(np.quantile(total_cash, 0.75)),
        "p95": float(np.quantile(total_cash, 0.95)),
    }

    std_errors: dict[str, float] = {
        "prob_loss": _binomial_se(prob_loss, n),
        "prob_loss_over_20": _binomial_se(prob_loss_over_20, n),
        "prob_loss_over_50": _binomial_se(prob_loss_over_50, n),
        "expected_cash": float(np.std(total_cash, ddof=1) / math.sqrt(n)) if n > 1 else 0.0,
    }
    if prob_called is not None:
        std_errors["prob_called"] = _binomial_se(prob_called, n)

    return LossOdds(
        measure=Measure.REAL_WORLD,
        drifts=drifts_tuple,
        drift_note=drift_note,
        prob_loss=prob_loss,
        prob_loss_over_20=prob_loss_over_20,
        prob_loss_over_50=prob_loss_over_50,
        prob_called=prob_called,
        expected_cash=expected_cash,
        quantiles=quantiles,
        std_errors=std_errors,
        n_paths=int(n_paths),
        seed=int(seed),
        excludes_issuer_default=True,
    )


@dataclass
class StressGrid:
    """Real-world stress heatmap over drift levels and volatility scales.

    Formula: prob_loss[row][col] is the real-world prob_loss from loss_odds
    with drifts = (drift_levels[row],) * N and vols scaled by
    vol_scales[col].

    Intuition: a grid of stated bad / good real-world scenarios shows how
    fragile the loss odds are, without claiming any cell is a forecast.

    Reference: project brief section 6.1 (separate labelled real-world
    simulation).
    """

    drift_levels: tuple[float, ...]
    vol_scales: tuple[float, ...]
    prob_loss: list[list[float]]
    expected_cash: list[list[float]]
    n_paths: int
    seed: int
    measure: Measure = Measure.REAL_WORLD

    def to_dict(self) -> dict:
        """Return a JSON-serialisable representation of this stress grid.

        Formula: dataclass fields are copied, tuples become lists and the
        measure enum becomes a string.

        Intuition: reports and the app need plain JSON types, not dataclasses
        or enums.

        Reference: project reporting requirements (every number labelled by
        measure).
        """
        return {
            "drift_levels": list(self.drift_levels),
            "vol_scales": list(self.vol_scales),
            "prob_loss": [list(row) for row in self.prob_loss],
            "expected_cash": [list(row) for row in self.expected_cash],
            "n_paths": self.n_paths,
            "seed": self.seed,
            "measure": self.measure.value,
        }


def stress_heatmap(
    ts: TermSheet,
    market: MarketInputs,
    drift_levels: tuple[float, ...] = (-0.10, -0.05, 0.0, 0.05, 0.10),
    vol_scales: tuple[float, ...] = (0.75, 1.0, 1.25, 1.5),
    n_paths: int = 20_000,
    seed: int = 0,
) -> StressGrid:
    """Real-world stress heatmap. REAL-WORLD measure; never for value.

    Formula: for each cell (mu, vol_scale), run loss_odds with
    drifts = (mu,) * N and vols_i' = vol_scale * vols_i, using the same
    ``seed`` for every cell; rows are drift_levels and columns are
    vol_scales in prob_loss and expected_cash.

    Intuition: using the same seed for every cell (common random numbers)
    makes differences between scenarios reflect the scenario, not simulation
    noise. Low drifts and high volatilities are the stressful corner for a
    worst-of note.

    Reference: Glasserman chapters 2-3 (common random numbers); Hull
    chapter 21; project brief section 6.1.

    Note: issuer default is not simulated (see LossOdds).
    """
    n = ts.n_underlyings
    prob_loss_rows: list[list[float]] = []
    expected_cash_rows: list[list[float]] = []
    for mu in drift_levels:
        prob_row: list[float] = []
        cash_row: list[float] = []
        for scale in vol_scales:
            scaled_market = replace(
                market,
                vols=tuple(float(v) * float(scale) for v in market.vols),
                sources={
                    **dict(market.sources),
                    "stress": f"vol_scale={float(scale):.6g}",
                },
            )
            result = loss_odds(
                ts,
                scaled_market,
                drifts=tuple(float(mu) for _ in range(n)),
                n_paths=n_paths,
                seed=seed,
            )
            prob_row.append(float(result.prob_loss))
            cash_row.append(float(result.expected_cash))
        prob_loss_rows.append(prob_row)
        expected_cash_rows.append(cash_row)

    return StressGrid(
        drift_levels=tuple(float(x) for x in drift_levels),
        vol_scales=tuple(float(x) for x in vol_scales),
        prob_loss=prob_loss_rows,
        expected_cash=expected_cash_rows,
        n_paths=int(n_paths),
        seed=int(seed),
        measure=Measure.REAL_WORLD,
    )
