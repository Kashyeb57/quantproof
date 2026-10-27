"""Cross-check one extraction against a second, independent extraction.

A second model extracts the same filing independently.  The two term
sheets are diffed field by field, and each open disagreement may be sent
back to a model with the filing text for a quoted recheck.  The first
term sheet is never changed here: the user confirms disagreements and
the application applies any choices.

Until ``BASETEN_API_KEY`` exists, the brief's intended second model,
Muse Spark Open on Baseten, cannot be used.  The default second run
therefore uses a different Muse Spark version.  That is weaker
independence than two genuinely different model services, and callers
should state that limitation where the cross-check is presented.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import Enum
from typing import Any

from quantproof.agent import client, extract
from quantproof.agent.extract import Extraction, ExtractionError, extract_termsheet
from quantproof.core.termsheet import TermSheet
from quantproof.ingest.html import Filing
from quantproof.validation.quotes import normalise_for_match, verify_quote


@dataclass
class FieldDiff:
    """One disagreement between two extracted term sheets.

    Attributes:
        path: Cited-field path, or ``"unsupported_features"`` for the
            unsupported-feature list compared as a whole.
        first: Value from the first term sheet, or ``None`` if absent.
        second: Value from the second term sheet, or ``None`` if absent.
        kind: ``"value"``, ``"only_in_first"`` or ``"only_in_second"``.
        status: ``"open"``, ``"resolved_first"``, ``"resolved_second"`` or
            ``"unresolved"``.
        quote: Verbatim filing quote supplied by a successful recheck.
        note: Explanation, particularly for an unresolved recheck.
    """

    path: str
    first: object
    second: object
    kind: str
    status: str = "open"
    quote: str | None = None
    note: str = ""


def _same(a: object, b: object, rel_tol: float = 1e-9) -> bool:
    """Return whether two extracted field values should be considered equal.

    Numbers are compared with :func:`math.isclose` using ``rel_tol`` and
    an absolute tolerance of ``1e-12``.  Strings are compared after
    case-folding and collapsing all whitespace runs.  Enum members are
    compared by their ``.value``; an enum may therefore match the raw
    value returned in a JSON recheck reply.  Dates compare by calendar
    equality, and an ISO ``YYYY-MM-DD`` string may match a date.  All
    other values use ordinary equality.  Booleans only match booleans.
    """
    if isinstance(a, Enum):
        a = a.value
    if isinstance(b, Enum):
        b = b.value

    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b

    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(float(a), float(b), rel_tol=rel_tol, abs_tol=1e-12)

    if isinstance(a, datetime):
        a = a.date()
    if isinstance(b, datetime):
        b = b.date()

    if isinstance(a, date) and isinstance(b, str):
        try:
            b = date.fromisoformat(b.strip())
        except ValueError:
            return False
    elif isinstance(a, str) and isinstance(b, date):
        try:
            a = date.fromisoformat(a.strip())
        except ValueError:
            return False

    if isinstance(a, date) or isinstance(b, date):
        return a == b

    if isinstance(a, str) and isinstance(b, str):
        return " ".join(a.casefold().split()) == " ".join(b.casefold().split())

    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y, rel_tol=rel_tol) for x, y in zip(a, b))

    return a == b


def diff_termsheets(first: TermSheet, second: TermSheet) -> list[FieldDiff]:
    """Diff the cited values of two term sheets, sorted by field path.

    Fields are taken from ``cited_fields()`` on both term sheets and are
    keyed by path.  Only values are compared; quotes and verification
    status are ignored.  A path present on only one side produces an
    ``"only_in_first"`` or ``"only_in_second"`` diff, and a path whose
    values differ under :func:`_same` produces a ``"value"`` diff.

    The ``unsupported_features`` entries are additionally compared as one
    field named ``"unsupported_features"``, using the cited string values
    as a list.  To avoid reporting that same list both as a whole and
    entry by entry, the individual ``unsupported_features[i]`` paths are
    represented by that aggregate field.
    """
    first_values: dict[str, object] = {
        path: cited.value
        for path, cited in first.cited_fields()
        if not path.startswith("unsupported_features[")
    }
    second_values: dict[str, object] = {
        path: cited.value
        for path, cited in second.cited_fields()
        if not path.startswith("unsupported_features[")
    }

    first_values["unsupported_features"] = [
        cited.value for cited in first.unsupported_features
    ]
    second_values["unsupported_features"] = [
        cited.value for cited in second.unsupported_features
    ]

    diffs: list[FieldDiff] = []
    for path in sorted(set(first_values) | set(second_values)):
        if path not in second_values:
            diffs.append(
                FieldDiff(
                    path=path,
                    first=first_values[path],
                    second=None,
                    kind="only_in_first",
                )
            )
        elif path not in first_values:
            diffs.append(
                FieldDiff(
                    path=path,
                    first=None,
                    second=second_values[path],
                    kind="only_in_second",
                )
            )
        elif not _same(first_values[path], second_values[path]):
            diffs.append(
                FieldDiff(
                    path=path,
                    first=first_values[path],
                    second=second_values[path],
                    kind="value",
                )
            )

    return sorted(diffs, key=lambda diff: diff.path)


RECHECK_PROMPT: str = """Answer from the filing only. Do not use outside knowledge and do not guess.

