"""Check release documentation and prepare downloadable JSON Schemas."""

from __future__ import annotations

import argparse
import datetime
import errno
import io
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import tomllib
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
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
    from collections.abc import Generator

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
_CHANGELOG_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_DESCRIPTOR_ALIAS = re.compile(
    r"/(?:dev/(?:stdin|stdout|stderr|fd/[0-9]+)|"
    r"proc/(?:self|thread-self|[0-9]+)(?:/task/[0-9]+)?/fd/[0-9]+)(?=/|$)"
)
_LINK_LIMIT = 40
_DIRECTORY_OPERATIONS = os.open in os.supports_dir_fd and os.scandir in os.supports_fd
_RELATIVE_STAT_OPERATIONS = os.stat in os.supports_dir_fd and os.stat in os.supports_follow_symlinks
_SEARCH_FLAG = getattr(os, "O_PATH", getattr(os, "O_SEARCH", None))
_SYMLINK_ERRORS = {getattr(errno, name) for name in ("ELOOP", "EMLINK", "EFTYPE") if hasattr(errno, name)}
_SYMLINK_REASON = "symlink not permitted"
_INVENTORY_DESTINATION_ERROR = (
    "Schema inventory output: destination must be a regular file or replaceable symlink; "
    "use --inventory-output - for stdout"
)


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


@dataclass(frozen=True)
class _DirectoryIdentity:
    path: Path
    entry: tuple[int, int, int]
    target: tuple[int, int, int]
    label: str


@dataclass(frozen=True)
class _SchemaInventory:
    files: frozenset[Path]
    directories: frozenset[Path]
    payloads: dict[Path, bytes]
    identities: tuple[_DirectoryIdentity, ...]


@dataclass(frozen=True)
class _ExpectedSchemaTree:
    files: frozenset[Path]
    directories: frozenset[Path] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "files", frozenset(self.files))
        object.__setattr__(
            self, "directories", frozenset(parent for path in self.files for parent in path.parents if parent != Path())
        )


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


def _changelog_date(value: str) -> datetime.date:
    """Require the documented calendar-date spelling before parsing it."""
    if _CHANGELOG_DATE.fullmatch(value) is None:
        message = "Changelog dates must use YYYY-MM-DD"
        raise ValueError(message)
    try:
        return datetime.date.fromisoformat(value)
    except ValueError as error:
        message = "Changelog dates must be valid calendar dates in YYYY-MM-DD format"
        raise ValueError(message) from error


def _changelog_entry_date(entries: dict[str, str], version: str, context: str) -> datetime.date:
    """Share heading validation without constructing a resource's context."""
    value = entries.get(version)
    if value is None:
        message = f"{context} has no matching heading"
        raise ValueError(message)
    if value == "unreleased":
        message = f"{context} is unreleased; needs a dated heading in YYYY-MM-DD format"
        raise ValueError(message)
    try:
        return _changelog_date(value)
    except ValueError as error:
        message = f"{context}: {error}"
        raise ValueError(message) from error


def _require_date(entries: dict[str, str], version: str, expected: str | None = None) -> None:
    context = f"Local docs/changelog.md: Release {version}"
    date = _changelog_entry_date(entries, version, context).isoformat()
    if expected is not None and date != expected:
        message = f"{context}: Changelog date must be {expected}, not {date}"
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
    reasons: dict[int | None, str] = {
        errno.ENOENT: "file missing",
        errno.EACCES: "permission denied",
        errno.EPERM: "permission denied",
        errno.EISDIR: "directory instead of file",
        errno.ENOTDIR: "path component is not a directory",
        errno.EIO: "I/O failure",
        errno.ENOSPC: "disk full",
        errno.EPIPE: "broken pipe",
        errno.EBADF: "bad file descriptor",
        errno.EAGAIN: "operation would block",
    }
    return reasons.get(error.errno, "OS failure")


