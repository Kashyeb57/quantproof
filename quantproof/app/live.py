"""Live-mode pipeline for the Streamlit app (Stage B).

Live mode downloads a filing (EDGAR link or upload), extracts its terms
with Muse Spark 1.3, verifies every quote against the filing text, and
cross-checks with a second extraction.  The result is shaped exactly
like a demo note so the confirm / proof / verdict flow is shared.

Rules enforced here, in code:
- Muse Spark 1.3 only (``LIVE_MODEL``); no other model is ever called,
  and Muse Wonder payoff synthesis is never called from the app.
- A field without a verified quote is carried in ``unverified`` and
  shown to the user; it is never priced silently (the UI requires the
  user to confirm the flagged term sheet before pricing).
- Unsupported features are reported with their filing quotes; the note
  is not priced by the templates.

The chat and EDGAR clients are injected, so tests run offline with
fakes and spend nothing.  Keys are never read here: the injected
clients read them from the environment (see ``quantproof.agent.client``
and ``quantproof.ingest.edgar``).
"""

from __future__ import annotations

from dataclasses import dataclass

from quantproof.agent.crosscheck import CrossCheck, crosscheck
from quantproof.agent.extract import Extraction, extract_termsheet
from quantproof.core.termsheet import TermSheet
from quantproof.ingest.html import Filing, read_filing

LIVE_MODEL = "muse-spark-1.3"

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


def daily_spend(log_path=None, *, day=None) -> float:
    """USD spent today (UTC) according to the Meta API cost log.

    Sums ``cost_usd`` over the JSON-lines log written by
    ``quantproof.agent.client.chat`` for records whose timestamp falls
    on ``day`` (default: today, UTC).  A missing log means zero spend.
    """
    import datetime
    import json as _json
    from pathlib import Path

    from quantproof.agent.client import DEFAULT_LOG

    path = Path(log_path) if log_path is not None else DEFAULT_LOG
    today = day or datetime.datetime.now(datetime.timezone.utc).date()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return 0.0
    total = 0.0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = _json.loads(line)
            stamp = datetime.datetime.fromisoformat(str(record["time"]))
        except (ValueError, KeyError, TypeError):
            continue
        if stamp.date() == today:
            total += float(record.get("cost_usd") or 0.0)
    return total


EXTRACTION_MAX_TOKENS = 16_000  # extract_termsheet default max_tokens
RECHECK_MAX_TOKENS = 4_000  # crosscheck recheck default max_tokens
RECHECK_ALLOWANCE = 3  # worst-case re-check calls allowed in the estimate


def estimate_live_cost(text_len: int, *, model: str = LIVE_MODEL) -> float:
    """Worst-case USD estimate for one whole live run, before it starts.

    Formula: input tokens per call = ceil(text_len / 4) (about four
    characters per token); each of the two extractions (first plus
    cross-check second) is charged that input plus the full
    ``EXTRACTION_MAX_TOKENS`` output; up to ``RECHECK_ALLOWANCE``
    re-check calls are charged the same input plus the full
    ``RECHECK_MAX_TOKENS`` output.  Output is charged at the maximum,
    never at an expected value, so the estimate errs high.

    Intuition: the daily cap is only useful if a run that would breach
    it is refused before it starts, at the most it could possibly cost.

    Reference: ``quantproof.agent.costs`` prices (Muse Spark: input and
    output dollars per million tokens); extract/recheck token limits in
    ``quantproof.agent.extract`` and ``quantproof.agent.crosscheck``.
    """
    import math

    from quantproof.agent.costs import Usage, estimate_cost

    if model != LIVE_MODEL:
        raise ValueError(f"the public app uses {LIVE_MODEL} only, not {model!r}")
    input_tokens = math.ceil(max(int(text_len), 0) / 4)
    one_extraction = estimate_cost(
        model, Usage(input_tokens=input_tokens, output_tokens=EXTRACTION_MAX_TOKENS)
    )
    one_recheck = estimate_cost(
        model, Usage(input_tokens=input_tokens, output_tokens=RECHECK_MAX_TOKENS)
    )
    return 2.0 * one_extraction + RECHECK_ALLOWANCE * one_recheck


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
                f"of ${spent_today:,.2f} plus this filing's worst-case "
                f"estimate of ${estimate:,.2f} would exceed the daily "
                f"cap of ${cap:,.2f}. Demo mode remains available."
            )
        if total_cap is not None and spent_total + estimate > total_cap:
            raise LiveRefused(
                "Live extraction refused before it starts: lifetime spend "
                f"of ${spent_total:,.2f} plus this filing's worst-case "
                f"estimate of ${estimate:,.2f} would exceed the lifetime "
                f"cap of ${total_cap:,.2f}. Demo mode remains available."
            )
        note = extract_fn()
        self.extractions_used += 1
        self.cache[key] = note
        return note, False


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
    cost_usd: float = 0.0


def filing_from_url(url: str, *, edgar_client) -> Filing:
    """Download ``url`` through the injected EDGAR client and parse it."""
    return read_filing(edgar_client.get(url))


def filing_from_upload(data: bytes) -> Filing:
    """Parse an uploaded filing's bytes (no network)."""
    return read_filing(data)


def extract_and_crosscheck(
    filing: Filing, source: str, *, chat, model: str = LIVE_MODEL
) -> LiveNote:
    """Extract with Muse Spark 1.3, verify quotes, cross-check. Live only.

    Both the first and the second extraction use Muse Spark 1.3 (the
    public app calls no other model; that makes the second extraction a
    weaker independence check than a different model service, which the
    UI states).  Quote verification happens inside
    ``extract_termsheet``; unverified fields are listed, never hidden.
    """
    if model != LIVE_MODEL:
        raise ValueError(
            f"the public app uses {LIVE_MODEL} only, not {model!r}"
        )
    extraction: Extraction = extract_termsheet(filing, chat=chat, model=LIVE_MODEL)
    result: CrossCheck = crosscheck(
        filing,
        extraction,
        chat=chat,
        second_model=LIVE_MODEL,
        recheck_model=LIVE_MODEL,
    )
    cost = 0.0
    if extraction.result is not None:
        cost += float(extraction.result.cost_usd)
    if result.second is not None and result.second.result is not None:
        cost += float(result.second.result.cost_usd)
    return LiveNote(
        source=source,
        termsheet=extraction.termsheet,
        filing_text=filing.text,
        checks=extraction.checks,
        unverified=list(extraction.unverified),
        diffs=list(result.needs_user),
        agreed=result.agreed,
        crosscheck_error=result.error,
        model=LIVE_MODEL,
        cost_usd=cost,
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
