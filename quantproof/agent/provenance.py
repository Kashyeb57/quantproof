"""Provenance check: the model never states a number.

Every number or date appearing in model-written text must be traceable to
an allowed source: an engine result, a :class:`~quantproof.core.termsheet.TermSheet`,
another supplied source object, or an explicitly stated methodology
constant supplied by the caller.  This module finds numbers and dates in
text, collects the values a caller allows, and reports anything that
cannot be matched.

Numbers inside verbatim filing quotes are citations, not claims made by
the model: they are checked separately by the quote verifier (see
:mod:`quantproof.validation.quotes`), so a caller may exempt verified
quotes from this check.  Unverified quotes never exempt anything, because
the verifier has not confirmed that the quoted text appears in the filing.

No AI and no network calls are made here.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime
from typing import Any


class ProvenanceError(ValueError):
    """Raised when text contains a number or date with no allowed source."""


@dataclass(frozen=True)
class NumberMention:
    """One number exactly as written in a piece of text.

    Attributes:
        text: The number as written, for example ``"$907.72"``, ``"9.6%"``
            or ``"1,000"``.
        value: The parsed numeric value.  A trailing percent sign is *not*
            divided by 100, so ``"9.6%"`` has value ``9.6``.
        decimals: The number of digits shown after the decimal point.
        percent: Whether the written number ends in ``"%"``.
        start: Character offset of the first character in the source text.
        end: Character offset one past the last character in the source text.
    """

    text: str
    value: float
    decimals: int
    percent: bool
    start: int
    end: int


_MONTHS: dict[str, int] = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}

# Longest month spellings first so that, for example, "March" is preferred
# to "Mar" by the regular-expression alternation.
_MONTH_PATTERN = "|".join(sorted(_MONTHS, key=len, reverse=True))

_MONTH_DATE_RE = re.compile(
    rf"\b(?P<month>{_MONTH_PATTERN})\s+(?P<day>\d{{1,2}}),\s*(?P<year>\d{{4}})\b",
    re.IGNORECASE,
)
_ISO_DATE_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})"
    r"(?![A-Za-z0-9_])"
)
_ISO_DATE_STRING_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# A standalone number: optional dollar sign and sign (in either common
# order), digits with optional thousands commas, optional decimals, and an
# optional percent sign.  The surrounding identifier checks are applied by
# the lookarounds in ``_NUMBER_RE`` together with the explicit checks in
# ``find_numbers``.
_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?P<text>"
    r"(?:[+\-\u2212]?\$?|\$[+\-\u2212]?)"  # U+2212 is the typographic minus
    r"(?:\d{1,3}(?:,\d{3})+|\d+)"
    r"(?:\.\d+)?"
    r"%?"
    r")"
    r"(?![A-Za-z0-9_])"
)


def _date_matches(text: str) -> list[tuple[str, date, int, int]]:
    """Return ``(written_text, date, start, end)`` for every date in ``text``.

    Invalid calendar dates, such as February 30, are ignored rather than
    being treated as dates.
    """
    matches: list[tuple[str, date, int, int]] = []

    for match in _MONTH_DATE_RE.finditer(text):
        month = _MONTHS[match.group("month").casefold()]
        try:
            value = date(
                int(match.group("year")), month, int(match.group("day"))
            )
        except ValueError:
            continue
        matches.append((match.group(0), value, match.start(), match.end()))

    for match in _ISO_DATE_RE.finditer(text):
        try:
            value = date(
                int(match.group("year")),
                int(match.group("month")),
                int(match.group("day")),
            )
        except ValueError:
            continue
        matches.append((match.group(0), value, match.start(), match.end()))

    matches.sort(key=lambda item: (item[2], item[3]))
    return matches


def find_dates(text: str) -> list[tuple[str, date]]:
    """Find calendar dates written in ``text``.

    Two filing spellings are recognised:

    - month-name dates such as ``"November 4, 2026"`` or
      ``"Nov 4, 2026"``, using a full or three-letter English month name;
    - ISO dates such as ``"2026-11-04"``.

    Returns:
        A list of ``(text_as_written, date)`` pairs in order of appearance.
    """
    return [(written, value) for written, value, _start, _end in _date_matches(text)]


def _is_markdown_numbering(text: str, start: int, end: int, mention_text: str) -> bool:
    """Return whether a candidate is Markdown heading/list numbering.

    This covers an integer followed by a full stop at the start of a line,
    such as ``"1. First item"``, including after indentation or a simple
    Markdown prefix, and a number used as the numbering of a Markdown
    heading, such as ``"## 2 Overview"``.
    """
    line_start = text.rfind("\n", 0, start) + 1
    prefix = text[line_start:start]
    stripped_prefix = prefix.strip()

    # A number immediately after one or more heading markers is the
    # heading's numbering, not a stated quantity.
    if stripped_prefix and set(stripped_prefix) == {"#"}:
        return True

    has_decimal_point = "." in mention_text
    followed_by_full_stop = end < len(text) and text[end] == "."
    prefix_is_only_markers = prefix.strip(" \t#>*-+") == ""
    return (
        not has_decimal_point
        and followed_by_full_stop
        and prefix_is_only_markers
    )


def _is_list_index(text: str, start: int, end: int, mention_text: str) -> bool:
    """Return whether a candidate is an integer used as a list index.

    This covers field paths such as
    ``"autocall.observations[8].call_amount"``: the integer is directly
    preceded by ``"["`` and directly followed by ``"]"``.  It identifies
    which observation is meant; it is not a quantity claimed by the text.
    Only a plain integer counts: a decimal, a percent, or a signed or
    dollar amount inside brackets is not a list index.
    """
    if start == 0 or end >= len(text):
        return False
    if text[start - 1] != "[" or text[end] != "]":
        return False
    # ``mention_text`` here is the plain-integer candidate: digits with
    # optional thousands commas, with no sign, dollar sign, decimal point
    # or percent sign.
    numeric = mention_text.replace(",", "")
    return numeric.isdigit()


def find_numbers(text: str) -> list[NumberMention]:
    """Find standalone numbers written in ``text``.

    The recognised form is an optional ``"$"``, an optional sign, digits
    with optional thousands commas, optional decimal digits, and an
    optional trailing ``"%"``.  The parsed ``value`` keeps a percent as
    written: ``"9.6%"`` parses to ``9.6``, not ``0.096``.

    The following are skipped:

    - digits inside words or identifiers, such as ``"CUSIP 48136LQH5"``,
      ``"N2"``, ``"p05"`` or ``"ea0306095"``: a letter, digit or underscore
      directly before or after the number disqualifies it (a trailing
      ``"%"`` is allowed);
    - numbers forming part of a date recognised by :func:`find_dates`;
    - Markdown heading numbering and list numbering such as ``"1."`` at
      the start of a line;
    - an integer written as a list index, directly preceded by ``"["``
      and directly followed by ``"]"``, such as the ``8`` in a field path
      like ``"autocall.observations[8].call_amount"``: it identifies an
      element of a list rather than stating a quantity.
    """
    date_spans = [(start, end) for _written, _value, start, end in _date_matches(text)]
    mentions: list[NumberMention] = []

    for match in _NUMBER_RE.finditer(text):
        start, end = match.start("text"), match.end("text")
        written = match.group("text")

        # Do not report the day or year inside "November 4, 2026", or any
        # component of an ISO date; find_dates reports those instead.
        if any(start < date_end and date_start < end for date_start, date_end in date_spans):
            continue

        if _is_markdown_numbering(text, start, end, written):
            continue

        if _is_list_index(text, start, end, written):
            continue

        percent = written.endswith("%")
        # A typographic minus (U+2212) is a minus sign: "−5.2%" is -5.2, so
        # it must not match a positive engine value.
        numeric = written.replace("$", "").replace(",", "").replace("\u2212", "-")
        if percent:
            numeric = numeric[:-1]

        if "." in numeric:
            decimals = len(numeric.rsplit(".", 1)[1])
        else:
            decimals = 0

        try:
            value = float(numeric)
        except ValueError:
            continue

        mentions.append(
            NumberMention(
                text=written,
                value=value,
                decimals=decimals,
                percent=percent,
                start=start,
                end=end,
            )
        )

    return mentions


def _walk_values(source: Any) -> list[Any]:
    """Recursively return leaf values contained in ``source``.

    Dictionaries contribute their keys and values; lists, tuples, sets and
    frozensets contribute their items; dataclass instances, including
    ``Cited`` and ``TermSheet``, contribute their field values.  Other
    objects are returned as single leaf values.  Strings are leaves and are
    not split into characters.
    """
    if isinstance(source, dict):
        values: list[Any] = []
        for key, value in source.items():
            values.extend(_walk_values(key))
            values.extend(_walk_values(value))
        return values
    if isinstance(source, (list, tuple, set, frozenset)):
        values = []
        for item in source:
            values.extend(_walk_values(item))
        return values
    if is_dataclass(source) and not isinstance(source, type):
        values = []
        for field in fields(source):
            values.extend(_walk_values(getattr(source, field.name)))
        return values
    return [source]


def allowed_numbers(*sources: Any) -> list[float]:
    """Collect every numeric value allowed to be stated from ``sources``.

    Each source is searched recursively through dictionaries, lists,
    tuples, sets, dataclasses and ``Cited`` values.  Every ``int`` or
    ``float`` found is included, excluding ``bool`` values.

    For every collected value ``v`` with ``abs(v) <= 1``, ``v * 100`` is
    also included.  This allows a fraction stored by an engine, such as
    ``0.096``, to be written as a percent, ``"9.6%"``.  A ``TermSheet``
    needs no special traversal beyond this rule because its cited numeric
    values are dataclass fields and are therefore collected recursively.
    """
    numbers: list[float] = []
    for source in sources:
        for value in _walk_values(source):
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                numbers.append(float(value))

    result = list(numbers)
    for value in numbers:
        if abs(value) <= 1:
            result.append(value * 100)
    return result


def allowed_dates(*sources: Any) -> set[date]:
    """Collect every date allowed to be stated from ``sources``.

    Sources are searched with the same recursive traversal as
    :func:`allowed_numbers`.  Both ``date`` objects (with ``datetime``
    objects reduced to their calendar date) and strings in exact ISO
    ``YYYY-MM-DD`` form are included.  Invalid ISO-looking strings are
    ignored.
    """
    dates: set[date] = set()
    for source in sources:
        for value in _walk_values(source):
            if isinstance(value, datetime):
                dates.add(value.date())
            elif isinstance(value, date):
                dates.add(value)
            elif isinstance(value, str):
                candidate = value.strip()
                if _ISO_DATE_STRING_RE.match(candidate):
                    try:
                        dates.add(date.fromisoformat(candidate))
                    except ValueError:
                        continue
    return dates


def verified_quotes(ts: Any) -> list[str]:
    """Return text backed by a term sheet's verified citations.

    Why only verified citations: a number inside a verbatim filing quote is a
    citation, not a number stated by the model, and the quote verifier has
    already confirmed that the quote appears in the filing.  An unverified
    quote has not been confirmed, so it must never exempt anything from
    the provenance check; numbers inside it are still reported.

    The same reasoning applies to the value of a cited field when that
    value is itself a string.  For example, an underlying name such as
    ``"S&P 500® Index"`` contains the digits ``500`` as part of a name,
    not as a quantity stated by the model.  Once the field's citation is
    verified, the value text is backed by that verified quote, so the
    value is returned here too and numbers inside it are exempted along
    with numbers inside the quote.  A string value whose citation is not
    verified contributes nothing.

    Args:
        ts: A ``TermSheet`` (or any object with a ``cited_fields()``
            method returning ``(path, cited)`` pairs, where each cited
            field has a ``value`` and a ``citation`` with ``quote`` and
            ``verified`` attributes).

    Returns:
        For every cited field whose ``citation.verified`` is true, in
        ``cited_fields()`` order: first ``citation.quote`` (when it is a
        non-empty string), then the field's ``value`` (when it is a
        non-empty string).  Fields without a citation, without a quote,
        or whose citation is not verified contribute nothing.
    """
    quotes: list[str] = []
    for _path, cited in ts.cited_fields():
        # A cited field carries its citation; be defensive about fields
        # that have none so one odd field cannot break the whole check.
        citation = getattr(cited, "citation", None)
        if citation is None:
            continue
        if getattr(citation, "verified", False) is not True:
            continue
        quote = getattr(citation, "quote", None)
        if isinstance(quote, str) and quote:
            quotes.append(quote)
        # A verified cited string value (for example an underlying name)
        # is backed by its verified citation, so exempt it exactly as the
        # quote itself is exempted.
        value = getattr(cited, "value", None)
        if isinstance(value, str) and value:
            quotes.append(value)
    return quotes


def _blank_out_quotes(text: str, quotes: Iterable[str]) -> str:
    """Return ``text`` with every quoted occurrence replaced by spaces.

    Each quote is blanked out, and so is each piece of a quote split on
    ``"..."`` (an elision in the stored quote), because the rendered text
    may show only one side of an elided quote.  Pieces shorter than 8
    characters are ignored: short fragments such as ``"9.6%"`` could occur
    by coincidence outside any quote and must not exempt real claims.

    Replacements use spaces of exactly the same length as the text they
    replace, so the result has the same length as ``text`` and every
    character offset (for example ``NumberMention.start``) stays valid
    for the original text.
    """
    blanked = text
    candidates: list[str] = []
    for quote in quotes:
        if not isinstance(quote, str) or not quote:
            continue
        candidates.append(quote)
        for piece in quote.split("..."):
            if len(piece) >= 8:
                candidates.append(piece)

    # Longest first, so a full quote is blanked before its own pieces and
    # a longer piece before any shorter piece contained in it.
    for candidate in sorted(set(candidates), key=len, reverse=True):
        if candidate in blanked:
            blanked = blanked.replace(candidate, " " * len(candidate))
    return blanked


@dataclass
class ProvenanceReport:
    """Result of checking one piece of text for unstated numbers.

    Attributes:
        ok: Whether every checked number and date was matched.
        checked: The number of numeric mentions checked.
        unmatched: Numeric mentions with no allowed value at the shown
            rounding precision.
        unmatched_dates: Date texts, as written, whose dates were not in
            the allowed date set.
    """

    ok: bool
    checked: int
    unmatched: list[NumberMention]
    unmatched_dates: list[str]


def check_provenance(
    text: str,
    allowed: list[float],
    dates: set[date] = frozenset(),
    constants: tuple[float, ...] = (),
    quotes: Iterable[str] = (),
) -> ProvenanceReport:
    """Check that every number and date in ``text`` has an allowed source.

    A mention ``x`` shown with ``d`` decimal places matches an allowed
    value ``v`` exactly when::

        abs(x - v) <= 0.5 * 10**-d + 1e-9

    Rounding tolerance is the right rule because text normally states a
    value rounded to the precision shown: an engine value of ``907.724``
    may legitimately be written as ``"$907.72"``, but not as ``"$907.73"``
    or ``"$908"``.  The sign must also agree because the comparison uses
    the signed values as written and as supplied.

    ``constants`` are methodology numbers stated explicitly by the caller,
    such as ``1.96`` for a 95% interval.  They are never model output and
    are matched by the same rounding rule, without any automatic percent
    conversion.

    ``quotes`` are verbatim filing quotes (normally from
    :func:`verified_quotes`).  Numbers and dates inside a quote are
    citations already checked by the quote verifier, not claims, so every
    occurrence of each quote -- and of each piece of a quote split on
    ``"..."``, ignoring pieces shorter than 8 characters -- is blanked
    out with same-length spaces before scanning.  Only pass quotes that
    the quote verifier has confirmed; unverified quotes exempt nothing.

    Dates found by :func:`find_dates` must be present in ``dates``.
    """
    candidates = [float(value) for value in allowed]
    candidates.extend(float(value) for value in constants)

    scanned = _blank_out_quotes(text, quotes)

    mentions = find_numbers(scanned)
    unmatched: list[NumberMention] = []
    for mention in mentions:
        tolerance = 0.5 * 10**-mention.decimals + 1e-9
        if not any(abs(mention.value - candidate) <= tolerance for candidate in candidates):
            unmatched.append(mention)

    unmatched_dates = [
        written for written, value in find_dates(scanned) if value not in dates
    ]

    return ProvenanceReport(
        ok=not unmatched and not unmatched_dates,
        checked=len(mentions),
        unmatched=unmatched,
        unmatched_dates=unmatched_dates,
    )


def require_provenance(
    text: str,
    *sources: Any,
    constants: tuple[float, ...] = (),
    quotes: Iterable[str] = (),
) -> None:
    """Raise :class:`ProvenanceError` if ``text`` states an unsourced value.

    Allowed numbers and dates are collected from ``sources`` with
    :func:`allowed_numbers` and :func:`allowed_dates`.  The error message
    lists every unmatched numeric mention and every unmatched date, as
    written in ``text``.

    ``quotes`` has the same meaning as in :func:`check_provenance`:
    verbatim filing quotes already confirmed by the quote verifier (see
    :func:`verified_quotes`), whose numbers are citations rather than
    claims.  Unverified quotes must not be passed here, and
    :func:`verified_quotes` never returns them.
    """
    report = check_provenance(
        text,
        allowed_numbers(*sources),
        dates=allowed_dates(*sources),
        constants=constants,
        quotes=quotes,
    )
    if report.ok:
        return

    problems: list[str] = []
    if report.unmatched:
        problems.append(
            "unmatched numbers: "
            + ", ".join(mention.text for mention in report.unmatched)
        )
    if report.unmatched_dates:
        problems.append("unmatched dates: " + ", ".join(report.unmatched_dates))
    raise ProvenanceError("; ".join(problems))
