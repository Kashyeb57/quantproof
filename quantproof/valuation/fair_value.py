"""Fair-value range over a stated assumption grid.

The bank's estimated value is a comparison point only. It is never used as an
input to this module and assumptions are never adjusted to match it.

Pure Python + NumPy. No AI and no network calls in this module.

References: J. C. Hull, *Options, Futures, and Other Derivatives*, chapters 4,
21 and 26; P. Glasserman, *Monte Carlo Methods in Financial Engineering*,
chapters 2-3 (common random numbers and Monte Carlo error).
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field, replace

import numpy as np

from quantproof.core.market import MarketInputs
from quantproof.core.result import PricingResult
from quantproof.core.termsheet import TermSheet
from quantproof.payoffs.worst_of import price_note


@dataclass(frozen=True)
class AssumptionGrid:
    """Assumption shifts used to build a fair-value range.

    Formula: each tuple lists absolute shifts (or, for correlation, blend
    weights and, for skew, an extra volatility per 10% below the initial
    level) applied one at a time and in combination over the cartesian grid.

    Intuition: a single set of assumptions gives a single, over-precise
    number. Showing the value over a stated range of plausible assumptions
    is more honest, especially for volatility, correlation and skew.

    Reference: project brief section 6 (fair value as a range; inputs stated
    with ranges; skew stated).
    """

    vol_shifts: tuple[float, ...] = (-0.05, 0.0, 0.05)
    correlation_shifts: tuple[float, ...] = (-0.25, 0.0, 0.25)
    spread_shifts: tuple[float, ...] = (-0.0025, 0.0, 0.0025)
    dividend_shifts: tuple[float, ...] = (-0.005, 0.0, 0.005)
    skew_per_10pct: tuple[float, ...] = (0.0, 0.02)

    def __post_init__(self) -> None:
        for name in (
            "vol_shifts",
            "correlation_shifts",
            "spread_shifts",
            "dividend_shifts",
            "skew_per_10pct",
        ):
            values = getattr(self, name)
            if 0.0 not in values:
                raise ValueError(f"AssumptionGrid.{name} must contain 0.0 (the base case)")


def shifted_market(
    market: MarketInputs,
    vol_shift: float = 0.0,
    correlation_shift: float = 0.0,
    spread_shift: float = 0.0,
    dividend_shift: float = 0.0,
    skew_vol: float = 0.0,
) -> MarketInputs:
    """Return a new MarketInputs with the stated shifts applied.

    Formula: vols_i = max(vol_i + vol_shift + skew_vol, 0.01);
    q_i = max(q_i + dividend_shift, 0.0);
    spread = max(spread + spread_shift, 0.0).
    Correlation blend: w = correlation_shift; if w > 0,
    C' = (1 - w) C + w J (J = all ones, perfect correlation); if w < 0,
    C' = (1 - |w|) C + |w| I (towards independence); w must lie in [-1, 1].

    Intuition: shifting every volatility and dividend together, and blending
    the correlation matrix towards perfect correlation or independence, moves
    the whole market in one stated direction at a time. A convex combination
    of positive semi-definite matrices is positive semi-definite, so the
    blended correlation matrix remains valid. For N = 1 the matrix stays
    [[1]].

    Reference: Hull chapter 21 (correlated simulation); Glasserman chapter 3
    (correlated normals require a positive semi-definite correlation matrix).

    The caller's ``market`` is never mutated; a new MarketInputs is built
    with ``dataclasses.replace`` and its ``sources["grid"]`` describes the
    shifts.
    """
    if not -1.0 <= correlation_shift <= 1.0:
        raise ValueError("correlation_shift must lie in [-1, 1]")

    new_vols = tuple(max(float(v) + vol_shift + skew_vol, 0.01) for v in market.vols)
    new_q = tuple(max(float(q) + dividend_shift, 0.0) for q in market.dividend_yields)
    new_spread = max(float(market.credit_spread) + spread_shift, 0.0)

    n = market.n
    if n == 1:
        new_correlation: tuple[tuple[float, ...], ...] = ((1.0,),)
    else:
        corr = market.correlation_matrix()
        w = float(correlation_shift)
        if w > 0.0:
            blended = (1.0 - w) * corr + w * np.ones_like(corr)
        elif w < 0.0:
            a = abs(w)
            blended = (1.0 - a) * corr + a * np.eye(n)
        else:
            blended = corr
        new_correlation = tuple(tuple(float(x) for x in row) for row in blended)

    new_sources = dict(market.sources)
    new_sources["grid"] = (
        f"grid shift: vol_shift={vol_shift:+.6g}, "
        f"correlation_shift={correlation_shift:+.6g}, "
        f"spread_shift={spread_shift:+.6g}, "
        f"dividend_shift={dividend_shift:+.6g}, "
        f"skew_vol={skew_vol:+.6g}"
    )

    return replace(
        market,
        vols=new_vols,
        dividend_yields=new_q,
        correlation=new_correlation,
        credit_spread=new_spread,
        sources=new_sources,
    )


def skew_vol(ts: TermSheet, skew_per_10pct: float) -> float:
    """Extra volatility implied by downside skew at the downside level.

    Formula: skew_vol = skew_per_10pct * max(0, 1 - K) / 0.10, where
    K = ts.downside.level.value is the downside level as a fraction of the
    initial level.

    Intuition: the note is short a put struck near K; equity implied vol is
    higher at lower strikes, so pricing at the barrier-strike vol captures
    the put's skew premium. It is a stated simplification: it applies that
    vol to the whole note (also to the coupon digitals), and it lowers the
    value, as brief section 6.3 requires.

    Reference: Hull chapter 20 (volatility smiles / skew); project brief
    section 6.3.
    """
    k = float(ts.downside.level.value)
    return float(skew_per_10pct) * max(0.0, 1.0 - k) / 0.10


@dataclass
class GridPoint:
    """One priced point of the assumption grid."""

    vol_shift: float
    correlation_shift: float
    spread_shift: float
    dividend_shift: float
    skew_per_10pct: float
    price: float
    std_error: float


@dataclass
class FairValueRange:
    """Fair-value range over an AssumptionGrid, compared with the bank's value.

    Formula: low / high are the min / max Monte Carlo price over the full
    cartesian product of the grid; ``base`` is priced at the unshifted inputs
    with no skew.

    Intuition: the range shows how much the estimated value moves when the
    stated assumptions move, instead of presenting one over-precise number.

    Reference: project brief section 6 (fair value as a range).
    """

    low: float
    high: float
    base: PricingResult
    points: list[GridPoint]
    sensitivities: dict[str, tuple[float, float]]
    bank_estimate: float | None
    bank_position: str | None
    preliminary: bool
    n_paths: int
    seed: int
    grid: AssumptionGrid
    assumptions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Return a JSON-serialisable representation of this range.

        Formula: dataclass fields are copied, tuples become lists, and the
        pricing measure / enums become strings.

        Intuition: reports and the app need plain JSON types, not dataclasses
        or enums.

        Reference: project reporting requirements (provenance for every
        number shown).
        """
        return {
            "low": self.low,
            "high": self.high,
            "base": {
                "price": self.base.price,
                "measure": self.base.measure.value,
                "model": self.base.model,
                "greeks": dict(self.base.greeks),
                "inputs": self.base.inputs,
                "seed": self.base.seed,
                "paths": self.base.paths,
                "std_error": self.base.std_error,
                "version": self.base.version,
            },
            "points": [
                {
                    "vol_shift": p.vol_shift,
                    "correlation_shift": p.correlation_shift,
                    "spread_shift": p.spread_shift,
                    "dividend_shift": p.dividend_shift,
                    "skew_per_10pct": p.skew_per_10pct,
                    "price": p.price,
                    "std_error": p.std_error,
                }
                for p in self.points
            ],
            "sensitivities": {
                name: [pair[0], pair[1]] for name, pair in self.sensitivities.items()
            },
            "bank_estimate": self.bank_estimate,
            "bank_position": self.bank_position,
            "preliminary": self.preliminary,
            "n_paths": self.n_paths,
            "seed": self.seed,
            "grid": {
                "vol_shifts": list(self.grid.vol_shifts),
                "correlation_shifts": list(self.grid.correlation_shifts),
                "spread_shifts": list(self.grid.spread_shifts),
                "dividend_shifts": list(self.grid.dividend_shifts),
                "skew_per_10pct": list(self.grid.skew_per_10pct),
            },
            "assumptions": list(self.assumptions),
        }


