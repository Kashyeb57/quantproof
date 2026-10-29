"""Offline tests for the synthesis --model option (fakes only, no network).

run_note records which model wrote each payoff; the benchmark script
routes --model open through the Baseten client (final answer only, spend
logged apart from Meta spend); the report splits costs by model.
"""

from __future__ import annotations

import importlib.util
import re
import sys

import pytest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quantproof.agent import baseten
from quantproof.agent.costs import Usage
from quantproof.report import synth_run
from quantproof.report.market_audit import check_neutral
from quantproof.report.synthesis_benchmark import (
    _model_of,
    render_markdown,
    summarise,
)

_SCRIPT = ROOT / "scripts" / "synthesis_benchmark.py"
_spec = importlib.util.spec_from_file_location("synthesis_benchmark_script", _SCRIPT)
bench_script = importlib.util.module_from_spec(_spec)
assert _spec is not None and _spec.loader is not None
_spec.loader.exec_module(bench_script)

OPEN = baseten.OPEN_MODEL
WONDER = synth_run.SYNTH_MODEL


def _setup_note(monkeypatch, seen):
    """Fake termsheet/market/examples/synthesis around run_note."""
    ts = SimpleNamespace(
        n_underlyings=1, autocall=None, coupon=None, upside=None,
        downside=None, cusip=None, issuer=None, issue_price=None,
        estimated_value=None, notional=SimpleNamespace(value=1000.0),
        unsupported_features=[],
    )
    monkeypatch.setattr(synth_run, "termsheet_from_audit", lambda calls, text: ts)
    monkeypatch.setattr(synth_run, "stated_market", lambda ts: object())

    def fake_examples(filing, **kw):
        seen["examples_model"] = kw.get("model")
        return []

    def fake_synth(*args, **kw):
        seen["synth_model"] = kw.get("model")
        seen["effort"] = kw.get("reasoning_effort")
        return SimpleNamespace(passed=False, attempts=[], code="", examples=[])

    monkeypatch.setattr(synth_run, "extract_examples", fake_examples)
    monkeypatch.setattr(synth_run, "synthesise_payoff", fake_synth)


def test_run_note_defaults_to_wonder(monkeypatch):
    seen: dict = {}
    _setup_note(monkeypatch, seen)
    result = synth_run.run_note(
        SimpleNamespace(text="filing"), [], {"accession": "x"},
        chat=object(), template_covered=False,
    )
    assert result["status"] == "synthesis_failed"
    assert result["model"] == WONDER
    assert result["examples_model"] == "muse-spark-1.3"
    assert seen["synth_model"] == WONDER
    assert seen["examples_model"] == "muse-spark-1.3"


def test_run_note_records_open_models(monkeypatch):
    seen: dict = {}
    _setup_note(monkeypatch, seen)
    result = synth_run.run_note(
        SimpleNamespace(text="filing"), [], {"accession": "x"},
        chat=object(), template_covered=False,
        synth_model=OPEN, examples_model=OPEN,
    )
    assert result["model"] == OPEN
    assert result["examples_model"] == OPEN
    assert seen["synth_model"] == OPEN
    assert seen["examples_model"] == OPEN


def test_run_note_skip_records_model_without_synthesis(monkeypatch):
    seen: dict = {}
    _setup_note(monkeypatch, seen)
    example = SimpleNamespace(
        worst_final=1.0, payment=SimpleNamespace(value=100.0, citation=None)
    )
    monkeypatch.setattr(synth_run, "extract_examples", lambda filing, **kw: [example])

    def boom(*args, **kwargs):
        raise AssertionError("skipped notes must not reach synthesis")

    monkeypatch.setattr(synth_run, "synthesise_payoff", boom)
    result = synth_run.run_note(
        SimpleNamespace(text="filing"), [], {"accession": "x"},
        chat=object(), template_covered=False, min_examples=3,
        synth_model=OPEN, examples_model=OPEN,
    )
    assert result["status"] == "skipped_few_examples"
    assert result["model"] == OPEN


