import json
import threading
import urllib.error
from concurrent.futures import ThreadPoolExecutor

import pytest

from quantproof.agent import baseten, client
from quantproof.app import live


@pytest.mark.parametrize("provider", ["meta", "baseten"])
def test_failed_requests_are_counted(provider, tmp_path):
    log = tmp_path / "calls.jsonl"

    def fail(*args, **kwargs):
        raise urllib.error.URLError("offline")

    kwargs = dict(log_path=log, urlopen=fail, sleep=lambda _: None)
    with pytest.raises(client.MetaAPIError):
        if provider == "meta":
            client.chat([], env={}, **kwargs)
        else:
            baseten.chat([], env={"BASETEN_API_KEY": "fake"}, **kwargs)
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(rows) == client.MAX_ATTEMPTS
    assert all(row["failed_attempt"] and row["cost_usd"] > 0 for row in rows)
    assert "fake" not in log.read_text()


def test_simultaneous_sessions_recheck_spend_under_shared_lock(monkeypatch):
    spent = [0.0]
    start = threading.Barrier(2)
    monkeypatch.setattr(live, "daily_spend", lambda: spent[0])
    monkeypatch.setattr(live, "total_spend", lambda: spent[0])
    monkeypatch.setattr(live, "baseten_daily_spend", lambda: 0.0)
    monkeypatch.setattr(live, "baseten_total_spend", lambda: 0.0)

    def run():
        start.wait()
        def extract():
            spent[0] += 0.75
            return "ok"
        try:
            return live.guarded_live_run(live.LiveGuard(), "note", extract,
                                         cap=1.0, estimate=0.75, total_cap=1.0)
        except live.LiveRefused:
            return "refused"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert results.count("refused") == 1
    assert spent[0] == 0.75
