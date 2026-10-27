"""Tests for charset decoding and HTML-to-text on a saved JPMorgan 424B2 filing."""
import codecs
import re
from pathlib import Path

from quantproof.ingest.html import (
    Filing,
    ImageRef,
    decode_html,
    detect_charset,
    html_to_text,
    normalise_unicode,
    read_filing,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "filings" / "jpm_424b2-38582.htm"


def _read_raw() -> bytes:
    return FIXTURE.read_bytes()


def test_fixture_charset_is_cp1252():
    raw = _read_raw()
    # Fixture declares windows-1252; canonical Python name is cp1252.
    assert detect_charset(raw) == "cp1252"
    assert read_filing(raw).charset == "cp1252"


def test_fixture_times_phrases():
    filing = read_filing(_read_raw())
    # Exact phrases from the fixture; decoding as UTF-8 would garble the sign.
    assert "at least 9.7500% × $1,000" in filing.text
    assert "at least 12.1875% × $1,000" in filing.text


def test_fixture_identifiers():
    filing = read_filing(_read_raw())
    assert "48136LQH5" in filing.text
    assert "2,341.377" in filing.text
    assert "6,672.41" in filing.text


def test_fixture_no_mojibake_or_markup():
    filing = read_filing(_read_raw())
    # U+FFFD would mean a decode fallback fired; Ã means UTF-8 bytes misread as cp1252.
    assert "\ufffd" not in filing.text
    assert "Ã" not in filing.text
    # No leftover tags: '<' directly followed by a letter.
    assert re.search(r"<[A-Za-z]", filing.text) is None
    # Script variable from the fixture must have been skipped.
    assert "bazadebezolkohpepadr" not in filing.text


def test_fixture_images_exact_order():
    filing = read_filing(_read_raw())
    basenames = [img.src.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] for img in filing.images]
    # Order observed in the saved page: 1, 6, 2, 3, 6x5, 4, 5, 6x4 = 15 local files.
    expected = (
        ["image1.gif", "image6.jpg", "image2.gif", "image3.gif"]
        + ["image6.jpg"] * 5
        + ["image4.jpg", "image5.jpg"]
        + ["image6.jpg"] * 4
    )
    assert basenames == expected
    assert len(filing.images) == 15
    # Tracking pixel skipped: no recorded src is a remote URL.
    assert all(not img.src.lower().startswith(("http://", "https://")) for img in filing.images)


def test_fixture_image_placeholder():
    filing = read_filing(_read_raw())
    # Placeholder uses the file name only, on its own line.
    assert "[image: image2.gif]" in filing.text


def test_fixture_keeps_curly_quotes_and_em_dashes():
    filing = read_filing(_read_raw())
    # Normalisation must not flatten these punctuation marks.
    assert "“" in filing.text
    assert "—" in filing.text


def test_meta_charset_utf8_with_times():
    raw = '<html><head><meta charset="utf-8"></head><body><p>2 × 3</p></body></html>'.encode(
        "utf-8"
    )
    assert detect_charset(raw) == "utf-8"
    text, charset = decode_html(raw)
    assert "×" in text


def test_http_equiv_form():
    raw = b'<meta http-equiv="Content-Type" content="text/html; charset=windows-1252"><p>\xd7</p>'
    # 0xD7 is × in cp1252 and invalid alone in UTF-8.
    assert detect_charset(raw) == "cp1252"
    text, _ = decode_html(raw)
    assert "×" in text


def test_single_quoted_charset():
    raw = b"<meta charset='utf-8'><p>hi</p>"
    assert detect_charset(raw) == "utf-8"


def test_unquoted_charset():
    raw = b"<meta charset=utf-8><p>hi</p>"
    assert detect_charset(raw) == "utf-8"


def test_xml_declaration():
    raw = b'<?xml version="1.0" encoding="UTF-8"?><p>hi</p>'
    assert detect_charset(raw) == "utf-8"


def test_bom_wins():
    raw = codecs.BOM_UTF8 + b'<meta charset="windows-1252"><p>hi</p>'
    # BOM takes precedence over any later declaration.
    assert detect_charset(raw) == "utf-8-sig"


def test_iso8859_label_maps_to_cp1252():
    raw = b'<meta charset="iso-8859-1"><p>\x96</p>'
    # WHATWG maps iso-8859-1 to windows-1252; 0x96 is en dash there.
    assert detect_charset(raw) == "cp1252"
    text, _ = decode_html(raw)
    assert "–" in text


def test_no_declaration_valid_utf8():
    raw = "<p>×</p>".encode("utf-8")
    # Valid strict UTF-8 with no declaration stays UTF-8.
    assert detect_charset(raw) == "utf-8"