def _read_bytes(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        message = f"{label}: unable to read file ({_os_error_reason(error)})"
        raise ValueError(message) from error


def _protected_reads_supported() -> bool:
    return all(hasattr(os, name) for name in ("O_NONBLOCK", "O_NOFOLLOW"))


def _anchored_paths_supported() -> bool:
    return _DIRECTORY_OPERATIONS and hasattr(os, "O_DIRECTORY") and _protected_reads_supported()


def _search_anchors_supported() -> bool:
    return _anchored_paths_supported() and _SEARCH_FLAG is not None and _RELATIVE_STAT_OPERATIONS


def _identity(info: os.stat_result) -> tuple[int, int, int]:
    return info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)


def _directory_identity(path: Path, label: str) -> _DirectoryIdentity:
    entry = path.lstat()
    target = path.stat() if stat.S_ISLNK(entry.st_mode) else entry
    return _DirectoryIdentity(path, _identity(entry), _identity(target), label)


def _ancestor_identities(
    path: Path, root: Path, label: str, *, display_prefix: tuple[Path, Path] | None = None
) -> list[_DirectoryIdentity]:
    """Give every logical ancestor the same context in either capability path."""
    identities = []
    logical = root
    for part in (None, *path.relative_to(root).parts):
        if part is not None:
            logical /= part
        display = logical.relative_to(root)
        if display_prefix is not None and logical.is_relative_to(display_prefix[0]):
            display = display_prefix[1] / logical.relative_to(display_prefix[0])
        location = _escaped_path(display) if display.parts else "selected root"
        context = f"{label} at {location}"
        try:
            identities.append(_directory_identity(logical, context))
        except OSError as error:
            message = f"{context}: unable to inspect directory ({_os_error_reason(error)})"
            raise ValueError(message) from error
    return identities


def _validate_directories(identities: list[_DirectoryIdentity] | tuple[_DirectoryIdentity, ...]) -> None:
    for expected in identities:
        try:
            actual = _directory_identity(expected.path, expected.label)
        except OSError as error:
            message = f"{expected.label}: unable to verify directory identity ({_os_error_reason(error)})"
            raise ValueError(message) from error
        if actual.entry != expected.entry or actual.target != expected.target:
            message = f"{expected.label}: directory changed during validation"
            raise ValueError(message)


def _opening_reason(error: OSError, path: Path | str, *, parent: int | None = None) -> str:
    if error.errno in _SYMLINK_ERRORS:
        return _SYMLINK_REASON
    if error.errno == errno.ENOTDIR:
        try:
            if parent is not None and not _RELATIVE_STAT_OPERATIONS:
                return _os_error_reason(error)
            mode = os.stat(path, dir_fd=parent, follow_symlinks=False).st_mode
        except (OSError, NotImplementedError):
            pass
        else:
            if stat.S_ISLNK(mode):
                return _SYMLINK_REASON
    return _os_error_reason(error)


@contextmanager
def _directory_descriptor(
    path: Path,
    root: Path,
    identities: list[_DirectoryIdentity],
    label: str,
    *,
    display_prefix: tuple[Path, Path] | None = None,
) -> Generator[int, None, None]:
    """Anchor existing ancestors with search permission, recording their identities."""
    search_flag = _SEARCH_FLAG
    if search_flag is None or not _search_anchors_supported():
        message = f"{label}: search-only directory anchoring is unavailable"
        raise ValueError(message)
    descriptors: list[int] = []
    try:
        for expected in _ancestor_identities(path, root, label, display_prefix=display_prefix):
            try:
                flags = search_flag | os.O_DIRECTORY
                descriptor = os.open(
                    expected.path if not descriptors else expected.path.name,
                    flags,
                    dir_fd=None if not descriptors else descriptors[-1],
                )
                descriptors.append(descriptor)
                if _identity(os.fstat(descriptor)) != expected.target:
                    message = f"{expected.label}: directory changed during validation"
                    raise ValueError(message)
                identities.append(expected)
            except OSError as error:
                message = f"{expected.label}: unable to open directory ({_os_error_reason(error)})"
                raise ValueError(message) from error
        yield descriptors[-1]
    finally:
        for descriptor in reversed(descriptors):
            with suppress(Exception):
                os.close(descriptor)


