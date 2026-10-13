"""Validate release documentation without depending on live services."""

import errno
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tomllib
from collections.abc import Callable
from http.client import IncompleteRead
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock
from urllib.error import HTTPError, URLError
from urllib.parse import quote

import pytest
import yaml
from jsonschema import Draft202012Validator
from markdown_it import MarkdownIt
from packaging.version import Version
from zest.releaser import utils
from zest.releaser.prerelease import HISTORY_HEADER, Prereleaser

import pyroma
from ci import release_docs

ROOT = Path(__file__).resolve().parents[2]
VERSION = "9.0.0"
NEXT_VERSION = "9.1.0"
DATE = "2026-10-12"
RELEASE_CHECKS_MARKER = "<!-- pyroma-release-checks -->"


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, f"Expected one mutation target {old!r} in {path.name}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def workflow_step(steps: list[dict[str, str]], step_id: str) -> dict[str, str]:
    matches = [step for step in steps if step.get("id") == step_id]
    assert len(matches) == 1, f"Expected exactly one workflow step with id {step_id!r}; found {len(matches)}"
    return matches[0]


def workflow_bash_command(step: dict[str, str]) -> list[str]:
    assert step.get("shell") == "bash", f"Workflow step {step['id']!r} must explicitly use shell: bash"
    return ["/bin/bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", step["run"]]


def schema_build_environment(project: Path) -> dict[str, str]:
    """Use the real checker with the test interpreter instead of resolving uv."""
    scripts = project / "ci"
    scripts.mkdir(exist_ok=True)
    shutil.copyfile(ROOT / "ci/release_docs.py", scripts / "release_docs.py")
    uv = project / "uv"
    uv.write_text(
        '#!/bin/sh\n[ "$1 $2 $3 $4 $5" = "run --locked --only-group docs python" ] || exit 2\n'
        'shift 5\nexec "$DOCS_TEST_PYTHON" "$@"\n',
        encoding="utf-8",
    )
    uv.chmod(0o755)
    runner = project / "runner"
    runner.mkdir(exist_ok=True)
    return {
        **os.environ,
        "PATH": str(project) + os.pathsep + os.environ["PATH"],
        "DOCS_TEST_PYTHON": sys.executable,
        "RUNNER_TEMP": str(runner),
        "GITHUB_OUTPUT": str(runner / "output"),
    }


