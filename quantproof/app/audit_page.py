"""Saved market-audit page for the Streamlit app.

This page reads ONLY the saved audit under ``data/market_audit/``
(written offline by ``scripts/market_audit.py``).  It makes no API
calls, reads no keys and performs no pricing maths of its own: every
number comes from :mod:`quantproof.report.market_audit`, which itself
only summarises the saved per-note result files.

Chart scaling: bank estimates and our fair-value ranges are shown as a
percent of each note's own issue price (estimate / issue price * 100),
so a $10 note and a $1,000 note are comparable on the same axis.

Language: neutral and factual (AGENTS.md rule 8).  "Cost to investor"
is the bank's own disclosed figure: issue price minus the bank's
estimated value.  This is a risk analysis under stated assumptions,
not investment advice.
"""

from __future__ import annotations

from pathlib import Path

from quantproof.app import demo
from quantproof.report.market_audit import (
    aggregate,
    load_results,
    load_run,
    note_row,
    table_columns,
)

ROOT = Path(__file__).resolve().parents[2]
AUDIT_DIR = ROOT / "data" / "market_audit"

_PLAIN_NOTE = (
    "Volatilities, dividends, correlation, rate and credit spread are "
    "stated assumptions (listed below), not market data as of each "
    "pricing date. 'Cost to investor' is the bank's own disclosed "
    "figure: issue price minus the bank's estimated value. This is a "
    "risk analysis under stated assumptions, not investment advice."
)


def page_data(audit_dir: Path = AUDIT_DIR) -> dict:
    """Build the page's data from a saved audit directory (pure).

    Returns a dict with:

    - ``rows``: one :func:`note_row` dict per saved result;
    - ``agg``: :func:`aggregate` over those rows and ``run.json``;
    - ``table``: one dict per row keyed by the ``table_columns``
      display headers, with unformatted numeric values so
      ``st.dataframe`` can sort them numerically;
    - ``chart``: one dict per row that has both an issue price and a
      bank estimated value, scaled to percent of issue price
      (``issue_price_pct`` is always 100.0; the range fields are
      ``None`` when the note was not priced).

    No Streamlit calls, no API calls, no keys.
    """
    results = load_results(audit_dir)
    run = load_run(audit_dir)
    rows = [note_row(result) for result in results]
    agg = aggregate(rows, run)

    table = [
        {header: row.get(key) for key, header in table_columns()}
        for row in rows
    ]

    chart: list[dict] = []
    for row in rows:
        issue_price = row.get("issue_price")
        estimated_value = row.get("estimated_value")
        if issue_price is None or estimated_value is None:
            continue
        issue_price_f = float(issue_price)
        if not issue_price_f:
            continue
        range_low = row.get("range_low")
        range_high = row.get("range_high")
        chart.append(
            {
                "issuer": row.get("issuer_group"),
                "cusip": row.get("cusip"),
                "issue_price_pct": 100.0,
                "estimate_pct": float(estimated_value) / issue_price_f * 100.0,
                "range_low_pct": (
                    float(range_low) / issue_price_f * 100.0
                    if range_low is not None
                    else None
                ),
                "range_high_pct": (
                    float(range_high) / issue_price_f * 100.0
                    if range_high is not None
                    else None
                ),
            }
        )

    return {"rows": rows, "agg": agg, "table": table, "chart": chart}


def _pct_fraction(value: object) -> str:
    """Format a fraction (0.5 -> '50.0%'); 'n/a' when missing."""
    if value is None:
        return "n/a"
    return f"{100.0 * float(value):.1f}%"


def _pct_points(value: object) -> str:
    """Format a value already in percent points (3.75 -> '3.75%')."""
    if value is None:
        return "n/a"
    return f"{float(value):.2f}%"


