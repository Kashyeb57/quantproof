"""Offline, deterministic tests for quantproof.report.audit_run.

Uses the saved JPM 38582 filing / extraction / cross-check fixtures only.
No network and no API key: every model call is replayed from recordings.
The priced replay takes ~3 s, so it runs once in a module-scoped fixture.

Test tolerances: exact equality for replayed deterministic values (same
seed, same code path), as stated in the task.
"""

from __future__ import annotations

import gzip
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quantproof.agent.client import ChatResult  # noqa: E402
from quantproof.agent.costs import Usage  # noqa: E402
from quantproof.ingest.edgar import FilingRef  # noqa: E402
from quantproof.ingest.html import read_filing  # noqa: E402
from quantproof.report import audit_run  # noqa: E402
from quantproof.report.audit_run import (  # noqa: E402
    Recorder,
    Replay,
    ReplayError,
    audit_note,
    has_estimated_value,
    is_preliminary_text,
    select_candidates,
)

F = ROOT / "tests" / "fixtures"
STEM = "jpm_424b2-38582"


def _filing():
    return read_filing((F / "filings" / f"{STEM}.htm").read_bytes())


def _calls(reply_text: str | None = None) -> list[dict]:
    """Recorded calls exactly as specified: extraction, then cross-check."""
    reply = (
        (F / "extractions" / f"{STEM}.reply.json").read_text(encoding="utf-8")
        if reply_text is None
        else reply_text
    )
    calls = [
        {
            "purpose": "extract",
            "model": "muse-spark-1.3",
            "finish_reason": "stop",
            "text": reply,
        }
    ]
    calls += json.loads(
        (F / "crosscheck" / f"{STEM}.calls.json").read_text(encoding="utf-8")
    )
    return calls


def _flat_inputs(names, pricing_date, maturity_date, **kwargs):
    """Pinned pre-anchoring inputs: replay tests stay hermetic.

    The real ``anchored_inputs`` reads the developer's local Treasury
    and FRED files, which CI does not have; pinning the old flat
    stated inputs here keeps every replay expectation independent of
    this machine. The anchored wiring itself is proved by
    ``test_anchored_inputs_price_with_implied_vols`` below.
    """
    n = len(names)
    vols = (0.25,) * n
    market = {
        "rate": 0.037,
        "spread": 0.006,
        "vols": vols,
        "dividends": (0.01,) * n,
        "rho": 0.6 if n > 1 else 0.0,
    }
    report = {
        "rate": 0.037,
        "rate_label": "stated 0.037",
        "vols": list(vols),
        "vol_labels": [f"{name}: stated 0.25 (not anchored)" for name in names],
        "rho": market["rho"],
        "corr_label": "stated 0.6" if n > 1 else "single underlying: rho 0.0",
        "spread": 0.006,
        "dividend": 0.01,
        "erp": 0.05,
    }
    return market, report


@pytest.fixture(autouse=True)
def _flat_market_inputs(monkeypatch):
    monkeypatch.setattr(audit_run, "anchored_inputs", _flat_inputs)


def _run(calls: list[dict]):
    replay = Replay(calls)
    result = audit_note(_filing(), chat=replay, meta={"accession": STEM})
    return replay, result


@pytest.fixture(scope="module")
def priced():
    """The priced replay, run once and reused (it takes ~3 s)."""
    original = audit_run.anchored_inputs
    audit_run.anchored_inputs = _flat_inputs
    try:
        return _run(_calls())
    finally:
        audit_run.anchored_inputs = original


def test_priced_replay(priced):
    replay, result = priced
    # Priced, then stopped by the plausibility gate: under the flat 25% vol
    # assumption our range is $750-944, below the bank's $954.
    assert result["status"] == "needs_review"
    assert result["reason"] == (
        "plausibility gate: bank's estimated value 954.00 is above our whole "
        "range (high 943.76)"
    )
    assert replay.remaining == 0
    assert result["estimated_value"] == 954.0
    assert result["issue_price"] == 1000.0
    assert result["cusip"] == "48136LQH5"
    assert result["quotes"] == {"checked": 78, "verified": 77}
    fair_value = result["fair_value"]
    assert fair_value["low"] <= fair_value["base"]["price"] <= fair_value["high"]
    assert result["loss_odds"] is not None
    assert result["crosscheck"]["agreed"] is True
    assert result["crosscheck"]["second_model"] == "muse-spark-1.2"
    assert result["structure"] == "worst-of 2, autocall, barrier 70% at maturity"
    assert result["fields"]
    for entry in result["fields"]:
        assert {"path", "value", "quote", "verified"} <= set(entry)
    json.dumps(result)


