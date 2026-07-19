"""Categories describing what kind of problem a rating test reports.

This module deliberately imports nothing from pyroma, so that any other
module can depend on it without risking an import cycle.

Tests are identified by their class name, which is already a stable public
identifier: it is what ``--skip-tests`` accepts and what the JSON reporter
emits as ``test``. A category is the axis that name does not capture --
whether a finding is a specification violation, something a package index
will reject, or an opinionated recommendation.
"""

from enum import StrEnum


class Category(StrEnum):
    """What kind of problem a rating test reports."""

    # A packaging specification says MUST, and pyroma can prove it is broken.
    SPEC = "spec"
    # A package index will reject the upload outright.
    INDEX = "index"
    # A metadata field is absent, empty or uninformative.
    METADATA = "metadata"
    # Two pieces of metadata are individually valid but contradict each other.
    COHERENCE = "coherence"
    # An opinionated recommendation, not required by any specification.
    PRACTICE = "practice"
    # Pyroma could not do its job: no config found, or the build failed.
    INTERNAL = "internal"


def categories() -> "list[str]":
    """Return every category name, for command-line validation and help."""
    return [category.value for category in Category]
