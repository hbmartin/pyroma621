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

import yaml
from packaging.version import Version

SITE_URL = "https://hbmartin.github.io/pyroma621/"
REPOSITORY = "https://github.com/hbmartin/pyroma621"
RELEASE_DATES = {"6.0.0": "2026-08-01", "6.1.0": "2026-10-12"}
_RESPONSE_LIMIT = 2 * 1024 * 1024
_PRECOMMIT_MARKER = "<!-- pyroma-pre-commit -->"
_FENCE_START = re.compile(r" {0,3}(`{3,}|~{3,})(.*)")
_FINAL_STATUSES = {"Development Status :: 5 - Production/Stable", "Development Status :: 6 - Mature"}


def changelog_entries(text: str) -> dict[str, str]:
    """Index version headings, including the versioned unreleased section."""
    return dict(re.findall(r"^## ([^\s]+) \(([^)]+)\)$", text, re.MULTILINE))


def _load_yaml(text: str, context: str) -> object:
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as error:
        message = f"{context} must contain valid YAML"
        raise ValueError(message) from error


def _fence_end(lines: list[str], start: int, delimiter: str) -> int | None:
    """Find a matching closing fence without inspecting its contents."""
    closing = re.compile(r" {0,3}" + re.escape(delimiter[0]) + "{" + str(len(delimiter)) + r",}[ \t]*")
    return next((index for index in range(start + 1, len(lines)) if closing.fullmatch(lines[index])), None)


def _marked_precommit_blocks(readme: str) -> list[tuple[int, str]]:
    """Read marked YAML fences while ignoring literal marker examples."""
    lines = readme.splitlines()
    blocks = []
    index = 0
    while index < len(lines):
        fence = _FENCE_START.fullmatch(lines[index])
        if fence:
            end = _fence_end(lines, index, fence[1])
            index = end + 1 if end is not None else len(lines)
            continue
        if not re.fullmatch(r" {0,3}" + re.escape(_PRECOMMIT_MARKER) + r"[ \t]*", lines[index]):
            index += 1
            continue
        marker_line = index + 1
        message = f"README line {marker_line}: marked pyroma hook example needs a complete yaml code block"
        index += 1
        while index < len(lines) and not lines[index].strip():
            index += 1
        fence = _FENCE_START.fullmatch(lines[index]) if index < len(lines) else None
        if fence is None or fence[2].strip() != "yaml":
            raise ValueError(message)
        end = _fence_end(lines, index, fence[1])
        if end is None:
            raise ValueError(message)
        blocks.append((marker_line, "\n".join(lines[index + 1 : end])))
        index = end + 1
    return blocks


def precommit_revision(readme: str) -> str:
    """Read the revision from explicitly marked pyroma hook examples."""
    message = "README must document the pyroma hook from hbmartin/pyroma621 with a release revision"
    revisions: set[str] = set()
    for marker_line, block in _marked_precommit_blocks(readme):
        context = f"README pre-commit example at line {marker_line}"
        example = _load_yaml(block, context)
        repositories = example.get("repos") if isinstance(example, dict) else None
        if not isinstance(repositories, list):
            continue
        for repository in repositories:
            if not isinstance(repository, dict) or not _is_pyroma_repository(repository.get("repo")):
                continue
            hooks = repository.get("hooks")
            if not isinstance(hooks, list) or not any(
                isinstance(hook, dict) and hook.get("id") == "pyroma" for hook in hooks
            ):
                continue
            revision = repository.get("rev")
            if not isinstance(revision, str) or not revision.strip():
                raise ValueError(message)
            revisions.add(revision)
    if not revisions:
        raise ValueError(message)
    if len(revisions) != 1:
        message = "README pyroma pre-commit examples have conflicting release revisions"
        raise ValueError(message)
    return revisions.pop()


def _require_hook_definition(text: str, context: str) -> None:
    """Require the hook ID and command to belong to the same manifest entry."""
    hooks = _load_yaml(text, context)
    if not isinstance(hooks, list) or not any(
        isinstance(hook, dict) and hook.get("id") == "pyroma" and hook.get("entry") == "pyroma" for hook in hooks
    ):
        message = f"{context} must provide the pyroma pre-commit hook with entry pyroma"
        raise ValueError(message)


