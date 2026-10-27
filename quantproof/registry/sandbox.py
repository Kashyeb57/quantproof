"""Static checks and subprocess runner for untrusted agent-written payoffs.

The payoff contract is ``cashflows(perf, terms)`` where ``perf`` has shape
``(n_paths, n_dates, n_assets)`` and the returned cash array has shape
``(n_paths, n_dates)``.  This module never executes agent code in the host
process: code that passes the static check is written to a temporary
directory and executed by ``_sandbox_child.py`` in a subprocess.

Pure Python + NumPy in the host-side validation.  The child harness uses
only the standard library plus the explicitly allowed NumPy/SciPy modules.
"""

from __future__ import annotations

import ast
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

try:
    import resource  # Blocks excessive resource use on POSIX/Linux.
except ImportError:  # pragma: no cover - resource is unavailable on Windows.
    resource = None  # type: ignore[assignment]


ALLOWED_MODULES = {"math", "numpy", "scipy", "scipy.stats", "scipy.special"}

FORBIDDEN_NAMES = {
    "open",  # Blocks direct file access.
    "exec",  # Blocks executing dynamically supplied code.
    "eval",  # Blocks evaluating dynamically supplied expressions.
    "compile",  # Blocks compiling dynamically supplied code.
    "__import__",  # Blocks bypassing the import allow-list.
    "globals",  # Blocks access to the global namespace.
    "locals",  # Blocks access to local namespaces.
    "vars",  # Blocks access to object namespaces.
    "input",  # Blocks interactive input.
    "breakpoint",  # Blocks entering a debugger.
    "getattr",  # Blocks constructing forbidden attribute names dynamically.
    "setattr",  # Blocks modifying objects and modules.
    "delattr",  # Blocks deleting object or module attributes.
    "help",  # Blocks the interactive help escape hatch.
    "memoryview",  # Blocks raw memory access.
    "exit",  # Blocks interpreter-control builtins.
    "quit",  # Blocks interpreter-control builtins.
    "type",  # Blocks dynamic class construction and type introspection.
    "object",  # Blocks direct use of the root type and object tricks.
    "super",  # Blocks method-resolution/object introspection tricks.
    "classmethod",  # Blocks class-construction descriptors.
    "staticmethod",  # Blocks class-construction descriptors.
    "property",  # Blocks class-construction descriptors.
}