def test_determinism_second_replay(priced):
    _, first = priced
    _, second = _run(_calls())
    # Exact equality: same seed, same code path, replayed recorded replies.
    assert second["fair_value"]["low"] == first["fair_value"]["low"]
    assert second["fair_value"]["high"] == first["fair_value"]["high"]
    assert second["fair_value"]["base"]["price"] == first["fair_value"]["base"]["price"]


def test_anchored_inputs_price_with_implied_vols(tmp_path, monkeypatch):
    """audit_note records anchored market inputs and prices on them.

    Tmp Treasury/FRED files stand in for the developer's local data:
    VIX 18.56 / RVX 23.36 on the 38582 pricing date become the grid
    centres, and the market_inputs record carries every source label.
    """
    from quantproof.market.anchored import anchored_inputs as real_inputs
    from quantproof.market.sources import FredClient

    (tmp_path / "treasury_daily_par_yield_curve_2025-11.csv").write_text(
        "# Source: fake\n"
        'Date,"1 Yr","2 Yr","5 Yr","7 Yr"\n'
        "11/25/2025,3.60,3.43,3.55,3.77\n",
        encoding="utf-8",
    )
    fred = tmp_path / "fred"
    fred.mkdir()
    (fred / "VIXCLS.csv").write_text(
        "observation_date,VIXCLS\n2025-11-25,18.56\n", encoding="utf-8"
    )
    (fred / "RVXCLS.csv").write_text(
        "observation_date,RVXCLS\n2025-11-25,23.36\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        audit_run,
        "anchored_inputs",
        lambda names, pricing_date, maturity_date, **kw: real_inputs(
            names,
            pricing_date,
            maturity_date,
            fred_client=FredClient(cache_dir=fred),
            treasury_dir=tmp_path,
        ),
    )
    _replay, result = _run(_calls())
    assert result["status"] == "priced"
    market_inputs = result["market_inputs"]
    assert market_inputs["vols"] == pytest.approx([0.2336, 0.1856])  # RTY, then SPX
    assert all(
        "stated simplification" in label for label in market_inputs["vol_labels"]
    )
    assert market_inputs["rate_label"].startswith("U.S. Treasury par yield, 2025-11-25")
    assert market_inputs["rate"] == pytest.approx(0.035515, abs=1e-6)


def test_replay_raises_when_calls_run_out_and_on_model_mismatch():
    with pytest.raises(ReplayError):
        audit_note(_filing(), chat=Replay([]), meta={"accession": STEM})

    calls = _calls()
    calls[0] = {**calls[0], "model": "muse-spark-1.2"}
    with pytest.raises(ReplayError):
        audit_note(_filing(), chat=Replay(calls), meta={"accession": STEM})


def test_non_json_reply_is_extraction_failed_no_raise():
    calls = [
        {
            "purpose": "extract",
            "model": "muse-spark-1.3",
            "finish_reason": "stop",
            "text": "not json",
        }
    ]
    _, result = _run(calls)
    assert result["status"] == "extraction_failed"
    assert result["reason"]


def test_no_estimated_value_stops_before_crosscheck():
    data = json.loads(
        (F / "extractions" / f"{STEM}.reply.json").read_text(encoding="utf-8")
    )
    data["estimated_value"] = None
    calls = [
        {
            "purpose": "extract",
            "model": "muse-spark-1.3",
            "finish_reason": "stop",
            "text": json.dumps(data),
        }
    ]
    replay, result = _run(calls)
    assert result["status"] == "no_estimated_value"
    # Only the extraction call was consumed: the cross-check is never called.
    assert replay.remaining == 0


def test_unsupported_features():
    filing = _filing()
    # Any quote string taken verbatim from the filing text.
    quote = next(
        line.strip() for line in filing.text.splitlines() if len(line.strip()) >= 20
    )
    data = json.loads(
        (F / "extractions" / f"{STEM}.reply.json").read_text(encoding="utf-8")
    )
    data["unsupported_features"] = [
        {"value": "Absolute Stock Return", "quote": quote, "section": "x"}
    ]
    replay = Replay(_calls(reply_text=json.dumps(data)))
    result = audit_note(filing, chat=replay, meta={"accession": STEM})
    assert result["status"] == "unsupported"
    assert "unsupported features" in result["reason"].lower()


