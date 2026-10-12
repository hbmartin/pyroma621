"""Rate Python packages for packaging best practices."""

import sys
from argparse import ArgumentParser, ArgumentTypeError, Namespace
from collections.abc import Generator, Iterable, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import asdict
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any, Never, TypeVar, cast, overload

from pyroma import _selectors, distributiondata, projectdata, pypidata, ratings, report
from pyroma import config as config_module
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


def _requested_format(arguments: Sequence[str], option_strings: Sequence[str], *, allow_abbrev: bool) -> str | None:
    """Find the last unambiguous format option, even after an invalid argument."""
    output_format = None
    for index, argument in enumerate(arguments):
        if argument == "--":
            break
        option, separator, value = argument.partition("=")
        if not option.startswith("--"):
            continue
        matches = [option] if option in option_strings else [name for name in option_strings if name.startswith(option)]
        if option == "--format" or (allow_abbrev and matches == ["--format"]):
            output_format = value if separator else (arguments[index + 1] if index + 1 < len(arguments) else None)
    return output_format


class _ArgumentParser(ArgumentParser):
    """Keep argparse's text behavior and expose JSON usage failures."""

    _json_errors = False
    _parse_depth = 0

    @contextmanager
    def _diagnostics(self, args: Iterable[str] | None) -> Generator[list[str], None, None]:
        """Initialize each outer parse without resetting nested intermixed passes."""
        arguments = list(sys.argv[1:] if args is None else args)
        if not self._parse_depth:
            self._json_errors = (
                _requested_format(arguments, list(self._option_string_actions), allow_abbrev=self.allow_abbrev)
                == "json"
            )
            # Python 3.14 introduced colored argparse diagnostics.
            cast("Any", self).color = not self._json_errors
        self._parse_depth += 1
        try:
            yield arguments
        finally:
            self._parse_depth -= 1

    @overload
    def parse_known_args(
        self, args: Iterable[str] | None = None, namespace: None = None
    ) -> tuple[Namespace, list[str]]: ...

    @overload
    def parse_known_args(self, args: Iterable[str] | None, namespace: _Namespace) -> tuple[_Namespace, list[str]]: ...

    @overload
    def parse_known_args(self, *, namespace: _Namespace) -> tuple[_Namespace, list[str]]: ...

    def parse_known_args(
        self, args: Iterable[str] | None = None, namespace: _Namespace | None = None
    ) -> tuple[Namespace | _Namespace, list[str]]:
        with self._diagnostics(args) as arguments:
            if namespace is None:
                return super().parse_known_args(arguments)
            return super().parse_known_args(arguments, namespace)

    @overload
    def parse_known_intermixed_args(
        self, args: Iterable[str] | None = None, namespace: None = None
    ) -> tuple[Namespace, list[str]]: ...

    @overload
    def parse_known_intermixed_args(
        self, args: Iterable[str] | None, namespace: _Namespace
    ) -> tuple[_Namespace, list[str]]: ...

    @overload
    def parse_known_intermixed_args(self, *, namespace: _Namespace) -> tuple[_Namespace, list[str]]: ...

    def parse_known_intermixed_args(
        self, args: Iterable[str] | None = None, namespace: _Namespace | None = None
    ) -> tuple[Namespace | _Namespace, list[str]]:
        with self._diagnostics(args) as arguments:
            if namespace is None:
                return super().parse_known_intermixed_args(arguments)
            return super().parse_known_intermixed_args(arguments, namespace)

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
    message = f"--min must be an integer between 1 and 10, not {arg!r}"
    try:
        f = int(arg)
    except ValueError as e:
        raise ArgumentTypeError(message) from e
    if not _MIN_RATING <= f <= _MAX_RATING:
        raise ArgumentTypeError(message)
    return f


def get_all_categories() -> "list[str]":
    """Return every skippable rule category."""
    return _selectors.category_names()


def get_all_tests() -> "list[str]":
    """Return the names of every registered rating test."""
    return _selectors.test_names()


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

    if _selectors.unknown_names(names):
        return None

    return names


def skip_tests(arg: str) -> "list[str]":
    """Parse skipped tests or raise an argparse validation error."""
    test_to_skip = parse_tests(arg)
    if test_to_skip:
        return test_to_skip

    # It returned None, so there was an invalid test mentioned, or none at all
    message = f"Invalid tests listed in --skip-tests: {arg!r}. {_selectors.available_names_message()}"
    raise ArgumentTypeError(message)


def _build_parser(*, version_meta: Mapping[str, object] | None = None) -> ArgumentParser:
    """Construct the command-line argument parser."""
    parser = _ArgumentParser()
    version_meta = _version_meta() if version_meta is None else version_meta
    parser.add_argument(
        "--version", action="version", version=f"{_DISTRIBUTION_NAME} {version_meta.get('pyroma', 'unknown')}"
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


def _config_enabled(args: Namespace, mode: str) -> bool:
    return bool(args.use_config and mode == "directory")


def _resolve_config(
    args: Namespace, mode: str, *, provenance: config_module.ConfigSource | None = None
) -> config_module.Config:
    """Merge command-line arguments over the project's configuration.

    An explicitly supplied flag always wins; the configuration only fills in
    what the user did not ask for.
    """
    settings = config_module.Config()
    if _config_enabled(args, mode):
        settings = config_module.from_directory(args.package, provenance=provenance)

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
    version_meta = _version_meta()
    try:
        args = _build_parser(version_meta=version_meta).parse_args()
    except UsageError as error:
        print(report.format_json_error(error, version_meta))
        sys.exit(2)
    mode = _resolve_mode(args.mode, args.package)
    meta = _json_meta(mode, args.package, version_meta=version_meta)
    provenance = config_module.ConfigSource(enabled=_config_enabled(args, mode))

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
        if args.output_format == "json":
            meta["config"] = asdict(provenance)
            print(report.format_json_error(e, meta))
        else:
            print(f"Error: {e}", file=sys.stderr)
        sys.exit(3)
    if rating < settings.min_rating:
        sys.exit(2)
    sys.exit(0)


def _json_meta(mode: str, argument: str, *, version_meta: Mapping[str, object] | None = None) -> dict[str, object]:
    version_meta = _version_meta() if version_meta is None else version_meta
    return {"package": argument, "mode": mode, **version_meta}


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