def _read_regular_stream(descriptor: int, label: str, expected: os.stat_result | None = None) -> bytes:
    """Keep descriptor ownership with the caller across stream acquisition failures."""
    observed = os.fstat(descriptor)
    _require_regular_file(observed, label)
    if expected is not None and _identity(observed) != _identity(expected):
        message = f"{label}: file changed during validation"
        raise ValueError(message)
    if _protected_reads_supported():
        os.set_blocking(descriptor, True)
    raw = io.FileIO(descriptor, "rb", closefd=False)
    try:
        stream = io.BufferedReader(raw)
        try:
            payload = stream.read()
        except BaseException:
            with suppress(Exception):
                stream.close()
            raise
        else:
            stream.close()
            return payload
    finally:
        if not raw.closed:
            if sys.exc_info()[0] is not None:
                with suppress(Exception):
                    raw.close()
            else:
                raw.close()


def _require_regular_file(info: os.stat_result, label: str) -> None:
    if stat.S_ISLNK(info.st_mode):
        message = f"{label}: unable to read file ({_SYMLINK_REASON})"
        raise ValueError(message)
    if not stat.S_ISREG(info.st_mode):
        message = f"{label}: file must be a regular file"
        raise ValueError(message)


def _regular_file_info(path: Path, label: str, *, parent: int | None = None) -> os.stat_result:
    try:
        if parent is not None and not _RELATIVE_STAT_OPERATIONS:
            message = f"{label}: non-following descriptor-relative stat is unavailable"
            raise ValueError(message)
        info = path.lstat() if parent is None else os.stat(path.name, dir_fd=parent, follow_symlinks=False)
    except NotImplementedError as error:
        message = f"{label}: non-following stat is unavailable"
        raise ValueError(message) from error
    _require_regular_file(info, label)
    return info


def _open_regular_file(
    path: Path, label: str, flags: int, expected: os.stat_result | None, *, parent: int | None = None
) -> tuple[int, os.stat_result]:
    """Pin an undiscovered file after checking its parents, immediately before opening."""
    if expected is None or not _protected_reads_supported():
        observed = _regular_file_info(path, label, parent=parent)
        if expected is None:
            expected = observed
        elif _identity(observed) != _identity(expected):
            message = f"{label}: file changed during validation"
            raise ValueError(message)
    descriptor = os.open(path if parent is None else path.name, flags, dir_fd=parent)
    return descriptor, expected


def _preliminary_file_info(path: Path, label: str) -> tuple[os.stat_result | None, OSError | ValueError | None]:
    """Keep fallback evidence early without masking a more specific parent error."""
    try:
        return _regular_file_info(path, label), None
    except (OSError, ValueError) as error:
        return None, error


def _read_schema_bytes(  # noqa: PLR0913 - optional discovery context avoids reopening scanned directories
    path: Path,
    label: str,
    *,
    root: Path | None = None,
    parent: int | None = None,
    expected: os.stat_result | None = None,
    identities: list[_DirectoryIdentity] | None = None,
    display_prefix: tuple[Path, Path] | None = None,
) -> bytes:
    """Read a regular file through its discovering directory when supplied."""
    descriptor = None
    opening = True
    captured: list[_DirectoryIdentity] = []
    try:
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        if _protected_reads_supported():
            flags |= os.O_NONBLOCK | os.O_NOFOLLOW
        if parent is not None:
            descriptor, expected = _open_regular_file(path, label, flags, expected, parent=parent)
        elif _search_anchors_supported():
            with _directory_descriptor(
                path.parent, root or path.parent, captured, label, display_prefix=display_prefix
            ) as ancestor:
                descriptor, expected = _open_regular_file(path, label, flags, expected, parent=ancestor)
        else:
            preliminary_error = None
            if expected is None:
                expected, preliminary_error = _preliminary_file_info(path, label)
            captured.extend(
                _ancestor_identities(path.parent, root or path.parent, label, display_prefix=display_prefix)
            )
            if preliminary_error is not None:
                raise preliminary_error
            descriptor, expected = _open_regular_file(path, label, flags, expected)
        opening = False
        payload = _read_regular_stream(descriptor, label, expected)
        _validate_directories(captured)
        if identities is not None:
            identities.extend(captured)
    except OSError as error:
        reason = (
            _opening_reason(error, path.name if parent is not None else path, parent=parent)
            if opening
            else _os_error_reason(error)
        )
        message = f"{label}: unable to read file ({reason})"
        raise ValueError(message) from error
    else:
        return payload
    finally:
        if descriptor is not None:
            with suppress(Exception):
                os.close(descriptor)


