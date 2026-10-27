"""Streamlit UI for QuantProof (demo mode default; Stage B live mode).

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

from quantproof.app import demo, live
from quantproof.agent import client as meta_client
from quantproof.ingest.edgar import EdgarClient

REPO_URL = "https://github.com/Kashyeb57/quantproof"
VALIDATION_URL = f"{REPO_URL}/blob/main/docs/VALIDATION.md"

def _write(text: object) -> None:
    """st.markdown with every $ escaped (Streamlit renders $...$ as LaTeX)."""
    st.markdown(demo.escape_dollars(text))


def _caption(text: object) -> None:
    st.caption(demo.escape_dollars(text))


def _warning(text: object) -> None:
    st.warning(demo.escape_dollars(text))


def _error(text: object) -> None:
    st.error(demo.escape_dollars(text))


def _info(text: object) -> None:
    st.info(demo.escape_dollars(text))




def _money(value: object) -> str:
    if value is None:
        return "n/a"
    return demo.escape_dollars(f"${float(value):,.2f}")


def _pct(value: object) -> str:
    if value is None:
        return "n/a"
    return f"{100.0 * float(value):.2f}%"


@st.cache_data(show_spinner=False)
def _cached_note(stem: str):
    """Demo note for ``stem``, cached per note (fixture loads are pure)."""
    return demo.load_note(stem)


@st.cache_data(show_spinner=False)
def _cached_battery(stem, edits_key, rate, spread, vols, dividends, rho):
    """Proof battery cached per (note, edits, market inputs) set."""
    note = _cached_note(stem)
    ts, _edited, _errors = demo.apply_user_edits(note.termsheet, dict(edits_key))
    market = demo.build_market(
        ts, rate=rate, spread=spread, vols=vols, dividends=dividends, rho=rho
    )
    return demo.run_proof_battery(note, ts, market)


@st.cache_data(show_spinner=False)
def _cached_analysis(stem, edits_key, rate, spread, vols, dividends, rho):
    """Verdict analysis cached per (note, edits, market inputs) set."""
    note = _cached_note(stem)
    ts, _edited, _errors = demo.apply_user_edits(note.termsheet, dict(edits_key))
    market = demo.build_market(
        ts, rate=rate, spread=spread, vols=vols, dividends=dividends, rho=rho
    )
    return demo.run_analysis(note, ts, market)


def _client_env() -> dict:
    """API keys from environment variables or Streamlit secrets only.

    Returns a mapping with MODEL_API_KEY / SEC_USER_AGENT when set in
    ``os.environ`` or ``st.secrets``.  Values are never logged, displayed
    or stored on any object that gets printed.
    """
    import os

    try:
        secrets = st.secrets  # type: ignore[attr-defined]
    except Exception:
        secrets = None
    return live.resolve_secrets(
        ("MODEL_API_KEY", "SEC_USER_AGENT", "QP_DAILY_CAP_USD"),
        env=os.environ,
        secrets=secrets,
    )


def _live_chat():
    """Chat callable bound to the env/secrets keys (Muse Spark 1.3 only)."""
    env = _client_env()

    def chat(messages, **kwargs):
        kwargs.setdefault("model", live.LIVE_MODEL)
        return meta_client.chat(messages, env=env, **kwargs)

    return chat


def run() -> None:
    """Render the Stage A demo app."""
    st.set_page_config(page_title="QuantProof risk analysis", layout="wide")
    st.title("QuantProof")
    mode = st.radio(
        "Mode",
        ["Demo mode", "Live mode"],
        horizontal=True,
        help="Demo mode uses saved filings and spends nothing. "
        "Live mode downloads a filing and extracts it with Muse Spark 1.3.",
    )
    live_mode = mode == "Live mode"
    cap_env = _client_env()
    cap = live.daily_cap_usd(
        {"QP_DAILY_CAP_USD": cap_env["QP_DAILY_CAP_USD"]}
        if "QP_DAILY_CAP_USD" in cap_env
        else {}
    )
    spent_today = live.daily_spend()
    if live_mode and not live.live_available(spent_today, cap):
        _error(
            "Live mode is off: today's spend has reached the daily cap "
            f"of ${cap:,.2f} (set by QP_DAILY_CAP_USD). "
            "Demo mode remains available."
        )
        live_mode = False
    if live_mode:
        _warning(
            "Live mode uses real API credits: each new filing is extracted "
            "with Muse Spark 1.3 (the only model the public app calls) "
            "and cross-checked with a second extraction."
        )
    else:
        _caption(
            "Demo mode: saved JPMorgan filings with recorded extractions. "
            "No API calls, no credits spent."
        )

    # a. Pick a note.
    st.header("1. Pick a note")
    live_note = None
    if live_mode:
        url = st.text_input("EDGAR link to a 424B2 filing (.htm)")
        uploaded = st.file_uploader("Or upload a filing (.htm)", type=["htm", "html"])
        fetch = st.button("Fetch and extract (uses real API credits)")
        if fetch:
            guard: live.LiveGuard = st.session_state.setdefault(
                "live_guard", live.LiveGuard()
            )
            try:
                if url.strip():
                    key = source = url.strip()

                    def extract_fn():
                        filing = live.filing_from_url(
                            url.strip(),
                            edgar_client=EdgarClient(env=_client_env()),
                        )
                        return live.extract_and_crosscheck(
                            filing, source, chat=_live_chat()
                        )

                elif uploaded is not None:
                    import hashlib

                    data = uploaded.getvalue()
                    key = "upload:" + hashlib.sha256(data).hexdigest()
                    source = "upload"

                    def extract_fn():
                        filing = live.filing_from_upload(data)
                        return live.extract_and_crosscheck(
                            filing, source, chat=_live_chat()
                        )

                else:
                    raise ValueError("enter an EDGAR link or upload a filing first")
                with st.spinner("Downloading and extracting (Muse Spark 1.3)..."):
                    live_note, from_cache = guard.get_or_extract(
                        key,
                        extract_fn,
                        spent_today=live.daily_spend(),
                        cap=cap,
                    )
                if from_cache:
                    _caption("Served from this session's cache (no new extraction).")
                st.session_state["live_note"] = live_note
            except live.LiveRefused as refused:
                _error(str(refused))
                _footer()
                return
            except Exception as error:  # download / extraction failures are shown, never hidden
                _error(f"Live extraction failed: {error}")
                _footer()
                return
        live_note = st.session_state.get("live_note") or live_note
        if live_note is None:
            _info("Enter an EDGAR link or upload a filing, then fetch and extract.")
            _footer()
            return
        stem = "live"
        note = demo.DemoNote(
            stem=stem,
            termsheet=live_note.termsheet,
            filing_text=live_note.filing_text,
            checks=live_note.checks,
            unverified=live_note.unverified,
        )
    else:
        labels = {}
        for stem_option in demo.NOTE_STEMS:
            labels[stem_option] = demo.note_label(
                stem_option, _cached_note(stem_option).termsheet
            )
        stem = st.selectbox(
            "Saved note (demo mode)", demo.NOTE_STEMS, format_func=labels.get
        )
        note = _cached_note(stem)
    edits_by_stem: dict = st.session_state.setdefault("edits", {})
    edits: dict[str, str] = edits_by_stem.setdefault(stem, {})

    edited_ts, edited_paths, edit_errors = demo.apply_user_edits(
        note.termsheet, edits
    )
    for error in edit_errors:
        _error(f"Edit not applied: {error}")
    ts = edited_ts if edited_paths else note.termsheet
    # Term rows reflect the edited values, labelled user-entered.
    row_note = demo.DemoNote(
        stem=stem, termsheet=ts, filing_text=note.filing_text,
        checks=note.checks, unverified=note.unverified,
    )

    # b. Term sheet.
    st.header("2. Term sheet")
    if ts.is_preliminary.value:
        _write("Based on preliminary terms.")
    rows = demo.term_rows(row_note, edited_paths)
    st.dataframe(rows, use_container_width=True, hide_index=True)
    if note.unverified:
        _write(
            "Fields needing confirmation (no verified quote): "
            + ", ".join(note.unverified)
        )

    if live_mode:
        diffs = live_note.diffs
        if live_note.crosscheck_error:
            _caption(f"Cross-check unavailable: {live_note.crosscheck_error}")
        elif not diffs:
            _write(
                "Cross-check: the second extraction (also Muse Spark 1.3 in "
                "the public app, a weaker independence check than a "
                "different model service) agrees on every field."
            )
        else:
            _write("Cross-check disagreements (confirm before pricing):")
    else:
        diffs = demo.recorded_crosscheck_diffs(stem, note.termsheet)
    if not live_mode and diffs is None:
        _caption("Cross-check: no recorded second extraction in demo mode.")
    elif not live_mode and not diffs:
        _write(
            "Cross-check: the recorded second extraction agrees on every field."
        )
    elif diffs:
        _write("Cross-check disagreements (confirm before pricing):")
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
        _write("User-entered fields: " + ", ".join(sorted(edited_paths)))

    unsupported = live.unsupported_features(ts)
    if unsupported:
        for feature, quote in unsupported:
            _error(f"Unsupported feature: {feature}")
            if quote:
                _write(f"Filing quote: {quote}")
        _write(
            "This feature is not covered by a pricing template. Payoff "
            "synthesis runs offline only, so this note cannot be priced "
            "in the public app."
        )
        _footer()
        return

    confirmed = st.checkbox("I confirm these terms")
    if not confirmed:
        _info("Tick “I confirm these terms” to continue to pricing.")
        return

    # c. Market inputs.
    st.header("3. Market inputs")
    _warning(
        f"{demo.ILLUSTRATIVE_BANNER}: the inputs below are illustrative "
        "assumptions for this analysis, not market data. Every result is "
        "conditional on them."
    )
    defaults = (
        live.generic_defaults(ts) if live_mode else demo.DEFAULT_INPUTS[stem]
    )
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
        battery = (
            demo.run_proof_battery(note, ts, market)
            if live_mode
            else _cached_battery(
                stem, tuple(sorted(edits.items())), rate, spread,
                tuple(vols), tuple(dividends), rho,
            )
        )
    _caption(battery["note"])
    if battery["kind"] == "refused":
        _error(f"Refused: {battery['note']}")
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
        _error(
            "The proof battery did not pass, so no value is shown. "
            "See the failed checks above."
        )
        return
    if battery["kind"] == "synthesized":
        _info(
            "This note's agent-written payoff passed its proof battery in "
            "the registry. Pricing registered payoffs in the app comes "
            "after Stage A, so no estimated value is shown here yet."
        )
        _footer()
        return

    # e. Verdict.
    st.header("5. Verdict")
    with st.spinner("Pricing under the stated assumptions..."):
        result = (
            demo.run_analysis(note, ts, market)
            if live_mode
            else _cached_analysis(
                stem, tuple(sorted(edits.items())), rate, spread,
                tuple(vols), tuple(dividends), rho,
            )
        )
    verdict = demo.verdict_summary(result)
    if verdict["preliminary"]:
        _write("Based on preliminary terms.")
    col_issue, col_bank, col_range = st.columns(3)
    col_issue.metric("Issue price", _money(verdict["issue_price"]))
    col_bank.metric("Bank's estimated value", _money(verdict["bank_estimate"]))
    col_range.metric(
        "Our estimated value range",
        f"{_money(verdict['low'])} - {_money(verdict['high'])}",
    )
    se = verdict["base_std_error"]
    if verdict["base_price"] is not None and se is not None:
        _write(
            f"Base estimated value: {_money(verdict['base_price'])} "
            f"+- {_money(1.96 * float(se))} (1.96 SE, SE = {_money(se)}), "
            f"seed {verdict['seed']}, base paths {verdict['base_paths']}, "
            f"grid paths per point {verdict['grid_paths']}."
        )
    if verdict["bank_position"] is not None:
        _write(
            "Position of the bank's estimated value: "
            f"{verdict['bank_position']}."
        )
    if verdict["driver"] is not None:
        label, low, high = verdict["driver"]
        _write(
            f"Biggest driver of the range: {label} "
            f"({_money(low)} to {_money(high)} across its stated range)."
        )

    st.subheader("Estimated value (risk-neutral)")
    _write(
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
    _write(
        "These figures use the REAL-WORLD measure. They are not a value "
        "and must not be read as an estimated value."
    )
    odds = result["loss_odds"]
    std_errors = odds.get("std_errors") or {}
    _write(f"- P(loss): {_pct(odds.get('prob_loss'))} (SE {_pct(std_errors.get('prob_loss'))})")
    _write(f"- P(loss > 20%): {_pct(odds.get('prob_loss_over_20'))} (SE {_pct(std_errors.get('prob_loss_over_20'))})")
    _write(f"- P(loss > 50%): {_pct(odds.get('prob_loss_over_50'))} (SE {_pct(std_errors.get('prob_loss_over_50'))})")
    if odds.get("prob_called") is None:
        _write("- P(called early): n/a (no autocall)")
    else:
        _write(f"- P(called early): {_pct(odds.get('prob_called'))} (SE {_pct(std_errors.get('prob_called'))})")
    _write(f"- Paths: {odds.get('n_paths')}, seed: {odds.get('seed')}, measure: {odds.get('measure')}")
    _write(demo.ISSUER_RISK_LINE)

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
    _caption(
        "Cells are P(loss) under the real-world measure for each drift "
        "(rows) and vol scale (columns)."
    )

    _footer()


def _footer() -> None:
    """f. Footer on every completed path."""
    st.divider()
    _write(demo.FOOTER_LINE + ".")
    _write(f"[QuantProof repository]({REPO_URL})")
    _write(f"[Validation report]({VALIDATION_URL})")