def test_no_declaration_lone_0xd7_is_cp1252():
    raw = b"<p>\xd7</p>"
    # Lone 0xD7 is invalid UTF-8, so fallback is cp1252 where it is ×.
    assert detect_charset(raw) == "cp1252"
    text, charset = decode_html(raw)
    assert charset == "cp1252"
    assert "×" in text


def test_unknown_label_falls_back():
    raw = b'<meta charset="x-nonsense"><p>hi</p>'
    # Unknown label is ignored; remainder is valid ASCII so UTF-8.
    assert detect_charset(raw) == "utf-8"


def test_declared_utf8_invalid_byte_falls_back():
    raw = b'<meta charset="utf-8"><p>\xff</p>'
    # Must not raise; backup decodes the byte as cp1252 ÿ.
    text, charset = decode_html(raw)
    assert charset == "cp1252"
    assert "ÿ" in text


def test_table_row_joins_cells_with_pipe():
    html = "<table><tr><td>Initial Value</td><td></td><td>2,341.377</td></tr></table>"
    text, _ = html_to_text(html)
    # Empty middle cell dropped after stripping; rest joined on one line.
    assert "Initial Value | 2,341.377" in text.splitlines()


def test_script_style_dropped_uppercase_and_nested_hidden_blocks():
    html = (
        "<p>keep</p>"
        "<script>evil()</script><STYLE>css evil</STYLE><SCRIPT>UPPER evil</SCRIPT>"
        "<noscript>hidden evil</noscript>"
        # ix:header holds ordinary tags, so nesting is real here; the depth counter must
        # stay in skip mode until the outer one closes.
        "<ix:header><div>evil<ix:header>deeper evil</ix:header>still evil</div></ix:header>"
        "<p>also keep</p>"
    )
    text, _ = html_to_text(html)
    # Blocks may be separated by one blank line; compare the non-empty lines.
    assert [line for line in text.splitlines() if line] == ["keep", "also keep"]


def test_entities_handled():
    text, _ = html_to_text("<p>a&times;b&#215;c&nbsp;d</p>")
    # convert_charrefs gives × twice and NBSP which normalises to a space.
    assert "a×b×c d" in text


def test_zero_width_and_soft_hyphen_removed():
    text, _ = html_to_text("<p>a\u200bb\u00adc</p>")
    # Zero-width space and soft hyphen are invisible in a browser, so they must vanish.
    assert text == "abc"


def test_whitespace_collapsed():
    text, _ = html_to_text("<p>a   b\t\tc</p>")
    # Tabs become spaces then runs collapse to one.
    assert "a b c" in text


def test_paragraphs_on_separate_lines():
    text, _ = html_to_text("<p>one</p><p>two</p>")
    lines = text.splitlines()
    i_one = next(i for i, line in enumerate(lines) if "one" in line)
    i_two = next(i for i, line in enumerate(lines) if "two" in line)
    # Block tags force a newline so the two words never share a line.
    assert i_one != i_two


def test_normalise_unicode_keeps_times_and_dash():
    out = normalise_unicode("a × b—c“d”")
    # Zs spaces become plain spaces; ×, em dash and curly quotes survive.
    assert "×" in out
    assert "—" in out
    assert "“" in out


def test_source_newlines_inside_a_paragraph_are_spaces():
    # Filings wrap long sentences across source lines; a quote must stay on one line.
    text, _ = html_to_text("<p>at least\n    9.7500% \u00d7\r\n$1,000</p>")
    assert text == "at least 9.7500% \u00d7 $1,000"


def test_local_image_recorded_with_alt_and_placeholder():
    text, images = html_to_text('<p>Payoff</p><img src="./x_files/image2.gif" alt="Diagram">'
                                '<img src="https://www.sec.gov/akam/pixel">')
    assert images == (ImageRef(src="./x_files/image2.gif", alt="Diagram"),)
    assert [line for line in text.splitlines() if line] == ["Payoff", "[image: image2.gif]"]


def test_read_filing_returns_filing():
    filing = read_filing(b'<meta charset="utf-8"><p>x</p>')
    assert filing == Filing(text="x", charset="utf-8", images=())


def test_layout_row_keeps_paragraph_lines():
    # Filings lay out pages as tables; a cell holding paragraphs is not a data row.
    html = ("<table><tr><td><p>Issuer: JPMorgan</p><p>Barrier Amount: 70.00%</p></td>"
            "<td>[side note]</td></tr></table>")
    text, _ = html_to_text(html)
    lines = [line for line in text.splitlines() if line]
    assert lines == ["Issuer: JPMorgan", "Barrier Amount: 70.00%", "[side note]"]


def test_fixture_key_terms_are_separate_lines():
    lines = read_filing(_read_raw()).text.splitlines()
    # Each key term starts its own line, so a model (and a reader) sees one term per line.
    assert "Barrier Amount: With respect to each Index, 70.00% of its Initial Value" in lines
    assert any(line.startswith("Pricing Date: On or about November 25, 2025") for line in lines)
