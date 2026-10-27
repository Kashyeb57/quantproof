"""Quote verifier: every extracted term must carry a verbatim filing quote.

AGENTS.md rule 5a requires that each ``Citation.quote`` appear verbatim in the
filing text. This module checks that claim and sets ``citation.verified``.

No AI, no network. Pure Python standard library only.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from quantproof.core.termsheet import TermSheet

#: A normalised quote (or ellipsis piece) shorter than this is too short to be
#: evidence (e.g. "70%" matches almost any filing).
MIN_QUOTE_CHARS = 8


def normalise_for_match(text: str) -> str:
    """Normalise text for quote matching.

    Steps: NFKC; lower-case; replace every run of characters that are not
    ``[a-z0-9.%$]`` with a single space; strip.

    Rationale: the model and the HTML differ in punctuation, quote style,
    dashes, line breaks and spacing, but never in words, digits, decimal
    points, percent or dollar signs, so those are what must match.

    Examples:
        "at least 9.7500% x $1,000" -> "at least 9.7500% $1 000"
        ""Index"" (curly quotes) -> "index"
        "Russell 2000(R)" -> "russell 2000"
    """
    lowered = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"[^a-z0-9.%$]+", " ", lowered).strip()


def verify_quote(quote: str, normalised_text: str) -> tuple[bool, str]:
    """Check that a citation quote appears verbatim in normalised filing text.

    Models join non-adjacent passages with an ellipsis, so the quote is split
    on ``...`` and ``…`` into pieces; each piece must appear in order.

    Returns ``(verified, reason)``. Reasons, checked in this order:

    - ``"empty"`` if no pieces remain after splitting and normalising;
    - ``"too short"`` if any piece is shorter than ``MIN_QUOTE_CHARS``;
    - ``"not found: <first missing piece>"`` unless the pieces appear in
      order with word boundaries;
    - otherwise ``"found"``.

    Word boundary: the characters next to a match may not be letters or digits, and a period
    next to it counts as part of the token when a letter or digit is on its other side. So
    "000" never matches inside "2000", "at least 9" never matches "at least 9.75", but a
    sentence-final period ("... was 6,672.41.") does not block a match.
    """
    pieces: list[str] = []
    for raw in re.split(r"\.{3,}|…", quote):
        piece = normalise_for_match(raw)
        if piece:
            pieces.append(piece)

    if not pieces:
        return (False, "empty")

    for piece in pieces:
        if len(piece) < MIN_QUOTE_CHARS:
            return (False, "too short")

    pos = 0
    for piece in pieces:
        bounded = re.compile(
            rf"(?<![a-z0-9])(?<![a-z0-9]\.){re.escape(piece)}(?![a-z0-9])(?!\.[a-z0-9])"
        )
        match = bounded.search(normalised_text, pos)
        if match is None:
            return (False, f"not found: {piece}")
        pos = match.end()

    return (True, "found")


@dataclass(frozen=True)
class QuoteCheck:
    """Result of checking one cited field against the filing text."""

    path: str
    quote: str | None
    verified: bool
    reason: str


def verify_termsheet(ts: TermSheet, filing_text: str) -> list[QuoteCheck]:
    """Verify every cited field of a term sheet against the filing text.

    The filing is normalised once. For each ``(path, cited)`` in
    ``ts.cited_fields()``: if ``cited.citation is None``, record
    ``QuoteCheck(path, None, False, "no citation")`` and leave it;
    otherwise run :func:`verify_quote`, set ``cited.citation.verified`` to
    the result (True or False, never left None), and record it.

    Returns the checks in ``cited_fields`` order.
    """
    normalised_text = normalise_for_match(filing_text)
    checks: list[QuoteCheck] = []
    for path, cited in ts.cited_fields():
        if cited.citation is None:
            checks.append(QuoteCheck(path=path, quote=None, verified=False, reason="no citation"))
            continue
        verified, reason = verify_quote(cited.citation.quote, normalised_text)
        cited.citation.verified = verified
        checks.append(QuoteCheck(path=path, quote=cited.citation.quote, verified=verified, reason=reason))
    return checks
