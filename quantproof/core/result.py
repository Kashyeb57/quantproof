"""PricingResult: an engine output together with everything needed to reproduce and trust it."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from quantproof import __version__


class Measure(str, Enum):
    """Which probability measure produced a number. Never mix them (brief section 6.1)."""

    RISK_NEUTRAL = "risk_neutral"  # for fair value: expected discounted payoff under Q
    REAL_WORLD = "real_world"  # for loss odds: simulation under a stated real-world drift (P)


@dataclass(frozen=True)
class PricingResult:
    """One priced quantity with its provenance.

    price      the value (for a real-world run, the statistic being reported)
    measure    risk-neutral or real-world; required so every number is labelled
    model      e.g. "bsm.call" or "montecarlo.worst_of_autocallable"
    greeks     sensitivities by name ("delta", "gamma", ...), may be empty
    inputs     the exact inputs used, so the result can be recomputed
    seed, paths, std_error   required for Monte Carlo results, None for closed forms
    version    quantproof package version that produced the result
    """

    price: float
    measure: Measure
    model: str
    greeks: dict[str, float] = field(default_factory=dict)
    inputs: dict[str, Any] = field(default_factory=dict)
    seed: int | None = None
    paths: int | None = None
    std_error: float | None = None
    version: str = __version__

    def __post_init__(self) -> None:
        if not math.isfinite(self.price):
            raise ValueError("price must be finite")
        monte_carlo = (self.seed, self.paths, self.std_error)
        if any(x is not None for x in monte_carlo) and any(x is None for x in monte_carlo):
            raise ValueError("a Monte Carlo result must report seed, paths and std_error together")
        if self.paths is not None and self.paths <= 0:
            raise ValueError("paths must be positive")
        if self.std_error is not None and self.std_error < 0:
            raise ValueError("std_error cannot be negative")

    @property
    def is_monte_carlo(self) -> bool:
        return self.paths is not None

    def confidence_interval(self, z: float = 1.96) -> tuple[float, float]:
        """price +/- z standard errors (95% by default). A closed form has zero width."""
        half = z * (self.std_error or 0.0)
        return self.price - half, self.price + half
