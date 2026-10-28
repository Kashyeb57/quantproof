"""Market data maths and licence-safe fetchers (no pricing).

``vol`` is pure maths: realised volatility and correlation from daily
closes, plus the grid centre/range the valuation range is built around.
``sources`` fetches daily closes at runtime from licence-safe sources
(see docs/VOL_SOURCES.md), caching under gitignored ``.cache/`` and
never committing raw data. Standard library + NumPy only; no AI calls.
"""

from quantproof.market.anchored import anchored_inputs, inputs_summary
from quantproof.market.vol import (
    correlation_matrix,
    corr_grid,
    log_returns,
    pairwise_correlation,
    realised_vol,
    trailing_window,
    vol_grid,
)

__all__ = [
    "correlation_matrix",
    "corr_grid",
    "log_returns",
    "pairwise_correlation",
    "realised_vol",
    "trailing_window",
    "vol_grid",
]
