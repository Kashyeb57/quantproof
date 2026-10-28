"""Business-day calendar helpers.

Pure Python only (``datetime`` standard library). No holidays, no network.

U.S. market holidays are NOT modelled, so a date computed here can be
1 day early around a holiday (for example, a payment date that would in
reality move past Thanksgiving). The effect on discounting is negligible
for the notes priced by this project -- one day of discounting on a
payment is far below Monte Carlo standard error -- and the limitation is
stated here so it is never hidden.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

#: English words for the numbers one to ten, as filings write them.
_NUMBER_WORDS: dict[str, int] = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}

#: English ordinal words for first to tenth, as filings write them.
_ORDINAL_WORDS: dict[str, int] = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
}

#: One token that can state a business-day count: digits ("5"), a digit
#: ordinal ("5th", "1st", "2nd", "3rd"), a number word ("five") or an
#: ordinal word ("fifth").
_COUNT_TOKEN = (
    r"\d+(?:st|nd|rd|th)?"
    r"|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth"
    r"|one|two|three|four|five|six|seven|eight|nine|ten"
)

#: "<N> business day(s) after" or "<N> business day(s) following".
_LAG_RE = re.compile(
    rf"\b({_COUNT_TOKEN})\s+business\s+days?\s+(?:after|following)\b",
    re.IGNORECASE,
)


def add_business_days(d: date, n: int) -> date:
    """Return the date ``n`` business days after ``d``.

    A business day is a weekday (Monday to Friday). Saturdays and Sundays
    are skipped. U.S. market holidays are NOT modelled, so the result can
    be 1 day early around a holiday (discounting effect negligible; stated
    in the module docstring).

    Args:
        d: The starting date.
        n: How many business days to step forward. Must be ``>= 0``.

    Returns:
        The resulting date. If ``n == 0``, returns ``d`` itself when ``d``
        is a weekday, else the next weekday (a payment date cannot fall
        on a weekend, so a zero lag still rolls forward off a weekend).

    Raises:
        ValueError: If ``n`` is negative.
    """
    if n < 0:
        raise ValueError(f"n must be >= 0, got {n}")
    result = d
    if n == 0:
        while result.weekday() >= 5:  # 5 = Saturday, 6 = Sunday.
            result += timedelta(days=1)
        return result
    remaining = n
    while remaining > 0:
        result += timedelta(days=1)
        if result.weekday() < 5:
            remaining -= 1
    return result


def parse_business_day_lag(text: str) -> int | None:
    """Read a business-day lag out of filing text, or return ``None``.

    Finds ``"<N> business day(s) after"`` or ``"<N> business day(s)
    following"`` (case-insensitive), where ``N`` is written as digits
    (``"5"``), as a word (``"five"``, one to ten), or as an ordinal
    (``"fifth"``, first to tenth, or ``"5th"``).

    Examples:
        "The fifth business day following the Call Observation Date" -> 5
        "Three business days after the applicable call date" -> 3
        "Approximately the fifth business day following" -> 5
        "the Maturity Date" -> None
    """
    match = _LAG_RE.search(text)
    if match is None:
        return None
    token = match.group(1).lower()
    if token in _ORDINAL_WORDS:
        return _ORDINAL_WORDS[token]
    if token in _NUMBER_WORDS:
        return _NUMBER_WORDS[token]
    # Digits, possibly with an ordinal suffix ("5", "5th", "1st", ...).
    digits = re.sub(r"(?:st|nd|rd|th)$", "", token)
    return int(digits)
