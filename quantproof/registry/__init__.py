"""Trusted payoffs. Agent-written payoffs enter only after passing the full proof battery."""

from quantproof.registry.sandbox import (
    ALLOWED_MODULES,
    SandboxError,
    SandboxResult,
    StaticReport,
    run_payoff,
    static_check,
)
from quantproof.registry.store import (
    DEFAULT_DIR,
    find_for,
    list_registered,
    load,
    register,
    safe_name,
)

__all__ = [
    "ALLOWED_MODULES",
    "DEFAULT_DIR",
    "SandboxError",
    "SandboxResult",
    "StaticReport",
    "find_for",
    "list_registered",
    "load",
    "register",
    "run_payoff",
    "safe_name",
    "static_check",
]
