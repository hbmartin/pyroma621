# This is a collection of "tests" done on the package data. The result of the
# tests is used to give the package a rating.
#
# Each test is a stateless object with a ``test(data)`` method that returns a
# ``TestResult``:
#
#     outcome:  True for pass, False for fail and None for not applicable
#               (meaning it will not be counted).
#     weight:   The relative importance of the test.
#               If the result has fatal set to True this is ignored.
#     fatal:    If set to True, the failure of this test will cause the
#               package to achieve the rating of 0, which is minimum.
#     message:  The problem description when the test failed.
#
# Tests may choose weight, fatality and message depending on the severity of
# the failure, but they must not mutate any state on the test instance: the
# instances in ALL_TESTS are shared.
import io
import os
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field

from docutils.core import publish_parts
from docutils.utils import SystemMessage
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from trove_classifiers import classifiers as CLASSIFIERS
from validate_pyproject import api as validate_pyproject

from pyroma._types import Metadata

if sys.version_info >= (3, 11):
    import tomllib
else:  # Python 3.10
    import tomli as tomllib

LEVELS = [
    "This cheese seems to contain no dairy products",
    "Vieux Bologne",
    "Limburger",
    "Gorgonzola",
    "Stilton",
    "Brie",
    "Comté",
    "Jarlsberg",
    "Philadelphia",
    "Cottage Cheese",
    "Your cheese is so fresh most people think it's a cream: Mascarpone",
]

MAX_RATING = 10


@dataclass(frozen=True)
class TestResult:
    """The outcome of running a single rating test against package data."""

    outcome: bool | None
    weight: int
    fatal: bool = False
    message: str = ""


@dataclass(frozen=True)
class Problem:
    """A single failed check, as reported to the user."""

    test: str
    message: str
    weight: int
    fatal: bool


@dataclass
class Rating:
    """The result of rating a package: a 0-10 score and a list of problems."""

    rating: int
    problems: list[Problem] = field(default_factory=list)

    @property
    def messages(self) -> list[str]:
        return [problem.message for problem in self.problems]

    @property
    def level(self) -> str:
        return LEVELS[self.rating]

    def as_dict(self) -> dict:
        return {
            "rating": self.rating,
            "max_rating": MAX_RATING,
            "level": self.level,
            "problems": [
                {
                    "test": problem.test,
                    "message": problem.message,
                    "weight": problem.weight,
                    "fatal": problem.fatal,
                }
                for problem in self.problems
            ],
        }


@dataclass(frozen=True)
class TestResult:
    """The outcome of running a single rating test."""

    outcome: bool | None
    weight: int
    fatal: bool
    message: str


@dataclass(frozen=True)
class Problem:
    """A single problem found while rating a package."""

    test: str
    message: str
    weight: int
    fatal: bool


@dataclass(frozen=True)
class RatedProject:
    """The full, structured result of rating a package."""

    name: str | None
    rating: int
    level: str
    problems: list[Problem]


class BaseTest:
    weight = 0
    fatal = False
    weight = 0

    def test(self, data: Metadata) -> TestResult:
        raise NotImplementedError

    def name(self) -> str:
        return self.__class__.__name__

    def _passed(self, weight: int | None = None, fatal: bool | None = None) -> TestResult:
        return TestResult(
            outcome=True,
            weight=self.weight if weight is None else weight,
            fatal=self.fatal if fatal is None else fatal,
        )

    def _failed(self, message: str, weight: int | None = None, fatal: bool | None = None) -> TestResult:
        return TestResult(
            outcome=False,
            weight=self.weight if weight is None else weight,
            fatal=self.fatal if fatal is None else fatal,
            message=message,
        )

    def _skipped(self) -> TestResult:
        return TestResult(outcome=None, weight=0)

    def test(self, data: Metadata) -> TestResult:
        raise NotImplementedError

    def _passed(self, weight: "int | None" = None) -> TestResult:
        return TestResult(True, self.weight if weight is None else weight, self.fatal, "")

    def _failed(self, message: str, weight: "int | None" = None, fatal: "bool | None" = None) -> TestResult:
        return TestResult(
            False,
            self.weight if weight is None else weight,
            self.fatal if fatal is None else fatal,
            message,
        )

    def _skipped(self) -> TestResult:
        return TestResult(None, 0, False, "")


