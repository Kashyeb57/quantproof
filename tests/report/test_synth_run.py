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


@pytest.mark.parametrize(
    "payment, quote, worst_final, ok",
    [
        (1073.4, "180.00 80.00% 7.34%", 1.8, True),     # JPM: total return on the notes
        (1800.0, "180.00 80.00% 7.34%", 1.8, False),    # not by the level column
        (1500.0, "150.000% | 121.200%", 1.5, False),    # not by the final-level column
        (1212.0, "150.000% | 121.200%", 1.5, True),     # percent of principal
        (750.0, "75.000% | 75.000%", 0.75, True),       # level and payment both 75%
        (1100.0, "110.00 10.00% 7.34%", 1.1, False),    # 10% is the underlier return only
        (1100.0, "110.00 10.00% 10.00%", 1.1, True),    # 1x upside: stated twice
    ],
)
def test_payment_in_quote_forms(payment, quote, worst_final, ok):
    from quantproof.validation.synth_battery import payment_in_quote

    assert payment_in_quote(payment, quote, 1000.0, worst_final) is ok


def test_repairs_stop_when_only_filing_evidence_is_missing(note):
    """No examples verify: one attempt, no paid repairs that cannot help."""
    from quantproof.agent.synthesis import synthesise_payoff

    filing, ts, market = note
    n = {"calls": 0}

    def chat(messages, **kw):
        n["calls"] += 1
        return ChatResult(text="```python\n" + CORRECT + "\n```", model="m", finish_reason="stop",
                          usage=Usage(1, 1), cost_usd=0.0, seconds=0.0, attempts=1)

    res = synthesise_payoff(filing, ts, market, examples=[], chat=chat, max_attempts=4, n_paths=2000)
    assert not res.passed and len(res.attempts) == 1 and n["calls"] == 1


def test_payment_in_quote_when_level_return_and_payment_coincide():
    from quantproof.validation.synth_battery import payment_in_quote

    # JPM row: level 50.00 (no %), underlier return -50.00%, note return -50.00%.
    assert payment_in_quote(500.0, "50.00 -50.00% -50.00%", 1000.0, 0.5) is True
    assert payment_in_quote(1000.0, "50.00 -50.00% 0.00%", 1000.0, 0.5) is True
    assert payment_in_quote(700.0, "50.00 -50.00% -50.00%", 1000.0, 0.5) is False


def test_prose_worked_example_verifies():
    """MS states examples as sentences, not a table (0001839882-26-049404)."""
    acc = "0001839882-26-049404"
    filing = read_filing(gzip.decompress((AUDIT / "filings" / f"{acc}.htm.gz").read_bytes()))
    quote = "the underlier appreciates 5%, investors will receive $1,075 per security"
    ex = extract_examples(filing, chat=_reply([(1.05, 1075.0, quote), (1.05, 1100.0, quote)]),
                          notional=1000.0)
    assert [e.payment.citation.verified for e in ex] == [True, False]


def test_budget_guard_accepts_custom_cost_fn():
    calls = []

    def fake(messages, **kw):
        calls.append(kw)
        return ChatResult(text="x", model="m", finish_reason="stop",
                          usage=Usage(1, 1), cost_usd=0.25, seconds=1.0, attempts=1)

    guard = synth_run.BudgetGuard(
        fake, budget_usd=1.0, cost_fn=lambda model, usage: 999.0
    )
    with pytest.raises(synth_run.BudgetExceeded):
        guard([{"role": "user", "content": "hi"}], model="m", max_tokens=10)
    assert guard.refused and not calls


def test_run_note_skips_below_min_examples_before_any_synthesis_spend(monkeypatch):
    from types import SimpleNamespace
    filing = SimpleNamespace(text="filing")
    ts = SimpleNamespace(
        n_underlyings=1, autocall=None, coupon=None, upside=None, downside=None,
        cusip=None, issuer=SimpleNamespace(value="I"), issue_price=None,
        estimated_value=None, notional=SimpleNamespace(value=1000.0),
        unsupported_features=[],
    )
    example = SimpleNamespace(
        worst_final=1.0, payment=SimpleNamespace(value=100.0, citation=None)
    )
    monkeypatch.setattr(synth_run, "termsheet_from_audit", lambda calls, text: ts)
    monkeypatch.setattr(synth_run, "stated_market", lambda ts: object())
    monkeypatch.setattr(
        synth_run, "extract_examples", lambda filing, **kw: [example]
    )

    def boom(*args, **kwargs):
        raise AssertionError("skipped notes must not reach synthesis")

    monkeypatch.setattr(synth_run, "synthesise_payoff", boom)
    result = synth_run.run_note(
        filing, [], {"accession": "x"}, chat=object(),
        template_covered=False, min_examples=3,
    )
    assert result["status"] == "skipped_few_examples"
    assert result["passed"] is False
    assert result["n_attempts"] == 0
    assert result["attempts"] == []
    assert "only 1" in result["skip_reason"]
    # Default min_examples=0 proceeds to synthesis (here: the boom).
    with pytest.raises(AssertionError):
        synth_run.run_note(
            filing, [], {"accession": "x"}, chat=object(),
            template_covered=False,
        )
