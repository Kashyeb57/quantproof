"""The recorded live extraction of the Day 1 filing, checked against the hand-built term sheet.

`jpm_38582_termsheet` (tests/conftest.py) was typed in by hand from the filing; the recorded
reply is what Muse Spark returned for the same filing. Every value must agree, and every quote
in the reply must be found in the filing text.
"""

from pathlib import Path

import pytest

from quantproof.agent.extract import parse_json_reply, to_termsheet
from quantproof.ingest.html import read_filing
from quantproof.validation.quotes import normalise_for_match, verify_termsheet

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture(scope="module")
def filing_text() -> str:
    return read_filing((FIXTURES / "filings" / "jpm_424b2-38582.htm").read_bytes()).text


@pytest.fixture
def extracted():
    text = (FIXTURES / "extractions" / "jpm_424b2-38582.reply.json").read_text(encoding="utf-8")
    return to_termsheet(parse_json_reply(text))


def comparable(value):
    # Names differ only in typography ("Russell 2000® Index" vs "Russell 2000 Index").
    return normalise_for_match(value) if isinstance(value, str) else value


def test_every_value_matches_the_hand_built_term_sheet(extracted, jpm_38582_termsheet):
    by_hand = dict(jpm_38582_termsheet.cited_fields())
    by_model = dict(extracted.cited_fields())
    assert by_model.keys() == by_hand.keys()  # same fields present, including 17 observations
    mismatches = {p: (by_model[p].value, by_hand[p].value) for p in by_hand
                  if comparable(by_model[p].value) != comparable(by_hand[p].value)}
    assert mismatches == {}
    assert extracted.autocall.amounts_are_minimums is True
    assert extracted.underlyings[0].initial_level is None  # set on the pricing date


def test_every_model_quote_is_in_the_filing(extracted, filing_text):
    checks = verify_termsheet(extracted, filing_text)
    not_found = [(c.path, c.reason) for c in checks if c.reason != "found"]
    # The filing never states a gearing, so the 1.0 default is flagged, not verified.
    assert not_found == [("downside.gearing", "no citation")]
    assert extracted.unverified_fields() == ["downside.gearing"]
