"""Rate normalized package metadata against packaging best practices.

Each rating test returns a :class:`TestResult` from its ``test`` method.

``outcome`` is true for a pass, false for a failure, and ``None`` when the
test does not apply. Tests are stateless and must not store per-run state on
the shared instances.
"""

import datetime
import io
import re
import string
from contextlib import suppress
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any, ClassVar, cast
from urllib.parse import urlsplit

import trove_classifiers
from docutils.core import publish_parts
from docutils.utils import SystemMessage
from packaging.licenses import InvalidLicenseExpression, canonicalize_license_expression
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion
from packaging.version import Version as PackagingVersion
from validate_pyproject import api as pyproject_api
from validate_pyproject import errors as pyproject_errors

from pyroma.metadata import Metadata, normalize
from pyroma.rules import Category

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

_MIN_SUMMARY_LENGTH = 10
_MIN_DESCRIPTION_LENGTH = 100
_PYTHON_CLASSIFIER_PREFIX_LENGTH = 2
_MAX_PROJECT_URL_LABEL_LENGTH = 32
_MIN_BUS_FACTOR = 3
_LOW_BUS_FACTOR = 2


class ConfigurationError(Exception):
    """Raised when pyroma is configured so that no rating can be calculated."""


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
    # Defaulted: several call sites construct a Problem with four arguments,
    # and the JSON reporter emits every field, so this has to be additive.
    category: str = Category.INTERNAL


@dataclass(frozen=True)
class RatedProject:
    """The full, structured result of rating a package."""

    name: str | None
    rating: int
    level: str
    problems: list[Problem]
    # Findings from advisory tests. These are reported but deliberately kept
    # out of the rating arithmetic, so that adding a new advisory test never
    # changes an existing package's score. Running in strict mode scores them
    # like any other test, and leaves this list empty.
    advisories: list[Problem] = field(default_factory=list)


class BaseTest:
    """Base protocol and result helpers for a metadata rating test."""

    weight = 0
    fatal = False
    # Advisory tests are reported but never scored, unless strict mode is on.
    advisory = False
    # What kind of problem this test reports. Used for reporting and for
    # skipping a whole class of checks with --skip-tests.
    category: str = Category.METADATA

    def test(self, data: Metadata) -> TestResult:
        """Evaluate metadata and return this test's outcome."""
        raise NotImplementedError

    def _passed(self, weight: "int | None" = None) -> TestResult:
        return TestResult(
            outcome=True,
            weight=self.weight if weight is None else weight,
            fatal=self.fatal,
            message="",
        )

    def _failed(self, message: str, weight: "int | None" = None, fatal: "bool | None" = None) -> TestResult:
        return TestResult(
            outcome=False,
            weight=self.weight if weight is None else weight,
            fatal=self.fatal if fatal is None else fatal,
            message=message,
        )

    def _skipped(self) -> TestResult:
        return TestResult(outcome=None, weight=0, fatal=False, message="")


# Shared helpers for the checks below. Every one of them is total: the
# property test in pyroma/tests.py feeds arbitrary metadata through every
# check, so none of these may raise on a None, an int, or a string where a
# list was expected.


