"""Extract unambiguous Markdown destinations without a rendering dependency.

This deliberately covers common inline/reference links and HTML attributes,
not all CommonMark block nesting or extension syntax. Ambiguity is skipped.
"""

import html
import re
import string
from html.parser import HTMLParser

_COMMENTS = re.compile(r"<!--.*?(?:-->|\Z)", re.DOTALL)
_LITERAL_HTML = re.compile(r"<(pre|code|script|style)\b[^>]*>.*?(?:</\1\s*>|\Z)", re.IGNORECASE | re.DOTALL)
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_QUOTE_PREFIX = re.compile(r"^ {0,3}(?:> ?)+")
_LIST_PREFIX = re.compile(r"^ {0,3}(?:[-+*]|\d+[.)]) +")
_DEFINITION = re.compile(r"^ {0,3}\[((?:\\.|[^\[\]\\\n])+)\]:[ \t]*(.*)$")
_ESCAPE = re.compile(r"\\([" + re.escape(string.punctuation) + r"])")
_HTML_TAG = re.compile(r"""</?[A-Za-z][\w-]*(?:\s+(?:[^<>"']|"[^"]*"|'[^']*')*)?/?>""")
_FIRST_PRINTABLE = 32


def _decode(value: str) -> str:
    return html.unescape(_ESCAPE.sub(r"\1", value))


def _label(value: str) -> str:
    return " ".join(value.split()).casefold()


def _blocks(source: str) -> str:
    source = _LITERAL_HTML.sub("", _COMMENTS.sub("", source))
    lines = []
    fence = ""
    for line in source.splitlines():
        content = _QUOTE_PREFIX.sub("", line)
        match = _FENCE.match(_LIST_PREFIX.sub("", content))
        if fence:
            if match and match[1][0] == fence[0] and len(match[1]) >= len(fence) and not match[2].strip():
                fence = ""
            continue
        if match and (match[1][0] != "`" or "`" not in match[2]):
            fence = match[1]
        elif not content.startswith(("    ", "\t")):
            lines.append(line)
    return "\n".join(lines)


def _inline_code(source: str) -> str:
    pieces = []
    position = 0
    while position < len(source):
        if source[position] == "\\":
            pieces.append(source[position : position + 2])
            position += 2
            continue
        tag = _HTML_TAG.match(source, position) if source[position] == "<" else None
        if tag:
            pieces.append(tag[0])
            position = tag.end()
            continue
        if source[position] != "`":
            pieces.append(source[position])
            position += 1
            continue
        end = position
        while end < len(source) and source[end] == "`":
            end += 1
        run = source[position:end]
        closing = re.search(r"(?<!`)" + re.escape(run) + r"(?!`)", source[end:])
        if closing is None:
            pieces.append(run)
            position = end
        else:
            position = end + closing.end()
            pieces.append(" ")
    return "".join(pieces)


def _angle_destination(source: str, start: int) -> tuple[str, int] | None:
    position = start + 1
    while position < len(source):
        char = source[position]
        if char == "\\":
            position += 2
            continue
        if char == ">":
            return source[start + 1 : position], position + 1
        if char in "<\n":
            return None
        position += 1
    return None


def _destination(source: str, start: int) -> tuple[str, int] | None:
    if start >= len(source):
        return None
    if source[start] == "<":
        return _angle_destination(source, start)
    position, depth = start, 0
    while position < len(source):
        char = source[position]
        if char == "\\":
            position += 2
            continue
        if char.isspace() or (char == ")" and depth == 0):
            break
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char in "<>" or ord(char) < _FIRST_PRINTABLE:
            return None
        position += 1
    if depth or position == start:
        return None
    return source[start:position], position


def _title_end(source: str, start: int) -> int | None:
    if start >= len(source) or source[start] not in "\"'(":
        return None
    closing = ")" if source[start] == "(" else source[start]
    position = start + 1
    while position < len(source):
        if source[position] == "\\":
            position += 2
        elif source[position] == closing:
            return position + 1
        elif source[position] in "\n(" and closing == ")":
            return None
        else:
            position += 1
    return None


def _target(source: str, start: int, *, inline: bool) -> tuple[str, int] | None:
    start += len(source[start:]) - len(source[start:].lstrip())
    destination = _destination(source, start)
    if destination is None:
        return None
    value, end = destination
    position = end + len(source[end:]) - len(source[end:].lstrip())
    if position > end and position < len(source) and source[position] in "\"'(":
        title = _title_end(source, position)
        if title is None:
            return None
        position = title + len(source[title:]) - len(source[title:].lstrip())
    if inline:
        return (_decode(value), position + 1) if source[position : position + 1] == ")" else None
    return (_decode(value), position) if position == len(source) else None


def _references(source: str) -> tuple[str, dict[str, str]]:
    definitions: dict[str, str] = {}
    lines = []
    for line in source.splitlines():
        match = _DEFINITION.match(line)
        if match is None:
            lines.append(line)
            continue
        target = _target(match[2], 0, inline=False)
        if target is not None:
            definitions.setdefault(_label(match[1]), target[0])
    return "\n".join(lines), definitions


def _bracket_end(source: str, start: int) -> int | None:
    position, depth = start + 1, 1
    while position < len(source):
        char = source[position]
        if char == "\\":
            position += 2
            continue
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return position
        position += 1
    return None


def _link(source: str, start: int, references: dict[str, str]) -> tuple[str | None, int]:
    end = _bracket_end(source, start)
    if end is None:
        return None, len(source)
    label = source[start + 1 : end]
    position = end + 1
    if source[position : position + 1] == "(":
        target = _target(source, position + 1, inline=True)
        return target or (None, position + 1)
    if source[position : position + 1] == "[":
        ref_end = _bracket_end(source, position)
        if ref_end is None:
            return None, len(source)
        label = source[position + 1 : ref_end] or label
        position = ref_end + 1
    return references.get(_label(label)), position


class _HTMLTargets(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.targets: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"pre", "code", "script", "style"}:
            return
        for name, value in attrs:
            if name in {"href", "src"} and value:
                self.targets.append(value)


def _html_targets(source: str) -> tuple[str, list[str]]:
    parser = _HTMLTargets()
    pieces = []
    end = 0
    for match in _HTML_TAG.finditer(source):
        prefix = source[: match.start()]
        if (len(prefix) - len(prefix.rstrip("\\"))) % 2:
            continue
        parser.feed(match[0])
        pieces.extend((source[end : match.start()], " "))
        end = match.end()
    pieces.append(source[end:])
    return "".join(pieces), parser.targets


def destinations(source: str) -> list[str]:
    """Return destinations outside code, resolving the first reference definition."""
    source, references = _references(_inline_code(_blocks(source)))
    source, found = _html_targets(source)
    position = 0
    while position < len(source):
        char = source[position]
        if char == "\\":
            position += 2
        elif char == "[":
            target, position = _link(source, position, references)
            if target:
                found.append(target)
        else:
            position += 1
    return found