FORBIDDEN_ATTRS = {
    "load",  # Blocks NumPy/SciPy data loading, including pickle-based loading.
    "loads",  # Blocks deserialising supplied data.
    "save",  # Blocks NumPy file output.
    "savez",  # Blocks NumPy archive output.
    "savez_compressed",  # Blocks NumPy compressed archive output.
    "savetxt",  # Blocks NumPy text-file output.
    "loadtxt",  # Blocks NumPy text-file input.
    "fromfile",  # Blocks reading raw files into arrays.
    "tofile",  # Blocks writing arrays directly to files.
    "genfromtxt",  # Blocks NumPy text-file input.
    "fromregex",  # Blocks constructing arrays from file contents.
    "memmap",  # Blocks memory-mapped file access.
    "DataSource",  # Blocks NumPy file/URL data sources.
    "lib",  # Blocks access to NumPy internals.
    "ctypeslib",  # Blocks loading or calling native libraries through ctypes helpers.
    "ctypes",  # Blocks native-library and raw-memory access.
    "f2py",  # Blocks compiled-extension tooling.
    "testing",  # Blocks test-framework internals not needed by a payoff.
    "distutils",  # Blocks build/tooling internals.
    "io",  # Blocks I/O subpackages such as scipy.io.
    "os",  # Blocks operating-system access through a module attribute.
    "sys",  # Blocks interpreter access through a module attribute.
    "system",  # Blocks shell command execution.
    "popen",  # Blocks process creation.
    "dump",  # Blocks serialising data to files or streams.
    "dumps",  # Blocks serialising data for escape into another interpreter.
    "frombuffer",  # Blocks constructing arrays from raw buffers.
    "set_printoptions",  # Blocks mutating global NumPy state.
    "show_config",  # Blocks exposing build/configuration details.
    "__getattribute__",  # Blocks object-introspection escape hatches.
    "gi_frame",  # Blocks generator-frame access leading to harness globals.
    "gi_code",  # Blocks generator code-object introspection.
    "gi_yieldfrom",  # Blocks delegated-generator introspection.
    "cr_frame",  # Blocks coroutine-frame access leading to harness globals.
    "cr_code",  # Blocks coroutine code-object introspection.
    "cr_await",  # Blocks coroutine await-chain introspection.
    "ag_frame",  # Blocks async-generator-frame access leading to harness globals.
    "ag_code",  # Blocks async-generator code-object introspection.
    "f_globals",  # Blocks frame-global namespace access.
    "f_locals",  # Blocks frame-local namespace access.
    "f_builtins",  # Blocks frame-builtin namespace access.
    "f_back",  # Blocks walking frame stacks toward the harness.
    "f_code",  # Blocks frame code-object introspection.
    "f_trace",  # Blocks frame tracing-function access and mutation.
    "tb_frame",  # Blocks traceback-frame access leading to harness globals.
    "tb_next",  # Blocks walking traceback chains toward the harness.
    "co_code",  # Blocks code-object bytecode introspection.
    "co_consts",  # Blocks code-object constant introspection.
    "format",  # Blocks dynamic string formatting used to build forbidden names.
    "format_map",  # Blocks mapping-based dynamic string formatting.
    "builtins",  # Blocks access to the real builtin namespace through a module.
    "importlib",  # Blocks import machinery access through a module.
    "subprocess",  # Blocks process creation through a module attribute.
    "shutil",  # Blocks filesystem utilities through a module attribute.
    "pathlib",  # Blocks filesystem paths through a module attribute.
    "pickle",  # Blocks pickle (de)serialisation through a module attribute.
    "socket",  # Blocks network access through a module attribute.
    "inspect",  # Blocks source/frame introspection through a module attribute.
    "info",  # Blocks NumPy informational helpers that can expose internals.
    "source",  # Blocks source-code introspection helpers such as np.source.
    "lookfor",  # Blocks help/search helpers that can expose internals.
    "random",  # Blocks random subpackages whose attributes can reach other modules.
    "char",  # Blocks NumPy char subpackage, which re-exports unrelated namespaces.
    "rec",  # Blocks NumPy record-array subpackage not needed by a payoff.
    "ma",  # Blocks NumPy masked-array subpackage not needed by a payoff.
    "polynomial",  # Blocks NumPy polynomial subpackage not needed by a payoff.
    "emath",  # Blocks NumPy extended-math subpackage not needed by a payoff.
    "linalg",  # Blocks linear-algebra subpackage attributes that can reach modules.
    "fft",  # Blocks FFT subpackage attributes not needed by a payoff.
    "typing",  # Blocks typing subpackage introspection not needed by a payoff.
    "exceptions",  # Blocks exception subpackage introspection.
    "dtypes",  # Blocks dtype subpackage introspection not needed by a payoff.
    "version",  # Blocks version subpackage/object introspection.
    "warnings",  # Blocks warnings-module access through a module attribute.
    "functools",  # Blocks functools access re-exported by subpackages such as np.char.
}


@dataclass
class StaticReport:
    """Result of the source-code check performed before any execution."""

    ok: bool
    problems: list[str] = field(default_factory=list)


@dataclass
class SandboxResult:
    """Result returned by :func:`run_payoff`, whether execution ran or not."""

    ok: bool
    cash: np.ndarray | None
    error: str | None
    seconds: float
    isolation: list[str] = field(default_factory=list)


class SandboxError(RuntimeError):
    """Raised for internal sandbox errors when a result cannot be produced."""


