"""Extract a TermSheet from a filing via Muse Spark.

The model only reads and quotes. It never computes anything. This module
builds the prompt, parses the model's JSON reply into a TermSheet where
every value is a Cited with a Citation, and runs the quote verifier.

Flow: build_messages -> client.chat -> parse_json_reply -> to_termsheet
-> verify_termsheet. Every number shown to a user comes from the engine
output, never from the model.
"""

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from quantproof.agent import client
from quantproof.core.termsheet import (
    Autocall,
    AutocallObservation,
    BarrierObservation,
    Cited,
    Citation,
    ContingentCoupon,
    Dependency,
    Downside,
    DownsideKind,
    TermSheet,
    Underlying,
)
from quantproof.ingest.html import Filing
from quantproof.validation.quotes import QuoteCheck, verify_termsheet

SYSTEM_PROMPT: str = """You read structured-note offering documents and return their terms as JSON.

You only read and quote. You never compute, price, infer, or guess.

A cited field "F" is an object {"value": ..., "quote": "...", "section": ...}.
- "value" is the extracted value.
- "quote" is the exact filing text supporting it. Copy the text exactly as it
  appears in the filing. The quote must contain the value itself as the filing
  writes it (for "Barrier Amount ... 70.00%" quote the 70.00%, not just the
  label). Keep it short: one sentence or one table row, under 300 characters.
  Join two separate passages with "..." only when needed.
- "section" is the nearest heading or page label (for example "Key Terms",
  "PS-1", "Cover"), or null if there is none.

Return a single JSON object with exactly these top level keys:

- issuer: F(str).
- guarantor: F(str) or null.
- cusip: F(str) or null.
- is_preliminary: F(bool). True when the filing is preliminary / subject to change.
- underlyings: list of {"name": F(str), "ticker": F(str) or null,
  "initial_level": F(number) or null, "reference_level": F(number) or null,
  "reference_date": F("YYYY-MM-DD") or null}.
- dependency: F("worst_of").
- notional: F(number).
- issue_price: F(number).
- pricing_date: F("YYYY-MM-DD").
- issue_date: F("YYYY-MM-DD").
- maturity_date: F("YYYY-MM-DD").
- final_review_date: F("YYYY-MM-DD").
- downside: {"kind": F("barrier" | "buffer"),
  "level": F(fraction of initial, for example 0.70),
  "observation": F("final_only" | "each_review" | "daily"),
  "gearing": F(number) or null}.
- autocall: null or {"trigger": F(fraction),
  "amounts_are_minimums": F(bool),
  "review_dates": F(list of "YYYY-MM-DD"),
  "payment_dates": F(list of "YYYY-MM-DD"),
  "call_amounts": F(list of numbers)}.
- coupon: null or {"barrier": F(fraction), "amount": F(number), "memory": F(bool)}.
- estimated_value: F(number) or null.
- estimated_value_floor: F(number) or null.
- max_selling_commission: F(number) or null.
- missing_or_ambiguous: list of strings, one sentence per item the filing
  does not state clearly.

What the fields mean:
- notional: the principal amount of one note (e.g. 1000 for "$1,000 principal amount note").
- issue_price: the price to the public of one note.
- is_preliminary: true for a preliminary pricing supplement (terms subject to completion).
- dependency: "worst_of" when the payoff follows the lesser performing underlying; also use
  "worst_of" for a single underlying.
- initial_level: the underlying's initial value as a number, when the filing states it.
- reference_level / reference_date: the most recent closing level the filing quotes for the
  underlying and the date of that close (for example "The closing level of the ... Index on
  November 17, 2025 was 2,341.377"). Not the initial or final value.
- final_review_date: the last date the underlyings are observed (the final review or
  valuation date), not the maturity date.
- downside.kind: "barrier" if, once the final level is below the barrier, the holder loses 1%
  for every 1% the underlying fell from its initial level; "buffer" if only losses beyond the
  buffer are borne. downside.level is that barrier or buffer as a fraction of initial.
- downside.observation: "final_only" if the barrier is checked only on the final review date,
  "each_review" if on every review date, "daily" if every trading day.
- downside.gearing: loss per 1% below the level, only when the filing states one; else null.
- autocall.trigger: the level (fraction of initial) every underlying must close at or above
  for the note to be called.
- coupon: a contingent coupon paid when the underlyings are at or above a coupon barrier;
  null if the note pays no coupon. memory is true if missed coupons are paid later.
- estimated_value: the issuer's estimated value of one note (e.g. "approximately $954.00
  per $1,000"); estimated_value_floor: the stated minimum for it ("will not be less than");
  max_selling_commission: the maximum selling commission per note.

Conventions you must follow:
- Levels and barriers are fractions of the initial level (70.00% becomes 0.70).
- Money amounts are per one note of `notional` and include principal (a call
  premium of 9.75% on $1,000 gives call amount 1097.5).
- A preliminary filing's "at least" amounts are the stated minimums, with
  amounts_are_minimums true.
- initial_level is null when the filing says it is set on the pricing date.
- A payment date written as "the Maturity Date" is the maturity date.
- review_dates, payment_dates and call_amounts have one entry per review date,
  in order.
- If something is not in the filing use null and add a sentence to
  missing_or_ambiguous. Never guess.
- Reply with the JSON object only, no prose, no code fences.
"""


