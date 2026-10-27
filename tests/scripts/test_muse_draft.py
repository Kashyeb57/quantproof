"""Tests for scripts/muse_draft.py. No network: the API call is replaced by fakes."""

import importlib.util
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from quantproof.agent.client import ChatResult
from quantproof.agent.costs import Usage, estimate_cost

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "muse_draft.py"
spec = importlib.util.spec_from_file_location("muse_draft", SCRIPT)
md = importlib.util.module_from_spec(spec)
spec.loader.exec_module(md)

TAG = "a1b2c3d4"
REPLY = f"""<<<FILE {TAG} quantproof/ingest/html.py>>>
def to_text(html):
    return html
<<<END {TAG}>>>
<<<FILE {TAG} tests/ingest/test_html.py>>>
```python
def test_x():
    assert True
```
<<<END {TAG}>>>
<<<NOTES {TAG}>>>
Unsure about charset fallback.
<<<END {TAG}>>>"""


def test_parse_reply_files_and_notes():
    files, notes = md.parse_reply(REPLY, TAG)
    assert list(files) == ["quantproof/ingest/html.py", "tests/ingest/test_html.py"]
    assert files["quantproof/ingest/html.py"] == "def to_text(html):\n    return html\n"
    # Markdown fences around a whole file are removed.
    assert files["tests/ingest/test_html.py"] == "def test_x():\n    assert True\n"
    assert notes == "Unsure about charset fallback."


def test_truncated_file_is_dropped_not_half_written():
    text = f"<<<FILE {TAG} a.py>>>\nx = 1\n<<<END {TAG}>>>\n<<<FILE {TAG} b.py>>>\ny = "
    files, _ = md.parse_reply(text, TAG)
    assert list(files) == ["a.py"]


@pytest.mark.parametrize("bad", ["../x.py", "/etc/passwd", "a/../../b.py", ".git/config",
                                 ".env", "config/.env.local", "C:\\x.py", "a\\b.py"])
def test_unsafe_paths_rejected(bad):
    with pytest.raises(ValueError):
        md.safe_path(bad)
    with pytest.raises(ValueError):
        md.parse_reply(f"<<<FILE {TAG} {bad}>>>\nx\n<<<END {TAG}>>>", TAG)


def test_every_env_file_is_refused_even_the_example():
    # .env.example is committed on purpose, but Muse never needs to write it; refusing every
    # .env* name keeps the rule simple and errs on the safe side.
    with pytest.raises(ValueError):
        md.safe_path(".env.example")


def test_build_messages_puts_rules_first_and_context_after_task():
    msgs = md.build_messages("Do X", {"a.py": "A = 1\n"}, rules="RULE ONE", tag=TAG)
    assert [m["role"] for m in msgs] == ["system", "user"]
    # The system prompt has no per-call tag, so it stays identical (cacheable) across calls.
    assert "RULE ONE" in msgs[0]["content"] and TAG not in msgs[0]["content"]
    assert msgs[1]["content"].startswith("Task:\nDo X")
    assert f"<<<CONTEXT {TAG} a.py>>>\nA = 1\n<<<END {TAG}>>>" in msgs[1]["content"]
    assert f"<<<FILE {TAG} " in msgs[1]["content"]


def sse(*events):
    """Server-sent events as the API streams them, ending with [DONE]."""
    lines = [f"data: {json.dumps(e)}\n\n" for e in events] + ["data: [DONE]\n\n"]
    return "".join(lines).encode()


STREAM = sse(
    {"choices": [{"delta": {"role": "assistant"}, "finish_reason": None}]},
    {"choices": [{"delta": {"content": "Hello, "}, "finish_reason": None}]},
    {"choices": [{"delta": {"content": "world"}, "finish_reason": "stop"}]},
    {"choices": [], "usage": {"prompt_tokens": 22, "completion_tokens": 2781,
                              "completion_tokens_details": {"reasoning_tokens": 1574}}},
)


def fake_urlopen(captured, payload=STREAM):
    def urlopen(req, timeout):
        captured["req"], captured["timeout"] = req, timeout
        return io.BytesIO(payload)
    return urlopen


