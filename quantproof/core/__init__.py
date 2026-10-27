"""Dataclasses shared by every layer: TermSheet (with a Citation per field), MarketInputs, PricingResult."""

from quantproof.core.market import MarketInputs
from quantproof.core.result import Measure, PricingResult
from quantproof.core.termsheet import (
    Autocall,
    AutocallObservation,
    BarrierObservation,
    Citation,
    Cited,
    ContingentCoupon,
    Dependency,
    Downside,
    DownsideKind,
    TermSheet,
    Underlying,
)

__all__ = [
    "Autocall", "AutocallObservation", "BarrierObservation", "Citation", "Cited",
    "ContingentCoupon", "Dependency", "Downside", "DownsideKind", "MarketInputs", "Measure",
    "PricingResult", "TermSheet", "Underlying",
]
