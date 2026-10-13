"""Check release documentation and prepare downloadable JSON Schemas."""

from __future__ import annotations

import argparse
import datetime
import json
import re
import shutil
import tomllib
from http.client import HTTPException
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import urlopen

import yaml
from markdown_it import MarkdownIt
from packaging.version import Version

if TYPE_CHECKING:
    from markdown_it.parser_block import RuleFuncBlockType
    from markdown_it.rules_block.state_block import StateBlock
    from markdown_it.token import Token

SITE_URL = "https://hbmartin.github.io/pyroma621/"
REPOSITORY = "https://github.com/hbmartin/pyroma621"
RELEASE_DATES = {"6.0.0": "2026-08-01", "6.1.0": "2026-10-12"}
_RESPONSE_LIMIT = 2 * 1024 * 1024
_PRECOMMIT_MARKER = "<!-- pyroma-pre-commit -->"
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


def _fence_with_closure(rule: RuleFuncBlockType) -> RuleFuncBlockType:
    """Record whether the standard rule consumed two delimiter lines."""

    def wrapped(state: StateBlock, start: int, end: int, silent: bool) -> bool:  # noqa: FBT001 - parser callback
        matched = rule(state, start, end, silent)
        if matched and not silent:
            token = state.tokens[-1]
            content_lines = token.content.count("\n") + bool(token.content and not token.content.endswith("\n"))
            token.meta["closed"] = token.map is not None and token.map[1] - token.map[0] == content_lines + 2
        return matched

    return wrapped


def _markdown_parser() -> MarkdownIt:
    """Wrap the registered fence rule while preserving its alternative chains."""
    parser = MarkdownIt("commonmark")
    # The release dependency is bounded to markdown-it-py 4.x for this rule metadata.
    rule = next(rule for rule in parser.block.ruler.__rules__ if rule.name == "fence")
    parser.block.ruler.at("fence", _fence_with_closure(rule.fn), {"alt": list(rule.alt)})
    return parser


def _marked_fence_message(line: int) -> str:
    return f"README line {line}: marked pyroma hook example needs a complete yaml code block"


def _marker_placement_message(line: int) -> str:
    return f"README line {line}: pyroma hook marker must be on its own line before the YAML fence"


def _read_marked_fence(token: Token, pending: tuple[int, int]) -> tuple[int, str]:
    marker_line, level = pending
    message = _marked_fence_message(marker_line)
    if token.type != "fence" or token.level != level or token.info.strip() != "yaml":
        raise ValueError(message)
    if not token.meta["closed"]:
        line = token.map[0] + 1 if token.map else 0
        message = f"{message}; unclosed code fence at README line {line}"
        raise ValueError(message)
    return marker_line, token.content


def _marked_precommit_blocks(readme: str) -> list[tuple[int, str]]:
    """Read adjacent marker/YAML blocks in the same Markdown container."""
    parser = _markdown_parser()
    blocks: list[tuple[int, str]] = []
    pending: tuple[int, int] | None = None
    for token in parser.parse(readme.removeprefix("\ufeff")):
        line = token.map[0] + 1 if token.map else 0
        if pending is not None:
            blocks.append(_read_marked_fence(token, pending))
            pending = None
            continue
        if token.type == "fence":
            if not token.meta["closed"]:
                message = f"README line {line}: unclosed code fence hides possible pyroma hook examples"
                raise ValueError(message)
            if _PRECOMMIT_MARKER in token.info:
                message = _marker_placement_message(line)
                raise ValueError(message)
        elif token.type == "html_block":
            if token.content.strip() == _PRECOMMIT_MARKER:
                pending = (line, token.level)
            elif _PRECOMMIT_MARKER in token.content:
                marker_line = line + token.content.partition(_PRECOMMIT_MARKER)[0].count("\n")
                message = _marker_placement_message(marker_line)
                raise ValueError(message)
    if pending is not None:
        message = _marked_fence_message(pending[0])
        raise ValueError(message)
    return blocks


def precommit_revision(readme: str) -> str:
    """Read the revision from explicitly marked pyroma hook examples."""
    message = "README must document the pyroma hook from hbmartin/pyroma621 with a release revision"
    revisions: set[str] = set()
    for marker_line, block in _marked_precommit_blocks(readme):
        context = f"README pre-commit example at line {marker_line}"
        block_message = f"{context}: {message}"
        example = _load_yaml(block, context)
        repositories = example.get("repos") if isinstance(example, dict) else None
        repositories = repositories if isinstance(repositories, list) else []
        block_revisions: set[str] = set()
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
                raise ValueError(block_message)
            block_revisions.add(revision)
        if not block_revisions:
            raise ValueError(block_message)
        revisions.update(block_revisions)
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
    readme = (project / "README.md").read_text(encoding="utf-8-sig")
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
        message = f"{label} must use HTTPS"
        raise ValueError(message)
    try:
        with urlopen(url, timeout=15) as response:  # noqa: S310 - public HTTPS URLs checked above
            payload = response.read(_RESPONSE_LIMIT + 1)
    except HTTPError as error:
        message = f"{label}: HTTP {error.code}"
        try:
            error.close()
        finally:
            raise ValueError(message) from error
    except TimeoutError as error:
        message = f"{label}: request timed out"
        raise ValueError(message) from error
    except URLError as error:
        reason = "request timed out" if isinstance(error.reason, TimeoutError) else "unable to fetch resource"
        message = f"{label}: {reason}"
        raise ValueError(message) from error
    except (OSError, HTTPException) as error:
        message = f"{label}: unable to read response"
        raise ValueError(message) from error
    if len(payload) > _RESPONSE_LIMIT:
        message = f"{label} exceeds the response limit"
        raise ValueError(message)
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as error:
        message = f"{label}: response must contain valid UTF-8"
        raise ValueError(message) from error


