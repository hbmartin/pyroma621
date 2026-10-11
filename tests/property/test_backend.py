"""Finite-domain oracle and algebraic properties for backend constraints."""

import pytest
from hypothesis import example, given
from hypothesis import strategies as st
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet

from pyroma._backend import backend_floor_satisfied

from .strategies import backend_cases, environment, property_settings

BackendCase = tuple[list[str], int, int, int, int, int]


def inputs(case: BackendCase) -> tuple[list[Requirement], SpecifierSet, str]:
    raw, python_lower, python_upper, _lower, _upper, floor = case
    return [Requirement(value) for value in raw], SpecifierSet(f">=3.{python_lower},<3.{python_upper}"), str(floor)


def enumerated_floor(case: BackendCase) -> bool | None:
    """Enumerate the complete representative set for backend_cases' grammar."""
    requirements, _supported, _floor = inputs(case)
    _raw, python_lower, python_upper, lower, upper, floor = case
    feasible = []
    for minor in range(python_lower, python_upper):
        active = [req for req in requirements if req.marker is None or req.marker.evaluate(environment(f"3.{minor}.0"))]
        feasible.extend(
            backend for backend in range(lower, upper) if all(req.specifier.contains(str(backend)) for req in active)
        )
    return all(backend >= floor for backend in feasible) if feasible else None


@property_settings
@given(backend_cases())
@example((["setuptools>=76,<79", "setuptools>=77"], 11, 12, 76, 79, 77))
@example((["setuptools>=76,<79"], 11, 12, 76, 79, 77))
@example((["setuptools>=76,<79", "setuptools==80"], 11, 12, 76, 79, 77))
def test_backend_floor_matches_finite_oracle(case: BackendCase) -> None:
    requirements, supported, floor = inputs(case)
    assert backend_floor_satisfied(requirements, supported, floor) is enumerated_floor(case)


@property_settings
@given(case=backend_cases(), data=st.data())
def test_order_duplicates_and_unreachable_conditions_preserve_result(case: BackendCase, data: st.DataObject) -> None:
    requirements, supported, floor = inputs(case)
    expected = backend_floor_satisfied(requirements, supported, floor)
    permuted = list(data.draw(st.permutations(requirements)))
    assert backend_floor_satisfied(permuted, supported, floor) is expected
    duplicate = data.draw(st.sampled_from(requirements))
    assert backend_floor_satisfied([*requirements, duplicate], supported, floor) is expected
    impossible = Requirement(f'setuptools==1; python_version < "3.{case[1]}"')
    assert backend_floor_satisfied([*requirements, impossible], supported, floor) is expected


@pytest.mark.parametrize(
    ("requirements", "expected"),
    [
        (["setuptools>=77"], True),
        (["setuptools>=77rc1"], False),
        (["setuptools==77.*"], False),
        (["setuptools==78.*"], True),
        (["setuptools>=77,<70"], None),
        (["setuptools @ https://example.invalid/backend.whl"], None),
        (['setuptools>=77; os_name == "posix"'], None),
        (["setuptools>=77", 'setuptools>=61; os_name == "posix"'], True),
        (["setuptools>=77", "setuptools @ https://example.invalid/backend.whl"], True),
    ],
)
def test_cases_outside_finite_grammar(requirements: list[str], expected: bool | None) -> None:
    assert (
        backend_floor_satisfied([Requirement(value) for value in requirements], SpecifierSet(">=3.11"), "77")
        is expected
    )


def test_empty_supported_python_range_is_unknown() -> None:
    assert backend_floor_satisfied([Requirement("setuptools>=77")], SpecifierSet(">3.12,<3.12"), "77") is None
