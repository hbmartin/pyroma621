"""Rate Python packages for packaging best practices."""

import sys
from argparse import ArgumentParser, ArgumentTypeError
from contextlib import suppress
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any, cast

from pyroma import distributiondata, projectdata, pypidata, ratings, report
from pyroma import metadata as metadata_types

_MIN_RATING = 1
_MAX_RATING = 10
_RELEASE_MIN_RATING = 8


def zester(data: "dict[str, Any]") -> None:
    """Run Pyroma from the optional zest.releaser prerelease hook."""
    working_directory = Path(data["workingdir"])
    main_files = {entry.name for entry in working_directory.iterdir()}
    config_files = {"setup.py", "setup.cfg", "pyproject.toml"}

    # If there are no standard Python config files in the main files
    # it's likely not a Python project, so just return.
    if not config_files & main_files:
        return

    # zest.releaser is an optional integration imported only for its hook.
    from zest.releaser.utils import ask  # noqa: PLC0415

    if ask("Run pyroma on the package before tagging?"):
        rating = run("directory", str(working_directory.resolve()), skip_tests="CheckManifest")
        if rating < _RELEASE_MIN_RATING and not ask("Continue?"):
            sys.exit(1)


def min_argument(arg: str) -> int:
    """Parse and validate the CLI's minimum-rating argument."""
    try:
        f = int(arg)
    except ValueError as e:
        message = "Must be an integer between 1 and 10"
        raise ArgumentTypeError(message) from e
    if f < 0:
        message = "Oh, it's not THAT bad, trust me."
        raise ArgumentTypeError(message)
    if f < _MIN_RATING:
        message = "Why run pyroma if you intend it to always pass?"
        raise ArgumentTypeError(message)
    if f > _MAX_RATING:
        message = "Why run pyroma if you intend it to never pass?"
        raise ArgumentTypeError(message)
    return f


def get_all_tests() -> "list[str]":
    """Return the names of every registered rating test."""
    return [x.__class__.__name__ for x in ratings.ALL_TESTS]


def parse_tests(arg: str) -> "list[str] | None":
    """Parse a separated list of rating-test names."""
    if not arg:
        return None

    # Split on spaces, commas and semicolons
    names = [arg]
    for sep in " ,;":
        skips: list[str] = []
        for t in names:
            skips.extend(t.split(sep))
        names = skips
    # Trailing or doubled separators leave empty tokens behind.
    names = [name for name in names if name]

    tests = get_all_tests()
    for skip in names:
        if skip not in tests:
            # Invalid test mentioned, fail and print valid tests
            return None

    return names


def skip_tests(arg: str) -> "list[str]":
    """Parse skipped tests or raise an argparse validation error."""
    test_to_skip = parse_tests(arg)
    if test_to_skip:
        return test_to_skip

    # It returned None, so there was an invalid test mentioned, or none at all
    tests = ", ".join(get_all_tests())
    message = f"Invalid tests listed. Available tests: {tests}"
    raise ArgumentTypeError(message)


def main() -> None:
    """Run the command-line interface."""
    parser = ArgumentParser()
    # argparse only grew the color attribute in Python 3.14.
    cast_parser = cast("Any", parser)
    cast_parser.color = True
    parser.add_argument(
        "package",
        help="A python package, can be a directory, a distribution file or a PyPI package name.",
    )
    parser.add_argument(
        "-n",
        "--min",
        dest="min",
        default=8,
        action="store",
        type=min_argument,
        help="Minimum rating for clean return between 1 and 10, inclusive.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "-a",
        "--auto",
        dest="mode",
        action="store_const",
        const="auto",
        help="Select mode automatically (default)",
    )
    group.add_argument(
        "-d",
        "--directory",
        dest="mode",
        action="store_const",
        const="directory",
        help="Run pyroma on a module in a project directory",
    )
    group.add_argument(
        "-f",
        "--file",
        dest="mode",
        action="store_const",
        const="file",
        help="Run pyroma on a distribution file",
    )
    group.add_argument(
        "-p",
        "--pypi",
        dest="mode",
        action="store_const",
        const="pypi",
        help="Run pyroma on a package on PyPI",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        dest="quiet",
        action="store_true",
        default=False,
        help="Output only the rating",
    )
    parser.add_argument(
        "--format",
        dest="output_format",
        choices=report.FORMATS,
        default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--skip-tests",
        type=skip_tests,
        help="Skip the named tests",
    )
    parser.add_argument(
        "--index-url",
        dest="index_url",
        help="Base URL of a PyPI-compatible package index (default: https://pypi.org)",
    )

    args = parser.parse_args()

    mode = args.mode
    if args.mode is None or args.mode == "auto":
        package_path = Path(args.package)
        if package_path.is_dir():
            mode = "directory"
        elif package_path.is_file():
            mode = "file"
        else:
            mode = "pypi"

    try:
        rating = run(mode, args.package, args.quiet, args.skip_tests, args.index_url, args.output_format)
    except (OSError, ratings.ConfigurationError, ValueError) as e:
        if args.output_format == "json":
            print(report.format_json_error(e, _json_meta(mode, args.package)))
        else:
            print(f"Error: {e}", file=sys.stderr)
        sys.exit(3)
    if rating < args.min:
        sys.exit(2)
    sys.exit(0)


def _json_meta(mode: str, argument: str) -> "dict[str, str]":
    meta = {"package": argument, "mode": mode}
    with suppress(PackageNotFoundError):
        meta["pyroma"] = package_version("pyroma621")
    return meta


def _get_data(mode: str, argument: str, index_url: "str | None" = None) -> metadata_types.Metadata:
    if mode == "directory":
        return projectdata.get_data(str(Path(argument).resolve()))
    if mode == "file":
        return distributiondata.get_data(str(Path(argument).resolve()))
    # It's probably a package name
    return pypidata.get_data(argument, index_url=index_url)


def run(  # noqa: PLR0913 - The positional API is retained for compatibility.
    mode: str,
    argument: str,
    quiet: bool = False,  # noqa: FBT001, FBT002 - Public compatibility.
    skip_tests: "list[str] | str | None" = None,
    index_url: "str | None" = None,
    output_format: str = "text",
) -> int:
    """Rate a package and print the result. Returns the rating as an int."""
    verbose = not quiet and output_format == "text"

    if verbose:
        print("-" * 30)
        print("Checking " + argument)

    data = _get_data(mode, argument, index_url=index_url)
    try:
        if verbose:
            print("Found " + (data.get("name") or "nothing"))

        rated = ratings.rate_project(data, skip_tests)

        if output_format == "json":
            print(report.format_json(rated, _json_meta(mode, argument)))
        elif quiet:
            print(rated.rating)
        else:
            print(report.format_text(rated))

        return rated.rating
    finally:
        distributiondata.cleanup(data)
