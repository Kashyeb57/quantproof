import io
import json
import urllib.error
from pathlib import Path

import pytest

from quantproof.agent import client
from quantproof.agent.costs import Usage, estimate_cost


def test_no_auth_header_when_variable_absent():
    # Cloud environment: the network secret adds the header, so we must not send one.
    assert client.auth_headers({}) == {}
    assert client.request_headers({}) == {"Content-Type": "application/json"}


def test_blank_variable_counts_as_absent():
    assert client.auth_headers({"MODEL_API_KEY": "   "}) == {}


def test_bearer_header_when_variable_set():
    headers = client.request_headers({"MODEL_API_KEY": "test-key-not-real"})
    assert headers["Authorization"] == "Bearer test-key-not-real"
    assert headers["Content-Type"] == "application/json"


def test_reads_os_environ_by_default(monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    assert client.auth_headers() == {}
    monkeypatch.setenv("MODEL_API_KEY", "test-key-not-real")
    assert client.auth_headers() == {"Authorization": "Bearer test-key-not-real"}


def test_base_url():
    assert client.BASE_URL == "https://api.meta.ai/v1"


def _req_header(req, name):
    # urllib Request header lookup with case-insensitive fallback for test robustness.
    try:
        value = req.get_header(name)
    except Exception:
        value = None
    if value is not None:
        return value
    candidates = []
    for store in (getattr(req, "headers", {}), getattr(req, "unredirected_hdrs", {})):
        try:
            items = store.items()
        except Exception:
            continue
        candidates.extend(items)
    try:
        candidates.extend(req.header_items())
    except Exception:
        pass
    for key, value in candidates:
        if isinstance(key, str) and key.lower() == name.lower():
            return value
    return None


def _sse_bytes(lines):
    return b"\n".join(lines) + b"\n"


def _usage_block():
    return {
        "prompt_tokens": 120,
        "completion_tokens": 30,
        "prompt_tokens_details": {"cached_tokens": 100},
        "completion_tokens_details": {"reasoning_tokens": 12},
    }


def _chat_stream_bytes(text_parts=("Hello", " world"), usage=None, finish="stop"):
    if usage is None:
        usage = _usage_block()
    lines = []
    lines.append(
        json.dumps({"choices": [{"delta": {"role": "assistant"}}]}).encode()
    )
    lines[0] = b"data: " + lines[0]
    for part in text_parts:
        lines.append(
            b"data: " + json.dumps({"choices": [{"delta": {"content": part}}]}).encode()
        )
    lines.append(
        b"data: "
        + json.dumps({"choices": [{"delta": {}, "finish_reason": finish}]}).encode()
    )
    lines.append(
        b"data: " + json.dumps({"choices": [], "usage": usage}).encode()
    )
    lines.append(b"data: [DONE]")
    lines.append(
        b"data: " + json.dumps({"choices": [{"delta": {"content": "IGNORED"}}]}).encode()
    )
    head = [b": keep-alive", b"event: message"]
    return _sse_bytes(head + lines)


class FakeSleep:
    def __init__(self):
        self.calls = []

    def __call__(self, seconds):
        self.calls.append(seconds)


def test_parse_sse_content_finish_usage_and_done():
    raw = _sse_bytes(
        [
            b": keep-alive comment",
            b"event: message",
            b'data: {"choices": [{"delta": {"role": "assistant"}, "finish_reason": null}]}',
            b'data: {"choices": [{"delta": {"content": "Hello "}, "finish_reason": null}]}',
            b'data: {"choices": [{"delta": {"content": null}, "finish_reason": null}]}',
            b'data: {"oops": 1}',
            b"not a data line",
            b'data: {"choices": [{"delta": {"content": "world"}, "finish_reason": "stop"}]}',
            b'data: {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}',
            b"data: [DONE]",
            b'data: {"choices": [{"delta": {"content": "IGNORED"}}]}',
        ]
    )
    text, finish_reason, usage = client.parse_sse(io.BytesIO(raw))
    assert text == "Hello world"
    assert finish_reason == "stop"
    assert usage == {"prompt_tokens": 10, "completion_tokens": 5}


def test_parse_sse_missing_choices_and_last_finish_wins():
    raw = _sse_bytes(
        [
            b'data: {"choices": [{"delta": {"content": "a"}, "finish_reason": "stop"}]}',
            b'data: {"usage": {"prompt_tokens": 3, "completion_tokens": 1}}',
            b"data: [DONE]",
        ]
    )
    text, finish_reason, usage = client.parse_sse(raw.splitlines(keepends=True))
    assert text == "a"
    # Final usage event has no choices, so the earlier finish_reason is kept.
    assert finish_reason == "stop"
    assert usage == {"prompt_tokens": 3, "completion_tokens": 1}


def test_chat_success_url_body_headers_result():
    messages = [{"role": "user", "content": "hello"}]
    stream = _chat_stream_bytes()
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["timeout"] = timeout
        seen["auth"] = _req_header(req, "Authorization")
        seen["content_type"] = _req_header(req, "Content-Type")
        seen["body"] = json.loads(req.data.decode("utf-8"))
        return io.BytesIO(stream)

    result = client.chat(
        messages, env={}, urlopen=fake_urlopen, sleep=FakeSleep(), log_path=None
    )
    assert seen["url"] == f"{client.BASE_URL}/chat/completions"
    assert seen["timeout"] == client.TIMEOUT_S
    assert seen["auth"] is None
    body = seen["body"]
    assert body["model"] == client.DEFAULT_MODEL
    assert body["messages"] == messages
    assert body["max_tokens"] == client.DEFAULT_MAX_TOKENS
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    assert result.text == "Hello world"
    assert result.finish_reason == "stop"
    assert result.attempts == 1
    assert result.usage == Usage.from_response(_usage_block())
    assert result.cost_usd == pytest.approx(estimate_cost(client.DEFAULT_MODEL, result.usage))
    assert result.seconds >= 0


def test_chat_sends_bearer_when_key_set():
    stream = _chat_stream_bytes(text_parts=("hi",))
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["auth"] = _req_header(req, "Authorization")
        return io.BytesIO(stream)

    client.chat(
        [{"role": "user", "content": "hi"}],
        env={"MODEL_API_KEY": "test-key-not-real"},
        urlopen=fake_urlopen,
        sleep=FakeSleep(),
        log_path=None,
    )
    assert seen["auth"] == "Bearer test-key-not-real"


def test_chat_502_then_success_retries_once():
    stream = _chat_stream_bytes()
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req)
        if len(calls) == 1:
            raise urllib.error.HTTPError(
                req.full_url, 502, "Bad Gateway", {}, io.BytesIO(b"gateway error")
            )
        return io.BytesIO(stream)

    sleep = FakeSleep()
    result = client.chat(
        [{"role": "user", "content": "hi"}],
        env={},
        urlopen=fake_urlopen,
        sleep=sleep,
        log_path=None,
    )
    assert result.attempts == 2
    assert result.text == "Hello world"
    assert sleep.calls == [2]
    assert len(calls) == 2