def _as_list(value: object) -> "list[str]":
    """Coerce a metadata field that may be a string, a list, or absent."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return []


def _parse_specifier_set(value: object) -> "SpecifierSet | None":
    """Parse a version specifier set, returning None when it is unusable."""
    if not value or not isinstance(value, str):
        return None
    try:
        return SpecifierSet(value)
    except InvalidSpecifier:
        return None


# "==" and "~=" both cap the version from above; "==" pins it outright.
_UPPER_BOUND_OPERATORS = ("<", "<=", "~=", "==", "===")
_LOWER_BOUND_OPERATORS = (">=", ">", "==", "===", "~=")


def _specifier_has_upper_bound(specifiers: SpecifierSet) -> bool:
    """Report whether a specifier set caps the version from above."""
    return any(specifier.operator in _UPPER_BOUND_OPERATORS for specifier in specifiers)


def _specifier_lower_bound(specifiers: SpecifierSet) -> "PackagingVersion | None":
    """Return the highest lower bound a specifier set imposes, if any."""
    bounds = []
    for specifier in specifiers:
        if specifier.operator not in _LOWER_BOUND_OPERATORS:
            continue
        try:
            # A wildcard release such as "==1.2.*" still floors the version.
            bounds.append(PackagingVersion(specifier.version.rstrip(".*")))
        except InvalidVersion:
            continue
    return max(bounds) if bounds else None


# Anchored, so that "Python :: 3", "Python :: 3 :: Only" and
# "Python :: Implementation :: CPython" are all correctly ignored.
_PYTHON_CLASSIFIER_RE = re.compile(r"^Programming Language :: Python :: (\d+)\.(\d+)$")


def _classifier_python_versions(classifiers: object) -> "list[tuple[int, int]]":
    """Extract the (major, minor) Python versions named by the classifiers."""
    versions = set()
    for classifier in _as_list(classifiers):
        match = _PYTHON_CLASSIFIER_RE.match(classifier.strip())
        if match:
            versions.add((int(match.group(1)), int(match.group(2))))
    return sorted(versions)


def _project_root(data: Metadata) -> "Path | None":
    """Return the project directory being rated, when one exists on disk."""
    path = data.get("_path")
    if not path:
        return None
    root = Path(path)
    return root if root.is_dir() else None


class FieldTest(BaseTest):
    """Test that a specific field is present and truthy."""

    field: str

    def test(self, data: Metadata) -> TestResult:
        """Check that the configured metadata field is present and truthy."""
        if bool(cast("dict[str, Any]", data).get(self.field)):
            return self._passed()
        suffix = "!" if self.fatal else "."
        return self._failed(f"Your package does not have {self.field} data{suffix}")


class Name(FieldTest):
    """Require project name metadata."""

    fatal = True
    field = "name"


class Version(FieldTest):
    """Require project version metadata."""

    fatal = True
    field = "version"


class VersionIsString(BaseTest):
    """Require the project version to be represented as a string."""

    weight = 50

    def test(self, data: Metadata) -> TestResult:
        """Check the runtime type of the version value."""
        # Check that the version is a string
        version = data.get("version")
        if isinstance(version, str):
            return self._passed()
        return self._failed("The version number should be a string.")


class PEPVersion(BaseTest):
    """Validate project versions against the packaging version rules."""

    category = Category.INDEX

    weight = 50

    def test(self, data: Metadata) -> TestResult:
        """Check version validity, canonical spelling, epochs, and locality."""
        # Check that the version number complies with the version specifiers
        # specification (PEP 440):
        version = data.get("version")
        if version is None:
            return self._skipped()

        try:
            parsed = PackagingVersion(str(version))
        except InvalidVersion:
            return self._failed(
                f"'{version}' is not a valid version. See "
                "https://packaging.python.org/en/latest/specifications/version-specifiers/",
                weight=100,
            )

        warnings = []
        if str(parsed) != str(version).strip():
            warnings.append(
                f"The version '{version}' is valid, but not in canonical form; it should be written as '{parsed}'."
            )
        if parsed.epoch:
            warnings.append(
                f"The version '{version}' uses a version epoch, which most tools handle poorly "
                "and should only be used to recover from a broken versioning scheme."
            )
        if parsed.local:
            warnings.append(
                f"The version '{version}' contains a local version segment, "
                "which should not be used for distributions published to an index."
            )
        if warnings:
            return self._failed("\n".join(warnings), weight=20)
        return self._passed()


class MetadataVersion(BaseTest):
    """Validate the declared Core Metadata version."""

    category = Category.INDEX

    weight = 100

    _valid = ("1.0", "1.1", "1.2", "2.1", "2.2", "2.3", "2.4", "2.5")

    def test(self, data: Metadata) -> TestResult:
        """Check that Metadata-Version names a supported specification."""
        metadata_version = data.get("metadata-version")
        if not metadata_version:
            return self._skipped()
        if str(metadata_version) in self._valid:
            return self._passed(weight=20)
        return self._failed(
            f"'{metadata_version}' is not a valid Metadata-Version; it must be one of {', '.join(self._valid)}."
        )


# The (\Z-anchored) project name format from the names-and-normalization
# specification.
NAME_RE = re.compile(r"^([A-Z0-9]|[A-Z0-9][A-Z0-9._-]*[A-Z0-9])\Z", re.IGNORECASE)


class NameFormat(BaseTest):
    """Validate the project-name syntax."""

    category = Category.INDEX

    fatal = True

    def test(self, data: Metadata) -> TestResult:
        """Check the name against the packaging normalization specification."""
        name = data.get("name")
        if not name:
            # The Name test already handles a missing name.
            return self._skipped()
        if NAME_RE.match(str(name)):
            return self._passed()
        return self._failed(
            f"'{name}' is not a valid project name: it may only contain ASCII letters, digits, "
            "'.', '_' and '-', and must start and end with a letter or digit. Package indices "
            "will reject it. See "
            "https://packaging.python.org/en/latest/specifications/name-normalization/"
        )


class Summary(BaseTest):
    """Require a useful one-line project summary."""

    weight = 100

    def test(self, data: Metadata) -> TestResult:
        """Check that the summary exists and is sufficiently descriptive."""
        summary = data.get("summary")
        if not summary:
            # No summary at all. That's fatal.
            return self._failed("The package had no Summary!", fatal=True)
        if len(summary) <= _MIN_SUMMARY_LENGTH:
            return self._failed("The package's Summary should be longer than 10 characters.")
        return self._passed()


class Description(BaseTest):
    """Require a useful long project description."""

    weight = 50

    def test(self, data: Metadata) -> TestResult:
        """Check that the project description has meaningful length."""
        description = data.get("description", "")
        if not isinstance(description, str):
            description = ""
        if len(description) > _MIN_DESCRIPTION_LENGTH:
            return self._passed()
        return self._failed("The package's Description is quite short.")


class Classifiers(FieldTest):
    """Require project classifiers."""

    weight = 100
    field = "classifier"


def _deprecated_classifier_line(classifier: str) -> str:
    """Describe one deprecated classifier and its replacement, if any."""
    replacements = trove_classifiers.deprecated_classifiers.get(classifier) or []
    if replacements:
        joined = " or ".join(repr(replacement) for replacement in replacements)
        return f"  {classifier!r} has been replaced by {joined}."
    return f"  {classifier!r} has been removed with no replacement."


class ClassifierVerification(BaseTest):
    """Validate classifiers against the registered classifier list."""

    category = Category.COHERENCE

    weight = 20

    def test(self, data: Metadata) -> TestResult:
        """Report deprecated and unknown classifiers separately."""
        classifiers = _as_list(data.get("classifier"))
        deprecated = [c for c in classifiers if c in trove_classifiers.deprecated_classifiers]
        unknown = [
            classifier
            for classifier in classifiers
            if classifier not in trove_classifiers.classifiers
            and classifier not in trove_classifiers.deprecated_classifiers
            and not classifier.startswith("Private :: ")
        ]
        if not deprecated and not unknown:
            return self._passed()

        blocks = []
        if deprecated:
            # A deprecated classifier is a known one, so telling the user it
            # is "not standard" and suggesting 'Private :: ' would be wrong.
            listed = "\n".join(_deprecated_classifier_line(c) for c in deprecated)
            blocks.append(f"Some of your classifiers are deprecated:\n{listed}")
        if unknown:
            listed = "\n".join(unknown)
            blocks.append(
                f"Some of your classifiers are not standard classifiers:\n{listed}\n"
                "If you have custom classifiers, they should start with 'Private :: '"
            )
        blocks.append("You can find the list of standard classifiers here: https://pypi.org/classifiers/")
        return self._failed("\n".join(blocks))


class PythonClassifierVersion(BaseTest):
    """Require classifiers for supported Python minor versions."""

    def test(self, data: Metadata) -> TestResult:
        """Check that at least one Python classifier includes a minor version."""
        major_version_specified = False

        classifiers = data.get("classifier", [])
        for classifier in classifiers:
            parts = [p.strip() for p in classifier.split("::")]
            if (
                len(parts) >= _PYTHON_CLASSIFIER_PREFIX_LENGTH
                and parts[0] == "Programming Language"
                and parts[1] == "Python"
            ):
                if len(parts) == _PYTHON_CLASSIFIER_PREFIX_LENGTH:
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
                "Python you support as well as what major version. "
                "You can find the list of standard classifiers here: "
                "https://pypi.org/classifiers/",
                weight=25,
            )
        # No Python version specified at all:
        return self._failed(
            "The classifiers should specify what Python versions you support. "
            "You can find the list of standard classifiers here: "
            "https://pypi.org/classifiers/",
            weight=100,
        )


class PythonRequiresVersion(BaseTest):
    """Validate the Requires-Python metadata field."""

    weight = 100

    def test(self, data: Metadata) -> TestResult:
        """Check that Requires-Python exists and contains valid specifiers."""
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
    """Require project keywords."""

    weight = 20
    field = "keywords"


class Author(FieldTest):
    """Require author identity, accepting a name embedded in author-email."""

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
    """Require author email metadata."""

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
    """Require useful project URLs with well-known labels."""

    weight = 20

    def test(self, data: Metadata) -> TestResult:
        """Check URL presence, label length, and well-known label usage."""
        urls = _get_project_urls(data)
        has_homepage = bool(data.get("home-page"))

        if not urls and not has_homepage:
            return self._failed(
                "Your package should include links to the project home page and other resources. "
                "Add them to the [project.urls] table in your pyproject.toml, using well-known labels "
                "such as Homepage, Source, Documentation, Changelog or Issues."
            )

        too_long = [label for label, _ in urls if len(label) > _MAX_PROJECT_URL_LABEL_LENGTH]
        if too_long:
            return self._failed(
                "Project-URL labels are limited to 32 characters, and package indices "
                f"will reject longer ones. Too long: {', '.join(too_long)}",
                fatal=True,
            )

        well_known = {WELL_KNOWN_URL_LABELS.get(_normalize_url_label(label)) for label, _ in urls}
        well_known.discard(None)
        if has_homepage:
            well_known.add("homepage")
        if not well_known:
            return self._failed(
                "None of your Project-URL labels match the well-known labels. Consider labeling "
                "your URLs with Homepage, Source, Documentation, Changelog or Issues. See "
                "https://packaging.python.org/en/latest/specifications/well-known-project-urls/",
                weight=10,
            )
        return self._passed()


class Licensing(BaseTest):
    """Validate modern project licensing metadata."""

    category = Category.INDEX

    weight = 50

    def test(self, data: Metadata) -> TestResult:  # noqa: PLR0911 - Each metadata combination is distinct.
        """Check license presence, exclusivity, and SPDX validity."""
        license_value = data.get("license")
        license_expression = data.get("license-expression")
        classifiers = data.get("classifier", [])
        has_license_classifier = any(c.startswith("License") for c in classifiers)

        if not license_value and not license_expression and not has_license_classifier:
            return self._failed(
                "You should specify a license for your package with the 'License-Expression' field. "
                "See https://packaging.python.org/en/latest/specifications/core-metadata/#license-expression "
                "for more information."
            )

        if license_value and license_expression:
            # The Core Metadata specification forbids this, and PyPI rejects
            # such uploads.
            return self._failed(
                "Specifying both a License and a License-Expression is forbidden "
                "and will be rejected by package indices.",
                fatal=True,
            )

        if license_expression:
            try:
                canonicalize_license_expression(str(license_expression))
            except InvalidLicenseExpression:
                # A License-Expression must be a valid SPDX expression, and
                # PyPI rejects invalid ones.
                return self._failed(
                    f"'{license_expression}' is not a valid SPDX license expression, "
                    "and package indices will reject it. See https://spdx.org/licenses/",
                    fatal=True,
                )

            if has_license_classifier:
                return self._failed(
                    "You specify both a License-Expression and license classifiers. License "
                    "classifiers are deprecated in favour of License-Expression, and keeping "
                    "both leaves two sources of truth that can disagree. Remove the license "
                    "classifiers."
                )
            return self._passed()

        if has_license_classifier:
            return self._failed("Using license classifiers is deprecated in favour of the license-expression field.")

        # Only the classic License field; its deprecation is reported by
        # DeprecatedMetadataFields.
        return self._passed()


class DescriptionContentType(BaseTest):
    """Validate Description-Content-Type media types and parameters."""

    category = Category.SPEC

    weight = 50

    _valid_types = ("text/plain", "text/x-rst", "text/markdown")
    _valid_variants = ("gfm", "commonmark")

    def test(self, data: Metadata) -> TestResult:
        """Check the description media type, charset, and Markdown variant."""
        raw = data.get("description-content-type")
        if not raw:
            # If absent, readers assume text/x-rst (or fall back to
            # text/plain), so this is not an error.
            return self._skipped()

        parts = [part.strip() for part in str(raw).split(";")]
        content_type = parts[0].lower()
        problems = []

        if content_type not in self._valid_types:
            problems.append(f"The content type should be one of {', '.join(self._valid_types)}, not '{parts[0]}'.")

        for parameter in parts[1:]:
            key, _, value = parameter.partition("=")
            key = key.strip().lower()
            value = value.strip().strip('"')
            if key == "charset":
                if value.lower() != "utf-8":
                    problems.append(f"The only accepted charset is UTF-8, not '{value}'.")
            elif key == "variant":
                if content_type != "text/markdown":
                    problems.append("The 'variant' parameter is only valid for text/markdown.")
                elif value.lower() not in self._valid_variants:
                    problems.append(f"The markdown variant should be GFM or CommonMark, not '{value}'.")
            else:
                problems.append(f"'{key}' is not a valid Description-Content-Type parameter.")

        if problems:
            return self._failed(
                f"Your Description-Content-Type '{raw}' is invalid:\n"
                + "\n".join(problems)
                + "\nSee https://packaging.python.org/en/latest/specifications/core-metadata/#description-content-type"
            )
        return self._passed(weight=20)


# Marker variables that are not versions, so ordered comparison operators
# make no sense for them.
NON_VERSION_MARKERS = (
    "os_name",
    "sys_platform",
    "platform_machine",
    "platform_system",
    "platform_python_implementation",
    "implementation_name",
    "extra",
)

_ORDERED_NON_VERSION_MARKER_RE = re.compile(rf"({'|'.join(NON_VERSION_MARKERS)})\s*(<=|>=|<|>|~=)")

# A parenthesized version specifier, e.g. "zope.interface (>4.0)". The
# dependency specifiers specification advises against this legacy form.
_PARENTHESIZED_VERSIONS_RE = re.compile(r"\(\s*[<>=!~]")


class DependencySpecifiers(BaseTest):
    """Validate Requires-Dist dependency specifiers and markers."""

    category = Category.SPEC

    weight = 100

    def test(self, data: Metadata) -> TestResult:
        """Check dependency syntax and discourage ambiguous legacy forms."""
        requirements = data.get("requires-dist")
        if not requirements:
            return self._skipped()
        if isinstance(requirements, str):
            requirements = [requirements]

        errors = []
        warnings = []
        for raw_requirement in requirements:
            requirement = str(raw_requirement)
            try:
                Requirement(requirement)
            except InvalidRequirement as e:
                errors.append(f"'{requirement}' is not a valid dependency specifier: {e}")
                continue

            specifier_part, _, marker_part = requirement.partition(";")
            if _PARENTHESIZED_VERSIONS_RE.search(specifier_part):
                warnings.append(
                    f"'{requirement}' puts the version specifier in parentheses, "
                    "a legacy form that the dependency specifiers specification advises against."
                )
            match = _ORDERED_NON_VERSION_MARKER_RE.search(marker_part)
            if match:
                warnings.append(
                    f"'{requirement}' uses an ordered comparison on the '{match.group(1)}' "
                    "environment marker, which is not a version, so the comparison "
                    "is a lexical string comparison and probably not what you want."
                )

        if errors:
            return self._failed(
                "Your Requires-Dist entries have problems:\n" + "\n".join(errors + warnings), weight=100
            )
        if warnings:
            return self._failed("Your Requires-Dist entries have problems:\n" + "\n".join(warnings), weight=10)
        return self._passed(weight=20)


def _load_pyproject(data: Metadata) -> "dict[str, Any] | None":
    """Return the pre-parsed pyproject.toml table, if there is a usable one.

    projectdata parses the file once; nothing here reads from disk. The
    isinstance guard matters because the property test feeds arbitrary
    values through this path.
    """
    pyproject = data.get("_pyproject")
    if not isinstance(pyproject, dict):
        return None
    return pyproject


def _load_project_table(data: Metadata) -> "dict[str, Any] | None":
    pyproject = _load_pyproject(data)
    if pyproject is None:
        return None
    project = pyproject.get("project")
    if not isinstance(project, dict):
        # No [project] table; the metadata comes from somewhere else.
        return None
    return cast("dict[str, Any]", project)


def _table_defines_file_and_text(value: object) -> bool:
    return isinstance(value, dict) and "file" in value and "text" in value


def _project_table_errors(project: "dict[str, Any]") -> "list[str]":
    errors = []
    dynamic = project.get("dynamic", [])
    if not isinstance(dynamic, list):
        # PyprojectTomlValid reports the schema violation.
        dynamic = []

    if "name" in dynamic:
        errors.append("The 'name' key must be static, it must never be listed in 'dynamic'.")
    if "version" not in project and "version" not in dynamic:
        errors.append("The 'version' key must either be set statically or be listed in 'dynamic'.")

    errors.extend(
        f"The '{table_name}' table must not specify both 'file' and 'text'."
        for table_name in ("readme", "license")
        if _table_defines_file_and_text(project.get(table_name))
    )

    entry_points = project.get("entry-points", {})
    if not isinstance(entry_points, dict):
        return errors
    errors.extend(
        f"Console and GUI scripts must be defined in [project.scripts] and "
        f"[project.gui-scripts], not [project.entry-points.{group}]."
        for group in ("console_scripts", "gui_scripts")
        if group in entry_points
    )
    return errors


class PyProjectProjectTable(BaseTest):
    """Spot violations of the pyproject.toml ``project`` table.

    This covers specification rules that validate-pyproject's schemas do not
    catch.
    """

    category = Category.SPEC

    def test(self, data: Metadata) -> TestResult:
        """Validate cross-field rules in the project table."""
        project = _load_project_table(data)
        if project is None:
            return self._skipped()

        errors = _project_table_errors(project)
        if not errors:
            # Like the other build system tests, this gives no positive rating.
            return self._passed(weight=0)

        # These are all MUST rules in the pyproject.toml specification;
        # build backends are required to raise errors for them.
        return self._failed(
            "Your pyproject.toml [project] table violates the pyproject.toml specification:\n"
            + "\n".join(errors)
            + "\nSee https://packaging.python.org/en/latest/specifications/pyproject-toml/",
            fatal=True,
        )


class DevStatusClassifier(BaseTest):
    """Require a Development Status classifier."""

    weight = 20

    def test(self, data: Metadata) -> TestResult:
        """Check that classifiers communicate project maturity."""
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
    """Require an uploaded source distribution when rating an index project."""

    category = Category.PRACTICE

    def test(self, data: Metadata) -> TestResult:
        """Check the source-distribution sentinel supplied by index metadata."""
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
    """Validate reStructuredText long descriptions."""

    weight = 50

    def test(self, data: Metadata) -> TestResult:
        """Render ReST descriptions and report parser errors."""
        # Only the media type matters here; parameters such as charset are
        # validated by DescriptionContentType.
        raw = data.get("description-content-type") or ""
        content_type = str(raw).split(";")[0].strip().lower()
        if content_type in ("text/plain", "text/markdown"):
            # These can't fail. Markdown will just assume everything
            # it doesn't understand is plain text.
            return self._passed()

        # This should be ReStructuredText
        source = data.get("description", "")
        stream = io.StringIO()
        settings = {"warning_stream": stream}

        caught_message = ""
        try:
            publish_parts(source=source, writer="html4css1", settings_overrides=settings)
        except SystemMessage as e:
            caught_message = str(e)
        errors = stream.getvalue().strip() or caught_message
        if not errors:
            return self._passed()

        return self._failed("Your Description is not valid ReST: \n" + errors)


class BusFactor(BaseTest):
    """Encourage projects to have multiple index owners."""

    category = Category.PRACTICE

    def test(self, data: Metadata) -> TestResult:
        """Rate the number of project owners reported by the index."""
        if "_owners" not in data:
            return self._skipped()

        owners = len(data.get("_owners", []))
        if owners >= _MIN_BUS_FACTOR:
            # Three or more, that's good.
            return self._passed(weight=100)

        message = "You should have three or more owners of the project on PyPI."
        if owners == _LOW_BUS_FACTOR:
            return self._failed(message, weight=50)
        # One owner, or none at all.
        return self._failed(message, weight=100)


class MissingBuildSystem(BaseTest):
    """Report projects that cannot be built by standard tooling."""

    category = Category.INTERNAL

    def test(self, data: Metadata) -> TestResult:
        """Check for the missing-build-system sentinel."""
        if "_missing_build_system" in data:
            # The build system tests give only negative weight, as they are effectively required
            # for a working package, so passing them shouldn't give you a better rating,
            # but failing them should give you a worse rating.
            return self._failed(
                "You seem to neither have a setup.py, nor a pyproject.toml, only setup.cfg.\n"
                "This makes it unclear how your project should be built, and some packaging tools may fail.\n"
                "See https://packaging.python.org for more information on how to package your project.",
                weight=400,
            )

        return self._passed(weight=0)


class MissingPyProjectToml(BaseTest):
    """Recommend a pyproject.toml build configuration."""

    category = Category.INTERNAL

    def test(self, data: Metadata) -> TestResult:
        """Check for missing pyproject.toml metadata sentinels."""
        # This may not yet be required, but it will be in the future, so we
        # give it a negative rating when it fails, but not a positive rating
        # when it succeeds.
        if "_missing_build_system" in data or "_missing_pyproject_toml" in data:
            return self._failed(
                "Your project does not have a pyproject.toml file, which is highly recommended.\n"
                "You probably want to create one declaring your build backend, for example:\n\n"
                "    [build-system]\n"
                '    requires = ["setuptools>=77"]\n'
                '    build-backend = "setuptools.build_meta"\n\n'
                "Any PEP 517 build backend works, for example flit_core, hatchling or uv_build.\n"
                "See https://packaging.python.org for more information on how to package your project.",
                weight=100,
            )
        return self._passed(weight=0)


@cache
def _pyproject_validator() -> pyproject_api.Validator:
    # The validator loads its schema plugins on creation, so cache it.
    return pyproject_api.Validator()


class PyprojectTomlValid(BaseTest):
    """Validate pyproject.toml against registered project schemas."""

    category = Category.SPEC

    def test(self, data: Metadata) -> TestResult:
        """Validate the project's pre-parsed pyproject.toml."""
        # The build system tests give only negative weight, as they are effectively required
        # for a working package, so passing them shouldn't give you a better rating,
        # but failing them should give you a worse rating.
        read_error = data.get("_pyproject_error")
        if read_error:
            # projectdata could not read or parse the file at all.
            return self._invalid(str(read_error))

        pyproject = _load_pyproject(data)
        if pyproject is None:
            # No pyproject.toml to validate, skip this test.
            return self._skipped()

        try:
            _pyproject_validator()(pyproject)
        except pyproject_errors.ValidationError as e:
            return self._invalid(str(e))
        return self._passed(weight=0)

    def _invalid(self, reason: str) -> TestResult:
        return self._failed(
            f"Your pyproject.toml is invalid: {reason}\n"
            "See https://packaging.python.org for more information on how to package your project.",
            weight=100,
        )


