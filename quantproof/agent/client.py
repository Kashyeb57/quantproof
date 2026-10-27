"""Meta Model API client.

Auth rule (CLAUDE.md, AGENTS.md rule 6): send `Authorization: Bearer <key>` only when the
environment variable MODEL_API_KEY is set. In the cloud environment the key is a network secret
and the proxy adds the header itself, so the variable is usually absent there and we send no auth
header. The key is never printed, logged or stored on objects that might be printed.

Full client: streaming Chat Completions with retries, token/cost logging and helpers.
"""

from __future__ import annotations

import base64
import datetime
import json
import mimetypes
import os
import time
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from quantproof.agent.costs import Usage, estimate_cost, price_for

BASE_URL = "https://api.meta.ai/v1"
API_KEY_ENV = "MODEL_API_KEY"

DEFAULT_MODEL = "muse-spark-1.3"
DEFAULT_MAX_TOKENS = 16000
TIMEOUT_S = 600
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 3
DEFAULT_LOG = Path(".cache") / "meta_calls.jsonl"


def auth_headers(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Headers for a Meta Model API request.

    Returns {"Authorization": "Bearer <key>"} when MODEL_API_KEY is set to a non-blank value,
    otherwise {} so the environment's network secret supplies the header. `env` defaults to
    os.environ; tests pass a dict instead.
    """
    env = os.environ if env is None else env
    key = env.get(API_KEY_ENV, "").strip()
    return {"Authorization": f"Bearer {key}"} if key else {}


def request_headers(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Full header set for a JSON request: content type plus auth (if any)."""
    return {"Content-Type": "application/json", **auth_headers(env)}


class MetaAPIError(RuntimeError):
    """Failure of a Meta Model API request.

    Attributes:
        status: HTTP status code, or None for network errors (URLError / timeout).
        detail: response body (or error text) truncated to 500 characters.
            Never contains request headers.
    """

    def __init__(
        self, message: str, *, status: int | None = None, detail: str = ""
    ) -> None:
        super().__init__(message)
        self.status = status
        self.detail = (detail if detail else message)[:500]


@dataclass(frozen=True)
class ChatResult:
    """Result of one chat() call."""

    text: str
    model: str
    finish_reason: str | None
    usage: Usage
    cost_usd: float
    seconds: float
    attempts: int

    @property
    def truncated(self) -> bool:
        """True when the model stopped because it hit max_tokens."""
        return self.finish_reason == "length"


def image_part(path: Path) -> dict:
    """OpenAI-style image content part for a local image file.

    Returns {"type": "image_url", "image_url": {"url": "data:<mime>;base64,<data>"}}.
    The mime type comes from mimetypes.guess_type; non image/* types raise ValueError.
    """
    mime, _ = mimetypes.guess_type(str(path))
    if mime is None or not mime.startswith("image/"):
        raise ValueError(f"not an image file: {path} (mime {mime!r})")
    raw = Path(path).read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


def parse_sse(lines: Iterable[bytes]) -> tuple[str, str | None, dict | None]:
    """Parse a Chat Completions server-sent-events stream.

    Returns (content, finish_reason, usage). Lines not starting with "data:" are
    ignored; parsing stops at "data: [DONE]". An event's "choices" may be empty
    or missing (the final usage event has none); delta "content" may be missing
    or None. Keeps the last non-None finish_reason and the last non-None usage.
    A stream that ends without a finish_reason was cut off; callers treat None as incomplete.
    Raises ValueError on an event that is not valid JSON.
    """
    parts: list[str] = []
    finish_reason: str | None = None
    usage: dict | None = None
    for raw in lines:
        if isinstance(raw, bytes):
            text = raw.decode("utf-8", errors="replace")
        else:
            text = raw  # type: ignore[assignment]
        stripped = text.strip()
        if not stripped.startswith("data:"):
            continue
        payload = stripped[len("data:") :].strip()
        if payload == "[DONE]":
            break
        if not payload:
            continue
        try:
            event = json.loads(payload)
        except json.JSONDecodeError as exc:
            # A dropped event could silently lose part of an extraction: fail loudly instead.
            raise ValueError(f"malformed event in stream: {payload[:200]!r}") from exc
        if not isinstance(event, dict):
            continue
        choices = event.get("choices")
        if choices:
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                reason = choice.get("finish_reason")
                if reason is not None:
                    finish_reason = reason
                delta = choice.get("delta") or {}
                if isinstance(delta, dict):
                    content = delta.get("content")
                    if content is not None:
                        parts.append(content)
        event_usage = event.get("usage")
        if event_usage is not None:
            usage = event_usage
    return "".join(parts), finish_reason, usage


def _read_http_error_body(exc: urllib.error.HTTPError) -> str:
    """Read an HTTPError body as text, truncated to 500 characters."""
    try:
        raw = exc.read()
    except Exception:
        raw = b""
    if raw is None:
        return str(exc)[:500]
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", errors="replace")
    else:
        text = str(raw)
    if not text:
        text = str(exc)
    return text[:500]


def chat(
    messages: list[dict],
    *,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    purpose: str = "",
    log_path: Path | None = DEFAULT_LOG,
    env: Mapping[str, str] | None = None,
    urlopen=urllib.request.urlopen,
    sleep=time.sleep,
) -> ChatResult:
    """POST a streaming Chat Completions request and return the assembled reply.

    Streams (stream=True with stream_options include_usage) because the gateway
    returns 502 when a non-streamed reply takes more than about 30 s; a stream
    keeps the connection busy while Muse Spark reasons (reasoning counts against
    max_tokens, hence the large default). Prompt caching is automatic on a
    repeated prefix, so callers put stable content (instructions, then filing
    text) first and the question last.

    Retries HTTP statuses in RETRY_STATUSES and network errors up to
    MAX_ATTEMPTS attempts in total, sleeping 2 ** attempt seconds between them.
    """
    price_for(model)  # an unknown model fails here, before any money is spent
    start = time.monotonic()
    url = f"{BASE_URL}/chat/completions"
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    payload = json.dumps(body).encode("utf-8")
    headers = request_headers(env)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        request = urllib.request.Request(
            url, data=payload, headers=headers, method="POST"
        )
        try:
            with urlopen(request, timeout=TIMEOUT_S) as response:
                text, finish_reason, usage_dict = parse_sse(response)
            usage = Usage.from_response(usage_dict or {})
            cost = estimate_cost(model, usage)
            seconds = time.monotonic() - start
            result = ChatResult(
                text=text,
                model=model,
                finish_reason=finish_reason,
                usage=usage,
                cost_usd=cost,
                seconds=seconds,
                attempts=attempt,
            )
            if log_path is not None:
                record = {
                    "time": datetime.datetime.now(
                        datetime.timezone.utc
                    ).isoformat(),
                    "model": model,
                    "purpose": purpose,
                    "input_tokens": usage.input_tokens,
                    "cached_tokens": usage.cached_tokens,
                    "output_tokens": usage.output_tokens,
                    "reasoning_tokens": usage.reasoning_tokens,
                    "cost_usd": cost,
                    "seconds": seconds,
                    "finish_reason": finish_reason,
                    "attempts": attempt,
                }
                log_file = Path(log_path)
                log_file.parent.mkdir(parents=True, exist_ok=True)
                with log_file.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record) + "\n")
            return result
        except urllib.error.HTTPError as exc:
            detail = _read_http_error_body(exc)
            if exc.code in RETRY_STATUSES:
                if attempt >= MAX_ATTEMPTS:
                    raise MetaAPIError(
                        f"Meta API request failed with status {exc.code} "
                        f"after {attempt} attempts: {detail}",
                        status=exc.code,
                        detail=detail,
                    ) from exc
                sleep(2**attempt)
                continue
            raise MetaAPIError(
                f"Meta API request failed with status {exc.code}: {detail}",
                status=exc.code,
                detail=detail,
            ) from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            detail = str(exc)[:500]
            if attempt >= MAX_ATTEMPTS:
                raise MetaAPIError(
                    f"Meta API request failed after {attempt} attempts: {detail}",
                    status=None,
                    detail=detail,
                ) from exc
            sleep(2**attempt)
            continue
    raise AssertionError("unreachable")  # pragma: no cover


def spend(log_path: Path = DEFAULT_LOG) -> float:
    """Sum of cost_usd in the JSON-lines cost log; 0.0 if the file is missing."""
    path = Path(log_path)
    try:
        fh = path.open("r", encoding="utf-8")
    except FileNotFoundError:
        return 0.0
    total = 0.0
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                total += float(record.get("cost_usd", 0.0) or 0.0)
            except (TypeError, ValueError):
                continue
    return total
