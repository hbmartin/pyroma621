"""Check that release preflight validates the selected tag revision."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ci import check_release_dispatch as module


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
def test_dispatch_guard_allows_matching_tags_and_other_events(event_name: str, ref: str, tag: str) -> None:
    module.check_dispatch(event_name, ref, tag)


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
def test_dispatch_guard_requires_exact_tag_identity(ref: str, tag: str) -> None:
    with pytest.raises(ValueError, match="Dispatch ref"):
        module.check_dispatch("workflow_dispatch", ref, tag)


@pytest.mark.parametrize("ref", ["refs/tags/v6.1.0", "refs/heads/master"])
def test_dispatch_guard_cli_returns_controlled_status(tmp_path: Path, ref: str) -> None:
    result = subprocess.run(  # noqa: S603 - fixed Python guard entry point and runner context
        [sys.executable, str(Path(module.__file__).resolve())],
        cwd=tmp_path,
        env={**os.environ, "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": ref, "RELEASE_TAG": "v6.1.0"},
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