def _load_json(text: str, label: str) -> object:
    try:
        return json.loads(text)
    except ValueError as error:
        message = f"{label} must contain valid JSON"
        raise ValueError(message) from error


def _load_toml(text: str, label: str) -> dict[str, object]:
    try:
        return tomllib.loads(text)
    except ValueError as error:
        message = f"{label} must contain valid TOML"
        raise ValueError(message) from error


def _hook_package_metadata(text: str) -> tuple[str, Version]:
    """Validate the fields consumed from the documented hook's package."""
    label = "Hook package metadata"
    project = _load_toml(text, label).get("project")
    if not isinstance(project, dict):
        message = f"{label}: project must be a table"
        raise ValueError(message)  # noqa: TRY004 - malformed release evidence
    name = project.get("name")
    if not isinstance(name, str) or not name.strip():
        message = f"{label}: project.name must be a nonempty string"
        raise ValueError(message)
    version = project.get("version")
    message = f"{label}: project.version must be a valid version string"
    if not isinstance(version, str):
        raise ValueError(message)  # noqa: TRY004 - malformed release evidence
    try:
        return name, Version(version)
    except ValueError as error:
        raise ValueError(message) from error


def _upload_date(artifact: object) -> str:
    """Read a safe calendar date from a PyPI artifact record."""
    label = "PyPI release metadata"
    if not isinstance(artifact, dict):
        message = f"{label}: release artifacts must be objects"
        raise ValueError(message)  # noqa: TRY004 - malformed release evidence
    timestamp = artifact.get("upload_time_iso_8601")
    message = f"{label}: upload_time_iso_8601 must be an ISO timestamp string"
    if not isinstance(timestamp, str) or "T" not in timestamp:
        raise ValueError(message)
    try:
        return datetime.datetime.fromisoformat(timestamp).date().isoformat()
    except ValueError as error:
        raise ValueError(message) from error


def _published_release_dates(text: str) -> dict[str, str]:
    """Validate PyPI response structure before using its release dates."""
    label = "PyPI release metadata"
    document = _load_json(text, label)
    if not isinstance(document, dict):
        message = f"{label}: response must be an object"
        raise ValueError(message)  # noqa: TRY004 - malformed release evidence
    releases = document.get("releases")
    if not isinstance(releases, dict):
        message = f"{label}: releases must be an object"
        raise ValueError(message)  # noqa: TRY004 - malformed release evidence
    dates: dict[str, str] = {}
    for version, artifacts in releases.items():
        if not isinstance(artifacts, list):
            message = f"{label}: release artifacts must be lists"
            raise ValueError(message)  # noqa: TRY004 - malformed release evidence
        if not artifacts:
            continue
        try:
            Version(version)
        except ValueError as error:
            message = f"{label}: release keys must be valid version strings"
            raise ValueError(message) from error
        dates[version] = min(_upload_date(artifact) for artifact in artifacts)
    return dates


def schema_paths(project: Path) -> list[Path]:
    """Discover every canonical JSON schema relative to the package."""
    package = project / "pyroma"
    paths = sorted(path.relative_to(package) for path in (package / "schemas").rglob("*.json") if path.is_file())
    if not paths:
        message = "Canonical packaged schema directory has no JSON schemas"
        raise ValueError(message)
    return paths


def _check_published_contract(project: Path) -> None:
    """Require the JSON guide and canonical schema downloads to be published."""
    fetch(SITE_URL + "json/", label="JSON guide")
    for relative in schema_paths(project):
        label = f"Published schema {relative.relative_to('schemas').as_posix()}"
        canonical = _load_json(
            (project / "pyroma" / relative).read_text(encoding="utf-8"), f"Packaged schema {relative.as_posix()}"
        )
        published = _load_json(fetch(SITE_URL + relative.as_posix(), label=label), label)
        if published != canonical:
            message = f"{label} does not match packaged schema"
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
    name, version = _hook_package_metadata(fetch(base + "pyproject.toml", label="Hook package metadata"))
    if name != "pyroma621" or version != Version(revision):
        message = "Documented pre-commit revision must install a pyroma621 release"
        raise ValueError(message)
    _require_hook_definition(
        fetch(base + ".pre-commit-hooks.yaml", label="Published hook manifest"), "Published pre-commit hooks"
    )
    entries = changelog_entries((project / "docs" / "changelog.md").read_text(encoding="utf-8"))
    dates = _published_release_dates(fetch("https://pypi.org/pypi/pyroma621/json", label="PyPI release metadata"))
    for version, date in dates.items():
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