class DeprecatedMetadataFields(BaseTest):
    """Report legacy metadata fields when modern replacements are available."""

    category = Category.COHERENCE

    weight = 50

    _deprecated: ClassVar[dict[str, tuple[str, str]]] = {
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
        """Check fields whose metadata-version declares them deprecated."""
        warnings = []

        for deprecated, (replacement, deprecated_since) in self._deprecated.items():
            if not self._version_at_least(data, deprecated_since):
                continue

            if cast("dict[str, Any]", data).get(deprecated) and not cast("dict[str, Any]", data).get(replacement):
                warnings.append(f"The metadata field '{deprecated}' is deprecated; use '{replacement}' instead.")

        if warnings:
            return self._failed("\n".join(warnings))
        return self._passed()


# ---------------------------------------------------------------------------
# Advisory checks.
#
# These are reported but never scored, so that adding one can never move an
# existing package's rating. Running with --strict scores them like any other
# test. See RatedProject.advisories.
# ---------------------------------------------------------------------------

_DEPENDENCY_FIELDS = ("requires-dist", "provides-dist", "obsoletes-dist")


class DirectUrlDependency(BaseTest):
    """Flag dependencies declared as direct URL references."""

    category = Category.INDEX

    advisory = True
    fatal = True

    def test(self, data: Metadata) -> TestResult:
        """Check that no dependency is declared as a direct URL reference."""
        direct = []
        for field_name in _DEPENDENCY_FIELDS:
            for entry in _as_list(cast("dict[str, Any]", data).get(field_name)):
                try:
                    requirement = Requirement(entry)
                except InvalidRequirement:
                    # DependencySpecifiers reports malformed specifiers.
                    continue
                if requirement.url is not None:
                    direct.append(entry)

        if not direct:
            return self._passed()
        listed = "\n".join(f"  {entry}" for entry in direct)
        return self._failed(
            "Your package declares direct URL dependencies, which package indices reject "
            f"outright:\n{listed}\n"
            "Depend on a released version from an index instead, and keep direct URLs in a "
            "requirements file or a [dependency-groups] entry."
        )


# Package indices limit the Summary to 512 characters; it is the only
# metadata field with a length limit.
_MAX_SUMMARY_LENGTH = 512


class SummaryFormat(BaseTest):
    """Enforce the Summary length and single-line rules indices apply."""

    category = Category.INDEX

    advisory = True
    fatal = True

    def test(self, data: Metadata) -> TestResult:
        """Check the Summary is one line and within the index length limit."""
        summary = data.get("summary")
        if not summary or not isinstance(summary, str):
            # Summary reports a missing or empty summary.
            return self._skipped()
        if "\n" in summary or "\r" in summary:
            return self._failed(
                "Your Summary contains a line break, but it must be a single line. "
                "Package indices reject multi-line summaries; move the detail into "
                "your Description."
            )
        if len(summary) > _MAX_SUMMARY_LENGTH:
            return self._failed(
                f"Your Summary is {len(summary)} characters long, but package indices limit "
                f"it to {_MAX_SUMMARY_LENGTH} and will reject the upload. Move the detail "
                "into your Description."
            )
        return self._passed()


_COLLIDING_PAIR = 2


def _collision_line(canonical: str, labels: "list[str]") -> str:
    """Describe one group of Project-URL labels that normalize alike."""
    qualifier = "both" if len(labels) == _COLLIDING_PAIR else "all"
    joined = ", ".join(repr(label) for label in labels)
    return f"  {joined} {qualifier} normalize to {canonical!r}."


class ProjectUrlLabelCollision(BaseTest):
    """Flag Project-URL labels that collide once normalized."""

    category = Category.COHERENCE

    advisory = True
    weight = 10

    def test(self, data: Metadata) -> TestResult:
        """Check that no two Project-URL labels normalize to the same name."""
        urls = _get_project_urls(data)
        if not urls:
            return self._skipped()

        grouped: dict[str, list[str]] = {}
        for label, _url in urls:
            normalized = _normalize_url_label(label)
            canonical = WELL_KNOWN_URL_LABELS.get(normalized, normalized)
            grouped.setdefault(canonical, []).append(str(label))

        # Sorted, so the message is deterministic for the property test.
        collisions = sorted((canonical, labels) for canonical, labels in grouped.items() if len(labels) > 1)
        if not collisions:
            return self._passed()
        listed = "\n".join(_collision_line(canonical, labels) for canonical, labels in collisions)
        return self._failed(
            "Several of your Project-URL labels mean the same thing once normalized, so "
            f"indices and other tools collapse them into one link:\n{listed}\n"
            "See https://packaging.python.org/en/latest/specifications/well-known-project-urls/"
        )


class PythonRequiresUpperBound(BaseTest):
    """Flag an upper bound on Requires-Python."""

    category = Category.PRACTICE

    advisory = True
    weight = 20

    def test(self, data: Metadata) -> TestResult:
        """Check that Requires-Python does not cap the Python version."""
        specifiers = _parse_specifier_set(data.get("requires-python"))
        if specifiers is None:
            # PythonRequiresVersion reports a missing or invalid value.
            return self._skipped()
        if not _specifier_has_upper_bound(specifiers):
            return self._passed()
        return self._failed(
            f"Your Requires-Python ('{data.get('requires-python')}') has an upper bound. "
            "Capping the Python version makes your package uninstallable on every new "
            "Python release until you publish a fix, and resolvers cannot work around it. "
            "Drop the cap and let your classifiers say which versions you actually test."
        )


def _runtime_requirements(data: Metadata) -> "list[Requirement]":
    """Parse the runtime dependencies, skipping extras and malformed entries."""
    requirements = []
    for entry in _as_list(cast("dict[str, Any]", data).get("requires-dist")):
        try:
            requirement = Requirement(entry)
        except InvalidRequirement:
            # DependencySpecifiers reports malformed specifiers.
            continue
        # An "extra" marker means this is an optional dependency, not a
        # dependency every user of the package will install.
        if requirement.marker is not None and "extra" in str(requirement.marker):
            continue
        requirements.append(requirement)
    return requirements


class DependencyUpperBounds(BaseTest):
    """Flag runtime dependencies that are capped or pinned."""

    category = Category.PRACTICE

    advisory = True
    weight = 20

    def test(self, data: Metadata) -> TestResult:
        """Check that runtime dependencies do not cap or pin their versions."""
        capped = [
            str(requirement)
            for requirement in _runtime_requirements(data)
            if _specifier_has_upper_bound(requirement.specifier)
        ]
        if not capped:
            return self._passed()
        listed = "\n".join(f"  {entry}" for entry in sorted(capped))
        return self._failed(
            "These runtime dependencies cap or pin their versions, which causes resolution "
            f"conflicts for anyone who depends on your package:\n{listed}\n"
            "You cannot know that the next release breaks you, and consumers cannot override "
            "the cap. If you publish an application rather than a library, skip this with "
            "--skip-tests DependencyUpperBounds."
        )


class DependencyLowerBounds(BaseTest):
    """Flag runtime dependencies with no minimum version."""

    category = Category.PRACTICE

    advisory = True
    weight = 10

    def test(self, data: Metadata) -> TestResult:
        """Check that every runtime dependency declares a lower bound."""
        floorless = [
            requirement.name
            for requirement in _runtime_requirements(data)
            # A direct URL reference names an exact artifact and has no
            # specifier at all; DirectUrlDependency reports those.
            if requirement.url is None and _specifier_lower_bound(requirement.specifier) is None
        ]
        if not floorless:
            return self._passed()
        return self._failed(
            "These runtime dependencies have no lower bound, so a resolver may install a "
            f"version far older than anything you tested: {', '.join(sorted(floorless))}. "
            "Add a floor for each, for example 'requests>=2.32'."
        )


_MARKDOWN_TARGET_RES = (
    re.compile(r"!\[[^\]]*\]\(\s*([^)\s]+)"),
    re.compile(r"(?<!!)\[[^\]]*\]\(\s*([^)\s]+)"),
    re.compile(r"<img[^>]+src=[\"']([^\"']+)"),
)
_REST_TARGET_RES = (
    re.compile(r"(?m)^\s*\.\.\s+image::\s*(\S+)"),
    re.compile(r"`[^`]+<([^>]+)>`_"),
)
_MAX_LISTED_TARGETS = 5


def _is_relative_target(target: str) -> bool:
    """Report whether a link target is a repository-relative path."""
    if target.startswith(("#", "//", "mailto:", "data:")):
        return False
    return urlsplit(target).scheme == ""


def _relative_description_targets(description: str, content_type: str) -> "list[str]":
    """Return the relative link and image targets found in a description."""
    if content_type == "text/plain":
        return []
    patterns = _MARKDOWN_TARGET_RES if content_type == "text/markdown" else _REST_TARGET_RES
    # A list rather than a set, so the order is deterministic.
    found: list[str] = []
    for pattern in patterns:
        for match in pattern.finditer(description):
            target = match.group(1)
            if target not in found and _is_relative_target(target):
                found.append(target)
    return found


class ReadmeRelativeLinks(BaseTest):
    """Flag relative links in the description, which do not resolve on PyPI."""

    category = Category.PRACTICE

    advisory = True
    weight = 20

    def test(self, data: Metadata) -> TestResult:
        """Check the Description has no repository-relative links or images."""
        description = data.get("description")
        if not description or not isinstance(description, str):
            return self._skipped()

        # Readers assume reStructuredText when no type is declared.
        raw = data.get("description-content-type") or "text/x-rst"
        content_type = str(raw).split(";")[0].strip().lower()
        targets = _relative_description_targets(description, content_type)
        if not targets:
            return self._passed()

        listed = "\n".join(f"  {target}" for target in targets[:_MAX_LISTED_TARGETS])
        if len(targets) > _MAX_LISTED_TARGETS:
            listed += f"\n  (and {len(targets) - _MAX_LISTED_TARGETS} more)"
        return self._failed(
            "Your Description refers to files with relative paths, which do not resolve on "
            f"PyPI because the rendered page has no repository context:\n{listed}\n"
            "Use absolute URLs instead, for example a https://raw.githubusercontent.com/... "
            "link for images."
        )


class PythonVersionCoherence(BaseTest):
    """Flag Python classifiers that contradict Requires-Python."""

    category = Category.COHERENCE

    advisory = True
    weight = 50

    def test(self, data: Metadata) -> TestResult:
        """Check the Python classifiers agree with Requires-Python."""
        specifiers = _parse_specifier_set(data.get("requires-python"))
        versions = _classifier_python_versions(data.get("classifier"))
        if specifiers is None or not versions:
            # PythonRequiresVersion and PythonClassifierVersion report absences.
            return self._skipped()

        excluded = [f"{major}.{minor}" for major, minor in versions if not specifiers.contains(f"{major}.{minor}")]
        if not excluded:
            return self._passed()
        return self._failed(
            f"Your classifiers claim support for Python {', '.join(excluded)}, but your "
            f"Requires-Python ('{data.get('requires-python')}') excludes those versions. "
            "Installers trust Requires-Python and ignore the classifiers, so the two must agree."
        )


# CPython end-of-life dates. Frozen at import so that a rating is
# deterministic within a process, which the property test requires, and so
# that tests can patch a fixed date.
_TODAY = datetime.datetime.now(tz=datetime.UTC).date()
_PYTHON_EOL_DATES = {
    (2, 6): datetime.date(2013, 10, 29),
    (2, 7): datetime.date(2020, 1, 1),
    (3, 0): datetime.date(2009, 6, 27),
    (3, 1): datetime.date(2012, 4, 9),
    (3, 2): datetime.date(2016, 2, 20),
    (3, 3): datetime.date(2017, 9, 29),
    (3, 4): datetime.date(2019, 3, 18),
    (3, 5): datetime.date(2020, 9, 30),
    (3, 6): datetime.date(2021, 12, 23),
    (3, 7): datetime.date(2023, 6, 27),
    (3, 8): datetime.date(2024, 10, 7),
    (3, 9): datetime.date(2025, 10, 31),
    (3, 10): datetime.date(2026, 10, 31),
    (3, 11): datetime.date(2027, 10, 31),
    (3, 12): datetime.date(2028, 10, 31),
    (3, 13): datetime.date(2029, 10, 31),
    (3, 14): datetime.date(2030, 10, 31),
}


def _is_end_of_life(version: "tuple[int, int]") -> bool:
    """Report whether a Python version has passed its end-of-life date."""
    end_of_life = _PYTHON_EOL_DATES.get(version)
    if end_of_life is not None:
        return end_of_life < _TODAY
    # Older than the oldest version tracked is long dead. Newer than the
    # newest is unreleased, so treat it as supported and extend the table
    # when the release happens; a stale table under-reports, never misfires.
    return version < min(_PYTHON_EOL_DATES)


class EndOfLifePython(BaseTest):
    """Flag claimed support for end-of-life Python versions."""

    category = Category.PRACTICE

    advisory = True
    weight = 20

    def test(self, data: Metadata) -> TestResult:
        """Check no end-of-life Python version is advertised as supported."""
        dead = [
            f"{major}.{minor}"
            for major, minor in _classifier_python_versions(data.get("classifier"))
            if _is_end_of_life((major, minor))
        ]
        if not dead:
            return self._passed()
        return self._failed(
            f"Your classifiers still claim support for Python {', '.join(dead)}, which "
            "reached end of life. Dropping dead versions lets you use newer syntax and "
            "stops resolvers offering your package to interpreters nobody supports."
        )


_DEV_STATUS_RE = re.compile(r"^Development Status :: (\d) - .+$")
_STABLE_DEV_STATUS = 5
_EARLY_DEV_STATUS = 3


def _development_status(classifiers: object) -> "tuple[int, str] | None":
    """Return the highest Development Status classifier, if there is one."""
    found = []
    for classifier in _as_list(classifiers):
        stripped = classifier.strip()
        match = _DEV_STATUS_RE.match(stripped)
        if match:
            found.append((int(match.group(1)), stripped))
    return max(found) if found else None


def _parse_version(value: object) -> "PackagingVersion | None":
    """Parse a version string, returning None when it is unusable."""
    if not value:
        return None
    try:
        return PackagingVersion(str(value))
    except InvalidVersion:
        return None


class DevelopmentStatusCoherence(BaseTest):
    """Flag a Development Status classifier that contradicts the version."""

    category = Category.COHERENCE

    advisory = True
    weight = 20

    def test(self, data: Metadata) -> TestResult:
        """Check the Development Status classifier agrees with the version."""
        status = _development_status(data.get("classifier"))
        version = _parse_version(data.get("version"))
        if status is None or version is None:
            return self._skipped()

        level, classifier = status
        if level >= _STABLE_DEV_STATUS:
            return self._unstable_version_result(level, classifier, version)
        if level <= _EARLY_DEV_STATUS and not version.is_prerelease and version.major >= 1:
            return self._failed(
                f"Your classifiers say '{classifier}', but you have released version "
                f"'{version}', a final release. Users read the classifier, not the version "
                "number, so raise the development status to match."
            )
        return self._passed()

    def _unstable_version_result(self, level: int, classifier: str, version: PackagingVersion) -> TestResult:
        """Check a stable-or-better status against an unstable version."""
        if version.is_prerelease or version.is_devrelease:
            return self._failed(
                f"Your classifiers say '{classifier}', but your version '{version}' is a "
                "pre-release. Pick one: ship a final version, or lower the development "
                "status to '4 - Beta' or below."
            )
        if version.major == 0:
            return self._failed(
                f"Your classifiers say '{classifier}', but your version is '{version}'. "
                "A 0.x version tells installers your API is not stable yet, which "
                "contradicts the classifier."
            )
        del level
        return self._passed()


def _has_typed_marker(root: Path) -> bool:
    """Report whether the project ships a PEP 561 py.typed marker."""
    # Depth-bounded: rglob would walk .git, .venv and node_modules.
    with suppress(OSError):
        return any(root.glob("*/py.typed")) or any(root.glob("src/*/py.typed"))
    return False


class TypedMarker(BaseTest):
    """Flag a py.typed marker that disagrees with the Typing classifier."""

    category = Category.COHERENCE

    advisory = True
    weight = 20

    def test(self, data: Metadata) -> TestResult:
        """Check the py.typed marker and Typing :: Typed classifier agree."""
        root = _project_root(data)
        if root is None:
            return self._skipped()

        has_marker = _has_typed_marker(root)
        has_classifier = "Typing :: Typed" in _as_list(data.get("classifier"))
        if has_marker and not has_classifier:
            return self._failed(
                "Your project ships a py.typed marker but does not advertise it. Add the "
                "'Typing :: Typed' classifier so users and tools know the package is typed."
            )
        if has_classifier and not has_marker:
            return self._failed(
                "You declare the 'Typing :: Typed' classifier, but no py.typed marker file "
                "was found in your package. Without it, type checkers ignore your "
                "annotations entirely. See https://peps.python.org/pep-0561/"
            )
        if not has_marker:
            # An untyped package is neither penalized nor rewarded.
            return self._skipped()
        return self._passed()


def _build_system_table(data: Metadata) -> "dict[str, Any] | None":
    """Return the [build-system] table from the pre-parsed pyproject.toml."""
    pyproject = _load_pyproject(data)
    if pyproject is None:
        return None
    build_system = pyproject.get("build-system")
    if not isinstance(build_system, dict):
        return None
    return cast("dict[str, Any]", build_system)


class BuildBackendDeclared(BaseTest):
    """Require a build-backend alongside a [build-system] table."""

    advisory = True
    category = Category.PRACTICE
    weight = 50

    def test(self, data: Metadata) -> TestResult:
        """Check that a declared [build-system] also names its backend."""
        build_system = _build_system_table(data)
        # A pyproject.toml with no [build-system] at all is a different
        # problem, and MissingPyProjectToml already covers having none.
        if build_system is None:
            return self._skipped()
        if build_system.get("build-backend"):
            return self._passed()
        return self._failed(
            "Your pyproject.toml has a [build-system] table but no build-backend key, so "
            "tools fall back to the legacy setuptools behaviour rather than the backend "
            'your requires list implies. Add, for example:\n\n    build-backend = "setuptools.build_meta"'
        )


# Backends whose PEP 639 support arrived in a known release. A backend that
# is not listed is deliberately skipped rather than guessed at.
_BACKEND_DISTRIBUTIONS = {
    "setuptools.build_meta": "setuptools",
    "setuptools.build_meta:__legacy__": "setuptools",
    "hatchling.build": "hatchling",
    "flit_core.buildapi": "flit-core",
}
_PEP639_BACKEND_FLOORS = {"setuptools": "77.0.0", "hatchling": "1.27", "flit-core": "1.11"}


def _uses_pep639_licensing(project: "dict[str, Any]") -> bool:
    """Report whether the project table uses PEP 639 licence metadata."""
    # A string license is the SPDX form; a table is the legacy file/text form.
    return isinstance(project.get("license"), str) or "license-files" in project


def _declared_backend_floor(build_system: "dict[str, Any]", distribution: str) -> "PackagingVersion | None":
    """Return the lower bound declared for one build requirement."""
    for entry in _as_list(build_system.get("requires")):
        try:
            requirement = Requirement(entry)
        except InvalidRequirement:
            continue
        if normalize(requirement.name) == distribution:
            return _specifier_lower_bound(requirement.specifier)
    return None


class BuildBackendVersionFloor(BaseTest):
    """Require a build backend new enough for the metadata features used."""

    advisory = True
    category = Category.COHERENCE
    weight = 100

    def test(self, data: Metadata) -> TestResult:
        """Check the build-system floor supports the licence metadata used."""
        project = _load_project_table(data)
        build_system = _build_system_table(data)
        if project is None or build_system is None or not _uses_pep639_licensing(project):
            return self._skipped()

        backend = build_system.get("build-backend")
        distribution = _BACKEND_DISTRIBUTIONS.get(str(backend))
        if distribution is None:
            # An unrecognised backend; do not guess at its PEP 639 support.
            return self._skipped()

        floor = _PEP639_BACKEND_FLOORS[distribution]
        declared = _declared_backend_floor(build_system, distribution)
        if declared is not None and declared >= PackagingVersion(floor):
            return self._passed()
        return self._failed(
            "Your [project] table uses PEP 639 licensing (an SPDX 'license' string or "
            f"'license-files'), but your declared build backend floor is too low: PEP 639 "
            f"needs {distribution}>={floor}. Older backends emit the wrong License metadata "
            f"without telling you. Raise the [build-system] requires entry to "
            f"'{distribution}>={floor}'."
        )


class LicenseFilesExist(BaseTest):
    """Require every license-files pattern to match a real file."""

    advisory = True
    category = Category.COHERENCE
    weight = 50

    def test(self, data: Metadata) -> TestResult:
        """Check that each license-files glob matches something on disk."""
        project = _load_project_table(data)
        root = _project_root(data)
        if project is None or root is None or "license-files" not in project:
            return self._skipped()

        unmatched = [pattern for pattern in _as_list(project.get("license-files")) if not _glob_matches(root, pattern)]
        if not unmatched:
            return self._passed()
        listed = "\n".join(f"  {pattern}" for pattern in unmatched)
        return self._failed(
            "These 'license-files' patterns in your pyproject.toml match no file in your "
            f"project:\n{listed}\n"
            "Your distribution will ship without a license file even though you declared "
            "one. See https://packaging.python.org/en/latest/specifications/pyproject-toml/"
        )


def _glob_matches(root: Path, pattern: str) -> bool:
    """Report whether a license-files pattern matches anything under root."""
    if not pattern or pattern.startswith("/") or ".." in pattern:
        # PEP 639 forbids absolute paths and parent traversal, and Path.glob
        # raises on them rather than returning nothing.
        return False
    try:
        return any(root.glob(pattern))
    except (NotImplementedError, ValueError, OSError):
        return False


_README_SUFFIX_TYPES = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".rst": "text/x-rst",
    ".txt": "text/plain",
}


