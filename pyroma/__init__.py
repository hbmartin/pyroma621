"""Rate Python packages for packaging best practices."""

import sys
from argparse import ArgumentParser, ArgumentTypeError, Namespace
from contextlib import suppress
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any, cast

from pyroma import config as config_module
from pyroma import distributiondata, projectdata, pypidata, ratings, report, rules
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


def get_all_categories() -> "list[str]":
    """Return every skippable rule category."""
    return rules.categories()


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

    # A token is either a test class name or a whole category.
    selectable = set(get_all_tests()) | set(get_all_categories())
    for skip in names:
        if skip not in selectable:
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
    categories = ", ".join(get_all_categories())
    message = f"Invalid tests listed. Available tests: {tests}. Available categories: {categories}"
    raise ArgumentTypeError(message)


def _build_parser() -> ArgumentParser:
    """Construct the command-line argument parser."""
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
        # None, not 8, so that an explicit flag can be told apart from a
        # default and take precedence over [tool.pyroma] min-rating.
        default=None,
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
    parser.add_argument(
        "--strict",
        dest="strict",
        action="store_true",
        default=None,
        help="Score the advisory checks as well, instead of only reporting them",
    )
    parser.add_argument(
        "--no-advisories",
        dest="show_advisories",
        action="store_false",
        default=None,
        help="Do not report advisory findings",
    )
    parser.add_argument(
        "--no-config",
        dest="use_config",
        action="store_false",
        default=True,
        help="Ignore the target project's [tool.pyroma] configuration",
    )
    return parser


def _resolve_config(args: Namespace, mode: str) -> config_module.Config:
    """Merge command-line arguments over the project's configuration.

    An explicitly supplied flag always wins; the configuration only fills in
    what the user did not ask for.
    """
    settings = config_module.Config()
    if args.use_config and mode == "directory":
        settings = config_module.from_directory(args.package)

    skip = args.skip_tests or settings.skip_tests
    return config_module.Config(
        min_rating=args.min if args.min is not None else settings.min_rating,
        strict=args.strict if args.strict is not None else settings.strict,
        show_advisories=(args.show_advisories if args.show_advisories is not None else settings.show_advisories),
        skip_tests=skip,
    )


def _resolve_mode(mode: "str | None", package: str) -> str:
    """Resolve the rating mode, inferring it from the argument when automatic."""
    if mode is not None and mode != "auto":
        return mode
    package_path = Path(package)
    if package_path.is_dir():
        return "directory"
    if package_path.is_file():
        return "file"
    return "pypi"


def main() -> None:
    """Run the command-line interface."""
    args = _build_parser().parse_args()
    mode = _resolve_mode(args.mode, args.package)

    try:
        settings = _resolve_config(args, mode)
        rating = run(
            mode,
            args.package,
            args.quiet,
            settings.skip_tests,
            args.index_url,
            args.output_format,
            strict=settings.strict,
            show_advisories=settings.show_advisories,
        )
    except (OSError, ratings.ConfigurationError, config_module.ConfigError, ValueError) as e:
        if args.output_format == "json":
            print(report.format_json_error(e, _json_meta(mode, args.package)))
        else:
            print(f"Error: {e}", file=sys.stderr)
        sys.exit(3)
    if rating < settings.min_rating:
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


def _emit(
    rated: ratings.RatedProject,
    output_format: str,
    meta: "dict[str, str]",
    *,
    quiet: bool,
    show_advisories: bool = True,
) -> None:
    """Print a rating result in the requested output format."""
    if output_format == "json":
        print(report.format_json(rated, meta))
    elif quiet:
        print(rated.rating)
    else:
        print(report.format_text(rated, show_advisories=show_advisories))


def run(  # noqa: PLR0913 - The positional API is retained for compatibility.
    mode: str,
    argument: str,
    quiet: bool = False,  # noqa: FBT001, FBT002 - Public compatibility.
    skip_tests: "list[str] | str | None" = None,
    index_url: "str | None" = None,
    output_format: str = "text",
    *,
    strict: bool = False,
    show_advisories: bool = True,
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

        rated = ratings.rate_project(data, skip_tests, strict=strict)
        _emit(
            rated,
            output_format,
            _json_meta(mode, argument),
            quiet=quiet,
            show_advisories=show_advisories,
        )
        return rated.rating
    finally:
        distributiondata.cleanup(data)
