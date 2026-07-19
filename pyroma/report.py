"""Render rating results for human or machine consumption.

This module is the only place where pyroma formats program output.
The logging module is reserved for genuine diagnostics.
"""

import dataclasses
import json
import textwrap

from pyroma.ratings import Problem, RatedProject

FORMATS = ("text", "json")
_DIVIDER = "-" * 30


def _advisory_lines(advisories: "list[Problem]") -> "list[str]":
    """Render the unscored advisory section that follows the rating."""
    count = len(advisories)
    noun = "finding" if count == 1 else "findings"
    lines = [f"Advisory (not scored, {count} {noun}) - run --strict to score these:"]
    # Advisory messages are often multi-line, so indent every line of each.
    lines.extend(textwrap.indent(advisory.message, "  ") for advisory in advisories)
    lines.append(_DIVIDER)
    return lines


def format_text(rated: RatedProject, *, show_advisories: bool = True) -> str:
    """Format the rating result the way pyroma traditionally prints it."""
    lines: list[str] = [_DIVIDER]
    lines.extend(problem.message for problem in rated.problems)
    if rated.problems:
        lines.append(_DIVIDER)
    lines.append(f"Final rating: {rated.rating}/10")
    lines.append(rated.level)
    lines.append(_DIVIDER)
    if show_advisories and rated.advisories:
        lines.extend(_advisory_lines(rated.advisories))
    return "\n".join(lines)


def format_json(rated: RatedProject, meta: dict[str, str] | None = None) -> str:
    """Format the rating result as a machine-readable JSON document."""
    document = {
        "name": rated.name,
        "rating": rated.rating,
        "level": rated.level,
        "problems": [dataclasses.asdict(problem) for problem in rated.problems],
        # Always present, regardless of --no-advisories: a machine-readable
        # consumer should never have its data silently truncated by a
        # presentation flag.
        "advisories": [dataclasses.asdict(advisory) for advisory in rated.advisories],
        "_meta": meta or {},
    }
    return json.dumps(document, indent=2)


def format_json_error(error: Exception, meta: dict[str, str] | None = None) -> str:
    """Format a fatal error as a machine-readable JSON document.

    The document has an "error" key instead of a "rating" key, so consumers
    can tell the two document kinds apart.
    """
    document = {
        "error": {"type": type(error).__name__, "message": str(error)},
        "_meta": meta or {},
    }
    return json.dumps(document, indent=2)
