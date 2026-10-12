"""Extract CommonMark and HTML destinations without rendering the document."""

import json
import re
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Any
from unicodedata import category
from urllib.parse import quote, unquote_to_bytes

from markdown_it import MarkdownIt

if TYPE_CHECKING:
    from collections.abc import Iterator

    from markdown_it.token import Token

_MARKDOWN = MarkdownIt("commonmark", {"html": True})
_LITERAL_TAGS = frozenset({"pre", "code", "script", "style"})
_ESCAPED_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp", "Zs"})
_JOINERS = frozenset({"\u200c", "\u200d"})
_READABLE_FORMATTING = _JOINERS | {" "}
_UNRESERVED_URL_CHARACTERS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
_PERCENT_ESCAPE_RE = re.compile(r"%[0-9a-fA-F]{2}")
_PERCENT_RUN_RE = re.compile(r"(?:%[0-9a-fA-F]{2})+")
_SURROGATE_ESCAPE_START = 0xDC80
_SURROGATE_ESCAPE_END = 0xDCFF
_SURROGATE_ESCAPE_BASE = 0xDC00


class _HTMLTargets(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.targets: list[str] = []
        self._literal_tags: list[str] = []

    @property
    def in_literal_region(self) -> bool:
        """Report whether an HTML token opened a literal code region."""
        return bool(self._literal_tags)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _LITERAL_TAGS:
            self._literal_tags.append(tag)
        elif not self.in_literal_region:
            self.targets.extend(value for name, value in attrs if name in {"href", "src"} and value)

    def handle_endtag(self, tag: str) -> None:
        for position in range(len(self._literal_tags) - 1, -1, -1):
            if self._literal_tags[position] == tag:
                del self._literal_tags[position]
                break


def _block_tokens(block: "Token") -> "Iterator[Token]":
    """Visit rendered tokens without descending into image alt text."""
    if block.type == "html_block":
        yield block
    elif block.type == "inline":
        yield from block.children or []


def _feed_html(html: _HTMLTargets, token: "Token") -> bool:
    """Feed a rendered HTML token and report whether it was consumed."""
    if token.type in {"html_block", "html_inline"}:
        html.feed(token.content)
        return True
    return False


def _uppercase_escape(match: re.Match[str]) -> str:
    """Keep percent-escape triplets in one spelling."""
    return match[0].upper()


def _unreserved_escape(match: re.Match[str]) -> str:
    """Decode unreserved ASCII without interpreting invalid UTF-8 bytes."""
    character = chr(int(match[0][1:], 16))
    return character if character in _UNRESERVED_URL_CHARACTERS else match[0].upper()


def canonical_destination(target: str) -> str:
    """Normalize URL identity without decoding reserved escapes or Unicode forms."""
    return _PERCENT_ESCAPE_RE.sub(_unreserved_escape, _MARKDOWN.normalizeLink(target))


def display_destination(target: str) -> str:
    """Quote readable paths and identify Unicode with their canonical URL."""
    canonical = canonical_destination(target)
    has_unicode = False

    def readable_escape(match: re.Match[str]) -> str:
        """Decode readable UTF-8 while retaining unsafe ASCII and invalid bytes."""
        nonlocal has_unicode
        result = []
        for char in unquote_to_bytes(match[0]).decode("utf-8", errors="surrogateescape"):
            value = ord(char)
            if _SURROGATE_ESCAPE_START <= value <= _SURROGATE_ESCAPE_END:
                result.append(f"%{value - _SURROGATE_ESCAPE_BASE:02X}")
            elif char.isascii():
                result.append(char if char in _UNRESERVED_URL_CHARACTERS or char == " " else f"%{value:02X}")
            else:
                has_unicode = True
                result.append(
                    quote(char, safe="")
                    if char not in _READABLE_FORMATTING and category(char) in _ESCAPED_CATEGORIES
                    else char
                )
        return "".join(result)

    readable = json.dumps(_PERCENT_RUN_RE.sub(readable_escape, canonical), ensure_ascii=False)
    if has_unicode:
        return f"{readable} (URL: {json.dumps(canonical)})"
    return readable


def _without_literal_definitions(source: str, blocks: "list[Token]") -> str:
    """Mask reference definitions hidden between tokens in literal HTML regions."""
    html = _HTMLTargets()
    # Token maps count CommonMark line endings, not Unicode paragraph separators.
    lines = source.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    previous_end = 0
    changed = False
    for block in blocks:
        if block.nesting != 0 or block.map is None:
            continue
        start, end = block.map
        if html.in_literal_region:
            # Definitions have no output tokens; gaps otherwise contain blank lines.
            for position in range(previous_end, start):
                if lines[position].strip():
                    lines[position] = ""
                    changed = True
        for token in _block_tokens(block):
            _feed_html(html, token)
        previous_end = max(previous_end, end)
    return "\n".join(lines) if changed else source


def destinations(source: str) -> list[str]:
    """Return normalized link and image URLs in document order, outside code."""
    environment: dict[str, Any] = {}
    blocks = _MARKDOWN.parse(source, environment)
    if environment.get("references"):
        filtered = _without_literal_definitions(source, blocks)
        if filtered != source:
            blocks = _MARKDOWN.parse(filtered)
    html = _HTMLTargets()
    for block in blocks:
        for token in _block_tokens(block):
            if _feed_html(html, token):
                continue
            if not html.in_literal_region and token.type in {"link_open", "image"}:
                target = token.attrGet("href" if token.type == "link_open" else "src")
                if isinstance(target, str) and target:
                    html.targets.append(target)
    html.close()
    return [_PERCENT_ESCAPE_RE.sub(_uppercase_escape, _MARKDOWN.normalizeLink(target)) for target in html.targets]