def _write_bytes(path: Path, payload: bytes, label: str) -> None:
    try:
        path.write_bytes(payload)
    except OSError as error:
        message = f"{label}: unable to write file ({_os_error_reason(error)})"
        raise ValueError(message) from error


def _output_entry(path: Path) -> os.stat_result | None:
    """Treat ordinary unresolved link targets as replaceable, preserving other failures."""
    try:
        return path.lstat()
    except OSError as error:
        if error.errno in {errno.ENOENT, errno.ENOTDIR, errno.ELOOP}:
            return None
        raise


def _resolved_output_mode(mode: int, *, linked: bool) -> int | None:
    if stat.S_ISREG(mode):
        return None if linked else mode & 0o777
    if linked and stat.S_ISDIR(mode):
        return None
    raise ValueError(_INVENTORY_DESTINATION_ERROR)


def _descriptor_namespaces() -> dict[tuple[int, int], Path]:
    """Recognize namespace directories without opening any descriptor aliases."""
    namespaces = {}
    for directory in (Path("/dev"), Path("/dev/fd"), Path("/proc")):
        try:
            info = directory.stat()
        except (FileNotFoundError, NotADirectoryError):
            continue
        if stat.S_ISDIR(info.st_mode):
            namespaces[info.st_dev, info.st_ino] = directory
    return namespaces


def _inventory_destination(path: Path) -> tuple[Path, int | None]:
    """Resolve components once, checking aliases before following any descriptor path."""
    absolute = path.absolute()
    resolved = Path("/") if os.name == "posix" else Path(absolute.anchor)
    alias_resolved = resolved
    alias_parents: list[Path] = []
    namespaces = _descriptor_namespaces() if os.name == "posix" else {}
    pending = list(absolute.parts[1:])
    expansions = 0
    linked = False
    destination = None
    while pending:
        remaining = "/" + alias_resolved.joinpath(*pending).as_posix().lstrip("/")
        if os.name == "posix" and _DESCRIPTOR_ALIAS.match(remaining):
            raise ValueError(_INVENTORY_DESTINATION_ERROR)
        component = pending.pop(0)
        if component == "..":
            resolved = resolved.parent
            alias_resolved = alias_parents.pop() if alias_parents else alias_resolved.parent
            continue
        candidate = resolved / component
        destination = candidate if not pending and not linked else destination
        info = _output_entry(candidate)
        if info is None:
            return destination or absolute, None
        if stat.S_ISLNK(info.st_mode):
            expansions += 1
            if expansions > _LINK_LIMIT:
                return destination or absolute, None
            linked |= not pending
            target = candidate.readlink()
            if target.is_absolute():
                resolved = Path("/") if os.name == "posix" else Path(target.anchor)
                alias_resolved = resolved
                alias_parents.clear()
                pending = list(target.parts[1:]) + pending
            else:
                pending = list(target.parts) + pending
        elif not pending:
            return destination or candidate, _resolved_output_mode(info.st_mode, linked=linked)
        elif not stat.S_ISDIR(info.st_mode):
            return destination or absolute, None
        else:
            resolved = candidate
            alias_parents.append(alias_resolved)
            alias_resolved = namespaces.get((info.st_dev, info.st_ino), alias_resolved / component)
    info = _output_entry(resolved)
    return destination or resolved, None if info is None else _resolved_output_mode(info.st_mode, linked=linked)


