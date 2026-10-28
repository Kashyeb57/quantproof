"""Turn saved SEC filing HTML bytes into clean text for LLM reading and quote verification.

Decoding follows the filing's own charset declaration (EDGAR filings are often windows-1252,
where byte 0xD7 is "×"); HTML whitespace is collapsed the way a browser renders it, and block
tags and table rows become line breaks.
"""

import codecs
import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser


_SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "ix:header"})
_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "br",
        "tr",
        "li",
        "ul",
        "ol",
        "table",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "section",
        "article",
        "blockquote",
        "pre",
        "hr",
        "document",
        "title",
        "center",
        "dt",
        "dd",
        "caption",
    }
)

_META_CHARSET_RE = re.compile(
    r'<meta\s[^>]*?charset\s*=\s*(?:"([^"]+)"|\'([^\']+)\'|([^\s"\'`>/;]+))',
    re.IGNORECASE,
)
_XML_ENCODING_RE = re.compile(
    r'<\?xml\s[^>]*?encoding\s*=\s*(?:"([^"]+)"|\'([^\']+)\'|([^\s"\'`>/?;]+))',
    re.IGNORECASE,
)

# Zero-width space, non-joiner, joiner, word joiner, BOM / zero-width no-break space, soft hyphen.
_DELETE_CHARS = frozenset({"\u200b", "\u200c", "\u200d", "\u2060", "\ufeff", "\u00ad"})
# HTML whitespace (not NBSP, which is content and is turned into a space by normalise_unicode).
_HTML_SPACE_RE = re.compile(r"[ \t\n\r\f]+")


def _canonical_label(label: str) -> str | None:
    """Return canonical codec name for a declared label, or None if unknown."""
    try:
        name = codecs.lookup(label.strip()).name
    except LookupError:
        return None
    if name in ("iso8859-1", "ascii"):
        # Browsers treat these labels as windows-1252 per WHATWG Encoding Standard.
        return codecs.lookup("windows-1252").name
    return name


def detect_charset(raw: bytes) -> str:
    """Detect the charset of raw filing HTML bytes.

    Order: UTF-8 BOM, then meta/xml declaration in first 4096 bytes,
    then strict UTF-8 trial, else windows-1252. Unknown labels fall
    through to the no-declaration rule. Returns a canonical codec name.
    """
    if raw.startswith(codecs.BOM_UTF8):
        return codecs.lookup("utf-8-sig").name
    head = raw[:4096].decode("ascii", errors="ignore")
    meta_match = _META_CHARSET_RE.search(head)
    xml_match = _XML_ENCODING_RE.search(head)
    candidates = []
    if meta_match is not None:
        candidates.append(meta_match)
    if xml_match is not None:
        candidates.append(xml_match)
    candidates.sort(key=lambda m: m.start())
    for m in candidates:
        label = m.group(1) or m.group(2) or m.group(3) or ""
        label = label.strip()
        if not label:
            continue
        canonical = _canonical_label(label)
        if canonical is not None:
            return canonical
        # Unknown label: fall through to next candidate or no-declaration rule.
    try:
        raw.decode("utf-8")
        return codecs.lookup("utf-8").name
    except UnicodeDecodeError:
        return codecs.lookup("windows-1252").name


def decode_html(raw: bytes) -> tuple[str, str]:
    """Decode raw HTML bytes strictly, with safe fallbacks.

    Tries the detected charset strictly, then strict UTF-8, then
    cp1252 with replacement. Returns (text, charset_actually_used).
    """
    detected = detect_charset(raw)
    try:
        return raw.decode(detected, errors="strict"), detected
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("utf-8", errors="strict"), codecs.lookup("utf-8").name
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace"), codecs.lookup("windows-1252").name


def normalise_unicode(text: str) -> str:
    """Normalise unicode for LLM reading and verbatim quote search.

    NFKC, delete zero-width chars and soft hyphen, map every Zs space
    and tab to a plain space, normalise CRLF/CR to LF. Keeps curly
    quotes, dashes and multiplication sign as they are.
    """
    text = unicodedata.normalize("NFKC", text)
    text = "".join(ch for ch in text if ch not in _DELETE_CHARS)
    text = "".join(
        " " if (ch == "\t" or unicodedata.category(ch) == "Zs") else ch for ch in text
    )
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


@dataclass(frozen=True)
class ImageRef:
    """A local image in the filing (payoff diagrams, charts), in document order."""

    src: str
    alt: str


@dataclass(frozen=True)
class Filing:
    """A decoded filing: clean text, the charset used to decode it, and its local images."""

    text: str
    charset: str
    images: tuple[ImageRef, ...]


