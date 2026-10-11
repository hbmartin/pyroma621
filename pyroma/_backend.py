"""Evaluate combined build requirements over feasible Python partitions."""

from typing import TYPE_CHECKING

from packaging.specifiers import SpecifierSet

from pyroma._markers import python_marker_range

if TYPE_CHECKING:
    from packaging.ranges import VersionRange
    from packaging.requirements import Requirement

_MAX_PARTITIONS = 128


def _partitions(
    requirements: "list[Requirement]", supported: "VersionRange"
) -> "list[tuple[VersionRange, VersionRange]] | None":
    partitions = [(supported, SpecifierSet().to_range())]
    for requirement in requirements:
        applicability = python_marker_range(requirement.marker) if requirement.marker else SpecifierSet().to_range()
        if applicability is None or requirement.url is not None:
            return None
        constraints = requirement.specifier.to_range()
        divided = []
        for python, backend in partitions:
            active, inactive = python & applicability, python - applicability
            if not active.is_empty:
                divided.append((active, backend & constraints))
            if not inactive.is_empty:
                divided.append((inactive, backend))
        partitions = divided
        if len(partitions) > _MAX_PARTITIONS:
            return None
    return partitions


def backend_floor_satisfied(requirements: "list[Requirement]", supported: SpecifierSet, floor: str) -> bool | None:
    """Prove a floor, find an older feasible backend, or skip uncertainty."""
    if supported.to_range().is_empty:
        return None
    older = ~SpecifierSet(f">={floor}").to_range()
    unconditional = SpecifierSet().to_range()
    for requirement in requirements:
        if requirement.marker is None and requirement.url is None:
            unconditional &= requirement.specifier.to_range()
    partitions = _partitions(sorted(requirements, key=str), supported.to_range())
    if partitions is None:
        return True if not unconditional.is_empty and (unconditional & older).is_empty else None
    feasible = [backend for python, backend in partitions if not python.is_empty and not backend.is_empty]
    if not feasible:
        return None
    return all((backend & older).is_empty for backend in feasible)