def _write_inventory_stdout(payload: bytes) -> None:
    """Emit exact bytes and disarm a failed standard stream before shutdown."""
    stream = getattr(sys.stdout, "buffer", None)
    if stream is None:
        message = "Schema inventory output: stdout must provide a binary buffer"
        raise ValueError(message)
    try:
        remaining = memoryview(payload)
        while remaining:
            written = stream.write(remaining)
            if written is None or written <= 0:
                raise BlockingIOError(errno.EAGAIN, "No stdout write progress")
            remaining = remaining[written:]
        stream.flush()
    except OSError as error:
        with suppress(Exception):
            sys.stdout.close()
        message = f"Schema inventory output: unable to write file ({_os_error_reason(error)})"
        raise ValueError(message) from error


def _write_inventory(path: Path, payload: bytes) -> None:
    """Replace files atomically with ordinary permissions."""
    temporary = None
    try:
        destination, mode = _inventory_destination(path)
        temporary = tempfile.TemporaryDirectory(dir=destination.parent, prefix=".schema-inventory-")
        folder = Path(temporary.name)
        folder.chmod(0o700)
        sibling = folder / "inventory.json"
        output = sibling.open("xb")
        try:
            if mode is not None:
                sibling.chmod(mode)
            output.write(payload)
            output.close()
        except BaseException:
            with suppress(Exception):
                output.close()
            raise
        os.replace(sibling, destination)  # noqa: PTH105 - replace the destination entry atomically
    except OSError as error:
        message = f"Schema inventory output: unable to write file ({_os_error_reason(error)})"
        raise ValueError(message) from error
    finally:
        if temporary is not None:
            with suppress(Exception):
                temporary.cleanup()


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


def _escaped_path(path: Path) -> str:
    """Display paths with JSON-style ASCII, control, quote and backslash escapes."""
    return json.dumps(path.as_posix())[1:-1]


def _schema_display_path(relative: Path) -> str:
    return _escaped_path(Path("schemas") / relative)


def _scan_child(  # noqa: PLR0913 - carry the scan context and the discovered entry's identity
    path: Path,
    root: Path,
    label: str,
    info: os.stat_result,
    *,
    ignore_symlinks: bool,
    descriptor: int | None,
    read_label: str | None,
    expected_tree: _ExpectedSchemaTree | None,
    logical_root: Path,
) -> _SchemaInventory:
    relative = path.relative_to(root)
    context = f"{label} at {_schema_display_path(relative)}"
    expected = _DirectoryIdentity(path, _identity(info), _identity(info), context)
    child = None
    try:
        if descriptor is not None:
            try:
                child = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            except OSError as error:
                reason = _opening_reason(error, path.name, parent=descriptor)
                message = f"{label}: unable to read inventory at {_schema_display_path(relative)} ({reason})"
                raise ValueError(message) from error
            if _identity(os.fstat(child)) != expected.target:
                message = f"{context}: directory changed during validation"
                raise ValueError(message)
        else:
            _validate_directories([expected])
        nested = _scan_inventory(
            path,
            root,
            label,
            ignore_symlinks=ignore_symlinks,
            descriptor=child,
            read_label=read_label,
            expected_tree=expected_tree,
            logical_root=logical_root,
        )
        return _SchemaInventory(nested.files, nested.directories, nested.payloads, (expected, *nested.identities))
    finally:
        if child is not None:
            with suppress(Exception):
                os.close(child)


