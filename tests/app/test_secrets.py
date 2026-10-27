"""Key resolution: environment variables or Streamlit secrets only."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quantproof.app import live

NAMES = ("MODEL_API_KEY", "SEC_USER_AGENT")


def test_environment_wins_over_secrets():
    resolved = live.resolve_secrets(
        NAMES,
        env={"MODEL_API_KEY": "env-key"},
        secrets={"MODEL_API_KEY": "secret-key", "SEC_USER_AGENT": "A a@b.c"},
    )
    assert resolved == {"MODEL_API_KEY": "env-key", "SEC_USER_AGENT": "A a@b.c"}


def test_secrets_are_the_fallback_and_blanks_are_unset():
    resolved = live.resolve_secrets(
        NAMES, env={"MODEL_API_KEY": ""}, secrets={"MODEL_API_KEY": "secret-key"}
    )
    assert resolved == {"MODEL_API_KEY": "secret-key"}
    assert live.resolve_secrets(NAMES, env={}, secrets={}) == {}
    assert live.resolve_secrets(NAMES, env={}, secrets=None) == {}


def test_secrets_example_has_placeholders_only():
    example = (ROOT / ".streamlit" / "secrets.toml.example").read_text(
        encoding="utf-8"
    )
    assert "paste-your-meta-model-api-key-here" in example
    assert "your-email@example.com" in example
