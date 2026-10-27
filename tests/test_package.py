import importlib

import quantproof

SUBPACKAGES = ["core", "ingest", "models", "payoffs", "valuation", "risk",
               "validation", "registry", "agent", "app"]


def test_version_is_set():
    assert quantproof.__version__ == "0.1.0"


def test_all_subpackages_import():
    for name in SUBPACKAGES:
        importlib.import_module(f"quantproof.{name}")
