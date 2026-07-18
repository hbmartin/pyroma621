# Extracts information from a project
import configparser
import importlib.metadata
import os
import pathlib
import re
import tempfile
from email.message import Message
from typing import cast

import build
import build.env
import pyproject_hooks

from pyroma._types import Metadata

# MAP from old setup.py type keys to Core Metadata keys
METADATA_MAP = {
    "description": "summary",
    "classifiers": "classifier",
    "project-urls": "project-url",
    "url": "home-page",
    "long-description": "description",
    "long-description-content-type": "description-content-type",
    "python-requires": "requires-python",
}


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _metadata_from_builder(builder: build.ProjectBuilder) -> Message:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = pathlib.Path(builder.metadata_path(tmpdir))
        # The runtime type is an email.message.Message subclass.
        return cast(Message, importlib.metadata.PathDistribution(path).metadata)


def _project_wheel_metadata(path: "os.PathLike[str] | str", isolated: bool) -> Message:
    """Get the wheel metadata for a project, via its PEP 517 build backend.

    Equivalent to (and adapted from) the deprecated
    ``build.util.project_wheel_metadata()``.
    """
    if isolated:
        with build.env.DefaultIsolatedEnv() as env:
            builder = build.ProjectBuilder.from_isolated_env(
                env,
                path,
                runner=pyproject_hooks.quiet_subprocess_runner,
            )
            env.install(builder.build_system_requires)
            env.install(builder.get_requires_for_build("wheel"))
            return _metadata_from_builder(builder)
    else:
        builder = build.ProjectBuilder(
            path,
            runner=pyproject_hooks.quiet_subprocess_runner,
        )
        return _metadata_from_builder(builder)


def wheel_metadata(path: "os.PathLike[str] | str", isolated: bool | None = None) -> Message:
    # If explictly specified whether to use isolation, pass it directly
    if isolated is not None:
        return _project_wheel_metadata(path, isolated=isolated)

    # Otherwise, try without build isolation first for efficiency
    try:
        return _project_wheel_metadata(path, isolated=False)
    # If building with build isolation fails, e.g. missing build deps, try with it
    except (build.BuildException, build.BuildBackendException):
        return _project_wheel_metadata(path, isolated=True)


def build_metadata(path: "os.PathLike[str] | str", isolated: bool | None = None) -> Metadata:
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
    data: Metadata = {}
    for key in set(metadata.keys()):
        value: list | str = cast(list, metadata.get_all(key))
        key = normalize(key)

        if len(value) == 1:
            value = value[0]
            if value.strip() == "UNKNOWN":
                # XXX This is also old behavior that may not happen any more.
                continue

        data[key] = value

    if "description" not in data.keys():
        # XXX I *think* having the description as a payload doesn't happen anymore, but I haven't checked.
        # Having the description as a payload tends to add two newlines, we clean that up here:
        description = cast(str, metadata.get_payload()).strip()
        if description:
            data["description"] = description + "\n"
    return data


def get_build_data(path: "os.PathLike[str] | str", isolated: bool | None = None) -> Metadata:
    metadata = build_metadata(path, isolated=isolated)
    # Check if there is a pyproject_toml
    if "pyproject.toml" not in os.listdir(path):
        metadata["_missing_pyproject_toml"] = True
    return metadata


def _expand_setupcfg_value(path: "os.PathLike[str] | str", value: str):
    if value.startswith("file:"):
        # A "file: README.rst[, CHANGES.txt ...]" directive: concatenate the
        # referenced files, like setuptools would.
        parts = []
        for filename in value[len("file:") :].split(","):
            file = pathlib.Path(path) / filename.strip()
            try:
                parts.append(file.read_text(encoding="utf-8"))
            except OSError:
                pass
        return "\n".join(parts)
    if "\n" in value:
        # A dangling list of values, for example classifiers or keywords.
        return [item.strip() for item in value.splitlines() if item.strip()]
    return value


def get_setupcfg_data(path: "os.PathLike[str] | str") -> Metadata:
    """Read metadata from setup.cfg with the standard library only.

    This is the legacy fallback for projects that have neither a
    pyproject.toml nor a setup.py, so no build backend can be invoked. Plain
    values, dangling lists and simple ``file:`` directives are supported;
    setuptools-specific ``attr:`` directives are left unexpanded. That is
    enough to rate such projects, which are flagged as broken by the
    build-system checks anyway.
    """
    setupcfg = pathlib.Path(path) / "setup.cfg"
    if not setupcfg.is_file():
        raise FileNotFoundError(str(setupcfg))

    parser = configparser.ConfigParser()
    parser.read(setupcfg, encoding="utf-8")

    metadata: Metadata = {}
    # Python requires is under "options" in setup.cfg (and so are other
    # requirements, but those are optional and have no tests)
    if parser.has_option("options", "python_requires"):
        metadata["requires-python"] = parser.get("options", "python_requires")

    if parser.has_section("metadata"):
        for key, value in parser.items("metadata"):
            key = normalize(key)
            if key in METADATA_MAP:
                key = METADATA_MAP[key]
            metadata[key] = _expand_setupcfg_value(path, value)

    return metadata


def get_data(path: "os.PathLike[str] | str") -> Metadata:
    data = _get_data(path)
    if data:
        # We got something, add the path to it.
        data["_path"] = path
    return data


def _get_data(path: "os.PathLike[str] | str") -> Metadata:
    try:
        return get_build_data(path)
    except build.BuildException as e:
        if "no pyproject.toml or setup.py" in e.args[0]:
            # It couldn't build the package, because there is no setup.py or pyproject.toml.
            # Let's see if there is a setup.cfg:
            try:
                metadata = get_setupcfg_data(path)
                # Yes, there's a setup.cfg. Pyroma accepted this earlier, because it worked,
                # and at some point the idea was that that setup.cfg should replace setup.py.
                # But that never happened, and instead pyproject.toml arrived.
                metadata["_missing_build_system"] = True
                return metadata
            except FileNotFoundError:
                # There is no setup.cfg either, so this isn't a python package at all
                return {"_no_config_found": True}
        else:
            # There's something else wrong
            raise e