def _scan_inventory(  # noqa: PLR0913 - optional reads share the discovery descriptor
    folder: Path,
    root: Path,
    label: str,
    *,
    ignore_symlinks: bool,
    logical_root: Path,
    descriptor: int | None = None,
    read_label: str | None = None,
    expected_tree: _ExpectedSchemaTree | None = None,
) -> _SchemaInventory:
    files: set[Path] = set()
    directories: set[Path] = set()
    payloads: dict[Path, bytes] = {}
    identities: list[_DirectoryIdentity] = []
    try:
        with os.scandir(folder if descriptor is None else descriptor) as entries:
            for entry in entries:
                path = folder / entry.name
                relative = path.relative_to(root)
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError as error:
                    message = (
                        f"{label}: unable to stat entry {_schema_display_path(relative)} ({_os_error_reason(error)})"
                    )
                    raise ValueError(message) from error
                if stat.S_ISLNK(info.st_mode):
                    if not ignore_symlinks:
                        message = f"{label}: symlinks are not permitted at {_schema_display_path(relative)}"
                        raise ValueError(message)
                elif stat.S_ISDIR(info.st_mode):
                    directories.add(relative)
                    if expected_tree is not None and relative not in expected_tree.directories:
                        continue
                    nested = _scan_child(
                        path,
                        root,
                        label,
                        info,
                        ignore_symlinks=ignore_symlinks,
                        descriptor=descriptor,
                        read_label=read_label,
                        expected_tree=expected_tree,
                        logical_root=logical_root,
                    )
                    files.update(nested.files)
                    directories.update(nested.directories)
                    payloads.update(nested.payloads)
                    identities.extend(nested.identities)
                elif stat.S_ISREG(info.st_mode):
                    files.add(relative)
                    if (
                        read_label is not None
                        and path.name.endswith(".json")
                        and (expected_tree is None or relative in expected_tree.files)
                    ):
                        payloads[relative] = _read_schema_bytes(
                            path,
                            f"{read_label} {_schema_display_path(relative)}",
                            root=logical_root,
                            parent=descriptor,
                            expected=info,
                            display_prefix=(root, Path("schemas")),
                        )
                else:
                    message = (
                        f"{label}: entries must be regular files or directories at {_schema_display_path(relative)}"
                    )
                    raise ValueError(message)
    except OSError as error:
        message = (
            f"{label}: unable to read inventory at {_schema_display_path(folder.relative_to(root))} "
            f"({_os_error_reason(error)})"
        )
        raise ValueError(message) from error
    return _SchemaInventory(frozenset(files), frozenset(directories), payloads, tuple(identities))


def _inventory_root(directory: Path, label: str) -> os.stat_result:
    try:
        info = directory.lstat()
    except FileNotFoundError as error:
        message = f"{label} is missing at schemas"
        raise ValueError(message) from error
    except OSError as error:
        message = f"{label}: unable to read inventory at schemas ({_os_error_reason(error)})"
        raise ValueError(message) from error
    if stat.S_ISLNK(info.st_mode):
        message = f"{label} must not be a symlink at schemas"
        raise ValueError(message)
    if not stat.S_ISDIR(info.st_mode):
        message = f"{label} is not a directory at schemas"
        raise ValueError(message)
    return info


