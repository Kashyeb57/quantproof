"""Live-mode pipeline for the Streamlit app (Stage B).

Live mode downloads a filing (EDGAR link or upload), extracts its terms
with Muse Spark 1.3, verifies every quote against the filing text, and
cross-checks with a second extraction by Muse Spark Open on Baseten.
The result is shaped exactly like a demo note so the confirm / proof /
verdict flow is shared.

Rules enforced here, in code:
- The first extraction always uses Muse Spark 1.3 (``LIVE_MODEL``); the
  second extraction always uses Muse Spark Open on Baseten
  (``SECOND_MODEL``).  Muse Wonder payoff synthesis is never called
  from the app.
- The Baseten key comes from the environment or Streamlit secrets and
  is only sent as the Authorization header, never printed or logged.
- Without a Baseten key, or when the second extraction fails or comes
  back incomplete, the cross-check is reported unavailable: the note
  shows that, never agreement.
- Every disagreement the first extraction did not win is shown for
  user confirmation; at most ``RECHECK_ALLOWANCE`` of them are
  re-checked (on Meta credits).
- Spend guard: the Meta and Baseten cost logs are separate files, but
  the daily, lifetime and per-session limits apply to COMBINED spend;
  the pre-flight estimate assumes every planned call retries to the
  limit at max output.
- A field without a verified quote is carried in ``unverified`` and
  shown to the user; it is never priced silently (the UI requires the
  user to confirm the flagged term sheet before pricing).
- Unsupported features are reported with their filing quotes; the note
  is not priced by the templates.

The chat and EDGAR clients are injected, so tests run offline with
fakes and spend nothing.  Keys are never read here: the injected
clients read them from the environment (see ``quantproof.agent.client``
and ``quantproof.agent.baseten``).
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock

from quantproof.agent import baseten as baseten_client
from quantproof.agent.client import MAX_ATTEMPTS
from quantproof.agent.crosscheck import CrossCheck, crosscheck
from quantproof.agent.extract import Extraction, extract_termsheet
from quantproof.core.termsheet import TermSheet
from quantproof.ingest.html import Filing, read_filing

LIVE_MODEL = "muse-spark-1.3"
LIVE_SPEND_LOCK = Lock()
SECOND_MODEL = baseten_client.OPEN_MODEL  # second extraction (Baseten)

LIVE_RUN_NOTICE = "A live run takes about 1-2 minutes."
PROGRESS_STEPS = (
    "Downloading the filing",
    "Checking it is a 424B2 pricing supplement",
    "Extracting terms with Muse Spark 1.3",
    "Verifying quotes against the filing",
    "Cross-checking with a second extraction",
    "Done",
)

MAX_FILING_CHARS_ENV = "QP_MAX_FILING_CHARS"
DEFAULT_MAX_FILING_CHARS = 400_000


def max_filing_chars(env: dict | None = None) -> int:
    """Cleaned-text size limit for a live filing: env or 400,000 chars."""
    import os

    raw = (os.environ if env is None else env).get(MAX_FILING_CHARS_ENV, "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_MAX_FILING_CHARS
    return value if value > 0 else DEFAULT_MAX_FILING_CHARS


def is_pricing_supplement(text: str) -> bool:
    """Whether cleaned filing text is a 424B2 pricing supplement.

    Checks the document header (the first 2,000 characters of cleaned
    text): SEC filings open with the form type, and every saved 424B2
    fixture opens ``424B2 ... PRICING SUPPLEMENT``.  Anything else
    (a 10-K, a prospectus without the supplement header, ...) is not a
    document this app prices.
    """
    head = text[:2000].upper()
    return "424B2" in head and "PRICING SUPPLEMENT" in head


def check_filing_admissible(filing, *, max_chars: int) -> None:
    """Refuse oversize or non-424B2 filings before any extraction spend.

    Raises ``LiveRefused`` with a user-facing message.  Downloading and
    cleaning a filing costs no credits, so a refusal here spends
    nothing.
    """
    if len(filing.text) > max_chars:
        raise LiveRefused(
            f"Filing refused: its cleaned text is {len(filing.text):,} "
            f"characters, over the {max_chars:,}-character limit for "
            "live mode. Demo mode remains available."
        )
    if not is_pricing_supplement(filing.text):
        raise LiveRefused(
            "Filing refused: this does not look like a 424B2 pricing "
            "supplement (the document header does not state 424B2 / "
            "PRICING SUPPLEMENT). Only 424B2 pricing supplements are "
            "priced in live mode."
        )

TOTAL_CAP_ENV = "QP_TOTAL_CAP_USD"
DEFAULT_TOTAL_CAP_USD = 20.0


def total_cap_usd(env: dict | None = None) -> float:
    """Lifetime spend cap in USD: env ``QP_TOTAL_CAP_USD``, default $20."""
    import os

    raw = (os.environ if env is None else env).get(TOTAL_CAP_ENV, "")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_TOTAL_CAP_USD
    return value if value > 0 else DEFAULT_TOTAL_CAP_USD


def total_spend(log_path=None) -> float:
    """USD spent over the whole cost log (all dates), via ``client.spend``."""
    from pathlib import Path

    from quantproof.agent.client import DEFAULT_LOG, spend

    return spend(Path(log_path) if log_path is not None else DEFAULT_LOG)


SESSION_EXTRACTION_LIMIT = 3
DAILY_CAP_ENV = "QP_DAILY_CAP_USD"
DEFAULT_DAILY_CAP_USD = 5.0


def resolve_secrets(names, *, env, secrets) -> dict:
    """Resolve API keys from environment variables or Streamlit secrets.

    Environment variables win; ``secrets`` (a mapping, e.g. ``st.secrets``)
    is the fallback.  Blank values count as unset.  The returned mapping
    holds only the names that resolved; values are never logged or
    displayed by any caller.
    """
    resolved: dict = {}
    for name in names:
        value = env.get(name) if env is not None else None
        if not value and secrets is not None:
            try:
                value = secrets.get(name)
            except Exception:
                value = None
        if value:
            resolved[str(name)] = str(value)
    return resolved


class LiveRefused(RuntimeError):
    """Live mode refused by the cost guard (session limit or daily cap)."""


def daily_cap_usd(env: dict | None = None) -> float:
    """Daily spend cap in USD: env ``QP_DAILY_CAP_USD``, default $5."""
    import os

    raw = (os.environ if env is None else env).get(DAILY_CAP_ENV, "")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_DAILY_CAP_USD
    return value if value > 0 else DEFAULT_DAILY_CAP_USD


def _spend_on_day(log_path, day):
    """USD in the JSON-lines cost log at ``log_path`` for ``day`` (UTC).

    Sums ``cost_usd`` over records whose timestamp falls on ``day``.
    A missing log means zero spend.  Malformed lines are skipped.
    """
    import datetime
    import json as _json
    from pathlib import Path

    path = Path(log_path)
    try:
        file_lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return 0.0
    total = 0.0
    for line in file_lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = _json.loads(line)
            stamp = datetime.datetime.fromisoformat(str(record["time"]))
        except (ValueError, KeyError, TypeError):
            continue
        if stamp.date() == day:
            total += float(record.get("cost_usd") or 0.0)
    return total


def daily_spend(log_path=None, *, day=None) -> float:
    """USD spent today (UTC) according to the Meta API cost log.

    Sums ``cost_usd`` over the JSON-lines log written by
    ``quantproof.agent.client.chat`` for records whose timestamp falls
    on ``day`` (default: today, UTC).  A missing log means zero spend.
    """
    import datetime
    from pathlib import Path

    from quantproof.agent.client import DEFAULT_LOG

    today = day or datetime.datetime.now(datetime.timezone.utc).date()
    path = Path(log_path) if log_path is not None else DEFAULT_LOG
    return _spend_on_day(path, today)


def baseten_daily_spend(log_path=None, *, day=None) -> float:
    """USD spent today (UTC) according to the Baseten cost log.

    Sums ``cost_usd`` over the JSON-lines log written by
    ``quantproof.agent.baseten.chat`` for records whose timestamp falls
    on ``day`` (default: today, UTC).  A missing log means zero spend.
    Meta and Baseten credits bill apart, but the live guard applies
    its caps to their sum (see ``LiveGuard``).
    """
    import datetime
    from pathlib import Path

    today = day or datetime.datetime.now(datetime.timezone.utc).date()
    path = (
        Path(log_path) if log_path is not None else baseten_client.DEFAULT_LOG
    )
    return _spend_on_day(path, today)


def baseten_total_spend(log_path=None) -> float:
    """USD spent over the whole Baseten cost log, via ``client.spend``."""
    from pathlib import Path

    from quantproof.agent.client import spend

    return spend(
        Path(log_path) if log_path is not None else baseten_client.DEFAULT_LOG
    )


EXTRACTION_MAX_TOKENS = 16_000  # extract_termsheet default max_tokens
RECHECK_MAX_TOKENS = 4_000  # crosscheck recheck default max_tokens
RECHECK_ALLOWANCE = 3  # worst-case re-check calls allowed in the estimate
ATTEMPT_ALLOWANCE = MAX_ATTEMPTS  # every planned call may bill up to 3 attempts


def estimate_live_cost_by_provider(
    text_len: int, *, with_baseten: bool = True
) -> dict:
    """Estimated USD per provider for one whole live run, before it starts.

    Returns ``{'meta': ..., 'baseten': ...}``.  Input tokens per call =
    ceil(text_len / 4) (about four characters per token); the Meta first
    extraction and the Baseten second extraction are each charged that
    input plus the full ``EXTRACTION_MAX_TOKENS`` output, and up to
    ``RECHECK_ALLOWANCE`` re-check calls (Meta) are charged the same input
    plus the full ``RECHECK_MAX_TOKENS`` output.  Output is charged at the
    maximum, never at an expected value, so the estimate errs high.

    Everything is multiplied by ``ATTEMPT_ALLOWANCE`` to allow for retries.
    Input token counts and provider charges remain estimates, not bounds.
    Rechecks are bounded because the live
    note passes ``max_rechecks=RECHECK_ALLOWANCE`` to the cross-check.

    The estimate is used to refuse runs that would exceed the local limits.

    Reference: ``quantproof.agent.costs`` prices (Muse Spark: input and
    output dollars per million tokens), ``quantproof.agent.baseten`` for
    the separately billed Open rate; extract/recheck token limits in
    ``quantproof.agent.extract`` and ``quantproof.agent.crosscheck``.
    """
    import math

    from quantproof.agent.costs import Usage, estimate_cost

    input_tokens = math.ceil(max(int(text_len), 0) / 4)
    meta_extraction = estimate_cost(
        LIVE_MODEL, Usage(input_tokens=input_tokens, output_tokens=EXTRACTION_MAX_TOKENS)
    )
    baseten_extraction = (
        baseten_client.estimate_cost(
            Usage(input_tokens=input_tokens, output_tokens=EXTRACTION_MAX_TOKENS)
        )
        if with_baseten
        else 0.0
    )
    one_recheck = estimate_cost(
        LIVE_MODEL, Usage(input_tokens=input_tokens, output_tokens=RECHECK_MAX_TOKENS)
    )
    meta = ATTEMPT_ALLOWANCE * (
        meta_extraction + RECHECK_ALLOWANCE * one_recheck
    )
    baseten = ATTEMPT_ALLOWANCE * baseten_extraction
    return {"meta": meta, "baseten": baseten}


def estimate_live_cost(
    text_len: int, *, model: str = LIVE_MODEL, with_baseten: bool = True
) -> float:
    """Combined estimated USD across both providers for one live run.

    This is the number the spend guard compares against the caps: Meta
    first extraction plus Baseten second extraction plus bounded rechecks,
    every call assumed to retry to the limit at max output (see
    ``estimate_live_cost_by_provider``).
    """
    if model != LIVE_MODEL:
        raise ValueError(f"the public app uses {LIVE_MODEL} only, not {model!r}")
    by_provider = estimate_live_cost_by_provider(
        text_len, with_baseten=with_baseten
    )
    return by_provider["meta"] + by_provider["baseten"]


def budget_status(
    *,
    extractions_used: int,
    session_limit: int = SESSION_EXTRACTION_LIMIT,
    spent_today: float,
    cap: float,
    spent_total: float,
    total_cap: float,
) -> dict:
    """Visitor-facing budget status: remaining runs and availability.

    Live is available only with session runs left, today's spend under
    the daily cap, and lifetime spend under the lifetime cap.  All
    numbers are copied from the guard state and the cost log.
    """
    remaining = max(int(session_limit) - int(extractions_used), 0)
    reasons: list[str] = []
    if remaining <= 0:
        reasons.append("session limit reached")
    if spent_today >= cap:
        reasons.append("daily cap reached")
    if spent_total >= total_cap:
        reasons.append("lifetime cap reached")
    return {
        "remaining": remaining,
        "session_limit": int(session_limit),
        "available": not reasons,
        "reason": "; ".join(reasons),
        "spent_today": float(spent_today),
        "cap": float(cap),
        "spent_total": float(spent_total),
        "total_cap": float(total_cap),
    }


def live_available(spent_today: float, cap: float) -> bool:
    """Whether live mode may spend at all under the daily cap."""
    return spent_today < cap





@dataclass
class LiveGuard:
    """Per-session cost guard: extraction limit + served-from-cache URLs.

    ``get_or_extract`` returns the cached note for a repeated filing
    key without consuming budget or calling the extractor.  A new
    filing is refused (``LiveRefused``) once the session has used its
    extractions or today's spend has reached the daily cap; the checks
    run before the extractor, so a refused filing costs nothing.
    """

    extractions_used: int = 0
    session_limit: int = SESSION_EXTRACTION_LIMIT
    cache: dict = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.cache is None:
            self.cache = {}

    def cached_note(self, key: str):
        """The cached note for ``key``, or None (never extracts)."""
        return self.cache.get(key)

    def get_or_extract(
        self, key: str, extract_fn, *, spent_today: float, cap: float,
        estimate: float = 0.0, spent_total: float = 0.0,
        total_cap: float | None = None,
    ):
        """Return (note, from_cache) for ``key``, enforcing the guard."""
        if key in self.cache:
            return self.cache[key], True
        if self.extractions_used >= self.session_limit:
            raise LiveRefused(
                f"Live extraction limit reached: at most "
                f"{self.session_limit} live extractions per browser session. "
                "Demo mode remains available."
            )
        if not live_available(spent_today, cap):
            raise LiveRefused(
                "Live mode is off: today's spend has reached the daily "
                f"cap of ${cap:,.2f}. Demo mode remains available."
            )
        if spent_today + estimate > cap:
            raise LiveRefused(
                "Live extraction refused before it starts: today's spend "
                f"of ${spent_today:,.2f} plus this filing's estimated "
                f"estimate of ${estimate:,.2f} would exceed the daily "
                f"cap of ${cap:,.2f}. Demo mode remains available."
            )
        if total_cap is not None and spent_total + estimate > total_cap:
            raise LiveRefused(
                "Live extraction refused before it starts: lifetime spend "
                f"of ${spent_total:,.2f} plus this filing's estimated "
                f"estimate of ${estimate:,.2f} would exceed the lifetime "
                f"cap of ${total_cap:,.2f}. Demo mode remains available."
            )
        note = extract_fn()
        self.extractions_used += 1
        self.cache[key] = note
        return note, False


def guarded_live_run(guard, key, extract_fn, *, cap, estimate, total_cap):
    """Serialize live runs and read the current logs inside the shared lock."""
    with LIVE_SPEND_LOCK:
        return guard.get_or_extract(
            key, extract_fn,
            spent_today=daily_spend() + baseten_daily_spend(),
            cap=cap, estimate=estimate,
            spent_total=total_spend() + baseten_total_spend(),
            total_cap=total_cap,
        )


@dataclass
class LiveNote:
    """A live-extracted note, shaped like a demo note for the shared flow."""

    source: str  # the EDGAR URL, or "upload"
    termsheet: TermSheet
    filing_text: str
    checks: list  # QuoteCheck list from the extraction
    unverified: list[str]
    diffs: list  # cross-check disagreements needing the user (FieldDiff)
    agreed: bool
    crosscheck_error: str | None
    model: str = LIVE_MODEL
    second_model: str = SECOND_MODEL
    cost_usd: float = 0.0  # Meta spend: first extraction (rechecks land in the Meta log)
    baseten_cost_usd: float = 0.0  # Baseten spend: second extraction


def filing_from_url(url: str, *, edgar_client) -> Filing:
    """Download ``url`` through the injected EDGAR client and parse it."""
    return read_filing(edgar_client.get(url))


def filing_from_upload(data: bytes) -> Filing:
    """Parse an uploaded filing's bytes (no network)."""
    return read_filing(data)


