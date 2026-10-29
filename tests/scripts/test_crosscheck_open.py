"""Offline tests for scripts/crosscheck_open (no network, no key)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_SCRIPT = ROOT / "scripts" / "crosscheck_open.py"
_spec = importlib.util.spec_from_file_location("crosscheck_open_script", _SCRIPT)
crosscheck_open_module = importlib.util.module_from_spec(_spec)
assert _spec is not None and _spec.loader is not None
_spec.loader.exec_module(crosscheck_open_module)

_splice_calls = crosscheck_open_module._splice_calls


def _call(purpose: str, model: str) -> dict:
    return {"purpose": purpose, "model": model, "text": "t"}


def test_splice_keeps_first_extraction_and_fresh_baseten_leg():
    # A stale second extraction shares the "extract" purpose, so the
    # splice must filter by model: keeping it would break offline replay,
    # which matches recorded models in order.
    old = [
        _call("extract", "muse-spark-1.3"),
        _call("extract", "muse-spark-1.2"),
        _call("recheck", "muse-spark-1.3"),
    ]
    new = [
        _call("extract", "meta-models/muse-spark-open-1026"),
        _call("recheck", "meta-models/muse-spark-open-1026"),
    ]
    assert [(c["purpose"], c["model"]) for c in _splice_calls(old, new)] == [
        ("extract", "muse-spark-1.3"),
        ("extract", "meta-models/muse-spark-open-1026"),
        ("recheck", "meta-models/muse-spark-open-1026"),
    ]


def test_splice_drops_stale_second_extraction_without_fresh_leg():
    old = [_call("extract", "muse-spark-1.3"), _call("extract", "muse-spark-1.2")]
    assert _splice_calls(old, []) == [_call("extract", "muse-spark-1.3")]