class FieldTest(BaseTest):
    """Tests that a specific field is in the data and is not empty or False"""

    field = ""

    def test(self, data: Metadata) -> TestResult:
        if data.get(self.field):
            return self._passed()
        return self._failed(self._field_message())

    def _field_message(self) -> str:
        return (f"Your package does not have {self.field} data") + ("!" if self.fatal else ".")


class Name(FieldTest):
    fatal = True
    field = "name"


class Version(FieldTest):
    fatal = True
    field = "version"


class VersionIsString(BaseTest):
    weight = 50

    def test(self, data: Metadata) -> TestResult:
        # Check that the version is a string
        version = data.get("version")
        if isinstance(version, str):
            return self._passed()
        return self._failed("The version number should be a string.")


PEP386_RE = re.compile(
    r"""
    ^
    (?P<version>\d+\.\d+)          # minimum 'N.N'
    (?P<extraversion>(?:\.\d+)*)   # any number of extra '.N' segments
    (?:
        (?P<prerel>[abc]|rc)       # 'a'=alpha, 'b'=beta, 'c'=release candidate
                                   # 'rc'= alias for release candidate
        (?P<prerelversion>\d+(?:\.\d+)*)
    )?
    (?P<postdev>(\.post(?P<post>\d+))?(\.dev(?P<dev>\d+))?)?
    $""",
    re.VERBOSE | re.IGNORECASE,
)


PEP440_RE = re.compile(
    r"""^
    v?
    (?:
        (?:(?P<epoch>[0-9]+)!)?                           # epoch
        (?P<release>[0-9]+(?:\.[0-9]+)*)                  # release segment
        (?P<pre>                                          # pre-release
            [-_\.]?
            (?P<pre_l>(a|b|c|rc|alpha|beta|pre|preview))
            [-_\.]?
            (?P<pre_n>[0-9]+)?
        )?
        (?P<post>                                         # post release
            (?:-(?P<post_n1>[0-9]+))
            |
            (?:
                [-_\.]?
                (?P<post_l>post|rev|r)
                [-_\.]?
                (?P<post_n2>[0-9]+)?
            )
        )?
        (?P<dev>                                          # dev release
            [-_\.]?
            (?P<dev_l>dev)
            [-_\.]?
            (?P<dev_n>[0-9]+)?
        )?
    )
    (?:\+(?P<local>[a-z0-9]+(?:[-_\.][a-z0-9]+)*))?       # local version
$""",
    re.VERBOSE | re.IGNORECASE,
)


class PEPVersion(BaseTest):
    weight = 50

    def test(self, data: Metadata) -> TestResult:
        # Check that the version number complies to PEP-386:
        version = data.get("version")
        pep386 = PEP386_RE.search(str(version)) is not None
        # Note: a version matching the (older, stricter) PEP-386 form is
        # counted with a lower weight, whether it passes or fails. This
        # preserves the historical scoring of this test.
        weight = 10 if pep386 else self.weight
        if PEP440_RE.search(str(version)) is not None:
            return self._passed(weight=weight)
        if pep386:
            return self._failed(
                "The package's version number complies only with PEP-386 and not PEP-440.",
                weight=weight,
            )
        return self._failed(
            "The package's version number does not comply with PEP-386 or PEP-440.",
            weight=weight,
        )


class Summary(BaseTest):
    weight = 100

    def test(self, data: Metadata) -> TestResult:
        summary = data.get("summary")
        if not summary:
            # No summary at all. That's fatal.
            return self._failed("The package had no Summary!", fatal=True)
        if len(summary) <= 10:
            return self._failed("The package's Summary should be longer than 10 characters.")
        return self._passed()