def baseten_configured(env=None) -> bool:
    """Whether a Baseten key is available (mapping or environment).

    Takes the resolved env/secrets mapping the app already holds (or
    ``None`` to read the process environment).  Returns only a boolean;
    the key itself never leaves the client that sends it as the
    Authorization header.
    """
    return baseten_client.api_key(env) is not None


def open_chat(*, env=None):
    """Chat callable for the Baseten second extraction (Muse Spark Open only).

    Binds the Baseten cost log and the resolved env/secrets mapping, so
    the key is only ever sent as the Authorization header, never printed
    or logged.  A missing key raises when the second extraction runs,
    which ``finish_live_note`` reports as an unavailable cross-check.
    """
    import functools

    return functools.partial(
        baseten_client.chat, log_path=baseten_client.DEFAULT_LOG, env=env
    )


def extract_only(filing: Filing, *, chat, model: str = LIVE_MODEL) -> Extraction:
    """First extraction only (quotes verified inside); Muse Spark 1.3 only."""
    if model != LIVE_MODEL:
        raise ValueError(
            f"the public app uses {LIVE_MODEL} only, not {model!r}"
        )
    return extract_termsheet(filing, chat=chat, model=LIVE_MODEL)


def finish_live_note(
    filing: Filing, source: str, extraction: Extraction, *, chat,
    model: str = LIVE_MODEL,
    second_chat=None,
    second_model: str = SECOND_MODEL,
) -> LiveNote:
    """Cross-check a first extraction with Muse Spark Open and build the LiveNote.

    The second extraction always runs ``second_model`` (Muse Spark Open on
    Baseten) through ``second_chat``; rechecks run the first model through
    ``chat``, at most ``RECHECK_ALLOWANCE`` of them — remaining open
    disagreements stay open for the user to confirm.

    When there is no second chat (Baseten key missing or second extractor
    not wired), or the second extraction fails or comes back incomplete,
    the note carries ``crosscheck_error`` and ``agreed=False`` with no
    diffs: an unavailable cross-check is shown as unavailable, never as
    agreement.  Meta spend (first extraction; rechecks land in the Meta
    log) and Baseten spend (second extraction) are recorded apart.
    """
    if model != LIVE_MODEL:
        raise ValueError(
            f"the public app uses {LIVE_MODEL} only, not {model!r}"
        )
    if second_model != SECOND_MODEL:
        raise ValueError(
            f"the public app cross-checks with {SECOND_MODEL} only, not {second_model!r}"
        )
    meta_cost = 0.0
    if extraction.result is not None:
        meta_cost = float(extraction.result.cost_usd)
    if second_chat is None:
        return LiveNote(
            source=source,
            termsheet=extraction.termsheet,
            filing_text=filing.text,
            checks=extraction.checks,
            unverified=list(extraction.unverified),
            diffs=[],
            agreed=False,
            crosscheck_error=(
                "Cross-check unavailable: no second extraction ran "
                "(Baseten key missing or second extractor not wired)."
            ),
            model=LIVE_MODEL,
            second_model=second_model,
            cost_usd=meta_cost,
            baseten_cost_usd=0.0,
        )
    try:
        result: CrossCheck = crosscheck(
            filing,
            extraction,
            chat=chat,
            second_model=second_model,
            second_chat=second_chat,
            recheck_model=LIVE_MODEL,
            recheck_chat=chat,
            max_rechecks=RECHECK_ALLOWANCE,
        )
    except Exception as exc:  # missing key, network, model errors
        return LiveNote(
            source=source,
            termsheet=extraction.termsheet,
            filing_text=filing.text,
            checks=extraction.checks,
            unverified=list(extraction.unverified),
            diffs=[],
            agreed=False,
            crosscheck_error=f"Cross-check unavailable: {exc}",
            model=LIVE_MODEL,
            second_model=second_model,
            cost_usd=meta_cost,
            baseten_cost_usd=0.0,
        )
    baseten_cost = 0.0
    if result.second is not None and result.second.result is not None:
        baseten_cost = float(result.second.result.cost_usd)
    return LiveNote(
        source=source,
        termsheet=extraction.termsheet,
        filing_text=filing.text,
        checks=extraction.checks,
        unverified=list(extraction.unverified),
        diffs=list(result.needs_user),
        agreed=result.agreed,
        crosscheck_error=(
            f"Cross-check unavailable: {result.error}"
            if result.error is not None
            else None
        ),
        model=LIVE_MODEL,
        second_model=second_model,
        cost_usd=meta_cost,
        baseten_cost_usd=baseten_cost,
    )