def _declared_readme(project: "dict[str, Any]") -> "tuple[str, str | None] | None":
    """Return the readme filename and its explicitly declared content type."""
    readme = project.get("readme")
    if isinstance(readme, str):
        return readme, None
    if isinstance(readme, dict):
        filename = readme.get("file")
        if isinstance(filename, str):
            content_type = readme.get("content-type")
            return filename, content_type if isinstance(content_type, str) else None
    return None


class ReadmeContentTypeMatch(BaseTest):
    """Require the readme's content type to match its file extension."""

    advisory = True
    category = Category.COHERENCE
    weight = 50

    def test(self, data: Metadata) -> TestResult:
        """Check the declared content type matches the readme's suffix."""
        project = _load_project_table(data)
        if project is None:
            return self._skipped()
        declared = _declared_readme(project)
        if declared is None:
            return self._skipped()

        filename, explicit_type = declared
        expected = _README_SUFFIX_TYPES.get(Path(filename).suffix.lower())
        raw = explicit_type or data.get("description-content-type") or ""
        actual = str(raw).split(";")[0].strip().lower()
        if expected is None or not actual or actual == expected:
            return self._passed()
        return self._failed(
            f"Your readme is '{filename}', which is {expected}, but its content type is "
            f"'{actual}'. PyPI renders the description using the declared type, so the page "
            f'will come out mangled. Set content-type = "{expected}".'
        )