class Description(BaseTest):
    weight = 50

    def test(self, data: Metadata) -> TestResult:
        description = data.get("description", "")
        if not isinstance(description, str):
            description = ""
        if len(description) > 100:
            return self._passed()
        return self._failed("The package's Description is quite short.")


class Classifiers(FieldTest):
    weight = 100
    field = "classifier"


class ClassifierVerification(BaseTest):
    weight = 20

    def test(self, data: Metadata) -> TestResult:
        incorrect = []
        classifiers = data.get("classifier", [])
        for classifier in classifiers:
            if classifier not in CLASSIFIERS and not classifier.startswith("Private :: "):
                incorrect.append(classifier)
        if incorrect:
            err = "\n".join(incorrect)
            return self._failed(
                f"Some of your classifiers are not standard classifiers:\n{err}\n"
                f"If you have custom classifiers, they should start with 'Private :: '\n"
                f"You can find the list of standard classifiers here: https://pypi.org/classifiers/"
            )
        return self._passed()


class PythonClassifierVersion(BaseTest):
    def test(self, data: Metadata) -> TestResult:
        major_version_specified = False

        classifiers = data.get("classifier", [])
        for classifier in classifiers:
            parts = [p.strip() for p in classifier.split("::")]
            if parts[0] == "Programming Language" and parts[1] == "Python":
                if len(parts) == 2:
                    # Specified Python, but no version.
                    continue
                version = parts[2]
                try:
                    float(version)
                except ValueError:
                    # Not a proper Python version
                    continue
                try:
                    int(version)
                except ValueError:
                    # It's a valid float, but not a valid int. Hence it's
                    # something like "2.7" or "3.3" but not just "2" or "3".
                    # This is a good specification, and we only need one.
                    return self._passed(weight=100)

                # It's a valid int, meaning it specified "2" or "3".
                major_version_specified = True

        # There was some sort of failure:
        if major_version_specified:
            # Python 2 or 3 was specified but no more detail than that:
            return self._failed(
                "The classifiers should specify what minor versions of "
                "Python you support as well as what major version."
                "You can find the list of standard classifiers here: "
                "https://pypi.org/classifiers/",
                weight=25,
            )
        # No Python version specified at all:
        return self._failed(
            "The classifiers should specify what Python versions you support."
            "You can find the list of standard classifiers here: "
            "https://pypi.org/classifiers/",
            weight=100,
        )


class PythonRequiresVersion(BaseTest):
    weight = 100

    _message = "You should specify what Python versions you support with the 'Requires-Python' metadata."

    def test(self, data: Metadata) -> TestResult:
        # https://github.com/regebro/pyroma/pull/83#discussion_r955611236
        python_requires = data.get("requires-python", None)

        message = "You should specify what Python versions you support with the 'Requires-Python' metadata."
        if not python_requires:
            return self._failed(self._message)

        try:
            SpecifierSet(python_requires)
        except InvalidSpecifier:
            return self._failed(self._message)

        return self._passed()


class Keywords(FieldTest):
    weight = 20
    field = "keywords"


class Author(FieldTest):
    weight = 100
    field = "author"

    def test(self, data: Metadata) -> TestResult:
        """Check if author-email field contains author name."""
        email = data.get("author-email")
        # Pass if author name in email, e.g. "Author Name <author@example.com>"
        if email and "<" in email:
            return self._passed()
        return super().test(data)


class AuthorEmail(FieldTest):
    weight = 100
    field = "author-email"


# Well-known Project-URL labels (and their aliases) from the well-known
# project URLs specification (PEP 753), keyed by normalized label.
WELL_KNOWN_URL_LABELS = {
    "homepage": "homepage",
    "source": "source",
    "repository": "source",
    "sourcecode": "source",
    "github": "source",
    "download": "download",
    "changelog": "changelog",
    "changes": "changelog",
    "whatsnew": "changelog",
    "history": "changelog",
    "releasenotes": "releasenotes",
    "documentation": "documentation",
    "docs": "documentation",
    "issues": "issues",
    "bugs": "issues",
    "issue": "issues",
    "tracker": "issues",
    "issuetracker": "issues",
    "bugtracker": "issues",
    "funding": "funding",
    "sponsor": "funding",
    "donate": "funding",
    "donation": "funding",
}