def test_recorder_records_and_returns_result_unchanged():
    expected_result = ChatResult(
        text="x",
        model="m",
        finish_reason="stop",
        usage=Usage(10, 20),
        cost_usd=0.5,
        seconds=1.5,
        attempts=1,
    )

    def fake_chat(messages, **kw):
        return expected_result

    recorder = Recorder(fake_chat)
    returned = recorder([{"role": "user", "content": "hi"}], purpose="extract", model="m")
    assert returned is expected_result
    assert recorder.calls == [
        {
            "purpose": "extract",
            "model": "m",
            "finish_reason": "stop",
            "text": "x",
            "input_tokens": 10,
            "output_tokens": 20,
            "cost_usd": 0.5,
            "seconds": 1.5,
        }
    ]


def test_preliminary_and_estimated_value_text_helpers():
    assert is_preliminary_text("Subject to completion, dated October 2026")
    assert is_preliminary_text("This is a Preliminary Pricing Supplement")
    assert not is_preliminary_text("Final pricing supplement for the notes")

    assert has_estimated_value("The estimated value of the notes is $962.10")
    assert has_estimated_value("estimated value $954.00 per $1,000 principal amount")
    assert not has_estimated_value("The estimated value is described below")
    assert not has_estimated_value("The notes are offered at $1,000 per note")


def _ref(accession: str) -> FilingRef:
    return FilingRef(
        cik="1",
        accession=accession,
        filing_date=date(2026, 10, 1),
        form="424B2",
        primary_document="a.htm",
    )


def test_select_candidates_quotas_dedupe_and_max_notes():
    refs_by_issuer = {
        "MajorA": [_ref("a1"), _ref("a2"), _ref("a3")],
        "Other": [_ref("a1"), _ref("o1"), _ref("o2")],
        "MajorB": [_ref("b1"), _ref("b2")],
    }
    selected = select_candidates(
        refs_by_issuer, majors=("MajorA", "MajorB"), per_major=2, per_other=1, max_notes=10
    )
    assert [(group, ref.accession) for group, ref in selected] == [
        ("MajorA", "a1"),
        ("MajorA", "a2"),
        ("MajorB", "b1"),
        ("MajorB", "b2"),
        # "a1" under Other is a duplicate accession and does not use the quota.
        ("Other", "o1"),
    ]

    stopped = select_candidates(
        refs_by_issuer, majors=("MajorA", "MajorB"), per_major=2, per_other=1, max_notes=3
    )
    assert [(group, ref.accession) for group, ref in stopped] == [
        ("MajorA", "a1"),
        ("MajorA", "a2"),
        ("MajorB", "b1"),
    ]


def test_market_audit_script_replay(tmp_path):
    script = ROOT / "scripts" / "market_audit.py"
    spec = importlib.util.spec_from_file_location("market_audit_script", script)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)

    out = tmp_path / "audit"
    accession = "00019617-26-38582"
    (out / "filings").mkdir(parents=True)
    (out / "calls").mkdir(parents=True)
    (out / "results").mkdir(parents=True)
    fixture_bytes = (F / "filings" / f"{STEM}.htm").read_bytes()
    (out / "filings" / f"{accession}.htm.gz").write_bytes(gzip.compress(fixture_bytes))
    (out / "calls" / f"{accession}.calls.json").write_text(
        json.dumps(_calls()), encoding="utf-8"
    )
    result_path = out / "results" / f"{accession}.json"
    result_path.write_text(
        json.dumps(
            {
                "meta": {"accession": accession, "issuer_group": "JPMorgan"},
                "status": "priced",
                "cost_usd": 0.12,
                "model_seconds": 40.0,
            }
        ),
        encoding="utf-8",
    )

    api_error_path = out / "results" / "api-error.json"
    api_error_text = json.dumps(
        {"meta": {"accession": "api-error"}, "status": "api_error", "cost_usd": 0.0}
    )
    api_error_path.write_text(api_error_text, encoding="utf-8")

    assert module._replay(out) == 0

    replayed = json.loads(result_path.read_text(encoding="utf-8"))
    assert replayed["status"] == "needs_review"  # plausibility gate, see test_priced_replay
    # Recorded cost / model time are kept: replay itself spends $0.
    assert replayed["cost_usd"] == 0.12
    assert replayed["model_seconds"] == 40.0
    # An api_error result has no complete recording and is left untouched.
    assert api_error_path.read_text(encoding="utf-8") == api_error_text


