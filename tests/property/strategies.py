"""Test-owned generators; expected behavior never comes from pyroma parsers."""

import datetime
from dataclasses import dataclass
from unittest.mock import patch

from hypothesis import settings
from hypothesis import strategies as st

from pyroma import ratings
from pyroma.metadata import Metadata

FIXED_TODAY = datetime.date(2026, 10, 11)
property_settings = settings(deadline=None)
WORDS = st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=12)
TEXT = st.text(alphabet=st.characters(exclude_categories=("Cc", "Cs")), max_size=1024)
OPERATORS = ("==", "!=", "<", "<=", ">", ">=")


@st.composite
def valid_metadata(draw: st.DrawFn) -> Metadata:
    name = "property-" + draw(WORDS)
    version = ".".join(str(draw(st.integers(low, high))) for low, high in ((1, 9), (0, 9), (0, 9)))
    summary = draw(st.text(alphabet="abcdef ", min_size=11, max_size=80))
    description = draw(st.text(alphabet="abcdef \n", min_size=101, max_size=200))
    project = {
        "name": name,
        "version": version,
        "description": summary,
        "readme": {"text": description, "content-type": "text/plain"},
        "requires-python": ">=3.11",
        "license": "MIT",
        "dependencies": ["example>=1"],
        "urls": {"Homepage": "https://example.invalid"},
    }
    return {
        "metadata-version": "2.4",
        "name": name,
        "version": version,
        "summary": summary,
        "description": description,
        "description-content-type": "text/plain",
        "classifier": [
            "Programming Language :: Python :: 3.11",
            "Development Status :: 5 - Production/Stable",
        ],
        "keywords": ["packaging"],
        "author-email": "Example Author <author@example.invalid>",
        "project-url": {"Homepage": "https://example.invalid"},
        "requires-python": ">=3.11",
        "requires-dist": ["example>=1"],
        "license-expression": "MIT",
        "_pyproject": {
            "project": project,
            "build-system": {"requires": ["setuptools>=77"], "build-backend": "setuptools.build_meta"},
        },
    }


@st.composite
def rating_metadata(draw: st.DrawFn) -> Metadata:
    data = draw(valid_metadata())
    for defect in draw(st.sets(st.sampled_from(("summary", "dependency", "classifiers", "typing", "license")))):
        if defect == "summary":
            data["summary"] = "short"
        elif defect == "dependency":
            data["requires-dist"] = ["example<3"]
        elif defect == "classifiers":
            data["classifier"] = ["Programming Language :: Python :: 3.10"]
        elif defect == "typing":
            data["_py_typed"] = True
        else:
            data["license-expression"] = "not-a-license"
    return data


def frozen_rate(data: Metadata, skip: list[str] | str | None = None, *, strict: bool = False) -> ratings.RatedProject:
    with patch.object(ratings, "_TODAY", FIXED_TODAY):
        return ratings.rate_project(data, skip, strict=strict)


def rating_outcome(
    data: Metadata, skip: list[str] | str | None = None, *, strict: bool = False
) -> ratings.RatedProject | tuple[type[ratings.ConfigurationError], str]:
    try:
        return frozen_rate(data, skip, strict=strict)
    except ratings.ConfigurationError as error:
        return type(error), str(error)


@dataclass(frozen=True)
class Comparison:
    variable: str
    operator: str
    value: str
    reversed: bool = False
    quote: str = '"'

    def render(self) -> str:
        literal = f"{self.quote}{self.value}{self.quote}"
        left, right = (literal, self.variable) if self.reversed else (self.variable, literal)
        return f"{left} {self.operator} {right}"


@dataclass(frozen=True)
class Boolean:
    left: "Expression"
    operator: str
    right: "Expression"

    def render(self) -> str:
        return f"({self.left.render()} {self.operator} {self.right.render()})"


Expression = Comparison | Boolean


def expressions(atoms: st.SearchStrategy[Comparison]) -> st.SearchStrategy[Expression]:
    return st.recursive(
        atoms,
        lambda children: st.builds(Boolean, children, st.sampled_from(("and", "or")), children),
        max_leaves=8,
    )


@st.composite
def python_atoms(draw: st.DrawFn, *, minor_only: bool = False) -> Comparison:
    variable = "python_version" if minor_only else draw(st.sampled_from(("python_version", "python_full_version")))
    value = f"3.{draw(st.integers(8, 14))}"
    if variable == "python_full_version":
        value += f".{draw(st.integers(0, 9))}"
    return Comparison(
        variable, draw(st.sampled_from(OPERATORS)), value, draw(st.booleans()), draw(st.sampled_from(('"', "'")))
    )


EXTRA_ATOMS = st.builds(
    Comparison,
    st.just("extra"),
    st.sampled_from(("==", "!=", "in", "not in")),
    st.sampled_from(("", "test", "docs", "test,docs", "dev-test")),
    st.booleans(),
)
ENVIRONMENT_ATOMS = st.builds(
    Comparison, st.just("os_name"), st.sampled_from(("==", "!=")), st.sampled_from(("posix", "nt", "other"))
)


def environment(version: str, os_name: str = "posix") -> dict[str, str]:
    return {
        "python_version": ".".join(version.split(".")[:2]),
        "python_full_version": version,
        "extra": "",
        "os_name": os_name,
    }


def comparisons(expression: Expression) -> list[Comparison]:
    if isinstance(expression, Comparison):
        return [expression]
    return comparisons(expression.left) + comparisons(expression.right)


@st.composite
def backend_cases(draw: st.DrawFn) -> tuple[list[str], int, int, int, int, int]:
    """A finite grammar with a final integer representative for every feasible cell.

    All backend intervals start with >= an integer and end with < an integer;
    exact pins are final integers. There are no strict lower bounds, wildcard
    pins, exclusions, or prerelease thresholds. Thus every nonempty intersection
    contains an integer, and all its versions are on the same side of the integer
    floor as its integer representatives. Python conditions only inspect minors.
    """
    lower = draw(st.integers(70, 78))
    upper = draw(st.integers(lower + 1, lower + 5))
    python_lower = draw(st.integers(8, 12))
    python_upper = draw(st.integers(python_lower + 1, 15))
    requirements = [f"setuptools>={lower},<{upper}"]
    # Reserve the fourth entry for duplicate/unreachable requirement mutations.
    for _ in range(draw(st.integers(0, 2))):
        operator = draw(st.sampled_from((">=", "<", "==")))
        bound = draw(st.integers(lower - 1, upper + 1))
        marker = draw(st.one_of(st.none(), expressions(python_atoms(minor_only=True))))
        requirement = f"setuptools{operator}{bound}"
        requirements.append(requirement if marker is None else requirement + "; " + marker.render())
    return requirements, python_lower, python_upper, lower, upper, draw(st.integers(lower - 1, upper + 1))


@st.composite
def markdown_targets(draw: st.DrawFn) -> str:
    stem = draw(WORDS)
    path = draw(st.sampled_from((f"docs/{stem}.md", f"docs/{stem}(a).md", f"docs/{stem}.md?a=1&b=2")))
    prefix = draw(st.sampled_from(("", "https://example.invalid/", "//example.invalid/")))
    return prefix + path
