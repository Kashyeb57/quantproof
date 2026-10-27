"""Cost-guard tests with a fake extractor: no API call, no credits.

Each limit is proved at the guard (quantproof/app/live.py LiveGuard):
the session extraction limit, the daily spend cap read from the cost
log, the Spark-1.3-only rule, and that a repeated filing is served
from cache without consuming budget.
"""

from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quantproof.app import live


class FakeExtractor:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return object()


def test_session_limit_is_three_live_extractions():
    guard = live.LiveGuard()
    fake = FakeExtractor()
    for i in range(3):
        note, from_cache = guard.get_or_extract(
            f"url-{i}", fake, spent_today=0.0, cap=5.0
        )
        assert from_cache is False
    assert fake.calls == 3
    with pytest.raises(live.LiveRefused, match="per browser session"):
        guard.get_or_extract("url-4", fake, spent_today=0.0, cap=5.0)
    assert fake.calls == 3  # refused before extracting: nothing spent


def test_same_filing_is_served_from_cache_without_budget():
    guard = live.LiveGuard()
    fake = FakeExtractor()
    first, cached_first = guard.get_or_extract("url-1", fake, spent_today=0.0, cap=5.0)
    second, cached_second = guard.get_or_extract(
        "url-1", fake, spent_today=0.0, cap=5.0
    )
    assert (cached_first, cached_second) == (False, True)
    assert first is second
    assert fake.calls == 1
    assert guard.extractions_used == 1


def test_daily_cap_refuses_before_extracting():
    guard = live.LiveGuard()
    fake = FakeExtractor()
    with pytest.raises(live.LiveRefused, match="daily cap"):
        guard.get_or_extract("url-1", fake, spent_today=5.0, cap=5.0)
    assert fake.calls == 0
    # Under the cap the same filing extracts normally.
    _note, from_cache = guard.get_or_extract(
        "url-1", fake, spent_today=4.99, cap=5.0
    )
    assert from_cache is False
    assert fake.calls == 1


def test_daily_cap_env_default_and_override():
    assert live.daily_cap_usd({}) == 5.0
    assert live.daily_cap_usd({"QP_DAILY_CAP_USD": "2.50"}) == 2.50
    assert live.daily_cap_usd({"QP_DAILY_CAP_USD": "not-a-number"}) == 5.0
    assert live.daily_cap_usd({"QP_DAILY_CAP_USD": "-1"}) == 5.0


def test_daily_spend_sums_only_todays_cost_log(tmp_path: Path):
    today = datetime.datetime.now(datetime.timezone.utc).date()
    yesterday = today - datetime.timedelta(days=1)
    log = tmp_path / "meta_calls.jsonl"
    records = [
        {"time": f"{today.isoformat()}T10:00:00+00:00", "cost_usd": 0.05},
        {"time": f"{today.isoformat()}T11:00:00+00:00", "cost_usd": 0.07},
        {"time": f"{yesterday.isoformat()}T11:00:00+00:00", "cost_usd": 9.99},
        {"time": "not-a-date", "cost_usd": 9.99},
    ]
    log.write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
    )
    assert live.daily_spend(log) == pytest.approx(0.12)
    assert live.daily_spend(tmp_path / "missing.jsonl") == 0.0
    assert live.live_available(0.12, 5.0) is True
    assert live.live_available(5.0, 5.0) is False
