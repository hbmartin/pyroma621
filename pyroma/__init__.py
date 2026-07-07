import os
import sys
from argparse import ArgumentParser, ArgumentTypeError
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from typing import Any

from pyroma import distributiondata, projectdata, pypidata, ratings, report
from pyroma import metadata as metadata_types


def zester(data: "dict[str, Any]") -> None:
    main_files = set(os.listdir(data["workingdir"]))
    config_files = {"setup.py", "setup.cfg", "pyproject.toml"}

    # If there are no standard Python config files in the main files
    # it's likely not a Python project, so just return.
    if not config_files & main_files:
        return

    from zest.releaser.utils import ask

    if ask("Run pyroma on the package before tagging?"):
        rating = run("directory", os.path.abspath(data["workingdir"]), skip_tests="CheckManifest")
        if rating < 8:
            if not ask("Continue?"):
                sys.exit(1)


def min_argument(arg: str) -> int:
    try:
        f = int(arg)
    except ValueError as e:
        raise ArgumentTypeError("Must be an integer between 1 and 10") from e
    if f < 0:
        raise ArgumentTypeError("Oh, it's not THAT bad, trust me.")
    if f < 1:
        raise ArgumentTypeError("Why run pyroma if you intend it to always pass?")
    if f > 10:
        raise ArgumentTypeError("Why run pyroma if you intend it to never pass?")
    return f


def get_all_tests() -> "list[str]":
    return [x.__class__.__name__ for x in ratings.ALL_TESTS]


def parse_tests(arg: str) -> "list[str] | None":
    if not arg:
        return None

    # Split on spaces, commas and semicolons
    names = [arg]
    for sep in " ,;":
        skips: list[str] = []
        for t in names:
            skips.extend(t.split(sep))
        names = skips

    tests = get_all_tests()
    for skip in names:
        if skip not in tests:
            # Invalid test mentioned, fail and print valid tests
            return None

    return names


def skip_tests(arg: str) -> "list[str]":
    test_to_skip = parse_tests(arg)
    if test_to_skip:
        return test_to_skip

    # It returned None, so there was an invalid test mentioned, or none at all
    tests = ", ".join(get_all_tests())
    message = f"Invalid tests listed. Available tests: {tests}"
    raise ArgumentTypeError(message)


def main() -> None:
    parser = ArgumentParser()
    parser.color = True  # type: ignore[attr-defined]
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
        if os.path.isdir(args.package):
            mode = "directory"
        elif os.path.isfile(args.package):
            mode = "file"
        else:
            mode = "pypi"

    rating = run(mode, args.package, args.quiet, args.skip_tests, args.index_url, args.output_format)
    if rating < args.min:
        sys.exit(2)
    sys.exit(0)


def _get_data(mode: str, argument: str, index_url: "str | None" = None) -> metadata_types.Metadata:
    if mode == "directory":
        return projectdata.get_data(os.path.abspath(argument))
    if mode == "file":
        return distributiondata.get_data(os.path.abspath(argument))
    # It's probably a package name
    return pypidata.get_data(argument, index_url=index_url)


def run(
    mode: str,
    argument: str,
    quiet: bool = False,
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

    if verbose:
        print("Found " + (data.get("name") or "nothing"))

    rated = ratings.rate_project(data, skip_tests)

    if output_format == "json":
        meta = {"package": argument, "mode": mode}
        try:
            meta["pyroma"] = package_version("pyroma")
        except PackageNotFoundError:
            pass
        print(report.format_json(rated, meta))
    elif quiet:
        print(rated.rating)
    else:
        print(report.format_text(rated))

    return rated.rating