def test_template_gaps_principal_protection_and_excess_return():
    from types import SimpleNamespace

    from quantproof.report.audit_run import template_gaps

    def ts_with(name):
        return SimpleNamespace(underlyings=[SimpleNamespace(name=SimpleNamespace(value=name))])

    protected = "Under no circumstances will the payment at maturity be less than the stated principal amount."
    reason = template_gaps(ts_with("S&P 500® Index"), protected)
    assert reason.startswith("principal protection is not modelled")
    assert "Under no circumstances" in reason  # the filing's own wording is kept
    reason = template_gaps(ts_with("S&P 500® Futures Excess Return Index"), "no floor here")
    assert reason.startswith("excess-return index underlying")
    assert template_gaps(ts_with("S&P 500® Index"), "no floor here") is None


# --- plausibility gate -------------------------------------------------------

def _fv(low, base, high):
    return {"low": low, "high": high, "base": {"price": base, "std_error": 1.0}}


def test_plausibility_gate_flags_premium_and_bank_above_range():
    # 2% premium: 1020.00 on a 1000 note is the edge (not flagged), 1020.01 is.
    assert audit_run.plausibility_issues(_fv(950, 1020.0, 1030), 1000.0, 990.0) is None
    reason = audit_run.plausibility_issues(_fv(950, 1020.01, 1030), 1000.0, 990.0)
    assert "exceeds the issue price" in reason
    # Bank above our whole range.
    reason = audit_run.plausibility_issues(_fv(900, 930, 960), 1000.0, 984.08)
    assert "above our whole range" in reason and "exceeds" not in reason
    # Bank below our range is not a plausibility issue (our value can exceed
    # the bank's: it is a comparison point, not a target).
    assert audit_run.plausibility_issues(_fv(960, 980, 1000), 1000.0, 950.0) is None
    # Units follow the note: a $10 note.
    assert audit_run.plausibility_issues(_fv(9.0, 9.3, 9.56), 10.0, 9.798) is not None


MS_MISREADS = {
    # accession: why template_gaps rejects it today
    "0001839882-26-049277": "excess-return index",
    "0001839882-26-051777": "principal protection",
    "0001839882-26-052733": "principal protection",
}


