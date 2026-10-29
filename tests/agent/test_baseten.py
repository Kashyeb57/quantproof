"""Offline tests for quantproof.agent.baseten (fake HTTP only).

No network and no key: urlopen and env are faked. Cost math is checked
against the published Baseten rate stated in the module ($1.25 / $0.15
cached / $4.25 per 1M tokens).
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from quantproof.agent import baseten

MODEL = "meta-models/muse-spark-open-1026"


class _FakeResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def _ok_payload(
    text: str = '{"a": 1}',
    reasoning: str = "thinking...",
    prompt: int = 100,
    completion: int = 50,
    cached: int = 10,
) -> dict:
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": text,
                    "reasoning_content": reasoning,
                },
            }
        ],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "prompt_tokens_details": {"cached_tokens": cached},
            "completion_tokens_details": {"reasoning_tokens": 20},
        },
    }


def test_chat_returns_final_answer_only_with_bearer_auth():
    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        seen["auth"] = request.get_header("Authorization")
        return _FakeResponse(_ok_payload())

    result = baseten.chat(
        [{"role": "user", "content": "hi"}],
        env={"BASETEN_API_KEY": "k"},
        urlopen=fake_urlopen,
        sleep=lambda seconds: None,
        log_path=None,
    )
    assert seen["url"] == "https://inference.baseten.co/v1/chat/completions"
    assert seen["auth"] == "Bearer k"
    assert result.text == '{"a": 1}'  # reasoning_content is ignored
    assert result.model == MODEL
    assert result.finish_reason == "stop"
    # (100 - 10) * 1.25 + 10 * 0.15 + 50 * 4.25 per million: exact formula.
    assert result.cost_usd == pytest.approx((90 * 1.25 + 10 * 0.15 + 50 * 4.25) / 1e6)


def test_chat_joins_list_content_blocks():
    payload = _ok_payload()
    payload["choices"][0]["message"]["content"] = [
        {"type": "text", "text": "a"},
        {"type": "text", "text": "b"},
    ]

    def fake_urlopen(request, timeout=None):
        return _FakeResponse(payload)

    result = baseten.chat(
        [{"role": "user", "content": "hi"}],
        env={"BASETEN_API_KEY": "k"},
        urlopen=fake_urlopen,
        sleep=lambda seconds: None,
        log_path=None,
    )
    assert result.text == "ab"


def test_chat_refuses_other_models_before_any_request():
    def fake_urlopen(request, timeout=None):
        raise AssertionError("no request must be sent")

    with pytest.raises(ValueError):
        baseten.chat(
            [],
            model="muse-spark-1.2",
            env={"BASETEN_API_KEY": "k"},
            urlopen=fake_urlopen,
            log_path=None,
        )


def test_chat_missing_key_raises_before_any_request():
    def fake_urlopen(request, timeout=None):
        raise AssertionError("no request must be sent")

    with pytest.raises(Exception):
        baseten.chat([], env={}, urlopen=fake_urlopen, log_path=None)
    assert baseten.api_key({}) is None
    assert baseten.api_key({"BASETEN_API_KEY": "  "}) is None


def test_chat_http_error_raises_without_retry_on_400():
    calls = []

    def fake_urlopen(request, timeout=None):
        calls.append(request)
        raise urllib.error.HTTPError(
            request.full_url, 400, "Bad Request", {}, io.BytesIO(b"bad")
        )

    with pytest.raises(Exception):
        baseten.chat(
            [{"role": "user", "content": "hi"}],
            env={"BASETEN_API_KEY": "k"},
            urlopen=fake_urlopen,
            sleep=lambda seconds: None,
            log_path=None,
        )
    assert len(calls) == 1


def test_chat_retries_a_500_then_returns():
    calls = []

    def fake_urlopen(request, timeout=None):
        calls.append(request)
        if len(calls) == 1:
            raise urllib.error.HTTPError(
                request.full_url, 500, "Server Error", {}, io.BytesIO(b"bad")
            )
        return _FakeResponse(_ok_payload())

    result = baseten.chat(
        [{"role": "user", "content": "hi"}],
        env={"BASETEN_API_KEY": "k"},
        urlopen=fake_urlopen,
        sleep=lambda seconds: None,
        log_path=None,
    )
    assert result.text == '{"a": 1}'
    assert result.attempts == 2