def _schema_inventory(  # noqa: PLR0913 - expected built paths limit reads during descriptor-backed discovery
    directory: Path,
    label: str,
    *,
    ignore_symlinks: bool,
    root: Path,
    read_label: str | None = None,
    expected_tree: _ExpectedSchemaTree | None = None,
) -> _SchemaInventory:
    """Discover and optionally read JSON through the same directory descriptors."""
    info = _inventory_root(directory, label)
    captured = [_DirectoryIdentity(directory, _identity(info), _identity(info), f"{label} at schemas")]
    descriptor = None
    try:
        if _search_anchors_supported():
            with _directory_descriptor(directory.parent, root, captured, label) as parent:
                try:
                    descriptor = os.open(directory.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                except OSError as error:
                    reason = _opening_reason(error, directory.name, parent=parent)
                    message = f"{label}: unable to read inventory at schemas ({reason})"
                    raise ValueError(message) from error
        else:
            captured.extend(_ancestor_identities(directory.parent, root, label))
            if _anchored_paths_supported():
                try:
                    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                except OSError as error:
                    message = f"{label}: unable to read inventory at schemas ({_opening_reason(error, directory)})"
                    raise ValueError(message) from error
        if descriptor is not None and _identity(os.fstat(descriptor)) != captured[0].target:
            message = f"{label} at schemas: directory changed during validation"
            raise ValueError(message)
        inventory = _scan_inventory(
            directory,
            directory,
            label,
            ignore_symlinks=ignore_symlinks,
            descriptor=descriptor,
            read_label=read_label,
            expected_tree=expected_tree,
            logical_root=root,
        )
        captured.extend(inventory.identities)
        _validate_directories(captured)
        return _SchemaInventory(inventory.files, inventory.directories, inventory.payloads, tuple(captured))
    except OSError as error:
        message = f"{label}: unable to read inventory at schemas ({_os_error_reason(error)})"
        raise ValueError(message) from error
    finally:
        if descriptor is not None:
            with suppress(Exception):
                os.close(descriptor)


def _canonical_inventory(project: Path, *, read: bool = False) -> _SchemaInventory:
    inventory = _schema_inventory(
        project / "pyroma/schemas",
        "Canonical packaged schema directory (pyroma/schemas)",
        ignore_symlinks=True,
        root=project,
        read_label="Packaged schema" if read else None,
    )
    if not any(path.name.endswith(".json") for path in inventory.files):
        message = "Canonical packaged schema directory (pyroma/schemas) has no JSON schemas"
        raise ValueError(message)
    return inventory


def schema_paths(project: Path) -> list[Path]:
    """Discover every canonical JSON schema relative to the package."""
    return sorted(Path("schemas") / path for path in _canonical_inventory(project).files if path.name.endswith(".json"))


def _canonical_schemas(
    project: Path, identities: list[_DirectoryIdentity] | None = None
) -> dict[Path, _SchemaEvidence]:
    """Validate bytes gathered during traversal before accepting source evidence."""
    inventory = _canonical_inventory(project, read=True)
    schemas = {}
    for path, payload in sorted(inventory.payloads.items()):
        relative = Path("schemas") / path
        label = f"Packaged schema {_escaped_path(relative)}"
        document = _load_json(_decode_utf8(payload, label), label)
        schemas[relative] = _SchemaEvidence(payload, document)
    _validate_directories(inventory.identities)
    if identities is not None:
        identities.extend(inventory.identities)
    return schemas


def _json_equal(left: object, right: object) -> bool:
    """Compare JSON values without treating booleans as numbers."""
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_json_equal(value, right[key]) for key, value in left.items())
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_json_equal(a, b) for a, b in zip(left, right, strict=True))
    return left == right


def _check_published_contract(schemas: dict[Path, _SchemaEvidence]) -> None:
    """Require the JSON guide and canonical schema downloads to be published."""
    fetch(SITE_URL + "json/", label="JSON guide")
    for relative, evidence in schemas.items():
        label = f"Published schema {_escaped_path(relative)}"
        published = _load_json(fetch(SITE_URL + quote(relative.as_posix(), safe="/"), label=label), label)
        if not _json_equal(published, evidence.document):
            message = f"{label} does not match packaged schema"
            raise ValueError(message)


def check_online(project: Path, revision: str) -> None:
    """Verify live docs, the installation revision, and all published dates."""
    identities: list[_DirectoryIdentity] = []
    schemas = _canonical_schemas(project, identities)
    entries = changelog_entries(_read_utf8(project / "docs" / "changelog.md", "Local docs/changelog.md"))
    _validate_directories(identities)
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
        if _changelog_entry_date(entries, release_version, context).isoformat() != date:
            message = f"{context} has a mismatched date"
            raise ValueError(message)


def prepare_schemas(project: Path) -> None:
    """Copy the canonical packaged schemas into the documentation input."""
    identities: list[_DirectoryIdentity] = []
    schemas = _canonical_schemas(project, identities)
    destination = project / "docs" / "schemas"
    label = "Generated schema directory docs/schemas"
    try:
        mode = destination.lstat().st_mode
    except FileNotFoundError:
        mode = None
    except OSError as error:
        message = f"{label}: unable to inspect directory ({_os_error_reason(error)})"
        raise ValueError(message) from error
    _validate_directories(identities)
    if mode is not None:
        if stat.S_ISLNK(mode):
            message = f"{label} must not be a symlink"
            raise ValueError(message)
        if not stat.S_ISDIR(mode):
            message = f"{label} is not a directory"
            raise ValueError(message)
        try:
            shutil.rmtree(destination)
        except OSError as error:
            message = f"{label}: unable to remove directory ({_os_error_reason(error)})"
            raise ValueError(message) from error
    for relative, evidence in schemas.items():
        path = project / "docs" / relative
        label = f"Generated schema {_escaped_path(relative)}"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            message = f"{label}: unable to create directory ({_os_error_reason(error)})"
            raise ValueError(message) from error
        _write_bytes(path, evidence.payload, label)


