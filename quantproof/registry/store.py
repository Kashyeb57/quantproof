"""Registry store for trusted, battery-proved synthesized payoffs.

Agent-written code enters the registry only after passing the full proof
battery (project rule 4): :func:`register` refuses anything whose battery
did not pass in full or whose static sandbox check fails.  Registered code
is still untrusted at run time -- it is only ever run through the sandbox
(``registry.sandbox.run_payoff``), never imported or executed here.  This
module only writes, reads and indexes the stored files, and checks the
stored SHA-256 on load so a tampered payoff file is refused.

Standard library + quantproof only.  No AI and no network calls.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path

DEFAULT_DIR = Path(__file__).parent / "payoffs"


def safe_name(text) -> str:
    """Filesystem-safe registry name: lowercase, [a-z0-9_] only, max 60 chars.

    Every run of characters outside [a-z0-9_] collapses to a single "_".
    """
    name = re.sub(r"[^a-z0-9_]+", "_", str(text).lower())
    return name[:60]


def register(name, code, ts, results, *, model, directory=DEFAULT_DIR) -> str:
    """Register battery-proved payoff ``code`` under ``name``; return the name used.

    Refuses (ValueError) unless every battery result passed
    (``battery_passed``) and the sandbox static check is ok, and refuses to
    overwrite an existing name.  Writes ``<name>.py`` (the code verbatim)
    and ``<name>.json`` metadata with the code's SHA-256, the model, today's
    date, issuer, CUSIP (or null), the unsupported features, and the battery
    results.  The code is stored, never executed.
    """
    # Lazy imports avoid an import cycle: validation.synth_battery imports
    # payoffs.synthesized, which imports quantproof.registry.sandbox.
    from quantproof.registry.sandbox import static_check
    from quantproof.validation.synth_battery import battery_passed

    if not battery_passed(list(results)):
        raise ValueError("refusing to register: the proof battery did not pass in full")
    report = static_check(code)
    if not report.ok:
        raise ValueError("refusing to register: static check failed: " + "; ".join(report.problems))

    stored_name = safe_name(name)
    if not stored_name.strip("_"):
        raise ValueError(f"refusing to register: name {name!r} gives an empty safe name")

    directory = Path(directory)
    py_path = directory / f"{stored_name}.py"
    json_path = directory / f"{stored_name}.json"
    if py_path.exists() or json_path.exists():
        raise ValueError(f"refusing to register: {stored_name!r} already exists")

    directory.mkdir(parents=True, exist_ok=True)
    metadata = {
        "name": stored_name,
        "sha256": hashlib.sha256(code.encode("utf-8")).hexdigest(),
        "model": model,
        "created": date.today().isoformat(),
        "issuer": ts.issuer.value,
        "cusip": (ts.cusip.value if getattr(ts, "cusip", None) is not None else None),
        "unsupported_features": [str(feature.value) for feature in ts.unsupported_features],
        "battery": [
            {
                "name": r.name,
                "passed": bool(r.passed),
                "value": r.value,
                "reference": r.reference,
                "tolerance": r.tolerance,
                "detail": r.detail,
            }
            for r in results
        ],
        "passed": True,
    }
    py_path.write_text(code, encoding="utf-8")
    json_path.write_text(json.dumps(metadata, indent=1), encoding="utf-8")
    return stored_name


def load(name, directory=DEFAULT_DIR) -> tuple[str, dict]:
    """Load registered payoff ``(code, metadata)`` with a tamper check.

    Raises ValueError when the files are missing or unreadable, when the
    SHA-256 of the stored ``.py`` differs from the metadata (the file was
    tampered with after registration), or when the metadata does not record
    ``"passed": true``.  The returned code is still only ever run through
    the sandbox.
    """
    stored_name = safe_name(name)
    directory = Path(directory)
    py_path = directory / f"{stored_name}.py"
    json_path = directory / f"{stored_name}.json"
    try:
        code = py_path.read_text(encoding="utf-8")
        metadata = json.loads(json_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"registered payoff not found: {stored_name!r}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"registered payoff {stored_name!r} is unreadable: {exc}") from exc
    if not isinstance(metadata, dict):
        raise ValueError(f"registered payoff {stored_name!r}: metadata is not an object")
    digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
    if metadata.get("sha256") != digest:
        raise ValueError(
            f"registered payoff {stored_name!r}: sha256 mismatch "
            "(file tampered with after registration)"
        )
    if metadata.get("passed") is not True:
        raise ValueError(f"registered payoff {stored_name!r}: metadata does not record a pass")
    return code, metadata


def find_for(ts, directory=DEFAULT_DIR) -> str | None:
    """Registered payoff name for ``ts``, by CUSIP first, then by features.

    A payoff whose stored CUSIP equals ``ts.cusip.value`` wins; otherwise a
    payoff whose sorted, casefolded unsupported_features equal the term
    sheet's.  Returns None when nothing matches.  Entries that fail
    :func:`load` (for example tampered files) are skipped.
    """
    names = list_registered(directory=directory)
    metas: dict[str, dict] = {}
    for name in names:
        try:
            _code, metadata = load(name, directory=directory)
        except ValueError:
            continue
        metas[name] = metadata

    cusip = ts.cusip.value if getattr(ts, "cusip", None) is not None else None
    if cusip is not None:
        for name in names:
            metadata = metas.get(name)
            if metadata is not None and metadata.get("cusip") == cusip:
                return name

    wanted = sorted(str(f.value).casefold() for f in ts.unsupported_features)
    for name in names:
        metadata = metas.get(name)
        if metadata is None:
            continue
        stored = metadata.get("unsupported_features") or []
        if sorted(str(f).casefold() for f in stored) == wanted:
            return name
    return None


def list_registered(directory=DEFAULT_DIR) -> list[str]:
    """Sorted names of registered payoffs (``*.py`` files) in ``directory``."""
    directory = Path(directory)
    if not directory.is_dir():
        return []
    return sorted(p.stem for p in directory.glob("*.py") if p.is_file())