# Extra names that almost always mean "not for your users".
_DEVELOPMENT_EXTRA_NAMES = frozenset(
    {
        "dev",
        "develop",
        "development",
        "test",
        "tests",
        "testing",
        "doc",
        "docs",
        "documentation",
        "lint",
        "linting",
        "typing",
        "typecheck",
        "check",
        "checks",
        "qa",
    }
)


class DevelopmentExtras(BaseTest):
    """Flag development dependencies published as optional extras."""

    advisory = True
    category = Category.PRACTICE
    weight = 20

    def test(self, data: Metadata) -> TestResult:
        """Check development dependencies use PEP 735 dependency groups."""
        project = _load_project_table(data)
        if project is None:
            return self._skipped()
        extras = project.get("optional-dependencies")
        if not isinstance(extras, dict):
            return self._skipped()

        development = sorted(name for name in extras if normalize(str(name)) in _DEVELOPMENT_EXTRA_NAMES)
        if not development:
            return self._passed()
        listed = ", ".join(repr(name) for name in development)
        return self._failed(
            f"Your development dependencies are declared as extras: {listed}. Extras are "
            "published in your package metadata and installable by your users, who do not "
            "need your test runner. Move them to [dependency-groups], which stays local to "
            "your project. See "
            "https://packaging.python.org/en/latest/specifications/dependency-groups/"
        )


