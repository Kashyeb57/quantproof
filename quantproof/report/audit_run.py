"""Market-audit pipeline for one filing: extract, cross-check, price.

This module has no network access itself.  The filing (already downloaded
and parsed by the caller) and the chat callable are injected, so the exact
same code path can run live with ``quantproof.agent.client.chat`` or
offline at zero cost with :class:`Replay`, which replays recorded model
replies saved from a live run.

Cross-check note: the cross-check uses a *different Muse Spark version*
(``SECOND_MODEL = "muse-spark-1.2"``) as the second extraction.  The
brief's intended second model, Muse Spark Open via Baseten, is not
available (no ``BASETEN_API_KEY``), so this is a weaker independence
check than two genuinely different model services, and audit outputs
should be read with that limitation stated.

Pricing follows brief section 6 / AGENTS.md rule 5: risk-neutral Monte
Carlo for the estimated fair value (as a range, via
``quantproof.report.analysis.analyse``), a separate labelled real-world
simulation for loss odds, issuer credit spread in discounting, and the
stated assumptions in :data:`ASSUMPTIONS` for every note.  Those
assumptions are *not* market data as of each pricing date, and they are
never adjusted to match the bank's estimated value; the bank's value is
a comparison point only.

References: project brief section 6; ``quantproof.report.analysis`` for
the pricing pipeline and ``quantproof.validation.battery`` for the
proof battery that gates pricing here.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from quantproof.agent import client as meta_client
from quantproof.agent.costs import Usage
from quantproof.agent.crosscheck import crosscheck
from quantproof.agent.extract import ExtractionError, extract_termsheet
from quantproof.app import demo as demo_module
from quantproof.ingest.html import Filing
from quantproof.payoffs.worst_of import UnsupportedTermSheet, check_supported
from quantproof.report import analysis
from quantproof.validation.battery import run_battery

AUDIT_DIR = Path("data/market_audit")
FIRST_MODEL = "muse-spark-1.3"
SECOND_MODEL = "muse-spark-1.2"
RECHECK_MODEL = "muse-spark-1.3"
SEED = 0
GRID_PATHS = 4_000
BASE_PATHS = 20_000
BATTERY_PATHS = 4_000

# Stated inputs used for every note.  These are NOT market data: they are
# fixed assumptions, stated up front, applied unchanged to every note.
ASSUMPTIONS: dict[str, Any] = {
    "risk_free_rate": 0.037,
    "credit_spread": 0.006,
    "vol": 0.25,
    "dividend": 0.01,
    "correlation": 0.6,
    "erp": 0.05,
    "note": (
        "These are stated assumptions, not market data as of each pricing "
        "date: risk_free_rate 0.037, credit_spread 0.006, vol 0.25 per "
        "underlying, dividend 0.01 per underlying, pairwise correlation "
        "0.6 (0 for one underlying), erp 0.05."
    ),
}


class ReplayError(RuntimeError):
    """A replayed chat call does not match the recorded calls."""


class Recorder:
    """Wrap a chat callable and record every call it makes.

    Each call is recorded as a JSON-safe dict with the keys ``purpose``,
    ``model``, ``finish_reason``, ``text``, ``input_tokens``,
    ``output_tokens``, ``cost_usd`` and ``seconds``, taken from the
    ``ChatResult`` returned by the wrapped callable (tokens from its
    ``usage``).  The result is returned unchanged, so recording never
    alters the pipeline's behaviour.
    """

    def __init__(self, chat: Callable) -> None:
        self._chat = chat
        self.calls: list[dict[str, Any]] = []

    def __call__(self, messages: list[dict], **kw: Any) -> Any:
        """Call the wrapped chat callable, record the call, return its result."""
        result = self._chat(messages, **kw)
        usage = result.usage
        self.calls.append(
            {
                "purpose": kw.get("purpose"),
                "model": kw.get("model", getattr(result, "model", None)),
                "finish_reason": result.finish_reason,
                "text": result.text,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cost_usd": result.cost_usd,
                "seconds": result.seconds,
            }
        )
        return result


class Replay:
    """A chat callable that replays recorded calls, in order, at zero cost.

    Built from a list of recorded call dicts (as produced by
    :class:`Recorder`).  Each call pops the next recording; a
    :class:`ReplayError` is raised when no recording is left, or when the
    requested ``model`` differs from the recording, or when ``purpose``
    differs (compared only when both the request and the recording set
    it).  The returned ``ChatResult`` carries the recorded text and token
    counts but ``cost_usd=0.0`` and ``seconds=0.0``: replay spends nothing.
    """

    def __init__(self, calls: list[dict[str, Any]]) -> None:
        self._calls: list[dict[str, Any]] = list(calls)

    @property
    def remaining(self) -> int:
        """Number of recorded calls not yet replayed."""
        return len(self._calls)

    def __call__(
        self, messages: list[dict], *, model: str, purpose: str | None = None, **kw: Any
    ) -> meta_client.ChatResult:
        """Return the next recorded call as a ``ChatResult`` (cost 0)."""
        if not self._calls:
            raise ReplayError("replay ran out of recorded calls")
        recorded = self._calls.pop(0)
        recorded_model = recorded.get("model")
        if recorded_model is not None and model is not None and recorded_model != model:
            raise ReplayError(
                f"replay model mismatch: recorded {recorded_model!r}, requested {model!r}"
            )
        recorded_purpose = recorded.get("purpose")
        if purpose and recorded_purpose and recorded_purpose != purpose:
            raise ReplayError(
                f"replay purpose mismatch: recorded {recorded_purpose!r}, "
                f"requested {purpose!r}"
            )
        return meta_client.ChatResult(
            text=recorded.get("text", ""),
            model=model,
            finish_reason=recorded.get("finish_reason"),
            usage=Usage(
                input_tokens=int(recorded.get("input_tokens", 0) or 0),
                output_tokens=int(recorded.get("output_tokens", 0) or 0),
            ),
            cost_usd=0.0,
            seconds=0.0,
            attempts=1,
        )


def is_preliminary_text(text: str) -> bool:
    """True when the text is a preliminary pricing supplement.

    Checks the lower-cased text for "subject to completion" or
    "preliminary pricing supplement" (AGENTS.md rule 5: preliminary
    terms are labelled, and the audit prices final terms only).
    """
    lowered = text.lower()
    return (
        "subject to completion" in lowered
        or "preliminary pricing supplement" in lowered
    )


_ESTIMATED_VALUE_RE = re.compile(
    r"estimated value.{0,300}?\$(?:\d{1,3}(?:,\d{3})*|\d+)(?:\.\d{2})?",
    re.IGNORECASE,
)


def has_estimated_value(text: str) -> bool:
    """True when the text discloses an estimated value with a dollar amount.

    Regex on whitespace-collapsed, case-insensitive text: "estimated
    value" followed within 300 characters by a dollar amount such as
    ``$954.00`` or ``$9.62``.
    """
    collapsed = " ".join(text.split())
    return _ESTIMATED_VALUE_RE.search(collapsed) is not None


def _structure_label(ts: Any) -> str:
    """Short neutral label describing the term sheet's structure.

    Parts appear only when present, in this order: dependency and
    underlying count, autocall, contingent coupon (with "(memory)" when
    coupons have memory), upside leverage / cap, and the downside kind
    with its level as a percent and its observation timing.  Example:
    "worst-of 2, autocall, contingent coupon (memory), barrier 70% at
    maturity".
    """
    n = ts.n_underlyings
    parts: list[str] = [f"worst-of {n}" if n > 1 else "single underlying"]
    if ts.autocall is not None:
        parts.append("autocall")
    if ts.coupon is not None:
        coupon_part = "contingent coupon"
        if ts.coupon.memory.value:
            coupon_part += " (memory)"
        parts.append(coupon_part)
    if ts.upside is not None:
        upside_parts: list[str] = []
        if ts.upside.leverage is not None:
            upside_parts.append(f"upside leverage {ts.upside.leverage.value:g}x")
        if ts.upside.cap is not None:
            upside_parts.append(f"cap {ts.upside.cap.value * 100:g}%")
        if upside_parts:
            parts.append(", ".join(upside_parts))
    if ts.downside is not None:
        kind = getattr(ts.downside.kind.value, "value", ts.downside.kind.value)
        level_pct = ts.downside.level.value * 100
        observation = ts.downside.observation.value
        when = {
            "final_only": "at maturity",
            "each_review": "each review",
            "daily": "daily",
        }.get(observation, observation)
        parts.append(f"{kind} {level_pct:g}% {when}")
    return ", ".join(parts)


_PROTECTION_PHRASES = (
    "under no circumstances will the payment at maturity be less than",
    "principal protected",
    "full principal protection",
)


def template_gaps(ts: Any, filing_text: str) -> str | None:
    """Features the worst-of template cannot represent, found in the filing.

    The template has no principal-protection payoff (a floor at par) and
    simulates total-return underlyings (drift r - q).  So a note is not
    priced when the filing text guarantees principal (the extractor can
    only map that to a barrier at 100%, which loses 1:1 below par), or when
    an underlying is an excess-return index (its risk-neutral drift is
    -fee, not r - q, because the futures carry no financing).  Returns the
    reason with the filing's own wording, or None.
    """
    collapsed = " ".join(filing_text.split())
    lowered = collapsed.lower()
    for phrase in _PROTECTION_PHRASES:
        at = lowered.find(phrase)
        if at >= 0:
            quote = collapsed[at : at + 160]
            return f"principal protection is not modelled by the template: \"{quote}\""
    for underlying in ts.underlyings:
        name = str(underlying.name.value)
        if "excess return" in name.lower():
            return (
                "excess-return index underlying is not modelled by the template "
                f"(no financing in its drift): {name}"
            )
    return None


# A note whose base value is more than this fraction above its issue price is
# implausible for a bank-issued note (issue price includes the bank's costs).
PLAUSIBILITY_MAX_PREMIUM = 0.02


def plausibility_issues(
    fair_value: dict, issue_price: float | None, estimated_value: float | None
) -> str | None:
    """Reasons a priced note needs human review before it counts as priced.

    Two tests: (1) the base value exceeds the issue price by more than
    PLAUSIBILITY_MAX_PREMIUM (2%); a bank's issue price already includes its
    fees and hedging costs, so a value well above it usually means a misread
    term (e.g. principal protection read as a 100% barrier); (2) the bank's
    estimated value lies above our whole fair-value range, so no assumption
    in our grid reproduces it.  This only flags rows; it never changes the
    assumptions (the bank's figure is a comparison point, not a target).
    Returns the joined reasons, or None.
    """
    reasons = []
    base = float(fair_value["base"]["price"])
    high = float(fair_value["high"])
    if issue_price and base > issue_price * (1.0 + PLAUSIBILITY_MAX_PREMIUM):
        premium = (base / issue_price - 1.0) * 100.0
        reasons.append(
            f"base value {base:.2f} exceeds the issue price {issue_price:.2f} "
            f"by {premium:.1f}% (more than {PLAUSIBILITY_MAX_PREMIUM:.0%})"
        )
    if estimated_value is not None and estimated_value > high:
        reasons.append(
            f"bank's estimated value {estimated_value:.2f} is above our whole "
            f"range (high {high:.2f})"
        )
    return "; ".join(reasons) if reasons else None


def audit_note(filing: Filing, *, chat: Callable, meta: dict) -> dict:
    """Run the per-note audit pipeline on one filing; never raises for
    note-level problems.

    Formula: no new pricing maths here.  Fair value is the risk-neutral
    Monte Carlo range from ``analysis.analyse`` (grid ``GRID_PATHS``,
    base ``BASE_PATHS``, seed ``SEED``) after the proof battery in
    ``run_battery`` passes; loss odds are the separate real-world
    simulation inside the same ``analyse`` call.

    Intuition: a note is only priced when its terms were extracted with
    verifiable quotes, it discloses a bank estimated value to compare
    against, the templates support its payoff, and the proof battery's
    maths checks pass; every other outcome is recorded with its reason
    instead of raising, so one bad filing never stops the audit.

    Reference: brief section 6 and AGENTS.md rules 5, 5a and 8
    (risk-neutral value, verified quotes, bank estimate shown alongside,
    no investment advice).
    """
    started = time.perf_counter()
    result: dict[str, Any] = {
        "meta": dict(meta),
        "status": None,
        "reason": None,
        "termsheet": None,
        "fields": [],
        "quotes": {"checked": 0, "verified": 0},
        "crosscheck": None,
        "battery": None,
        "fair_value": None,
        "loss_odds": None,
        "estimated_value": None,
        "issue_price": None,
        "notional": None,
        "cusip": None,
        "issuer": None,
        "underlyings": [],
        "is_preliminary": is_preliminary_text(filing.text),
        "pricing_date": None,
        "structure": None,
        "seconds": 0.0,
    }

    def finish(status: str, reason: str | None) -> dict:
        result["status"] = status
        result["reason"] = reason
        result["seconds"] = time.perf_counter() - started
        return result

    # Step a: extract terms (Muse Spark 1.3 via FIRST_MODEL) and verify quotes.
    try:
        extraction = extract_termsheet(filing, chat=chat, model=FIRST_MODEL)
    except (ExtractionError, ValueError, KeyError, TypeError) as exc:
        return finish("extraction_failed", str(exc)[:500])

    ts = extraction.termsheet
    checks = extraction.checks

    result["termsheet"] = analysis.termsheet_summary(ts, checks)
    verified_by_path: dict[str, bool] = {}
    for check in checks or []:
        path = getattr(check, "path", None)
        if path is not None:
            verified_by_path[str(path)] = bool(getattr(check, "verified", False))
    fields: list[dict[str, Any]] = []
    for path, cited in ts.cited_fields():
        citation = cited.citation
        if path in verified_by_path:
            verified = verified_by_path[path]
        elif citation is not None:
            verified = citation.verified is True
        else:
            verified = False
        fields.append(
            {
                "path": path,
                "value": analysis._json_value(cited.value),
                "quote": citation.quote if citation is not None else "",
                "verified": bool(verified),
            }
        )
    result["fields"] = fields
    result["quotes"] = {
        "checked": len(checks),
        "verified": sum(1 for check in checks if check.verified),
    }
    result["estimated_value"] = (
        ts.estimated_value.value if ts.estimated_value is not None else None
    )
    result["issue_price"] = ts.issue_price.value
    result["notional"] = ts.notional.value
    result["cusip"] = ts.cusip.value if ts.cusip is not None else None
    result["issuer"] = ts.issuer.value
    result["underlyings"] = [u.name.value for u in ts.underlyings]
    result["is_preliminary"] = bool(ts.is_preliminary.value)
    result["pricing_date"] = ts.pricing_date.value.isoformat()
    result["structure"] = _structure_label(ts)

    if ts.estimated_value is None:
        # Stop before the cross-check to save cost: no bank estimate to
        # compare against means this note is outside the audit's scope.
        return finish("no_estimated_value", "no estimated value disclosed")

    # Step b: cross-check with a different Spark version (SECOND_MODEL).
    try:
        cc = crosscheck(
            filing,
            extraction,
            chat=chat,
            second_model=SECOND_MODEL,
            recheck_model=RECHECK_MODEL,
        )
        result["crosscheck"] = {
            "second_model": cc.second_model,
            "agreed": bool(cc.agreed),
            "error": cc.error,
            "diffs": [
                {
                    "path": diff.path,
                    "first": analysis._json_value(diff.first),
                    "second": analysis._json_value(diff.second),
                    "status": diff.status,
                }
                for diff in cc.diffs
            ],
        }
    except ReplayError:
        raise
    except Exception as exc:  # a failed cross-check is recorded, not raised
        result["crosscheck"] = {
            "second_model": SECOND_MODEL,
            "agreed": False,
            "error": str(exc)[:500],
            "diffs": [],
        }

    # Step c: template support gate.
    try:
        check_supported(ts)
    except UnsupportedTermSheet as exc:
        return finish("unsupported", str(exc))
    gap = template_gaps(ts, filing.text)
    if gap is not None:
        return finish("unsupported", gap)

    # Step d: market inputs from the stated assumptions, proof battery gate.
    n = ts.n_underlyings
    rho = float(ASSUMPTIONS["correlation"]) if n > 1 else 0.0
    market = demo_module.build_market(
        ts,
        rate=float(ASSUMPTIONS["risk_free_rate"]),
        spread=float(ASSUMPTIONS["credit_spread"]),
        vols=(float(ASSUMPTIONS["vol"]),) * n,
        dividends=(float(ASSUMPTIONS["dividend"]),) * n,
        rho=rho,
    )
    # Same construction as demo.build_market, but the provenance label must
    # say these are the audit's stated assumptions, not market data.
    market = replace(
        market, sources={"all": "stated assumptions, not market data"}
    )

    try:
        battery_results = run_battery(
            ts, market, n_paths=BATTERY_PATHS, seed=SEED, filing_text=filing.text
        )
    except (ValueError, ZeroDivisionError, IndexError) as exc:
        return finish("pricing_error", f"proof battery raised: {str(exc)[:400]}")
    battery_dicts = [analysis.check_to_dict(check) for check in battery_results]
    result["battery"] = battery_dicts
    if not demo_module.maths_passed(battery_dicts):
        failing = [
            str(check.get("name"))
            for check in battery_dicts
            if check.get("name") != "quotes" and not check.get("passed")
        ]
        reason = "proof battery failed: " + (
            ", ".join(failing) if failing else "no maths checks passed"
        )
        return finish("battery_failed", reason)

    # Step e: price (risk-neutral range + real-world loss odds).
    try:
        priced = analysis.analyse(
            ts,
            checks,
            market,
            paths=GRID_PATHS,
            base_paths=BASE_PATHS,
            seed=SEED,
            erp=ASSUMPTIONS["erp"],
            battery=False,
            filing_text=filing.text,
        )
    except (ValueError, ZeroDivisionError, IndexError) as exc:
        return finish("pricing_error", f"pricing raised: {str(exc)[:400]}")
    result["fair_value"] = priced["fair_value"]
    result["loss_odds"] = priced["loss_odds"]
    issue = plausibility_issues(
        result["fair_value"], result.get("issue_price"), result.get("estimated_value")
    )
    if issue is not None:
        return finish("needs_review", "plausibility gate: " + issue)
    return finish("priced", None)


def select_candidates(
    refs_by_issuer: dict[str, list],
    *,
    majors: tuple[str, ...],
    per_major: int,
    per_other: int,
    max_notes: int,
) -> list[tuple[str, Any]]:
    """Pick audit candidates across issuer groups.  Pure function.

    Accessions are deduplicated across issuers (the first selected
    occurrence wins).  Up to ``per_major`` refs are taken from each major
    issuer group, in the order the majors are given, then up to
    ``per_other`` from each other group in the dict's order, stopping as
    soon as ``max_notes`` candidates are chosen.
    """
    selected: list[tuple[str, Any]] = []
    seen_accessions: set[str] = set()

    def take(group: str, limit: int) -> None:
        taken = 0
        for ref in refs_by_issuer.get(group, []):
            if len(selected) >= max_notes or taken >= limit:
                return
            if ref.accession in seen_accessions:
                continue
            seen_accessions.add(ref.accession)
            selected.append((group, ref))
            taken += 1

    for group in majors:
        if len(selected) >= max_notes:
            break
        take(group, per_major)
    for group in refs_by_issuer:
        if len(selected) >= max_notes:
            break
        if group in majors:
            continue
        take(group, per_other)
    return selected
