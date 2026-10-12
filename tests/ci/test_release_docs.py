"""Validate release documentation without depending on live services."""

import json
import re
import shutil
from pathlib import Path
from unittest.mock import Mock
from urllib.error import URLError

import pytest

import pyroma
from ci import release_docs
from tests.contract.test_cli import validator

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    for filename in ("README.md", "pyproject.toml", "zensical.toml"):
        shutil.copy(ROOT / filename, tmp_path / filename)
    (tmp_path / "docs").mkdir()
    shutil.copy(ROOT / "docs" / "changelog.md", tmp_path / "docs" / "changelog.md")
    shutil.copytree(Path(pyroma.__file__).parent / "schemas", tmp_path / "pyroma" / "schemas")
    return tmp_path


@pytest.fixture
def online_evidence(project: Path) -> dict[str, str]:
    base = "https://raw.githubusercontent.com/hbmartin/pyroma621/6.1.0/"
    responses = {
        release_docs.SITE_URL + page: "documentation" for page in ("", "rules/", "upgrading/", "changelog/", "json/")
    }
    for kind in ("rating", "error"):
        relative = Path("schemas") / "v1" / f"{kind}.json"
        schema = json.loads((project / "pyroma" / relative).read_text(encoding="utf-8"))
        responses[release_docs.SITE_URL + relative.as_posix()] = json.dumps(schema, sort_keys=True)
    responses[base + "pyproject.toml"] = '[project]\nname="pyroma621"\nversion="6.1.0"\n'
    responses[base + ".pre-commit-hooks.yaml"] = "- id: pyroma\n  entry: pyroma\n"
    responses["https://pypi.org/pypi/pyroma621/json"] = json.dumps(
        {"releases": {"6.1.0": [{"upload_time_iso_8601": "2026-10-12T17:42:33Z"}], "empty": []}}
    )
    return responses


def test_documentation_consistency() -> None:
    assert release_docs.check_offline(ROOT) == "6.1.0"


def test_documentation_json_examples() -> None:
    for path in (ROOT / "docs").glob("*.md"):
        for block in re.findall(r"```json\n(.*?)\n```", path.read_text(encoding="utf-8"), re.DOTALL):
            document = json.loads(block)
            validator("error" if "error" in document else "rating").validate(document)


@pytest.mark.parametrize(
    ("filename", "old", "new", "message"),
    [
        ("README.md", "pip install pyroma621", "pip install pyroma", "distribution"),
        ("README.md", "https://hbmartin.github.io/pyroma621/", "https://hbmartin.github.io/pyroma/", "canonical"),
        ("README.md", "repo: https://github.com/hbmartin/pyroma621", "repo: https://github.com/regebro/pyroma", "hook"),
        ("README.md", 'rev: "6.1.0"', "", "revision"),
        ("docs/changelog.md", "6.1.0 (2026-10-12)", "6.1.0 (unreleased)", "dated"),
        ("docs/changelog.md", "6.0.0 (2026-08-01)", "6.0.0 (2026-08-02)", "2026-08-01"),
        ("zensical.toml", release_docs.SITE_URL, "https://example.com/", "canonical"),
    ],
)
def test_documentation_regressions(project: Path, filename: str, old: str, new: str, message: str) -> None:
    path = project / filename
    path.write_text(path.read_text().replace(old, new))
    with pytest.raises(ValueError, match=message):
        release_docs.check_offline(project)


def test_future_release_needs_a_date(project: Path) -> None:
    metadata = project / "pyproject.toml"
    metadata.write_text(metadata.read_text().replace('version = "6.1.0"', 'version = "6.2.0"', 1))
    assert release_docs.check_offline(project) == "6.1.0"
    with pytest.raises(ValueError, match="dated"):
        release_docs.check_offline(project, "6.2.0")
    with pytest.raises(ValueError, match="does not match"):
        release_docs.check_offline(project, "6.1.0")


@pytest.mark.parametrize("revision", ["6.1.0", "6.2.0", "v6.2.0", "6.2"])
def test_tagged_release_requires_matching_hook(project: Path, revision: str) -> None:
    metadata = project / "pyproject.toml"
    metadata.write_text(metadata.read_text().replace('version = "6.1.0"', 'version = "6.2.0"', 1))
    changelog = project / "docs" / "changelog.md"
    changelog.write_text(changelog.read_text() + "\n## 6.2.0 (2026-10-12)\n")
    readme = project / "README.md"
    readme.write_text(readme.read_text().replace('rev: "6.1.0"', f'rev: "{revision}"'))
    assert release_docs.check_offline(project) == revision
    if revision == "6.1.0":
        with pytest.raises(ValueError, match="pre-commit revision does not match release"):
            release_docs.check_offline(project, "v6.2.0")
    else:
        assert release_docs.check_offline(project, "v6.2.0") == revision


@pytest.mark.parametrize("failure", ["", "distribution", "hook", "date"])
def test_online_release_evidence(
    monkeypatch: pytest.MonkeyPatch, project: Path, online_evidence: dict[str, str], failure: str
) -> None:
    base = "https://raw.githubusercontent.com/hbmartin/pyroma621/6.1.0/"
    if failure == "distribution":
        online_evidence[base + "pyproject.toml"] = '[project]\nname="pyroma"\nversion="6.1.0"\n'
    elif failure == "hook":
        online_evidence[base + ".pre-commit-hooks.yaml"] = ""
    elif failure == "date":
        url = "https://pypi.org/pypi/pyroma621/json"
        online_evidence[url] = online_evidence[url].replace("2026-10-12", "2026-10-13")
    fetch = Mock(side_effect=online_evidence.__getitem__)
    monkeypatch.setattr(release_docs, "fetch", fetch)
    if failure:
        with pytest.raises(ValueError, match=r"pre-commit|hook|Changelog"):
            release_docs.check_online(project, "6.1.0")
    else:
        release_docs.check_online(project, "6.1.0")
        assert {call.args[0] for call in fetch.call_args_list} == set(online_evidence)


@pytest.mark.parametrize("missing", ["json/", "schemas/v1/rating.json", "schemas/v1/error.json"])
def test_online_contract_links_must_exist(
    monkeypatch: pytest.MonkeyPatch, project: Path, online_evidence: dict[str, str], missing: str
) -> None:
    def evidence(url: str) -> str:
        if url == release_docs.SITE_URL + missing:
            message = "missing contract download"
            raise URLError(message)
        return online_evidence[url]

    monkeypatch.setattr(release_docs, "fetch", evidence)
    with pytest.raises(URLError, match="missing contract download"):
        release_docs.check_online(project, "6.1.0")


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
    monkeypatch.setattr(release_docs, "fetch", online_evidence.__getitem__)
    with pytest.raises(ValueError, match=message):
        release_docs.check_online(project, "6.1.0")


def test_schema_publication(project: Path) -> None:
    release_docs.prepare_schemas(project)
    for kind in ("rating", "error"):
        relative = Path("schemas") / "v1" / f"{kind}.json"
        assert (project / "docs" / relative).read_bytes() == (project / "pyroma" / relative).read_bytes()


def test_fetch_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
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
    with pytest.raises(ValueError, match="response limit"):
        release_docs.fetch("https://example.com/")
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
