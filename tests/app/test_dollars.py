"""Dollar escaping: Streamlit renders $...$ as LaTeX, eating money signs."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quantproof.app import demo


def test_escape_dollars_leaves_no_unescaped_pair():
    samples = [
        "Base estimated value: $907.72 +- $1.45 (1.96 SE)",
        "Our range: $779.88 - $973.78",
        "Biggest driver: Volatility ($953.98 to $862.91)",
        "$1,000.00",
        "no money here",
        "already \\$5.00 escaped",
    ]
    for sample in samples:
        escaped = demo.escape_dollars(sample)
        # An unescaped pair would be read as a LaTeX formula delimiter.
        assert demo.unescaped_dollar_count(escaped) == 0, sample
        assert "\\$" in escaped or "$" not in sample


def test_escape_dollars_is_idempotent():
    once = demo.escape_dollars("$10.00 and $20.00")
    assert demo.escape_dollars(once) == once