class ExtractionError(ValueError):
    """The model reply (or chat call) could not become a TermSheet."""

    def __init__(self, problems: list[str]) -> None:
        self.problems: list[str] = list(problems)
        super().__init__("; ".join(self.problems))


@dataclass
class Extraction:
    """A TermSheet parsed from a model reply, with verification results."""

    termsheet: TermSheet
    checks: list[QuoteCheck]
    result: client.ChatResult | None
    raw: dict

    @property
    def unverified(self) -> list[str]:
        """Paths of fields without a verified citation."""
        return self.termsheet.unverified_fields()


def build_messages(filing_text: str, image_parts: list[dict] = ()) -> list[dict]:
    """Build chat messages with stable content first for prefix caching.

    The system message is SYSTEM_PROMPT. The user content is a list of parts:
    the filing text first, then the image parts, then a short instruction to
    extract now (images are the filing's payoff diagrams).
    """
    content: list[dict] = [{"type": "text", "text": "Filing text:\n" + filing_text}]
    for part in image_parts:
        content.append(part)
    content.append(
        {
            "type": "text",
            "text": "Extract the terms now. The images are the filing's payoff "
            "diagrams. Reply with the JSON object only.",
        }
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]


def parse_json_reply(text: str) -> dict:
    """Parse the model's JSON reply into a dict.

    Strips a ```json fence if present, otherwise takes the substring from the
    first "{" to the last "}". Raises ExtractionError when parsing fails or
    the top level value is not an object.
    """
    candidate: str = text.strip()
    if "```" in candidate:
        fence = re.search(r"```(?:json)?\s*(.*?)\s*```", candidate, re.DOTALL)
        if fence is not None:
            candidate = fence.group(1).strip()
    else:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidate = candidate[start : end + 1]
    try:
        obj = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ExtractionError([f"reply is not valid JSON: {exc}"]) from exc
    if not isinstance(obj, dict):
        raise ExtractionError(
            [f"reply JSON must be an object, got {type(obj).__name__}"]
        )
    return obj