def test_chat_400_raises_at_once():
    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 400, "Bad Request", {}, io.BytesIO(b"bad request body")
        )

    sleep = FakeSleep()
    with pytest.raises(client.MetaAPIError) as excinfo:
        client.chat(
            [{"role": "user", "content": "hi"}],
            env={},
            urlopen=fake_urlopen,
            sleep=sleep,
            log_path=None,
        )
    assert excinfo.value.status == 400
    assert "bad request body" in excinfo.value.detail
    assert sleep.calls == []


def test_chat_three_503s_gives_up():
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req)
        raise urllib.error.HTTPError(
            req.full_url, 503, "Unavailable", {}, io.BytesIO(b"try later")
        )

    sleep = FakeSleep()
    with pytest.raises(client.MetaAPIError) as excinfo:
        client.chat(
            [{"role": "user", "content": "hi"}],
            env={},
            urlopen=fake_urlopen,
            sleep=sleep,
            log_path=None,
        )
    assert excinfo.value.status == 503
    assert len(calls) == 3
    assert sleep.calls == [2, 4]


def test_chat_urlerror_then_success_retried():
    stream = _chat_stream_bytes()
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req)
        if len(calls) == 1:
            raise urllib.error.URLError("boom")
        return io.BytesIO(stream)

    sleep = FakeSleep()
    result = client.chat(
        [{"role": "user", "content": "hi"}],
        env={},
        urlopen=fake_urlopen,
        sleep=sleep,
        log_path=None,
    )
    assert result.attempts == 2
    assert sleep.calls == [2]