def _is_pyroma_repository(repository: object) -> bool:
    """Accept Git's optional suffix and slash without accepting another repo."""
    return isinstance(repository, str) and repository.rstrip("/").removesuffix(".git") == REPOSITORY


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


def _require_hook_revision(revision: str, release_tag: str) -> None:
    """Keep tagged installation instructions aligned with the release."""
    if release_tag and revision != release_tag:
        message = "Documented pre-commit revision does not match release tag"
        raise ValueError(message)


def check_offline(project: Path, release_tag: str = "") -> str:
    """Check distribution identity, canonical links, and known release dates."""
    _require_hook_definition(
        (project / ".pre-commit-hooks.yaml").read_text(encoding="utf-8"), "Local .pre-commit-hooks.yaml"
    )
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
        if not Version(metadata["version"]).is_prerelease and not _FINAL_STATUSES.intersection(
            metadata.get("classifiers", [])
        ):
            message = "Final pyroma621 releases require Development Status :: 5 - Production/Stable or 6 - Mature"
            raise ValueError(message)
    revision = precommit_revision(readme)
    _require_hook_revision(revision, release_tag)
    return revision


def fetch(url: str, *, label: str = "Release evidence") -> str:
    """Read bounded public HTTPS release evidence with a timeout."""
    if not url.startswith("https://"):
        message = "Release evidence must use HTTPS"
        raise ValueError(message)
    with urlopen(url, timeout=15) as response:  # noqa: S310 - public HTTPS URLs checked above
        payload = response.read(_RESPONSE_LIMIT + 1)
    if len(payload) > _RESPONSE_LIMIT:
        message = f"{label} exceeds the response limit"
        raise ValueError(message)
    return payload.decode("utf-8")


def _check_published_contract(project: Path) -> None:
    """Require the JSON guide and canonical schema downloads to be published."""
    fetch(SITE_URL + "json/", label="JSON guide")
    for kind in ("rating", "error"):
        relative = Path("schemas") / "v1" / f"{kind}.json"
        canonical = json.loads((project / "pyroma" / relative).read_text(encoding="utf-8"))
        published = json.loads(fetch(SITE_URL + relative.as_posix(), label=f"Published {kind} schema"))
        if published != canonical:
            message = f"Published {kind} schema does not match packaged schema"
            raise ValueError(message)


def check_online(project: Path, revision: str) -> None:
    """Verify live docs, the installation revision, and all published dates."""
    for page, label in (
        ("", "Documentation home"),
        ("rules/", "Rules guide"),
        ("upgrading/", "Upgrade guide"),
        ("changelog/", "Changelog"),
    ):
        fetch(SITE_URL + page, label=label)
    _check_published_contract(project)
    base = "https://raw.githubusercontent.com/hbmartin/pyroma621/" + quote(revision, safe="") + "/"
    metadata = tomllib.loads(fetch(base + "pyproject.toml", label="Hook package metadata"))["project"]
    if metadata["name"] != "pyroma621" or Version(metadata["version"]) != Version(revision):
        message = "Documented pre-commit revision must install a pyroma621 release"
        raise ValueError(message)
    _require_hook_definition(
        fetch(base + ".pre-commit-hooks.yaml", label="Published hook manifest"), "Published pre-commit hooks"
    )
    entries = changelog_entries((project / "docs" / "changelog.md").read_text(encoding="utf-8"))
    releases = json.loads(fetch("https://pypi.org/pypi/pyroma621/json", label="PyPI release metadata"))["releases"]
    for version, artifacts in releases.items():
        if artifacts:
            date = min(artifact["upload_time_iso_8601"] for artifact in artifacts)[:10]
            _require_date(entries, version, date)


def prepare_schemas(project: Path) -> None:
    """Copy the canonical packaged schemas into the documentation input."""
    source = project / "pyroma" / "schemas"
    destination = project / "docs" / "schemas"
    if not source.is_dir():
        message = "Canonical packaged schema directory is missing"
        raise ValueError(message)
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)


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
