"""Check that release preflight validates the selected tag revision."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ci import package_artifacts as module


@pytest.fixture
def project(tmp_path: Path) -> Path:
    path = tmp_path / "pyproject.toml"
    path.write_text('[project]\nversion="6.1.0"\n', encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("event_name", "ref", "tag"),
    [
        ("workflow_dispatch", "refs/tags/v6.1.0", "v6.1.0"),
        ("workflow_dispatch", "refs/tags/6.1.0", "6.1.0"),
        ("workflow_dispatch", "refs/heads/master", ""),
        ("release", "refs/tags/v6.1.0", "v6.1.0"),
        ("push", "refs/heads/master", "v6.1.0"),
        ("pull_request", "refs/pull/1/merge", "v6.1.0"),
    ],
)
def test_preflight_allows_matching_tags_and_other_events(project: Path, event_name: str, ref: str, tag: str) -> None:
    module.preflight(project, tag, event_name, ref)


@pytest.mark.parametrize(
    ("ref", "tag"),
    [
        ("refs/heads/master", "v6.1.0"),
        ("refs/heads/v6.1.0", "v6.1.0"),
        ("refs/tags/v6.0.0", "v6.1.0"),
        ("refs/tags/6.1.0", "v6.1.0"),
        ("refs/tags/v6.1.0", "v6.1"),
        ("", "v6.1.0"),
    ],
)
def test_preflight_requires_exact_tag_identity(project: Path, ref: str, tag: str) -> None:
    with pytest.raises(ValueError, match="Dispatch ref"):
        module.preflight(project, tag, "workflow_dispatch", ref)


@pytest.mark.parametrize("tag", ["vgarbage", "vv6.1.0", "V6.1.0", "v6.foo", "v6.2.0", "v6.1.0 "])
def test_preflight_rejects_bad_versions_on_matching_refs(project: Path, tag: str) -> None:
    with pytest.raises(ValueError, match=r"Release tag|Invalid version"):
        module.preflight(project, tag, "workflow_dispatch", f"refs/tags/{tag}")


@pytest.mark.parametrize("tag", ["6.1", "v6.1.0.0"])
def test_preflight_preserves_version_aliases(project: Path, tag: str) -> None:
    module.preflight(project, tag, "workflow_dispatch", f"refs/tags/{tag}")


def test_tagless_preflight_does_not_require_a_project(tmp_path: Path) -> None:
    module.preflight(tmp_path / "missing.toml", "", "workflow_dispatch", "refs/heads/master")


@pytest.mark.parametrize("ref", ["refs/tags/v6.1.0", "refs/heads/master"])
def test_preflight_cli_uses_environment_ref_and_returns_controlled_status(project: Path, ref: str) -> None:
    result = subprocess.run(  # noqa: S603 - fixed Python guard entry point and runner context
        [sys.executable, str(Path(module.__file__).resolve()), "preflight", "--release-tag", "v6.1.0"],
        cwd=project.parent,
        env={**os.environ, "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": ref},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if ref.startswith("refs/tags/"):
        assert result.returncode == 0
        assert result.stderr == ""
    else:
        assert result.returncode == 1
        assert "Release preflight failed: Dispatch ref" in result.stderr
        assert "Traceback" not in result.stderr


def test_preflight_cli_explicit_context_overrides_environment(project: Path) -> None:
    result = subprocess.run(  # noqa: S603 - fixed validator entry point and explicit release context
        [
            sys.executable,
            str(Path(module.__file__).resolve()),
            "preflight",
            "--project",
            str(project),
            "--release-tag",
            "v6.1.0",
            "--event-name",
            "workflow_dispatch",
            "--ref",
            "refs/tags/v6.1.0",
        ],
        cwd=project.parent,
        env={**os.environ, "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/heads/master"},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("tag", ["vgarbage", "v6.2.0"])
def test_preflight_cli_rejects_invalid_tags_before_artifacts_exist(project: Path, tag: str) -> None:
    result = subprocess.run(  # noqa: S603 - fixed validator entry point and isolated project
        [sys.executable, str(Path(module.__file__).resolve()), "preflight", "--release-tag", tag],
        cwd=project.parent,
        env={**os.environ, "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": f"refs/tags/{tag}"},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 1
    assert "Release preflight failed: Release tag" in result.stderr
    assert "Traceback" not in result.stderr
    assert not (project.parent / "dist").exists()