def _assumptions(ts: TermSheet, market: MarketInputs, grid: AssumptionGrid) -> list[str]:
    corr = market.correlation
    if market.n == 1:
        corr_text = "correlation: single underlying, correlation matrix [[1]]"
    else:
        corr_text = f"correlation: {corr} (grid blends towards independence / perfect correlation)"
    lines = [
        f"risk-free rate r = {market.risk_free_rate:.6g} per year, as of {market.as_of.isoformat()}",
        f"issuer credit spread = {market.credit_spread:.6g} per year",
        f"volatilities = {tuple(market.vols)} per year (grid shifts {tuple(grid.vol_shifts)})",
        f"dividend yields = {tuple(market.dividend_yields)} per year (grid shifts {tuple(grid.dividend_shifts)})",
        corr_text,
        (
            f"skew: extra vol per 10% below initial in {tuple(grid.skew_per_10pct)}, "
            f"applied at downside level {ts.downside.level.value:.6g}"
        ),
        "measure: risk-neutral (for estimated value)",
        "discounting at r + issuer spread",
        "the bank's estimated value is a comparison point, not a calibration target",
    ]
    if ts.is_preliminary.value:
        lines.append("based on preliminary terms")
    return lines


def fair_value_range(
    ts: TermSheet,
    market: MarketInputs,
    grid: AssumptionGrid = AssumptionGrid(),
    n_paths: int = 20_000,
    seed: int = 0,
    n_paths_base: int = 100_000,
) -> FairValueRange:
    """Fair-value range over the full cartesian product of ``grid``.

    Formula: for every combination of (vol_shift, correlation_shift,
    spread_shift, dividend_shift, skew_per_10pct) in the grid, price the note
    with ``price_note`` on ``shifted_market(market, ..., skew_vol=
    skew_vol(ts, skew_per_10pct))`` using ``n_paths`` and the same ``seed``
    for every point; low / high are the min / max point prices. ``base`` is
    priced at the unshifted inputs with no skew using ``n_paths_base``.
    Correlation shifts other than 0 are skipped when N = 1.

    Intuition: using the same seed for every point (common random numbers)
    means the differences between points are far more precise than each
    point's own standard error, so the range reflects assumption changes
    rather than simulation noise. The bank's estimated value is never used
    as an input; it is only compared with the resulting range.

    Reference: Glasserman chapters 2-3 (common random numbers); Hull
    chapters 4, 21 and 26; project brief section 6.
    """
    base = price_note(ts, market, n_paths=n_paths_base, seed=seed)

    if ts.n_underlyings == 1:
        correlation_shifts: tuple[float, ...] = (0.0,)
    else:
        correlation_shifts = tuple(grid.correlation_shifts)

    points: list[GridPoint] = []
    for vol_shift, corr_shift, spread_shift, div_shift, skew in itertools.product(
        grid.vol_shifts,
        correlation_shifts,
        grid.spread_shifts,
        grid.dividend_shifts,
        grid.skew_per_10pct,
    ):
        shifted = shifted_market(
            market,
            vol_shift=float(vol_shift),
            correlation_shift=float(corr_shift),
            spread_shift=float(spread_shift),
            dividend_shift=float(div_shift),
            skew_vol=skew_vol(ts, float(skew)),
        )
        result = price_note(ts, shifted, n_paths=n_paths, seed=seed)
        points.append(
            GridPoint(
                vol_shift=float(vol_shift),
                correlation_shift=float(corr_shift),
                spread_shift=float(spread_shift),
                dividend_shift=float(div_shift),
                skew_per_10pct=float(skew),
                price=float(result.price),
                std_error=float(result.std_error) if result.std_error is not None else 0.0,
            )
        )

    if not points:
        raise ValueError("assumption grid produced no points")

    low = min(p.price for p in points)
    high = max(p.price for p in points)

    # One-at-a-time sensitivities read from points, not recomputed.
    by_key: dict[tuple[float, float, float, float, float], GridPoint] = {
        (
            p.vol_shift,
            p.correlation_shift,
            p.spread_shift,
            p.dividend_shift,
            p.skew_per_10pct,
        ): p
        for p in points
    }

    def _point(
        vol: float = 0.0,
        corr: float = 0.0,
        spread: float = 0.0,
        div: float = 0.0,
        skew: float = 0.0,
    ) -> GridPoint:
        return by_key[(vol, corr, spread, div, skew)]

    if ts.n_underlyings == 1:
        corr_low_price = corr_high_price = _point(corr=0.0).price
    else:
        corr_low_price = _point(corr=min(grid.correlation_shifts)).price
        corr_high_price = _point(corr=max(grid.correlation_shifts)).price

    sensitivities: dict[str, tuple[float, float]] = {
        "vol": (
            _point(vol=min(grid.vol_shifts)).price,
            _point(vol=max(grid.vol_shifts)).price,
        ),
        "correlation": (corr_low_price, corr_high_price),
        "spread": (
            _point(spread=min(grid.spread_shifts)).price,
            _point(spread=max(grid.spread_shifts)).price,
        ),
        "dividend": (
            _point(div=min(grid.dividend_shifts)).price,
            _point(div=max(grid.dividend_shifts)).price,
        ),
        "skew": (
            _point(skew=min(grid.skew_per_10pct)).price,
            _point(skew=max(grid.skew_per_10pct)).price,
        ),
    }

    bank_estimate = float(ts.estimated_value.value) if ts.estimated_value is not None else None
    if bank_estimate is None:
        bank_position: str | None = None
    elif bank_estimate < low:
        bank_position = "below range"
    elif bank_estimate > high:
        bank_position = "above range"
    else:
        bank_position = "inside range"

    return FairValueRange(
        low=float(low),
        high=float(high),
        base=base,
        points=points,
        sensitivities=sensitivities,
        bank_estimate=bank_estimate,
        bank_position=bank_position,
        preliminary=bool(ts.is_preliminary.value),
        n_paths=int(n_paths),
        seed=int(seed),
        grid=grid,
        assumptions=_assumptions(ts, market, grid),
    )
