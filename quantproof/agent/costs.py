"""Token prices and cost estimates for Meta Model API calls.

Used by the development drafting script (scripts/muse_draft.py) and, later, by the product's
cost log and cost guard. Prices are US dollars per million tokens as listed on the Meta developer
billing page (Oct 2026). Muse Wonder's cached-input price is not listed, so cached tokens are
charged at the full input price there: an estimate should err high, never low.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    """Dollars per million tokens."""

    input: float
    cached_input: float
    output: float


# Keyed by model family; a model id matches the longest family prefix (muse-spark-1.3 -> muse-spark).
PRICES: dict[str, Price] = {
    "muse-spark": Price(input=1.25, cached_input=0.15, output=4.25),
    "muse-wonder": Price(input=4.00, cached_input=4.00, output=20.00),
}


def price_for(model: str) -> Price:
    """Price for a model id. Unknown models raise, so a call is never logged as costing $0."""
    matches = [family for family in PRICES if model.startswith(family)]
    if not matches:
        raise ValueError(f"no price listed for model {model!r}; add it to PRICES")
    return PRICES[max(matches, key=len)]


@dataclass(frozen=True)
class Usage:
    """Token counts for one call. `input_tokens` includes the cached ones; `output_tokens`
    includes reasoning tokens (Muse Spark reasons before answering, and reasoning is billed)."""

    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0
    reasoning_tokens: int = 0

    @classmethod
    def from_response(cls, usage: Mapping) -> "Usage":
        """Read a usage block in either API shape.

        Chat Completions: prompt_tokens / completion_tokens, prompt_tokens_details.cached_tokens,
        completion_tokens_details.reasoning_tokens. Responses API: input_tokens / output_tokens,
        input_tokens_details / output_tokens_details.
        """
        inp = usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0
        out = usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0
        in_details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
        out_details = usage.get("completion_tokens_details") or usage.get("output_tokens_details") or {}
        return cls(
            input_tokens=int(inp),
            output_tokens=int(out),
            cached_tokens=int(in_details.get("cached_tokens", 0) or 0),
            reasoning_tokens=int(out_details.get("reasoning_tokens", 0) or 0),
        )


def estimate_cost(model: str, usage: Usage) -> float:
    """Estimated dollars for one call:
    (input - cached) x input price + cached x cached price + output x output price, per 1e6 tokens.
    """
    p = price_for(model)
    fresh = usage.input_tokens - usage.cached_tokens
    return (fresh * p.input + usage.cached_tokens * p.cached_input + usage.output_tokens * p.output) / 1e6
