"""Require a manual release preflight to run on its supplied tag."""

import os
import sys


def check_dispatch(event_name: str, ref: str, release_tag: str) -> None:
    """Reject manual release validation against a different branch or tag."""
    if event_name == "workflow_dispatch" and release_tag and ref != f"refs/tags/{release_tag}":
        message = f"Dispatch ref {ref!r} must be 'refs/tags/{release_tag}' when release-tag is supplied"
        raise ValueError(message)


def main() -> None:
    """Check the runner context before building release distributions."""
    try:
        check_dispatch(
            os.environ.get("GITHUB_EVENT_NAME", ""),
            os.environ.get("GITHUB_REF", ""),
            os.environ.get("RELEASE_TAG", ""),
        )
    except ValueError as error:
        sys.exit(f"Release preflight failed: {error}")


if __name__ == "__main__":
    main()
