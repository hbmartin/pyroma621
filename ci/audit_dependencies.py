"""Audit every locked registry version, regardless of environment markers."""

import argparse
import json
import re
import subprocess
import sys
import tomllib
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from packaging.utils import canonicalize_name


def locked_requirements(lock: Path) -> dict[tuple[str, str], set[str]]:
    """Collect every version and its artifact hashes; reject unauditable sources."""
    entries: dict[tuple[str, str], set[str]] = {}
    for package in tomllib.loads(lock.read_text())["package"]:
        source = package["source"]
        if source in ({"editable": "."}, {"virtual": "."}):
            continue
        if set(source) != {"registry"}:
            message = f"Unsupported audit source for {package['name']}: {source}"
            raise ValueError(message)
        key = canonicalize_name(package["name"]), package["version"]
        artifacts = [*package.get("wheels", []), *([package["sdist"]] if "sdist" in package else [])]
        hashes = {artifact.get("hash", "") for artifact in artifacts}
        if not hashes or any(not re.fullmatch(r"sha256:[0-9a-f]{64}", digest) for digest in hashes):
            message = f"Missing or invalid artifact hashes for {key}"
            raise ValueError(message)
        entries.setdefault(key, set()).update(hashes)
    if not entries:
        message = "The lockfile contains no auditable registry dependencies"
        raise ValueError(message)
    return entries


def load_exceptions(path: Path, today: date) -> list[dict[str, Any]]:
    """Require specific, explained exceptions with bounded lifetimes."""
    entries = tomllib.loads(path.read_text()).get("exceptions")
    if not isinstance(entries, list):
        message = "exceptions must be a TOML array"
        raise TypeError(message)
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            message = "Each audit exception must be a TOML table."
            raise TypeError(message)
        for field in ("advisory", "package", "version", "reason"):
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                message = f"Exception needs a nonempty {field}"
                raise ValueError(message)
        entry.setdefault("owner", "hbmartin")
        if not isinstance(entry["owner"], str) or not entry["owner"].strip():
            message = "Exception needs an owner"
            raise ValueError(message)
        created, expires = entry.get("created"), entry.get("expires")
        if type(created) is not date or type(expires) is not date:
            message = "Exception created/expires must be TOML dates"
            raise ValueError(message)
        if not created <= today < expires or not timedelta() < expires - created <= timedelta(days=30):
            message = f"Expired, future, or overlong exception: {entry['advisory']}"
            raise ValueError(message)
        key = entry["advisory"], canonicalize_name(entry["package"]), entry["version"]
        if key in seen:
            message = f"Duplicate exception: {key}"
            raise ValueError(message)
        seen.add(key)
    return entries


def validate_findings(findings: object, key: tuple[str, str]) -> None:
    """Reject malformed vulnerability identifiers and aliases."""
    if not isinstance(findings, list):
        message = f"Malformed audit vulnerabilities: {key}"
        raise TypeError(message)
    for finding in findings:
        if (
            not isinstance(finding, dict)
            or not isinstance(finding.get("id"), str)
            or not finding["id"]
            or not isinstance(finding.get("aliases", []), list)
            or any(not isinstance(alias, str) for alias in finding.get("aliases", []))
        ):
            message = f"Malformed audit vulnerability: {key}"
            raise TypeError(message)


def validate_report(report: object, expected: set[tuple[str, str]]) -> None:
    """Require a well-formed report covering each expected dependency once."""
    if not isinstance(report, dict) or not isinstance(report.get("dependencies"), list):
        message = "Malformed audit report: dependencies must be an array"
        raise TypeError(message)
    if not isinstance(report.get("fixes", []), list):
        message = "Malformed audit report: fixes must be an array"
        raise TypeError(message)
    observed = set()
    for dependency in report["dependencies"]:
        if not isinstance(dependency, dict) or not isinstance(dependency.get("name"), str):
            message = "Malformed audit dependency"
            raise TypeError(message)
        name = canonicalize_name(dependency["name"])
        if "skip_reason" in dependency or "vulns" not in dependency:
            message = f"Dependency was not audited: {(name, dependency.get('version'))}"
            raise ValueError(message)
        if not isinstance(dependency.get("version"), str):
            message = f"Malformed audit dependency: {name}"
            raise TypeError(message)
        key = name, dependency["version"]
        if key in observed:
            message = f"Duplicate audit dependency: {key}"
            raise ValueError(message)
        observed.add(key)
        validate_findings(dependency["vulns"], key)
    if observed != expected:
        message = f"Incomplete audit: missing={expected - observed}, unexpected={observed - expected}"
        raise ValueError(message)