def run_schema_build(project: Path, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Execute the actual build step with the same bounded Bash invocation."""
    workflow = yaml.safe_load((ROOT / ".github/workflows/docs.yml").read_text())
    return subprocess.run(  # noqa: S603 - exercise the repository's actual workflow command
        workflow_bash_command(workflow_step(workflow["jobs"]["build"]["steps"], "schema_build_outputs")),
        cwd=project,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


def scandir_entries(*entries: Mock) -> Mock:
    """Provide context-managed entries for stat and special-file simulations."""
    scan = Mock()
    scan.__enter__ = Mock(return_value=iter(entries))
    scan.__exit__ = Mock(return_value=False)
    return scan


def mock_inventory_stream(monkeypatch: pytest.MonkeyPatch, output: Mock) -> None:
    """Create the exclusive file before substituting a failing stream."""
    original = Path.open

    def open_file(path: Path, mode: str = "r", **kwargs: object) -> object:
        if mode == "xb":
            with original(path, mode, **kwargs):
                pass
            return output
        return original(path, mode, **kwargs)

    monkeypatch.setattr(Path, "open", open_file)


def fail_inventory_stream(monkeypatch: pytest.MonkeyPatch, phase: str, error: OSError) -> None:
    """Inject acquisition, partial-write and close failures in a real stream."""
    original_open = Path.open

    def open_output(path: Path, mode: str = "r", *args: object, **kwargs: object) -> object:
        if mode != "xb":
            return original_open(path, mode, *args, **kwargs)
        if phase == "open":
            raise error
        actual = original_open(path, mode, *args, **kwargs)
        output = Mock()

        def write(payload: bytes) -> int:
            if phase == "write":
                actual.write(payload[:3])
                raise error
            return actual.write(payload)

        def close() -> None:
            actual.close()
            if phase == "close":
                raise error

        output.write.side_effect = write
        output.close.side_effect = close
        return output

    monkeypatch.setattr(Path, "open", open_output)


def run_fifo_checks(project: Path, *, race: str = "", timeout: float = 10) -> subprocess.CompletedProcess[str]:
    """Bound actual scan/read races and explicitly import the trusted source."""
    script = """
import os
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from ci import release_docs

project = Path(sys.argv[2])
race = sys.argv[3]
original_paths = release_docs.schema_paths
original_inventory = release_docs._schema_inventory
victim = project / ("site" if race.startswith("built") else "pyroma") / "schemas/v1/error.json"
previous = victim.read_bytes()
def swap():
    victim.unlink()
    if race.endswith("fifo"):
        os.mkfifo(victim)
    else:
        victim.symlink_to(project / "pyroma/schemas/v1/rating.json")
def discover(project):
    paths = original_paths(project)
    swap()
    return paths
def inventory(directory, label, **kwargs):
    result = original_inventory(directory, label, **kwargs)
    if directory == project / "site/schemas":
        swap()
    return result
if race.startswith("canonical"):
    release_docs.schema_paths = discover
elif race.startswith("built"):
    release_docs._schema_inventory = inventory
def fetch(*args, **kwargs):
    raise AssertionError("Special entries must fail before fetching")
release_docs.fetch = fetch
commands = [["check-schemas"]] if race.startswith("built") else [["schemas"], ["check", "--online"]]
for command in commands:
    sys.argv = ["release_docs", *command, "--project", str(project)]
    try:
        release_docs.main()
    except SystemExit as error:
        assert error.code == 1
    else:
        raise AssertionError("Special entries must be rejected")
    finally:
        if race:
            victim.unlink()
            victim.write_bytes(previous)
"""
    return subprocess.run(  # noqa: S603 - bound pipe races and verify zero fetches in the child
        [sys.executable, "-c", script, str(ROOT), str(project), race],
        cwd=project,
        env={**os.environ, "PYTHONSAFEPATH": "1"},
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def prepare_site(project: Path) -> None:
    release_docs.prepare_schemas(project)
    shutil.copytree(project / "docs/schemas", project / "site/schemas")
    guide = project / "site/json/index.html"
    guide.parent.mkdir(parents=True)
    guide.write_text("JSON guide", encoding="utf-8")


def release_check_commands(text: str) -> str:
    tokens = MarkdownIt("commonmark").parse(text)
    matches = [
        index
        for index, token in enumerate(tokens)
        if token.type == "html_block" and token.content.strip() == RELEASE_CHECKS_MARKER
    ]
    assert len(matches) == 1, f"Expected exactly one {RELEASE_CHECKS_MARKER} block; found {len(matches)}"
    marker = tokens[matches[0]]
    index = matches[0] + 1
    assert index < len(tokens), "Release-check marker must precede a console fence"
    token = tokens[index]
    assert token.level == marker.level, "Release-check marker and console fence must share a Markdown container"
    assert token.type == "fence", "Release-check marker must precede a console fence"
    assert token.info == "console", "Release-check marker must precede a console fence"
    content_lines = token.content.count("\n") + bool(token.content and not token.content.endswith("\n"))
    assert token.map is not None, "Release-check console fence must have source lines"
    assert token.map[1] - token.map[0] == content_lines + 2, (
        "Release-check console fence must have an explicit closing fence"
    )
    return token.content


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "pyroma621"\nversion = "{VERSION}"\n'
        'classifiers = ["Development Status :: 5 - Production/Stable"]\n'
        f'[project.urls]\nDocumentation = "{release_docs.SITE_URL}"\n',
        encoding="utf-8",
    )
    (tmp_path / "zensical.toml").write_text(
        f'[project]\nsite_url = "{release_docs.SITE_URL}"\n',
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text(
        f"Install with `pip install pyroma621`.\nDocumentation: {release_docs.SITE_URL}\n"
        f'<!-- pyroma-pre-commit -->\n```yaml\nrepos:\n  - repo: {release_docs.REPOSITORY}\n    rev: "{VERSION}"\n'
        "    hooks:\n      - id: pyroma\n```\n",
        encoding="utf-8",
    )
    (tmp_path / ".pre-commit-hooks.yaml").write_text("- id: pyroma\n  entry: pyroma\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    history = "".join(
        f"## {version} ({date})\n\n- Released.\n\n" for version, date in release_docs.RELEASE_DATES.items()
    )
    (tmp_path / "docs" / "changelog.md").write_text(
        f"# Changelog\n\n## {VERSION} ({DATE})\n\n- Released.\n\n{history}",
        encoding="utf-8",
    )
    shutil.copytree(Path(pyroma.__file__).parent / "schemas", tmp_path / "pyroma" / "schemas")
    return tmp_path


@pytest.fixture
def online_evidence(project: Path) -> dict[str, str]:
    base = f"https://raw.githubusercontent.com/hbmartin/pyroma621/{VERSION}/"
    responses = {
        release_docs.SITE_URL + page: "documentation" for page in ("", "rules/", "upgrading/", "changelog/", "json/")
    }
    package = project / "pyroma"
    for path in (package / "schemas").rglob("*.json"):
        relative = path.relative_to(package)
        schema = json.loads(path.read_text(encoding="utf-8"))
        responses[release_docs.SITE_URL + relative.as_posix()] = json.dumps(schema, sort_keys=True)
    responses[base + "pyproject.toml"] = f'[project]\nname="pyroma621"\nversion="{VERSION}"\n'
    responses[base + ".pre-commit-hooks.yaml"] = "- id: pyroma\n  entry: pyroma\n"
    responses["https://pypi.org/pypi/pyroma621/json"] = json.dumps(
        {"releases": {VERSION: [{"upload_time_iso_8601": f"{DATE}T17:42:33Z"}], "empty": []}}
    )
    return responses


def test_documentation_consistency() -> None:
    revision = release_docs.check_offline(ROOT)
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    assert not Version(revision).is_devrelease
    assert Version(version) >= Version(revision)


def validate_examples(project: Path, validator: Callable[[str], Draft202012Validator]) -> None:
    for path in (project / "docs").glob("*.md"):
        for block in re.findall(r"```json\n(.*?)\n```", path.read_text(encoding="utf-8"), re.DOTALL):
            document = json.loads(block)
            validator("error" if "error" in document else "rating").validate(document)
            Version(document["_meta"]["pyroma"])


def test_documentation_json_examples(validator: Callable[[str], Draft202012Validator]) -> None:
    validate_examples(ROOT, validator)


@pytest.mark.parametrize(
    ("filename", "old", "new", "message"),
    [
        ("README.md", "pip install pyroma621", "pip install pyroma", "distribution"),
        ("README.md", "https://hbmartin.github.io/pyroma621/", "https://hbmartin.github.io/pyroma/", "canonical"),
        ("README.md", "repo: https://github.com/hbmartin/pyroma621", "repo: https://github.com/regebro/pyroma", "hook"),
        ("README.md", f'rev: "{VERSION}"', "", "revision"),
        ("docs/changelog.md", "6.1.0 (2026-10-12)", "6.1.0 (unreleased)", "dated"),
        ("docs/changelog.md", "6.0.0 (2026-08-01)", "6.0.0 (2026-08-02)", "2026-08-01"),
        ("zensical.toml", release_docs.SITE_URL, "https://example.com/", "canonical"),
    ],
)
def test_documentation_regressions(project: Path, filename: str, old: str, new: str, message: str) -> None:
    replace_once(project / filename, old, new)
    with pytest.raises(ValueError, match=message):
        release_docs.check_offline(project)


def test_future_release_needs_a_date(project: Path) -> None:
    replace_once(project / "pyproject.toml", f'version = "{VERSION}"', f'version = "{NEXT_VERSION}"')
    assert release_docs.check_offline(project) == VERSION
    with pytest.raises(ValueError, match="has no matching heading"):
        release_docs.check_offline(project, NEXT_VERSION)
    with pytest.raises(ValueError, match="does not match"):
        release_docs.check_offline(project, VERSION)


@pytest.mark.parametrize("revision", [VERSION, NEXT_VERSION, f"v{NEXT_VERSION}", "9.1"])
@pytest.mark.parametrize("release_tag", [NEXT_VERSION, f"v{NEXT_VERSION}"])
def test_tagged_release_requires_matching_hook(project: Path, revision: str, release_tag: str) -> None:
    replace_once(project / "pyproject.toml", f'version = "{VERSION}"', f'version = "{NEXT_VERSION}"')
    changelog = project / "docs" / "changelog.md"
    changelog.write_text(changelog.read_text() + f"\n## {NEXT_VERSION} ({DATE})\n")
    replace_once(project / "README.md", f'rev: "{VERSION}"', f'rev: "{revision}"')
    assert release_docs.check_offline(project) == revision
    if revision != release_tag:
        with pytest.raises(ValueError, match="pre-commit revision does not match release tag"):
            release_docs.check_offline(project, release_tag)
    else:
        assert release_docs.check_offline(project, release_tag) == revision


@pytest.mark.parametrize("release_tag", ["9.0", "v9.0.0", "v9.0.0.0"])
def test_package_versions_allow_equivalence_but_hook_refs_are_literal(project: Path, release_tag: str) -> None:
    replace_once(project / "README.md", f'rev: "{VERSION}"', f'rev: "{release_tag}"')
    assert release_docs.check_offline(project, release_tag) == release_tag


@pytest.mark.parametrize("version", [VERSION, "9.1.0rc1", "9.1.0.dev0"])
def test_beta_is_allowed_except_for_final_tagged_releases(project: Path, version: str) -> None:
    replace_once(project / "pyproject.toml", f'version = "{VERSION}"', f'version = "{version}"')
    replace_once(project / "pyproject.toml", "5 - Production/Stable", "4 - Beta")
    replace_once(project / "README.md", f'rev: "{VERSION}"', f'rev: "{version}"')
    replace_once(project / "docs" / "changelog.md", f"## {VERSION} ({DATE})", f"## {version} ({DATE})")
    assert release_docs.check_offline(project) == version
    if version == VERSION:
        with pytest.raises(ValueError, match=r"Final pyroma621 releases require.*Production/Stable"):
            release_docs.check_offline(project, version)
    else:
        assert release_docs.check_offline(project, version) == version


@pytest.mark.parametrize("status", ["5 - Production/Stable", "6 - Mature"])
def test_final_release_accepts_stable_or_mature(project: Path, status: str) -> None:
    replace_once(project / "pyproject.toml", "5 - Production/Stable", status)
    assert release_docs.check_offline(project, VERSION) == VERSION


@pytest.mark.parametrize("status", ["1 - Planning", "2 - Pre-Alpha", "3 - Alpha", "4 - Beta", "7 - Inactive", ""])
def test_final_release_rejects_other_or_missing_status(project: Path, status: str) -> None:
    metadata = project / "pyproject.toml"
    replacement = f'classifiers = ["Development Status :: {status}"]' if status else "classifiers = []"
    replace_once(metadata, 'classifiers = ["Development Status :: 5 - Production/Stable"]', replacement)
    with pytest.raises(ValueError, match="Production/Stable or 6 - Mature"):
        release_docs.check_offline(project, VERSION)


@pytest.mark.parametrize("release_tag", ["", VERSION])
def test_local_hook_manifest_is_checked_without_network(
    monkeypatch: pytest.MonkeyPatch, project: Path, release_tag: str
) -> None:
    blocked = Mock(side_effect=AssertionError("Local hook validation must not fetch release evidence"))
    monkeypatch.setattr(release_docs, "fetch", blocked)
    if not release_tag:
        replace_once(project / "pyproject.toml", f'version = "{VERSION}"', f'version = "{VERSION}.dev0"')
    manifest = project / ".pre-commit-hooks.yaml"
    manifest.write_text("- id: other\n  entry: other\n- id: pyroma\n  entry: pyroma\n", encoding="utf-8")
    assert release_docs.check_offline(project, release_tag) == VERSION
    manifest.write_text("- id: other\n  entry: other\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"Local \.pre-commit-hooks.yaml.*pyroma"):
        release_docs.check_offline(project, release_tag)
    blocked.assert_not_called()


def test_missing_local_hook_manifest_fails_clearly(
    monkeypatch: pytest.MonkeyPatch, project: Path, capsys: pytest.CaptureFixture
) -> None:
    (project / ".pre-commit-hooks.yaml").unlink()
    monkeypatch.setattr("sys.argv", ["release_docs", "check", "--project", str(project), "--release-tag", VERSION])
    with pytest.raises(SystemExit) as raised:
        release_docs.main()
    assert raised.value.code == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert ".pre-commit-hooks.yaml" in err
    assert "Traceback" not in err


@pytest.mark.parametrize("failure", ["", "distribution", "hook", "date"])
def test_online_release_evidence(
    monkeypatch: pytest.MonkeyPatch, project: Path, online_evidence: dict[str, str], failure: str
) -> None:
    base = f"https://raw.githubusercontent.com/hbmartin/pyroma621/{VERSION}/"
    if failure == "distribution":
        online_evidence[base + "pyproject.toml"] = f'[project]\nname="pyroma"\nversion="{VERSION}"\n'
    elif failure == "hook":
        online_evidence[base + ".pre-commit-hooks.yaml"] = ""
    elif failure == "date":
        url = "https://pypi.org/pypi/pyroma621/json"
        online_evidence[url] = json.dumps({"releases": {VERSION: [{"upload_time_iso_8601": "2026-10-13T00:00:00Z"}]}})
    fetch = Mock(side_effect=lambda url, **_kwargs: online_evidence[url])
    monkeypatch.setattr(release_docs, "fetch", fetch)
    if failure:
        with pytest.raises(ValueError, match=r"pre-commit|hook|Changelog"):
            release_docs.check_online(project, VERSION)
    else:
        release_docs.check_online(project, VERSION)
        assert {call.args[0] for call in fetch.call_args_list} == set(online_evidence)
        assert {call.kwargs["label"] for call in fetch.call_args_list} == {
            "Documentation home",
            "Rules guide",
            "Upgrade guide",
            "Changelog",
            "JSON guide",
            "Published schema schemas/v1/rating.json",
            "Published schema schemas/v1/error.json",
            "Hook package metadata",
            "Published hook manifest",
            "PyPI release metadata",
        }


@pytest.mark.parametrize("missing", ["json/", "schemas/v1/rating.json", "schemas/v1/error.json"])
def test_online_contract_links_must_exist(
    monkeypatch: pytest.MonkeyPatch, project: Path, online_evidence: dict[str, str], missing: str
) -> None:
    def evidence(url: str, **_kwargs: str) -> str:
        if url == release_docs.SITE_URL + missing:
            message = "missing contract download"
            raise URLError(message)
        return online_evidence[url]

    monkeypatch.setattr(release_docs, "fetch", evidence)
    with pytest.raises(URLError, match="missing contract download"):
        release_docs.check_online(project, VERSION)


@pytest.mark.parametrize("kind", ["rating", "error"])
@pytest.mark.parametrize("failure", ["invalid", "mismatch"])
def test_online_schemas_must_match_package(
    monkeypatch: pytest.MonkeyPatch, project: Path, online_evidence: dict[str, str], kind: str, failure: str
) -> None:
    url = release_docs.SITE_URL + f"schemas/v1/{kind}.json"
    if failure == "invalid":
        online_evidence[url] = "not JSON"
        message = f"Published schema schemas/v1/{kind}.json must contain valid JSON"
    else:
        schema = json.loads(online_evidence[url])
        schema["title"] = "Stale published schema"
        online_evidence[url] = json.dumps(schema)
        message = f"Published schema schemas/v1/{kind}.json does not match packaged schema"
    monkeypatch.setattr(release_docs, "fetch", lambda url, **_kwargs: online_evidence[url])
    with pytest.raises(ValueError, match=message):
        release_docs.check_online(project, VERSION)


def test_schema_publication(project: Path) -> None:
    release_docs.prepare_schemas(project)
    for path in (project / "pyroma" / "schemas").rglob("*.json"):
        relative = path.relative_to(project / "pyroma")
        assert (project / "docs" / relative).read_bytes() == path.read_bytes()


@pytest.mark.parametrize(
    "label", [None, "Published rating schema", "Published error schema", "Published hook manifest"]
)
def test_fetch_is_bounded(monkeypatch: pytest.MonkeyPatch, label: str | None) -> None:
    response = Mock()
    response.read.return_value = b"evidence"
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=None)
    opener = Mock(return_value=response)
    monkeypatch.setattr(release_docs, "urlopen", opener)
    assert release_docs.fetch("https://example.com/") == "evidence"
    opener.assert_called_once_with("https://example.com/", timeout=15)
    response.read.assert_called_once_with(release_docs._RESPONSE_LIMIT + 1)
    response.read.return_value = b"x" * (release_docs._RESPONSE_LIMIT + 1)
    labels = {} if label is None else {"label": label}
    with pytest.raises(ValueError, match="response limit") as raised:
        release_docs.fetch("https://username:password@example.com/?token=secret", **labels)
    assert (label or "Release evidence") in str(raised.value)
    assert "secret" not in str(raised.value)
    assert "https://" not in str(raised.value)
    assert "username" not in str(raised.value)
    assert "password" not in str(raised.value)
    with pytest.raises(ValueError, match="HTTPS"):
        release_docs.fetch("http://example.com/")


def test_online_failures_are_controlled(
    monkeypatch: pytest.MonkeyPatch, project: Path, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(release_docs, "check_online", Mock(side_effect=URLError("unavailable")))
    monkeypatch.setattr("sys.argv", ["release_docs", "check", "--online", "--project", str(project)])
    with pytest.raises(SystemExit) as raised:
        release_docs.main()
    assert raised.value.code == 1
    assert "unavailable" in capsys.readouterr().err


@pytest.mark.parametrize("suffix", ["", ".git", "/", ".git/"])
@pytest.mark.parametrize("quoted", [False, True])
def test_precommit_revision_belongs_to_matching_repository(suffix: str, quoted: bool) -> None:
    repository = release_docs.REPOSITORY + suffix
    if quoted:
        repository = f'"{repository}"'
    readme = (
        "<!-- pyroma-pre-commit -->\n```yaml\nrepos:\n"
        "  - repo: https://github.com/example/other\n    rev: 99.0.0\n    hooks:\n      - id: pyroma\n"
        f"  - repo: {repository}\n    rev: 98.0.0\n    hooks:\n      - id: unrelated\n"
        f"  - repo: {repository}\n    rev: {VERSION}\n    hooks:\n      - id: pyroma\n```\n"
    )
    assert release_docs.precommit_revision(readme) == VERSION


@pytest.mark.parametrize("example", ["repos: [", "a: 1\n---\nb: 2", "...", "!!python/object:example.Unsafe {}"])
def test_unmarked_yaml_does_not_affect_hook_validation(project: Path, example: str) -> None:
    readme = (project / "README.md").read_text()
    unrelated = f"\n```yaml\n{example}\n```\n"
    assert release_docs.precommit_revision(unrelated + readme + unrelated) == VERSION


@pytest.mark.parametrize(
    "mention",
    [
        "Mark examples with <!-- pyroma-pre-commit -->.",
        "`<!-- pyroma-pre-commit -->`",
        "> `<!-- pyroma-pre-commit -->`",
        "    <!-- pyroma-pre-commit -->",
        "```markdown\n<!-- pyroma-pre-commit -->\n```",
        "~~~markdown\n<!-- pyroma-pre-commit -->\n~~~",
        "````markdown\n```yaml\n<!-- pyroma-pre-commit -->\n```\n````",
    ],
)
def test_literal_marker_mentions_do_not_affect_hook_validation(project: Path, mention: str) -> None:
    readme = (project / "README.md").read_text()
    assert release_docs.precommit_revision(mention + "\n" + readme + "\n" + mention) == VERSION


def test_fenced_hook_example_is_only_a_literal(project: Path) -> None:
    readme = (project / "README.md").read_text()
    literal = readme.replace(f'rev: "{VERSION}"', 'rev: "0.1.0"')
    assert release_docs.precommit_revision("````markdown\n" + literal + "````\n" + readme) == VERSION


@pytest.mark.parametrize("delimiter", ["```", "~~~", "````"])
@pytest.mark.parametrize("blank_lines", ["", "\n\n"])
def test_marker_allows_blank_lines_and_matching_fences(project: Path, delimiter: str, blank_lines: str) -> None:
    readme = (project / "README.md").read_text().replace("```", delimiter)
    readme = readme.replace("<!-- pyroma-pre-commit -->\n", "<!-- pyroma-pre-commit -->\n" + blank_lines)
    assert release_docs.precommit_revision(readme) == VERSION


@pytest.mark.parametrize(
    "following", ["", "prose\n", "```text\nrepos: []\n```", "```yaml\nrepos: []", "````yaml\nrepos: []\n```"]
)
def test_incomplete_marked_examples_identify_marker_line(project: Path, following: str) -> None:
    readme = (project / "README.md").read_text()
    marker_line = len(readme.splitlines()) + 1
    readme += "<!-- pyroma-pre-commit -->\n" + following
    with pytest.raises(ValueError, match=f"README line {marker_line}:.*complete yaml code block"):
        release_docs.precommit_revision(readme)


@pytest.mark.parametrize("replacement", ["", "<!-- unrelated -->", "<!-- pyroma-pre-commit -->\n```yaml\nrepos: ["])
def test_hook_example_requires_a_marker_and_complete_fence(project: Path, replacement: str) -> None:
    readme = (project / "README.md").read_text()
    if "repos: [" in replacement:
        readme += replacement
    else:
        readme = readme.replace("<!-- pyroma-pre-commit -->", replacement)
    with pytest.raises(ValueError, match="pyroma hook"):
        release_docs.precommit_revision(readme)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (release_docs.REPOSITORY, release_docs.REPOSITORY + "-fork"),
        ("id: pyroma", "id: pyroma-strict"),
        ("id: pyroma", "id: unrelated # id: pyroma"),
        ("hooks:\n      - id: pyroma", "hooks: pyroma"),
        ("repos:\n", "examples:\n"),
    ],
)
def test_precommit_revision_requires_exact_repository_and_hook(project: Path, old: str, new: str) -> None:
    readme = project / "README.md"
    replace_once(readme, old, new)
    with pytest.raises(ValueError, match="pyroma hook"):
        release_docs.precommit_revision(readme.read_text())


@pytest.mark.parametrize("revision", ["", "null", "6.2", '""', '" "', "[9.0.0]"])
def test_precommit_revision_requires_nonempty_string(project: Path, revision: str) -> None:
    readme = project / "README.md"
    replace_once(readme, f'rev: "{VERSION}"', f"rev: {revision}")
    with pytest.raises(ValueError, match="release revision"):
        release_docs.precommit_revision(readme.read_text())


@pytest.mark.parametrize("same_revision", [False, True])
def test_precommit_repeated_examples_require_consistent_revisions(project: Path, same_revision: bool) -> None:
    readme = (project / "README.md").read_text()
    assert readme.count(f'rev: "{VERSION}"') == 1
    repeated = readme if same_revision else readme.replace(f'rev: "{VERSION}"', f'rev: "{NEXT_VERSION}"', 1)
    if same_revision:
        assert release_docs.precommit_revision(readme + repeated) == VERSION
    else:
        with pytest.raises(ValueError, match="conflicting release revisions"):
            release_docs.precommit_revision(readme + repeated)


@pytest.mark.parametrize("example", ["repos: [", "!!python/object:example.Unsafe {}", "a: 1\n---\nb: 2"])
def test_yaml_failures_are_controlled(
    monkeypatch: pytest.MonkeyPatch, project: Path, capsys: pytest.CaptureFixture, example: str
) -> None:
    (project / "README.md").write_text(
        f"pip install pyroma621\n{release_docs.SITE_URL}\n<!-- pyroma-pre-commit -->\n```yaml\n{example}\n```\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("sys.argv", ["release_docs", "check", "--project", str(project)])
    with pytest.raises(SystemExit) as raised:
        release_docs.main()
    assert raised.value.code == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert "README pre-commit example at line 3 must contain valid YAML" in err
    assert "Traceback" not in err


@pytest.mark.parametrize(
    "hooks",
    [
        "",
        "[]",
        "- id: pyroma-strict\n  entry: pyroma\n",
        "- id: pyroma\n  entry: pyroma-strict\n",
        "- id: pyroma\n  entry: unrelated\n- id: unrelated\n  entry: pyroma\n",
        "id: pyroma\nentry: pyroma\n",
        "- pyroma\n",
        "- id: [\n",
    ],
)
@pytest.mark.parametrize("online", [False, True])
def test_hook_fields_belong_to_the_same_exact_entry(
    monkeypatch: pytest.MonkeyPatch, project: Path, online_evidence: dict[str, str], hooks: str, online: bool
) -> None:
    if online:
        url = f"https://raw.githubusercontent.com/hbmartin/pyroma621/{VERSION}/.pre-commit-hooks.yaml"
        online_evidence[url] = hooks
        monkeypatch.setattr(release_docs, "fetch", lambda url, **_kwargs: online_evidence[url])
    else:
        (project / ".pre-commit-hooks.yaml").write_text(hooks, encoding="utf-8")
    check = release_docs.check_online if online else release_docs.check_offline
    with pytest.raises(ValueError, match="hook"):
        check(project, VERSION)


def test_schema_preparation_rebuilds_only_generated_tree(project: Path) -> None:
    release_docs.prepare_schemas(project)
    stale = project / "docs" / "schemas" / "v0" / "stale.json"
    stale.parent.mkdir()
    stale.write_text("stale", encoding="utf-8")
    future = project / "pyroma" / "schemas" / "v2" / "rating.json"
    future.parent.mkdir()
    future.write_bytes((project / "pyroma" / "schemas" / "v1" / "rating.json").read_bytes())
    unrelated = project / "docs" / "unrelated.md"
    unrelated.write_text("keep", encoding="utf-8")
    release_docs.prepare_schemas(project)
    assert not stale.exists()
    assert (project / "docs" / "schemas" / "v2" / "rating.json").read_bytes() == future.read_bytes()
    assert unrelated.read_text() == "keep"


def test_missing_canonical_schemas_does_not_remove_generated_files(project: Path) -> None:
    release_docs.prepare_schemas(project)
    published = project / "docs" / "schemas" / "v1" / "rating.json"
    original = published.read_bytes()
    shutil.rmtree(project / "pyroma" / "schemas")
    with pytest.raises(ValueError, match=r"Canonical packaged schema directory \(pyroma/schemas\) is missing"):
        release_docs.prepare_schemas(project)
    assert published.read_bytes() == original


def test_prerelease_dates_only_the_unreleased_heading(tmp_path: Path) -> None:
    history = tmp_path / "changelog.md"
    original = f"# Changelog\n\n## {NEXT_VERSION} (unreleased)\n\n- New feature.\n\n## {VERSION} ({DATE})\n"
    history.write_text(original, encoding="utf-8")
    version = NEXT_VERSION + ".dev0"
    releaser = Prereleaser.__new__(Prereleaser)
    releaser.vcs = Mock(version=version)
    releaser.vcs.history_file.return_value = str(history)
    releaser.zest_releaser_config = Mock()
    releaser.zest_releaser_config.history_format.return_value = "md"
    releaser.zest_releaser_config.history_file.return_value = str(history)
    releaser.data = {"today": DATE, "history_header": HISTORY_HEADER}
    releaser._grab_version(initial=True)
    releaser._grab_history()
    first = releaser.data["headings"][0]
    assert first["date"] == "unreleased"
    assert first["version"].removeprefix("## ") == utils.cleanup_version(version)
    releaser._change_header()
    expected = original.split("\n")
    expected[first["line"]] = f"## {utils.cleanup_version(version)} ({DATE})"
    assert releaser.data["history_lines"] == expected
    assert f"## {VERSION} ({DATE})" in releaser.data["history_lines"]


def test_release_lifecycle_keeps_examples_and_offline_checks_valid(
    project: Path, validator: Callable[[str], Draft202012Validator], monkeypatch: pytest.MonkeyPatch
) -> None:
    blocked = Mock(side_effect=AssertionError("Offline releases must not fetch live documentation"))
    monkeypatch.setattr(release_docs, "fetch", blocked)
    examples = project / "docs" / "json.md"
    shutil.copy(ROOT / "docs" / "json.md", examples)
    original_examples = examples.read_bytes()
    metadata = project / "pyproject.toml"
    history = project / "docs" / "changelog.md"
    replace_once(metadata, f'version = "{VERSION}"', f'version = "{NEXT_VERSION}.dev0"')
    replace_once(metadata, "5 - Production/Stable", "4 - Beta")
    history.write_text(history.read_text().replace("# Changelog\n", f"# Changelog\n\n## {NEXT_VERSION} (unreleased)\n"))
    assert release_docs.check_offline(project) == VERSION
    validate_examples(project, validator)
    replace_once(metadata, f'version = "{NEXT_VERSION}.dev0"', f'version = "{NEXT_VERSION}"')
    replace_once(metadata, "4 - Beta", "5 - Production/Stable")
    replace_once(project / "README.md", f'rev: "{VERSION}"', f'rev: "v{NEXT_VERSION}"')
    with pytest.raises(ValueError, match=f"Local docs/changelog.md: Release {NEXT_VERSION} is unreleased"):
        release_docs.check_offline(project, f"v{NEXT_VERSION}")
    replace_once(history, f"## {NEXT_VERSION} (unreleased)", f"## {NEXT_VERSION} ({DATE})")
    assert release_docs.check_offline(project, f"v{NEXT_VERSION}") == f"v{NEXT_VERSION}"
    validate_examples(project, validator)

    replace_once(metadata, f'version = "{NEXT_VERSION}"', 'version = "9.2.0.dev0"')
    replace_once(metadata, "5 - Production/Stable", "4 - Beta")
    history.write_text(history.read_text().replace("# Changelog\n", "# Changelog\n\n## 9.2.0 (unreleased)\n"))
    assert release_docs.check_offline(project) == f"v{NEXT_VERSION}"
    validate_examples(project, validator)
    assert examples.read_bytes() == original_examples
    blocked.assert_not_called()


def hook_example(project: Path) -> str:
    readme = (project / "README.md").read_text(encoding="utf-8")
    return "<!-- pyroma-pre-commit -->" + readme.partition("<!-- pyroma-pre-commit -->")[2]


def in_container(example: str, container: str) -> str:
    if container == "quote":
        return "".join("> " + line + "\n" for line in example.split("\n")[:-1])
    if container == "list":
        return "10. Install:\n\n" + "".join("    " + line + "\n" for line in example.split("\n")[:-1])
    if container == "nested":
        return "10. Install:\n\n" + "".join("    > " + line + "\n" for line in example.split("\n")[:-1])
    return example


@pytest.mark.parametrize("container", ["top", "quote", "list", "nested"])
@pytest.mark.parametrize("stale", [False, True])
def test_every_rendered_hook_example_is_checked(project: Path, container: str, stale: bool) -> None:
    valid = hook_example(project)
    repeated = valid.replace(VERSION, "0.1.0") if stale else valid
    readme = "```pyroma .``` rates the cwd.\n\n" + in_container(repeated, container) + "\n" + valid
    if stale:
        with pytest.raises(ValueError, match="conflicting release revisions"):
            release_docs.precommit_revision(readme)
    else:
        assert release_docs.precommit_revision(readme) == VERSION


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (release_docs.REPOSITORY, "https://github.com/regebro/pyroma"),
        ("id: pyroma", "id: pyromaa"),
        ("repos:", "examples:"),
        (f'rev: "{VERSION}"', "rev: null"),
    ],
)
@pytest.mark.parametrize("bad_first", [False, True])
def test_valid_example_cannot_hide_an_invalid_marked_block(project: Path, old: str, new: str, bad_first: bool) -> None:
    valid = hook_example(project)
    invalid = valid.replace(old, new)
    readme = invalid + valid if bad_first else valid + invalid
    marker_line = 1 if bad_first else len(valid.split("\n"))
    with pytest.raises(ValueError, match=f"README pre-commit example at line {marker_line}.*pyroma hook"):
        release_docs.precommit_revision(readme)


@pytest.mark.parametrize(
    "readme",
    [
        "> <!-- pyroma-pre-commit -->\n\n```yaml\nrepos: []\n```\n",
        "<!-- pyroma-pre-commit -->\n> ```yaml\n> repos: []\n> ```\n",
        "10. <!-- pyroma-pre-commit -->\n\n11. ```yaml\n    repos: []\n    ```\n",
        "> <!-- pyroma-pre-commit -->\n",
    ],
)
def test_marker_requires_a_fence_in_the_same_container(project: Path, readme: str) -> None:
    with pytest.raises(ValueError, match=r"README line 1:.*complete yaml code block"):
        release_docs.precommit_revision(readme + "\n" + hook_example(project))


@pytest.mark.parametrize("container", ["top", "quote", "list", "nested"])
@pytest.mark.parametrize("before", [False, True])
def test_marker_on_a_fence_line_is_rejected(project: Path, container: str, before: bool) -> None:
    valid = hook_example(project)
    replacement = "<!-- pyroma-pre-commit --> ```yaml" if before else "```yaml <!-- pyroma-pre-commit -->"
    invalid = valid.replace("<!-- pyroma-pre-commit -->\n```yaml", replacement)
    with pytest.raises(ValueError, match=r"README line \d+:.*marker must be on its own line"):
        release_docs.precommit_revision(in_container(invalid, container) + "\n" + valid)


@pytest.mark.parametrize("container", ["top", "quote", "list", "nested"])
@pytest.mark.parametrize("closing", ["````", "```   ", "```\t"])
def test_explicit_fence_closure_in_containers(project: Path, container: str, closing: str) -> None:
    example = hook_example(project).removesuffix("```\n") + closing + "\n"
    assert release_docs.precommit_revision(in_container(example, container).rstrip("\n")) == VERSION


@pytest.mark.parametrize("container", ["top", "quote", "list", "nested"])
@pytest.mark.parametrize("closing", ["", "~~~,", "    ```", "``"])
def test_unclosed_marked_fences_identify_the_opening_line(project: Path, container: str, closing: str) -> None:
    example = hook_example(project).removesuffix("```\n") + closing + "\n"
    with pytest.raises(ValueError, match=r"unclosed code fence at README line \d+"):
        release_docs.precommit_revision(in_container(example, container))


def test_unclosed_unmarked_fence_identifies_its_opening(project: Path) -> None:
    with pytest.raises(ValueError, match="README line 2: unclosed code fence"):
        release_docs.precommit_revision("Introduction\n~~~markdown\n" + hook_example(project))


def test_first_line_marker_accepts_utf8_bom(project: Path) -> None:
    readme = hook_example(project) + f"\npip install pyroma621\n{release_docs.SITE_URL}\n"
    (project / "README.md").write_text(readme, encoding="utf-8-sig")
    assert release_docs.check_offline(project) == VERSION
    assert release_docs.precommit_revision("\ufeff" + hook_example(project)) == VERSION


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_readme_lines_follow_markdown_line_endings(newline: str) -> None:
    readme = newline.join(["Introduction\u2028still the first source line", "<!-- pyroma-pre-commit -->", "prose"])
    with pytest.raises(ValueError, match=r"README line 2:.*complete yaml code block"):
        release_docs.precommit_revision(readme)


@pytest.mark.parametrize("version", ["9.1.0rc1", "9.1.0.dev0"])
@pytest.mark.parametrize("heading", ["missing", "unreleased", DATE])
def test_tagged_prereleases_require_their_own_dated_heading(project: Path, version: str, heading: str) -> None:
    replace_once(project / "pyproject.toml", f'version = "{VERSION}"', f'version = "{version}"')
    replace_once(project / "pyproject.toml", "5 - Production/Stable", "4 - Beta")
    replace_once(project / "README.md", f'rev: "{VERSION}"', f'rev: "v{version}"')
    history = project / "docs" / "changelog.md"
    if heading != "missing":
        history.write_text(history.read_text() + f"\n## {version} ({heading})\n", encoding="utf-8")
    if heading == DATE:
        assert release_docs.check_offline(project, f"v{version}") == f"v{version}"
    else:
        message = "has no matching heading" if heading == "missing" else "is unreleased"
        with pytest.raises(ValueError, match=f"Local docs/changelog.md: Release {re.escape(version)} {message}"):
            release_docs.check_offline(project, f"v{version}")


@pytest.mark.parametrize(
    ("failure", "diagnostic"),
    [
        (HTTPError("https://user:password@example.com/?token=secret", 404, "secret", {}, None), "HTTP 404"),
        (URLError("https://user:password@example.com/?token=secret"), "unable to fetch resource"),
        (URLError(TimeoutError("secret")), "request timed out"),
        (TimeoutError("secret"), "request timed out"),
        (OSError("password secret"), "unable to read response"),
        (IncompleteRead(b"password secret"), "unable to read response"),
    ],
)
@pytest.mark.parametrize("phase", ["open", "read"])
def test_fetch_errors_identify_resources_without_urls(
    monkeypatch: pytest.MonkeyPatch, failure: Exception, diagnostic: str, phase: str
) -> None:
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=None)
    if phase == "read":
        response.read.side_effect = failure
    opener = Mock(side_effect=failure) if phase == "open" else Mock(return_value=response)
    monkeypatch.setattr(release_docs, "urlopen", opener)
    with pytest.raises(ValueError, match=f"Published hook manifest: {diagnostic}") as raised:
        release_docs.fetch("https://user:password@example.com/?token=secret", label="Published hook manifest")
    assert not any(secret in str(raised.value) for secret in ("https://", "password", "secret", "user:"))


def test_invalid_remote_utf8_is_labelled(monkeypatch: pytest.MonkeyPatch) -> None:
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=None)
    response.read.return_value = b"\xffpassword secret"
    monkeypatch.setattr(release_docs, "urlopen", Mock(return_value=response))
    with pytest.raises(ValueError, match="PyPI release metadata: response must contain valid UTF-8") as raised:
        release_docs.fetch("https://example.com/?token=secret", label="PyPI release metadata")
    assert "secret" not in str(raised.value)


@pytest.mark.parametrize("resource", ["schemas/v1/rating.json", "pyproject.toml", "pypi"])
def test_remote_parse_failures_are_labelled_and_controlled(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    online_evidence: dict[str, str],
    capsys: pytest.CaptureFixture,
    resource: str,
) -> None:
    if resource == "pypi":
        url, label, kind = "https://pypi.org/pypi/pyroma621/json", "PyPI release metadata", "JSON"
    elif resource == "pyproject.toml":
        url = f"https://raw.githubusercontent.com/hbmartin/pyroma621/{VERSION}/pyproject.toml"
        label, kind = "Hook package metadata", "TOML"
    else:
        url, label, kind = release_docs.SITE_URL + resource, "Published schema schemas/v1/rating.json", "JSON"
    online_evidence[url] = "[password secret"
    monkeypatch.setattr(release_docs, "fetch", lambda url, **_kwargs: online_evidence[url])
    monkeypatch.setattr("sys.argv", ["release_docs", "check", "--online", "--project", str(project)])
    with pytest.raises(SystemExit) as raised:
        release_docs.main()
    assert raised.value.code == 1
    out, err = capsys.readouterr()
    assert not out
    assert f"{label} must contain valid {kind}" in err
    assert not any(secret in err for secret in ("Traceback", "password", "secret", "https://"))


def test_transport_failure_has_a_controlled_cli_diagnostic(
    monkeypatch: pytest.MonkeyPatch, project: Path, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(release_docs, "urlopen", Mock(side_effect=URLError("https://example.com/?token=secret")))
    monkeypatch.setattr("sys.argv", ["release_docs", "check", "--online", "--project", str(project)])
    with pytest.raises(SystemExit) as raised:
        release_docs.main()
    assert raised.value.code == 1
    out, err = capsys.readouterr()
    assert not out
    assert "Documentation home: unable to fetch resource" in err
    assert not any(secret in err for secret in ("Traceback", "secret", "https://"))


@pytest.mark.parametrize("failure", ["", "missing", "mismatch"])
def test_online_evidence_covers_future_schema_versions(
    monkeypatch: pytest.MonkeyPatch, project: Path, online_evidence: dict[str, str], failure: str
) -> None:
    future = project / "pyroma" / "schemas" / "v2" / "rating.json"
    future.parent.mkdir()
    future.write_text('{"title": "Future schema"}', encoding="utf-8")
    url = release_docs.SITE_URL + "schemas/v2/rating.json"
    online_evidence[url] = future.read_text() if not failure else "{}"

    def evidence(url: str, **_kwargs: str) -> str:
        if failure == "missing" and url.endswith("schemas/v2/rating.json"):
            message = "Published schema schemas/v2/rating.json: HTTP 404"
            raise ValueError(message)
        return online_evidence[url]

    fetch = Mock(side_effect=evidence)
    monkeypatch.setattr(release_docs, "fetch", fetch)
    if failure:
        with pytest.raises(ValueError, match=re.escape("Published schema schemas/v2/rating.json")):
            release_docs.check_online(project, VERSION)
    else:
        release_docs.check_online(project, VERSION)
        assert url in {call.args[0] for call in fetch.call_args_list}


@pytest.mark.parametrize("failure", ["", "missing", "modified", "extra", "non-json", "guide", "empty-guide"])
@pytest.mark.skipif(os.name == "nt", reason="Documentation workflows use POSIX shells")
def test_docs_workflow_checks_all_built_schema_contents(project: Path, failure: str) -> None:
    future = project / "pyroma" / "schemas" / "v2" / "rating.json"
    future.parent.mkdir()
    future.write_text('{"title": "Future schema"}', encoding="utf-8")
    prepare_site(project)
    guide = project / "site" / "json" / "index.html"
    built = project / "site" / "schemas" / "v2" / "rating.json"
    if failure == "missing":
        built.unlink()
    elif failure == "modified":
        built.write_text("{}", encoding="utf-8")
    elif failure == "extra":
        (built.parent / "extra.json").write_text("{}", encoding="utf-8")
    elif failure == "non-json":
        (built.parent / "extra.txt").write_text("extra", encoding="utf-8")
    elif failure == "guide":
        guide.unlink()
    elif failure == "empty-guide":
        guide.write_text("", encoding="utf-8")
    environment = schema_build_environment(project)
    result = run_schema_build(project, environment)
    assert (result.returncode != 0) == bool(failure), result.stdout + result.stderr
    output = Path(environment["GITHUB_OUTPUT"])
    if failure:
        assert not output.exists(), "Failed verification must not emit deploy inventory"
    else:
        assert json.loads(output.read_text().removeprefix("schema_paths=")) == [
            "schemas/v1/error.json",
            "schemas/v1/rating.json",
            "schemas/v2/rating.json",
        ]


@pytest.mark.parametrize("missing", ["", "json/", "schemas/v2/rating.json"])
@pytest.mark.skipif(os.name == "nt", reason="Documentation workflows use POSIX shells")
def test_deployment_probes_every_manifest_schema_without_a_checkout(project: Path, missing: str) -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/docs.yml").read_text())
    step = workflow_step(workflow["jobs"]["deploy"]["steps"], "schema_availability")
    shutil.rmtree(project / "pyroma")
    curl = project / "curl"
    curl.write_text(
        "#!/bin/sh\nfor request_url do :; done\n"
        'printf "%s\\n" "$request_url" >> "$DOCS_PROBE_LOG"\n'
        '[ "$request_url" != "$DOCS_PROBE_MISSING" ]\n',
        encoding="utf-8",
    )
    curl.chmod(0o755)
    log = project / "requests"
    environment = {
        **os.environ,
        "PATH": str(project) + os.pathsep + os.environ["PATH"],
        "DOCS_URL": release_docs.SITE_URL,
        "DOCS_PROBE_LOG": str(log),
        "DOCS_PROBE_MISSING": release_docs.SITE_URL + missing if missing else "",
        "SCHEMA_PATHS_JSON": json.dumps(["schemas/v1/error.json", "schemas/v1/rating.json", "schemas/v2/rating.json"]),
    }
    result = subprocess.run(  # noqa: S603 - execute the repository's deployment step with a curl probe
        workflow_bash_command(step),
        cwd=project,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert (result.returncode != 0) == bool(missing), result.stdout + result.stderr
    if not missing:
        assert set(log.read_text().splitlines()) == {
            release_docs.SITE_URL + path
            for path in ("json/", "schemas/v1/error.json", "schemas/v1/rating.json", "schemas/v2/rating.json")
        }


@pytest.mark.parametrize("failed_gate", ["", "lock", "preflight", "check", "build", "3.11", "3.13", "3.14"])
@pytest.mark.skipif(os.name == "nt", reason="Documented local release commands use a POSIX shell")
def test_documented_release_sequence_stops_at_first_failure(tmp_path: Path, failed_gate: str) -> None:
    uv = tmp_path / "uv"
    uv.write_text(
        '#!/bin/sh\ncase "$1" in\n'
        '  lock|build) gate="$1" ;;\n'
        '  *) gate="$7"; if [ "$gate" = validate ]; then gate="${11}"; fi ;;\n'
        'esac\nprintf "%s\\n" "$gate" >> "$RELEASE_PROBE_LOG"\n'
        '[ "$gate" != "$RELEASE_PROBE_FAILURE" ]\n',
        encoding="utf-8",
    )
    uv.chmod(0o755)
    log = tmp_path / "calls"
    text = (ROOT / "docs" / "development.md").read_text()
    command = release_check_commands(text)
    environment = {
        **os.environ,
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
        "TMPDIR": str(tmp_path),
        "RELEASE_PROBE_LOG": str(log),
        "RELEASE_PROBE_FAILURE": failed_gate,
    }
    result = subprocess.run(  # noqa: S603 - execute documented commands with a local uv probe
        ["/bin/sh", "-c", command],
        env=environment,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    expected = ["lock", "preflight", "check", "build", "3.11", "3.13", "3.14"]
    if failed_gate:
        expected = expected[: expected.index(failed_gate) + 1]
    assert log.read_text().splitlines() == expected
    assert (result.returncode != 0) == bool(failed_gate), result.stdout + result.stderr


@pytest.mark.parametrize("container", ["top", "quote", "list", "nested"])
@pytest.mark.parametrize("bad_first", [False, True])
@pytest.mark.parametrize("html", ["<details>", "<br>", '<p align="center">', '<img src="example">', "trailing"])
def test_html_cannot_hide_a_marked_example(project: Path, container: str, bad_first: bool, html: str) -> None:
    valid = hook_example(project)
    hidden = valid.replace(VERSION, "0.1.0")
    if html == "trailing":
        hidden = hidden.replace("<!-- pyroma-pre-commit -->", "<!-- pyroma-pre-commit --><br>")
    else:
        hidden = html + "\n" + hidden
    hidden = in_container(hidden, container)
    readme = hidden + "\n" + valid if bad_first else valid + "\n" + hidden
    marker_line = next(
        index + 1
        for index, line in enumerate(readme.split("\n"))
        if "<!-- pyroma-pre-commit -->" in line and (bad_first or index > valid.count("\n"))
    )
    with pytest.raises(ValueError, match=f"README line {marker_line}:.*marker must be on its own line"):
        release_docs.precommit_revision(readme)


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_html_marker_diagnostic_uses_its_source_line(project: Path, newline: str) -> None:
    valid = hook_example(project)
    readme = "\ufeff<details>\nIntroduction\u2028on the same line\n" + valid.replace(VERSION, "0.1.0")
    readme += "\n</details>\n\n" + valid
    with pytest.raises(ValueError, match=r"README line 3:.*marker must be on its own line"):
        release_docs.precommit_revision(readme.replace("\n", newline))


@pytest.mark.parametrize("container", ["top", "nested"])
def test_html_wrapper_can_separate_an_active_marker(project: Path, container: str) -> None:
    wrapped = "<details>\n\n" + hook_example(project) + "\n</details>\n"
    assert release_docs.precommit_revision(in_container(wrapped, container)) == VERSION


def test_other_entries_in_a_valid_marked_block_remain_allowed(project: Path) -> None:
    valid = hook_example(project)
    stale = valid.replace(VERSION, "0.1.0").replace("id: pyroma", "id: pyromaa")
    combined = valid.removesuffix("```\n") + stale.partition("repos:\n")[2]
    assert release_docs.precommit_revision(combined) == VERSION


@pytest.mark.parametrize("delimiter", ["```", "~~~", "````"])
@pytest.mark.parametrize("container", ["top", "quote", "list", "nested"])
@pytest.mark.parametrize("closed", [False, True])
def test_empty_fence_closure_at_eof(delimiter: str, container: str, closed: bool) -> None:
    source = delimiter + "\n" + (delimiter + "\n" if closed else "")
    source = in_container(source, container).rstrip("\n")
    tokens = release_docs._markdown_parser().parse(source)
    fences = [token for token in tokens if token.type == "fence"]
    assert len(fences) == 1
    assert fences[0].content == ""
    assert fences[0].meta["closed"] is closed


@pytest.mark.parametrize("source", ["```\ncontent", "```\ncontent\n", "```\n\n", "```\n\n```"])
def test_fence_content_line_count_handles_eof(source: str) -> None:
    token = release_docs._markdown_parser().parse(source)[0]
    assert token.meta["closed"] is source.endswith("\n```")


def test_fence_wrapper_preserves_registered_rule_and_alternatives(monkeypatch: pytest.MonkeyPatch) -> None:
    parser = MarkdownIt("commonmark")
    rule = next(rule for rule in parser.block.ruler.__rules__ if rule.name == "fence")
    original = Mock(wraps=rule.fn)
    alternatives = [*rule.alt, "future-container"]
    parser.block.ruler.at("fence", original, {"alt": alternatives})
    monkeypatch.setattr(release_docs, "MarkdownIt", Mock(return_value=parser))
    wrapped = release_docs._markdown_parser()
    assert wrapped is parser
    for alternative in alternatives:
        assert rule.fn in wrapped.block.ruler.getRules(alternative)
    assert wrapped.parse("```\ncontent\n```")[0].meta["closed"] is True
    original.assert_called()


@pytest.mark.parametrize("code", [404, 500])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_http_error_response_is_closed_without_losing_diagnostic(
    monkeypatch: pytest.MonkeyPatch, code: int, cleanup_fails: bool
) -> None:
    body = BytesIO(b"password secret")
    error = HTTPError("https://user:password@example.com/?token=secret", code, "secret", {}, body)
    close = error.close

    def cleanup() -> None:
        close()
        if cleanup_fails:
            message = "https://user:password@example.com/?token=secret"
            raise OSError(message)

    closer = Mock(side_effect=cleanup)
    monkeypatch.setattr(error, "close", closer)
    monkeypatch.setattr(release_docs, "urlopen", Mock(side_effect=error))
    with pytest.raises(ValueError, match=f"^Published hook manifest: HTTP {code}$"):
        release_docs.fetch("https://example.com/", label="Published hook manifest")
    closer.assert_called_once_with()
    assert body.closed


@pytest.mark.parametrize(
    "case",
    [
        ("", "project"),
        ('project = "password secret"', "project"),
        ("[project]", "project.name"),
        ('[project]\nname = []\nversion = "9.0.0"', "project.name"),
        ('[project]\nname = ""\nversion = "9.0.0"', "project.name"),
        ('[project]\nname = "pyroma621"', "project.version"),
        ('[project]\nname = "pyroma621"\nversion = []', "project.version"),
        ('[project]\nname = "pyroma621"\nversion = 9', "project.version"),
        ('[project]\nname = "pyroma621"\nversion = ""', "project.version"),
        (
            '[project]\nname = "pyroma621"\nversion = "https://user:password@example.com/?token=secret"',
            "project.version",
        ),
    ],
)
def test_remote_toml_structure_failures_are_labelled_and_controlled(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    online_evidence: dict[str, str],
    capsys: pytest.CaptureFixture,
    case: tuple[str, str],
) -> None:
    payload, field = case
    url = f"https://raw.githubusercontent.com/hbmartin/pyroma621/{VERSION}/pyproject.toml"
    online_evidence[url] = payload
    monkeypatch.setattr(release_docs, "fetch", lambda url, **_kwargs: online_evidence[url])
    monkeypatch.setattr("sys.argv", ["release_docs", "check", "--online", "--project", str(project)])
    with pytest.raises(SystemExit) as raised:
        release_docs.main()
    assert raised.value.code == 1
    out, err = capsys.readouterr()
    assert not out
    assert f"Hook package metadata: {field} must be" in err
    assert not any(secret in err for secret in ("Traceback", "password", "secret", "https://", "user:"))


@pytest.mark.parametrize(
    "case",
    [
        (None, "response"),
        ([], "response"),
        ({}, "releases"),
        ({"releases": []}, "releases"),
        ({"releases": None}, "releases"),
        ({"releases": {VERSION: {}}}, "release artifacts"),
        ({"releases": {VERSION: None}}, "release artifacts"),
        ({"releases": {VERSION: [None]}}, "release artifacts"),
        ({"releases": {VERSION: ["password secret"]}}, "release artifacts"),
        ({"releases": {VERSION: [{}]}}, "upload_time_iso_8601"),
        ({"releases": {VERSION: [{"upload_time_iso_8601": None}]}}, "upload_time_iso_8601"),
        ({"releases": {VERSION: [{"upload_time_iso_8601": []}]}}, "upload_time_iso_8601"),
        ({"releases": {VERSION: [{"upload_time_iso_8601": "2026-10-12"}]}}, "upload_time_iso_8601"),
        ({"releases": {VERSION: [{"upload_time_iso_8601": "2026-99-99T00:00:00Z"}]}}, "upload_time_iso_8601"),
        (
            {"releases": {VERSION: [{"upload_time_iso_8601": "https://user:password@example.com/?token=secret"}]}},
            "upload_time_iso_8601",
        ),
        ({"releases": {"password secret": [{"upload_time_iso_8601": f"{DATE}T00:00:00Z"}]}}, "release keys"),
    ],
)
def test_remote_json_structure_failures_are_labelled_and_controlled(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    online_evidence: dict[str, str],
    capsys: pytest.CaptureFixture,
    case: tuple[object, str],
) -> None:
    payload, field = case
    online_evidence["https://pypi.org/pypi/pyroma621/json"] = json.dumps(payload)
    monkeypatch.setattr(release_docs, "fetch", lambda url, **_kwargs: online_evidence[url])
    monkeypatch.setattr("sys.argv", ["release_docs", "check", "--online", "--project", str(project)])
    with pytest.raises(SystemExit) as raised:
        release_docs.main()
    assert raised.value.code == 1
    out, err = capsys.readouterr()
    assert not out
    assert f"PyPI release metadata: {field} must be" in err
    assert not any(secret in err for secret in ("Traceback", "password", "secret", "https://", "user:"))


def test_pypi_metadata_uses_first_upload_date_and_allows_empty_releases(
    monkeypatch: pytest.MonkeyPatch, project: Path, online_evidence: dict[str, str]
) -> None:
    online_evidence["https://pypi.org/pypi/pyroma621/json"] = json.dumps(
        {
            "releases": {
                VERSION: [
                    {"upload_time_iso_8601": "2026-10-13T01:02:03+00:00"},
                    {"upload_time_iso_8601": f"{DATE}T23:59:59Z"},
                ],
                "empty": [],
            }
        }
    )
    monkeypatch.setattr(release_docs, "fetch", lambda url, **_kwargs: online_evidence[url])
    release_docs.check_online(project, VERSION)


def test_malformed_packaged_schema_has_a_controlled_path_diagnostic(
    monkeypatch: pytest.MonkeyPatch, project: Path, online_evidence: dict[str, str], capsys: pytest.CaptureFixture
) -> None:
    (project / "pyroma" / "schemas" / "v1" / "rating.json").write_text("[password secret", encoding="utf-8")
    monkeypatch.setattr(release_docs, "fetch", lambda url, **_kwargs: online_evidence[url])
    monkeypatch.setattr("sys.argv", ["release_docs", "check", "--online", "--project", str(project)])
    with pytest.raises(SystemExit) as raised:
        release_docs.main()
    assert raised.value.code == 1
    out, err = capsys.readouterr()
    assert not out
    assert "Packaged schema schemas/v1/rating.json must contain valid JSON" in err
    assert not any(secret in err for secret in ("Traceback", "password", "secret", "https://"))


def test_deployment_uses_verified_build_inventory_after_deploy() -> None:
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "docs.yml").read_text())
    deploy = workflow["jobs"]["deploy"]
    assert deploy["permissions"] == {"pages": "write", "id-token": "write"}
    steps = deploy["steps"]
    ids = [step.get("id") for step in steps]
    assert ids.index("deployment") < ids.index("schema_availability")
    assert not any(step.get("uses", "").startswith("actions/checkout@") for step in steps)
    availability = workflow_step(steps, "schema_availability")
    assert availability["env"]["SCHEMA_PATHS_JSON"] == "${{ needs.build.outputs.schema_paths }}"
    build = workflow["jobs"]["build"]
    assert build["outputs"]["schema_paths"] == "${{ steps.schema_build_outputs.outputs.schema_paths }}"
    steps = build["steps"]
    check = steps.index(workflow_step(steps, "schema_build_outputs"))
    upload = next(index for index, step in enumerate(steps) if "actions/upload-pages-artifact@" in step.get("uses", ""))
    assert check < upload


def test_workflow_selector_uses_ids_instead_of_display_names() -> None:
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "docs.yml").read_text())
    for job, step_id in [("build", "schema_build_outputs"), ("deploy", "schema_availability")]:
        steps = workflow["jobs"][job]["steps"]
        step = workflow_step(steps, step_id)
        step["name"] = "Renamed display name"
        assert workflow_step(steps, step_id)["run"] == step["run"]


@pytest.mark.parametrize("count", [0, 2])
def test_workflow_selector_requires_one_id(count: int) -> None:
    steps = [{"id": "schema_availability", "run": "true"}] * count
    with pytest.raises(AssertionError, match=f"Expected exactly one workflow step.*found {count}"):
        workflow_step(steps, "schema_availability")


def test_workflow_executor_rejects_an_unspecified_shell() -> None:
    with pytest.raises(AssertionError, match="must explicitly use shell: bash"):
        workflow_bash_command({"id": "schema_availability", "run": "true"})


def test_release_command_selector_does_not_depend_on_example_version() -> None:
    text = (ROOT / "docs" / "development.md").read_text()
    changed = re.sub(r"RELEASE_TAG=[^\n]+", "RELEASE_TAG=99.1.0rc1", text)
    assert "RELEASE_TAG=99.1.0rc1" in release_check_commands(changed)


@pytest.mark.parametrize("count", [0, 2])
def test_release_command_selector_requires_one_marker(count: int) -> None:
    block = RELEASE_CHECKS_MARKER + "\n\n```console\ntrue\n```\n\n"
    with pytest.raises(AssertionError, match=f"Expected exactly one.*block; found {count}"):
        release_check_commands(block * count)


@pytest.mark.parametrize("following", ["", "Prose", "```python\npass\n```"])
def test_release_command_selector_requires_console_commands(following: str) -> None:
    with pytest.raises(AssertionError, match="must precede a console fence"):
        release_check_commands(RELEASE_CHECKS_MARKER + "\n\n" + following)


@pytest.mark.parametrize("prefix", ["Paragraph ", "# Heading ", "| Table | ", "- List item "])
@pytest.mark.parametrize("bad_first", [False, True])
def test_inline_prose_markers_keep_examples_unmarked(project: Path, prefix: str, bad_first: bool) -> None:
    valid = hook_example(project)
    stale = valid.replace(VERSION, "0.1.0").replace("<!-- pyroma-pre-commit -->", prefix + "<!-- pyroma-pre-commit -->")
    readme = stale + "\n" + valid if bad_first else valid + "\n" + stale
    assert release_docs.precommit_revision(readme) == VERSION


@pytest.mark.parametrize("html", ["<pre>\n{marker}\n</pre>", "<code>\n{marker}\n</code>", "<!-- Note: {marker}\n-->"])
def test_raw_html_literal_mentions_still_require_marker_placement(project: Path, html: str) -> None:
    readme = html.format(marker="<!-- pyroma-pre-commit -->") + "\n\n" + hook_example(project)
    with pytest.raises(ValueError, match=r"README line \d+:.*marker must be on its own line"):
        release_docs.precommit_revision(readme)


@pytest.mark.parametrize(
    ("text", "field"),
    [
        ("", "project"),
        ('project = "password secret"', "project"),
        ("project = []", "project"),
        ('[project]\nversion = "9.0.0"', "project.name"),
        ("[project]\nname = 42", "project.name"),
        ('[project]\nname = " "', "project.name"),
        ('[project]\nname = "pyroma621"', "project.version"),
        ('[project]\nname = "pyroma621"\nversion = []', "project.version"),
        ('[project]\nname = "pyroma621"\nversion = "password secret"', "project.version"),
        ('[project]\nname = "pyroma621"\nversion = "9.0.0"', "project.urls"),
        ('[project]\nname = "pyroma621"\nversion = "9.0.0"\nurls = []', "project.urls"),
        ('[project]\nname = "pyroma621"\nversion = "9.0.0"\n[project.urls]', "project.urls.Documentation"),
        (
            '[project]\nname = "pyroma621"\nversion = "9.0.0"\n[project.urls]\nDocumentation = 42',
            "project.urls.Documentation",
        ),
        (
            '[project]\nname = "pyroma621"\nversion = "9.0.0"\n[project.urls]\nDocumentation = " "',
            "project.urls.Documentation",
        ),
    ],
)
def test_local_project_structure_has_controlled_field_diagnostics(
    monkeypatch: pytest.MonkeyPatch, project: Path, capsys: pytest.CaptureFixture, text: str, field: str
) -> None:
    (project / "pyproject.toml").write_text(text, encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["release_docs", "check", "--project", str(project)])
    with pytest.raises(SystemExit) as raised:
        release_docs.main()
    assert raised.value.code == 1
    out, err = capsys.readouterr()
    assert not out
    assert f"Local pyproject.toml: {field} must be" in err
    assert not any(secret in err for secret in ("Traceback", "password", "secret", "https://"))


@pytest.mark.parametrize("classifiers", ['"password secret"', "{}", "[42]", '["Stable", 42]'])
def test_local_classifiers_require_strings(
    monkeypatch: pytest.MonkeyPatch, project: Path, capsys: pytest.CaptureFixture, classifiers: str
) -> None:
    replace_once(project / "pyproject.toml", '["Development Status :: 5 - Production/Stable"]', classifiers)
    monkeypatch.setattr("sys.argv", ["release_docs", "check", "--project", str(project)])
    with pytest.raises(SystemExit) as raised:
        release_docs.main()
    assert raised.value.code == 1
    err = capsys.readouterr().err
    assert "Local pyproject.toml: project.classifiers must be a list of strings" in err
    assert not any(secret in err for secret in ("Traceback", "password", "secret"))


def test_optional_local_classifiers_can_be_absent(project: Path) -> None:
    replace_once(project / "pyproject.toml", 'classifiers = ["Development Status :: 5 - Production/Stable"]\n', "")
    assert release_docs.check_offline(project) == VERSION
    with pytest.raises(ValueError, match="Final pyroma621 releases require"):
        release_docs.check_offline(project, VERSION)


@pytest.mark.parametrize(
    ("text", "field"),
    [
        ("", "project"),
        ('project = "password secret"', "project"),
        ("project = []", "project"),
        ("[project]", "project.site_url"),
        ("[project]\nsite_url = []", "project.site_url"),
        ('[project]\nsite_url = " "', "project.site_url"),
    ],
)
def test_local_site_structure_has_controlled_field_diagnostics(
    monkeypatch: pytest.MonkeyPatch, project: Path, capsys: pytest.CaptureFixture, text: str, field: str
) -> None:
    (project / "zensical.toml").write_text(text, encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["release_docs", "check", "--project", str(project)])
    with pytest.raises(SystemExit) as raised:
        release_docs.main()
    assert raised.value.code == 1
    err = capsys.readouterr().err
    assert f"Local zensical.toml: {field} must be" in err
    assert not any(secret in err for secret in ("Traceback", "password", "secret", "https://"))


@pytest.mark.parametrize("filename", ["pyproject.toml", "zensical.toml"])
@pytest.mark.parametrize("failure", ["syntax", "utf8", "missing"])
def test_local_toml_read_and_parse_failures_are_labelled(
    monkeypatch: pytest.MonkeyPatch, project: Path, capsys: pytest.CaptureFixture, filename: str, failure: str
) -> None:
    path = project / filename
    if failure == "missing":
        path.unlink()
        message = "unable to read file"
    else:
        path.write_bytes(b"\xffpassword secret" if failure == "utf8" else b"[password secret")
        message = "valid UTF-8" if failure == "utf8" else "valid TOML"
    monkeypatch.setattr("sys.argv", ["release_docs", "check", "--project", str(project)])
    with pytest.raises(SystemExit) as raised:
        release_docs.main()
    assert raised.value.code == 1
    err = capsys.readouterr().err
    assert f"Local {filename}" in err
    assert message in err
    assert not any(secret in err for secret in ("Traceback", "password", "secret", "https://"))


def test_invalid_packaged_schema_utf8_names_the_path(
    monkeypatch: pytest.MonkeyPatch, project: Path, online_evidence: dict[str, str], capsys: pytest.CaptureFixture
) -> None:
    (project / "pyroma/schemas/v1/rating.json").write_bytes(b"\xffpassword secret")
    monkeypatch.setattr(release_docs, "fetch", lambda url, **_kwargs: online_evidence[url])
    monkeypatch.setattr("sys.argv", ["release_docs", "check", "--online", "--project", str(project)])
    with pytest.raises(SystemExit) as raised:
        release_docs.main()
    assert raised.value.code == 1
    err = capsys.readouterr().err
    assert "Packaged schema schemas/v1/rating.json must contain valid UTF-8" in err
    assert not any(secret in err for secret in ("Traceback", "password", "secret", "https://"))


@pytest.mark.parametrize(
    ("timestamp", "date"),
    [
        ("2026-10-13T01:00:00+05:00", "2026-10-12"),
        ("2026-10-12T23:00:00-05:00", "2026-10-13"),
        ("2026-10-12t17:42:33.123456Z", DATE),
        ("2026-10-12 17:42:33+00:00", DATE),
        ("20261012T174233Z", DATE),
        ("2026-10-12T17:42:33.123456+00:00", DATE),
    ],
)
def test_upload_dates_use_utc_and_supported_iso_forms(timestamp: str, date: str) -> None:
    assert release_docs._upload_date({"upload_time_iso_8601": timestamp}) == date


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-10-12",
        "2026-10-12T17:42:33",
        "2026-10-12t17:42:33",
        "password secret",
        "0001-01-01T00:00:00+01:00",
        "9999-12-31T23:59:59-01:00",
    ],
)
def test_bad_upload_timestamps_fail_safely_through_cli(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    online_evidence: dict[str, str],
    capsys: pytest.CaptureFixture,
    timestamp: str,
) -> None:
    online_evidence["https://pypi.org/pypi/pyroma621/json"] = json.dumps(
        {"releases": {VERSION: [{"upload_time_iso_8601": timestamp}]}}
    )
    monkeypatch.setattr(release_docs, "fetch", lambda url, **_kwargs: online_evidence[url])
    monkeypatch.setattr("sys.argv", ["release_docs", "check", "--online", "--project", str(project)])
    with pytest.raises(SystemExit) as raised:
        release_docs.main()
    assert raised.value.code == 1
    err = capsys.readouterr().err
    assert "PyPI release metadata: upload_time_iso_8601 must be" in err
    assert not any(secret in err for secret in ("Traceback", "password", "secret", "https://"))


@pytest.mark.parametrize("empty", [False, True])
def test_pypi_local_version_labels_cannot_leak(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    online_evidence: dict[str, str],
    capsys: pytest.CaptureFixture,
    empty: bool,
) -> None:
    artifacts = [] if empty else [{"upload_time_iso_8601": f"{DATE}T00:00:00Z"}]
    online_evidence["https://pypi.org/pypi/pyroma621/json"] = json.dumps(
        {"releases": {"9.0.0+password.secret": artifacts}}
    )
    monkeypatch.setattr(release_docs, "fetch", lambda url, **_kwargs: online_evidence[url])
    monkeypatch.setattr("sys.argv", ["release_docs", "check", "--online", "--project", str(project)])
    if empty:
        release_docs.main()
    else:
        with pytest.raises(SystemExit) as raised:
            release_docs.main()
        assert raised.value.code == 1
    out, err = capsys.readouterr()
    assert not out
    if not empty:
        assert "PyPI release metadata: release keys must be public version strings without local labels" in err
    assert not any(secret in err for secret in ("Traceback", "password", "secret", "https://"))


@pytest.mark.parametrize(
    "case",
    [
        ("", "has no matching heading"),
        ("unreleased", "is unreleased; needs a dated heading in YYYY-MM-DD format"),
        ("password secret", ": Changelog dates must use YYYY-MM-DD"),
        ("20261012", ": Changelog dates must use YYYY-MM-DD"),
        ("2026-W42-1", ": Changelog dates must use YYYY-MM-DD"),
        ("2026-02-29", ": Changelog dates must be valid calendar dates in YYYY-MM-DD format"),
        ("2026-10-13", "has a mismatched date"),
    ],
)
def test_online_changelog_errors_do_not_echo_remote_release_keys(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    online_evidence: dict[str, str],
    capsys: pytest.CaptureFixture,
    case: tuple[str, str],
) -> None:
    heading, diagnostic = case
    replace_once(project / "docs/changelog.md", f"## {VERSION} ({DATE})", f"## {VERSION} ({heading})")
    monkeypatch.setattr(release_docs, "fetch", lambda url, **_kwargs: online_evidence[url])
    monkeypatch.setattr("sys.argv", ["release_docs", "check", "--online", "--project", str(project)])
    with pytest.raises(SystemExit) as raised:
        release_docs.main()
    assert raised.value.code == 1
    err = capsys.readouterr().err
    separator = "" if diagnostic.startswith(":") else " "
    assert (
        f"PyPI release metadata: Changelog for a published release (expected UTC date {DATE}){separator}{diagnostic}\n"
        in err
    )
    assert VERSION not in err
    assert not any(secret in err for secret in ("Traceback", "https://", "password", "secret"))


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit])
def test_http_cleanup_preserves_interrupts(monkeypatch: pytest.MonkeyPatch, interrupt: type[BaseException]) -> None:
    body = BytesIO(b"response")
    error = HTTPError("https://example.com/", 500, "failure", {}, body)
    close = error.close

    def cleanup() -> None:
        close()
        raise interrupt

    monkeypatch.setattr(error, "close", cleanup)
    monkeypatch.setattr(release_docs, "urlopen", Mock(side_effect=error))
    with pytest.raises(interrupt):
        release_docs.fetch("https://example.com/", label="Published hook manifest")
    assert body.closed


@pytest.mark.parametrize("following", ["> ```console\n> true\n> ```\n", "```console\ntrue\n", "```console\n"])
def test_release_command_selector_rejects_wrong_containers_and_unclosed_fences(following: str) -> None:
    with pytest.raises(AssertionError, match=r"must precede a console fence|explicit closing fence"):
        release_check_commands(RELEASE_CHECKS_MARKER + "\n\n" + following)


def test_release_command_selector_rejects_a_fence_outside_the_marker_container() -> None:
    with pytest.raises(AssertionError, match="must share a Markdown container"):
        release_check_commands("> " + RELEASE_CHECKS_MARKER + "\n\n```console\ntrue\n```\n")


@pytest.mark.parametrize("delimiter", ["```", "~~~", "````"])
def test_release_command_selector_accepts_closed_nested_fences(delimiter: str) -> None:
    text = "> " + RELEASE_CHECKS_MARKER + f"\n>\n> {delimiter}console\n> true\n> {delimiter}"
    assert release_check_commands(text) == "true\n"


@pytest.mark.parametrize("links", ["file", "broken-file", "directory", "broken-directory", "root", "broken-root"])
@pytest.mark.skipif(os.name == "nt", reason="Schema symlinks require POSIX permissions")
def test_preparation_and_build_verification_use_only_permitted_schemas(project: Path, links: str) -> None:
    source = project / "pyroma/schemas"
    future = source / "v2/rating.json"
    future.parent.mkdir()
    future.write_bytes(b'{\r\n "title": "Future schema"\r\n}\r\n')
    release_docs.prepare_schemas(project)
    previous = (project / "docs/schemas/v2/rating.json").read_bytes()
    if links in {"root", "broken-root"}:
        destination = project / "external-schemas"
        source.rename(destination)
        source.symlink_to(destination if links == "root" else project / "absent", target_is_directory=True)
        with pytest.raises(
            ValueError, match=r"Canonical packaged schema directory \(pyroma/schemas\) must not be a symlink"
        ):
            release_docs.prepare_schemas(project)
        assert (project / "docs/schemas/v2/rating.json").read_bytes() == previous
        return
    external = project / "external"
    if "directory" in links:
        external.mkdir()
        (external / "outside.json").write_bytes(b"\xff")
        (source / "v99").symlink_to(external if links == "directory" else project / "absent", target_is_directory=True)
    else:
        external.write_bytes(b"\xff")
        (source / "linked.json").symlink_to(external if links == "file" else project / "absent")
    (source / "notes.txt").write_text("not a schema")
    prepare_site(project)
    expected = ["schemas/v1/error.json", "schemas/v1/rating.json", "schemas/v2/rating.json"]
    assert [path.as_posix() for path in release_docs.schema_paths(project)] == expected
    assert (
        sorted(path.relative_to(project / "docs").as_posix() for path in (project / "docs/schemas").rglob("*.*"))
        == expected
    )
    assert (project / "docs/schemas/v2/rating.json").read_bytes() == previous
    environment = schema_build_environment(project)
    result = run_schema_build(project, environment)
    assert result.returncode == 0, result.stderr
    assert json.loads(Path(environment["GITHUB_OUTPUT"]).read_text().removeprefix("schema_paths=")) == expected


def fail_schema_discovery(monkeypatch: pytest.MonkeyPatch, source: Path, failure: str) -> None:
    if failure == "root-stat":
        original_stat = Path.lstat

        def denied_stat(path: Path) -> os.stat_result:
            if path == source:
                raise PermissionError(errno.EACCES, "password secret")
            return original_stat(path)

        monkeypatch.setattr(Path, "lstat", denied_stat)
    else:
        original_scan = os.scandir

        def denied_scan(path: Path) -> object:
            if Path(path) == source / "v1":
                if failure == "scanning":
                    raise PermissionError(errno.EACCES, "password secret")
                entry = Mock()
                entry.path = str(source / "v1/rating.json")
                entry.stat.side_effect = OSError(errno.EIO, "password secret")
                return scandir_entries(entry)
            return original_scan(path)

        monkeypatch.setattr(os, "scandir", denied_scan)


@pytest.mark.parametrize("failure", ["missing", "empty", "utf8", "json", "scanning", "root-stat", "entry-stat"])
def test_schema_failures_preserve_output_and_precede_all_fetches(
    monkeypatch: pytest.MonkeyPatch, project: Path, failure: str
) -> None:
    release_docs.prepare_schemas(project)
    previous = (project / "docs/schemas/v1/rating.json").read_bytes()
    source = project / "pyroma/schemas"
    if failure in {"missing", "empty"}:
        shutil.rmtree(source)
        if failure == "empty":
            source.mkdir()
    elif failure in {"utf8", "json"}:
        (source / "v1/rating.json").write_bytes(b"\xffpassword secret" if failure == "utf8" else b"[password secret")
    else:
        fail_schema_discovery(monkeypatch, source, failure)
    fetch = Mock(side_effect=AssertionError("Local schema failures must precede network calls"))
    monkeypatch.setattr(release_docs, "fetch", fetch)
    for operation in [
        lambda: release_docs.prepare_schemas(project),
        lambda: release_docs.check_online(project, VERSION),
    ]:
        with pytest.raises(ValueError, match=r"[Ss]chema") as raised:
            operation()
        assert "schema" in str(raised.value).lower()
        if failure == "scanning":
            assert "inventory at schemas/v1" in str(raised.value)
        elif failure == "entry-stat":
            assert "stat entry schemas/v1/rating.json" in str(raised.value)
        assert not any(secret in str(raised.value) for secret in ("password", "secret"))
    assert (project / "docs/schemas/v1/rating.json").read_bytes() == previous
    fetch.assert_not_called()


@pytest.mark.skipif(os.name == "nt", reason="Directory permissions require POSIX")
def test_unreadable_nested_schema_directory_is_not_silently_skipped(project: Path) -> None:
    hidden = project / "pyroma/schemas/v2"
    hidden.mkdir()
    (hidden / "rating.json").write_text("{}")
    hidden.chmod(0)
    try:
        try:
            with os.scandir(hidden):
                pass
        except PermissionError:
            pass
        else:
            pytest.skip("This account bypasses directory permission checks")
        with pytest.raises(
            ValueError,
            match=r"Canonical packaged schema directory \(pyroma/schemas\): "
            r"unable to read inventory.*permission denied",
        ):
            release_docs.schema_paths(project)
    finally:
        hidden.chmod(0o755)


@pytest.mark.parametrize("location", ["root", "file", "directory", "broken"])
@pytest.mark.skipif(os.name == "nt", reason="Built symlinks require POSIX")
def test_built_schema_symlinks_fail_verification(project: Path, location: str) -> None:
    prepare_site(project)
    schemas = project / "site/schemas"
    if location == "root":
        target = project / "external"
        schemas.rename(target)
        schemas.symlink_to(target, target_is_directory=True)
    elif location == "directory":
        (schemas / "v99").symlink_to(project / "pyroma/schemas/v1", target_is_directory=True)
    elif location == "broken":
        (schemas / "broken.json").symlink_to(project / "absent")
    else:
        (schemas / "v1/rating.json").unlink()
        (schemas / "v1/rating.json").symlink_to(project / "pyroma/schemas/v1/rating.json")
    with pytest.raises(ValueError, match=r"Built schema directory.*symlink") as raised:
        release_docs.check_schema_outputs(project, project / "site")
    if location != "root":
        entry = {"directory": "v99", "broken": "broken.json", "file": "v1/rating.json"}[location]
        assert entry in str(raised.value)
    assert str(project) not in str(raised.value)


@pytest.mark.parametrize(
    "filename",
    [
        "README.md",
        "docs/changelog.md",
        ".pre-commit-hooks.yaml",
        "pyproject.toml",
        "zensical.toml",
        "pyroma/schemas/v1/rating.json",
    ],
)
@pytest.mark.parametrize(
    "case",
    [
        (errno.ENOENT, "file missing"),
        (errno.EACCES, "permission denied"),
        (errno.EISDIR, "directory instead of file"),
        (errno.EIO, "I/O failure"),
        (None, "OS failure"),
        ("utf8", "valid UTF-8"),
    ],
)
def test_every_local_read_has_safe_cli_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    capsys: pytest.CaptureFixture,
    filename: str,
    case: tuple[int | str | None, str],
) -> None:
    error_number, category = case
    path = project / filename
    original_read = Path.read_bytes

    def read(path_to_read: Path) -> bytes:
        if path_to_read == path:
            if error_number == "utf8":
                return b"\xffpassword secret"
            raise OSError(error_number, "password secret https://user:password@example.com/?secret=1")
        return original_read(path_to_read)

    monkeypatch.setattr(Path, "read_bytes", read)

    if filename.startswith("pyroma/"):
        if error_number == "utf8":
            path.write_bytes(b"\xffpassword secret")
        else:
            original_open = os.open

            def open_schema(file: Path, flags: int, *args: object, **kwargs: object) -> int:
                if Path(file) == path:
                    raise OSError(error_number, "password secret https://user:password@example.com/?secret=1")
                return original_open(file, flags, *args, **kwargs)

            monkeypatch.setattr(os, "open", open_schema)
    monkeypatch.setattr("sys.argv", ["release_docs", "check", "--online", "--project", str(project)])
    fetch = Mock(side_effect=AssertionError("Read errors must precede network requests"))
    monkeypatch.setattr(release_docs, "fetch", fetch)
    with pytest.raises(SystemExit) as raised:
        release_docs.main()
    assert raised.value.code == 1
    err = capsys.readouterr().err
    label = "Packaged schema schemas/v1/rating.json" if filename.startswith("pyroma/") else f"Local {filename}"
    assert label in err
    assert category in err
    assert not any(secret in err for secret in ("Traceback", "password", "secret", "https://"))
    fetch.assert_not_called()


@pytest.mark.parametrize(
    ("inventory", "diagnostic"),
    [
        ("", "is missing"),
        ("[password secret", "valid JSON"),
        ("{}", "must be a list"),
        ("null", "must be a list"),
        ("[]", "has no JSON schemas"),
        ("[42]", "invalid schema path"),
        ('["schemas/../password.secret.json"]', "invalid schema path"),
        ('["/schemas/password.secret.json"]', "invalid schema path"),
        ('["schemas//password.secret.json"]', "invalid schema path"),
        ('["schemas/password.secret.txt"]', "invalid schema path"),
        ('["schemas/a.json","schemas/a.json"]', "duplicate schema paths"),
    ],
)
@pytest.mark.skipif(os.name == "nt", reason="Deployment workflow uses POSIX shells")
def test_invalid_deploy_manifest_fails_before_any_probe(project: Path, inventory: str, diagnostic: str) -> None:
    curl = project / "curl"
    curl.write_text("#!/bin/sh\nprintf 'unexpected probe\\n'\n", encoding="utf-8")
    curl.chmod(0o755)
    workflow = yaml.safe_load((ROOT / ".github/workflows/docs.yml").read_text())
    step = workflow_step(workflow["jobs"]["deploy"]["steps"], "schema_availability")
    result = subprocess.run(  # noqa: S603 - validate the actual manifest decoder with a local curl probe
        workflow_bash_command(step),
        cwd=project,
        env={
            **os.environ,
            "PATH": str(project) + os.pathsep + os.environ["PATH"],
            "DOCS_URL": release_docs.SITE_URL,
            "SCHEMA_PATHS_JSON": inventory,
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 1
    assert "Schema inventory from build" in result.stderr
    assert diagnostic in result.stderr
    assert not result.stdout
    assert not any(secret in result.stderr for secret in ("Traceback", "password", "secret"))


@pytest.mark.skipif(os.name == "nt", reason="Workflow uses POSIX shells")
def test_verified_workflow_inventory_is_probed_with_encoded_paths(project: Path) -> None:
    filename = "new # schema\\name\n$(touch injected).json"
    future = project / "pyroma/schemas/v2" / filename
    future.parent.mkdir()
    future.write_text("{}")
    prepare_site(project)
    environment = schema_build_environment(project)
    workflow = yaml.safe_load((ROOT / ".github/workflows/docs.yml").read_text())
    result = run_schema_build(project, environment)
    assert result.returncode == 0, result.stderr
    inventory = Path(environment["GITHUB_OUTPUT"]).read_text().removeprefix("schema_paths=").strip()
    shutil.rmtree(project / "pyroma")
    curl = project / "curl"
    curl.write_text('#!/bin/sh\nfor request_url do :; done\nprintf "%s\\n" "$request_url"\n', encoding="utf-8")
    curl.chmod(0o755)
    result = subprocess.run(  # noqa: S603 - probe exactly the verified manifest with a local curl
        workflow_bash_command(workflow_step(workflow["jobs"]["deploy"]["steps"], "schema_availability")),
        cwd=project,
        env={**environment, "SCHEMA_PATHS_JSON": inventory, "DOCS_URL": release_docs.SITE_URL},
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert set(result.stdout.splitlines()) == {
        release_docs.SITE_URL + "json/",
        *[release_docs.SITE_URL + quote(path, safe="/") for path in json.loads(inventory)],
    }
    assert not (project / "injected").exists()


def test_check_schemas_cli_supports_an_explicit_site_and_preserves_failed_manifest(
    monkeypatch: pytest.MonkeyPatch, project: Path, capsys: pytest.CaptureFixture
) -> None:
    prepare_site(project)
    site = project / "custom-site"
    (project / "site").rename(site)
    inventory = project / "inventory.json"
    arguments = [
        "release_docs",
        "check-schemas",
        "--project",
        str(project),
        "--site",
        str(site),
        "--inventory-output",
        str(inventory),
    ]
    monkeypatch.setattr("sys.argv", arguments)
    release_docs.main()
    previous = inventory.read_bytes()
    assert json.loads(previous) == ["schemas/v1/error.json", "schemas/v1/rating.json"]
    (site / "schemas/v1/rating.json").write_bytes(b"modified")
    with pytest.raises(SystemExit) as raised:
        release_docs.main()
    assert raised.value.code == 1
    assert inventory.read_bytes() == previous
    assert "Built schema schemas/v1/rating.json does not match packaged schema bytes" in capsys.readouterr().err


def test_untagged_checks_still_require_a_static_version(project: Path) -> None:
    replace_once(project / "pyproject.toml", f'version = "{VERSION}"', 'dynamic = ["version"]')
    with pytest.raises(ValueError, match=r"Local pyproject\.toml: project\.version must be a valid version string"):
        release_docs.check_offline(project)


@pytest.mark.parametrize(
    "date",
    [
        "20261012",
        "2026-W42-1",
        "2026-1-12",
        "2026-10-2",
        "2026-02-29",
        "2026-13-01",
        "2026-10-12 ",
        " 2026-10-12",
        "\uff12\uff10\uff12\uff16-\uff11\uff10-\uff11\uff12",
    ],
)
@pytest.mark.parametrize("version", [VERSION, "9.0.0rc1", "9.0.0.dev1"])
def test_tagged_checks_reject_noncanonical_changelog_dates(
    monkeypatch: pytest.MonkeyPatch, project: Path, capsys: pytest.CaptureFixture, date: str, version: str
) -> None:
    replace_once(project / "pyproject.toml", f'version = "{VERSION}"', f'version = "{version}"')
    replace_once(project / "README.md", f'rev: "{VERSION}"', f'rev: "{version}"')
    replace_once(project / "docs/changelog.md", f"## {VERSION} ({DATE})", f"## {version} ({date})")
    monkeypatch.setattr("sys.argv", ["release_docs", "check", "--project", str(project), "--release-tag", version])
    with pytest.raises(SystemExit) as raised:
        release_docs.main()
    assert raised.value.code == 1
    err = capsys.readouterr().err
    assert f"Local docs/changelog.md: Release {version}: Changelog dates must" in err
    assert "YYYY-MM-DD" in err
    assert "Traceback" not in err


@pytest.mark.parametrize("date", [DATE, "2024-02-29", "2000-02-29", "2026-12-31"])
def test_valid_calendar_dates_pass_both_checks(
    monkeypatch: pytest.MonkeyPatch, project: Path, online_evidence: dict[str, str], date: str
) -> None:
    replace_once(project / "docs/changelog.md", f"## {VERSION} ({DATE})", f"## {VERSION} ({date})")
    online_evidence["https://pypi.org/pypi/pyroma621/json"] = json.dumps(
        {"releases": {VERSION: [{"upload_time_iso_8601": f"{date}T17:42:33Z"}]}}
    )
    monkeypatch.setattr(release_docs, "fetch", lambda url, **_kwargs: online_evidence[url])
    assert release_docs.check_offline(project, VERSION) == VERSION
    release_docs.check_online(project, VERSION)


@pytest.mark.parametrize(
    "case",
    [
        ('{"const":1}', '{"const":true}', False),
        ('{"const":0}', '{"const":false}', False),
        ('{"enum":[true]}', '{"enum":[1]}', False),
        ('{"nested":{"enum":[1,false]}}', '{"nested":{"enum":[true,0]}}', False),
        ('{"const":1}', '{"const":1.0}', True),
        ('{"const":1.0}', '{"const":1}', True),
        ('{"enum":[1,2.0]}', '{"enum":[1.0,2]}', True),
        ('{"type":"number","const":1}', '{"const":1.0,"type":"number"}', True),
        ('{"enum":[1,2]}', '{"enum":[2,1]}', False),
        ('{"enum":[1,2]}', '{"enum":[1]}', False),
        ('{"const":null}', '{"const":false}', False),
        ('{"const":"1"}', '{"const":1}', False),
        ('{"const":1}', '{"const":2}', False),
        ('{"type":"number"}', '{"type":"number","const":1}', False),
        ('{"const":true}', '{"const":true}', True),
    ],
)
def test_online_schema_comparison_uses_json_types(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    online_evidence: dict[str, str],
    capsys: pytest.CaptureFixture,
    case: tuple[str, str, bool],
) -> None:
    packaged, published, equal = case
    (project / "pyroma/schemas/v1/rating.json").write_text(packaged)
    online_evidence[release_docs.SITE_URL + "schemas/v1/rating.json"] = published
    monkeypatch.setattr(release_docs, "fetch", lambda url, **_kwargs: online_evidence[url])
    monkeypatch.setattr("sys.argv", ["release_docs", "check", "--online", "--project", str(project)])
    if equal:
        release_docs.main()
    else:
        with pytest.raises(SystemExit) as raised:
            release_docs.main()
        assert raised.value.code == 1
        assert "Published schema schemas/v1/rating.json does not match packaged schema" in capsys.readouterr().err


@pytest.mark.parametrize(
    "failure",
    [
        "extra-file",
        "missing-file",
        "extra-directory",
        "missing-directory",
        pytest.param(
            "escaped", marks=pytest.mark.skipif(os.name == "nt", reason="Control characters require POSIX filenames")
        ),
    ],
)
def test_built_inventory_diagnostics_name_relative_entries(
    monkeypatch: pytest.MonkeyPatch, project: Path, capsys: pytest.CaptureFixture, failure: str
) -> None:
    prepare_site(project)
    schemas = project / "site/schemas"
    if failure == "extra-file":
        (schemas / ".DS_Store").write_text("extra")
        expected = 'files: extra ["schemas/.DS_Store"]'
    elif failure == "missing-file":
        (schemas / "v1/rating.json").unlink()
        expected = 'missing ["schemas/v1/rating.json"]'
    elif failure == "extra-directory":
        (schemas / "v99/nested").mkdir(parents=True)
        expected = 'directories: extra ["schemas/v99", "schemas/v99/nested"]'
    elif failure == "missing-directory":
        shutil.rmtree(schemas / "v1")
        expected = 'directories: extra []; missing ["schemas/v1"]'
    else:
        (schemas / "extra\nname.json").write_text("extra")
        expected = 'files: extra ["schemas/extra\\nname.json"]'
    monkeypatch.setattr("sys.argv", ["release_docs", "check-schemas", "--project", str(project)])
    with pytest.raises(SystemExit) as raised:
        release_docs.main()
    assert raised.value.code == 1
    err = capsys.readouterr().err
    assert expected in err
    assert str(project) not in err
    assert "Traceback" not in err


def test_required_schema_parents_and_ignored_empty_source_directories(project: Path) -> None:
    source = project / "pyroma/schemas"
    future = source / "v99/nested/rating.json"
    future.parent.mkdir(parents=True)
    future.write_text("{}")
    (source / "unused/empty").mkdir(parents=True)
    prepare_site(project)
    assert release_docs.check_schema_outputs(project, project / "site") == [
        Path("schemas/v1/error.json"),
        Path("schemas/v1/rating.json"),
        Path("schemas/v99/nested/rating.json"),
    ]
    assert not (project / "site/schemas/unused").exists()


@pytest.mark.parametrize("kind", ["socket", "character-device", "block-device"])
@pytest.mark.skipif(os.name == "nt", reason="Special filesystem entries require POSIX")
def test_special_source_entries_preserve_output_and_precede_fetches(
    monkeypatch: pytest.MonkeyPatch, project: Path, capsys: pytest.CaptureFixture, kind: str
) -> None:
    release_docs.prepare_schemas(project)
    previous = (project / "docs/schemas/v1/rating.json").read_bytes()
    source = project / "pyroma/schemas"
    entry_path = source / "unexpected.json"
    if kind == "socket":
        monkeypatch.chdir(source)
        with socket.socket(socket.AF_UNIX) as connection:
            connection.bind(entry_path.name)
    else:
        entry = Mock()
        entry.path = str(entry_path)
        entry.stat.return_value.st_mode = stat.S_IFCHR if kind == "character-device" else stat.S_IFBLK
        original_scan = os.scandir

        def scan(path: Path) -> object:
            if Path(path) == source:
                return scandir_entries(entry)
            return original_scan(path)

        monkeypatch.setattr(os, "scandir", scan)
    fetch = Mock(side_effect=AssertionError("Special entries must fail before fetching"))
    monkeypatch.setattr(release_docs, "fetch", fetch)
    for command in [["schemas"], ["check", "--online"]]:
        monkeypatch.setattr("sys.argv", ["release_docs", *command, "--project", str(project)])
        with pytest.raises(SystemExit) as raised:
            release_docs.main()
        assert raised.value.code == 1
        err = capsys.readouterr().err
        assert (
            "Canonical packaged schema directory (pyroma/schemas): entries must be regular files "
            "or directories at schemas/unexpected.json" in err
        )
        assert str(project) not in err
        assert "Traceback" not in err
    assert (project / "docs/schemas/v1/rating.json").read_bytes() == previous
    fetch.assert_not_called()


@pytest.mark.parametrize("broken", [False, True])
@pytest.mark.skipif(os.name == "nt", reason="Generated symlinks require POSIX")
def test_generated_root_symlinks_have_safe_cli_diagnostics(
    monkeypatch: pytest.MonkeyPatch, project: Path, capsys: pytest.CaptureFixture, broken: bool
) -> None:
    target = project / "external"
    if not broken:
        target.mkdir()
        (target / "preserved").write_text("keep")
    (project / "docs/schemas").symlink_to(target, target_is_directory=True)
    monkeypatch.setattr("sys.argv", ["release_docs", "schemas", "--project", str(project)])
    with pytest.raises(SystemExit) as raised:
        release_docs.main()
    assert raised.value.code == 1
    err = capsys.readouterr().err
    assert "Generated schema directory docs/schemas must not be a symlink" in err
    assert str(project) not in err
    assert "Traceback" not in err
    assert (project / "docs/schemas").is_symlink()
    if not broken:
        assert (target / "preserved").read_text() == "keep"


def test_generated_root_file_is_rejected_before_removal(
    monkeypatch: pytest.MonkeyPatch, project: Path, capsys: pytest.CaptureFixture
) -> None:
    destination = project / "docs/schemas"
    destination.write_text("preserved")
    monkeypatch.setattr("sys.argv", ["release_docs", "schemas", "--project", str(project)])
    with pytest.raises(SystemExit) as raised:
        release_docs.main()
    assert raised.value.code == 1
    assert "Generated schema directory docs/schemas is not a directory" in capsys.readouterr().err
    assert destination.read_text() == "preserved"


@pytest.mark.parametrize("operation", ["inspect", "remove", "mkdir", "schema-write", "manifest-write"])
@pytest.mark.parametrize(
    "case",
    [
        (errno.ENOENT, "file missing"),
        (errno.EACCES, "permission denied"),
        (errno.EIO, "I/O failure"),
        (None, "OS failure"),
    ],
)
def test_output_os_failures_have_safe_cli_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    capsys: pytest.CaptureFixture,
    operation: str,
    case: tuple[int | None, str],
) -> None:
    number, category = case
    prepare_site(project)
    destination = project / "docs/schemas"
    manifest = project / "manifest.json"
    label = "Generated schema directory docs/schemas"
    error = OSError(number, "password secret https://example.com/", str(project / "secret"))
    if operation == "inspect":
        # ENOENT means no prior output, so use an ENOTDIR inspection failure instead.
        if number == errno.ENOENT:
            error = OSError(errno.ENOTDIR, "password secret", str(destination))
            category = "path component is not a directory"
        original = Path.lstat

        def inspect(path: Path) -> os.stat_result:
            if path == destination:
                raise error
            return original(path)

        monkeypatch.setattr(Path, "lstat", inspect)
    elif operation == "remove":
        monkeypatch.setattr(shutil, "rmtree", Mock(side_effect=error))
    elif operation == "mkdir":
        monkeypatch.setattr(Path, "mkdir", Mock(side_effect=error))
        label = "Generated schema schemas/v1/error.json"
    elif operation == "manifest-write":
        monkeypatch.setattr(release_docs.tempfile, "TemporaryDirectory", Mock(side_effect=error))
        label = "Schema inventory output"
    else:
        monkeypatch.setattr(Path, "write_bytes", Mock(side_effect=error))
        label = "Generated schema schemas/v1/error.json"
    arguments = ["release_docs", "schemas", "--project", str(project)]
    if operation == "manifest-write":
        arguments = ["release_docs", "check-schemas", "--project", str(project), "--inventory-output", str(manifest)]
    monkeypatch.setattr("sys.argv", arguments)
    with pytest.raises(SystemExit) as raised:
        release_docs.main()
    assert raised.value.code == 1
    err = capsys.readouterr().err
    assert label in err
    assert category in err
    assert not any(value in err for value in ("Traceback", "password", "secret", "https://", str(project)))


@pytest.mark.parametrize("failure", ["empty-directory", "special-file"])
@pytest.mark.skipif(os.name == "nt", reason="Documentation workflows require POSIX")
def test_actual_build_step_rejects_unexpected_tree_entries(project: Path, failure: str) -> None:
    prepare_site(project)
    if failure == "empty-directory":
        (project / "site/schemas/v99").mkdir()
    else:
        os.mkfifo(project / "site/schemas/unexpected.json")
    environment = schema_build_environment(project)
    result = run_schema_build(project, environment)
    assert result.returncode == 1
    assert "Built schema" in result.stderr
    entry = "v99" if failure == "empty-directory" else "unexpected.json"
    assert entry in result.stderr
    assert not Path(environment["GITHUB_OUTPUT"]).exists()


@pytest.mark.skipif(os.name == "nt", reason="Documentation workflows require POSIX")
def test_actual_build_step_does_not_hide_a_failed_manifest_read(project: Path) -> None:
    environment = schema_build_environment(project)
    # Simulate a successful checker that did not leave the expected handoff file.
    (project / "uv").write_text("#!/bin/sh\nexit 0\n")
    result = run_schema_build(project, environment)
    assert result.returncode != 0
    assert "schema-inventory.json" in result.stderr
    assert not Path(environment["GITHUB_OUTPUT"]).exists()


@pytest.mark.skipif(os.name == "nt", reason="Real FIFOs require POSIX")
def test_fifo_source_is_rejected_in_a_bounded_process(project: Path) -> None:
    release_docs.prepare_schemas(project)
    previous = (project / "docs/schemas/v1/rating.json").read_bytes()
    os.mkfifo(project / "pyroma/schemas/unexpected.json")
    result = run_fifo_checks(project)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr.count("regular files or directories at schemas/unexpected.json") == 2
    assert "Traceback" not in result.stderr
    assert str(project) not in result.stderr
    assert (project / "docs/schemas/v1/rating.json").read_bytes() == previous


@pytest.mark.parametrize("race", ["canonical-fifo", "canonical-symlink", "built-fifo", "built-symlink"])
@pytest.mark.skipif(os.name == "nt", reason="Real FIFOs require POSIX")
def test_schema_scan_read_races_are_rejected_promptly(project: Path, race: str) -> None:
    prepare_site(project)
    previous = (project / "docs/schemas/v1/error.json").read_bytes()
    result = run_fifo_checks(project, race=race)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "schema" in result.stderr.lower()
    assert ("regular file" if race.endswith("fifo") else "symlink not permitted") in result.stderr
    assert "Traceback" not in result.stderr
    assert str(project) not in result.stderr
    assert (project / "docs/schemas/v1/error.json").read_bytes() == previous


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize(
    "case",
    [
        (phase, number)
        for phase in ["creation", "open", "write", "close", "permissions", "replace"]
        for number in [errno.ENOSPC, errno.EACCES, errno.EIO]
    ],
)
def test_atomic_inventory_failures_preserve_previous_output(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    capsys: pytest.CaptureFixture,
    existing: bool,
    case: tuple[str, int],
) -> None:
    phase, number = case
    prepare_site(project)
    manifest = project / "inventory.json"
    previous = b'["previous"]\n'
    if existing:
        manifest.write_bytes(previous)
    error = OSError(number, "password secret", str(project / "secret"))
    fail_inventory_stream(monkeypatch, phase, error)
    original_chmod = Path.chmod

    def chmod(path: Path, mode: int, **kwargs: object) -> None:
        if phase == "permissions":
            raise error
        original_chmod(path, mode, **kwargs)

    if phase == "creation":
        monkeypatch.setattr(release_docs.tempfile, "TemporaryDirectory", Mock(side_effect=error))
    monkeypatch.setattr(Path, "chmod", chmod)
    if phase == "replace":
        monkeypatch.setattr(os, "replace", Mock(side_effect=error))
    monkeypatch.setattr(
        "sys.argv", ["release_docs", "check-schemas", "--project", str(project), "--inventory-output", str(manifest)]
    )
    with pytest.raises(SystemExit) as raised:
        release_docs.main()
    assert raised.value.code == 1
    err = capsys.readouterr().err
    assert "Schema inventory output: unable to write file" in err
    assert {errno.ENOSPC: "disk full", errno.EACCES: "permission denied", errno.EIO: "I/O failure"}[number] in err
    assert not any(value in err for value in ("Traceback", "password", "secret", str(project)))
    assert manifest.read_bytes() == previous if existing else not manifest.exists()
    assert not list(project.glob(".schema-inventory-*"))


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("phase", ["creation", "replace", "cleanup"])
def test_inventory_interrupts_propagate_and_preserve_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    interrupt: type[BaseException],
    phase: str,
) -> None:
    manifest = tmp_path / "inventory.json"
    manifest.write_bytes(b"previous")
    if phase == "creation":
        monkeypatch.setattr(release_docs.tempfile, "TemporaryDirectory", Mock(side_effect=interrupt))
    else:
        monkeypatch.setattr(
            os, "replace", Mock(side_effect=interrupt if phase == "replace" else OSError(errno.ENOSPC, "secret"))
        )
        if phase == "cleanup":
            monkeypatch.setattr(release_docs.tempfile.TemporaryDirectory, "_rmtree", Mock(side_effect=interrupt))
    with pytest.raises(interrupt):
        release_docs._write_inventory(manifest, b"replacement")
    assert manifest.read_bytes() == b"previous"
    if phase != "cleanup":
        assert not list(tmp_path.glob(".schema-inventory-*"))


def test_inventory_cleanup_failure_preserves_the_primary_diagnostic(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest = tmp_path / "inventory.json"
    manifest.write_bytes(b"previous")
    monkeypatch.setattr(os, "replace", Mock(side_effect=OSError(errno.ENOSPC, "secret")))
    monkeypatch.setattr(
        release_docs.tempfile.TemporaryDirectory, "_rmtree", Mock(side_effect=OSError(errno.EACCES, "password"))
    )
    with pytest.raises(ValueError, match=r"Schema inventory output: unable to write file \(disk full\)"):
        release_docs._write_inventory(manifest, b"replacement")
    assert manifest.read_bytes() == b"previous"


@pytest.mark.parametrize("existing", [False, True])
def test_inventory_replacement_keeps_compact_manifest_format(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    existing: bool,
) -> None:
    prepare_site(project)
    manifest = project / "inventory.json"
    if existing:
        manifest.write_bytes(b"old")
    monkeypatch.setattr(
        "sys.argv", ["release_docs", "check-schemas", "--project", str(project), "--inventory-output", str(manifest)]
    )
    release_docs.main()
    assert manifest.read_bytes() == b'["schemas/v1/error.json","schemas/v1/rating.json"]\n'
    assert not list(project.glob(".schema-inventory-*"))


@pytest.mark.skipif(os.name == "nt", reason="Output symlinks require POSIX")
def test_inventory_replacement_replaces_link_without_changing_target(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_bytes(b"preserved")
    manifest = tmp_path / "inventory.json"
    manifest.symlink_to(target)
    release_docs._write_inventory(manifest, b"replacement")
    assert not manifest.is_symlink()
    assert manifest.read_bytes() == b"replacement"
    assert target.read_bytes() == b"preserved"


def test_inventory_output_does_not_create_missing_parents(tmp_path: Path) -> None:
    directory = tmp_path / "missing"
    with pytest.raises(ValueError, match=r"Schema inventory output.*file missing"):
        release_docs._write_inventory(directory / "inventory.json", b"replacement")
    assert not directory.exists()


@pytest.mark.parametrize("heading", ["missing", "unreleased"])
@pytest.mark.parametrize("version", [VERSION, "9.0.0rc1", "9.0.0.dev1"])
def test_tagged_changelog_diagnostics_distinguish_missing_and_unreleased(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    capsys: pytest.CaptureFixture,
    heading: str,
    version: str,
) -> None:
    replace_once(project / "pyproject.toml", f'version = "{VERSION}"', f'version = "{version}"')
    replace_once(project / "README.md", f'rev: "{VERSION}"', f'rev: "{version}"')
    replace_once(
        project / "docs/changelog.md",
        f"## {VERSION} ({DATE})",
        "" if heading == "missing" else f"## {version} (unreleased)",
    )
    monkeypatch.setattr("sys.argv", ["release_docs", "check", "--project", str(project), "--release-tag", version])
    with pytest.raises(SystemExit) as raised:
        release_docs.main()
    assert raised.value.code == 1
    err = capsys.readouterr().err
    assert f"Local docs/changelog.md: Release {version}" in err
    assert ("has no matching heading" if heading == "missing" else "is unreleased") in err
    if heading == "unreleased":
        assert "YYYY-MM-DD" in err
    assert "Traceback" not in err


@pytest.mark.parametrize("root_failure", ["scan", "stat"])
def test_root_inventory_errors_identify_schemas(
    monkeypatch: pytest.MonkeyPatch, project: Path, root_failure: str
) -> None:
    root = project / "pyroma/schemas"
    error = OSError(errno.EIO, "secret")
    if root_failure == "stat":
        fail_schema_discovery(monkeypatch, root, "root-stat")
    else:
        original = os.scandir

        def denied_scan(path: Path) -> object:
            if Path(path) == root:
                raise error
            return original(path)

        monkeypatch.setattr(os, "scandir", denied_scan)
    with pytest.raises(
        ValueError, match=r"Canonical packaged schema directory \(pyroma/schemas\): unable to read inventory at schemas"
    ) as raised:
        release_docs.schema_paths(project)
    assert " at . " not in str(raised.value)
    assert str(project) not in str(raised.value)


@pytest.mark.parametrize("operation", ["stat", "symlink", "special", "content", "published"])
@pytest.mark.skipif(os.name == "nt", reason="Unusual filenames and symlinks require POSIX")
def test_schema_path_diagnostics_share_one_escaped_base(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    operation: str,
) -> None:
    expected = 'schemas/v2/r\\u00e9ponse\\"\\\\line\\n.json'
    filename = 'réponse"\\line\n.json'
    relative = Path("schemas/v2") / filename
    source = project / "pyroma" / relative
    source.parent.mkdir()
    source.write_text("{}")
    prepare_site(project)
    built = project / "site" / relative
    schemas = {}
    if operation in {"stat", "special"}:
        entry = Mock()
        entry.path = str(source)
        if operation == "stat":
            entry.stat.side_effect = OSError(errno.EIO, "secret")
        else:
            entry.stat.return_value.st_mode = stat.S_IFCHR
        original = os.scandir
        monkeypatch.setattr(
            os, "scandir", lambda path: scandir_entries(entry) if Path(path) == source.parent else original(path)
        )
    elif operation == "published":
        schemas = release_docs._canonical_schemas(project)
        monkeypatch.setattr(release_docs, "fetch", lambda *_args, **_kwargs: "[]")
        # Keep only the unusual file so its failure is the one reported.
    elif operation == "symlink":
        built.unlink()
        built.symlink_to(source)
    else:
        built.write_text("[]")

    def check() -> None:
        if operation in {"stat", "special"}:
            release_docs.schema_paths(project)
        elif operation == "published":
            release_docs._check_published_contract({relative: schemas[relative]})
        else:
            release_docs.check_schema_outputs(project, project / "site")

    with pytest.raises(ValueError, match="schema") as raised:
        check()
    message = str(raised.value)
    assert expected in message
    assert str(project) not in message
    assert "secret" not in message
    assert "\n" not in message


@pytest.mark.parametrize("phase", ["write", "close"])
@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit])
def test_inventory_write_and_close_interrupts_are_preserved(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    phase: str,
    interrupt: type[BaseException],
) -> None:
    manifest = tmp_path / "inventory.json"
    manifest.write_bytes(b"previous")
    output = Mock()
    if phase == "write":
        output.write.side_effect = interrupt
        output.close.side_effect = OSError(errno.EIO, "cleanup secret")
    else:
        output.close.side_effect = interrupt
    mock_inventory_stream(monkeypatch, output)
    with pytest.raises(interrupt):
        release_docs._write_inventory(manifest, b"replacement")
    assert manifest.read_bytes() == b"previous"
    assert not list(tmp_path.glob(".schema-inventory-*"))


def test_inventory_write_failure_is_not_masked_by_close_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "inventory.json"
    manifest.write_bytes(b"previous")
    output = Mock()
    output.write.side_effect = OSError(errno.ENOSPC, "write secret")
    output.close.side_effect = OSError(errno.EIO, "close secret")
    mock_inventory_stream(monkeypatch, output)
    with pytest.raises(ValueError, match=r"Schema inventory output: unable to write file \(disk full\)"):
        release_docs._write_inventory(manifest, b"replacement")
    assert manifest.read_bytes() == b"previous"
    assert not list(tmp_path.glob(".schema-inventory-*"))


@pytest.mark.parametrize("mask", [0o002, 0o022, 0o077, 0o777])
@pytest.mark.skipif(os.name == "nt", reason="POSIX mode and umask semantics")
def test_new_manifest_uses_normal_creation_permissions(tmp_path: Path, mask: int) -> None:
    output = tmp_path / "inventory.json"
    previous = os.umask(mask)
    try:
        release_docs._write_inventory(output, b"[]\n")
    finally:
        os.umask(previous)
    assert stat.S_IMODE(output.stat().st_mode) == 0o666 & ~mask
    output.chmod(0o600)
    assert output.read_bytes() == b"[]\n"
    assert not list(tmp_path.glob(".schema-inventory-*"))


@pytest.mark.parametrize("mode", [0o400, 0o640, 0o666, 0o755, 0o4755])
@pytest.mark.skipif(os.name == "nt", reason="POSIX mode semantics")
def test_manifest_preserves_ordinary_permissions(tmp_path: Path, mode: int) -> None:
    output = tmp_path / "inventory.json"
    output.write_bytes(b"old")
    output.chmod(mode)
    release_docs._write_inventory(output, b"[]\n")
    assert stat.S_IMODE(output.stat().st_mode) == mode & 0o777
    assert output.read_bytes() == b"[]\n"


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory permissions")
def test_manifest_requires_writable_parent_without_inplace_fallback(tmp_path: Path) -> None:
    if os.getuid() == 0:
        pytest.skip("Root bypasses directory permissions")
    directory = tmp_path / "readonly"
    directory.mkdir()
    manifest = directory / "inventory.json"
    manifest.write_bytes(b"old")
    directory.chmod(0o555)
    try:
        with pytest.raises(ValueError, match=r"Schema inventory output.*permission denied"):
            release_docs._write_inventory(manifest, b"new")
        assert manifest.read_bytes() == b"old"
    finally:
        directory.chmod(0o755)


@pytest.mark.parametrize("target", ["broken", "directory"])
@pytest.mark.skipif(os.name == "nt", reason="POSIX symlinks")
def test_manifest_replaces_other_symlinks(tmp_path: Path, target: str) -> None:
    destination = tmp_path / "target"
    if target == "directory":
        destination.mkdir()
    output = tmp_path / "inventory.json"
    output.symlink_to(destination)
    release_docs._write_inventory(output, b"[]\n")
    assert not output.is_symlink()
    assert output.read_bytes() == b"[]\n"
    assert destination.is_dir() if target == "directory" else not destination.exists()


@pytest.mark.parametrize("kind", ["fifo", "linked-fifo", "socket", "directory"])
@pytest.mark.skipif(os.name == "nt", reason="POSIX special entries")
def test_manifest_rejects_special_destinations_before_creating_temporary_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str
) -> None:
    output = tmp_path / "inventory.json"
    with socket.socket(socket.AF_UNIX) as listener:
        if kind in {"fifo", "linked-fifo"}:
            target = tmp_path / "pipe"
            os.mkfifo(target)
            if kind == "linked-fifo":
                output.symlink_to(target)
            else:
                target.rename(output)
        elif kind == "socket":
            monkeypatch.chdir(tmp_path)
            listener.bind(output.name)
        else:
            output.mkdir()
        create = Mock(side_effect=AssertionError("Special output must be rejected before allocation"))
        monkeypatch.setattr(release_docs.tempfile, "TemporaryDirectory", create)
        with pytest.raises(ValueError, match=r"Schema inventory output:.*use --inventory-output - for stdout"):
            release_docs._write_inventory(output, b"[]\n")
        create.assert_not_called()
        assert not list(tmp_path.glob(".schema-inventory-*"))


@pytest.mark.parametrize("invalid", [False, True])
def test_manifest_stdout_only_follows_successful_verification(
    monkeypatch: pytest.MonkeyPatch, project: Path, capsys: pytest.CaptureFixture, invalid: bool
) -> None:
    prepare_site(project)
    if invalid:
        (project / "site/schemas/extra.json").write_text("{}")
    monkeypatch.setattr(
        "sys.argv", ["release_docs", "check-schemas", "--project", str(project), "--inventory-output", "-"]
    )
    if invalid:
        with pytest.raises(SystemExit) as raised:
            release_docs.main()
        assert raised.value.code == 1
    else:
        release_docs.main()
    out, err = capsys.readouterr()
    assert out == ("" if invalid else '["schemas/v1/error.json","schemas/v1/rating.json"]\n')
    assert bool(err) == invalid
    assert not (project / "-").exists()
    assert not list(project.glob(".schema-inventory-*"))


@pytest.mark.parametrize("phase", ["write", "flush"])
def test_manifest_stdout_failures_are_labelled(
    monkeypatch: pytest.MonkeyPatch, project: Path, capsys: pytest.CaptureFixture, phase: str
) -> None:
    prepare_site(project)
    stream = Mock()
    getattr(stream, phase).side_effect = OSError(errno.EIO, "password secret")
    monkeypatch.setattr(
        "sys.argv", ["release_docs", "check-schemas", "--project", str(project), "--inventory-output", "-"]
    )
    with monkeypatch.context() as patch:
        patch.setattr(sys, "stdout", stream)
        with pytest.raises(SystemExit) as raised:
            release_docs.main()
        assert raised.value.code == 1
    assert "Schema inventory output: unable to write file (I/O failure)" in capsys.readouterr().err
    stream.close.assert_not_called()


@pytest.mark.skipif(os.name == "nt", reason="POSIX pipes")
def test_manifest_stdout_broken_pipe_has_no_shutdown_traceback(project: Path) -> None:
    prepare_site(project)
    read_descriptor, write_descriptor = os.pipe()
    os.close(read_descriptor)
    try:
        result = subprocess.run(  # noqa: S603 - exercise the actual CLI with a closed pipe
            [
                sys.executable,
                str(ROOT / "ci/release_docs.py"),
                "check-schemas",
                "--project",
                str(project),
                "--inventory-output",
                "-",
            ],
            stdout=write_descriptor,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=10,
        )
    finally:
        os.close(write_descriptor)
    assert result.returncode == 1
    assert "Schema inventory output: unable to write file (broken pipe)" in result.stderr
    assert "Traceback" not in result.stderr
    assert "Exception ignored" not in result.stderr


@pytest.mark.parametrize("operation", ["open", "stat", "read"])
@pytest.mark.parametrize("failure", [OSError(errno.EIO, "secret"), KeyboardInterrupt(), SystemExit()])
def test_protected_schema_reader_closes_descriptors_and_preserves_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, operation: str, failure: BaseException
) -> None:
    source = tmp_path / "schema.json"
    source.write_bytes(b"{}")
    close = Mock(wraps=os.close)
    monkeypatch.setattr(os, "close", close)
    function = {"open": "open", "stat": "fstat", "read": "read"}[operation]
    monkeypatch.setattr(os, function, Mock(side_effect=failure))
    if isinstance(failure, OSError):
        with pytest.raises(ValueError, match=r"Packaged schema schemas/schema.json: unable to read file.*I/O failure"):
            release_docs._read_schema_bytes(source, "Packaged schema schemas/schema.json")
    else:
        with pytest.raises(type(failure)):
            release_docs._read_schema_bytes(source, "Packaged schema schemas/schema.json")
    assert close.call_count == (0 if operation == "open" else 1)


@pytest.mark.parametrize("mode", [stat.S_IFIFO, stat.S_IFSOCK, stat.S_IFCHR, stat.S_IFDIR])
def test_protected_schema_reader_checks_the_opened_descriptor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: int
) -> None:
    source = tmp_path / "schema.json"
    source.write_bytes(b"{}")
    monkeypatch.setattr(os, "fstat", Mock(return_value=Mock(st_mode=mode)))
    read = Mock(side_effect=AssertionError("Never read a nonregular descriptor"))
    monkeypatch.setattr(os, "read", read)
    with pytest.raises(ValueError, match="schema must be a regular file"):
        release_docs._read_schema_bytes(source, "Packaged schema schemas/schema.json")
    read.assert_not_called()


@pytest.mark.parametrize("linked", [False, True])
def test_schema_reader_fallback_retains_nonfollowing_precheck(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, linked: bool
) -> None:
    source = tmp_path / "schema.json"
    source.write_bytes(b"{}")
    if linked:
        if os.name == "nt":
            pytest.skip("Symlink creation requires privileges")
        target = tmp_path / "link.json"
        target.symlink_to(source)
        source = target
    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
    if linked:
        with pytest.raises(ValueError, match="schema must be a regular file"):
            release_docs._read_schema_bytes(source, "Packaged schema schemas/schema.json")
    else:
        assert release_docs._read_schema_bytes(source, "Packaged schema schemas/schema.json") == b"{}"


@pytest.mark.parametrize("number", [errno.EACCES, errno.EIO])
def test_preserved_permission_failure_keeps_manifest_and_has_safe_cli_diagnostic(
    monkeypatch: pytest.MonkeyPatch, project: Path, capsys: pytest.CaptureFixture, number: int
) -> None:
    prepare_site(project)
    manifest = project / "inventory.json"
    manifest.write_bytes(b"old")
    original = Path.chmod

    def chmod(path: Path, mode: int, **kwargs: object) -> None:
        if path.name == "inventory.json" and path.parent.name.startswith(".schema-inventory-"):
            raise OSError(number, "secret password", str(path))
        original(path, mode, **kwargs)

    monkeypatch.setattr(Path, "chmod", chmod)
    monkeypatch.setattr(
        "sys.argv", ["release_docs", "check-schemas", "--project", str(project), "--inventory-output", str(manifest)]
    )
    with pytest.raises(SystemExit) as raised:
        release_docs.main()
    assert raised.value.code == 1
    err = capsys.readouterr().err
    assert "Schema inventory output: unable to write file" in err
    assert not any(value in err for value in ("Traceback", "secret", "password", str(project)))
    assert manifest.read_bytes() == b"old"
    assert not list(project.glob(".schema-inventory-*"))


@pytest.mark.parametrize("root", ["canonical", "built"])
def test_schema_root_labels_identify_their_location(project: Path, root: str) -> None:
    prepare_site(project)
    if root == "canonical":
        shutil.rmtree(project / "pyroma/schemas")
        expected = "Canonical packaged schema directory (pyroma/schemas)"
    else:
        shutil.rmtree(project / "site/schemas")
        expected = "Built schema directory (schemas relative to --site)"

    def verify() -> None:
        if root == "canonical":
            release_docs.schema_paths(project)
        else:
            release_docs.check_schema_outputs(project, project / "site")

    with pytest.raises(ValueError, match=re.escape(expected)) as raised:
        verify()
    assert expected in str(raised.value)
    assert str(project) not in str(raised.value)
