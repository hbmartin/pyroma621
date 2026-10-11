"""Conservative Boolean analysis of public, normalized packaging markers."""

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from packaging.markers import InvalidMarker, Marker, UndefinedComparison, UndefinedEnvironmentName
from packaging.specifiers import InvalidSpecifier, SpecifierSet

if TYPE_CHECKING:
    from packaging.ranges import VersionRange

_TOKEN = re.compile(r""""[^"]*"|'[^']*'|\(|\)|===|==|!=|<=|>=|~=|<|>|[\w.]+""")
_SERIES = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\Z")
_REVERSED = {"==": "==", "!=": "!=", "<": ">", ">": "<", "<=": ">=", ">=": "<="}


@dataclass(frozen=True, kw_only=True)
class Atom:
    """One comparison, retaining quoted literals separately from variables."""

    left: str
    operator: str
    right: str


@dataclass(frozen=True, kw_only=True)
class Junction:
    """A conjunction or disjunction of marker conditions."""

    operator: str
    left: "Node"
    right: "Node"


Node = Atom | Junction


class _Parser:
    def __init__(self, source: str) -> None:
        self.tokens: list[str] = []
        end = 0
        for match in _TOKEN.finditer(source):
            if source[end : match.start()].strip():
                raise ValueError
            self.tokens.append(match.group())
            end = match.end()
        if source[end:].strip():
            raise ValueError
        self.position = 0

    def take(self) -> str:
        token = self.tokens[self.position]
        self.position += 1
        return token

    def expression(self, operator: str = "or") -> Node:
        child = self.expression("and") if operator == "or" else self.primary()
        while self.position < len(self.tokens) and self.tokens[self.position] == operator:
            self.take()
            right = self.expression("and") if operator == "or" else self.primary()
            child = Junction(operator=operator, left=child, right=right)
        return child

    def primary(self) -> Node:
        left = self.take()
        if left == "(":
            child = self.expression()
            if self.take() != ")":
                raise ValueError
            return child
        operator = self.take()
        if operator == "not":
            if self.take() != "in":
                raise ValueError
            operator = "not in"
        return Atom(left=left, operator=operator, right=self.take())


def _parse(marker: Marker) -> Node | None:
    try:
        parser = _Parser(str(marker))
        node = parser.expression()
        return node if parser.position == len(parser.tokens) else None
    except (ValueError, IndexError, RecursionError):
        return None


def _possible_without_extra(node: Node) -> bool:
    if isinstance(node, Junction):
        left, right = _possible_without_extra(node.left), _possible_without_extra(node.right)
        return left and right if node.operator == "and" else left or right
    if "extra" not in (node.left, node.right):
        # Other environment conditions can be true on another machine.
        return True
    try:
        return Marker(f"{node.left} {node.operator} {node.right}").evaluate({"extra": ""})
    except (InvalidMarker, UndefinedComparison, UndefinedEnvironmentName, ValueError):
        return True


def active_without_extra(marker: Marker) -> bool:
    """Keep a dependency unless its marker provably requires a selected extra."""
    node = _parse(marker)
    return node is None or _possible_without_extra(node)


def _python_comparison(atom: Atom) -> tuple[str, str, str] | None:
    left, operator, right = atom.left, atom.operator, atom.right
    if left.startswith(('"', "'")):
        left, right = right, left
        operator = _REVERSED.get(operator, "")
    if left not in {"python_version", "python_full_version"} or not right.startswith(('"', "'")):
        return None
    return left, operator, right[1:-1]


def _series_specifier(operator: str, value: str) -> str | None:
    match = _SERIES.fullmatch(value)
    if match is None:
        return None
    major, minor = map(int, match.groups())
    if operator in {"==", "!="}:
        return f"{operator}{value}.*"
    if operator == "<":
        return f"<{value}"
    if operator == ">=":
        return f"<{value}"
    if operator in {">", "<="}:
        return f"<{major}.{minor + 1}"
    return None


def _atom_range(atom: Atom) -> "VersionRange | None":
    comparison = _python_comparison(atom)
    if comparison is None:
        return None
    variable, operator, value = comparison
    if operator not in {"==", "!=", "<", ">", "<=", ">=", "~="}:
        return None
    specifier = _series_specifier(operator, value) if variable == "python_version" else operator + value
    if specifier is None:
        return None
    try:
        versions = SpecifierSet(specifier).to_range()
    except InvalidSpecifier:
        return None
    return ~versions if variable == "python_version" and operator in {">", ">="} else versions


def _python_range(node: Node) -> "VersionRange | None":
    if isinstance(node, Atom):
        return _atom_range(node)
    left, right = _python_range(node.left), _python_range(node.right)
    if left is None or right is None:
        return None
    return left & right if node.operator == "and" else left | right


def python_marker_range(marker: Marker) -> "VersionRange | None":
    """Return Python-only applicability, or None for unsupported conditions.

    `python_version` compares the major/minor series; `python_full_version`
    compares the complete version. No host environment is consulted.
    """
    node = _parse(marker)
    return None if node is None else _python_range(node)