def static_check(code: str, max_chars: int = 20_000) -> StaticReport:
    """Check untrusted payoff source without executing it.

    The check is deliberately syntactic and conservative.  It permits the
    small language needed to calculate an array of cashflows and rejects
    imports, dynamic execution, object introspection, file access, classes,
    generators, asynchronous code, and private/dunder names.
    """
    problems: list[str] = []

    if not isinstance(code, str):  # Blocks non-text code objects.
        return StaticReport(ok=False, problems=["code must be a string"])
    if len(code) > max_chars:  # Blocks oversized generated programs.
        problems.append(f"code is longer than max_chars={max_chars}")
    if max_chars < 0:  # Blocks a nonsensical size limit.
        problems.append("max_chars must be non-negative")

    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        problems.append(f"syntax error: {exc.msg} at line {exc.lineno}")
        return StaticReport(ok=False, problems=problems)

    # The first statement may be a module docstring.
    body = list(tree.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]

    cashflows_defs: list[ast.FunctionDef] = []
    for statement in body:
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            continue  # Import destinations are validated in the full walk below.
        if isinstance(statement, ast.FunctionDef):
            if statement.name == "cashflows":
                cashflows_defs.append(statement)
            continue  # Helper functions are allowed at top level.
        if isinstance(statement, ast.Assign):
            if len(statement.targets) != 1 or not isinstance(
                statement.targets[0], ast.Name
            ):
                problems.append("top-level assignments must assign one simple name")
                continue
            value = statement.value
            if isinstance(value, ast.Constant):
                continue  # Simple scalar constants are allowed at top level.
            if isinstance(value, ast.Tuple) and all(
                isinstance(item, ast.Constant) for item in value.elts
            ):
                continue  # Simple tuples of constants are allowed at top level.
            problems.append(
                "top-level assignments must be a constant or tuple of constants"
            )
            continue
        problems.append(
            f"top-level {type(statement).__name__} statements are not allowed"
        )

    if len(cashflows_defs) != 1:
        problems.append(
            f"exactly one top-level def named cashflows is required, found {len(cashflows_defs)}"
        )
    else:
        cashflows_def = cashflows_defs[0]
        positional_args = list(cashflows_def.args.posonlyargs) + list(
            cashflows_def.args.args
        )
        if len(positional_args) != 2:  # Blocks a payoff with the wrong call signature.
            problems.append("cashflows must have exactly two positional arguments")
        if cashflows_def.args.vararg is not None:  # Blocks hiding extra inputs in *args.
            problems.append("cashflows must not use *args")
        if cashflows_def.args.kwarg is not None:  # Blocks hiding extra inputs in **kwargs.
            problems.append("cashflows must not use **kwargs")
        if cashflows_def.args.kwonlyargs:  # Blocks extra keyword-only payoff inputs.
            problems.append("cashflows must not use keyword-only arguments")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in ALLOWED_MODULES:  # Blocks imports outside the allow-list.
                    problems.append(f"import of {alias.name!r} is not allowed")
                if alias.asname is not None and alias.asname.startswith("_"):  # Blocks private import aliases.
                    problems.append("names starting with '_' are not allowed")
                if alias.asname is not None and alias.asname in FORBIDDEN_ATTRS:  # Blocks rebinding an import to a forbidden attribute name.
                    problems.append(f"import alias {alias.asname!r} is forbidden")
                if alias.asname is not None and alias.asname in FORBIDDEN_NAMES:  # Blocks rebinding an import to a forbidden builtin name.
                    problems.append(f"import alias {alias.asname!r} is forbidden")
        elif isinstance(node, ast.ImportFrom):
            if node.level != 0:  # Blocks relative imports outside the allow-list.
                problems.append("relative imports are not allowed")
            if node.module is None:  # Blocks an unresolvable relative import.
                problems.append("import without a module name is not allowed")
            elif node.module == "scipy":
                for alias in node.names:
                    if alias.name == "*":  # Blocks wildcard imports hiding names from the checker.
                        problems.append("wildcard imports are not allowed")
                    elif alias.name not in {"stats", "special"}:  # Blocks other SciPy subpackages such as scipy.io.
                        problems.append(f"from scipy import {alias.name} is not allowed")
                    if alias.name.startswith("_"):  # Blocks importing private names from SciPy.
                        problems.append("names starting with '_' are not allowed")
                    if alias.name in FORBIDDEN_ATTRS:  # Blocks forbidden SciPy names such as io even via from-import.
                        problems.append(f"imported name {alias.name!r} is forbidden")
                    if alias.name in FORBIDDEN_NAMES:  # Blocks forbidden builtin names even via a SciPy from-import.
                        problems.append(f"imported name {alias.name!r} is forbidden")
                    if alias.asname is not None and alias.asname.startswith("_"):  # Blocks private import aliases.
                        problems.append("names starting with '_' are not allowed")
                    if alias.asname is not None and alias.asname in FORBIDDEN_ATTRS:  # Blocks rebinding a SciPy import to a forbidden attribute name.
                        problems.append(f"import alias {alias.asname!r} is forbidden")
                    if alias.asname is not None and alias.asname in FORBIDDEN_NAMES:  # Blocks rebinding a SciPy import to a forbidden builtin name.
                        problems.append(f"import alias {alias.asname!r} is forbidden")
            elif node.module not in ALLOWED_MODULES:  # Blocks from-imports outside the allow-list.
                problems.append(f"import from {node.module!r} is not allowed")
            else:
                for alias in node.names:
                    if alias.name == "*":  # Blocks wildcard imports hiding names from the checker.
                        problems.append("wildcard imports are not allowed")
                    if alias.name.startswith("_"):  # Blocks importing private names.
                        problems.append("names starting with '_' are not allowed")
                    if alias.name in FORBIDDEN_ATTRS:  # Blocks importing forbidden names such as load or lib directly, not only as attributes.
                        problems.append(f"imported name {alias.name!r} is forbidden")
                    if alias.name in FORBIDDEN_NAMES:  # Blocks importing forbidden builtin names directly, not only as bare names.
                        problems.append(f"imported name {alias.name!r} is forbidden")
                    if alias.asname is not None and alias.asname.startswith("_"):  # Blocks private import aliases.
                        problems.append("names starting with '_' are not allowed")
                    if alias.asname is not None and alias.asname in FORBIDDEN_ATTRS:  # Blocks rebinding an import to a forbidden attribute name.
                        problems.append(f"import alias {alias.asname!r} is forbidden")
                    if alias.asname is not None and alias.asname in FORBIDDEN_NAMES:  # Blocks rebinding an import to a forbidden builtin name.
                        problems.append(f"import alias {alias.asname!r} is forbidden")
        elif isinstance(node, ast.ClassDef):
            problems.append("class definitions are not allowed")  # Blocks class/object construction tricks.
        elif isinstance(node, ast.AsyncFunctionDef):
            problems.append("async function definitions are not allowed")  # Blocks asynchronous execution.
            if node.name.startswith("_"):  # Blocks private/dunder function names.
                problems.append("names starting with '_' are not allowed")
        elif isinstance(node, ast.Await):
            problems.append("await is not allowed")  # Blocks asynchronous execution.
        elif isinstance(node, (ast.Yield, ast.YieldFrom)):
            problems.append("yield is not allowed")  # Blocks generators and lazy deferred execution.
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            problems.append("global/nonlocal statements are not allowed")  # Blocks mutation of outer namespaces.
        elif isinstance(node, ast.FunctionDef):
            if node.name.startswith("_"):  # Blocks private/dunder function names.
                problems.append("names starting with '_' are not allowed")
        elif isinstance(node, ast.Name):
            if node.id.startswith("_"):  # Blocks private and dunder name access.
                problems.append(f"name {node.id!r} starting with '_' is not allowed")
            if node.id in FORBIDDEN_NAMES:  # Blocks dangerous or introspective builtins.
                problems.append(f"name {node.id!r} is forbidden")
        elif isinstance(node, ast.arg):
            if node.arg.startswith("_"):  # Blocks private and dunder argument names.
                problems.append("argument names starting with '_' are not allowed")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):  # Blocks private and dunder attribute access.
                problems.append(
                    f"attribute {node.attr!r} starting with '_' is not allowed"
                )
            if node.attr in FORBIDDEN_ATTRS:  # Blocks I/O, native-code, frame, and module escape hatches.
                problems.append(f"attribute {node.attr!r} is forbidden")
        elif isinstance(node, ast.Constant):
            if isinstance(node.value, str) and "__" in node.value:  # Blocks constructing dunder names in strings.
                problems.append("string constants containing '__' are not allowed")

    return StaticReport(ok=not problems, problems=problems)


