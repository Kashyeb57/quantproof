"""Streamlit UI for QuantProof (Stage A: demo mode, no API calls).

Flow, in order: pick a saved note -> term sheet with quotes and
cross-check disagreements -> user confirms (edits are labelled
user-entered) -> illustrative market inputs -> proof battery (the
verdict shows only if the maths checks pass) -> verdict with the
risk-neutral value section and the real-world loss-odds section kept
separate -> footer.

All decisions and pricing live in ``quantproof.app.demo`` and
``quantproof.report``; this module is widgets only.  No API key is read
here: Stage A makes no API calls at all.  (Stage B will read keys only
from environment variables or Streamlit secrets.)
"""

from __future__ import annotations

import streamlit as st

from quantproof.app import demo

REPO_URL = "https://github.com/Kashyeb57/quantproof"
VALIDATION_URL = f"{REPO_URL}/blob/main/docs/VALIDATION.md"


def _money(value: object) -> str:
    if value is None:
        return "n/a"
    return f"${float(value):,.2f}"


def _pct(value: object) -> str:
    if value is None:
        return "n/a"
    return f"{100.0 * float(value):.2f}%"


def run() -> None:
    """Render the Stage A demo app."""
    st.set_page_config(page_title="QuantProof risk analysis", layout="wide")
    st.title("QuantProof")
    st.caption(
        "Demo mode: saved JPMorgan filings with recorded extractions. "
        "No API calls, no credits spent."
    )

    # a. Pick a note.
    st.header("1. Pick a note")
    labels = {}
    for stem in demo.NOTE_STEMS:
        note = demo.load_note(stem)
        labels[stem] = demo.note_label(stem, note.termsheet)
    stem = st.selectbox(
        "Saved note (demo mode)", demo.NOTE_STEMS, format_func=labels.get
    )
    st.text_input("EDGAR link (coming soon)", value="", disabled=True)
    st.file_uploader("Upload a filing (coming soon)", disabled=True)

    note = demo.load_note(stem)
    edits_by_stem: dict = st.session_state.setdefault("edits", {})
    edits: dict[str, str] = edits_by_stem.setdefault(stem, {})

    edited_ts, edited_paths, edit_errors = demo.apply_user_edits(
        note.termsheet, edits
    )
    for error in edit_errors:
        st.error(f"Edit not applied: {error}")
    ts = edited_ts if edited_paths else note.termsheet
    # Term rows reflect the edited values, labelled user-entered.
    row_note = demo.DemoNote(
        stem=stem, termsheet=ts, filing_text=note.filing_text,
        checks=note.checks, unverified=note.unverified,
    )

    # b. Term sheet.
    st.header("2. Term sheet")
    if ts.is_preliminary.value:
        st.write("Based on preliminary terms.")
    rows = demo.term_rows(row_note, edited_paths)
    st.dataframe(rows, use_container_width=True, hide_index=True)
    if note.unverified:
        st.write(
            "Fields needing confirmation (no verified quote): "
            + ", ".join(note.unverified)
        )

    diffs = demo.recorded_crosscheck_diffs(stem, note.termsheet)
    if diffs is None:
        st.caption("Cross-check: no recorded second extraction in demo mode.")
    elif not diffs:
        st.write(
            "Cross-check: the recorded second extraction agrees on every field."
        )
    else:
        st.write("Cross-check disagreements (confirm before pricing):")
        st.dataframe(
            [
                {
                    "field": d.path,
                    "first extraction": str(d.first),
                    "second extraction": str(d.second),
                    "status": d.status,
                }
                for d in diffs
            ],
            use_container_width=True,
            hide_index=True,
        )

    with st.expander("Edit a field (edited fields are labelled user-entered)"):
        field = st.selectbox("Field", [r["field"] for r in rows], key="edit_field")
        raw = st.text_input("New value", key="edit_value")
        if st.button("Apply edit"):
            if raw.strip():
                edits[field] = raw.strip()
                st.rerun()
        if edits and st.button("Clear edits"):
            edits.clear()
            st.rerun()
    if edited_paths:
        st.write("User-entered fields: " + ", ".join(sorted(edited_paths)))

    confirmed = st.checkbox("I confirm these terms")
    if not confirmed:
        st.info("Tick “I confirm these terms” to continue to pricing.")
        return

    # c. Market inputs.
    st.header("3. Market inputs")
    st.warning(
        f"{demo.ILLUSTRATIVE_BANNER}: the inputs below are illustrative "
        "assumptions for this analysis, not market data. Every result is "
        "conditional on them."
    )
    defaults = demo.DEFAULT_INPUTS[stem]
    col_rate, col_spread, col_rho = st.columns(3)
    rate = col_rate.number_input(
        "Risk-free rate", value=float(defaults["rate"]), format="%.4f"
    )
    spread = col_spread.number_input(
        "Issuer credit spread", value=float(defaults["spread"]), format="%.4f"
    )
    rho = col_rho.number_input(
        "Correlation (all pairs)",
        value=float(defaults["rho"]),
        min_value=-0.99,
        max_value=1.0,
        format="%.2f",
        disabled=ts.n_underlyings < 2,
    )
    vols: list[float] = []
    dividends: list[float] = []
    for i, underlying in enumerate(ts.underlyings):
        col_vol, col_div = st.columns(2)
        default_vol = defaults["vols"][i] if i < len(defaults["vols"]) else 0.20
        default_div = (
            defaults["dividends"][i] if i < len(defaults["dividends"]) else 0.01
        )
        vols.append(
            col_vol.number_input(
                f"Volatility: {underlying.name.value}",
                value=float(default_vol), format="%.4f", key=f"vol_{i}",
            )
        )
        dividends.append(
            col_div.number_input(
                f"Dividend yield: {underlying.name.value}",
                value=float(default_div), format="%.4f", key=f"div_{i}",
            )
        )
    market = demo.build_market(
        ts, rate=rate, spread=spread, vols=tuple(vols),
        dividends=tuple(dividends), rho=rho,
    )

    # d. Proof checks.
    st.header("4. Proof checks")
    with st.spinner("Running the proof battery..."):
        battery = demo.run_proof_battery(note, ts, market)
    st.caption(battery["note"])
    if battery["kind"] == "refused":
        st.error(f"Refused: {battery['note']}")
        return
    st.dataframe(
        [
            {
                "check": r.get("name"),
                "result": (
                    "NEEDS CONFIRMATION"
                    if r.get("name") == "quotes" and not r.get("passed")
                    else ("PASS" if r.get("passed") else "FAIL")
                ),
                "detail": r.get("detail"),
            }
            for r in battery["results"]
        ],
        use_container_width=True,
        hide_index=True,
    )
    if not battery["passed"]:
        st.error(
            "The proof battery did not pass, so no value is shown. "
            "See the failed checks above."
        )
        return
    if battery["kind"] == "synthesized":
        st.info(
            "This note's agent-written payoff passed its proof battery in "
            "the registry. Pricing registered payoffs in the app comes "
            "after Stage A, so no estimated value is shown here yet."
        )
        _footer()
        return

    # e. Verdict.
    st.header("5. Verdict")
    with st.spinner("Pricing under the stated assumptions..."):
        result = demo.run_analysis(note, ts, market)
    verdict = demo.verdict_summary(result)
    if verdict["preliminary"]:
        st.write("Based on preliminary terms.")
    col_issue, col_bank, col_range = st.columns(3)
    col_issue.metric("Issue price", _money(verdict["issue_price"]))
    col_bank.metric("Bank's estimated value", _money(verdict["bank_estimate"]))
    col_range.metric(
        "Our estimated value range",
        f"{_money(verdict['low'])} - {_money(verdict['high'])}",
    )
    se = verdict["base_std_error"]
    if verdict["base_price"] is not None and se is not None:
        st.write(
            f"Base estimated value: {_money(verdict['base_price'])} "
            f"+- {_money(1.96 * float(se))} (1.96 SE, SE = {_money(se)}), "
            f"seed {verdict['seed']}, base paths {verdict['base_paths']}, "
            f"grid paths per point {verdict['grid_paths']}."
        )
    if verdict["bank_position"] is not None:
        st.write(
            "Position of the bank's estimated value: "
            f"{verdict['bank_position']}."
        )
    if verdict["driver"] is not None:
        label, low, high = verdict["driver"]
        st.write(
            f"Biggest driver of the range: {label} "
            f"({_money(low)} to {_money(high)} across its stated range)."
        )

    st.subheader("Estimated value (risk-neutral)")
    st.write(
        "Risk-neutral Monte Carlo under the assumptions above. The "
        "bank's estimated value is a comparison point only; assumptions "
        "are never adjusted to match it."
    )
    fair = result["fair_value"]
    st.dataframe(
        [
            {"assumption": a}
            for a in (fair.get("assumptions") or [])
        ],
        use_container_width=True,
        hide_index=True,
    )

    st.subheader("Real-world loss odds (REAL-WORLD measure, not a value)")
    st.write(
        "These figures use the REAL-WORLD measure. They are not a value "
        "and must not be read as an estimated value."
    )
    odds = result["loss_odds"]
    std_errors = odds.get("std_errors") or {}
    st.write(f"- P(loss): {_pct(odds.get('prob_loss'))} (SE {_pct(std_errors.get('prob_loss'))})")
    st.write(f"- P(loss > 20%): {_pct(odds.get('prob_loss_over_20'))} (SE {_pct(std_errors.get('prob_loss_over_20'))})")
    st.write(f"- P(loss > 50%): {_pct(odds.get('prob_loss_over_50'))} (SE {_pct(std_errors.get('prob_loss_over_50'))})")
    if odds.get("prob_called") is None:
        st.write("- P(called early): n/a (no autocall)")
    else:
        st.write(f"- P(called early): {_pct(odds.get('prob_called'))} (SE {_pct(std_errors.get('prob_called'))})")
    st.write(f"- Paths: {odds.get('n_paths')}, seed: {odds.get('seed')}, measure: {odds.get('measure')}")
    st.write(demo.ISSUER_RISK_LINE)

    st.subheader("Loss heatmap (real-world)")
    stress = result["stress"]
    vol_scales = stress.get("vol_scales") or []
    heat_rows = []
    for i, drift in enumerate(stress.get("drift_levels") or []):
        row = {"Drift": f"{float(drift):.4f}"}
        probs = (stress.get("prob_loss") or [[]])[i]
        for j, scale in enumerate(vol_scales):
            row[f"{float(scale):.2f}x vol"] = _pct(probs[j]) if j < len(probs) else "n/a"
        heat_rows.append(row)
    st.dataframe(heat_rows, use_container_width=True, hide_index=True)
    st.caption(
        "Cells are P(loss) under the real-world measure for each drift "
        "(rows) and vol scale (columns)."
    )

    _footer()


def _footer() -> None:
    """f. Footer on every completed path."""
    st.divider()
    st.write(demo.FOOTER_LINE + ".")
    st.write(f"[QuantProof repository]({REPO_URL})")
    st.write(f"[Validation report]({VALIDATION_URL})")
