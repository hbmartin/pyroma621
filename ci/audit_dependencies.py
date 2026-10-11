"""Audit every locked registry version, regardless of environment markers."""

import argparse
import datetime
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any


def canonical(name: str) -> str:
    """Normalize distribution names without importing runtime dependencies."""
    return re.sub(r"[-_.]+", "-", name).lower()


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
        key = canonical(package["name"]), package["version"]
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


def load_exceptions(path: Path, today: datetime.date) -> list[dict[str, Any]]:
    """Require specific, explained exceptions with bounded lifetimes."""
    entries = tomllib.loads(path.read_text()).get("exceptions")
    if not isinstance(entries, list):
        message = "exceptions must be a TOML array"
        raise TypeError(message)
    seen = set()
    for entry in entries:
        for field in ("advisory", "package", "version", "reason"):
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                message = f"Exception needs a nonempty {field}"
                raise ValueError(message)
        entry.setdefault("owner", "hbmartin")
        if not isinstance(entry["owner"], str) or not entry["owner"].strip():
            message = "Exception needs an owner"
            raise ValueError(message)
        created, expires = entry.get("created"), entry.get("expires")
        if type(created) is not datetime.date or type(expires) is not datetime.date:
            message = "Exception created/expires must be TOML dates"
            raise ValueError(message)
        if not created <= today < expires or not datetime.timedelta() < expires - created <= datetime.timedelta(
            days=30
        ):
            message = f"Expired, future, or overlong exception: {entry['advisory']}"
            raise ValueError(message)
        key = entry["advisory"], canonical(entry["package"]), entry["version"]
        if key in seen:
            message = f"Duplicate exception: {key}"
            raise ValueError(message)
        seen.add(key)
    return entries


def evaluate(report: dict[str, Any], expected: set[tuple[str, str]], exceptions: list[dict[str, Any]]) -> list[str]:
    """Reject partial reports and return findings lacking a reviewed exception."""
    observed = set()
    blocked = set()
    used = set()
    for dependency in report["dependencies"]:
        key = canonical(dependency["name"]), dependency.get("version")
        if "skip_reason" in dependency or "vulns" not in dependency:
            message = f"Dependency was not audited: {key}"
            raise ValueError(message)
        observed.add(key)
        for finding in dependency["vulns"]:
            ids = {finding["id"], *finding.get("aliases", [])}
            matches = {
                index
                for index, entry in enumerate(exceptions)
                if entry["advisory"] in ids and (canonical(entry["package"]), entry["version"]) == key
            }
            used.update(matches)
            if not matches:
                blocked.add(f"{key[0]}=={key[1]}: {finding['id']}")
    if observed != expected:
        message = f"Incomplete audit: missing={expected - observed}, unexpected={observed - expected}"
        raise ValueError(message)
    if used != set(range(len(exceptions))):
        message = "Unused audit exception; remove it rather than carrying it forward"
        raise ValueError(message)
    return sorted(blocked)


def audit(lock: Path, exceptions_path: Path, report_path: Path) -> None:
    """Run the audit without allowing failures to become successful reports."""
    requirements = locked_requirements(lock)
    exceptions = load_exceptions(exceptions_path, datetime.datetime.now(datetime.UTC).date())
    requirements_path = report_path.with_suffix(".requirements.txt")
    requirements_path.write_text(
        "\n".join(
            f"{name}=={version} " + " ".join(f"--hash={digest}" for digest in sorted(hashes))
            for (name, version), hashes in sorted(requirements.items())
        )
        + "\n"
    )
    report_path.unlink(missing_ok=True)
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
        check=False,
    )
    if result.returncode not in (0, 1):
        message = f"Audit service/tool failed with status {result.returncode}"
        raise ValueError(message)
    report = json.loads(report_path.read_text())
    blocked = evaluate(report, set(requirements), exceptions)
    if result.returncode and not any(item["vulns"] for item in report["dependencies"]):
        message = "Audit failed without reporting vulnerabilities"
        raise ValueError(message)
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
        audit(args.lock, args.exceptions, args.report)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"Audit failed: {error}\n")


if __name__ == "__main__":
    main()