def test_open_chat_drops_reasoning_effort(monkeypatch):
    seen: dict = {}

    def fake_chat(messages, **kw):
        seen.update(kw)
        return "SENTINEL"

    monkeypatch.setattr(bench_script.baseten_client, "chat", fake_chat)
    out = bench_script._open_chat(
        [{"role": "user", "content": "hi"}],
        model=OPEN, max_tokens=10, reasoning_effort="medium", purpose="synthesis",
    )
    assert out == "SENTINEL"
    assert "reasoning_effort" not in seen  # Baseten takes no such kwarg
    assert seen["model"] == OPEN


def test_model_config_backends_and_unknown():
    wonder = bench_script._model_config("wonder")
    assert wonder["synth_model"] == WONDER
    assert wonder["examples_model"] == "muse-spark-1.3"
    assert wonder["log_path"] == bench_script.COST_LOG
    assert wonder["cost_fn"] is None

    opened = bench_script._model_config("open")
    assert opened["synth_model"] == OPEN
    assert opened["examples_model"] == OPEN
    assert opened["log_path"] == baseten.DEFAULT_LOG  # Baseten spend logs apart
    usage = Usage(input_tokens=1000, output_tokens=1000)
    assert opened["cost_fn"](OPEN, usage) == baseten.estimate_cost(usage)

    try:
        bench_script._model_config("other")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown --model must raise")


def test_main_passes_model_to_live(monkeypatch):
    seen: dict = {}

    def fake_live(out, budget_usd, only, model="wonder"):
        seen.update(out=out, budget_usd=budget_usd, only=only, model=model)
        return 0

    monkeypatch.setattr(bench_script, "live", fake_live)
    assert bench_script.main(["--model", "open", "--budget-usd", "15"]) == 0
    assert seen["model"] == "open"
    assert seen["budget_usd"] == 15.0
    assert bench_script.main([]) == 0
    assert seen["model"] == "wonder"  # argparse default


def _result(model, cost, cusip, accession):
    row = {
        "cusip": cusip,
        "structure": "buffered note",
        "issue_price": 1000.0,
        "estimated_value": 950.0,
        "examples": [],
        "attempts": [],
        "passed": False,
        "n_attempts": 0,
        "repaired": False,
        "first_failing_checks": [],
        "status": "synthesis_failed",
        "cost_usd": cost,
        "model_seconds": 1.0,
        "meta": {"accession": accession},
    }
    if model is not None:
        row["model"] = model
    return row


def test_summarise_splits_cost_by_model():
    assert _model_of({}) == WONDER  # saved rows predate the field
    summary = summarise(
        [_result(WONDER, 0.10, "A", "1"), _result(OPEN, 0.20, "B", "2"),
         _result(None, 0.05, "C", "3")],
        [],
    )
    assert summary["models"] == [OPEN, WONDER]
    # Binary floating point: 0.10 + 0.05 is not exactly 0.15.
    assert summary["cost_by_model"][OPEN] == pytest.approx(0.20)
    assert summary["cost_by_model"][WONDER] == pytest.approx(0.15)
    assert summary["total_cost_usd"] == pytest.approx(0.35)


def test_render_marks_model_and_split_costs_neutral():
    results = [_result(WONDER, 0.10, "AAAA00001", "1"),
               _result(OPEN, 0.20, "BBBB00002", "2")]
    summary = summarise(results, [])
    markdown = render_markdown(results, summary)
    assert "--model open" in markdown
    assert WONDER in markdown and OPEN in markdown
    assert "Cost (meta-models/muse-spark-open-1026, USD): 0.2000" in markdown
    assert "Baseten credits, billed separately from Meta credits" in markdown
    prose = re.sub(r"```.*?```|~~~.*?~~~", "", markdown, flags=re.DOTALL)
    assert check_neutral(prose) == []