def _normalize_url_label(label: str) -> str:
    # Normalization from the well-known project URLs specification:
    # remove punctuation and whitespace, lowercase.
    return "".join(c for c in str(label).lower() if not c.isspace() and c not in string.punctuation)


def _get_project_urls(data: Metadata) -> "list[tuple[str, str]]":
    """Return the project URLs as a list of (label, url) tuples.

    Handles both the Core Metadata form ("label, https://url") and the
    PyPI JSON API form ({label: url}).
    """
    urls = data.get("project-url")
    if not urls:
        return []
    if isinstance(urls, dict):
        return list(urls.items())
    if isinstance(urls, str):
        urls = [urls]
    result = []
    for entry in urls:
        label, _, url = str(entry).partition(",")
        result.append((label.strip(), url.strip()))
    return result


class Url(BaseTest):
    weight = 20

    def test(self, data: Metadata) -> TestResult:
        if bool(data.get("home-page")) or bool(data.get("project-url")):
            return self._passed()
        return self._failed(
            "Your package should have a 'url' field with a link to the "
            "project home page, or a 'project_urls' field, with a "
            "dictionary of links, or both."
        )


class Licensing(BaseTest):
    weight = 50

    def test(self, data: Metadata) -> TestResult:
        license = data.get("license")
        license_expression = data.get("license-expression")
        classifiers = data.get("classifier", [])
        has_license_classifier = any(c.startswith("License") for c in classifiers)

        if not license and not license_expression and not has_license_classifier:
            return self._failed(
                "You should specify a license for your package with the 'License-Expression' field. "
                "See https://packaging.python.org/en/latest/specifications/core-metadata/#license-expression "
                "for more information."
            )

        if license and license_expression:
            return self._failed(
                "Specifying both a License and a License-Expression is ambiguous, deprecated, "
                "and may be rejected by package indices."
            )

        if license_expression and has_license_classifier:
            return self._failed(
                "Specifying both a License-Expression and license classifiers is ambiguous, deprecated, "
                "and may be rejected by package indices."
            )

        if has_license_classifier:
            return self._failed("Using license classifiers is deprecated in favour of the license-expression field.")

        return self._passed()


class DevStatusClassifier(BaseTest):
    weight = 20

    def test(self, data: Metadata) -> TestResult:
        classifiers = data.get("classifier", [])
        for classifier in classifiers:
            parts = [p.strip() for p in classifier.split("::")]
            if parts[0] == "Development Status":
                # development status classifier exists
                return self._passed()
        return self._failed(
            "Specifying a development status in the classifiers gives users a "
            "hint of how stable your software is. See "
            "https://pypi.org/classifiers/"
        )


class SDist(BaseTest):
    def test(self, data: Metadata) -> TestResult:
        if "_has_sdist" not in data:
            # We aren't checking on PyPI
            return self._skipped()

        if data["_has_sdist"]:
            return self._passed(weight=100)
        return self._failed(
            "You have no source distribution on the Cheeseshop. "
            "Uploading the source distribution to the Cheeseshop ensures "
            "maximum availability of your package.",
            weight=100,
        )


class ValidREST(BaseTest):
    weight = 50

    def test(self, data: Metadata) -> TestResult:
        content_type = data.get("description-content-type", None)
        if content_type in ("text/plain", "text/markdown"):
            # These can't fail. Markdown will just assume everything
            # it doesn't understand is plain text.
            return self._passed()

        # This should be ReStructuredText
        source = data.get("description", "")
        stream = io.StringIO()
        settings = {"warning_stream": stream}

        message = ""
        try:
            publish_parts(source=source, writer="html4css1", settings_overrides=settings)
        except SystemMessage:
            # The errors also show up in the warning stream, handled below.
            pass
        errors = stream.getvalue().strip()
        if not errors:
            return self._passed()

        return self._failed("Your Description is not valid ReST: " + "\n" + errors)


