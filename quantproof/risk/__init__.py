"""Real-world (labelled) loss simulation and stress scenarios. No AI, no network."""

from quantproof.risk.real_world import (
    LossOdds,
    StressGrid,
    loss_odds,
    real_world_drifts,
    stress_heatmap,
)

__all__ = [
    "LossOdds",
    "StressGrid",
    "loss_odds",
    "real_world_drifts",
    "stress_heatmap",
]