BUILD_SYSTEM_TESTS: "list[BaseTest]" = [
    MissingBuildSystem(),
    MissingPyProjectToml(),
    PyprojectTomlValid(),
    PyProjectProjectTable(),
]

ALL_TESTS: "list[BaseTest]" = [
    *BUILD_SYSTEM_TESTS,
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
    # Advisory checks. Reported but not scored; see RatedProject.advisories.
    DirectUrlDependency(),
    SummaryFormat(),
    ProjectUrlLabelCollision(),
    PythonRequiresUpperBound(),
    DependencyUpperBounds(),
    DependencyLowerBounds(),
    ReadmeRelativeLinks(),
    PythonVersionCoherence(),
    EndOfLifePython(),
    DevelopmentStatusCoherence(),
    TypedMarker(),
    BuildBackendDeclared(),
    BuildBackendVersionFloor(),
    LicenseFilesExist(),
    ReadmeContentTypeMatch(),
    DevelopmentExtras(),
]


try:
    import check_manifest

    class CheckManifest(BaseTest):
        """Compare version-controlled files with the built source distribution."""

        category = Category.PRACTICE

        def test(self, data: Metadata) -> TestResult:
            """Run check-manifest when metadata represents a VCS checkout."""
            if "_path" not in data:
                return self._skipped()

            if "_sdist" in data:
                # An unpacked sdist is not the VCS checkout that
                # check-manifest needs to compare against.
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