class BusFactor(BaseTest):
    def test(self, data: Metadata) -> TestResult:
        if "_owners" not in data:
            return self._skipped()

        message = "You should have three or more owners of the project on PyPI."
        if len(data.get("_owners", [])) == 1:
            return self._failed(message, weight=100)

        if len(data.get("_owners", [])) == 2:
            return self._failed(message, weight=50)

        # Three or more, that's good.
        return self._passed(weight=100)


class MissingBuildSystem(BaseTest):
    def test(self, data: Metadata) -> TestResult:
        if "_missing_build_system" in data:
            # The build system tests give only negative weight, as they are effectively required
            # for a working package, so passing them shouldn't give you a better rating,
            # but failing them should give you a worse rating.
            return self._failed(
                "You seem to neither have a setup.py, nor a pyproject.toml, only setup.cfg.\n"
                "This makes it unclear how your project should be built, and some packaging tools may fail."
                "See https://packaging.python.org for more information on how to package your project.",
                weight=400,
            )

        return self._passed(weight=0)


class MissingPyProjectToml(BaseTest):
    def test(self, data: Metadata) -> TestResult:
        # This may not yet be required, but it will be in the future, so we
        # give it a negative rating when it fails, but not a positive rating
        # when it succeeds.
        if "_missing_build_system" in data or "_missing_pyproject_toml" in data:
            return self._failed(
                "Your project does not have a pyproject.toml file, which is highly recommended.\n"
                "You probably want to create one with the following configuration:\n\n"
                "    [build-system]\n"
                '    requires = ["setuptools>=42"]\n'
                '    build-backend = "setuptools.build_meta"\n'
                "See https://packaging.python.org for more information on how to package your project.",
                weight=100,
            )
        return self._passed(weight=0)


class PyprojectTomlValid(BaseTest):
    def test(self, data: Metadata) -> TestResult:
        # The build system tests give only negative weight, as they are effectively required
        # for a working package, so passing them shouldn't give you a better rating,
        # but failing them should give you a worse rating.

        # Only test if we have a path and pyproject.toml exists
        if "_path" not in data:
            return self._skipped()

        if "_missing_pyproject_toml" in data or "_missing_build_system" in data:
            # No pyproject.toml to validate, skip this test
            return self._skipped()

        pyproject_path = os.path.join(data["_path"], "pyproject.toml")

        try:
            with open(pyproject_path, "rb") as pyproject_file:
                config = tomllib.load(pyproject_file)
            validate_pyproject.Validator()(config)
        except Exception as e:
            return self._failed(
                f"Your pyproject.toml is invalid: {e}\n"
                "See https://packaging.python.org for more information on how to package your project.",
                weight=100,
            )
        return self._passed(weight=0)


class DeprecatedMetadataFields(BaseTest):
    weight = 50

    _deprecated = {
        "home-page": ("project-url", "1.2"),
        "download-url": ("project-url", "1.2"),
        "requires": ("requires-dist", "1.2"),
        "provides": ("provides-dist", "1.2"),
        "obsoletes": ("obsoletes-dist", "1.2"),
        "license": ("license-expression", "2.4"),
    }

    def _version_at_least(self, data: Metadata, minimum: str) -> bool:
        metadata_version = data.get("metadata-version")
        if not metadata_version:
            return True

        try:
            current = tuple(int(p) for p in str(metadata_version).split("."))
            required = tuple(int(p) for p in str(minimum).split("."))
        except ValueError:
            return True

        return current >= required

    def test(self, data: Metadata) -> TestResult:
        warnings = []

        for deprecated, (replacement, deprecated_since) in self._deprecated.items():
            if not self._version_at_least(data, deprecated_since):
                continue

            if data.get(deprecated) and not data.get(replacement):
                warnings.append(f"The metadata field '{deprecated}' is deprecated; use '{replacement}' instead.")

        if warnings:
            return self._failed("\n".join(warnings))
        return self._passed()


