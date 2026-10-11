"""Exercise conflict-free auditing and complete, controlled failures."""

import datetime
import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from ci import audit_dependencies as module

PINS = [("a-pkg", "1"), ("a-pkg", "2"), ("a-pkg", "3"), ("b", "1"), ("b", "2"), ("single", "1")]


@pytest.fixture
def audit_files(tmp_path: Path) -> tuple[Path, Path, Path]:
    lock = tmp_path / "uv.lock"
    lock.write_text(
        "".join(
            f'[[package]]\nname="{name.replace("-", "_").upper()}"\nversion="{version}"\n'
            'source={registry="https://pypi.org/simple"}\nwheels=[{hash="sha256:' + str(index) * 64 + '"}]\n'
            for index, (name, version) in enumerate(reversed(PINS))
        ),
        encoding="utf-8",
    )
    exceptions = tmp_path / "exceptions.toml"
    exceptions.write_text("exceptions=[]\n", encoding="utf-8")
    return lock, exceptions, tmp_path / "audit-report.json"


def batch_pins(arguments: list[str]) -> list[tuple[str, str]]:
    source = Path(arguments[arguments.index("-r") + 1]).read_text()
    return [tuple(line.split()[0].split("==")) for line in source.splitlines()]


def batch_document(pins: list[tuple[str, str]]) -> dict[str, Any]:
    return {"dependencies": [{"name": name, "version": version, "vulns": []} for name, version in pins], "fixes": []}


def write_exception(path: Path, version: str, advisory: str) -> None:
    today = datetime.datetime.now(datetime.UTC).date()
    with path.open("a", encoding="utf-8") as stream:
        stream.write(
            f'[[exceptions]]\nadvisory="{advisory}"\npackage="A.PKG"\nversion="{version}"\n'
            f'reason="Reviewed"\ncreated={today}\nexpires={today + datetime.timedelta(days=1)}\n'
        )


