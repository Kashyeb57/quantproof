"""Meta Model API client.

Auth rule (CLAUDE.md, AGENTS.md rule 6): send `Authorization: Bearer <key>` only when the
environment variable MODEL_API_KEY is set. In the cloud environment the key is a network secret
and the proxy adds the header itself, so the variable is usually absent there and we send no auth
header. The key is never printed, logged or stored on objects that might be printed.

The request/response client (Responses API, retries, prompt caching, token and cost log) is a
Day 2 task; this module currently holds the shared constants and the header rule.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

BASE_URL = "https://api.meta.ai/v1"
API_KEY_ENV = "MODEL_API_KEY"


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
