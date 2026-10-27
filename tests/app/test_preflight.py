"""Pre-flight cost estimate: a run that would breach the cap never starts."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quantproof.agent.costs import Usage, estimate_cost
from quantproof.app import live


def test_estimate_matches_the_stated_worst_case_formula():
    text_len = 44_189  # cleaned length of the saved 38582 fixture
    input_tokens = -(-text_len // 4)  # ceil(chars / 4)
    one_extraction = estimate_cost(
        live.LIVE_MODEL,
        Usage(input_tokens=input_tokens, output_tokens=16_000),
    )
    one_recheck = estimate_cost(
        live.LIVE_MODEL,
        Usage(input_tokens=input_tokens, output_tokens=4_000),
    )
    expected = 2 * one_extraction + 3 * one_recheck
    assert live.estimate_live_cost(text_len) == pytest.approx(expected)
    # Worst case, not expected case: well above a typical $0.05 extraction.
    assert live.estimate_live_cost(text_len) > 0.20


def test_estimate_scales_with_text_and_refuses_other_models():
    assert live.estimate_live_cost(400_000) > live.estimate_live_cost(40_000)
    with pytest.raises(ValueError, match="muse-spark-1.3 only"):
        live.estimate_live_cost(1000, model="muse-wonder-1.0")


class FakeExtractor:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return object()


def test_preflight_refusal_spends_nothing_with_a_fake_client():
    guard = live.LiveGuard()
    fake = FakeExtractor()
    # Spent $4.50 of a $5 cap; the filing's worst case is $0.75 more.
    with pytest.raises(live.LiveRefused, match="before it starts"):
        guard.get_or_extract(
            "url-1", fake, spent_today=4.50, cap=5.0, estimate=0.75
        )
    assert fake.calls == 0  # the fake client was never called


def test_preflight_allows_a_run_that_fits_the_cap():
    guard = live.LiveGuard()
    fake = FakeExtractor()
    _note, from_cache = guard.get_or_extract(
        "url-1", fake, spent_today=4.50, cap=5.0, estimate=0.49
    )
    assert from_cache is False
    assert fake.calls == 1
