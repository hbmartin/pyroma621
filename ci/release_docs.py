"""Check release documentation and prepare downloadable JSON Schemas."""

import argparse
import datetime
import json
import re
import shutil
import tomllib
from pathlib import Path
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import urlopen

from packaging.version import Version

SITE_URL = "https://hbmartin.github.io/pyroma621/"
REPOSITORY = "https://github.com/hbmartin/pyroma621"
RELEASE_DATES = {"6.0.0": "2026-08-01", "6.1.0": "2026-10-12"}
_RESPONSE_LIMIT = 2 * 1024 * 1024


def changelog_entries(text: str) -> dict[str, str]:
    """Index version headings without confusing the separate unreleased section."""
    return dict(re.findall(r"^## ([^\s]+) \(([^)]+)\)$", text, re.MULTILINE))


def precommit_revision(readme: str) -> str:
    """Require the documented hook to select this distribution's repository."""
    examples = re.findall(r"```yaml\n(.*?)\n```", readme, re.DOTALL)
    for example in examples:
        if f"repo: {REPOSITORY}" in example and re.search(r"\bid: pyroma\b", example):
            match = re.search(r"\brev:\s*['\"]?([^\s'\"]+)", example)
            if match is not None:
                return match[1]
    message = "README must document the pyroma hook from hbmartin/pyroma621 with a release revision"
    raise ValueError(message)


def _require_date(entries: dict[str, str], version: str, expected: str | None = None) -> None:
    value = entries.get(version, "")
    try:
        datetime.date.fromisoformat(value)
    except ValueError as error:
        message = f"Published/release version {version} needs a dated changelog entry"
        raise ValueError(message) from error
    if expected is not None and value != expected:
        message = f"Changelog date for {version} must be {expected}, not {value}"
        raise ValueError(message)


def check_offline(project: Path, release_tag: str = "") -> str:
    """Check distribution identity, canonical links, and known release dates."""
    metadata = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    site = tomllib.loads((project / "zensical.toml").read_text(encoding="utf-8"))["project"]
    readme = (project / "README.md").read_text(encoding="utf-8")
    if metadata["name"] != "pyroma621" or "pip install pyroma621" not in readme:
        message = "Installation documentation must select the pyroma621 distribution"
        raise ValueError(message)
    if metadata["urls"]["Documentation"] != SITE_URL or site["site_url"] != SITE_URL:
        message = "Project metadata and documentation site must use the canonical documentation URL"
        raise ValueError(message)
    if SITE_URL not in readme or "https://hbmartin.github.io/pyroma/" in readme:
        message = "README must link to the canonical pyroma621 documentation"
        raise ValueError(message)
    entries = changelog_entries((project / "docs" / "changelog.md").read_text(encoding="utf-8"))
    for version, date in RELEASE_DATES.items():
        _require_date(entries, version, date)
    if release_tag:
        if Version(release_tag) != Version(metadata["version"]):
            message = "Documentation release tag does not match project version"
            raise ValueError(message)
        _require_date(entries, metadata["version"])
    return precommit_revision(readme)


def fetch(url: str) -> str:
    """Read bounded public HTTPS release evidence with a timeout."""
    if not url.startswith("https://"):
        message = "Release evidence must use HTTPS"
        raise ValueError(message)
    with urlopen(url, timeout=15) as response:  # noqa: S310 - public HTTPS URLs checked above
        payload = response.read(_RESPONSE_LIMIT + 1)
    if len(payload) > _RESPONSE_LIMIT:
        message = f"Release evidence exceeds the response limit: {url}"
        raise ValueError(message)
    return payload.decode("utf-8")


def check_online(project: Path, revision: str) -> None:
    """Verify live docs, the installation revision, and all published dates."""
    for page in ("", "rules/", "upgrading/", "changelog/"):
        fetch(SITE_URL + page)
    base = "https://raw.githubusercontent.com/hbmartin/pyroma621/" + quote(revision, safe="") + "/"
    metadata = tomllib.loads(fetch(base + "pyproject.toml"))["project"]
    if metadata["name"] != "pyroma621" or Version(metadata["version"]) != Version(revision):
        message = "Documented pre-commit revision must install a pyroma621 release"
        raise ValueError(message)
    hooks = fetch(base + ".pre-commit-hooks.yaml")
    if not re.search(r"\bid: pyroma\b", hooks) or not re.search(r"\bentry: pyroma\b", hooks):
        message = "Documented release does not provide the pyroma pre-commit hook"
        raise ValueError(message)
    entries = changelog_entries((project / "docs" / "changelog.md").read_text(encoding="utf-8"))
    releases = json.loads(fetch("https://pypi.org/pypi/pyroma621/json"))["releases"]
    for version, artifacts in releases.items():
        if artifacts:
            date = min(artifact["upload_time_iso_8601"] for artifact in artifacts)[:10]
            _require_date(entries, version, date)


def prepare_schemas(project: Path) -> None:
    """Copy the canonical packaged schemas into the documentation input."""
    shutil.copytree(project / "pyroma" / "schemas", project / "docs" / "schemas", dirs_exist_ok=True)


def main() -> None:
    """Expose offline checks, tagged-release checks, and schema preparation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "schemas"))
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--release-tag", default="")
    parser.add_argument("--online", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "schemas":
            prepare_schemas(args.project)
        else:
            revision = check_offline(args.project, args.release_tag)
            if args.online:
                check_online(args.project, revision)
    except (OSError, ValueError, KeyError, TypeError, URLError) as error:
        parser.exit(1, f"Release documentation check failed: {error}\n")


if __name__ == "__main__":
    main()