BUILD_SYSTEM_TESTS: list[BaseTest] = [MissingBuildSystem(), MissingPyProjectToml(), PyprojectTomlValid()]

ALL_TESTS: list[BaseTest] = [
    MissingBuildSystem(),
    MissingPyProjectToml(),
    PyprojectTomlValid(),
    PyProjectProjectTable(),
    Name(),
    NameFormat(),
    MetadataVersion(),
    Version(),
    VersionIsString(),
    PEPVersion(),
    Summary(),
    Description(),
    Classifiers(),
    ClassifierVerification(),
    PythonClassifierVersion(),
    PythonRequiresVersion(),
    Keywords(),
    Author(),
    AuthorEmail(),
    Url(),
    Licensing(),
    DescriptionContentType(),
    DependencySpecifiers(),
    SDist(),
    ValidREST(),
    BusFactor(),
    DevStatusClassifier(),
    DeprecatedMetadataFields(),
]


try:
    import check_manifest

    class CheckManifest(BaseTest):
        def test(self, data: Metadata) -> TestResult:
            if "_path" not in data:
                return self._skipped()

            try:
                ok = check_manifest.check_manifest(data["_path"])
            except check_manifest.Failure:
                # Most likely this means check-manifest didn't find any
                # package configuration, which is the same failure as
                # MissingBuildSystem, so this is double errors, but
                # it does mean your setup is completely broken, so...
                ok = False
            if ok:
                return self._passed(weight=200)
            return self._failed("Check-manifest returned errors", weight=200)

    ALL_TESTS.append(CheckManifest())

except ImportError:
    pass


def rate(data: Metadata, skip_tests: Sequence[str] | None = None) -> Rating:
    problems: list[Problem] = []
    good = 0
    bad = 0
    fatality = False
    test_list = ALL_TESTS
    name = data.get("name")

    if len([key for key in data if not key.startswith("_")]) == 0:
        if "_no_config_found" in data:
            # Are you in the correct directory?:
            return Rating(
                0,
                [
                    Problem(
                        test="NoPackageData",
                        message="I couldn't find any package data. Are checking the correct directory or file?",
                        weight=0,
                        fatal=True,
                    )
                ],
            )

    if "_wheel_build_failed" in data:
        problems.append(
            Problem(
                test="WheelBuildFailed",
                message=(
                    "Pyroma failed to build your packages wheel metadata, which indicates an error with "
                    "your build configuration, like you not having a pyproject.toml file, or it being faulty.\n"
                    "Running `python -m build` in your package directory may give more information."
                ),
                weight=0,
                fatal=True,
            )
        )
        fatality = True
        test_list = BUILD_SYSTEM_TESTS

    if skip_tests is None:
        skip_tests = []

    for test in test_list:
        if test.name() in skip_tests:
            continue
        result = test.test(data)
        if result.outcome is False:
            problems.append(
                Problem(
                    test=test.name(),
                    message=result.message,
                    weight=result.weight,
                    fatal=result.fatal,
                )
            )
            if result.fatal:
                fatality = True
            else:
                bad += result.weight
        elif result.outcome is True:
            if not result.fatal:
                good += result.weight
        # If the outcome is None, it's ignored.

    if fatality:
        # A fatal test failed. That means we give a 0 rating:
        return Rating(0, problems)
    if good + bad == 0:
        # Nothing was counted at all (everything skipped); nothing failed either.
        return Rating(MAX_RATING, problems)
    # Multiply good by 9, and add 1 to get a rating between
    # 1: All non-fatal tests failed.
    # 10: All tests succeeded.
    return Rating((good * 9) // (good + bad) + 1, problems)
