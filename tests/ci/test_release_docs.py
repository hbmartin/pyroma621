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
    return tmp_path


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


@pytest.mark.parametrize("failure", ["", "distribution", "hook", "date"])
def test_online_release_evidence(monkeypatch: pytest.MonkeyPatch, project: Path, failure: str) -> None:
    def evidence(url: str) -> str:
        if url.endswith("pyproject.toml"):
            name = "pyroma" if failure == "distribution" else "pyroma621"
            return f'[project]\nname="{name}"\nversion="6.1.0"\n'
        if url.endswith(".pre-commit-hooks.yaml"):
            return "" if failure == "hook" else "- id: pyroma\n  entry: pyroma\n"
        if url.endswith("/json"):
            date = "2026-10-13" if failure == "date" else "2026-10-12"
            return json.dumps({"releases": {"6.1.0": [{"upload_time_iso_8601": date + "T17:42:33Z"}], "empty": []}})
        return "documentation"

    fetch = Mock(side_effect=evidence)
    monkeypatch.setattr(release_docs, "fetch", fetch)
    if failure:
        with pytest.raises(ValueError, match=r"pre-commit|hook|Changelog"):
            release_docs.check_online(project, "6.1.0")
    else:
        release_docs.check_online(project, "6.1.0")
        assert fetch.call_count == 7


def test_schema_publication(project: Path) -> None:
    shutil.copytree(Path(pyroma.__file__).parent / "schemas", project / "pyroma" / "schemas")
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
