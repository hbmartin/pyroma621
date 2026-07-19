"""Read pyroma's own settings from a project's ``[tool.pyroma]`` table.

The configuration belongs to the project being rated, not to the directory
pyroma happens to be invoked from: a project's decision to skip a rule
should behave the same in a local run, in CI, and under pre-commit.

Only directory mode reads configuration. In file and PyPI modes the project
is an archive that has not been unpacked when the settings are needed, and a
downloaded artifact is a less trustworthy source for settings that change
pyroma's own exit code.
"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Union

DEFAULT_MIN_RATING = 8
_LOWEST_RATING = 1
_HIGHEST_RATING = 10

Pathish = Union[str, "Path"]


class ConfigError(Exception):
    """Raised when a ``[tool.pyroma]`` table cannot be used."""


@dataclass(frozen=True)
class Config:
    """Effective pyroma settings for one rating run."""

    min_rating: int = DEFAULT_MIN_RATING
    strict: bool = False
    show_advisories: bool = True
    skip_tests: "list[str]" = field(default_factory=list)


def _coerce_min_rating(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        message = f"[tool.pyroma] min-rating must be a whole number, not {value!r}."
        raise ConfigError(message)
    if not _LOWEST_RATING <= value <= _HIGHEST_RATING:
        message = f"[tool.pyroma] min-rating must be between 1 and 10, not {value}."
        raise ConfigError(message)
    return value


def _coerce_bool(key: str, value: object) -> bool:
    if not isinstance(value, bool):
        message = f"[tool.pyroma] {key} must be true or false, not {value!r}."
        raise ConfigError(message)
    return value


def _coerce_skip_tests(value: object) -> "list[str]":
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        message = f"[tool.pyroma] skip-tests must be a list of strings, not {value!r}."
        raise ConfigError(message)
    # Rebuilt rather than cast: the isinstance check above does not narrow
    # the element type for a type checker.
    return [str(item) for item in value]


# One coercion per key, so that adding a key does not grow a single function.
_COERCIONS = {
    "min-rating": lambda value: ("min_rating", _coerce_min_rating(value)),
    "strict": lambda value: ("strict", _coerce_bool("strict", value)),
    "advisories": lambda value: ("show_advisories", _coerce_bool("advisories", value)),
    "skip-tests": lambda value: ("skip_tests", _coerce_skip_tests(value)),
}


def from_table(table: object) -> Config:
    """Build a Config from a parsed ``[tool.pyroma]`` table."""
    if not isinstance(table, dict):
        return Config()

    settings: dict[str, Any] = {}
    for key, value in table.items():
        coerce = _COERCIONS.get(str(key))
        if coerce is None:
            known = ", ".join(sorted(_COERCIONS))
            message = f"[tool.pyroma] has an unknown key {key!r}. Known keys are: {known}."
            raise ConfigError(message)
        name, coerced = coerce(value)
        settings[name] = coerced
    return Config(**settings)


def from_directory(path: Pathish) -> Config:
    """Read ``[tool.pyroma]`` from a project directory's pyproject.toml."""
    pyproject_path = Path(path) / "pyproject.toml"
    try:
        with pyproject_path.open("rb") as pyproject_file:
            parsed = tomllib.load(pyproject_file)
    except (OSError, tomllib.TOMLDecodeError):
        # An unreadable or broken pyproject.toml is reported by the rating
        # itself; it must not stop pyroma from running at all.
        return Config()
    tools = parsed.get("tool")
    if not isinstance(tools, dict):
        return Config()
    return from_table(tools.get("pyroma", {}))
