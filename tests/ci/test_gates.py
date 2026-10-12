"""Ensure CI gates reject incomplete or unsafe inputs."""

import datetime
import io
import shutil
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path
from unittest.mock import Mock

import pytest

from ci import audit_dependencies
from ci.audit_dependencies import evaluate, load_exceptions, locked_requirements
from ci.package_artifacts import inspect

TODAY = datetime.date(2026, 10, 11)
_VALID_EXCEPTION = """[[exceptions]]
advisory="GHSA-example"
package="dependency"
version="1"
reason="No compatible fix; independently reviewed"
created=2026-10-01
expires=2026-10-20
"""


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


@pytest.mark.parametrize(
    "entries",
    [
        "[1]",
        '["bad"]',
        "[true]",
        "[[1]]",
        '[{advisory="GHSA-example", package="dependency", version="1", reason="Reviewed", '
        "created=2026-10-01, expires=2026-10-20}, 1]",
    ],
    ids=["integer", "string", "boolean", "array", "mixed"],
)
def test_exception_entries_must_be_tables(tmp_path: Path, entries: str) -> None:
    path = tmp_path / "exceptions.toml"
    path.write_text(f"exceptions={entries}\n", encoding="utf-8")
    with pytest.raises(TypeError, match=r"^Each audit exception must be a TOML table\.$"):
        load_exceptions(path, TODAY)


@pytest.mark.parametrize(("owner_field", "expected"), [("", "hbmartin"), ('owner="maintainer"\n', "maintainer")])
def test_exception_accepts_default_and_explicit_owners(tmp_path: Path, owner_field: str, expected: str) -> None:
    path = tmp_path / "exceptions.toml"
    path.write_text(_VALID_EXCEPTION + owner_field, encoding="utf-8")
    entries = load_exceptions(path, TODAY)
    assert len(entries) == 1
    assert entries[0]["owner"] == expected


@pytest.mark.parametrize("owner", ['" "', "1", "true"])
def test_exception_rejects_invalid_owners(tmp_path: Path, owner: str) -> None:
    path = tmp_path / "exceptions.toml"
    path.write_text(_VALID_EXCEPTION + f"owner={owner}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Exception needs an owner"):
        load_exceptions(path, TODAY)


def test_malformed_exception_uses_controlled_main_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    lock = tmp_path / "uv.lock"
    lock.write_text(
        '[[package]]\nname="dependency"\nversion="1"\nsource={registry="https://pypi.org/simple"}\n'
        'wheels=[{hash="sha256:' + "0" * 64 + '"}]\n',
        encoding="utf-8",
    )
    exceptions = tmp_path / "exceptions.toml"
    exceptions.write_text("exceptions=[1]\n", encoding="utf-8")
    report = tmp_path / "audit-report.json"
    report.write_text("stale", encoding="utf-8")
    report.with_suffix(".requirements.txt").write_text("stale inventory", encoding="utf-8")
    runner = Mock(side_effect=AssertionError("Unexpected audit subprocess"))
    monkeypatch.setattr(audit_dependencies.subprocess, "run", runner)
    monkeypatch.setattr(
        sys,
        "argv",
        ["audit_dependencies", "--lock", str(lock), "--exceptions", str(exceptions), "--report", str(report)],
    )
    with pytest.raises(SystemExit) as failure:
        audit_dependencies.main()
    assert failure.value.code == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "Audit failed: Each audit exception must be a TOML table.\n"
    runner.assert_not_called()
    assert not report.exists()
    assert not report.with_suffix(".requirements.txt").exists()


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