class _FilingHTMLParser(HTMLParser):
    """Extract visible text, table rows and local images, skipping hidden data."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._images: list[ImageRef] = []
        self._skip_depth: int = 0
        self._current_row: list[str] | None = None
        self._current_cell_parts: list[str] | None = None

    def handle_data(self, data: str) -> None:
        if self._is_skipping():
            return
        # A newline in HTML source is just whitespace; only block tags make line breaks.
        data = _HTML_SPACE_RE.sub(" ", data)
        if self._current_cell_parts is not None:
            self._current_cell_parts.append(data)
        else:
            self._chunks.append(data)

    def close(self) -> None:
        super().close()
        # Flush an unclosed row so malformed tables still emit their cells.
        self._finish_row()

    def result(self) -> tuple[str, tuple[ImageRef, ...]]:
        return "".join(self._chunks), tuple(self._images)

    def _is_skipping(self) -> bool:
        return self._skip_depth > 0

    def _finish_row(self) -> None:
        if self._current_row is None:
            return
        if self._current_cell_parts is not None:
            self._current_row.append("".join(self._current_cell_parts))
            self._current_cell_parts = None
        cells = [c.strip() for c in self._current_row]
        non_empty = [c for c in cells if c]
        if any("\n" in c for c in non_empty):
            # Page layout: filings put whole sections (e.g. Key Terms) inside table cells.
            # Keep their paragraphs as lines instead of gluing them into one row.
            self._chunks.append("\n" + "\n".join(non_empty) + "\n")
        elif non_empty:
            # A data row: one-line cells such as "Initial Value | 2,341.377".
            self._chunks.append("\n" + " | ".join(non_empty) + "\n")
        else:
            self._chunks.append("\n")
        self._current_row = None

    def _handle_img(self, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {k.lower(): (v if v is not None else "") for k, v in attrs}
        src = (attrs_dict.get("src") or "").strip()
        alt = (attrs_dict.get("alt") or "").strip()
        if not src:
            return
        low = src.lower()
        if low.startswith("http://") or low.startswith("https://"):
            # Tracking pixel: record nothing and emit no placeholder.
            return
        self._images.append(ImageRef(src=src, alt=alt))
        base = src.split("?")[0].split("#")[0]
        filename = base.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        if not filename:
            filename = base
        placeholder = f"[image: {filename}]"
        if self._current_cell_parts is not None:
            self._current_cell_parts.append(f"\n{placeholder}\n")
        else:
            self._chunks.append(f"\n{placeholder}\n")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        ltag = tag.lower()
        if ltag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._is_skipping():
            return
        if ltag == "img":
            self._handle_img(attrs)
            return
        if ltag in ("td", "th"):
            if self._current_row is not None:
                if self._current_cell_parts is not None:
                    self._current_row.append("".join(self._current_cell_parts))
                self._current_cell_parts = []
            else:
                self._chunks.append("\n")
            return
        if ltag == "tr":
            if self._current_row is not None:
                self._finish_row()
            self._current_row = []
            self._current_cell_parts = None
            self._chunks.append("\n")
            return
        if ltag in _BLOCK_TAGS:
            if self._current_cell_parts is not None:
                self._current_cell_parts.append("\n")
            else:
                self._chunks.append("\n")
            return

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        ltag = tag.lower()
        if ltag in _SKIP_TAGS:
            return
        if self._is_skipping():
            return
        if ltag == "img":
            self._handle_img(attrs)
            return
        if ltag in ("td", "th", "tr"):
            # Self-closing table tags are not expected; treat as start tag.
            self.handle_starttag(tag, attrs)
            return
        if ltag in _BLOCK_TAGS:
            if self._current_cell_parts is not None:
                self._current_cell_parts.append("\n")
            else:
                self._chunks.append("\n")
            return

    def handle_endtag(self, tag: str) -> None:
        ltag = tag.lower()
        if ltag in _SKIP_TAGS:
            if self._skip_depth > 0:
                self._skip_depth -= 1
            return
        if self._is_skipping():
            return
        if ltag in ("td", "th"):
            if self._current_row is not None and self._current_cell_parts is not None:
                self._current_row.append("".join(self._current_cell_parts))
                self._current_cell_parts = None
            return
        if ltag == "tr":
            self._finish_row()
            return
        if ltag in _BLOCK_TAGS:
            if self._current_cell_parts is not None:
                self._current_cell_parts.append("\n")
            else:
                self._chunks.append("\n")
            return


def html_to_text(html: str) -> tuple[str, tuple[ImageRef, ...]]:
    """Convert filing HTML string to clean text plus local image refs.

    Skips script/style/noscript/template/ix:header with nesting depth,
    joins a row's non-empty one-line cells with ' | ' (a row whose cells hold paragraphs is
    page layout and keeps its lines), records local img files in
    order while skipping http(s) tracking pixels, then normalises
    unicode, collapses spaces per line and 3+ newlines to 2, and drops a
    Wingdings "o" sub-bullet glued to a line's first word ("oIf the
    underlier ..." -> "If the underlier ..."; no English word starts with
    "o" followed by a capitalised word).
    """
    parser = _FilingHTMLParser()
    parser.feed(html)
    parser.close()
    raw_text, images = parser.result()
    text = normalise_unicode(raw_text)
    lines = text.split("\n")
    cleaned = [re.sub(r" +", " ", line).strip() for line in lines]
    text = "\n".join(cleaned)
    text = re.sub(r"(?m)^o(?=[A-Z][a-z])", "", text)  # glued "o" sub-bullet
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    return text, images


def read_filing(raw: bytes) -> Filing:
    """Decode raw filing bytes then extract clean text and images."""
    decoded, charset = decode_html(raw)
    text, images = html_to_text(decoded)
    return Filing(text=text, charset=charset, images=images)
