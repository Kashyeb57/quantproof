"""Tests for quantproof/validation/report.py and scripts/validation_report.py.

Offline and deterministic: SVG builders are fed fixed rows, the Markdown
renderer is fed fake CheckResults, and the end-to-end script test runs the
real battery at 4,000 paths with the default seed on saved fixtures.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from quantproof.validation.battery import CheckResult
from quantproof.validation.report import (
    render_markdown,
    svg_convergence,
    svg_decomposition,
    svg_series,
)

LABEL = "label <&>"


def _assert_svg(svg: str) -> None:
    assert svg.startswith("<svg")
    assert "<title>" in svg
    ET.fromstring(svg)  # well-formed XML


def test_svg_convergence_escapes_and_parses() -> None:
    rows = [
        {"paths": 1000, "price": 990.0, "se": 2.0, "closed_form": 992.0},
        {"paths": 4000, "price": 991.0, "se": 1.0, "closed_form": 992.0},
        {"paths": 16000, "price": 992.0, "se": 0.5, "closed_form": 992.0},
    ]
    svg = svg_convergence(rows)
    _assert_svg(svg)
    assert "Monte Carlo convergence" in svg
    assert 'viewBox="0 0 640 360"' in svg
    assert "sans-serif" in svg


def test_svg_decomposition_escapes_and_parses() -> None:
    data = {
        "bond": 980.0,
        "put": -30.0,
        "digital": -10.0,
        "closed_form": 940.0,
        "mc": 941.0,
        "se": 1.5,
    }
    svg = svg_decomposition(data)
    _assert_svg(svg)
    assert "Barrier note = bond - put - digital" in svg
    # Values labelled to 2 decimals.
    assert "980.00" in svg
    assert "-30.00" in svg


def test_svg_series_escapes_label_and_parses() -> None:
    rows = [
        {"rho": 0.0, "price": 900.0, "se": 2.0},
        {"rho": 0.5, "price": 920.0, "se": 1.5},
        {"rho": 0.95, "price": 940.0, "se": 1.0},
    ]
    svg = svg_series(rows, "rho", "price", LABEL, LABEL)
    _assert_svg(svg)
    # The raw label must not appear; the escaped form must.
    assert "<&>" not in svg
    assert "&lt;&amp;&gt;" in svg


def _fake_notes() -> list[dict]:
    passed = CheckResult(
        name="determinism",
        passed=True,
        value=1000.0,
        reference=1000.0,
        tolerance=0.0,
        detail="run twice with the same seed",
        data={},
    )
    quotes = CheckResult(
        name="quotes",
        passed=False,
        value=3.0,
        reference=4.0,
        tolerance=0.0,
        detail="3/4 verified",
        data={"unverified": ["issuer"]},
    )
    return [
        {
            "label": "fake-one",
            "file": "fake-one",
            "termsheet_summary": "Issuer: Example. Underlyings (1): Example Index.",
            "market_summary": "Illustrative inputs, not market data.",
            "results": [passed, quotes],
            "refused": None,
            "charts": {"convergence": "validation/fake-one_convergence.svg"},
        },
        {
            "label": "fake-two",
            "file": "fake-two",
            "termsheet_summary": "",
            "market_summary": "",
            "results": None,
            "refused": "unsupported features: ['daily barrier']",
            "charts": {},
        },
    ]


def test_render_markdown_fake_notes() -> None:
    md = render_markdown(_fake_notes(), generated="2026-01-01")
    assert "# Validation report" in md
    assert "PASS" in md
    assert "FLAGGED" in md
    assert "issuer" in md  # unverified field listed
    assert "unsupported features: ['daily barrier']" in md
    assert "![convergence](validation/fake-one_convergence.svg)" in md
    assert "not a valuation" in md
    assert "scripts/validation_report.py at 2026-01-01" in md
    lowered = md.lower()
    assert "buy" not in lowered
    assert "sell" not in lowered
    assert "overpriced" not in lowered


def test_scripts_validation_report_end_to_end(tmp_path: Path) -> None:
    # Imported here: scripts/ is a namespace package on the repo root.
    from scripts.validation_report import main

    result = main(
        ["--out-dir", str(tmp_path), "--paths", "4000", "--generated", "2026-01-01"]
    )
    assert result in (0, 1)
    report = tmp_path / "VALIDATION.md"
    assert report.exists()
    md = report.read_text(encoding="utf-8")
    assert "# Validation report" in md
    assert "not a valuation" in md
    # At least the convergence charts were written.
    svgs = sorted((tmp_path / "validation").glob("*.svg"))
    assert svgs
    for svg_path in svgs:
        ET.fromstring(svg_path.read_text(encoding="utf-8"))

    # Per-note sections.
    section_38582 = md.split("## jpm_424b2-38582", 1)[1].split("\n## ", 1)[0]
    assert "FAIL" not in section_38582
    refused_section = md.split("## jpm_424b2-ea0306095", 1)[1].split("\n## ", 1)[0]
    assert "Refused:" in refused_section
