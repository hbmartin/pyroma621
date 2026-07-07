# This is a collection of "tests" done on the package data. The result of the
# tests is used to give the package a rating.
#
# Each test returns a TestResult from its test() method:
#
#     outcome:  True for pass, False for fail and None for not applicable
#               (meaning it will not be counted).
#     weight:   The relative importance of the test.
#               If the test is fatal this is ignored.
#     fatal:    If True, the failure of this test will cause the
#               package to achieve the rating of 0, which is minimum.
#     message:  The message to show the user on failure.
#
# Tests are stateless: they must not store per-run state on the (shared)
# test instances, but compute everything locally and return it in the
# TestResult.
import io
import os
import re
from dataclasses import dataclass

from docutils.core import publish_parts
from docutils.utils import SystemMessage
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from setuptools.config.pyprojecttoml import read_configuration
from trove_classifiers import classifiers as CLASSIFIERS

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

    def test(self, data):
        raise NotImplementedError

    def _passed(self, weight=None):
        return TestResult(True, self.weight if weight is None else weight, self.fatal, "")

    def _failed(self, message, weight=None, fatal=None):
        return TestResult(
            False,
            self.weight if weight is None else weight,
            self.fatal if fatal is None else fatal,
            message,
        )

    def _skipped(self):
        return TestResult(None, 0, False, "")


class FieldTest(BaseTest):
    """Tests that a specific field is in the data and is not empty or False"""

    def test(self, data):
        if bool(data.get(self.field)):
            return self._passed()
        return self._failed(f"Your package does not have {self.field} data" + (self.fatal and "!" or "."))


class Name(FieldTest):
    fatal = True
    field = "name"


class Version(FieldTest):
    fatal = True
    field = "version"


class VersionIsString(BaseTest):
    weight = 50

    def test(self, data):
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

    def test(self, data):
        # Check that the version number complies to PEP-386:
        version = data.get("version")
        pep386 = PEP386_RE.search(str(version)) is not None
        pep440 = PEP440_RE.search(str(version)) is not None
        if pep440:
            return self._passed(weight=10 if pep386 else 50)
        if pep386:
            return self._failed(
                "The package's version number complies only with PEP-386 and not PEP-440.",
                weight=10,
            )
        return self._failed("The package's version number does not comply with PEP-386 or PEP-440.")


class Summary(BaseTest):
    weight = 100

    def test(self, data):
        summary = data.get("summary")
        if not summary:
            # No summary at all. That's fatal.
            return self._failed("The package had no Summary!", fatal=True)
        if len(summary) <= 10:
            return self._failed("The package's Summary should be longer than 10 characters.")
        return self._passed()


class Description(BaseTest):
    weight = 50

    def test(self, data):
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

    def test(self, data):
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
    def test(self, data):
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

    def test(self, data):
        # https://github.com/regebro/pyroma/pull/83#discussion_r955611236
        python_requires = data.get("requires-python", None)

        message = "You should specify what Python versions you support with the 'Requires-Python' metadata."
        if not python_requires:
            return self._failed(message)

        try:
            SpecifierSet(python_requires)
        except InvalidSpecifier:
            return self._failed(message)

        return self._passed()


class Keywords(FieldTest):
    weight = 20
    field = "keywords"


class Author(FieldTest):
    weight = 100
    field = "author"

    def test(self, data):
        """Check if author-email field contains author name."""
        email = data.get("author-email")
        # Pass if author name in email, e.g. "Author Name <author@example.com>"
        if email and "<" in email:
            return self._passed()
        return super().test(data)


class AuthorEmail(FieldTest):
    weight = 100
    field = "author-email"


class Url(BaseTest):
    weight = 20

    def test(self, data):
        if bool(data.get("home-page")) or bool(data.get("project-url")):
            return self._passed()
        return self._failed(
            "Your package should have a 'url' field with a link to the "
            "project home page, or a 'project_urls' field, with a "
            "dictionary of links, or both."
        )


