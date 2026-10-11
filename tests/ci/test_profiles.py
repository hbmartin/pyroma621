"""Exercise profile loading before collection through independent pytest runs."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_PROBE = """
import os
from hypothesis import settings

def test_profile_settings():
    assert settings.get_current_profile_name() == os.environ['PYROMA_TEST_PROFILE']
    assert settings.default.max_examples == int(os.environ['PYROMA_TEST_EXAMPLES'])
    assert settings.default.deadline is None
    if os.environ.get('PYROMA_TEST_CI'):
        inherited = settings.get_profile('ci')
        for name in ('derandomize', 'database', 'phases', 'print_blob', 'suppress_health_check'):
            assert getattr(settings.default, name) == getattr(inherited, name)
"""


def profile_project(tmp_path: Path) -> Path:
    root = Path(__file__).resolve().parents[2]
    shutil.copy(root / "conftest.py", tmp_path / "conftest.py")
    config = tmp_path / "pytest.ini"
    config.write_text("[pytest]\naddopts=--strict-config --strict-markers\nfilterwarnings=error\n", encoding="utf-8")
    for directory in ("property", "ci"):
        target = tmp_path / "tests" / directory
        target.mkdir(parents=True)
        (target / "__init__.py").write_text("", encoding="utf-8")
        (target / "test_probe.py").write_text(_PROBE, encoding="utf-8")
    (tmp_path / "tests" / "__init__.py").write_text("", encoding="utf-8")
    return config


def run_profile_probe(
    tmp_path: Path, target: str, profile: str | None, *, ci: bool = False
) -> subprocess.CompletedProcess[str]:
    config = profile_project(tmp_path)
    selected = profile or "property"
    arguments = [sys.executable, "-m", "pytest", "-q", "-c", str(config), target]
    if profile is not None:
        arguments.append(f"--hypothesis-profile={profile}")
    environment = {
        **os.environ,
        "PYROMA_TEST_PROFILE": selected,
        "PYROMA_TEST_EXAMPLES": "1000" if selected == "property-deep" else "100",
        "PYROMA_TEST_CI": "1" if ci else "",
    }
    if ci:
        environment["CI"] = "1"
    return subprocess.run(  # noqa: S603 - fixed pytest probe in an isolated configuration tree
        arguments, cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=30, check=False
    )


@pytest.mark.parametrize("target", [".", "tests/property", "tests/property/test_probe.py", "tests/ci"])
@pytest.mark.parametrize("profile", [None, "property", "property-deep"])
def test_profiles_load_before_collection(tmp_path: Path, target: str, profile: str | None) -> None:
    result = run_profile_probe(tmp_path, target, profile)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("profile", ["property", "property-deep"])
def test_profiles_preserve_inherited_ci_settings(tmp_path: Path, profile: str) -> None:
    result = run_profile_probe(tmp_path, "tests/property", profile, ci=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_unknown_profiles_still_fail(tmp_path: Path) -> None:
    result = run_profile_probe(tmp_path, ".", "pyroma-unknown-profile")
    assert result.returncode != 0
    assert "Profile 'pyroma-unknown-profile' is not registered" in result.stdout + result.stderr
