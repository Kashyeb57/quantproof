"""Lifetime cap: enforced from the whole cost log, before extracting."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quantproof.app import live


def test_total_cap_env_default_and_override():
    assert live.total_cap_usd({}) == 20.0
    assert live.total_cap_usd({"QP_TOTAL_CAP_USD": "7.5"}) == 7.5
    assert live.total_cap_usd({"QP_TOTAL_CAP_USD": "junk"}) == 20.0


def test_total_spend_sums_the_whole_log(tmp_path: Path):
    log = tmp_path / "meta_calls.jsonl"
    records = [
        {"time": "2026-10-01T10:00:00+00:00", "cost_usd": 1.25},
        {"time": "2026-10-27T10:00:00+00:00", "cost_usd": 2.75},
    ]
    log.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    assert live.total_spend(log) == pytest.approx(4.0)
    assert live.total_spend(tmp_path / "missing.jsonl") == 0.0


class FakeExtractor:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return object()


def test_lifetime_cap_refuses_before_extracting():
    guard = live.LiveGuard()
    fake = FakeExtractor()
    with pytest.raises(live.LiveRefused, match="lifetime cap"):
        guard.get_or_extract(
            "url-1", fake, spent_today=0.0, cap=5.0, estimate=0.50,
            spent_total=19.75, total_cap=20.0,
        )
    assert fake.calls == 0


def test_lifetime_cap_allows_a_run_that_fits():
    guard = live.LiveGuard()
    fake = FakeExtractor()
    _note, from_cache = guard.get_or_extract(
        "url-1", fake, spent_today=0.0, cap=5.0, estimate=0.50,
        spent_total=19.00, total_cap=20.0,
    )
    assert from_cache is False
    assert fake.calls == 1