def _inventory_difference(kind: str, built: set[Path], expected: set[Path]) -> str:
    extra = json.dumps(sorted(path.as_posix() for path in built - expected))
    missing = json.dumps(sorted(path.as_posix() for path in expected - built))
    return f"{kind}: extra {extra}; missing {missing}"


def check_schema_outputs(project: Path, site: Path) -> list[Path]:
    """Require exactly the permitted schema files and their original bytes."""
    identities: list[_DirectoryIdentity] = []
    schemas = _canonical_schemas(project, identities)
    guide = site / "json" / "index.html"
    if not _read_schema_bytes(guide, "Built JSON guide json/index.html", root=site, identities=identities):
        message = "Built JSON guide json/index.html must not be empty"
        raise ValueError(message)
    expected_tree = _ExpectedSchemaTree(frozenset(relative.relative_to("schemas") for relative in schemas))
    inventory = _schema_inventory(
        site / "schemas",
        "Built schema directory (schemas relative to the site directory)",
        ignore_symlinks=False,
        root=site,
        read_label="Built schema",
        expected_tree=expected_tree,
    )
    built = {Path("schemas") / path for path in inventory.files}
    directories = {Path("schemas") / path for path in inventory.directories}
    expected = {Path("schemas") / path for path in expected_tree.files}
    expected_directories = {Path("schemas"), *(Path("schemas") / path for path in expected_tree.directories)}
    directories.add(Path("schemas"))
    if built != expected or directories != expected_directories:
        message = "Built schema files do not match the canonical packaged inventory; " + "; ".join(
            [
                _inventory_difference("files", built, expected),
                _inventory_difference("directories", directories, expected_directories),
            ]
        )
        raise ValueError(message)
    for relative, evidence in schemas.items():
        label = f"Built schema {_escaped_path(relative)}"
        if inventory.payloads[relative.relative_to("schemas")] != evidence.payload:
            message = f"{label} does not match packaged schema bytes"
            raise ValueError(message)
    _validate_directories([*identities, *inventory.identities])
    return list(schemas)


def main() -> None:
    """Expose offline checks, tagged-release checks, and schema preparation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "schemas", "check-schemas"))
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--release-tag", default="")
    parser.add_argument("--online", action="store_true")
    parser.add_argument("--site", type=Path, help="Built site for check-schemas (default: PROJECT/site)")
    parser.add_argument("--inventory-output", help="Write verified schema paths as compact JSON; - uses stdout")
    args = parser.parse_args()
    if args.command != "check-schemas" and (args.site is not None or args.inventory_output is not None):
        parser.error("--site and --inventory-output require check-schemas")
    try:
        if args.command == "schemas":
            prepare_schemas(args.project)
        elif args.command == "check-schemas":
            paths = check_schema_outputs(args.project, args.site if args.site is not None else args.project / "site")
            if args.inventory_output is not None:
                payload = (json.dumps([path.as_posix() for path in paths], separators=(",", ":")) + "\n").encode(
                    "utf-8"
                )
                if args.inventory_output == "-":
                    _write_inventory_stdout(payload)
                else:
                    _write_inventory(Path(args.inventory_output), payload)
        else:
            revision = check_offline(args.project, args.release_tag)
            if args.online:
                check_online(args.project, revision)
    except (OSError, ValueError, KeyError, TypeError, URLError) as error:
        parser.exit(1, f"Release documentation check failed: {error}\n")


if __name__ == "__main__":
    main()
