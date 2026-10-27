"""Offline tests for the registry store. No network, no API calls."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quantproof.registry.store import find_for, list_registered, load, register, safe_name
from quantproof.validation.battery import CheckResult

CODE = """
import numpy as np

def cashflows(perf, terms):
    return np.zeros((perf.shape[0], perf.shape[1]))
"""

UNSAFE_CODE = "import os\n" + CODE


def _ts(cusip="123456AB1", features=("Absolute Return",)):
    return SimpleNamespace(
        issuer=SimpleNamespace(value="Test Issuer"),
        cusip=(SimpleNamespace(value=cusip) if cusip is not None else None),
        unsupported_features=[SimpleNamespace(value=f) for f in features],
    )


def _passed_results():
    return [
        CheckResult(
            name="static_safety", passed=True, value=1.0, reference=1.0,
            tolerance=0.0, detail="ok",
        )
    ]


def _failed_results():
    return [
        CheckResult(
            name="filing_examples", passed=False, value=5.0, reference=0.0,
            tolerance=0.01, detail="wrong payment",
        )
    ]


def test_safe_name():
    assert safe_name("JPM Note 2026!") == "jpm_note_2026_"
    assert safe_name("Absolute---Return  Feature") == "absolute_return_feature"
    assert safe_name("already_safe_1") == "already_safe_1"
    assert len(safe_name("a" * 100)) == 60


def test_register_load_round_trip(tmp_path):
    ts = _ts()
    name = register("My Payoff", CODE, ts, _passed_results(), model="muse-wonder-1.0", directory=tmp_path)
    assert (tmp_path / f"{name}.py").read_text(encoding="utf-8") == CODE
    code, metadata = load(name, directory=tmp_path)
    assert code == CODE
    assert metadata["name"] == name
    assert metadata["model"] == "muse-wonder-1.0"
    assert metadata["issuer"] == "Test Issuer"
    assert metadata["cusip"] == "123456AB1"
    assert metadata["unsupported_features"] == ["Absolute Return"]
    assert metadata["passed"] is True
    assert metadata["battery"][0]["name"] == "static_safety"
    assert metadata["battery"][0]["passed"] is True
    assert list_registered(directory=tmp_path) == [name]


def test_register_refuses_failed_results(tmp_path):
    with pytest.raises(ValueError):
        register("payoff", CODE, _ts(), _failed_results(), model="m", directory=tmp_path)
    assert list_registered(directory=tmp_path) == []


def test_register_refuses_static_unsafe_code(tmp_path):
    with pytest.raises(ValueError):
        register("payoff", UNSAFE_CODE, _ts(), _passed_results(), model="m", directory=tmp_path)
    assert list_registered(directory=tmp_path) == []


def test_register_refuses_empty_results(tmp_path):
    with pytest.raises(ValueError):
        register("payoff", CODE, _ts(), [], model="m", directory=tmp_path)


def test_register_no_overwrite(tmp_path):
    register("payoff", CODE, _ts(), _passed_results(), model="m", directory=tmp_path)
    with pytest.raises(ValueError):
        register("payoff", CODE, _ts(), _passed_results(), model="m", directory=tmp_path)


def test_load_tampered_file_raises(tmp_path):
    name = register("payoff", CODE, _ts(), _passed_results(), model="m", directory=tmp_path)
    (tmp_path / f"{name}.py").write_text(CODE + "\n# tampered\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load(name, directory=tmp_path)


def test_find_for_by_cusip(tmp_path):
    register("by_cusip", CODE, _ts(cusip="AAA111", features=("Feature One",)),
             _passed_results(), model="m", directory=tmp_path)
    register("by_features", CODE, _ts(cusip="BBB222", features=("Feature Two",)),
             _passed_results(), model="m", directory=tmp_path)
    found = find_for(_ts(cusip="AAA111", features=("Something Else",)), directory=tmp_path)
    assert found == "by_cusip"


def test_find_for_by_features_casefolded(tmp_path):
    register("by_features", CODE, _ts(cusip="BBB222", features=("Feature Two",)),
             _passed_results(), model="m", directory=tmp_path)
    found = find_for(_ts(cusip="ZZZ999", features=("feature two",)), directory=tmp_path)
    assert found == "by_features"


def test_find_for_no_match_gives_none(tmp_path):
    register("only", CODE, _ts(cusip="BBB222", features=("Feature Two",)),
             _passed_results(), model="m", directory=tmp_path)
    assert find_for(_ts(cusip="ZZZ999", features=("Nope",)), directory=tmp_path) is None


def test_list_registered_empty_dir(tmp_path):
    assert list_registered(directory=tmp_path) == []
    assert list_registered(directory=tmp_path / "missing") == []