def extract_and_crosscheck(
    filing: Filing,
    source: str,
    *,
    chat,
    second_chat=None,
    model: str = LIVE_MODEL,
    second_model: str = SECOND_MODEL,
) -> LiveNote:
    """Extract with Muse Spark 1.3, verify quotes, cross-check with Muse Spark Open.

    The first extraction uses Muse Spark 1.3; the second uses Muse Spark
    Open on Baseten (``second_chat``); pass no second chat and the note
    reports the cross-check as unavailable instead of agreeing with
    itself.  Quote verification happens inside ``extract_termsheet``;
    unverified fields are listed, never hidden.
    """
    extraction = extract_only(filing, chat=chat, model=model)
    return finish_live_note(
        filing,
        source,
        extraction,
        chat=chat,
        model=model,
        second_chat=second_chat,
        second_model=second_model,
    )


def unsupported_features(ts: TermSheet) -> list[tuple[str, str]]:
    """(feature, filing quote) for features no template covers."""
    return [
        (str(cited.value), cited.citation.quote if cited.citation else "")
        for cited in ts.unsupported_features
    ]


def generic_defaults(ts: TermSheet) -> dict:
    """Illustrative default inputs for a live note (not market data)."""
    n = ts.n_underlyings
    return {
        "rate": 0.037,
        "spread": 0.006,
        "vols": (0.25,) * n,
        "dividends": (0.01,) * n,
        "rho": 0.6 if n > 1 else 0.0,
    }
