"""Payoff templates for structured notes (worst-of over N >= 1 underlyings). No AI, no network."""

from quantproof.payoffs.worst_of import (
    UnsupportedTermSheet,
    check_supported,
    observation_dates,
    pathwise_cashflows,
    price_note,
)

__all__ = [
    "UnsupportedTermSheet",
    "check_supported",
    "observation_dates",
    "pathwise_cashflows",
    "price_note",
]
