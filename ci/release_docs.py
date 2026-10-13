"""Check release documentation and prepare downloadable JSON Schemas."""

from __future__ import annotations

import argparse
import datetime
import errno
import json
import os
import re
import shutil
import stat
import tomllib
from contextlib import suppress
from dataclasses import dataclass
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
_LOCAL_PROJECT_LABEL = "Local pyproject.toml"


@dataclass(frozen=True)
class _LocalProjectMetadata:
    name: str
    version: str
    documentation: str
    classifiers: tuple[str, ...]


@dataclass(frozen=True)
class _SchemaEvidence:
    payload: bytes
    document: object


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
        _read_utf8(project / ".pre-commit-hooks.yaml", "Local .pre-commit-hooks.yaml"),
        "Local .pre-commit-hooks.yaml",
    )
    metadata = _local_project_metadata(_read_utf8(project / "pyproject.toml", _LOCAL_PROJECT_LABEL))
    site_url = _local_site_url(_read_utf8(project / "zensical.toml", "Local zensical.toml"))
    readme = _read_utf8(project / "README.md", "Local README.md", encoding="utf-8-sig")
    if metadata.name != "pyroma621" or "pip install pyroma621" not in readme:
        message = "Installation documentation must select the pyroma621 distribution"
        raise ValueError(message)
    if metadata.documentation != SITE_URL or site_url != SITE_URL:
        message = "Project metadata and documentation site must use the canonical documentation URL"
        raise ValueError(message)
    if SITE_URL not in readme or "https://hbmartin.github.io/pyroma/" in readme:
        message = "README must link to the canonical pyroma621 documentation"
        raise ValueError(message)
    entries = changelog_entries(_read_utf8(project / "docs" / "changelog.md", "Local docs/changelog.md"))
    for version, date in RELEASE_DATES.items():
        _require_date(entries, version, date)
    if release_tag:
        if Version(release_tag) != Version(metadata.version):
            message = "Documentation release tag does not match project version"
            raise ValueError(message)
        _require_date(entries, metadata.version)
        if not Version(metadata.version).is_prerelease and not _FINAL_STATUSES.intersection(metadata.classifiers):
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
        with suppress(Exception):
            error.close()
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


def _os_error_reason(error: OSError) -> str:
    """Keep useful OS failure categories without copying exception text."""
    return {
        errno.ENOENT: "file missing",
        errno.EACCES: "permission denied",
        errno.EPERM: "permission denied",
        errno.EISDIR: "directory instead of file",
        errno.ENOTDIR: "path component is not a directory",
        errno.EIO: "I/O failure",
    }.get(error.errno, "OS failure")


