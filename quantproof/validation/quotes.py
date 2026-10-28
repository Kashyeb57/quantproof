"""Quote verifier: every extracted term must carry a verbatim filing quote.

AGENTS.md rule 5a requires that each ``Citation.quote`` appear verbatim in the
filing text. This module checks that claim and sets ``citation.verified``.

Two checks are performed by :func:`verify_termsheet`:

1. Quote presence (:func:`verify_quote`): each citation quote (split on
   ellipses into pieces) must appear verbatim, in order, in the filing text
   with word boundaries.
2. Value support (:func:`value_in_quote`): the quote must also contain the
   field's value in a form the filing would write it (numbers with or without
   commas and with varying decimals, percentages, dates in common filings
   spellings). A quote that is just a label (``"Review Dates*:"``) passes
   check 1 but fails check 2, and the field is then unverified with reason
   ``"value not in quote: <value>"``.

Bools and enums are presence-only: :func:`value_forms` returns ``None`` for
them, so only check 1 applies. Their machine values (``True``/``False`` or
enum names) rarely appear verbatim in a filing; a human reads those quotes in
the validation report to confirm the extraction.

Three extra support rules live in :func:`verify_termsheet`:

- A buffer ``downside.level`` quoted as a Buffer Amount must show ``1 - level``.
- A trigger/barrier/level of exactly 1.0 may be quoted as the Initial (or
  Strike) Value without a percent sign.
- A payment date (``autocall.observations[i].payment_date`` or
  ``coupon.observations[i].payment_date``) whose quote states a business-day
  offset instead of the date itself -- for example "the fifth business day
  following the Call Observation Date" -- is supported when
  :func:`quantproof.core.calendar.parse_business_day_lag` reads a lag ``N``
  from the quote and ``add_business_days(review_date, N)`` for the same
  observation equals the payment-date value. This covers payment dates the
  extractor computed from a stated offset; the concrete date never appears
  in the filing, so the ordinary value-in-quote check cannot pass. The
  quote must still pass :func:`verify_quote` (presence) as usual.

No AI, no network. Pure Python standard library only.
"""

from __future__ import annotations

import enum
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Any

from quantproof.core.calendar import add_business_days, parse_business_day_lag
from quantproof.core.termsheet import DownsideKind, TermSheet

#: A normalised quote (or ellipsis piece) shorter than this is too short to be
#: evidence (e.g. "70%" matches almost any filing).
MIN_QUOTE_CHARS = 8

