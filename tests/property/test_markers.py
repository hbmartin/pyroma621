"""Differential marker checks with explicit, host-independent environments."""

import pytest
from hypothesis import example, given
from packaging.markers import Marker
from packaging.version import Version

from pyroma._markers import active_without_extra, python_marker_range

from .strategies import (
    ENVIRONMENT_ATOMS,
    EXTRA_ATOMS,
    Boolean,
    Comparison,
    Expression,
    comparisons,
    environment,
    expressions,
    python_atoms,
)


@given(expressions(python_atoms()))
@example(Comparison("python_version", ">", "3.11"))
@example(Comparison("python_full_version", ">=", "3.11.5", reversed=True))
@example(Boolean(Comparison("python_version", "==", "3.11"), "and", Comparison("python_version", "!=", "3.11")))
def test_python_ranges_match_packaging(expression: Expression) -> None:
    marker = Marker(expression.render())
    versions = python_marker_range(marker)
    assert versions is not None
    probes = {"3.7.0", "3.15.0"}
    for atom in comparisons(expression):
        parts = [int(part) for part in atom.value.split(".")]
        minor = parts[1]
        patch = parts[2] if len(parts) == 3 else 0
        probes.update(
            (f"3.{minor - 1}.9", f"3.{minor}.0", f"3.{minor}.{patch}", f"3.{minor}.{patch + 1}", f"3.{minor + 1}.0")
        )
        if patch:
            probes.add(f"3.{minor}.{patch - 1}")
    for version in probes:
        assert versions.contains(Version(version)) is marker.evaluate(environment(version)), (marker, version)


@given(expressions(EXTRA_ATOMS))
@example(Comparison("extra", "==", "test"))
@example(Comparison("extra", "!=", "test"))
@example(Comparison("extra", "in", "test,docs"))
def test_extra_only_conditions_are_exact(expression: Expression) -> None:
    marker = Marker(expression.render())
    assert active_without_extra(marker) is marker.evaluate(environment("3.11.5"))


@given(expressions(EXTRA_ATOMS | ENVIRONMENT_ATOMS | python_atoms()))
@example(Boolean(Comparison("extra", "==", "test"), "and", Comparison("os_name", "==", "posix")))
@example(Boolean(Comparison("extra", "==", "test"), "or", Comparison("python_version", ">=", "3.11")))
def test_mixed_conditions_never_drop_an_active_dependency(expression: Expression) -> None:
    marker = Marker(expression.render())
    retained = active_without_extra(marker)
    for version in ("3.8.0", "3.11.5", "3.14.0"):
        for os_name in ("posix", "nt", "other"):
            if marker.evaluate(environment(version, os_name)):
                assert retained, marker


@pytest.mark.parametrize(
    "source",
    [
        'os_name == "posix"',
        'python_version in "3.11,3.12"',
        'python_version ~= "3.11"',
        'python_version == "3.11.5"',
        'python_version >= "3.11" and os_name == "posix"',
    ],
)
def test_unsupported_python_conditions_remain_unknown(source: str) -> None:
    marker = Marker(source)
    assert python_marker_range(marker) is None
    assert active_without_extra(marker) is True
