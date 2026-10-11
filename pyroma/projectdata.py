"""Extract and normalize package metadata from a local project directory."""

# Kept at runtime so the public Pathish alias resolves through get_type_hints.
import os
import subprocess
import tomllib
from email.message import Message
from pathlib import Path
from typing import TYPE_CHECKING, Any, Union, cast

import build
import build.util

from pyroma.metadata import Metadata, normalize

if TYPE_CHECKING:
    from collections.abc import Iterator

Pathish = Union[str, "os.PathLike[str]"]

# Failures of the isolated build environment itself (its creation or the
# installation of the build requirements) surface as process errors rather
# than as build exceptions.
_BACKEND_UNAVAILABLE = (build.BuildBackendException, build.FailedProcessError, subprocess.CalledProcessError)


class BuildError(Exception):
    """The project's build configuration could not be read, so no metadata exists.

    Raised when the build frontend rejects the project before any backend
    runs, for example because `pyproject.toml` is not valid TOML or its
    `[build-system]` table is malformed. Unlike a failing build backend,
    which still lets Pyroma rate what it knows, this leaves nothing to rate.
    """


def wheel_metadata(path: Pathish, isolated: "bool | None" = None) -> Message:
    """Return wheel metadata, retrying in isolation when necessary."""
    # If explictly specified whether to use isolation, pass it directly
    if isolated is not None:
        return cast("Message", build.util.project_wheel_metadata(path, isolated=isolated))

    # Otherwise, try without build isolation first for efficiency
    try:
        return cast("Message", build.util.project_wheel_metadata(path, isolated=False))
    # If building with build isolation fails, e.g. missing build deps, try with it
    except (build.BuildException, build.BuildBackendException):
        return cast("Message", build.util.project_wheel_metadata(path, isolated=True))


def build_metadata(path: Pathish, isolated: "bool | None" = None) -> Metadata:
    """Build and normalize the Core Metadata for a project."""
    try:
        metadata = wheel_metadata(path, isolated)
    except _BACKEND_UNAVAILABLE:
        # The backend failed spectacularily, or could not even be installed.
        # This happens with old packages, when we can't build a wheel. It's
        # not always a fatal error. F ex, if you are getting info for a
        # package from PyPI, we already have the metadata from PyPI, we just
        # couldn't get the additional build data.
        return {"_wheel_build_failed": True}
    except build.BuildException as e:
        # The frontend could not even read the project configuration, so
        # there is no metadata at all. Report it as an error instead of a
        # traceback.
        raise BuildError(str(e)) from e

    # As far as I can tell, we can't trust that the builders normalize the keys,
    # so we do it here. Definitely most builders do not lower case them, which
    # Core Metadata Specs recommend.
    data: dict[str, Any] = {}
    for raw_key in set(metadata.keys()):
        values = metadata.get_all(raw_key) or []
        key = normalize(raw_key)

        if key == "classifier":
            if len(values) == 1 and values[0].strip() == "UNKNOWN":
                continue
            data[key] = values
            continue

        value: Any = values
        if len(values) == 1:
            value = values[0]
            if value.strip() == "UNKNOWN":
                # Legacy metadata uses UNKNOWN as an empty-value marker.
                continue

        data[key] = value

    if "description" not in data:
        # Legacy email metadata can store the description in the payload.
        # Payload descriptions tend to add two newlines, which we normalize.
        payload = metadata.get_payload()
        description = payload.strip() if isinstance(payload, str) else ""
        if description:
            data["description"] = description + "\n"
    return cast("Metadata", data)


def read_pyproject(path: Pathish) -> "tuple[dict[str, Any] | None, str | None]":
    """Read and parse a project's pyproject.toml.

    Returns a `(table, error)` pair: exactly one of the two is set. The
    error is a plain string rather than an exception so that metadata stays
    plain data and rating remains a pure function of it.
    """
    pyproject_path = Path(path) / "pyproject.toml"
    try:
        with pyproject_path.open("rb") as pyproject_file:
            return tomllib.load(pyproject_file), None
    except (OSError, tomllib.TOMLDecodeError) as error:
        return None, str(error)


_EXCLUDED_SOURCE_DIRS = frozenset(
    {"venv", "env", "virtualenv", "__pycache__", "__pypackages__", "build", "dist", "_build", "node_modules"}
)


