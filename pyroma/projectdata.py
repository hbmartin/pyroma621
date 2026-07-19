"""Extract and normalize package metadata from a local project directory."""

# Kept at runtime so the public Pathish alias resolves through get_type_hints.
import os  # noqa: TC003
import tomllib
from email.message import Message
from pathlib import Path
from typing import Any, Union, cast

import build.util

import build
from pyroma.metadata import Metadata, normalize

Pathish = Union[str, "os.PathLike[str]"]


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
    except build.BuildBackendException:
        # The backend failed spectacularily. This happens with old packages,
        # when we can't build a wheel. It's not always a fatal error. F ex, if
        # you are getting info for a package from PyPI, we already have the
        # metadata from PyPI, we just couldn't get the additional build data.
        return {"_wheel_build_failed": True}

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

    Returns a ``(table, error)`` pair: exactly one of the two is set. The
    error is a plain string rather than an exception so that metadata stays
    plain data and rating remains a pure function of it.
    """
    pyproject_path = Path(path) / "pyproject.toml"
    try:
        with pyproject_path.open("rb") as pyproject_file:
            return tomllib.load(pyproject_file), None
    except (OSError, tomllib.TOMLDecodeError) as error:
        return None, str(error)


def get_build_data(path: Pathish, isolated: "bool | None" = None) -> Metadata:
    """Return built metadata plus project build-system sentinels."""
    metadata = build_metadata(path, isolated=isolated)
    # Check if there is a pyproject_toml
    if "pyproject.toml" not in {entry.name for entry in Path(path).iterdir()}:
        metadata["_missing_pyproject_toml"] = True
        return metadata

    # Parse it once here, so that no rating test has to touch the disk.
    table, error = read_pyproject(path)
    if table is not None:
        metadata["_pyproject"] = table
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