class Licensing(BaseTest):
    weight = 50

    def test(self, data):
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

    def test(self, data):
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
    def test(self, data):
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

    def test(self, data):
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
        except SystemMessage as e:
            message = e.args[0]
        errors = stream.getvalue().strip()
        if not errors:
            return self._passed()

        message = "\n" + errors
        return self._failed("Your Description is not valid ReST: " + message)


class BusFactor(BaseTest):
    def test(self, data):
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
    def test(self, data):
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
    def test(self, data):
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
    def test(self, data):
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
            read_configuration(pyproject_path)
            return self._passed(weight=0)
        except Exception as e:
            return self._failed(
                f"Your pyproject.toml is invalid: {e}\n"
                "See https://packaging.python.org for more information on how to package your project.",
                weight=100,
            )


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

    def _version_at_least(self, data, minimum):
        metadata_version = data.get("metadata-version")
        if not metadata_version:
            return True

        try:
            current = tuple(int(p) for p in str(metadata_version).split("."))
            required = tuple(int(p) for p in str(minimum).split("."))
        except ValueError:
            return True

        return current >= required

    def test(self, data):
        warnings = []

        for deprecated, (replacement, deprecated_since) in self._deprecated.items():
            if not self._version_at_least(data, deprecated_since):
                continue

            if data.get(deprecated) and not data.get(replacement):
                warnings.append(f"The metadata field '{deprecated}' is deprecated; use '{replacement}' instead.")

        if warnings:
            return self._failed("\n".join(warnings))
        return self._passed()


BUILD_SYSTEM_TESTS = [MissingBuildSystem(), MissingPyProjectToml(), PyprojectTomlValid()]

ALL_TESTS = [
    MissingBuildSystem(),
    MissingPyProjectToml(),
    PyprojectTomlValid(),
    Name(),
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
    SDist(),
    ValidREST(),
    BusFactor(),
    DevStatusClassifier(),
    DeprecatedMetadataFields(),
]


try:
    import check_manifest

    class CheckManifest(BaseTest):
        def test(self, data):
            if "_path" not in data:
                return self._skipped()

            # A quiet UI, so check-manifest doesn't write to stdout and
            # corrupt machine-readable output.
            ui = check_manifest.UI(verbosity=0)
            try:
                if check_manifest.check_manifest(data["_path"], ui=ui):
                    return self._passed(weight=200)
            except check_manifest.Failure:
                # Most likely this means check-manifest didn't find any
                # package configuration, which is the same failure as
                # MissingBuildSystem, so this is double errors, but
                # it does mean your setup is completely broken, so...
                pass
            return self._failed("Check-manifest returned errors", weight=200)

    ALL_TESTS.append(CheckManifest())

except ImportError:
    pass


def rate_project(data, skip_tests=None):
    """Rate a package, returning a structured RatedProject result."""
    problems = []
    good = 0
    bad = 0
    fatality = False
    test_list = ALL_TESTS
    name = data.get("name")

    if len([key for key in data if not key.startswith("_")]) == 0:
        if "_no_config_found" in data:
            # Are you in the correct directory?:
            return RatedProject(
                name=name,
                rating=0,
                level=LEVELS[0],
                problems=[
                    Problem(
                        test="NoConfigFound",
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
        test_name = test.__class__.__name__
        if test_name in skip_tests:
            continue
        result = test.test(data)
        if result.outcome is False:
            problems.append(Problem(test=test_name, message=result.message, weight=result.weight, fatal=result.fatal))
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
        rating = 0
    else:
        # Multiply good by 9, and add 1 to get a rating between
        # 1: All non-fatal tests failed.
        # 10: All tests succeeded.
        rating = (good * 9) // (good + bad) + 1

    return RatedProject(name=name, rating=rating, level=LEVELS[rating], problems=problems)


def rate(data, skip_tests=None):
    """Rate a package, returning a (rating, [problem messages]) tuple.

    This is the backwards-compatible API; rate_project() returns a
    structured result.
    """
    rated = rate_project(data, skip_tests)
    return rated.rating, [problem.message for problem in rated.problems]
