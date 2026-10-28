"""Synthesis benchmark helpers: offline, deterministic, saved audit fixtures only.

The note is the saved GS 3x leveraged capped note (0001193125-26-426561), which
the worst-of template covers, so a hand-written correct payoff must agree with
the template and both must reproduce the filing's own table to the cent.
"""

import gzip
import json
from pathlib import Path

import pytest

from quantproof.agent.client import ChatResult
from quantproof.agent.costs import Usage
from quantproof.agent.synthesis import extract_examples
from quantproof.core.termsheet import Cited, Citation
from quantproof.ingest.html import read_filing
from quantproof.report import synth_run
from quantproof.validation.synth_battery import PayoutExample

AUDIT = Path(__file__).resolve().parents[2] / "data" / "market_audit"
GS = "0001193125-26-426561"

CORRECT = '''
import numpy as np
def cashflows(perf, terms):
    k = terms["final_index"]
    w = np.min(perf[:, k, :], axis=1)
    up = np.minimum(terms["upside"]["leverage"] * (w - 1.0), terms["upside"]["cap"])
    cash = np.zeros(perf.shape[:2])
    cash[:, k] = np.where(w >= 1.0, 1.0 + up, w) * terms["notional"]
    return cash
'''
NO_CAP = CORRECT.replace(', terms["upside"]["cap"])', ', 10.0)')  # bug: cap ignored

# Rows of the filing's table ("Hypothetical Final Index Value | Payment at Maturity").
ROWS = [(1.5, 1212.0, "150.000% | 121.200%"), (1.03, 1090.0, "103.000% | 109.000%"),
        (1.0, 1000.0, "100.000% | 100.000%"), (0.75, 750.0, "75.000% | 75.000%")]


@pytest.fixture(scope="module")
def note():
    filing = read_filing(gzip.decompress((AUDIT / "filings" / f"{GS}.htm.gz").read_bytes()))
    calls = json.loads((AUDIT / "calls" / f"{GS}.calls.json").read_text(encoding="utf-8"))
    ts = synth_run.termsheet_from_audit(calls, filing.text)
    return filing, ts, synth_run.stated_market(ts)


def _reply(rows):
    text = json.dumps({"examples": [{"worst_final": w, "payment": p, "quote": q} for w, p, q in rows]})
    return lambda messages, **kw: ChatResult(text=text, model="m", finish_reason="stop",
                                             usage=Usage(1, 1), cost_usd=0.0, seconds=0.0, attempts=1)


def test_percent_table_rows_verify_only_with_the_notional(note):
    filing = note[0]
    rows = ROWS + [(1.5, 212.0, "150.000% | 121.200%")]  # 21.2% must not match inside 121.2%
    with_notional = extract_examples(filing, chat=_reply(rows), notional=1000.0)
    assert [e.payment.citation.verified for e in with_notional] == [True, True, True, True, False]
    without = extract_examples(filing, chat=_reply(ROWS))
    assert not any(e.payment.citation.verified for e in without)


def test_correct_payoff_agrees_with_template_within_3_se(note):
    _, ts, market = note
    cmp = synth_run.template_comparison(ts, market, CORRECT)
    assert cmp["within_3se"] is True
    # Same seed and same paths: a correct payoff matches to rounding.
    assert abs(cmp["diff"]) < 1e-6


def test_payoff_that_ignores_the_cap_fails_the_template_comparison(note):
    _, ts, market = note
    assert synth_run.template_comparison(ts, market, NO_CAP)["within_3se"] is False


def test_template_reproduces_the_filing_table_to_the_cent(note):
    filing, ts, _ = note
    examples = [PayoutExample(worst_final=w, payment=Cited(p, Citation(q, "table", True)))
                for w, p, q in ROWS]
    out = synth_run.template_examples(ts, examples, filing.text)
    assert out["passed"] is True and out["max_error"] <= 0.01  # filing states cents
    assert [r["expected"] for r in out["rows"]] == [1212.0, 1090.0, 1000.0, 750.0]


def test_synthesized_range_brackets_the_base(note):
    _, ts, market = note
    rng = synth_run.synthesized_range(ts, market, CORRECT, n_paths=1000)
    assert rng["points"] == 27  # 3 vol x 3 spread x 3 dividend; one underlying, no skew
    assert rng["skew"] == "not included"
    assert rng["low"] <= rng["high"] and rng["base"]["std_error"] > 0


def test_budget_guard_refuses_before_calling_and_tracks_spend():
    calls = []

    def fake(messages, **kw):
        calls.append(kw)
        return ChatResult(text="x", model="muse-wonder-1.0", finish_reason="stop",
                          usage=Usage(1, 1), cost_usd=0.25, seconds=1.0, attempts=1)

    guard = synth_run.BudgetGuard(fake, budget_usd=1.0)
    msg = [{"role": "user", "content": "a" * 4000}]  # ~1000 input tokens
    guard(msg, model="muse-wonder-1.0", max_tokens=1000)  # worst ~$0.024
    assert guard.spent_usd == 0.25 and not guard.refused
    with pytest.raises(synth_run.BudgetExceeded):
        guard(msg, model="muse-wonder-1.0", max_tokens=40000)  # worst ~$0.80 > $0.75 left
    assert guard.refused and len(calls) == 1


def test_error_recorder_and_replay_reproduce_a_failed_call():
    state = {"n": 0}

    def flaky(messages, **kw):
        state["n"] += 1
        if state["n"] == 1:
            raise TimeoutError("gateway cut the stream")
        return ChatResult(text="ok", model="m", finish_reason="stop",
                          usage=Usage(2, 3), cost_usd=0.1, seconds=2.0, attempts=1)

    rec = synth_run.ErrorRecorder(flaky)
    with pytest.raises(TimeoutError):
        rec([], model="m", purpose="synthesis")
    assert rec([], model="m", purpose="synthesis").text == "ok"
    replay = synth_run.ErrorReplay(rec.calls)
    with pytest.raises(synth_run.RecordedCallError, match="TimeoutError"):
        replay([], model="m", purpose="synthesis")
    assert replay([], model="m", purpose="synthesis").text == "ok" and replay.remaining == 0


def test_fill_attempt_costs_uses_recorded_synthesis_calls_in_order():
    result = {"attempts": [{"cost_usd": 0.0}, {"cost_usd": 0.0}]}
    calls = [{"purpose": "extract_examples", "cost_usd": 0.05, "seconds": 3},
             {"purpose": "synthesis", "cost_usd": 0.4, "seconds": 90},
             {"purpose": "synthesis", "cost_usd": 0.3, "seconds": 70}]
    synth_run.fill_attempt_costs(result, calls)
    assert [a["cost_usd"] for a in result["attempts"]] == [0.4, 0.3]
    assert [a["seconds"] for a in result["attempts"]] == [90.0, 70.0]


def test_battery_filing_examples_accepts_a_percent_table(note):
    """check_filing_examples verifies percent-of-principal rows (bounded, per note)."""
    from quantproof.validation.synth_battery import check_filing_examples

    filing, ts, _ = note
    good = [PayoutExample(worst_final=w, payment=Cited(p, Citation(q, "table", True)))
            for w, p, q in ROWS]
    assert check_filing_examples(CORRECT, ts, good, filing_text=filing.text).passed
    # A payoff that ignores the cap pays 1500 on the 150% row: caught to the cent.
    res = check_filing_examples(NO_CAP, ts, good, filing_text=filing.text)
    assert not res.passed and res.data["rows"][0]["got"] == pytest.approx(2500.0)