def test_chat_log_line_and_no_message_content(tmp_path):
    log_path = tmp_path / "nested" / "calls.jsonl"
    messages = [{"role": "user", "content": "secret-message-xyz-123"}]
    stream = _chat_stream_bytes()

    def fake_urlopen(req, timeout=None):
        return io.BytesIO(stream)

    result = client.chat(
        messages,
        env={},
        purpose="unit-test",
        urlopen=fake_urlopen,
        sleep=FakeSleep(),
        log_path=log_path,
    )
    raw = log_path.read_text(encoding="utf-8")
    assert "secret-message-xyz-123" not in raw
    assert "Authorization" not in raw
    record = json.loads(raw.strip().splitlines()[-1])
    assert record["model"] == client.DEFAULT_MODEL
    assert record["purpose"] == "unit-test"
    assert record["input_tokens"] == result.usage.input_tokens
    assert record["cached_tokens"] == result.usage.cached_tokens
    assert record["output_tokens"] == result.usage.output_tokens
    assert record["reasoning_tokens"] == result.usage.reasoning_tokens
    assert record["cost_usd"] == pytest.approx(result.cost_usd)
    assert record["seconds"] >= 0
    assert record["finish_reason"] == "stop"
    assert record["attempts"] == 1
    assert "time" in record


def test_chat_log_path_none_writes_nothing(tmp_path):
    stream = _chat_stream_bytes()

    def fake_urlopen(req, timeout=None):
        return io.BytesIO(stream)

    client.chat(
        [{"role": "user", "content": "hi"}],
        env={},
        urlopen=fake_urlopen,
        sleep=FakeSleep(),
        log_path=None,
    )
    assert list(tmp_path.iterdir()) == []


def test_truncated_property():
    base = dict(
        text="x",
        model=client.DEFAULT_MODEL,
        usage=Usage(input_tokens=1, output_tokens=1),
        cost_usd=0.0,
        seconds=0.0,
        attempts=1,
    )
    assert client.ChatResult(finish_reason="length", **base).truncated is True
    assert client.ChatResult(finish_reason="stop", **base).truncated is False
    assert client.ChatResult(finish_reason=None, **base).truncated is False


def test_image_part_gif():
    path = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "filings"
        / "jpm_424b2-38582_files"
        / "image2.gif"
    )
    part = client.image_part(path)
    assert part["type"] == "image_url"
    assert part["image_url"]["url"].startswith("data:image/gif;base64,")


def test_image_part_txt_raises(tmp_path):
    path = tmp_path / "note.txt"
    path.write_text("hello", encoding="utf-8")
    with pytest.raises(ValueError):
        client.image_part(path)


def test_spend_sums_two_logged_calls(tmp_path):
    log_path = tmp_path / "calls.jsonl"
    stream = _chat_stream_bytes()

    def fake_urlopen(req, timeout=None):
        return io.BytesIO(stream)

    first = client.chat(
        [{"role": "user", "content": "one"}],
        env={},
        urlopen=fake_urlopen,
        sleep=FakeSleep(),
        log_path=log_path,
    )
    second = client.chat(
        [{"role": "user", "content": "two"}],
        env={},
        urlopen=fake_urlopen,
        sleep=FakeSleep(),
        log_path=log_path,
    )
    assert client.spend(log_path) == pytest.approx(first.cost_usd + second.cost_usd)


def test_spend_missing_file(tmp_path):
    assert client.spend(tmp_path / "does-not-exist.jsonl") == 0.0


def test_parse_sse_rejects_malformed_event():
    # Skipping a broken event could silently drop part of the reply.
    with pytest.raises(ValueError, match="malformed"):
        client.parse_sse([b'data: {"choices": [\n', b"data: [DONE]\n"])


def test_chat_unknown_model_fails_before_calling(tmp_path):
    def urlopen(*a, **k):
        raise AssertionError("must not call the API")
    with pytest.raises(ValueError, match="no price"):
        client.chat([], model="not-a-meta-model", log_path=None, env={}, urlopen=urlopen)
