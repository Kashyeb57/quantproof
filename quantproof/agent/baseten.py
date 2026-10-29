"""Muse Spark Open client via Baseten (the independent second extraction).

OpenAI-compatible Chat Completions at ``https://inference.baseten.co/v1``,
model exactly ``meta-models/muse-spark-open-1026``. The model always
reasons, so only the final answer (``message.content``) is returned;
``message.reasoning_content`` is never treated as the reply.

Auth rule: ``Authorization: Bearer <key>`` from env ``BASETEN_API_KEY``,
which must be set (there is no network-secret proxy for Baseten as there
is for the Meta API). The key is never printed, logged or stored. Calls
are billed in Baseten credits, separately from Meta credits, and are
logged apart (``.cache/baseten_calls.jsonl``), never in the app's cost
log. Published Baseten rate for Muse Spark Open (Oct 2026, several
independent listings agree): $1.25 / $0.15 cached / $4.25 per 1M tokens.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path

from quantproof.agent import client as meta_client
from quantproof.agent.client import ChatResult, MetaAPIError
from quantproof.agent.costs import Price, Usage

BASE_URL = "https://inference.baseten.co/v1"
KEY_ENV = "BASETEN_API_KEY"
OPEN_MODEL = "meta-models/muse-spark-open-1026"
DEFAULT_MAX_TOKENS = 16000
TIMEOUT_S = 600
DEFAULT_LOG = Path(".cache") / "baseten_calls.jsonl"

# Published Baseten rate for Muse Spark Open (Oct 2026). Same numbers as
# the muse-spark family; kept here (not in costs.py) because Baseten
# credits are billed separately from Meta credits.
OPEN_PRICE = Price(input=1.25, cached_input=0.15, output=4.25)


def api_key(env: Mapping[str, str] | None = None) -> str | None:
    """The Baseten key, or None when it is missing or blank."""
    env = os.environ if env is None else env
    key = env.get(KEY_ENV, "").strip()
    return key or None


def estimate_cost(usage: Usage) -> float:
    """Estimated Baseten-credit dollars for one call at the published rate."""
    fresh = usage.input_tokens - usage.cached_tokens
    return (
        fresh * OPEN_PRICE.input
        + usage.cached_tokens * OPEN_PRICE.cached_input
        + usage.output_tokens * OPEN_PRICE.output
    ) / 1e6


def _parse_reply(data: dict) -> tuple[str, str | None, dict | None]:
    """Return (final answer, finish_reason, usage) from a Completions body.

    Only ``choices[0].message.content`` is read; a list of content blocks
    is joined on its ``text`` parts. ``reasoning_content`` is ignored.
    """
    choices = data.get("choices") or []
    choice = choices[0] if choices else {}
    message = choice.get("message") or {}
    content = message.get("content")
    if content is None:
        text = ""
    elif isinstance(content, str):
        text = content
    else:
        text = "".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict)
        )
    return text, choice.get("finish_reason"), data.get("usage")


def chat(
    messages: list[dict],
    *,
    model: str = OPEN_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    purpose: str = "",
    log_path: Path | None = DEFAULT_LOG,
    env: Mapping[str, str] | None = None,
    urlopen=urllib.request.urlopen,
    sleep=time.sleep,
) -> ChatResult:
    """POST one Chat Completions request to Baseten; return the final answer.

    Only ``model`` ``OPEN_MODEL`` is served here: any other model id fails
    before any money is spent. Retries HTTP 429/5xx and network errors up
    to ``MAX_ATTEMPTS`` attempts like the Meta client.
    """
    if model != OPEN_MODEL:
        raise ValueError(
            f"baseten.chat serves only {OPEN_MODEL!r}, not {model!r}"
        )
    key = api_key(env)
    if key is None:
        raise MetaAPIError(
            f"{KEY_ENV} is not set; the Baseten second extraction needs it",
            status=None,
            detail="missing Baseten API key",
        )
    start = time.monotonic()
    url = f"{BASE_URL}/chat/completions"
    body = {"model": model, "messages": messages, "max_tokens": max_tokens}
    payload = json.dumps(body).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
    }
    max_attempts = meta_client.MAX_ATTEMPTS
    for attempt in range(1, max_attempts + 1):
        request = urllib.request.Request(
            url, data=payload, headers=headers, method="POST"
        )
        try:
            with urlopen(request, timeout=TIMEOUT_S) as response:
                data = json.loads(response.read().decode("utf-8", errors="replace"))
            text, finish_reason, usage_dict = _parse_reply(data)
            usage = Usage.from_response(usage_dict or {})
            usage_estimated = (
                usage.input_tokens == 0 and usage.output_tokens == 0
            )
            if usage_estimated:
                usage = Usage(
                    input_tokens=len(payload) // 4, output_tokens=max_tokens
                )
            cost = estimate_cost(usage)
            seconds = time.monotonic() - start
            meta_client._log_call(
                log_path,
                model=model,
                purpose=purpose,
                usage=usage,
                cost=cost,
                seconds=seconds,
                finish_reason=finish_reason,
                attempts=attempt,
                usage_estimated=usage_estimated,
                empty_reply=not text,
            )
            return ChatResult(
                text=text,
                model=model,
                finish_reason=finish_reason,
                usage=usage,
                cost_usd=cost,
                seconds=seconds,
                attempts=attempt,
                usage_estimated=usage_estimated,
            )
        except urllib.error.HTTPError as exc:
            detail = meta_client._read_http_error_body(exc)
            if exc.code in meta_client.RETRY_STATUSES:
                if attempt >= max_attempts:
                    raise MetaAPIError(
                        f"Baseten request failed with status {exc.code} "
                        f"after {attempt} attempts: {detail}",
                        status=exc.code,
                        detail=detail,
                    ) from exc
                sleep(2**attempt)
                continue
            raise MetaAPIError(
                f"Baseten request failed with status {exc.code}: {detail}",
                status=exc.code,
                detail=detail,
            ) from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            detail = str(exc)[:500]
            if attempt >= max_attempts:
                raise MetaAPIError(
                    f"Baseten request failed after {attempt} attempts: {detail}",
                    status=None,
                    detail=detail,
                ) from exc
            sleep(2**attempt)
            continue
    raise AssertionError("unreachable")  # pragma: no cover
