"""Verify platform commands and semantic versions in artifact smoke checks."""

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import pyroma
from ci import package_artifacts as module


@pytest.mark.parametrize(
    ("windows", "name", "relative"),
    [
        (False, "python", "bin/python"),
        (False, "pyroma", "bin/pyroma"),
        (True, "python", "Scripts/python.exe"),
        (True, "pyroma", "Scripts/pyroma.exe"),
    ],
)
def test_platform_script_paths(tmp_path: Path, windows: bool, name: str, relative: str) -> None:
    assert module.script_path(tmp_path, name, windows=windows) == tmp_path / relative


@pytest.mark.parametrize("tests", [False, True])
def test_environment_installs_through_its_platform_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tests: bool
) -> None:
    runner = Mock(return_value="")
    monkeypatch.setattr(module.shutil, "which", lambda _name: "uv")
    monkeypatch.setattr(module, "run", runner)
    executable = module.environment(
        tmp_path, "3.11", tmp_path / "package.whl", tmp_path / "pyproject.toml", tests=tests
    )
    name = "test-env" if tests else "runtime-env"
    assert executable == module.script_path(tmp_path / name, "python", windows=os.name == "nt")
    commands = [call.args[0] for call in runner.call_args_list]
    assert commands[0] == ["uv", "venv", "--python", "3.11", str(tmp_path / name)]
    assert "--locked" in commands[1]
    assert ("--group" in commands[1]) == tests
    assert commands[2][commands[2].index("--python") + 1] == str(executable)
    assert "--require-hashes" in commands[2]
    assert commands[3][-2:] == ["--no-deps", str(tmp_path / "package.whl")]


def test_environment_requires_uv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module.shutil, "which", lambda _name: None)
    with pytest.raises(ValueError, match="uv must be installed"):
        module.environment(tmp_path, "3.11", tmp_path / "package.whl", tmp_path / "pyproject.toml", tests=False)


@pytest.mark.parametrize(
    ("installed", "reported", "failure"),
    [
        ("6.2.0-rc1", "6.2.0RC1", ""),
        ("6.2.0", "6.2.0rc1", "Installed version"),
        ("invalid", "6.2.0rc1", "Invalid version"),
        ("6.2.0rc1", "6.2.0", "Installed CLI"),
        ("6.2.0rc1", "invalid", "Invalid version"),
    ],
)
def test_smoke_normalizes_installed_and_reported_versions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, installed: str, reported: str, failure: str
) -> None:
    root = tmp_path / "environment"
    package = root / "lib" / "pyroma"
    package.mkdir(parents=True)
    (package / "py.typed").touch()
    license_file = root / "pyroma621.dist-info" / "licenses" / "LICENSE.txt"
    license_file.parent.mkdir(parents=True)
    license_file.touch()
    distribution = Mock()
    distribution.version = installed
    distribution.locate_file.side_effect = lambda path: root / path
    distribution.files = [Path("pyroma621.dist-info/licenses/LICENSE.txt")]
    distribution.metadata.get_all.return_value = None
    distribution.entry_points = [
        SimpleNamespace(group="console_scripts", name="pyroma", value="pyroma:main"),
        SimpleNamespace(group="zest.releaser.prereleaser.before", name="pyroma", value="pyroma:zester"),
    ]
    runner = Mock(
        side_effect=lambda arguments, _cwd: (
            "" if "--help" in arguments else json.dumps({"rating": 10, "_meta": {"pyroma": reported}})
        )
    )
    monkeypatch.setattr(module.sys, "prefix", str(root))
    monkeypatch.setattr(pyroma, "__file__", str(package / "__init__.py"))
    distribution.locate_file.side_effect = lambda path: (
        package / "__init__.py" if str(path) == "pyroma/__init__.py" else root / path
    )
    monkeypatch.setattr(module.metadata, "distribution", lambda _name: distribution)
    monkeypatch.setattr(module, "run", runner)
    if failure:
        with pytest.raises(ValueError, match=failure):
            module.smoke("6.2.0rc1")
    else:
        module.smoke("6.2.0rc1")
        assert runner.call_count == 4
        assert runner.call_args_list[0].args[0][0] == str(module.script_path(root, "pyroma", windows=os.name == "nt"))
        assert runner.call_args_list[2].args[0] == [module.sys.executable, "-m", "pyroma", "--help"]
