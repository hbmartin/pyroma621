"""Render rating results for human or machine consumption.

This module is the only place where pyroma formats program output.
The logging module is reserved for genuine diagnostics.
"""

import dataclasses
import json

from pyroma.ratings import RatedProject

FORMATS = ("text", "json")
_DIVIDER = "-" * 30


def format_text(rated: RatedProject) -> str:
    """Format the rating result the way pyroma traditionally prints it."""
    lines = [_DIVIDER]
    for problem in rated.problems:
        lines.append(problem.message)
    if rated.problems:
        lines.append(_DIVIDER)
    lines.append(f"Final rating: {rated.rating}/10")
    lines.append(rated.level)
    lines.append(_DIVIDER)
    return "\n".join(lines)


def format_json(rated: RatedProject, meta: dict[str, str] | None = None) -> str:
    """Format the rating result as a machine-readable JSON document."""
    document = {
        "name": rated.name,
        "rating": rated.rating,
        "level": rated.level,
        "problems": [dataclasses.asdict(problem) for problem in rated.problems],
        "_meta": meta or {},
    }
    return json.dumps(document, indent=2)