You will be given filing text and one disputed structured-note field with two candidate values. Decide which candidate the filing supports.

Reply with a single JSON object only, in exactly this form:
{"value": ..., "quote": "<verbatim filing text>", "choice": "first"|"second"|"neither"}

The "value" must be the candidate value you choose. The "quote" must be copied verbatim from the filing and must support that value. Use "neither" when the filing supports neither candidate."""


def _display_value(value: object) -> str:
    """Render a candidate value compactly for a recheck prompt."""

    def default(obj: object) -> object:
        if isinstance(obj, Enum):
            return obj.value
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        return str(obj)

    return json.dumps(value, default=default, ensure_ascii=False)


def build_recheck_messages(filing_text: str, diff: FieldDiff) -> list[dict]:
    """Build messages asking a model to recheck one disputed field.

    The filing text is placed first in the user message, before the
    question, so the stable filing prefix can be cached.  The question
    names the field path and both candidate values.
    """
    question = (
        "Recheck this disputed field from the filing text above.\n"
        f"Field: {diff.path}\n"
        f"First candidate value: {_display_value(diff.first)}\n"
        f"Second candidate value: {_display_value(diff.second)}\n"
        'Reply with JSON only: {"value": ..., "quote": "<verbatim filing text>", '
        '"choice": "first"|"second"|"neither"}.'
    )
    return [
        {"role": "system", "content": RECHECK_PROMPT},
        {"role": "user", "content": "Filing text:\n" + filing_text + "\n\n" + question},
    ]


def recheck(
    filing: Filing,
    diffs: list[FieldDiff],
    *,
    chat=client.chat,
    model: str = "muse-spark-1.3",
    max_tokens: int = 4000,
) -> list[FieldDiff]:
    """Recheck every open diff against the filing and return new diffs.

    For each diff with status ``"open"``, the model is called with
    purpose ``"recheck"`` and its reply is parsed with
    :func:`quantproof.agent.extract.parse_json_reply` (called through the
    ``extract`` module, so tests may patch ``extract.parse_json_reply``).
    A reply whose ``finish_reason`` is not ``"stop"`` may be truncated or
    otherwise incomplete, so it is never trusted: the diff becomes
    ``"unresolved"`` with a note saying so.  A choice of ``"first"`` or
    ``"second"`` is accepted only when both conditions hold:

    1. the reply quote is verified by
       :func:`quantproof.validation.quotes.verify_quote` against the
       filing text normalised with
       :func:`quantproof.validation.quotes.normalise_for_match`, the same
       normalisation used by ``verify_termsheet``; and
    2. the chosen candidate value is the same, under :func:`_same`, as
       the reply's ``"value"``.

    Otherwise the diff becomes ``"unresolved"`` with an explanatory note.
    Any exception from the chat call or from parsing also becomes an
    ``"unresolved"`` diff whose note is the error message; this function
    never raises for such failures.  Input diffs are not mutated.
    """
    normalised_text = normalise_for_match(filing.text)
    results: list[FieldDiff] = []

    for diff in diffs:
        if diff.status != "open":
            results.append(replace(diff))
            continue

        try:
            result = chat(
                build_recheck_messages(filing.text, diff),
                model=model,
                max_tokens=max_tokens,
                purpose="recheck",
            )

            finish_reason = getattr(result, "finish_reason", "stop")
            if finish_reason != "stop":
                results.append(
                    replace(
                        diff,
                        status="unresolved",
                        note=(
                            "recheck reply did not finish with 'stop' "
                            f"(finish_reason={finish_reason!r}); reply not trusted"
                        ),
                    )
                )
                continue

            reply = extract.parse_json_reply(result.text)
            choice = reply.get("choice")
            quote = reply.get("quote")
            reply_value = reply.get("value")

            if choice not in ("first", "second"):
                results.append(
                    replace(
                        diff,
                        status="unresolved",
                        note=f"recheck did not choose first or second: {choice!r}",
                    )
                )
                continue

            if not isinstance(quote, str) or not quote.strip():
                results.append(
                    replace(
                        diff,
                        status="unresolved",
                        note="recheck quote is missing or empty",
                    )
                )
                continue

            verified, reason = verify_quote(quote, normalised_text)
            if not verified:
                results.append(
                    replace(
                        diff,
                        status="unresolved",
                        note=f"recheck quote not verified: {reason}",
                    )
                )
                continue

            chosen = diff.first if choice == "first" else diff.second
            if not _same(chosen, reply_value):
                results.append(
                    replace(
                        diff,
                        status="unresolved",
                        note=(
                            f"recheck value does not match the {choice} "
                            f"candidate value: {reply_value!r}"
                        ),
                    )
                )
                continue

            results.append(
                replace(
                    diff,
                    status="resolved_first" if choice == "first" else "resolved_second",
                    quote=quote,
                )
            )
        except Exception as exc:  # Chat and parsing failures are user-facing diffs, not crashes.
            results.append(replace(diff, status="unresolved", note=str(exc)))

    return results


@dataclass
class CrossCheck:
    """The result of extracting a filing a second time and diffing it."""

    first: Extraction
    second: Extraction | None
    diffs: list[FieldDiff]
    second_model: str
    error: str | None

    @property
    def needs_user(self) -> list[FieldDiff]:
        """Diffs the user must see to confirm the first extraction.

        Every disagreement that the first extraction did not win is shown:
        unresolved diffs, open diffs, and diffs resolved in favour of the
        second extraction.
        """
        return [diff for diff in self.diffs if diff.status != "resolved_first"]

    @property
    def agreed(self) -> bool:
        """Whether a second extraction exists and produced no diffs."""
        return self.second is not None and not self.diffs


def crosscheck(
    filing: Filing,
    first: Extraction,
    *,
    chat=client.chat,
    second_model: str = "muse-spark-1.2",
    recheck_model: str = "muse-spark-1.3",
) -> CrossCheck:
    """Run a second extraction, diff it against the first, and recheck diffs.

    The second extraction uses ``second_model``.  The brief's intended
    second model is Muse Spark Open on Baseten; until
    ``BASETEN_API_KEY`` exists, the default second run uses a different
    Muse Spark version, which is weaker independence than a genuinely
    separate model service.

    If the second extraction raises
    :class:`~quantproof.agent.extract.ExtractionError`, a ``CrossCheck``
    with ``second=None``, no diffs, and the error text is returned.
    Otherwise the two term sheets are diffed and open diffs are rechecked
    with ``recheck_model``.  The first term sheet is never changed.
    """
    try:
        second = extract_termsheet(filing, chat=chat, model=second_model)
    except ExtractionError as exc:
        return CrossCheck(
            first=first,
            second=None,
            diffs=[],
            second_model=second_model,
            error=str(exc),
        )

    diffs = diff_termsheets(first.termsheet, second.termsheet)
    diffs = recheck(filing, diffs, chat=chat, model=recheck_model)
    return CrossCheck(
        first=first,
        second=second,
        diffs=diffs,
        second_model=second_model,
        error=None,
    )
