"""Trusted child harness for untrusted payoff code.

Run only as a script, for example::

    python -I -B _sandbox_child.py /path/to/temporary-directory

The directory must contain ``payoff.py``, ``perf.npy`` and ``terms.json``.
On success this harness writes ``cash.npy`` and ``isolation.txt`` to the
same directory.  It is not intended to be imported by product code, although
importing it has no side effects so tests can compare its allow-list with
``sandbox.ALLOWED_MODULES``.
"""

from __future__ import annotations

import builtins
import json
import math
import os
import socket
import sys
import types

import numpy as np
import scipy
import scipy.special
import scipy.stats


# Duplicated from sandbox.py because ``python -I`` with cwd set to the
# temporary directory cannot import the quantproof package from here.
ALLOWED_MODULES = {"math", "numpy", "scipy", "scipy.stats", "scipy.special"}

# Duplicated from sandbox.py because ``python -I`` cannot import quantproof here.
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


def _build_proxy(module: types.ModuleType) -> types.SimpleNamespace:
    """Return a read-by-convention proxy holding only safe public attributes."""
    proxy = types.SimpleNamespace()
    for name in dir(module):
        if name.startswith("_"):  # Blocks private/dunder attributes from the proxy.
            continue
        if name in FORBIDDEN_ATTRS:  # Blocks forbidden attributes/subpackages from the proxy.
            continue
        try:
            value = getattr(module, name)
        except Exception:  # Blocks attributes whose lazy access fails or is deprecated away.
            continue
        if isinstance(value, types.ModuleType):  # Blocks real submodules, which can reach os/sys/ctypes.
            continue
        setattr(proxy, name, value)
    return proxy


# Aliases so tests/reviewers can find the one builder under obvious names.
_build_module_proxy = _build_proxy  # Alias for the module-proxy builder above.
_make_proxy = _build_proxy  # Alias for the module-proxy builder above.
build_proxy = _build_proxy  # Alias for the module-proxy builder above.


def _build_scipy_proxy() -> types.SimpleNamespace:
    """Return a SciPy proxy holding only the stats and special proxies."""
    return types.SimpleNamespace(
        stats=_build_proxy(scipy.stats),  # Blocks every SciPy subpackage except stats.
        special=_build_proxy(scipy.special),  # Blocks every SciPy subpackage except special.
    )


_MATH_PROXY = _build_proxy(math)
_NUMPY_PROXY = _build_proxy(np)
_SCIPY_PROXY = _build_scipy_proxy()

_PROXY_MODULES = {
    "math": _MATH_PROXY,
    "numpy": _NUMPY_PROXY,
    "scipy": _SCIPY_PROXY,
    "scipy.stats": _SCIPY_PROXY.stats,
    "scipy.special": _SCIPY_PROXY.special,
}

_ALLOWED_MODULE_OBJECTS = dict(_PROXY_MODULES)

_REAL_IMPORT = builtins.__import__

_PAYOFF_CODE = None
_AUDIT_ACTIVE = False
_AUDIT_HOOK_INSTALLED = False


def _audit_hook(event: str, args) -> None:
    """Raise PermissionError for audit events a payoff must never trigger."""
    if event == "open":  # Blocks all file opens after the hook is installed.
        raise PermissionError("open is not allowed in sandbox")
    if event.startswith("os."):  # Blocks all os.* operations after the hook is installed.
        raise PermissionError(f"{event} is not allowed in sandbox")
    if event.startswith("subprocess."):  # Blocks process creation after the hook is installed.
        raise PermissionError(f"{event} is not allowed in sandbox")
    if event.startswith("socket."):  # Blocks network operations after the hook is installed.
        raise PermissionError(f"{event} is not allowed in sandbox")
    if event.startswith("ctypes."):  # Blocks native-library operations after the hook is installed.
        raise PermissionError(f"{event} is not allowed in sandbox")
    if event.startswith("shutil."):  # Blocks shutil filesystem operations after the hook is installed.
        raise PermissionError(f"{event} is not allowed in sandbox")
    if event == "compile":  # Blocks compiling new code after the payoff is compiled.
        raise PermissionError("compile is not allowed in sandbox")
    if event == "builtins.input":  # Blocks interactive input after the hook is installed.
        raise PermissionError("input is not allowed in sandbox")
    if event == "sys._getframe":  # Blocks frame access, simplified to the public audit spelling.
        raise PermissionError("frame access is not allowed in sandbox")
    if event == "sys.settrace":  # Blocks installing a trace function that can inspect frames.
        raise PermissionError("settrace is not allowed in sandbox")
    if event == "sys.setprofile":  # Blocks installing a profile function that can inspect frames.
        raise PermissionError("setprofile is not allowed in sandbox")
    if event == "object.__getattr__":  # Blocks all object.__getattr__ tricks; no name-based whitelist.
        raise PermissionError("object.__getattr__ is not allowed in sandbox")
    if event == "import":  # Blocks importing any module not already loaded by the trusted harness.
        module_name = args[0] if args and isinstance(args[0], str) else ""
        if module_name not in sys.modules:  # Blocks first-time imports of non-harness modules.
            raise PermissionError(f"import of {module_name!r} is not allowed in sandbox")
        return
    if event == "exec":  # Blocks executing any code object other than the compiled payoff.
        code_object = args[0] if args else None
        if code_object is not _PAYOFF_CODE:  # Blocks exec of dynamically created or other code.
            raise PermissionError("exec of non-payoff code is not allowed in sandbox")
        return


