"""HTML-to-plain-text conversion for job descriptions.

Greenhouse and Ashby return HTML; the classifier and the DB want plain text. Uses the
stdlib parser rather than adding a dependency for what is a narrow, forgiving job.
"""

from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser

#: Tags whose boundaries imply a line break in the rendered text.
_BLOCK_TAGS = {
    "p",
    "div",
    "br",
    "tr",
    "section",
    "article",
    "header",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "ul",
    "ol",
    "table",
    "blockquote",
}
_DROP_TAGS = {"script", "style", "head"}

_BLANK_LINES = re.compile(r"\n{3,}")
_TRAILING_WS = re.compile(r"[ \t]+\n")
_INLINE_WS = re.compile(r"[ \t]{2,}")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._suppress_depth = 0

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in _DROP_TAGS:
            self._suppress_depth += 1
        elif tag == "li":
            self._parts.append("\n- ")
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _DROP_TAGS:
            self._suppress_depth = max(0, self._suppress_depth - 1)
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._suppress_depth == 0:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def html_to_text(html: str | None) -> str | None:
    """Convert an HTML fragment to readable plain text.

    Unescapes first: Greenhouse double-encodes its `content` field, so the payload
    arrives as escaped markup rather than markup.
    """
    if not html:
        return None

    extractor = _TextExtractor()
    extractor.feed(unescape(html))
    extractor.close()

    text = extractor.text()
    text = _INLINE_WS.sub(" ", text)
    text = _TRAILING_WS.sub("\n", text)
    text = _BLANK_LINES.sub("\n\n", text)
    return text.strip() or None
