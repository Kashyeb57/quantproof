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
