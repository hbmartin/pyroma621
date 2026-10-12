"""Validate release documentation without depending on live services."""

import json
import re
import shutil
import tomllib
from collections.abc import Callable
from pathlib import Path
from unittest.mock import Mock
from urllib.error import URLError

import pytest
from jsonschema import Draft202012Validator
from packaging.version import Version
from zest.releaser import utils
from zest.releaser.prerelease import HISTORY_HEADER, Prereleaser

import pyroma
from ci import release_docs

ROOT = Path(__file__).resolve().parents[2]
VERSION = "9.0.0"
NEXT_VERSION = "9.1.0"
DATE = "2026-10-12"


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, f"Expected one mutation target {old!r} in {path.name}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


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
    for kind in ("rating", "error"):
        relative = Path("schemas") / "v1" / f"{kind}.json"
        schema = json.loads((project / "pyroma" / relative).read_text(encoding="utf-8"))
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
    with pytest.raises(ValueError, match="dated"):
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


@pytest.mark.parametrize("renamed", [False, True])
def test_missing_local_hook_manifest_fails_clearly(
    monkeypatch: pytest.MonkeyPatch, project: Path, capsys: pytest.CaptureFixture, renamed: bool
) -> None:
    manifest = project / ".pre-commit-hooks.yaml"
    if renamed:
        manifest.rename(project / "renamed-hooks.yaml")
    else:
        manifest.unlink()
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
            "Published rating schema",
            "Published error schema",
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
        message = "Expecting value"
    else:
        schema = json.loads(online_evidence[url])
        schema["title"] = "Stale published schema"
        online_evidence[url] = json.dumps(schema)
        message = f"Published {kind} schema does not match packaged schema"
    monkeypatch.setattr(release_docs, "fetch", lambda url, **_kwargs: online_evidence[url])
    with pytest.raises(ValueError, match=message):
        release_docs.check_online(project, VERSION)


def test_schema_publication(project: Path) -> None:
    release_docs.prepare_schemas(project)
    for kind in ("rating", "error"):
        relative = Path("schemas") / "v1" / f"{kind}.json"
        assert (project / "docs" / relative).read_bytes() == (project / "pyroma" / relative).read_bytes()


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
        "> <!-- pyroma-pre-commit -->",
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
    with pytest.raises(ValueError, match="Canonical packaged schema directory is missing"):
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
    with pytest.raises(ValueError, match=f"{NEXT_VERSION} needs a dated changelog entry"):
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