def distributions(
    tmp_path: Path,
    *,
    marker: bool = True,
    version: str = "6.1.0",
    wheel_version: str | None = None,
    source_version: str | None = None,
) -> Path:
    project = tmp_path / "pyproject.toml"
    project.write_text(f'[project]\nversion="{version}"\n')
    with zipfile.ZipFile(tmp_path / "package.whl", "w") as archive:
        for name in ("__init__.py", "__main__.py", "tests.py", *(("py.typed",) if marker else ())):
            archive.writestr("pyroma/" + name, "")
        archive.writestr(
            "pyroma621-6.1.0.dist-info/METADATA", f"Name: pyroma621\nVersion: {wheel_version or version}\n"
        )
    with tarfile.open(tmp_path / "package.tar.gz", "w:gz") as archive:
        data = f"Name: pyroma621\nVersion: {source_version or version}\n".encode()
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
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    project = tmp_path / "pyproject.toml"
    config = f'[project]\nname="lock-probe"\nversion="{version}"\nrequires-python=">=3.11"\n'
    project.write_text(config)
    command = [
        uv,
        "lock",
        "--offline",
        "--no-python-downloads",
        "--cache-dir",
        str(tmp_path / "cache"),
        "--python",
        sys.executable,
    ]
    subprocess.run(  # noqa: S603 - fixed executable and isolated offline lock probe
        command, cwd=tmp_path, capture_output=True, text=True, timeout=30, check=True
    )
    lock = (tmp_path / "uv.lock").read_bytes()
    replacement = "99.0.0" if version != "99.0.0" else "99.0.1"
    changed = config.replace(f'version="{version}"', f'version="{replacement}"', 1)
    assert changed != config
    project.write_text(changed)
    result = subprocess.run(  # noqa: S603 - fixed executables and isolated CI probe arguments
        [*command, "--check"], cwd=tmp_path, capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode != 0
    assert "lockfile" in result.stderr.lower()
    assert "needs to be updated" in result.stderr
    assert (tmp_path / "uv.lock").read_bytes() == lock


@pytest.mark.parametrize("tag", ["v6.2.0-rc1", "6.2.0rc1", "v6.2.0RC1"])
def test_artifact_versions_accept_equivalent_prereleases(tmp_path: Path, tag: str) -> None:
    project = distributions(tmp_path, version="6.2.0-rc1", wheel_version="6.2.0rc1", source_version="6.2.0RC1")
    assert inspect(tmp_path, project, tag)[2] == "6.2.0rc1"


@pytest.mark.parametrize("tag", ["", "6.2", "v6.2", "v6.2.0", "v6.2.0.0"])
def test_artifact_versions_accept_equivalent_release_components(tmp_path: Path, tag: str) -> None:
    project = distributions(tmp_path, version="6.2.0")
    assert inspect(tmp_path, project, tag)[2] == "6.2.0"


@pytest.mark.parametrize("tag", ["vfoo", "invalid", "V6.2.0", "vv6.2.0", "vV6.2.0", " 6.2.0", "6.2.0 ", "v6.2.0\n"])
def test_release_tags_require_one_optional_lowercase_prefix(tmp_path: Path, tag: str) -> None:
    project = distributions(tmp_path, version="6.2.0")
    with pytest.raises(ValueError, match="Release tag"):
        inspect(tmp_path, project, tag)


@pytest.mark.parametrize("field", ["project", "wheel", "source"])
def test_invalid_artifact_versions_fail(tmp_path: Path, field: str) -> None:
    project = distributions(
        tmp_path,
        version="invalid" if field == "project" else "6.1.0",
        wheel_version="invalid" if field == "wheel" else None,
        source_version="invalid" if field == "source" else None,
    )
    with pytest.raises(ValueError, match="Invalid version"):
        inspect(tmp_path, project)


def test_release_tag_with_invalid_version_reports_the_version_error(tmp_path: Path) -> None:
    project = distributions(tmp_path)
    with pytest.raises(ValueError, match="Invalid version"):
        inspect(tmp_path, project, "v6.foo")


@pytest.mark.parametrize("field", ["wheel", "source"])
def test_artifact_metadata_version_mismatch_fails(tmp_path: Path, field: str) -> None:
    project = distributions(
        tmp_path,
        wheel_version="6.2.0" if field == "wheel" else None,
        source_version="6.2.0" if field == "source" else None,
    )
    with pytest.raises(ValueError, match="name/version"):
        inspect(tmp_path, project)


def add_archive_member(tmp_path: Path, archive_type: str, name: str) -> None:
    if archive_type == "wheel":
        with zipfile.ZipFile(tmp_path / "package.whl", "a") as archive:
            archive.writestr("pyroma/" + name, "fixture")
    else:
        path = tmp_path / "package.tar.gz"
        with tarfile.open(path) as archive:
            entries = [(member, archive.extractfile(member).read()) for member in archive.getmembers()]
        with tarfile.open(path, "w:gz") as archive:
            for member, data in entries:
                archive.addfile(member, io.BytesIO(data))
            member = tarfile.TarInfo("pyroma621-6.1.0/pyroma/" + name)
            member.size = len(b"fixture")
            archive.addfile(member, io.BytesIO(b"fixture"))


@pytest.mark.parametrize("archive_type", ["wheel", "source"])
@pytest.mark.parametrize(
    "name",
    [
        "testdata/example.egg-info/PKG-INFO",
        "__pycache__/x.pyc",
        "cache.pyc",
        "cache.pyo",
        ".DS_Store",
        "build/x.py",
        "dist/x.whl",
    ],
)
def test_generated_members_block_artifacts(tmp_path: Path, archive_type: str, name: str) -> None:
    project = distributions(tmp_path)
    add_archive_member(tmp_path, archive_type, name)
    with pytest.raises(ValueError, match="Generated files") as failure:
        inspect(tmp_path, project)
    assert name in str(failure.value)


def test_intentional_distribution_fixtures_remain_allowed(tmp_path: Path) -> None:
    project = distributions(tmp_path)
    for archive_type in ("wheel", "source"):
        add_archive_member(tmp_path, archive_type, "testdata/distributions/complete-1.0.tar.gz")
    inspect(tmp_path, project)
