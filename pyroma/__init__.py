"""Rate Python packages for packaging best practices."""

import sys
from argparse import ArgumentParser, ArgumentTypeError, Namespace
from collections.abc import Iterable, Mapping, Sequence
from contextlib import suppress
from dataclasses import asdict
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any, Never, TypeVar, cast, overload

from pyroma import config as config_module
from pyroma import distributiondata, projectdata, pypidata, ratings, report, rules
from pyroma import metadata as metadata_types

_MIN_RATING = 1
_MAX_RATING = 10
_RELEASE_MIN_RATING = 8
# The distribution name differs from the import name; version lookups must
# use the former.
_DISTRIBUTION_NAME = "pyroma621"
_Namespace = TypeVar("_Namespace")


class UsageError(Exception):
    """Raised when invalid command-line arguments prevent rating."""


def _checker_version() -> str | None:
    with suppress(PackageNotFoundError):
        return package_version(_DISTRIBUTION_NAME)
    return None


def _version_meta() -> dict[str, object]:
    version = _checker_version()
    return {"pyroma": version} if version is not None else {}


def _requested_format(arguments: Sequence[str]) -> str | None:
    """Find the last exact format option, even after an invalid argument."""
    output_format = None
    for index, argument in enumerate(arguments):
        if argument == "--":
            break
        if argument.startswith("--format="):
            output_format = argument.partition("=")[2]
        elif argument == "--format":
            output_format = arguments[index + 1] if index + 1 < len(arguments) else None
    return output_format


class _ArgumentParser(ArgumentParser):
    """Keep argparse's text behavior and expose JSON usage failures."""

    _json_errors = False

    @overload
    def parse_args(self, args: Iterable[str] | None = None, namespace: None = None) -> Namespace: ...

    @overload
    def parse_args(self, args: Iterable[str] | None, namespace: _Namespace) -> _Namespace: ...

    @overload
    def parse_args(self, *, namespace: _Namespace) -> _Namespace: ...

    def parse_args(
        self, args: Iterable[str] | None = None, namespace: _Namespace | None = None
    ) -> Namespace | _Namespace:
        arguments = list(sys.argv[1:] if args is None else args)
        self._json_errors = _requested_format(arguments) == "json"
        # Python 3.14 introduced colored argparse diagnostics.
        cast("Any", self).color = not self._json_errors
        if namespace is None:
            return super().parse_args(arguments)
        return super().parse_args(arguments, namespace)

    def error(self, message: str) -> Never:
        if self._json_errors:
            raise UsageError(message)
        super().error(message)


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
        message = f"--min must be an integer between 1 and 10, not {arg!r}"
        raise ArgumentTypeError(message) from e
    if not _MIN_RATING <= f <= _MAX_RATING:
        message = f"--min must be an integer between 1 and 10, not {arg!r}"
        raise ArgumentTypeError(message)
    return f


def get_all_categories() -> "list[str]":
    """Return every skippable rule category."""
    return rules.categories()


def get_all_tests() -> "list[str]":
    """Return the names of every registered rating test."""
    return [x.__class__.__name__ for x in ratings.ALL_TESTS]


def _selectable_names() -> "set[str]":
    """Return every token a skip list accepts: a test class name or a category."""
    return set(get_all_tests()) | set(get_all_categories())


def _available_names_message() -> str:
    tests = ", ".join(get_all_tests())
    categories = ", ".join(get_all_categories())
    return f"Available tests: {tests}. Available categories: {categories}"


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

    selectable = _selectable_names()
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
    message = f"Invalid tests listed in --skip-tests: {arg!r}. {_available_names_message()}"
    raise ArgumentTypeError(message)


def _validate_config_skips(names: "list[str]") -> None:
    """Reject [tool.pyroma] skip-tests entries that name nothing, as the CLI does."""
    unknown = sorted(set(names) - _selectable_names())
    if unknown:
        listed = ", ".join(repr(name) for name in unknown)
        message = f"[tool.pyroma] skip-tests names unknown tests: {listed}. {_available_names_message()}"
        raise config_module.ConfigError(message)


def _build_parser() -> ArgumentParser:
    """Construct the command-line argument parser."""
    parser = _ArgumentParser()
    # argparse only grew the color attribute in Python 3.14.
    cast_parser = cast("Any", parser)
    cast_parser.color = True
    parser.add_argument(
        "--version", action="version", version=f"{_DISTRIBUTION_NAME} {_checker_version() or 'unknown'}"
    )
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
        help="Skip the named tests or whole categories, separated by commas or spaces",
    )
    parser.add_argument(
        "--index-url",
        dest="index_url",
        help="Base URL of a PyPI-compatible package index (default: https://pypi.org)",
    )
    # Each setting that [tool.pyroma] can fix has a flag in both directions,
    # so that the command line can always override the configuration.
    parser.add_argument(
        "--strict",
        dest="strict",
        action="store_true",
        default=None,
        help=(
            "Score the advisory checks too, instead of only reporting them. "
            "Combine with --skip-tests practice to score everything except opinions"
        ),
    )
    parser.add_argument(
        "--no-strict",
        dest="strict",
        action="store_false",
        default=None,
        help="Only report the advisory checks, overriding strict = true in [tool.pyroma]",
    )
    parser.add_argument(
        "--advisories",
        dest="show_advisories",
        action="store_true",
        default=None,
        help="Report advisory findings, overriding advisories = false in [tool.pyroma]",
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


def _resolve_config(
    args: Namespace, mode: str, *, provenance: config_module.ConfigSource | None = None
) -> config_module.Config:
    """Merge command-line arguments over the project's configuration.

    An explicitly supplied flag always wins; the configuration only fills in
    what the user did not ask for.
    """
    settings = config_module.Config()
    if args.use_config and mode == "directory":
        settings = config_module.from_directory(args.package, provenance=provenance)
        _validate_config_skips(settings.skip_tests)

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
    try:
        args = _build_parser().parse_args()
    except UsageError as error:
        print(report.format_json_error(error, _version_meta()))
        sys.exit(2)
    mode = _resolve_mode(args.mode, args.package)
    meta = _json_meta(mode, args.package)
    provenance = config_module.ConfigSource(enabled=args.use_config and mode == "directory")

    try:
        settings = _resolve_config(args, mode, provenance=provenance)
        meta["policy"] = {**asdict(settings), "skip_tests": sorted(set(settings.skip_tests))}
        meta["config"] = asdict(provenance)
        rating = run(
            mode,
            args.package,
            args.quiet,
            settings.skip_tests,
            args.index_url,
            args.output_format,
            strict=settings.strict,
            show_advisories=settings.show_advisories,
            report_meta=meta,
        )
    except (
        OSError,
        projectdata.BuildError,
        ratings.ConfigurationError,
        config_module.ConfigError,
        ValueError,
    ) as e:
        if isinstance(e, config_module.ConfigError):
            provenance.loaded = False
        if args.output_format == "json":
            meta["config"] = asdict(provenance)
            print(report.format_json_error(e, meta))
        else:
            print(f"Error: {e}", file=sys.stderr)
        sys.exit(3)
    if rating < settings.min_rating:
        sys.exit(2)
    sys.exit(0)


def _json_meta(mode: str, argument: str) -> dict[str, object]:
    return {"package": argument, "mode": mode, **_version_meta()}


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
    meta: Mapping[str, object],
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
    report_meta: Mapping[str, object] | None = None,
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
            report_meta if report_meta is not None else _json_meta(mode, argument),
            quiet=quiet,
            show_advisories=show_advisories,
        )
        return rated.rating
    finally:
        distributiondata.cleanup(data)