def to_termsheet(data: dict) -> TermSheet:
    """Turn a parsed model reply into a TermSheet.

    Every cited field becomes Cited(value, Citation(quote, section)); a null optional field
    becomes None; dates use date.fromisoformat; enums use Dependency / DownsideKind /
    BarrierObservation; numbers become float. The autocall's three cited lists are zipped into
    AutocallObservation objects, each value with its own Citation carrying the list's quote.
    A null downside gearing keeps the dataclass default.

    Every problem (missing key, wrong type, bad date, unknown enum, list lengths differ, empty
    quote) is collected and raised together as one ExtractionError, so a bad reply is reported
    in full rather than one error at a time. A ValueError from TermSheet's own validation
    (e.g. dates out of order) is re-raised as ExtractionError too.
    """
    if not isinstance(data, dict):
        raise ExtractionError(["reply must be a JSON object"])
    problems: list[str] = []

    # --- value parsers: return the parsed value, or record a problem and return None ---------

    def as_str(value: object, path: str) -> str | None:
        if isinstance(value, str):
            return value
        problems.append(f"{path}.value: expected string, got {type(value).__name__}")
        return None

    def as_bool(value: object, path: str) -> bool | None:
        if isinstance(value, bool):
            return value
        problems.append(f"{path}.value: expected bool, got {type(value).__name__}")
        return None

    def as_number(value: object, path: str) -> float | None:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        problems.append(f"{path}.value: expected number, got {type(value).__name__}")
        return None

    def as_date(value: object, path: str) -> date | None:
        try:
            return date.fromisoformat(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            problems.append(f"{path}.value: bad date {value!r}, expected YYYY-MM-DD")
            return None

    def as_enum(cls):
        def parse(value: object, path: str):
            try:
                return cls(value)
            except ValueError:
                problems.append(f"{path}.value: unknown {cls.__name__} {value!r}")
                return None
        return parse

    def as_list(item_parser):
        def parse(value: object, path: str) -> list | None:
            if not isinstance(value, list):
                problems.append(f"{path}.value: expected list, got {type(value).__name__}")
                return None
            items = [item_parser(v, f"{path}.value[{i}]") for i, v in enumerate(value)]
            return None if any(item is None for item in items) else items
        return parse

    # --- containers ---------------------------------------------------------------------------

    def obj(container: dict, key: str, path: str, allow_null: bool = False) -> dict | None:
        """A plain (uncited) object such as downside or autocall."""
        if key not in container:
            problems.append(f"{path}: missing required key {key!r}")
        elif container[key] is None:
            if not allow_null:
                problems.append(f"{path}: expected object, got null")
        elif not isinstance(container[key], dict):
            problems.append(f"{path}: expected object, got {type(container[key]).__name__}")
        else:
            return container[key]
        return None

    def cited(container: dict, key: str, path: str, parse, allow_null: bool = False) -> Cited | None:
        """A cited field {"value", "quote", "section"} -> Cited, or None (null or a problem)."""
        if key not in container:
            problems.append(f"{path}: missing required key {key!r}")
            return None
        raw = container[key]
        if allow_null and isinstance(raw, dict) and raw.get("value") is None:
            raw = None  # the model sometimes writes {"value": null, "quote": ...} for "absent"
        if raw is None:
            if not allow_null:
                problems.append(f"{path}: expected cited field, got null")
            return None
        if not isinstance(raw, dict) or "value" not in raw:
            problems.append(f"{path}: expected an object with 'value' and 'quote'")
            return None
        quote, section = raw.get("quote"), raw.get("section")
        if not isinstance(quote, str) or not quote.strip():
            problems.append(f"{path}.quote: empty quote")
            return None
        if section is not None and not isinstance(section, str):
            problems.append(f"{path}.section: expected string or null")
            section = None
        value = parse(raw["value"], path)
        return None if value is None else Cited(value, Citation(quote, section))

    def split(c: Cited) -> list[Cited]:
        """A cited list -> one Cited per item, each with its own Citation (same quote)."""
        return [Cited(v, Citation(c.citation.quote, c.citation.section)) for v in c.value]

    # --- document -----------------------------------------------------------------------------

    fields = dict(
        issuer=cited(data, "issuer", "issuer", as_str),
        guarantor=cited(data, "guarantor", "guarantor", as_str, allow_null=True),
        cusip=cited(data, "cusip", "cusip", as_str, allow_null=True),
        is_preliminary=cited(data, "is_preliminary", "is_preliminary", as_bool),
        dependency=cited(data, "dependency", "dependency", as_enum(Dependency)),
        notional=cited(data, "notional", "notional", as_number),
        issue_price=cited(data, "issue_price", "issue_price", as_number),
        pricing_date=cited(data, "pricing_date", "pricing_date", as_date),
        issue_date=cited(data, "issue_date", "issue_date", as_date),
        maturity_date=cited(data, "maturity_date", "maturity_date", as_date),
        final_review_date=cited(data, "final_review_date", "final_review_date", as_date),
        estimated_value=cited(data, "estimated_value", "estimated_value", as_number, allow_null=True),
        estimated_value_floor=cited(data, "estimated_value_floor", "estimated_value_floor",
                                    as_number, allow_null=True),
        max_selling_commission=cited(data, "max_selling_commission", "max_selling_commission",
                                     as_number, allow_null=True),
    )

    # --- underlyings --------------------------------------------------------------------------

    underlyings: list[Underlying] = []
    raw_unds = data.get("underlyings")
    if not isinstance(raw_unds, list) or not raw_unds:
        problems.append("underlyings: expected a non-empty list")
        raw_unds = []
    for i, entry in enumerate(raw_unds):
        base = f"underlyings[{i}]"
        if not isinstance(entry, dict):
            problems.append(f"{base}: expected object, got {type(entry).__name__}")
            continue
        underlyings.append(Underlying(
            name=cited(entry, "name", f"{base}.name", as_str),
            ticker=cited(entry, "ticker", f"{base}.ticker", as_str, allow_null=True),
            initial_level=cited(entry, "initial_level", f"{base}.initial_level", as_number,
                                allow_null=True),
            reference_level=cited(entry, "reference_level", f"{base}.reference_level", as_number,
                                  allow_null=True),
            reference_date=cited(entry, "reference_date", f"{base}.reference_date", as_date,
                                 allow_null=True),
        ))

    # --- downside -----------------------------------------------------------------------------

    downside = None
    if (raw := obj(data, "downside", "downside")) is not None:
        kind = cited(raw, "kind", "downside.kind", as_enum(DownsideKind))
        level = cited(raw, "level", "downside.level", as_number)
        observation = cited(raw, "observation", "downside.observation", as_enum(BarrierObservation))
        gearing = cited(raw, "gearing", "downside.gearing", as_number, allow_null=True)
        if None not in (kind, level, observation):
            extra = {"gearing": gearing} if gearing is not None else {}  # null keeps the default 1.0
            downside = Downside(kind=kind, level=level, observation=observation, **extra)

    # --- autocall -----------------------------------------------------------------------------

    autocall = None
    if (raw := obj(data, "autocall", "autocall", allow_null=True)) is not None:
        trigger = cited(raw, "trigger", "autocall.trigger", as_number)
        minimums = cited(raw, "amounts_are_minimums", "autocall.amounts_are_minimums", as_bool)
        reviews = cited(raw, "review_dates", "autocall.review_dates", as_list(as_date))
        payments = cited(raw, "payment_dates", "autocall.payment_dates", as_list(as_date))
        amounts = cited(raw, "call_amounts", "autocall.call_amounts", as_list(as_number))
        if None not in (trigger, minimums, reviews, payments, amounts):
            lengths = (len(reviews.value), len(payments.value), len(amounts.value))
            if len(set(lengths)) != 1:
                problems.append("autocall: list lengths differ: review_dates={}, payment_dates={}, "
                                "call_amounts={}".format(*lengths))
            elif lengths[0] == 0:
                problems.append("autocall: expected at least one review date")
            else:
                observations = [AutocallObservation(review_date=r, payment_date=p, call_amount=a)
                                for r, p, a in zip(split(reviews), split(payments), split(amounts))]
                # Autocall.amounts_are_minimums is a plain flag; its quote is checked above.
                autocall = Autocall(trigger=trigger, observations=observations,
                                    amounts_are_minimums=minimums.value)

    # --- coupon -------------------------------------------------------------------------------

    coupon = None
    if (raw := obj(data, "coupon", "coupon", allow_null=True)) is not None:
        barrier = cited(raw, "barrier", "coupon.barrier", as_number)
        amount = cited(raw, "amount", "coupon.amount", as_number)
        memory = cited(raw, "memory", "coupon.memory", as_bool)
        if None not in (barrier, amount, memory):
            coupon = ContingentCoupon(barrier=barrier, amount=amount, memory=memory)

    # --- missing_or_ambiguous -----------------------------------------------------------------

    missing = data.get("missing_or_ambiguous")
    if not isinstance(missing, list) or not all(isinstance(m, str) for m in missing):
        problems.append("missing_or_ambiguous: expected a list of strings")
        missing = []

    if problems:
        raise ExtractionError(problems)
    try:
        return TermSheet(underlyings=underlyings, downside=downside, autocall=autocall,
                         coupon=coupon, missing_or_ambiguous=list(missing), **fields)
    except ValueError as exc:
        raise ExtractionError([str(exc)]) from exc


def extract_termsheet(
    filing: Filing,
    *,
    image_paths: list[Path] = (),
    chat=client.chat,
    model: str = "muse-spark-1.3",
    max_tokens: int = 16000,
) -> Extraction:
    """Run the full extraction: prompt, chat, parse, build, verify.

    Builds messages (with an image part per path), calls chat with
    purpose="extract", raises ExtractionError when finish_reason is not
    "stop", parses the JSON, converts to a TermSheet, and verifies every
    quote against the filing text.
    """
    image_parts = [client.image_part(p) for p in image_paths]
    messages = build_messages(filing.text, image_parts)
    result = chat(messages, model=model, max_tokens=max_tokens, purpose="extract")
    if result.finish_reason != "stop":
        raise ExtractionError(
            [f"extraction cut off or incomplete: finish_reason={result.finish_reason!r}"]
        )
    raw = parse_json_reply(result.text)
    termsheet = to_termsheet(raw)
    checks = verify_termsheet(termsheet, filing.text)
    return Extraction(termsheet=termsheet, checks=checks, result=result, raw=raw)
