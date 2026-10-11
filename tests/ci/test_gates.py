"""Ensure CI gates reject incomplete or unsafe inputs."""

import datetime
import io
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from ci.audit_dependencies import evaluate, load_exceptions, locked_requirements
from ci.package_artifacts import inspect

TODAY = datetime.date(2026, 10, 11)


def test_lock_includes_other_platform_versions(tmp_path: Path) -> None:
    lock = tmp_path / "uv.lock"
    lock.write_text(
        """[[package]]
name = "root"
version = "1"
source = { editable = "." }
[[package]]
name = "dependency"
version = "1"
source = { registry = "https://pypi.org/simple" }
resolution-markers = ["sys_platform == 'win32'"]
wheels = [{ hash = "sha256:"""
        + "0" * 64
        + """" }]
[[package]]
name = "dependency"
version = "2"
source = { registry = "https://pypi.org/simple" }
resolution-markers = ["python_version >= '3.14'"]
wheels = [{ hash = "sha256:"""
        + "1" * 64
        + '" }]\n'
    )
    assert set(locked_requirements(lock)) == {("dependency", "1"), ("dependency", "2")}


@pytest.mark.parametrize("source", ['{git="https://example.invalid/repo"}', '{registry="https://pypi.org/simple"}'])
def test_unauditable_or_unhashed_lock_fails(tmp_path: Path, source: str) -> None:
    lock = tmp_path / "uv.lock"
    lock.write_text(f'[[package]]\nname="dependency"\nversion="1"\nsource={source}\n')
    with pytest.raises(ValueError, match=r"source|hashes"):
        locked_requirements(lock)


@pytest.mark.parametrize("expires", ["2026-10-11", "2026-11-20"])
def test_expired_or_overlong_exception_fails(tmp_path: Path, expires: str) -> None:
    path = tmp_path / "exceptions.toml"
    path.write_text(f"""[[exceptions]]
advisory="GHSA-example"
package="dependency"
version="1"
reason="No compatible fix; independently reviewed"
created=2026-10-01
expires={expires}
""")
    with pytest.raises(ValueError, match="exception"):
        load_exceptions(path, TODAY)


def test_exception_requires_reason(tmp_path: Path) -> None:
    path = tmp_path / "exceptions.toml"
    path.write_text('[[exceptions]]\nadvisory="GHSA-example"\npackage="dependency"\nversion="1"\n')
    with pytest.raises(ValueError, match="reason"):
        load_exceptions(path, TODAY)


def test_audit_finding_alias_and_exact_version() -> None:
    report = {
        "dependencies": [
            {"name": "dependency", "version": "1", "vulns": [{"id": "PYSEC-example", "aliases": ["GHSA-example"]}]}
        ]
    }
    expected = {("dependency", "1")}
    assert evaluate(report, expected, []) == ["dependency==1: PYSEC-example"]
    exception = {"advisory": "GHSA-example", "package": "dependency", "version": "1"}
    assert evaluate(report, expected, [exception]) == []
    with pytest.raises(ValueError, match="Unused"):
        evaluate(report, expected, [{**exception, "version": "2"}])


@pytest.mark.parametrize("dependencies", [[], [{"name": "dependency", "version": "1", "skip_reason": "missing"}]])
def test_incomplete_audit_fails(dependencies: list[dict[str, str]]) -> None:
    with pytest.raises(ValueError, match=r"Incomplete|not audited"):
        evaluate({"dependencies": dependencies}, {("dependency", "1")}, [])


def distributions(tmp_path: Path, *, marker: bool = True) -> Path:
    project = tmp_path / "pyproject.toml"
    project.write_text('[project]\nversion="6.1.0"\n')
    with zipfile.ZipFile(tmp_path / "package.whl", "w") as archive:
        for name in ("__init__.py", "__main__.py", "tests.py", *(("py.typed",) if marker else ())):
            archive.writestr("pyroma/" + name, "")
        archive.writestr("pyroma621-6.1.0.dist-info/METADATA", "Name: pyroma621\nVersion: 6.1.0\n")
    with tarfile.open(tmp_path / "package.tar.gz", "w:gz") as archive:
        data = b"Name: pyroma621\nVersion: 6.1.0\n"
        member = tarfile.TarInfo("pyroma621-6.1.0/PKG-INFO")
        member.size = len(data)
        archive.addfile(member, io.BytesIO(data))
    return project


def test_missing_marker_blocks_artifact(tmp_path: Path) -> None:
    project = distributions(tmp_path, marker=False)
    with pytest.raises(ValueError, match="Missing wheel files"):
        inspect(tmp_path, project)


def test_release_version_mismatch_blocks_artifact(tmp_path: Path) -> None:
    project = distributions(tmp_path)
    assert inspect(tmp_path, project, "v6.1.0")[2] == "6.1.0"
    with pytest.raises(ValueError, match="Release tag"):
        inspect(tmp_path, project, "v6.2.0")


@pytest.mark.parametrize("failure", ["marker", "configuration", "warning"])
def test_pytest_strictness_fails_closed(tmp_path: Path, failure: str) -> None:
    config = tmp_path / "pytest.ini"
    config.write_text(
        "[pytest]\naddopts=--strict-config --strict-markers\nfilterwarnings=error\n"
        + ("unknown_setting=true\n" if failure == "configuration" else "")
    )
    source = {
        "marker": "import pytest\n@pytest.mark.unknown_marker\ndef test_probe(): pass\n",
        "warning": "import warnings\ndef test_probe(): warnings.warn('unexpected', stacklevel=1)\n",
        "configuration": "def test_probe(): pass\n",
    }[failure]
    (tmp_path / "test_probe.py").write_text(source)
    result = subprocess.run(  # noqa: S603 - fixed executables and isolated CI probe arguments
        [sys.executable, "-m", "pytest", "-c", str(config), str(tmp_path), "-q"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode != 0
    assert {"marker": "unknown_marker", "configuration": "unknown_setting", "warning": "unexpected"}[
        failure
    ] in result.stdout + result.stderr


def test_stale_lock_fails(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    assert uv is not None
    root = Path(__file__).resolve().parents[2]
    shutil.copy(root / "uv.lock", tmp_path / "uv.lock")
    config = (root / "pyproject.toml").read_text().replace('version = "6.1.0"', 'version = "99.0.0"', 1)
    (tmp_path / "pyproject.toml").write_text(config)
    result = subprocess.run(  # noqa: S603 - fixed executables and isolated CI probe arguments
        [uv, "lock", "--check"], cwd=tmp_path, capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode != 0
    assert "lockfile" in (result.stdout + result.stderr).lower()