def render(st) -> None:
    """Render the Market Audit page using the injected Streamlit module."""
    st.header("Market Audit")

    data = page_data(AUDIT_DIR)
    run = load_run(AUDIT_DIR)
    finished = run.get("finished") if isinstance(run, dict) else None
    st.caption(
        demo.escape_dollars(
            "Saved audit run"
            + (f" (finished: {finished})" if finished else "")
            + ". This page reads the saved audit results only and makes "
            "no API calls."
        )
    )

    if not data["rows"]:
        st.info(
            demo.escape_dollars(
                "No saved market audit results were found. Run the "
                "offline audit first; this page only displays a saved run."
            )
        )
        return

    agg = data["agg"]

    col1, col2, col3 = st.columns(3)
    col1.metric("Notes read", f"{int(agg.get('notes_read') or 0)}")
    total_seconds = agg.get("total_model_seconds")
    col2.metric(
        "Total model time (min)",
        "n/a" if total_seconds is None else f"{float(total_seconds) / 60.0:.1f}",
    )
    total_cost = agg.get("total_cost_usd")
    col3.metric(
        demo.escape_dollars("Total model cost ($)"),
        demo.escape_dollars(
            "n/a" if total_cost is None else f"${float(total_cost):,.2f}"
        ),
    )
    col4, col5, col6 = st.columns(3)
    col4.metric(
        demo.escape_dollars(
            "Median cost to investor (% of issue price, the bank's own figure)"
        ),
        _pct_points(agg.get("median_cost_to_investor_pct")),
    )
    col5.metric("Share priced (%)", _pct_fraction(agg.get("share_priced")))
    col6.metric(
        "Bank estimate inside our range (% of priced or flagged notes)",
        _pct_fraction(agg.get("share_bank_in_range")),
    )

    status_counts = agg.get("status_counts") or {}
    if status_counts:
        counts_text = ", ".join(
            f"{status}: {count}" for status, count in status_counts.items()
        )
        st.markdown(demo.escape_dollars(f"Status counts: {counts_text}"))
    st.caption(
        "needs_review: priced, then stopped by the plausibility gate (base value "
        "more than 2% above the issue price, or the bank's estimate above our "
        "whole range). These rows are not counted as priced until a person "
        "reviews them, but they count in the range denominator above: the gate "
        "flags a note exactly when the bank's estimate is above our range."
    )

    st.dataframe(data["table"], use_container_width=True, hide_index=True)

    if data["chart"]:
        st.caption(
            demo.escape_dollars(
                "Each note is scaled to percent of its own issue price, "
                "so $10 and $1,000 notes are comparable on the same axis. "
                "Points are the bank's estimated value; vertical rules "
                "show our estimated value range where priced; the "
                "horizontal rule is the issue price (100%)."
            )
        )
        x_encoding = {
            "field": "cusip",
            "type": "nominal",
            "title": "Note (CUSIP)",
            "sort": {"field": "estimate_pct", "order": "ascending"},
        }
        spec = {
            "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
            "layer": [
                {
                    "mark": {"type": "rule", "strokeWidth": 3},
                    "encoding": {
                        "x": x_encoding,
                        "y": {
                            "field": "range_low_pct",
                            "type": "quantitative",
                            "title": "% of issue price",
                        },
                        "y2": {"field": "range_high_pct"},
                    },
                },
                {
                    "mark": {"type": "point", "filled": True, "size": 80},
                    "encoding": {
                        "x": x_encoding,
                        "y": {
                            "field": "estimate_pct",
                            "type": "quantitative",
                            "title": "% of issue price",
                        },
                        "tooltip": [
                            {"field": "issuer", "type": "nominal"},
                            {"field": "cusip", "type": "nominal"},
                            {
                                "field": "estimate_pct",
                                "type": "quantitative",
                                "title": "Bank estimate (% of issue price)",
                            },
                            {
                                "field": "range_low_pct",
                                "type": "quantitative",
                                "title": "Range low (% of issue price)",
                            },
                            {
                                "field": "range_high_pct",
                                "type": "quantitative",
                                "title": "Range high (% of issue price)",
                            },
                        ],
                    },
                },
                {
                    "data": {"values": [{"issue_price_pct": 100.0}]},
                    "mark": {
                        "type": "rule",
                        "strokeDash": [4, 4],
                        "color": "#666666",
                    },
                    "encoding": {
                        "y": {
                            "field": "issue_price_pct",
                            "type": "quantitative",
                            "title": "% of issue price",
                        }
                    },
                },
            ],
        }
        st.vega_lite_chart(data["chart"], spec, use_container_width=True)

    st.markdown(demo.escape_dollars(_PLAIN_NOTE))

    assumptions = agg.get("assumptions")
    st.markdown(demo.escape_dollars("**Assumptions**"))
    if isinstance(assumptions, dict) and assumptions:
        for key in sorted(assumptions):
            st.markdown(demo.escape_dollars(f"- {key}: {assumptions[key]}"))
    elif assumptions is not None:
        st.markdown(demo.escape_dollars(f"- {assumptions}"))
    else:
        st.markdown(
            demo.escape_dollars("- No assumptions recorded in the saved run.")
        )

    csv_path = AUDIT_DIR / "notes.csv"
    if csv_path.is_file():
        st.download_button(
            "Download notes.csv",
            data=csv_path.read_bytes(),
            file_name="notes.csv",
            mime="text/csv",
        )
