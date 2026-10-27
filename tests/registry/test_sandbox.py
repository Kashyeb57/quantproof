"""Tests for the static checker and subprocess sandbox for generated payoffs."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import types
from pathlib import Path

import numpy as np
import pytest

from quantproof.registry import sandbox
from quantproof.registry.sandbox import run_payoff, static_check


IS_LINUX = sys.platform.startswith("linux")
requires_linux = pytest.mark.skipif(
    not IS_LINUX, reason="subprocess sandbox limits are Linux-only"
)


GOOD_PAYOFF = """
import numpy as np

def cashflows(perf, terms):
    cash = np.zeros((perf.shape[0], perf.shape[1]))
    cash[:, -1] = 1000.0 * np.min(perf[:, -1, :], axis=1)
    return cash
"""


def make_perf() -> np.ndarray:
    return np.array(
        [
            [[1.0, 1.0], [1.1, 0.9], [1.2, 1.1]],
            [[1.0, 1.0], [0.8, 1.0], [0.7, 1.3]],
            [[1.0, 1.0], [1.0, 1.0], [1.5, 1.4]],
            [[1.0, 1.0], [0.9, 0.8], [0.6, 0.5]],
        ],
        dtype=np.float64,
    )


def make_terms() -> dict:
    return {"payment_dates": [0.5, 1.0, 1.5], "notional": 1000.0}


def load_child_module():
    child_path = Path(sandbox.__file__).with_name("_sandbox_child.py")
    spec = importlib.util.spec_from_file_location("_sandbox_child_for_test", child_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@requires_linux
def test_good_payoff_runs_and_matches_numpy_reference_exactly() -> None:
    perf = make_perf()

    result = run_payoff(GOOD_PAYOFF, perf, make_terms(), timeout=20.0)

    assert result.ok, result.error
    assert result.error is None
    assert result.cash is not None
    assert result.cash.shape == (perf.shape[0], perf.shape[1])
    reference = np.zeros((perf.shape[0], perf.shape[1]), dtype=np.float64)
    reference[:, -1] = 1000.0 * np.min(perf[:, -1, :], axis=1)
    # Exact equality is justified: both sides perform the same min and
    # multiplication on identical float64 inputs, with no sampling involved.
    np.testing.assert_array_equal(result.cash, reference)
    assert result.seconds >= 0.0


def wrap(body: str) -> str:
    return "import numpy as np\n\ndef cashflows(perf, terms):\n" + body + "\n"


STATIC_REJECT_CASES = [
    ("import-os", "import os\n\ndef cashflows(perf, terms):\n    return perf[:, :, 0]\n"),
    (
        "import-subprocess",
        "import subprocess\n\ndef cashflows(perf, terms):\n    return perf[:, :, 0]\n",
    ),
    (
        "from-scipy-import-io",
        "from scipy import io\n\ndef cashflows(perf, terms):\n    return perf[:, :, 0]\n",
    ),
    ("dunder-import", wrap('    return __import__("os")')),
    ("open", wrap('    return open("x")')),
    ("eval", wrap('    return eval("1")')),
    ("np-load", wrap('    return np.load("x.npy")')),
    ("np-lib", wrap("    return np.lib")),
    ("dunder-class-attribute", wrap("    return perf.__class__")),
    ("getattr-dynamic-load", wrap('    return getattr(np, "lo" + "ad")')),
    (
        "class-definition",
        "class X:\n    pass\n\ndef cashflows(perf, terms):\n    return perf[:, :, 0]\n",
    ),
    ("dunder-in-string", wrap('    return "a__b"')),
    (
        "two-cashflows-defs",
        "def cashflows(perf, terms):\n    return perf[:, :, 0]\n\n"
        "def cashflows(perf, terms):\n    return perf[:, :, 0]\n",
    ),
    ("missing-cashflows", "import numpy as np\n"),
    ("global", wrap("    global x\n    return perf[:, :, 0]")),
    (
        "top-level-call",
        "print(1)\n\ndef cashflows(perf, terms):\n    return perf[:, :, 0]\n",
    ),
    ("syntax-error", "def cashflows(:\n"),
]


@pytest.mark.parametrize(
    "code", [code for _, code in STATIC_REJECT_CASES], ids=[name for name, _ in STATIC_REJECT_CASES]
)
def test_static_check_rejects_each_forbidden_case(code: str) -> None:
    report = static_check(code)

    assert not report.ok
    assert report.problems


NEW_STATIC_REJECT_CASES = [
    (
        "generator-frame-globals",
        wrap("    return (x for x in ()).gi_frame.f_back.f_globals"),
    ),
    (
        "from-numpy-import-load",
        "from numpy import load\n\ndef cashflows(perf, terms):\n    return perf[:, :, 0]\n",
    ),
    (
        "from-numpy-import-lib-as-l",
        "from numpy import lib as L\n\ndef cashflows(perf, terms):\n    return perf[:, :, 0]\n",
    ),
    (
        "from-numpy-import-sqrt-as-load",
        "from numpy import sqrt as load\n\ndef cashflows(perf, terms):\n    return perf[:, :, 0]\n",
    ),
    (
        "frame-f-globals",
        wrap("    return perf.f_globals"),
    ),
    (
        "code-co-consts",
        wrap("    return perf.co_consts"),
    ),
    (
        "np-char-attribute",
        wrap("    return np.char"),
    ),
    (
        "np-random-attribute",
        wrap("    return np.random"),
    ),
    (
        "str-format-attribute",
        wrap('    return "{0}".format(1)'),
    ),
]


@pytest.mark.parametrize(
    "code",
    [code for _, code in NEW_STATIC_REJECT_CASES],
    ids=[name for name, _ in NEW_STATIC_REJECT_CASES],
)
def test_static_check_rejects_new_escape_cases(code: str) -> None:
    report = static_check(code)

    assert not report.ok
    assert report.problems


def test_static_check_accepts_good_payoff() -> None:
    report = static_check(GOOD_PAYOFF)

    assert report.ok, report.problems
    assert report.problems == []


@requires_linux
def test_infinite_loop_times_out() -> None:
    code = wrap("    while True:\n        pass")

    result = run_payoff(code, make_perf(), make_terms(), timeout=2.0)

    assert not result.ok
    assert result.cash is None
    assert result.error is not None
    assert "timeout after 2" in result.error


@requires_linux
def test_huge_allocation_is_not_ok() -> None:
    code = wrap("    return np.ones(10**11)")

    result = run_payoff(code, make_perf(), make_terms(), timeout=20.0)

    assert not result.ok
    assert result.cash is None
    assert result.error


@requires_linux
def test_exception_in_code_is_not_ok_with_message() -> None:
    code = wrap('    raise ValueError("boom message")')

    result = run_payoff(code, make_perf(), make_terms(), timeout=20.0)

    assert not result.ok
    assert result.error is not None
    assert "ValueError" in result.error
    assert "boom message" in result.error


@requires_linux
def test_wrong_shape_is_not_ok() -> None:
    code = wrap("    return np.zeros((2, 2))")

    result = run_payoff(code, make_perf(), make_terms(), timeout=20.0)

    assert not result.ok
    assert result.error is not None
    assert "shape" in result.error


@requires_linux
def test_nan_is_not_ok() -> None:
    code = wrap("    return np.full((perf.shape[0], perf.shape[1]), np.nan)")

    result = run_payoff(code, make_perf(), make_terms(), timeout=20.0)

    assert not result.ok
    assert result.error is not None
    assert "non-finite" in result.error


@requires_linux
def test_list_of_lists_of_correct_shape_is_ok_and_cast() -> None:
    code = wrap(
        "    return [[0.0 for k in range(perf.shape[1])] "
        "for p in range(perf.shape[0])]"
    )

    result = run_payoff(code, make_perf(), make_terms(), timeout=20.0)

    assert result.ok, result.error
    assert result.cash is not None
    assert result.cash.dtype == np.float64
    np.testing.assert_array_equal(result.cash, np.zeros((4, 3)))


@requires_linux
def test_run_payoff_passes_only_the_three_allowed_env_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}
    real_run = subprocess.run

    def capturing_run(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return real_run(*args, **kwargs)

    monkeypatch.setenv("MODEL_API_KEY", "must-not-reach-the-child")
    monkeypatch.setattr(subprocess, "run", capturing_run)

    result = run_payoff(GOOD_PAYOFF, make_perf(), make_terms(), timeout=20.0)

    assert result.ok, result.error
    env = captured["kwargs"]["env"]
    assert set(env.keys()) == {"PATH", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS"}
    assert "MODEL_API_KEY" not in env


def test_socket_import_is_rejected_statically() -> None:
    report = static_check(
        "import socket\n\ndef cashflows(perf, terms):\n    return perf[:, :, 0]\n"
    )

    assert not report.ok
    assert report.problems


def test_child_allowed_modules_equal_sandbox_allowed_modules() -> None:
    child_path = Path(sandbox.__file__).with_name("_sandbox_child.py")
    spec = importlib.util.spec_from_file_location("_sandbox_child_for_test", child_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.ALLOWED_MODULES == sandbox.ALLOWED_MODULES


def test_child_forbidden_attrs_equal_sandbox_forbidden_attrs() -> None:
    module = load_child_module()

    assert module.FORBIDDEN_ATTRS == sandbox.FORBIDDEN_ATTRS


def test_numpy_proxy_has_no_module_type_attribute() -> None:
    module = load_child_module()

    proxy = module._build_proxy(np)

    assert not isinstance(proxy, types.ModuleType)
    assert all(
        not isinstance(value, types.ModuleType) for value in vars(proxy).values()
    )
    for forbidden in (
        "char",
        "f2py",
        "ctypeslib",
        "load",
        "lib",
        "random",
        "linalg",
        "fft",
        "ctypes",
        "os",
        "sys",
        "subprocess",
    ):
        assert not hasattr(proxy, forbidden), forbidden
    assert hasattr(proxy, "min")
    assert hasattr(proxy, "maximum")
    assert hasattr(proxy, "where")
    assert hasattr(proxy, "cumsum")
    assert hasattr(proxy, "exp")
    assert hasattr(proxy, "sqrt")


def test_scipy_proxy_has_only_stats_and_special_proxies() -> None:
    module = load_child_module()

    scipy_proxy = module._guarded_import("scipy", None, None, ("stats",), 0)

    assert set(vars(scipy_proxy).keys()) == {"stats", "special"}
    assert all(
        not isinstance(value, types.ModuleType)
        for value in vars(scipy_proxy.stats).values()
    )
    assert all(
        not isinstance(value, types.ModuleType)
        for value in vars(scipy_proxy.special).values()
    )


def test_guarded_import_top_and_fromlist_semantics() -> None:
    module = load_child_module()

    numpy_top = module._guarded_import("numpy", None, None, (), 0)
    scipy_top = module._guarded_import("scipy.stats", None, None, (), 0)
    stats_proxy = module._guarded_import("scipy.stats", None, None, ("norm",), 0)
    scipy_from = module._guarded_import("scipy", None, None, ("stats",), 0)

    assert numpy_top is module._PROXY_MODULES["numpy"]
    assert scipy_top is module._PROXY_MODULES["scipy"]
    assert stats_proxy is module._PROXY_MODULES["scipy.stats"]
    assert scipy_from is module._PROXY_MODULES["scipy"]
    assert hasattr(stats_proxy, "norm")


@pytest.mark.parametrize(
    "event,args",
    [
        ("open", ("x", "r")),
        ("os.system", ("echo hi",)),
        ("socket.connect", (object(), ("127.0.0.1", 1))),
        ("import", ("tkinter", None, None, None, 0)),
        ("compile", ("1", "x", "eval")),
        ("subprocess.Popen", (["echo"],)),
        ("ctypes.dlopen", ("libc.so.6",)),
        ("object.__getattr__", (object(), "anything")),
        ("exec", (compile("1", "x", "eval"),)),
    ],
)
def test_audit_hook_raises_for_forbidden_events(event: str, args) -> None:
    module = load_child_module()
    module._AUDIT_ACTIVE = True

    with pytest.raises(PermissionError):
        module._audit_hook(event, args)


def test_audit_hook_allows_import_of_already_loaded_numpy() -> None:
    module = load_child_module()
    module._AUDIT_ACTIVE = True

    assert "numpy" in sys.modules
    module._audit_hook("import", ("numpy", None, None, None, 0))


def run_child_directly(tmp_path: Path, payoff_code: str) -> subprocess.CompletedProcess:
    child_path = Path(sandbox.__file__).with_name("_sandbox_child.py")
    perf = make_perf()
    with (tmp_path / "perf.npy").open("wb") as handle:
        np.save(handle, perf, allow_pickle=False)
    (tmp_path / "terms.json").write_text(json.dumps(make_terms()), encoding="utf-8")
    (tmp_path / "payoff.py").write_text(payoff_code, encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-I", "-B", str(child_path), str(tmp_path)],
        cwd=str(tmp_path),
        env={"PATH": "/usr/bin:/bin"},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=20.0,
    )


@pytest.mark.parametrize(
    "attribute", ["np.char", "np.f2py", "np.ctypeslib"]
)
@requires_linux
def test_proxy_attributes_fail_at_runtime_in_child(
    tmp_path: Path, attribute: str
) -> None:
    code = (
        "import numpy as np\n\n"
        "def cashflows(perf, terms):\n"
        f"    return {attribute}\n"
    )

    completed = run_child_directly(tmp_path, code)

    assert completed.returncode != 0
    assert "AttributeError" in completed.stderr


@requires_linux
def test_good_payoff_with_scipy_stats_runs_under_audit_hook() -> None:
    code = """
import numpy as np
from scipy import stats

def cashflows(perf, terms):
    base = np.maximum(perf[:, :, 0], 0.0)
    cash = np.where(base > 0.0, np.cumsum(base, axis=1), 0.0)
    cash = cash * np.exp(-0.01) * np.sqrt(1.0)
    cash[:, -1] = cash[:, -1] * stats.norm.cdf(0.0)
    cash[:, 0] = np.min(perf[:, 0, :], axis=1)
    return cash
"""

    result = run_payoff(code, make_perf(), make_terms(), timeout=20.0)

    assert result.ok, result.error
    assert result.cash is not None
    assert result.cash.shape == (4, 3)
    assert np.all(np.isfinite(result.cash))


@requires_linux
def test_isolation_list_contains_subprocess_timeout_and_empty_env() -> None:
    result = run_payoff(GOOD_PAYOFF, make_perf(), make_terms(), timeout=20.0)

    assert result.ok, result.error
    assert "subprocess" in result.isolation
    assert "timeout" in result.isolation
    assert "empty-env" in result.isolation