def _source_directories(path: Pathish) -> "Iterator[tuple[Path, list[str]]]":
    """Walk source directories without generated trees, environments or symlinks."""
    for directory, children, files in os.walk(path, followlinks=False):
        root = Path(directory)
        children[:] = [
            child
            for child in children
            if not child.startswith(".")
            and child not in _EXCLUDED_SOURCE_DIRS
            and not (root / child).is_symlink()
            and not (root / child / "pyvenv.cfg").is_file()
        ]
        yield root, [name for name in files if (root / name).is_file() and not (root / name).is_symlink()]


def _complete_stub(root: Path) -> bool:
    marker = root / "py.typed"
    if marker.is_symlink():
        return False
    if not marker.exists():
        return True
    if not marker.is_file():
        return False
    try:
        return "partial" not in marker.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return False


def typing_facts(path: Pathish) -> tuple[bool, bool]:
    """Find regular markers and complete stub packages in the source tree.

    These facts describe sources only; they do not verify wheel contents.
    """
    has_marker = False
    stub_roots: set[Path] = set()
    root = Path(path).resolve()
    for directory, files in _source_directories(root):
        has_marker |= "py.typed" in files
        if not any(name.endswith(".pyi") for name in files):
            continue
        parent = directory
        while parent != root and parent.is_relative_to(root):
            if parent.name.endswith("-stubs"):
                stub_roots.add(parent)
                break
            parent = parent.parent
    return has_marker, any(_complete_stub(stub) for stub in stub_roots)


def has_typed_marker(path: Pathish) -> bool:
    """Report whether a regular PEP 561 marker exists in the source tree."""
    return typing_facts(path)[0]


def _glob_matches(root: Path, pattern: str) -> bool:
    """Report whether a license-files pattern matches anything under root."""
    if not pattern or pattern.startswith("/") or ".." in pattern:
        # PEP 639 forbids absolute paths and parent traversal, and Path.glob
        # raises on them rather than returning nothing.
        return False
    try:
        return any(root.glob(pattern))
    except (NotImplementedError, ValueError, OSError):
        return False


def unmatched_license_files(path: Pathish, patterns: "list[str]") -> "list[str]":
    """Return the license-files patterns that match nothing under the project."""
    root = Path(path)
    return [pattern for pattern in patterns if not _glob_matches(root, pattern)]


def _license_file_patterns(table: "dict[str, Any]") -> "list[str] | None":
    """Return the [project] license-files patterns, or None when not declared."""
    project = table.get("project")
    if not isinstance(project, dict) or "license-files" not in project:
        return None
    patterns = project.get("license-files")
    if isinstance(patterns, str):
        patterns = [patterns]
    if not isinstance(patterns, list):
        # Malformed; PyprojectTomlValid reports it against the schema.
        return None
    return [str(pattern) for pattern in patterns]


def get_build_data(path: Pathish, isolated: "bool | None" = None) -> Metadata:
    """Return built metadata plus the facts the checks need from the working tree.

    Everything that has to be read from disk is gathered here, so that rating
    stays a pure function of the metadata dict.
    """
    metadata = build_metadata(path, isolated=isolated)
    metadata["_py_typed"], metadata["_stub_only"] = typing_facts(path)
    # Check if there is a pyproject_toml
    if "pyproject.toml" not in {entry.name for entry in Path(path).iterdir()}:
        metadata["_missing_pyproject_toml"] = True
        return metadata

    # Parse it once here, so that no rating test has to touch the disk.
    table, error = read_pyproject(path)
    if table is not None:
        metadata["_pyproject"] = table
        patterns = _license_file_patterns(table)
        if patterns is not None:
            metadata["_license_files_unmatched"] = unmatched_license_files(path, patterns)
    elif error is not None:
        metadata["_pyproject_error"] = error
    return metadata


def get_data(path: Pathish) -> Metadata:
    """Return metadata for a project directory and retain its source path."""
    data = _get_data(path)
    if data:
        # We got something, add the path to it.
        data["_path"] = path
    return data


def _get_data(path: Pathish) -> Metadata:
    listing = {entry.name for entry in Path(path).iterdir()}
    if "pyproject.toml" not in listing and "setup.py" not in listing:
        # No standard tool can build the package without a pyproject.toml
        # or a setup.py. Let's see if there is a setup.cfg:
        if "setup.cfg" in listing:
            # There is only a setup.cfg. Pyroma accepted this earlier,
            # because it worked, and at some point the idea was that
            # setup.cfg should replace setup.py. But that never happened,
            # and instead pyproject.toml arrived. No standard tool can
            # build such a project, so there is no metadata to extract;
            # we just flag the broken build system.
            return {"_missing_build_system": True}
        # There is no setup.cfg either, so this isn't a python package at all
        return {"_no_config_found": True}
    return get_build_data(path)
