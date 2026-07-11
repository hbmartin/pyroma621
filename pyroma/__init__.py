import os
import sys
from argparse import ArgumentParser, ArgumentTypeError
from collections.abc import Sequence

from pyroma import distributiondata, projectdata, pypidata, ratings
from pyroma._types import Metadata
from pyroma.report import get_reporter

logger = logging.getLogger("pyroma")


def _configure_logging(suppress: bool) -> None:
    """Send pyroma's diagnostic messages to stdout, or suppress them.

    This only configures the "pyroma" logger, never the root logger, so
    library users' logging configuration is left alone.
    """
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.CRITICAL + 1 if suppress else logging.DEBUG)


def zester(data: dict) -> None:
    main_files = set(os.listdir(data["workingdir"]))
    config_files = {"setup.py", "setup.cfg", "pyproject.toml"}

    # If there are no standard Python config files in the main files
    # it's likely not a Python project, so just return.
    if not config_files & main_files:
        return

    from zest.releaser.utils import ask

    if ask("Run pyroma on the package before tagging?"):
        rating = run("directory", os.path.abspath(data["workingdir"]), skip_tests=["CheckManifest"])
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


def get_all_tests() -> list[str]:
    return [x.name() for x in ratings.ALL_TESTS]


def parse_tests(arg: str) -> list[str] | None:
    if not arg:
        return None

    # Split on spaces, commas and semicolons
    parts = [arg]
    for sep in " ,;":
        skips: list[str] = []
        for t in parts:
            skips.extend(t.split(sep))
        parts = skips

    tests = get_all_tests()
    for skip in parts:
        if skip not in tests:
            # Invalid test mentioned, fail and print valid tests
            return None

    return parts


def skip_tests(arg: str) -> list[str]:
    test_to_skip = parse_tests(arg)
    if test_to_skip:
        return test_to_skip

    # It returned None, so there was an invalid test mentioned, or none at all
    tests = ", ".join(get_all_tests())
    message = f"Invalid tests listed. Available tests: {tests}"
    raise ArgumentTypeError(message)


def main() -> None:
    parser = ArgumentParser()
    parser.color = True  # type: ignore[attr-defined]  # Enables color output on Python 3.14+
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
    parser.add_argument(
        "--format",
        dest="output_format",
        choices=("text", "json"),
        default="text",
        help="Output format: classic human-readable text, or a JSON document (default: text)",
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


def _collect_data(mode: str, argument: str, index_url: str | None) -> Metadata:
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
    skip_tests: Sequence[str] | None = None,
    index_url: str | None = None,
    output_format: str = "text",
) -> int:
    # Suppress diagnostics when quiet, and in JSON mode, where stdout
    # must stay machine-readable.
    _configure_logging(suppress=quiet or output_format == "json")

    reporter = get_reporter(output_format, quiet=quiet)
    reporter.start(str(argument))

    data = _collect_data(mode, argument, index_url)
    reporter.found(data.get("name", "nothing"))

    rating = ratings.rate(data, skip_tests)
    reporter.finish(rating)

    return rating.rating