def _read_bytes(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        message = f"{label}: unable to read file ({_os_error_reason(error)})"
        raise ValueError(message) from error


def _decode_utf8(payload: bytes, label: str, *, encoding: str = "utf-8") -> str:
    try:
        return payload.decode(encoding)
    except UnicodeDecodeError as error:
        message = f"{label} must contain valid UTF-8"
        raise ValueError(message) from error


def _read_utf8(path: Path, label: str, *, encoding: str = "utf-8") -> str:
    """Label reading and decoding while preserving universal newline handling."""
    text = _decode_utf8(_read_bytes(path, label), label, encoding=encoding)
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _local_project_metadata(text: str) -> _LocalProjectMetadata:
    """Validate the local project fields consumed by documentation checks."""
    label = _LOCAL_PROJECT_LABEL
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
        Version(version)
    except ValueError as error:
        raise ValueError(message) from error
    urls = project.get("urls")
    if not isinstance(urls, dict):
        message = f"{label}: project.urls must be a table"
        raise ValueError(message)  # noqa: TRY004 - malformed release evidence
    documentation = urls.get("Documentation")
    if not isinstance(documentation, str) or not documentation.strip():
        message = f"{label}: project.urls.Documentation must be a nonempty string"
        raise ValueError(message)
    classifiers = project.get("classifiers", [])
    if not isinstance(classifiers, list) or not all(isinstance(value, str) for value in classifiers):
        message = f"{label}: project.classifiers must be a list of strings"
        raise ValueError(message)
    return _LocalProjectMetadata(name, version, documentation, tuple(classifiers))


def _local_site_url(text: str) -> str:
    """Validate the local documentation site's canonical URL field."""
    label = "Local zensical.toml"
    project = _load_toml(text, label).get("project")
    if not isinstance(project, dict):
        message = f"{label}: project must be a table"
        raise ValueError(message)  # noqa: TRY004 - malformed release evidence
    site_url = project.get("site_url")
    if not isinstance(site_url, str) or not site_url.strip():
        message = f"{label}: project.site_url must be a nonempty string"
        raise ValueError(message)
    return site_url


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
    if not isinstance(timestamp, str):
        raise ValueError(message)  # noqa: TRY004 - malformed release evidence
    try:
        uploaded = datetime.datetime.fromisoformat(timestamp)
    except ValueError as error:
        raise ValueError(message) from error
    if uploaded.utcoffset() is None:
        message = f"{label}: upload_time_iso_8601 must be a timezone-aware ISO timestamp string"
        raise ValueError(message)
    try:
        return uploaded.astimezone(datetime.UTC).date().isoformat()
    except (ValueError, OverflowError) as error:
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
            parsed_version = Version(version)
        except ValueError as error:
            message = f"{label}: release keys must be valid version strings"
            raise ValueError(message) from error
        if parsed_version.local is not None:
            message = f"{label}: release keys must be public version strings without local labels"
            raise ValueError(message)
        dates[version] = min(_upload_date(artifact) for artifact in artifacts)
    return dates


def _scan_inventory(folder: Path, root: Path, label: str, *, ignore_symlinks: bool) -> list[Path]:
    paths: list[Path] = []
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                entry_mode = entry.stat(follow_symlinks=False).st_mode
                path = Path(entry.path)
                if stat.S_ISLNK(entry_mode):
                    if not ignore_symlinks:
                        message = f"{label}: symlinks are not permitted"
                        raise ValueError(message)
                elif stat.S_ISDIR(entry_mode):
                    paths.extend(_scan_inventory(path, root, label, ignore_symlinks=ignore_symlinks))
                elif stat.S_ISREG(entry_mode):
                    paths.append(path.relative_to(root))
                elif not ignore_symlinks:
                    message = f"{label}: entries must be regular files or directories"
                    raise ValueError(message)
    except OSError as error:
        message = f"{label}: unable to read inventory ({_os_error_reason(error)})"
        raise ValueError(message) from error
    return paths


def _inventory_files(directory: Path, label: str, *, ignore_symlinks: bool) -> list[Path]:
    """Scan without following links or suppressing directory/stat failures."""
    try:
        mode = directory.lstat().st_mode
    except FileNotFoundError as error:
        message = f"{label} is missing"
        raise ValueError(message) from error
    except OSError as error:
        message = f"{label}: unable to read inventory ({_os_error_reason(error)})"
        raise ValueError(message) from error
    if stat.S_ISLNK(mode):
        message = f"{label} must not be a symlink"
        raise ValueError(message)
    if not stat.S_ISDIR(mode):
        message = f"{label} is not a directory"
        raise ValueError(message)
    return sorted(_scan_inventory(directory, directory, label, ignore_symlinks=ignore_symlinks))


def schema_paths(project: Path) -> list[Path]:
    """Discover every canonical JSON schema relative to the package."""
    paths = [
        Path("schemas") / path
        for path in _inventory_files(
            project / "pyroma" / "schemas", "Canonical packaged schema directory", ignore_symlinks=True
        )
        if path.name.endswith(".json")
    ]
    if not paths:
        message = "Canonical packaged schema directory has no JSON schemas"
        raise ValueError(message)
    return paths


def _canonical_schemas(project: Path) -> dict[Path, _SchemaEvidence]:
    """Read all schema evidence before modifying output or fetching resources."""
    schemas = {}
    for relative in schema_paths(project):
        label = f"Packaged schema {relative.as_posix()}"
        payload = _read_bytes(project / "pyroma" / relative, label)
        document = _load_json(_decode_utf8(payload, label), label)
        schemas[relative] = _SchemaEvidence(payload, document)
    return schemas


def _check_published_contract(schemas: dict[Path, _SchemaEvidence]) -> None:
    """Require the JSON guide and canonical schema downloads to be published."""
    fetch(SITE_URL + "json/", label="JSON guide")
    for relative, evidence in schemas.items():
        label = f"Published schema {relative.relative_to('schemas').as_posix()}"
        published = _load_json(fetch(SITE_URL + quote(relative.as_posix(), safe="/"), label=label), label)
        if published != evidence.document:
            message = f"{label} does not match packaged schema"
            raise ValueError(message)


def check_online(project: Path, revision: str) -> None:
    """Verify live docs, the installation revision, and all published dates."""
    schemas = _canonical_schemas(project)
    entries = changelog_entries(_read_utf8(project / "docs" / "changelog.md", "Local docs/changelog.md"))
    for page, label in (
        ("", "Documentation home"),
        ("rules/", "Rules guide"),
        ("upgrading/", "Upgrade guide"),
        ("changelog/", "Changelog"),
    ):
        fetch(SITE_URL + page, label=label)
    _check_published_contract(schemas)
    base = "https://raw.githubusercontent.com/hbmartin/pyroma621/" + quote(revision, safe="") + "/"
    name, hook_version = _hook_package_metadata(fetch(base + "pyproject.toml", label="Hook package metadata"))
    if name != "pyroma621" or hook_version != Version(revision):
        message = "Documented pre-commit revision must install a pyroma621 release"
        raise ValueError(message)
    _require_hook_definition(
        fetch(base + ".pre-commit-hooks.yaml", label="Published hook manifest"), "Published pre-commit hooks"
    )
    dates = _published_release_dates(fetch("https://pypi.org/pypi/pyroma621/json", label="PyPI release metadata"))
    for release_version, date in dates.items():
        context = f"PyPI release metadata: Changelog for a published release (expected UTC date {date})"
        value = entries.get(release_version)
        if value is None:
            message = f"{context} has no matching heading"
            raise ValueError(message)
        try:
            datetime.date.fromisoformat(value)
        except ValueError as error:
            message = f"{context} needs a valid dated heading"
            raise ValueError(message) from error
        if value != date:
            message = f"{context} has a mismatched date"
            raise ValueError(message)


def prepare_schemas(project: Path) -> None:
    """Copy the canonical packaged schemas into the documentation input."""
    schemas = _canonical_schemas(project)
    destination = project / "docs" / "schemas"
    if destination.exists():
        shutil.rmtree(destination)
    for relative, evidence in schemas.items():
        path = project / "docs" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(evidence.payload)


def check_schema_outputs(project: Path, site: Path) -> list[Path]:
    """Require exactly the permitted schema files and their original bytes."""
    schemas = _canonical_schemas(project)
    guide = site / "json" / "index.html"
    if not _read_bytes(guide, "Built JSON guide json/index.html"):
        message = "Built JSON guide json/index.html must not be empty"
        raise ValueError(message)
    built = {
        Path("schemas") / path
        for path in _inventory_files(site / "schemas", "Built schema directory", ignore_symlinks=False)
    }
    if built != set(schemas):
        message = "Built schema files do not match the canonical packaged inventory"
        raise ValueError(message)
    for relative, evidence in schemas.items():
        label = f"Built schema {relative.as_posix()}"
        if _read_bytes(site / relative, label) != evidence.payload:
            message = f"{label} does not match packaged schema bytes"
            raise ValueError(message)
    return list(schemas)


def main() -> None:
    """Expose offline checks, tagged-release checks, and schema preparation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "schemas", "check-schemas"))
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--release-tag", default="")
    parser.add_argument("--online", action="store_true")
    parser.add_argument("--site", type=Path, help="Built site for check-schemas (default: PROJECT/site)")
    parser.add_argument("--inventory-output", type=Path, help="Write verified schema paths as compact JSON")
    args = parser.parse_args()
    if args.command != "check-schemas" and (args.site is not None or args.inventory_output is not None):
        parser.error("--site and --inventory-output require check-schemas")
    try:
        if args.command == "schemas":
            prepare_schemas(args.project)
        elif args.command == "check-schemas":
            paths = check_schema_outputs(args.project, args.site if args.site is not None else args.project / "site")
            if args.inventory_output is not None:
                args.inventory_output.write_text(
                    json.dumps([path.as_posix() for path in paths], separators=(",", ":")) + "\n", encoding="utf-8"
                )
        else:
            revision = check_offline(args.project, args.release_tag)
            if args.online:
                check_online(args.project, revision)
    except (OSError, ValueError, KeyError, TypeError, URLError) as error:
        parser.exit(1, f"Release documentation check failed: {error}\n")


if __name__ == "__main__":
    main()