@pytest.mark.parametrize("accession", sorted(MS_MISREADS))
def test_plausibility_gate_alone_catches_the_morgan_stanley_misreads(accession, monkeypatch):
    """Switch off template_gaps: the gate must still stop these three notes.

    Replays the saved audit recordings (no network, $0).  These notes were
    priced at ~$1,100-1,330 against bank values of $949-984 before
    template_gaps existed.
    """
    audit = ROOT / "data" / "market_audit"
    old = json.loads((audit / "results" / f"{accession}.json").read_text(encoding="utf-8"))
    filing = read_filing(gzip.decompress((audit / "filings" / f"{accession}.htm.gz").read_bytes()))
    calls = json.loads((audit / "calls" / f"{accession}.calls.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(audit_run, "template_gaps", lambda ts, text: None)
    replay = audit_run.Replay(calls)
    recorded_cross = old.get("crosscheck") or {}
    result = audit_run.audit_note(
        filing,
        chat=replay,
        meta=old["meta"],
        second_model=recorded_cross.get("second_model", audit_run.SECOND_MODEL),
        second_chat=replay,
        recheck_model=recorded_cross.get("recheck_model", audit_run.RECHECK_MODEL),
        recheck_chat=replay,
    )
    assert result["status"] == "needs_review"
    assert result["reason"].startswith("plausibility gate:")
    assert "exceeds the issue price" in result["reason"]


def test_market_audit_script_redo_keeps_previous_recording(tmp_path, monkeypatch):
    """--redo re-runs a saved note through the (mocked) live client, keeps the old calls."""
    script = ROOT / "scripts" / "market_audit.py"
    spec = importlib.util.spec_from_file_location("market_audit_script_redo", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    out = tmp_path / "audit"
    accession = "00019617-26-38582"
    for sub in ("filings", "calls", "results"):
        (out / sub).mkdir(parents=True)
    (out / "filings" / f"{accession}.htm.gz").write_bytes(
        gzip.compress((F / "filings" / f"{STEM}.htm").read_bytes())
    )
    (out / "calls" / f"{accession}.calls.json").write_text('[{"old": true}]', encoding="utf-8")
    (out / "results" / f"{accession}.json").write_text(
        json.dumps({"meta": {"accession": accession, "issuer_group": "JPMorgan"},
                    "status": "extraction_failed", "reason": "old schema"}),
        encoding="utf-8",
    )
    fake_live = Replay(_calls())  # stands in for the Meta API: no network
    monkeypatch.setattr(module.meta_client, "chat", lambda messages, log_path=None, **kw: fake_live(messages, **kw))

    assert module._redo([accession], out, budget_usd=2.0) == 0
    result = json.loads((out / "results" / f"{accession}.json").read_text(encoding="utf-8"))
    assert result["status"] == "needs_review"
    assert result["previous_status"] == "extraction_failed"
    assert result["previous_reason"] == "old schema"
    assert json.loads((out / "calls" / f"{accession}.calls.prev.json").read_text()) == [{"old": True}]
    assert len(json.loads((out / "calls" / f"{accession}.calls.json").read_text())) == len(_calls())

    # A budget below one note's worst case stops before any call.
    (out / "results" / f"{accession}.json").write_text(json.dumps(
        {"meta": {"accession": accession}, "status": "extraction_failed"}), encoding="utf-8")
    assert module._redo([accession], out, budget_usd=0.001) == 0
    assert json.loads((out / "results" / f"{accession}.json").read_text())["status"] == "extraction_failed"


def test_anchored_mask_selects_relative_band(monkeypatch):
    """Anchored vols price on the relative band through audit_note (offline).

    The mask comes from the inputs record, never from the bank's number;
    the same recorded model calls replay, so this spends nothing.
    """
    def _anchored(names, pricing_date, maturity_date, **kwargs):
        n = len(names)
        vols = (0.24, 0.17)[:n] if n == 2 else (0.25,) * n
        rho = 0.6 if n > 1 else 0.0
        return (
            {
                "rate": 0.037,
                "spread": 0.006,
                "vols": vols,
                "dividends": (0.01,) * n,
                "rho": rho,
            },
            {
                "rate": 0.037,
                "rate_label": "test",
                "vols": list(vols),
                "vol_labels": ["test"] * n,
                "vol_anchored": [True] * n,
                "rho": rho,
                "corr_label": "test",
                "spread": 0.006,
                "dividend": 0.01,
                "erp": 0.05,
            },
        )

    monkeypatch.setattr(audit_run, "anchored_inputs", _anchored)
    replay, result = _run(_calls())
    assert replay.remaining == 0
    assert result["market_inputs"]["vol_anchored"] == [True, True]
    assert result["fair_value"] is not None
    assert any(
        "±15% relative band" in line
        for line in result["fair_value"]["assumptions"]
    )


def test_crosscheck_models_recorded_through_audit_note():
    """The models that ran are stored on the result (offline, fakes only)."""
    reply_text = (F / "extractions" / f"{STEM}.reply.json").read_text(encoding="utf-8")
    seen = []

    def fake_second(messages, **kw):
        seen.append((kw.get("model"), kw.get("purpose")))
        return ChatResult(
            text=reply_text,
            model=kw.get("model"),
            finish_reason="stop",
            usage=Usage(input_tokens=10, output_tokens=5),
            cost_usd=0.01,
            seconds=0.5,
            attempts=1,
        )

    replay = Replay(
        [
            {
                "purpose": "extract",
                "model": "muse-spark-1.3",
                "finish_reason": "stop",
                "text": reply_text,
            }
        ]
    )
    result = audit_note(
        _filing(),
        chat=replay,
        meta={"accession": STEM},
        second_model="second-test-model",
        second_chat=fake_second,
        recheck_model="recheck-test-model",
        recheck_chat=fake_second,
    )
    assert replay.remaining == 0
    assert result["crosscheck"]["second_model"] == "second-test-model"
    assert result["crosscheck"]["recheck_model"] == "recheck-test-model"
    # Same reply twice: no diffs, so the recheck chat is never needed.
    assert result["crosscheck"]["agreed"] is True
    assert seen == [("second-test-model", "extract")]
    assert result["fair_value"] is not None
