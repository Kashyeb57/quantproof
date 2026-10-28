"""Saved synthesis-benchmark replay page for the Streamlit app.

This page reads ONLY the saved benchmark under
``data/synthesis_benchmark/`` (written offline) and, for coverage, the
saved market audit under ``data/market_audit/``.  It makes no API calls,
reads no keys and performs no pricing maths of its own: every number
comes from :mod:`quantproof.report.synthesis_benchmark`, which itself
only summarises the saved per-note result files.

Language: neutral and factual (AGENTS.md rule 8).  This is a risk
analysis under stated assumptions, not investment advice.
"""

from __future__ import annotations

from pathlib import Path

from quantproof.app import demo
from quantproof.report import market_audit, synthesis_benchmark

ROOT = Path(__file__).resolve().parents[2]
BENCH_DIR = ROOT / "data" / "synthesis_benchmark"
AUDIT_DIR = ROOT / "data" / "market_audit"


def _audit_rows(bench_dir: Path) -> list[dict]:
    """Audit rows for coverage, preferring a sibling ``market_audit`` dir.

    When ``bench_dir`` is the real ``data/synthesis_benchmark``, the
    sibling is the real ``data/market_audit``.  A tmp benchmark dir in
    tests usually has no sibling audit, which gives an empty list.
    """
    sibling = Path(bench_dir).parent / "market_audit"
    audit_dir = sibling if sibling.is_dir() else AUDIT_DIR
    if not Path(audit_dir).is_dir():
        return []
    # In tests a tmp bench dir must not silently pick up the real audit.
    if sibling != AUDIT_DIR and not sibling.is_dir():
        return []
    return [
        market_audit.note_row(result)
        for result in market_audit.load_results(audit_dir)
    ]


def page_data(bench_dir: Path = BENCH_DIR) -> dict:
    """Build the page's data from a saved benchmark directory (pure).

    Returns ``{"summary": ..., "showcase": ...}`` where ``summary`` is
    :func:`synthesis_benchmark.summarise` over the saved benchmark
    results and the saved audit rows, and ``showcase`` is
    :func:`synthesis_benchmark.showcase` over the same results (None
    when no note was repaired).  No Streamlit calls, no API calls.
    """
    results = synthesis_benchmark.load_results(bench_dir)
    summary = synthesis_benchmark.summarise(results, _audit_rows(bench_dir))
    return {"summary": summary, "showcase": synthesis_benchmark.showcase(results)}


def _counterexample_text(attempt: dict) -> str:
    """An attempt's counterexamples joined into one string."""
    counterexamples = attempt.get("counterexamples")
    if isinstance(counterexamples, dict):
        parts = [
            str(counterexamples[key])
            for key in sorted(counterexamples, key=str)
            if counterexamples[key]
        ]
        return "\n\n".join(parts)
    if counterexamples:
        return str(counterexamples)
    return ""


def render(st) -> None:
    """Render the Synthesis replay page using the injected Streamlit module."""
    st.header("Synthesis replay")

    data = page_data(BENCH_DIR)
    summary = data["summary"]

    st.caption(
        demo.escape_dollars(
            "Replayed from the saved benchmark log; no API calls."
        )
    )

    if not summary.get("n_notes"):
        st.info(
            demo.escape_dollars(
                "No saved synthesis benchmark results were found. Run "
                "the offline benchmark first; this page only replays a "
                "saved log."
            )
        )
        return

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Notes", f"{int(summary.get('n_notes') or 0)}")
    col2.metric("Passed", f"{int(summary.get('n_passed') or 0)}")
    mean_attempts = summary.get("mean_attempts")
    col3.metric(
        "Mean attempts",
        "n/a" if mean_attempts is None else f"{float(mean_attempts):.2f}",
    )
    col4.metric("Repaired", f"{int(summary.get('n_repaired') or 0)}")

    example = data["showcase"]
    if example is None:
        st.info(
            demo.escape_dollars(
                "No repaired note is in the saved benchmark log, so "
                "there is no worked example to replay."
            )
        )
        return

    heading = "Worked example"
    if example.get("cusip"):
        heading += f": {example['cusip']}"
    if example.get("structure"):
        heading += f" ({example['structure']})"
    st.subheader(demo.escape_dollars(heading))
    if example.get("label"):
        st.caption(demo.escape_dollars(str(example["label"])))

    # Final figures for the passing attempt, from the saved result row.
    fair_value: dict = {}
    for result in synthesis_benchmark.load_results(BENCH_DIR):
        if result.get("cusip") == example.get("cusip"):
            candidate = result.get("fair_value")
            if isinstance(candidate, dict):
                fair_value = candidate
            break
    base = fair_value.get("base")
    if not isinstance(base, dict):
        base = {}

    attempts = example.get("attempts") or []
    for index, attempt in enumerate(attempts):
        number = index + 1
        st.subheader(f"Attempt {number}")
        st.code(attempt.get("code") or "", language="python")
        checks = [
            {
                "name": check.get("name"),
                "passed": check.get("passed"),
                "detail": check.get("detail"),
            }
            for check in (attempt.get("checks") or [])
            if isinstance(check, dict)
        ]
        if checks:
            st.dataframe(checks, use_container_width=True, hide_index=True)
        counterexample = _counterexample_text(attempt)
        if counterexample:
            st.warning(demo.escape_dollars(counterexample))
        if index + 1 < len(attempts):
            next_feedback = attempts[index + 1].get("feedback_sent")
            if next_feedback:
                with st.expander(
                    demo.escape_dollars(
                        f"Feedback sent to attempt {number + 1}"
                    )
                ):
                    st.markdown(demo.escape_dollars(str(next_feedback)))
        if attempt.get("passed"):
            if base.get("price") is not None:
                text = (
                    f"Passed. Base estimated value: "
                    f"${float(base['price']):,.2f} "
                    f"+/- ${float(base.get('std_error') or 0.0):,.2f} (SE)"
                )
                if (
                    fair_value.get("low") is not None
                    and fair_value.get("high") is not None
                ):
                    text += (
                        f"; estimated value range "
                        f"${float(fair_value['low']):,.2f} - "
                        f"${float(fair_value['high']):,.2f}."
                    )
                else:
                    text += "."
                st.success(demo.escape_dollars(text))
            else:
                st.success("Passed.")
