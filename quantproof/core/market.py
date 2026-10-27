"""MarketInputs: the market assumptions a valuation uses, all as of one date.

These are inputs we choose and state (brief section 6), not values read from the filing. Each one
carries a `sources` note so the report can say where it came from. They must be dated as of the
filing's pricing date so our value is comparable with the bank's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np


@dataclass(frozen=True)
class MarketInputs:
    """Flat, continuously compounded market inputs for N underlyings.

    as_of            the date these inputs describe (should equal the filing's pricing date)
    risk_free_rate   r, continuously compounded, per year
    vols             sigma_i per underlying, per year
    dividend_yields  q_i per underlying, continuously compounded, per year
    correlation      N x N correlation matrix of the underlyings' log-returns
    credit_spread    issuer spread s added to r when discounting the note's cash flows
    sources          free-text provenance for each input, e.g. {"vols": "user-entered, 1y realised"}
    """

    as_of: date
    risk_free_rate: float
    vols: tuple[float, ...]
    dividend_yields: tuple[float, ...]
    correlation: tuple[tuple[float, ...], ...]
    credit_spread: float = 0.0
    sources: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        n = len(self.vols)
        if n == 0:
            raise ValueError("need at least one underlying")
        if len(self.dividend_yields) != n:
            raise ValueError("one dividend yield per underlying")
        if any(v <= 0 for v in self.vols):
            raise ValueError("volatilities must be positive")
        if self.credit_spread < 0:
            raise ValueError("credit spread cannot be negative")
        c = self.correlation_matrix()
        if c.shape != (n, n):
            raise ValueError(f"correlation must be {n} x {n}")
        if not np.allclose(c, c.T):
            raise ValueError("correlation matrix must be symmetric")
        if not np.allclose(np.diag(c), 1.0):
            raise ValueError("correlation matrix must have ones on the diagonal")
        if np.any(np.abs(c) > 1 + 1e-12):
            raise ValueError("correlations must lie in [-1, 1]")
        # Positive semi-definite: otherwise no joint distribution has these correlations.
        # The tolerance allows the rounding error of a valid matrix such as rho = 1 exactly.
        if np.linalg.eigvalsh(c).min() < -1e-10:
            raise ValueError("correlation matrix must be positive semi-definite")

    @property
    def n(self) -> int:
        return len(self.vols)

    def correlation_matrix(self) -> np.ndarray:
        return np.array(self.correlation, dtype=float)

    @classmethod
    def single(cls, as_of: date, risk_free_rate: float, vol: float, dividend_yield: float = 0.0,
               credit_spread: float = 0.0, sources: dict[str, str] | None = None) -> MarketInputs:
        """Inputs for one underlying (correlation matrix [[1]])."""
        return cls(as_of, risk_free_rate, (vol,), (dividend_yield,), ((1.0,),), credit_spread,
                   sources or {})