def evaluate(report: dict[str, Any], expected: set[tuple[str, str]], exceptions: list[dict[str, Any]]) -> list[str]:
    """Reject partial reports and return findings lacking a reviewed exception."""
    validate_report(report, expected)
    blocked = set()
    used = set()
    for dependency in report["dependencies"]:
        key = canonicalize_name(dependency["name"]), dependency["version"]
        for finding in dependency["vulns"]:
            ids = {finding["id"], *finding.get("aliases", [])}
            matches = {
                index
                for index, entry in enumerate(exceptions)
                if entry["advisory"] in ids and (canonicalize_name(entry["package"]), entry["version"]) == key
            }
            used.update(matches)
            if not matches:
                blocked.add(f"{key[0]}=={key[1]}: {finding['id']}")
    if used != set(range(len(exceptions))):
        message = "Unused audit exception; remove it rather than carrying it forward"
        if blocked:
            message += "\nUnaccepted vulnerabilities:\n" + "\n".join(sorted(blocked))
        raise ValueError(message)
    return sorted(blocked)


def requirement_batches(
    requirements: dict[tuple[str, str], set[str]],
) -> list[dict[tuple[str, str], set[str]]]:
    """Assign each name's sorted versions to separate, deterministic batches."""
    batches: list[dict[tuple[str, str], set[str]]] = []
    positions: dict[str, int] = {}
    for key, hashes in sorted(requirements.items()):
        index = positions.get(key[0], 0)
        if index == len(batches):
            batches.append({})
        batches[index][key] = hashes
        positions[key[0]] = index + 1
    return batches


def requirement_text(requirements: dict[tuple[str, str], set[str]]) -> str:
    """Serialize hashed pins for an inventory or a conflict-free batch."""
    return (
        "\n".join(
            f"{name}=={version} " + " ".join(f"--hash={digest}" for digest in sorted(hashes))
            for (name, version), hashes in sorted(requirements.items())
        )
        + "\n"
    )


def read_batch_report(report_path: Path, expected: set[tuple[str, str]], status: int) -> dict[str, Any]:
    """Require a complete batch report and a consistent exit status."""
    if status not in (0, 1):
        message = f"Unexpected audit status {status}"
        raise ValueError(message)
    if not report_path.is_file():
        message = "Audit did not produce a report"
        raise ValueError(message)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    validate_report(report, expected)
    if status and not any(item["vulns"] for item in report["dependencies"]):
        message = "Audit failed without reporting vulnerabilities"
        raise ValueError(message)
    return report


def audit_batch(requirements_path: Path, report_path: Path, expected: set[tuple[str, str]]) -> dict[str, Any]:
    """Audit one conflict-free batch, retaining diagnostics on any failure."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip_audit",
            "--strict",
            "--disable-pip",
            "--no-deps",
            "--require-hashes",
            "-r",
            str(requirements_path),
            "--format",
            "json",
            "--output",
            str(report_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        report = read_batch_report(report_path, expected, result.returncode)
    except (OSError, ValueError, TypeError) as error:
        diagnostic = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
        message = f"Audit batch failed (status {result.returncode}): {error}"
        if diagnostic:
            message += "\n" + diagnostic
        raise ValueError(message) from error
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    return report


def audit(lock: Path, exceptions_path: Path, report_path: Path, *, today: date) -> None:
    """Audit all versions and publish only a complete merged report."""
    inventory_path = report_path.with_suffix(".requirements.txt")
    report_path.unlink(missing_ok=True)
    inventory_path.unlink(missing_ok=True)
    requirements = locked_requirements(lock)
    exceptions = load_exceptions(exceptions_path, today)
    inventory_path.write_text(requirement_text(requirements), encoding="utf-8")
    merged: dict[str, Any] = {"dependencies": [], "fixes": []}
    with TemporaryDirectory(prefix="pyroma-audit-") as directory:
        for index, batch in enumerate(requirement_batches(requirements)):
            batch_requirements = Path(directory) / f"batch-{index}.requirements.txt"
            batch_report = Path(directory) / f"batch-{index}.json"
            batch_requirements.write_text(requirement_text(batch), encoding="utf-8")
            report = audit_batch(batch_requirements, batch_report, set(batch))
            merged["dependencies"].extend(report["dependencies"])
            merged["fixes"].extend(report.get("fixes", []))
    merged["dependencies"].sort(key=lambda item: (canonicalize_name(item["name"]), item["version"]))
    validate_report(merged, set(requirements))
    report_path.write_text(json.dumps(merged), encoding="utf-8")
    blocked = evaluate(merged, set(requirements), exceptions)
    if blocked:
        message = "Unaccepted vulnerabilities:\n" + "\n".join(blocked)
        raise ValueError(message)
    print(f"Audited {len(requirements)} locked package versions; {len(exceptions)} reviewed exceptions")


def main() -> None:
    """Write a complete audit report and fail closed on findings or errors."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=Path("uv.lock"))
    parser.add_argument("--exceptions", type=Path, default=Path("audit-exceptions.toml"))
    parser.add_argument("--report", type=Path, default=Path("audit-report.json"))
    args = parser.parse_args()
    try:
        audit(args.lock, args.exceptions, args.report, today=datetime.now(UTC).date())
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"Audit failed: {error}\n")


if __name__ == "__main__":
    main()
