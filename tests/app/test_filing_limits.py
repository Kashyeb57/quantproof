"""Size and form limits for live filings: refusals spend nothing."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quantproof.app import live

FIXTURE = ROOT / "tests" / "fixtures" / "filings" / "jpm_424b2-38582.htm"


def _filing(text: str) -> SimpleNamespace:
    return SimpleNamespace(text=text)


def test_config_toml_caps_upload_size():
    config = (ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    assert "maxUploadSize = 2" in config


def test_max_filing_chars_env_and_default():
    assert live.max_filing_chars({}) == 400_000
    assert live.max_filing_chars({"QP_MAX_FILING_CHARS": "1000"}) == 1000
    assert live.max_filing_chars({"QP_MAX_FILING_CHARS": "junk"}) == 400_000


def test_saved_fixture_is_admissible():
    filing = live.filing_from_upload(FIXTURE.read_bytes())
    live.check_filing_admissible(filing, max_chars=400_000)  # no refusal


def test_oversize_filing_is_refused_with_a_clear_message():
    filing = _filing("424B2 PRICING SUPPLEMENT " + "x" * 5000)
    with pytest.raises(live.LiveRefused, match="over the 1,000-character limit"):
        live.check_filing_admissible(filing, max_chars=1000)


def test_non_424b2_document_is_refused_with_a_clear_message():
    filing = _filing("10-K ANNUAL REPORT " + "x" * 100)
    with pytest.raises(live.LiveRefused, match="424B2 pricing supplement"):
        live.check_filing_admissible(filing, max_chars=400_000)


def test_form_check_needs_both_header_markers():
    assert live.is_pricing_supplement("424B2 filing PRICING SUPPLEMENT ...")
    assert not live.is_pricing_supplement("424B2 amendment only")
    assert not live.is_pricing_supplement("PRICING SUPPLEMENT without form")