#: Full English month names indexed by month number (1-12). Built explicitly
#: so date formatting never depends on locale-dependent strftime.
_FULL_MONTHS = (
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

#: English month abbreviations indexed by month number (1-12).
_ABBR_MONTHS = (
    "",
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


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


def _find_bounded(piece: str, normalised_text: str, start: int = 0) -> re.Match | None:
    """Find a normalised piece in normalised text with word boundaries.

    Same regex and boundary rules as :func:`verify_quote`: the characters
    next to a match may not be letters or digits, and a period next to it
    counts as part of the token when a letter or digit is on its other side.
    So "000" never matches inside "2000", "at least 9" never matches
    "at least 9.75", but a sentence-final period ("... was 6,672.41.") does
    not block a match.

    Args:
        piece: already-normalised search string.
        normalised_text: already-normalised text to search.
        start: character offset in ``normalised_text`` to start searching.

    Returns:
        The ``re.Match`` if found at or after ``start``, else ``None``.
    """
    bounded = re.compile(
        rf"(?<![a-z0-9])(?<![a-z0-9]\.){re.escape(piece)}(?![a-z0-9])(?!\.[a-z0-9])"
    )
    return bounded.search(normalised_text, start)


def verify_quote(quote: str, normalised_text: str) -> tuple[bool, str]:
    """Check that a citation quote appears verbatim in normalised filing text.

    Models join non-adjacent passages with an ellipsis, so the quote is split
    on ``...`` and ``…`` into pieces; each piece must appear in order.

    Returns ``(verified, reason)``. Reasons, checked in this order:

    - ``"empty"`` if no pieces remain after splitting and normalising;
    - ``"too short"`` if any piece is shorter than ``MIN_QUOTE_CHARS``;
    - ``"out of order: <piece>"`` if a piece exists only before the previous one;
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
        match = _find_bounded(piece, normalised_text, pos)
        if match is None:
            if pos and _find_bounded(piece, normalised_text) is not None:
                # Real text, but earlier than the previous piece: the quote reorders the filing.
                return (False, f"out of order: {piece}")
            return (False, f"not found: {piece}")
        pos = match.end()

    return (True, "found")


def number_forms(x: float) -> set[str]:
    """All filing spellings of a number with 0..6 decimals.

    For each d in 0..6 where ``x`` has at most ``d`` decimals
    (``abs(round(x, d) - x) <= 1e-9 * max(1, abs(x))``), add both the
    comma-grouped (``f"{x:,.{d}f}"``) and plain (``f"{x:.{d}f}"``) forms.

    Intuition: filings write the same amount as "1,000", "1000" or "954.00";
    any of those spellings is evidence for the value.

    Examples:
        954.0 -> {"954", "954.0", "954.00", ...}
        2341.377 -> {"2,341.377", "2341.377", "2,341.3770", ...}
    """
    forms: set[str] = set()
    value = float(x)
    scale = max(1.0, abs(value))
    for d in range(0, 7):
        if abs(round(value, d) - value) <= 1e-9 * scale:
            forms.add(f"{value:,.{d}f}")
            forms.add(f"{value:.{d}f}")
    return forms


def percent_forms(fraction: float) -> set[str]:
    """All filing spellings of a fraction as a percent string.

    Let ``p = round(fraction * 100, 10)``. For each d in 0..6 where ``p`` has
    at most ``d`` decimals (same tolerance as :func:`number_forms`), add the
    comma-grouped and plain forms of ``p`` each followed by ``"%"``.

    Intuition: a barrier of 0.70 is written "70%" or "70.00%"; a coupon of
    0.0975 is written "9.75%" or "9.7500%".

    Examples:
        0.70 -> {"70%", "70.0%", "70.00%", ...}
        0.0975 -> {"9.75%", "9.750%", "9.7500%", ...}
    """
    forms: set[str] = set()
    p = round(float(fraction) * 100, 10)
    scale = max(1.0, abs(p))
    for d in range(0, 7):
        if abs(round(p, d) - p) <= 1e-9 * scale:
            forms.add(f"{p:,.{d}f}%")
            forms.add(f"{p:.{d}f}%")
    return forms


def date_forms(d: date) -> set[str]:
    """Common filing spellings of a calendar date.

    Returns the 8 forms (month names in English, built explicitly without
    locale-dependent strftime):

    - "December 2, 2026", "December 02, 2026",
    - "Dec. 2, 2026", "Dec 2, 2026",
    - "2 December 2026",
    - "2026-12-02",
    - "12/2/2026", "12/02/2026".
    """
    full = _FULL_MONTHS[d.month]
    abbr = _ABBR_MONTHS[d.month]
    return {
        f"{full} {d.day}, {d.year}",
        f"{full} {d.day:02d}, {d.year}",
        f"{abbr}. {d.day}, {d.year}",
        f"{abbr} {d.day}, {d.year}",
        f"{d.day} {full} {d.year}",
        f"{d.year}-{d.month:02d}-{d.day:02d}",
        f"{d.month}/{d.day}/{d.year}",
        f"{d.month}/{d.day:02d}/{d.year}",
    }


def value_forms(
    value: Any, *, notional: float | None = None, per_notional: bool = False
) -> set[str] | None:
    """All filing spellings of an extracted value, or None if presence-only.

    - str -> ``{value}`` (matched verbatim after normalisation).
    - date -> :func:`date_forms`.
    - bool or Enum -> ``None`` (no written form; quote presence only).
    - int/float -> ``number_forms(v) | percent_forms(v)``, because a filing
      may write a level as points or as a percent.
    - anything else -> ``None``.
    - if ``per_notional`` and ``notional``: the value is an amount per note
      that may be written as a share of the notional, so also add, with
      ``premium = v - notional`` when ``v > notional`` (a call amount
      includes principal) else ``v``:
      ``number_forms(premium) | percent_forms(premium / notional)``.

    Example: 1097.5 with notional 1000 -> includes "9.75%", "9.7500%" and
    "97.50", since the filing may say "at least 9.7500% x $1,000".
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, enum.Enum):
        return None
    if isinstance(value, str):
        return {value}
    if isinstance(value, date):
        return set(date_forms(value))
    if isinstance(value, (int, float)):
        v = float(value)
        forms: set[str] = set(number_forms(v) | percent_forms(v))
        if per_notional and notional:
            n = float(notional)
            premium = v - n if v > n else v
            forms |= number_forms(premium) | percent_forms(premium / n)
        return forms
    return None


def value_in_quote(
    value: Any, quote: str, *, notional: float | None = None, per_notional: bool = False
) -> bool | None:
    """Check that a quote contains the value in a filing spelling.

    Returns ``None`` when :func:`value_forms` is ``None`` (bool/Enum/other:
    presence-only). Otherwise normalise the quote (after replacing ``...``
    runs and ``…`` with a space) and each candidate form with
    :func:`normalise_for_match`, and return ``True`` if any form is found
    with :func:`_find_bounded` in the normalised quote, else ``False``.

    Intuition: proves the quote shows the value, not just its label.
    """
    forms = value_forms(value, notional=notional, per_notional=per_notional)
    if forms is None:
        return None
    cleaned = re.sub(r"\.{3,}|…", " ", quote)
    normalised_quote = normalise_for_match(cleaned)
    for form in forms:
        normalised_form = normalise_for_match(form)
        if not normalised_form:
            continue
        if _find_bounded(normalised_form, normalised_quote) is not None:
            return True
    return False


#: Fields holding a level as a fraction of initial, which filings sometimes write indirectly.
_LEVEL_PATHS = ("autocall.trigger", "coupon.barrier", "downside.level")
#: Names of the initial level; "at or above the Initial Value" is a level of exactly 1.0.
_INITIAL_LEVEL_NAMES = ("initialvalue", "strikevalue", "initiallevel")

#: Payment-date paths whose value may be derived from a business-day offset quote.
_PAYMENT_DATE_PATH_RE = re.compile(r"^(autocall|coupon)\.observations\[(\d+)\]\.payment_date$")


def _level_support(path: str, value: Any, quote: str, ts: TermSheet) -> bool | None:
    """Support rules for levels that a filing states indirectly; None if no rule applies.

    - A buffer quoted as a "Buffer Amount" states 1 - level: "Buffer Amount: 40.00%" means
      losses start at 0.60 of initial, so it supports 0.60 and not 0.40.
    - A quote with no percent that names the initial level ("greater than or equal to the
      Initial Value") supports exactly 1.0. "80.00% of the Initial Value" has a percent, so
      the ordinary check applies and it does not support 1.0.
    """
    if path not in _LEVEL_PATHS:
        return None
    # Compare without spaces so "Buffer Amount" and "BufferAmount" match alike.
    compact = normalise_for_match(re.sub(r"\.{3,}|…", " ", quote)).replace(" ", "")
    if (path == "downside.level" and ts.downside.kind.value is DownsideKind.BUFFER
            and "bufferamount" in compact):
        return value_in_quote(round(1.0 - value, 10), quote) is True
    if value == 1.0 and "%" not in quote and any(n in compact for n in _INITIAL_LEVEL_NAMES):
        return True
    return None


def _payment_lag_support(path: str, value: Any, quote: str, ts: TermSheet) -> bool | None:
    """Support rule for payment dates derived from a business-day offset.

    Returns ``None`` when the rule does not apply (the path is not an
    autocall/coupon observation payment date, or the quote states no
    readable lag). Otherwise returns ``True`` exactly when the same
    observation's review date plus the lagged number of business days
    equals ``value``, and ``False`` when it does not.

    Intuition: the filing never writes the concrete payment date, only
    "the fifth business day following the Observation Date", so the date
    is supported by showing it is exactly that offset applied to the
    (separately cited) review date.
    """
    match = _PAYMENT_DATE_PATH_RE.match(path)
    if match is None:
        return None
    lag = parse_business_day_lag(quote)
    if lag is None:
        return None
    block = ts.autocall if match.group(1) == "autocall" else ts.coupon
    if block is None:
        return False
    index = int(match.group(2))
    if index >= len(block.observations):
        return False
    review_date = block.observations[index].review_date.value
    if not isinstance(review_date, date) or not isinstance(value, date):
        return False
    return add_business_days(review_date, lag) == value


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

    Two checks per cited field:

    1. Quote presence: :func:`verify_quote` must return ``"found"``.
    2. Value support: if check 1 passes, :func:`value_in_quote` is called
       with ``notional = ts.notional.value`` and ``per_notional = True`` for
       paths ending in ``".call_amount"`` or equal to ``"coupon.amount"``
       (amounts per note that filings often state as a premium percent
       times the notional), else ``per_notional = False``. If it returns
       ``False``, the field is marked unverified with reason
       ``"value not in quote: <value>"``. If it returns ``None`` (bool,
       Enum or other presence-only values, whose machine spellings do not
       appear verbatim -- a human reads those quotes in the validation
       report) or ``True``, the ``"found"`` result stands.

    Extra support rules:

    Levels (``autocall.trigger``, ``coupon.barrier``, ``downside.level``) first go through
    :func:`_level_support`: a buffer quoted as a "Buffer Amount" must show ``1 - level``, and
    a quote naming "the Initial Value" without a percent supports exactly 1.0.

    Payment dates (``autocall.observations[i].payment_date`` and
    ``coupon.observations[i].payment_date``) whose ordinary value check
    returns ``False`` go through :func:`_payment_lag_support`: if the quote
    states a business-day lag ``N`` and the same observation's review date
    plus ``N`` business days equals the payment-date value, the field is
    supported (verified). The quote must still have passed quote presence
    above; this rule only replaces the value-support check.

    Returns the checks in ``cited_fields`` order.
    """
    normalised_text = normalise_for_match(filing_text)
    notional_value = ts.notional.value

    checks: list[QuoteCheck] = []
    for path, cited in ts.cited_fields():
        if cited.citation is None:
            checks.append(QuoteCheck(path=path, quote=None, verified=False, reason="no citation"))
            continue
        verified, reason = verify_quote(cited.citation.quote, normalised_text)
        if verified:
            field_value = cited.value
            per_notional = path.endswith(".call_amount") or path == "coupon.amount"
            support = _level_support(path, field_value, cited.citation.quote, ts)
            if support is None:
                support = value_in_quote(
                    field_value,
                    cited.citation.quote,
                    notional=notional_value,
                    per_notional=per_notional,
                )
            if support is False:
                # Third extra support rule: a payment date computed from a
                # stated business-day offset is supported if the offset,
                # applied to the same observation's review date, gives it.
                lag_support = _payment_lag_support(
                    path, field_value, cited.citation.quote, ts
                )
                if lag_support is True:
                    support = True
            if support is False:
                verified = False
                reason = f"value not in quote: {field_value}"
        cited.citation.verified = verified
        checks.append(QuoteCheck(path=path, quote=cited.citation.quote, verified=verified, reason=reason))
    return checks