def _no_config_result(data: Metadata) -> "RatedProject | None":
    if any(not key.startswith("_") for key in data) or "_no_config_found" not in data:
        return None

    return RatedProject(
        name=data.get("name"),
        rating=0,
        level=LEVELS[0],
        problems=[
            Problem(
                test="NoConfigFound",
                message="I couldn't find any package data. Are you checking the correct directory or file?",
                weight=0,
                fatal=True,
            )
        ],
    )


def _initial_rating_state(data: Metadata) -> "tuple[list[BaseTest], list[Problem], bool]":
    problems = []
    fatality = False
    test_list = ALL_TESTS

    if not any(not key.startswith("_") for key in data) and "_missing_build_system" in data:
        # There is no way to build the package, so no metadata could be
        # extracted. Only report the build-system problems; complaining
        # about each missing metadata field would just be noise.
        test_list = BUILD_SYSTEM_TESTS

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
    return test_list, problems, fatality


def _normalized_skip_tests(skip_tests: "list[str] | str | None") -> "set[str]":
    if skip_tests is None:
        return set()
    if isinstance(skip_tests, str):
        # A single test name; a plain `in` check against the string would
        # match substrings (e.g. "Name" in "NameFormat").
        return {skip_tests}
    return set(skip_tests)


@dataclass
class _Evaluation:
    """Mutable accumulator for a single rating pass."""

    problems: "list[Problem]" = field(default_factory=list)
    advisories: "list[Problem]" = field(default_factory=list)
    good: int = 0
    bad: int = 0
    fatality: bool = False


