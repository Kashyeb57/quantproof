"""TermSheet: the payoff specification of a structured note, with a citation for every field.

The extraction agent fills this from a filing. Every value is wrapped in `Cited`, so each number
or date can be traced to a quote in the filing. The quote verifier (validation/quotes.py) later sets
`Citation.verified`; fields that are not verified must be flagged to the user, never used silently.

Conventions
- Levels such as barriers and call triggers are fractions of the initial level (0.70 = 70%).
- Money amounts are per `notional` (usually $1,000) and include principal where stated
  (a call amount of 1097.50 means $1,000 principal plus a $97.50 premium).
- All templates are worst-of over N >= 1 underlyings; N = 1 is an ordinary single-asset note.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from datetime import date
from enum import Enum
from typing import Generic, Iterator, TypeVar

T = TypeVar("T")


@dataclass
class Citation:
    """Where a value came from in the filing.

    `quote` must appear verbatim in the normalised filing text. `verified` is None until the quote
    verifier has run, then True or False.
    """

    quote: str
    section: str | None = None
    verified: bool | None = None


@dataclass
class Cited(Generic[T]):
    """A value together with the citation that supports it. `citation` is None only for values we
    set ourselves (for example a default), which the unverified-field check reports."""

    value: T
    citation: Citation | None = None


class Dependency(str, Enum):
    """How the payoff depends on several underlyings."""

    WORST_OF = "worst_of"  # payoff driven by the lesser-performing underlying (N = 1 is single-asset)


class BarrierObservation(str, Enum):
    """When the downside barrier is checked."""

    FINAL_ONLY = "final_only"  # only on the final review date (a "European" barrier)
    EACH_REVIEW = "each_review"  # on every review date
    DAILY = "daily"  # every trading day (stretch scope)


class DownsideKind(str, Enum):
    """How principal is lost at maturity."""

    BARRIER = "barrier"  # below the barrier, lose 1:1 from the initial level (knock-in put)
    BUFFER = "buffer"  # losses beyond the buffer only, scaled by the downside gearing


@dataclass
class Underlying:
    name: Cited[str]
    ticker: Cited[str] | None = None
    # Closing level on the pricing date. None in a preliminary filing (not yet known).
    initial_level: Cited[float] | None = None
    # A recent level the filing quotes (used to price preliminary terms), and its date.
    reference_level: Cited[float] | None = None
    reference_date: Cited[date] | None = None


@dataclass
class AutocallObservation:
    review_date: Cited[date]
    payment_date: Cited[date]
    call_amount: Cited[float]  # paid per notional if called on this date, principal included


@dataclass
class Autocall:
    # Called if every underlying closes at or above trigger x initial on a review date.
    trigger: Cited[float]
    observations: list[AutocallObservation]
    amounts_are_minimums: bool = False  # preliminary filings state "at least" amounts


@dataclass
class ContingentCoupon:
    # Coupon paid on a review date if every underlying closes at or above `barrier` x initial.
    barrier: Cited[float]
    amount: Cited[float]  # per notional, per review date
    memory: Cited[bool]  # missed coupons are paid later if a later review date pays


@dataclass
class Downside:
    kind: Cited[DownsideKind]
    level: Cited[float]  # barrier or buffer level, as a fraction of initial
    observation: Cited[BarrierObservation]
    gearing: Cited[float] = field(default_factory=lambda: Cited(1.0))  # loss per 1% below level


@dataclass
class TermSheet:
    # Document
    issuer: Cited[str]
    is_preliminary: Cited[bool]
    underlyings: list[Underlying]
    dependency: Cited[Dependency]
    notional: Cited[float]
    issue_price: Cited[float]
    pricing_date: Cited[date]
    issue_date: Cited[date]
    maturity_date: Cited[date]
    final_review_date: Cited[date]
    downside: Downside
    guarantor: Cited[str] | None = None
    cusip: Cited[str] | None = None
    # Payoff features (each optional; templates check which are present)
    autocall: Autocall | None = None
    coupon: ContingentCoupon | None = None
    # The bank's own disclosures (comparison points, never calibration targets)
    estimated_value: Cited[float] | None = None
    estimated_value_floor: Cited[float] | None = None
    max_selling_commission: Cited[float] | None = None
    # Anything the extractor could not determine
    missing_or_ambiguous: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.underlyings:
            raise ValueError("a TermSheet needs at least one underlying")
        if self.notional.value <= 0 or self.issue_price.value <= 0:
            raise ValueError("notional and issue price must be positive")
        if not self.pricing_date.value <= self.issue_date.value <= self.maturity_date.value:
            raise ValueError("expected pricing date <= issue date <= maturity date")
        if not self.pricing_date.value < self.final_review_date.value <= self.maturity_date.value:
            raise ValueError("final review date must lie after pricing and on or before maturity")
        if not 0 < self.downside.level.value <= 1:
            raise ValueError("downside level must be a fraction of initial in (0, 1]")
        if self.autocall is not None:
            reviews = [o.review_date.value for o in self.autocall.observations]
            if reviews != sorted(reviews) or len(set(reviews)) != len(reviews):
                raise ValueError("autocall review dates must be strictly increasing")
            for o in self.autocall.observations:
                if o.payment_date.value < o.review_date.value:
                    raise ValueError(f"payment date before review date {o.review_date.value}")
                if o.call_amount.value <= 0:
                    raise ValueError("call amounts must be positive")

    @property
    def n_underlyings(self) -> int:
        return len(self.underlyings)

    def cited_fields(self) -> Iterator[tuple[str, Cited]]:
        """Yield (path, Cited) for every cited value in the term sheet, e.g. ('underlyings[0].name', ...)."""
        yield from _walk(self, "")

    def unverified_fields(self) -> list[str]:
        """Paths of fields with no citation or a citation the quote verifier has not confirmed."""
        return [p for p, c in self.cited_fields() if c.citation is None or c.citation.verified is not True]


def _walk(obj: object, path: str) -> Iterator[tuple[str, Cited]]:
    if isinstance(obj, Cited):
        yield path, obj
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            yield from _walk(item, f"{path}[{i}]")
    elif is_dataclass(obj):
        for f in fields(obj):
            child = getattr(obj, f.name)
            yield from _walk(child, f"{path}.{f.name}" if path else f.name)
