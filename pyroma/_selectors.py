"""Share registered skip selectors and diagnostics across CLI and config."""

from collections.abc import Iterable

from pyroma import ratings, rules


def test_names() -> list[str]:
    """Return registered test names in registry order."""
    return [test.__class__.__name__ for test in ratings.ALL_TESTS]


def category_names() -> list[str]:
    """Return every skippable category in declaration order."""
    return rules.categories()


def unknown_names(names: Iterable[str]) -> list[str]:
    """Return the distinct unknown selectors in diagnostic order."""
    return sorted(set(names) - set(test_names()) - set(category_names()))


def available_names_message() -> str:
    """Describe accepted selectors identically for every input source."""
    return f"Available tests: {', '.join(test_names())}. Available categories: {', '.join(category_names())}"