def test_audit_batches_every_version_once_and_evaluates_exceptions_globally(
    audit_files: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    lock, exceptions, report = audit_files
    exceptions.write_text("", encoding="utf-8")
    for version in ("1", "2"):
        write_exception(exceptions, version, f"GHSA-{version}")
    report.write_text("stale", encoding="utf-8")
    seen = []
    directories = []

    def run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs == {"capture_output": True, "text": True, "check": False}
        assert not report.exists()
        assert {"--strict", "--disable-pip", "--no-deps", "--require-hashes"} <= set(arguments)
        pins = batch_pins(arguments)
        assert len({name for name, _ in pins}) == len(pins)
        seen.append(pins)
        output = Path(arguments[arguments.index("--output") + 1])
        directories.append(output.parent)
        document = batch_document(pins)
        for dependency in document["dependencies"]:
            if dependency["name"] == "a-pkg" and dependency["version"] in {"1", "2"}:
                dependency["vulns"] = [
                    {"id": "PYSEC-" + dependency["version"], "aliases": ["GHSA-" + dependency["version"]]}
                ]
        output.write_text(json.dumps(document), encoding="utf-8")
        status = int(any(item["vulns"] for item in document["dependencies"]))
        return subprocess.CompletedProcess(arguments, status, "", "audit summary")

    monkeypatch.setattr(module.subprocess, "run", run)
    module.audit(lock, exceptions, report)
    assert seen == [[("a-pkg", "1"), ("b", "1"), ("single", "1")], [("a-pkg", "2"), ("b", "2")], [("a-pkg", "3")]]
    assert [pin for batch in seen for pin in batch] == [PINS[index] for index in (0, 3, 5, 1, 4, 2)]
    document = json.loads(report.read_text())
    assert set(document) == {"dependencies", "fixes"}
    assert [(item["name"], item["version"]) for item in document["dependencies"]] == PINS
    assert document["fixes"] == []
    inventory = report.with_suffix(".requirements.txt").read_text()
    assert inventory == module.requirement_text(module.locked_requirements(lock))
    assert all(not directory.exists() for directory in directories)
    assert "Audited 6 locked package versions; 2 reviewed exceptions" in capsys.readouterr().out


@pytest.mark.parametrize(
    "failure",
    ["status", "missing", "json", "shape", "partial", "duplicate", "skipped", "no-vulnerabilities", "vulnerability"],
)
def test_fatal_batch_removes_stale_report_and_preserves_diagnostics(
    audit_files: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    lock, exceptions, report = audit_files
    report.write_text("stale", encoding="utf-8")
    calls = []

    def run(arguments: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        output = Path(arguments[arguments.index("--output") + 1])
        calls.append(output)
        document = batch_document(batch_pins(arguments))
        if failure == "partial":
            document["dependencies"].pop()
        elif failure == "duplicate":
            document["dependencies"].append(document["dependencies"][0])
        elif failure == "skipped":
            document["dependencies"][0]["skip_reason"] = "unavailable"
        elif failure == "vulnerability":
            document["dependencies"][0]["vulns"] = [{"id": "NEW", "aliases": 1}]
        if failure != "missing":
            content = "{" if failure == "json" else "[]" if failure == "shape" else json.dumps(document)
            output.write_text(content, encoding="utf-8")
        status = 2 if failure == "status" else int(failure in {"missing", "no-vulnerabilities"})
        return subprocess.CompletedProcess(arguments, status, "collector output", "service diagnostic")

    monkeypatch.setattr(module.subprocess, "run", run)
    with pytest.raises(ValueError, match="Audit batch failed") as error:
        module.audit(lock, exceptions, report)
    assert "collector output" in str(error.value)
    assert "service diagnostic" in str(error.value)
    assert len(calls) == 1
    assert not calls[0].parent.exists()
    assert not report.exists()
    assert report.with_suffix(".requirements.txt").is_file()


def test_later_batch_failure_never_publishes_partial_report(
    audit_files: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    lock, exceptions, report = audit_files
    calls = []

    def run(arguments: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        if len(calls) == 1:
            output = Path(arguments[arguments.index("--output") + 1])
            output.write_text(json.dumps(batch_document(batch_pins(arguments))), encoding="utf-8")
            return subprocess.CompletedProcess(arguments, 0, "", "")
        return subprocess.CompletedProcess(arguments, 1, "", "connection failed")

    monkeypatch.setattr(module.subprocess, "run", run)
    with pytest.raises(ValueError, match="connection failed"):
        module.audit(lock, exceptions, report)
    assert len(calls) == 2
    assert not report.exists()


def test_unused_exception_reports_new_findings_together() -> None:
    document = batch_document([("a-pkg", "1"), ("a-pkg", "2")])
    for dependency in document["dependencies"]:
        dependency["vulns"] = [{"id": "NEW-" + dependency["version"]}]
    with pytest.raises(ValueError, match="Unused audit exception") as error:
        module.evaluate(
            document, {("a-pkg", "1"), ("a-pkg", "2")}, [{"advisory": "OLD", "package": "a_pkg", "version": "1"}]
        )
    assert "a-pkg==1: NEW-1\na-pkg==2: NEW-2" in str(error.value)


def test_fatal_audit_main_uses_controlled_diagnostic(
    audit_files: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    lock, exceptions, report = audit_files
    runner = Mock(return_value=subprocess.CompletedProcess([], 1, "", "OSV unreachable"))
    monkeypatch.setattr(module.subprocess, "run", runner)
    monkeypatch.setattr(
        sys, "argv", ["audit", "--lock", str(lock), "--exceptions", str(exceptions), "--report", str(report)]
    )
    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code == 1
    diagnostic = capsys.readouterr().err
    assert "Audit failed:" in diagnostic
    assert "did not produce a report" in diagnostic
    assert "OSV unreachable" in diagnostic
    assert "FileNotFoundError" not in diagnostic


@pytest.mark.parametrize(
    "report", [None, {"dependencies": None}, {"dependencies": [None]}, {"dependencies": [], "fixes": None}]
)
def test_malformed_report_shapes_fail_controlled(report: object) -> None:
    with pytest.raises(TypeError, match="Malformed"):
        module.validate_report(report, set())