def _blocked_network(*args, **kwargs):
    """Reject every attempt to create or resolve a network connection."""
    raise PermissionError("network disabled in sandbox")  # Blocks all socket/network use.


def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    """Import only pre-imported modules on the payoff allow-list, as proxies."""
    if level != 0:  # Blocks relative imports.
        raise ImportError("relative imports are not allowed in sandbox")
    if name not in ALLOWED_MODULES:  # Blocks imports outside the allow-list.
        raise ImportError(f"import of {name!r} is not allowed in sandbox")
    if not fromlist:  # Returns the top-level proxy for plain ``import a.b`` semantics.
        return _PROXY_MODULES[name.split(".", 1)[0]]
    return _PROXY_MODULES[name]  # Returns the named module's proxy for ``from a.b import ...`` semantics.


def _record_network_isolation(directory: str) -> None:
    """Record netns isolation if present, otherwise socket blocking."""
    try:
        interfaces = socket.if_nameindex()
        interface_names = {name for _, name in interfaces}
    except (OSError, AttributeError):
        interface_names = set()
    # A fresh network namespace normally has no usable interface, or only lo.
    mode = "netns" if interface_names <= {"lo"} else "socket-blocked"
    with open(os.path.join(directory, "isolation.txt"), "w", encoding="utf-8") as handle:
        handle.write(mode + "\n")


def _block_network(directory: str) -> None:
    """Record the isolation mode, then disable the socket entry points."""
    _record_network_isolation(directory)
    socket.socket = _blocked_network  # Blocks creating sockets.
    socket.create_connection = _blocked_network  # Blocks outbound TCP connections.
    socket.getaddrinfo = _blocked_network  # Blocks DNS/name resolution.
    socket.socketpair = _blocked_network  # Blocks socket-pair communication channels.


def _safe_builtins() -> dict:
    """Build the minimal builtin namespace available to payoff code."""
    return {
        "abs": abs,
        "all": all,
        "any": any,
        "bool": bool,
        "dict": dict,
        "divmod": divmod,
        "enumerate": enumerate,
        "filter": filter,
        "float": float,
        "int": int,
        "isinstance": isinstance,
        "len": len,
        "list": list,
        "map": map,
        "max": max,
        "min": min,
        "pow": pow,
        "range": range,
        "reversed": reversed,
        "round": round,
        "set": set,
        "slice": slice,
        "sorted": sorted,
        "str": str,
        "sum": sum,
        "tuple": tuple,
        "zip": zip,
        "ValueError": ValueError,
        "TypeError": TypeError,
        "ZeroDivisionError": ZeroDivisionError,
        "ArithmeticError": ArithmeticError,
        "Exception": Exception,
        "True": True,
        "False": False,
        "None": None,
        "__import__": _guarded_import,  # Blocks imports outside ALLOWED_MODULES and returns proxies.
    }


def main(argv: list[str]) -> int:
    """Execute payoff.py from argv[1] and write cash.npy beside its inputs."""
    global _PAYOFF_CODE, _AUDIT_ACTIVE, _AUDIT_HOOK_INSTALLED
    if len(argv) != 2:  # Blocks running without exactly one sandbox directory.
        print("usage: _sandbox_child.py DIR", file=sys.stderr)
        return 2
    directory = argv[1]

    _block_network(directory)

    with open(os.path.join(directory, "perf.npy"), "rb") as handle:
        perf = np.load(handle, allow_pickle=False)  # Blocks pickle payloads in the performance input.
    with open(os.path.join(directory, "terms.json"), encoding="utf-8") as handle:
        terms = json.load(handle)
    with open(os.path.join(directory, "payoff.py"), encoding="utf-8") as handle:
        code = handle.read()

    with open(os.path.join(directory, "cash.npy"), "wb") as cash_handle:
        payoff_code = compile(code, "payoff.py", "exec")  # Compiles only the payoff before the audit hook exists.
        _PAYOFF_CODE = payoff_code  # Allows the audit hook to recognise the one trusted exec.
        _AUDIT_ACTIVE = True  # Marks the audit hook active; set only in main, never on import.
        sys.addaudithook(_audit_hook)  # Installs an audit hook Python code cannot remove.
        _AUDIT_HOOK_INSTALLED = True  # Records that main installed the audit hook.
        namespace = {"__builtins__": _safe_builtins(), "__name__": "payoff"}
        exec(payoff_code, namespace)  # Executes only the code object whitelisted in _PAYOFF_CODE.
        cashflows = namespace["cashflows"]
        cash = cashflows(perf.copy(), terms)
        np.save(cash_handle, np.asarray(cash), allow_pickle=False)  # Writes to the pre-opened handle; no new open after the hook.
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except SystemExit:
        raise
    except Exception as exc:  # Report payoff and harness failures to the host.
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