def run_payoff(
    code: str,
    perf: np.ndarray,
    terms: dict,
    *,
    timeout: float = 20.0,
    memory_mb: int = 2048,
) -> SandboxResult:
    """Run an untrusted payoff in a restricted subprocess.

    ``perf`` must have shape ``(n_paths, n_dates, n_assets)``.  A successful
    result contains a finite ``float64`` cash array of shape
    ``(n_paths, n_dates)``.  Failures are returned, not raised, except for
    unexpected host-side programming errors.
    """
    started = time.perf_counter()

    def elapsed() -> float:
        return float(time.perf_counter() - started)

    report = static_check(code)
    if not report.ok:
        return SandboxResult(
            ok=False,
            cash=None,
            error="static check: " + "; ".join(report.problems),
            seconds=elapsed(),
            isolation=[],
        )

    try:
        perf_array = np.asarray(perf)
    except Exception as exc:  # Blocks malformed host inputs from reaching the child.
        return SandboxResult(
            ok=False,
            cash=None,
            error=f"invalid perf array: {type(exc).__name__}: {exc}",
            seconds=elapsed(),
            isolation=[],
        )
    if perf_array.ndim != 3:  # Blocks inputs that do not match the payoff contract.
        return SandboxResult(
            ok=False,
            cash=None,
            error="perf must have shape (n_paths, n_dates, n_assets)",
            seconds=elapsed(),
            isolation=[],
        )
    if perf_array.dtype.kind not in "fiu":  # Blocks object/complex performance arrays.
        return SandboxResult(
            ok=False,
            cash=None,
            error="perf must have a real numeric dtype",
            seconds=elapsed(),
            isolation=[],
        )
    perf_array = np.asarray(perf_array, dtype=np.float64)
    n_paths, n_dates = int(perf_array.shape[0]), int(perf_array.shape[1])

    if timeout <= 0:  # Blocks a non-positive execution deadline.
        return SandboxResult(
            ok=False,
            cash=None,
            error="timeout must be positive",
            seconds=elapsed(),
            isolation=[],
        )
    if memory_mb <= 0:  # Blocks a non-positive memory limit.
        return SandboxResult(
            ok=False,
            cash=None,
            error="memory_mb must be positive",
            seconds=elapsed(),
            isolation=[],
        )

    child_path = Path(__file__).with_name("_sandbox_child.py")
    if not child_path.is_file():  # Blocks running without the trusted child harness.
        raise SandboxError(f"sandbox child harness not found: {child_path}")

    is_linux = sys.platform.startswith("linux")

    def _limits() -> None:
        """Apply POSIX limits in the child between fork and exec."""
        if resource is not None:
            try:
                resource.setrlimit(
                    resource.RLIMIT_AS,
                    (int(memory_mb) * 1024 * 1024, int(memory_mb) * 1024 * 1024),
                )  # Blocks excessive virtual-memory allocation.
            except (OSError, ValueError):
                pass
            try:
                cpu_seconds = int(math.ceil(timeout)) + 1
                resource.setrlimit(
                    resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds)
                )  # Blocks CPU use beyond the wall-clock timeout.
            except (OSError, ValueError):
                pass
            try:
                max_file_bytes = int(4 * perf_array.nbytes + 64 * 1024 * 1024)
                resource.setrlimit(
                    resource.RLIMIT_FSIZE, (max_file_bytes, max_file_bytes)
                )  # Blocks writing unreasonably large output files.
            except (OSError, ValueError):
                pass
            if hasattr(resource, "RLIMIT_NPROC"):
                try:
                    resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))  # Blocks creating further processes.
                except (OSError, ValueError):
                    pass
        if hasattr(os, "unshare") and hasattr(os, "CLONE_NEWUSER") and hasattr(
            os, "CLONE_NEWNET"
        ):
            try:
                os.unshare(os.CLONE_NEWUSER | os.CLONE_NEWNET)  # Blocks network access with a private network namespace.
            except OSError:
                pass

    isolation: list[str] = ["subprocess", "timeout"]
    if is_linux and resource is not None:
        isolation.append("rlimits")
    isolation.append("empty-env")

    try:
        with tempfile.TemporaryDirectory(prefix="quantproof-sandbox-") as tmp:
            tmpdir = Path(tmp)
            (tmpdir / "payoff.py").write_text(code, encoding="utf-8")
            with (tmpdir / "perf.npy").open("wb") as handle:
                np.save(handle, perf_array, allow_pickle=False)  # Blocks pickle payloads in the performance input.
            (tmpdir / "terms.json").write_text(
                json.dumps(terms), encoding="utf-8"
            )

            try:
                completed = subprocess.run(
                    [sys.executable, "-I", "-B", str(child_path), str(tmpdir)],
                    cwd=str(tmpdir),
                    env={
                        "PATH": "/usr/bin:/bin",
                        "OMP_NUM_THREADS": "1",
                        "OPENBLAS_NUM_THREADS": "1",
                    },  # Blocks inherited secrets, proxies, and configuration.
                    stdin=subprocess.DEVNULL,  # Blocks interactive input to the child.
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=timeout,
                    preexec_fn=_limits if is_linux else None,  # Blocks excess resources/network on Linux.
                )
            except subprocess.TimeoutExpired:
                return SandboxResult(
                    ok=False,
                    cash=None,
                    error=f"timeout after {timeout} s",
                    seconds=elapsed(),
                    isolation=isolation,
                )

            isolation_path = tmpdir / "isolation.txt"
            if isolation_path.is_file():
                child_isolation = isolation_path.read_text(
                    encoding="utf-8", errors="replace"
                ).strip()
                if child_isolation in {"netns", "socket-blocked"}:
                    isolation.append(child_isolation)

            if completed.returncode != 0:
                stderr = completed.stderr or ""
                return SandboxResult(
                    ok=False,
                    cash=None,
                    error=stderr[-2000:] if stderr else f"child exited with code {completed.returncode}",
                    seconds=elapsed(),
                    isolation=isolation,
                )

            cash_path = tmpdir / "cash.npy"
            if not cash_path.is_file():  # Blocks a child that exits without producing cashflows.
                return SandboxResult(
                    ok=False,
                    cash=None,
                    error="child did not produce cash.npy",
                    seconds=elapsed(),
                    isolation=isolation,
                )
            try:
                with cash_path.open("rb") as handle:
                    cash = np.load(handle, allow_pickle=False)  # Blocks pickle payloads in the child output.
            except Exception as exc:
                return SandboxResult(
                    ok=False,
                    cash=None,
                    error=f"could not load cash.npy: {type(exc).__name__}: {exc}",
                    seconds=elapsed(),
                    isolation=isolation,
                )
    except SandboxError:
        raise
    except Exception as exc:
        return SandboxResult(
            ok=False,
            cash=None,
            error=f"sandbox failure: {type(exc).__name__}: {exc}",
            seconds=elapsed(),
            isolation=isolation,
        )

    if not isinstance(cash, np.ndarray):  # Blocks non-array child output.
        return SandboxResult(
            ok=False,
            cash=None,
            error="cash output is not an ndarray",
            seconds=elapsed(),
            isolation=isolation,
        )
    if cash.dtype == np.bool_ or cash.dtype.kind in "iu":
        cash = cash.astype(np.float64)  # Casts legitimate integer/bool output to the contract dtype.
    elif cash.dtype.kind == "f":
        cash = cash.astype(np.float64, copy=False)
    else:  # Blocks string, object, complex, and other non-real output.
        return SandboxResult(
            ok=False,
            cash=None,
            error=f"cash dtype {cash.dtype} is not a real numeric dtype",
            seconds=elapsed(),
            isolation=isolation,
        )
    if cash.shape != (n_paths, n_dates):  # Blocks cashflows on the wrong paths or dates.
        return SandboxResult(
            ok=False,
            cash=None,
            error=f"cash shape {cash.shape} != expected {(n_paths, n_dates)}",
            seconds=elapsed(),
            isolation=isolation,
        )
    if not np.all(np.isfinite(cash)):  # Blocks NaN and infinite cashflows.
        return SandboxResult(
            ok=False,
            cash=None,
            error="cash contains non-finite values",
            seconds=elapsed(),
            isolation=isolation,
        )

    return SandboxResult(
        ok=True,
        cash=cash,
        error=None,
        seconds=elapsed(),
        isolation=isolation,
    )
