"""Extract CommonMark and HTML destinations without rendering the document."""

from html.parser import HTMLParser
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote_to_bytes

from markdown_it import MarkdownIt

if TYPE_CHECKING:
    from collections.abc import Iterator

    from markdown_it.token import Token

_MARKDOWN = MarkdownIt("commonmark", {"html": True})
_LITERAL_TAGS = frozenset({"pre", "code", "script", "style"})
_ASCII_CONTROLS = frozenset(chr(value) for value in range(32)) | {"\x7f"}


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


def display_destination(target: str) -> str:
    """Decode readable URL text, retaining reserved escapes and controls."""
    try:
        unquote_to_bytes(target).decode("utf-8")
    except UnicodeDecodeError:
        return target
    readable = _MARKDOWN.normalizeLinkText(target)
    return "".join(f"%{ord(char):02X}" if char in _ASCII_CONTROLS else char for char in readable)


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
    return [_MARKDOWN.normalizeLink(target) for target in html.targets]