def test_call_muse_streams_and_rebuilds_the_reply(monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    captured = {}
    out = md.call_muse([{"role": "user", "content": "hi"}], "muse-spark-1.3", 4000,
                       urlopen=fake_urlopen(captured))
    assert (out.text, out.finish_reason) == ("Hello, world", "stop")
    assert out.usage.output_tokens == 2781 and out.usage.reasoning_tokens == 1574
    req = captured["req"]
    assert req.full_url == "https://api.meta.ai/v1/chat/completions"
    assert req.get_header("Authorization") is None  # network secret supplies it
    body = json.loads(req.data)
    assert body["model"] == "muse-spark-1.3" and body["max_tokens"] == 4000
    assert body["stream"] is True and body["stream_options"] == {"include_usage": True}


def test_call_muse_sends_bearer_when_key_set(monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "test-key-not-real")
    captured = {}
    md.call_muse([], "muse-spark-1.3", 4000, urlopen=fake_urlopen(captured))
    assert captured["req"].get_header("Authorization") == "Bearer test-key-not-real"


def test_call_muse_cut_off_stream_keeps_length_reason():
    out = md.call_muse([], "muse-spark-1.3", 10, urlopen=fake_urlopen({}, sse(
        {"choices": [{"delta": {"content": "x ="}, "finish_reason": "length"}]})))
    assert out.finish_reason == "length"
    # No usage in the stream: charged as an upper bound, all 10 max tokens.
    assert out.usage_estimated and out.usage.output_tokens == 10


def test_log_rows_and_total(tmp_path):
    log = tmp_path / "log.md"
    log.write_text("| When | Model | Task | Sent | Returned | In | Out | Est. cost | Review |\n"
                   "|---|---|---|---|---|---|---|---|---|\n"
                   "| by hand | Muse Code | side task | - | - | - | - | n/a | ok |\n")
    when = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
    for cost in (0.05, 0.1234):
        md.append_log(log, when=when, model="muse-spark-1.3", task="a | b\nc", sent=["x.py"],
                      returned=["y.py"], usage=Usage(10, 20, 5, 15), cost=cost)
    last = log.read_text().splitlines()[-1]
    assert "a \\| b c" in last and "10 (5)" in last and "20 (15)" in last and "$0.1234" in last
    # The escaped pipe in the task must not shift the cost column.
    assert md.total_spend(log) == pytest.approx(0.1734)
    assert md.total_spend(tmp_path / "missing.md") == 0.0


def test_write_files_refuses_escape(tmp_path):
    md.write_files({"a/b.py": "x\n"}, tmp_path)
    assert (tmp_path / "a" / "b.py").read_text() == "x\n"
    with pytest.raises(ValueError):
        md.write_files({"../evil.py": "x"}, tmp_path)


def result(text, finish, prompt_tokens, completion_tokens):
    usage = Usage(prompt_tokens, completion_tokens)
    return ChatResult(text=text, model="muse-spark-1.3", finish_reason=finish, usage=usage,
                      cost_usd=estimate_cost("muse-spark-1.3", usage), seconds=1.0, attempts=1)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    log = tmp_path / "log.md"
    log.write_text("")
    monkeypatch.setattr(md, "LOG_PATH", log)
    monkeypatch.setattr(md, "DRAFTS_DIR", tmp_path / "drafts")
    monkeypatch.setattr(md, "make_tag", lambda: TAG)  # replies in these tests use TAG
    return tmp_path


def test_dry_run_makes_no_call(sandbox, monkeypatch):
    monkeypatch.setattr(md, "call_muse", lambda *a, **k: pytest.fail("API called on dry run"))
    assert md.main(["--task", "x", "--dry-run"]) == 0


def test_main_writes_drafts_and_logs(sandbox, monkeypatch):
    monkeypatch.setattr(md, "call_muse", lambda *a, **k: result(REPLY, "stop", 1000, 2000))
    assert md.main(["--task", "write html.py", "--context", "pyproject.toml"]) == 0
    (run,) = (sandbox / "drafts").iterdir()
    assert (run / "files" / "quantproof" / "ingest" / "html.py").exists()
    assert json.loads((run / "meta.json").read_text())["estimated_cost_usd"] == pytest.approx(0.00975)
    row = md.LOG_PATH.read_text()
    assert "write html.py" in row and "pyproject.toml" in row and "awaiting review" in row


def test_cut_off_reply_is_logged_and_flagged(sandbox, monkeypatch):
    monkeypatch.setattr(md, "call_muse",
                        lambda *a, **k: result("<<<FILE a.py>>>\nx =", "length", 100, 24000))
    assert md.main(["--task", "x"]) == 1
    assert "cut off at max_tokens" in md.LOG_PATH.read_text()
    assert md.total_spend(md.LOG_PATH) > 0  # billed tokens are still counted


def test_over_budget_stops_before_calling(sandbox, monkeypatch):
    md.append_log(md.LOG_PATH, when=datetime.now(timezone.utc), model="m", task="t", sent=[],
                  returned=[], usage=Usage(0, 0), cost=80.01)
    monkeypatch.setattr(md, "call_muse", lambda *a, **k: pytest.fail("called over budget"))
    assert md.main(["--task", "x"]) == 2


def test_stream_that_ends_early_is_flagged(sandbox, monkeypatch):
    # No finish_reason means the connection closed mid-reply: never trust that output.
    monkeypatch.setattr(md, "call_muse", lambda *a, **k: result(REPLY, None, 100, 200))
    assert md.main(["--task", "x"]) == 1
    assert "stream ended early" in md.LOG_PATH.read_text()


def test_file_containing_fixed_markers_is_kept_whole():
    # Day 2: a file that itself contains "<<<FILE ...>>>" / "<<<END>>>" text (this script) was cut
    # in two, and the second half was written over an unrelated file.
    body = 'FORMAT = """\n<<<FILE other/file.py>>>\nx\n<<<END>>>\n"""\nprint(1)\n'
    files, _ = md.parse_reply(f"<<<FILE {TAG} scripts/x.py>>>\n{body}<<<END {TAG}>>>", TAG)
    assert files == {"scripts/x.py": body}


def test_other_tag_is_ignored():
    files, _ = md.parse_reply("<<<FILE deadbeef a.py>>>\nx\n<<<END deadbeef>>>", TAG)
    assert files == {}


def test_apply_holds_existing_files_not_in_context(sandbox, monkeypatch):
    repo = sandbox / "repo"
    (repo / "keep").mkdir(parents=True)
    (repo / "keep" / "old.py").write_text("original\n")
    (repo / "AGENTS.md").write_text("rules")
    monkeypatch.setattr(md, "REPO", repo)
    reply = (f"<<<FILE {TAG} keep/old.py>>>\nclobbered\n<<<END {TAG}>>>\n"
             f"<<<FILE {TAG} keep/new.py>>>\nfresh\n<<<END {TAG}>>>")
    monkeypatch.setattr(md, "call_muse", lambda *a, **k: result(reply, "stop", 10, 20))
    assert md.main(["--task", "x", "--apply"]) == 1
    assert (repo / "keep" / "old.py").read_text() == "original\n"  # not overwritten
    assert (repo / "keep" / "new.py").read_text() == "fresh\n"  # new files are fine
    assert "held (existing, not in --context): keep/old.py" in md.LOG_PATH.read_text()