def _record_result(
    evaluation: _Evaluation,
    test_name: str,
    result: TestResult,
    *,
    scored: bool,
    category: str = Category.METADATA,
) -> None:
    if not scored:
        # An unscored test contributes nothing to the rating in either
        # direction; only its failures are worth reporting.
        if result.outcome is False:
            evaluation.advisories.append(
                Problem(
                    test=test_name,
                    message=result.message,
                    weight=result.weight,
                    fatal=result.fatal,
                    category=category,
                )
            )
        return
    if result.outcome is False:
        evaluation.problems.append(
            Problem(
                test=test_name,
                message=result.message,
                weight=result.weight,
                fatal=result.fatal,
                category=category,
            )
        )
        if result.fatal:
            evaluation.fatality = True
        else:
            evaluation.bad += result.weight
    elif result.outcome is True and not result.fatal:
        evaluation.good += result.weight
    # If the outcome is None, it's ignored.


def _evaluate_rating_tests(
    data: Metadata,
    test_list: "list[BaseTest]",
    skip_tests: "set[str]",
    evaluation: _Evaluation,
    *,
    strict: bool = False,
) -> None:
    for test in test_list:
        test_name = test.__class__.__name__
        # A test is skippable by its class name or by its whole category.
        if test_name in skip_tests or test.category in skip_tests:
            continue
        scored = strict or not test.advisory
        _record_result(evaluation, test_name, test.test(data), scored=scored, category=test.category)


def _calculate_rating(good: int, bad: int, *, fatality: bool) -> int:
    if fatality:
        # A fatal test failed. That means we give a 0 rating:
        return 0
    if good + bad == 0:
        message = "The configuration skips all tests that contribute to the rating, so no rating can be calculated."
        raise ConfigurationError(message)
    # Multiply good by 9, and add 1 to get a rating between
    # 1: All non-fatal tests failed.
    # 10: All tests succeeded.
    return (good * 9) // (good + bad) + 1


def rate_project(
    data: Metadata,
    skip_tests: "list[str] | str | None" = None,
    *,
    strict: bool = False,
) -> RatedProject:
    """Rate a package, returning a structured RatedProject result.

    Advisory tests are reported separately and excluded from the rating,
    unless ``strict`` is set, in which case they are scored like any other.
    """
    no_config = _no_config_result(data)
    if no_config is not None:
        return no_config

    test_list, problems, initial_fatality = _initial_rating_state(data)
    evaluation = _Evaluation(problems=problems, fatality=initial_fatality)
    _evaluate_rating_tests(data, test_list, _normalized_skip_tests(skip_tests), evaluation, strict=strict)
    rating = _calculate_rating(evaluation.good, evaluation.bad, fatality=evaluation.fatality)
    return RatedProject(
        name=data.get("name"),
        rating=rating,
        level=LEVELS[rating],
        problems=evaluation.problems,
        advisories=evaluation.advisories,
    )


def rate(data: Metadata, skip_tests: "list[str] | str | None" = None) -> "tuple[int, list[str]]":
    """Rate a package, returning a (rating, [problem messages]) tuple.

    This is the backwards-compatible API; rate_project() returns a
    structured result.
    """
    rated = rate_project(data, skip_tests)
    return rated.rating, [problem.message for problem in rated.problems]
