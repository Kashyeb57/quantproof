"""Short overview landing page for the Streamlit app.

This page reads ONLY the saved market audit under ``data/market_audit/``
and the saved synthesis benchmark under ``data/synthesis_benchmark/``
(written offline).  It makes no API calls, reads no keys and performs
no pricing maths of its own: every number comes from
:mod:`quantproof.report.market_audit` and
:mod:`quantproof.report.synthesis_benchmark`, which only summarise the
saved per-note result files.

Language: neutral and factual (AGENTS.md rule 8).  This is a risk
analysis under stated assumptions, not investment advice.
"""

from __future__ import annotations

from pathlib import Path

from quantproof.app import demo
from quantproof.report import market_audit, synthesis_benchmark

ROOT = Path(__file__).resolve().parents[2]
AUDIT_DIR = ROOT / "data" / "market_audit"
BENCH_DIR = ROOT / "data" / "synthesis_benchmark"


def page_data(
    audit_dir: Path = AUDIT_DIR, bench_dir: Path = BENCH_DIR
) -> dict:
    """Headline numbers for the overview page (pure, saved data only).

    Returns ``{"audit": ..., "synthesis": ...}`` where ``audit`` is
    :func:`market_audit.aggregate` over the saved audit rows and
    ``synthesis`` is :func:`synthesis_benchmark.summarise` over the
    saved benchmark results and the same audit rows.  No Streamlit
    calls, no API calls.
    """
    results = market_audit.load_results(audit_dir)
    run = market_audit.load_run(audit_dir)
    rows = [market_audit.note_row(result) for result in results]
    return {
        "audit": market_audit.aggregate(rows, run),
        "synthesis": synthesis_benchmark.summarise(
            synthesis_benchmark.load_results(bench_dir), rows
        ),
    }


def _pct1(value: object) -> str:
    """Fraction as a one-decimal percent, or "n/a" when missing."""
    if value is None:
        return "n/a"
    return f"{100.0 * float(value):.1f}%"


def render(st) -> None:
    """Render the Overview page using the injected Streamlit module."""
    st.header("Overview")
    st.caption(
        demo.escape_dollars(
            "QuantProof reads a structured note's SEC filing, proves its "
            "payoff code, and reports an independently computed value "
            "range next to the bank's own estimated value. Below are the "
            "headlines of the two saved offline studies; both pages "
            "replay saved data and spend nothing. This is a risk analysis "
            "under stated assumptions, not investment advice."
        )
    )

    data = page_data()
    agg = data["audit"]
    summary = data["synthesis"]

    if not agg.get("notes_read") and not summary.get("n_notes"):
        st.info(
            demo.escape_dollars(
                "No saved audit or benchmark results were found. Run the "
                "offline market audit and synthesis benchmark first; this "
                "page only replays saved results."
            )
        )
        return

    st.subheader("Market audit")
    col1, col2, col3 = st.columns(3)
    col1.metric("Notes read", f"{int(agg.get('notes_read') or 0)}")
    col2.metric("Issuers", f"{len(agg.get('issuers') or [])}")
    col3.metric("Extracted", f"{int(agg.get('notes_extracted') or 0)}")
    col1.metric(
        "Priced",
        f"{int(agg.get('n_priced') or 0)}"
        f" (+{int(agg.get('n_needs_review') or 0)} held for review)",
    )
    col2.metric(
        "Quotes verified", _pct1(agg.get("quote_verification_rate"))
    )
    col3.metric(
        "Bank value in our range",
        f"{int(agg.get('n_bank_in_range') or 0)}"
        f" of {int(agg.get('n_priced_or_review') or 0)}",
    )

    st.subheader("Synthesis benchmark")
    coverage = summary.get("coverage") or {}
    col1, col2, col3 = st.columns(3)
    col1.metric("Attempted", f"{int(summary.get('n_notes') or 0)}")
    col2.metric(
        "Passed",
        f"{int(summary.get('n_passed') or 0)}"
        f" ({_pct1(summary.get('pass_rate'))})",
    )
    col3.metric("Repaired", f"{int(summary.get('n_repaired') or 0)}")
    st.write(
        demo.escape_dollars(
            f"Coverage: templates priced {int(coverage.get('n_template') or 0)}"
            f" of {int(coverage.get('n_extracted') or 0)} extracted notes; "
            f"with proven AI-written payoffs, "
            f"{int(coverage.get('n_ai') or 0)} of "
            f"{int(coverage.get('n_extracted') or 0)}."
        )
    )
    audit_cost = float(agg.get("total_cost_usd") or 0.0)
    synth_cost = float(summary.get("total_cost_usd") or 0.0)
    st.write(
        demo.escape_dollars(
            f"Model cost: ${audit_cost:,.2f} (market audit) + "
            f"${synth_cost:,.2f} (synthesis) = "
            f"${audit_cost + synth_cost:,.2f} combined."
        )
    )

    st.subheader("Explore")
    if st.button("Open the Market Audit page"):
        st.session_state["page"] = "Market Audit"
        st.rerun()
    if st.button("Open the Synthesis replay page"):
        st.session_state["page"] = "Synthesis replay"
        st.rerun()
    if st.button("Analyse a note"):
        st.session_state["page"] = "Note analysis"
        st.rerun()
