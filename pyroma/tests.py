import contextlib
import copy
import datetime
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import unittest.mock
import warnings
from collections.abc import Callable
from email.message import Message
from importlib.metadata import PackageNotFoundError
from itertools import permutations
from pathlib import Path
from typing import Never
from xmlrpc import client as xmlrpclib

import build
import build.util
from hypothesis import example, given, settings
from hypothesis import strategies as st
from pyproject_hooks import BuildBackendWarning

import pyroma
from pyroma import config, distributiondata, projectdata, pypidata, ratings, report
from pyroma.metadata import Metadata
from pyroma.ratings import rate

_SOURCE_TESTDATA_DIR = Path(__file__).parent / "testdata"
TESTDATA_DIR = _SOURCE_TESTDATA_DIR
PYROMA_ROOT = Path(pyroma.__file__).resolve().parent.parent
_FIXTURE_DIRECTORY: tempfile.TemporaryDirectory[str] | None = None


def setUpModule() -> None:
    """Track copied wheel fixtures in Git so manifest tests need no checkout."""
    global TESTDATA_DIR, _FIXTURE_DIRECTORY  # noqa: PLW0603 - module fixture lifecycle
    directory = tempfile.TemporaryDirectory(prefix="pyroma-fixtures-")
    root = Path(directory.name)
    target = root / "testdata"
    environment = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    environment.update(
        HOME=str(root / "home"),
        XDG_CONFIG_HOME=str(root / "config"),
        GIT_CONFIG_NOSYSTEM="1",
        GIT_ATTR_NOSYSTEM="1",
    )
    try:
        for name in ("home", "config", "templates", "hooks"):
            (root / name).mkdir()
        git_options = [
            "-c",
            f"core.excludesFile={os.devnull}",
            "-c",
            f"core.attributesFile={os.devnull}",
            "-c",
            f"core.hooksPath={root / 'hooks'}",
            "-c",
            "core.autocrlf=false",
        ]
        shutil.copytree(
            _SOURCE_TESTDATA_DIR,
            target,
            ignore=shutil.ignore_patterns("__pycache__", "*.egg-info", "dist", "build", ".DS_Store"),
        )
        subprocess.run(  # noqa: S603 - fixed fixture Git command
            ["git", *git_options, "init", "--quiet", f"--template={root / 'templates'}", str(target)],  # noqa: S607 - fixed Git executable
            env=environment,
            check=True,
        )
        subprocess.run(  # noqa: S603 - fixed fixture Git command
            ["git", *git_options, "-C", str(target), "add", "."],  # noqa: S607 - fixed Git executable
            env=environment,
            check=True,
        )
    except BaseException:
        directory.cleanup()
        raise
    TESTDATA_DIR = target
    _FIXTURE_DIRECTORY = directory


def tearDownModule() -> None:
    """Release the fixture repository and restore the source directory."""
    global TESTDATA_DIR, _FIXTURE_DIRECTORY  # noqa: PLW0603 - module fixture lifecycle
    try:
        if _FIXTURE_DIRECTORY is not None:
            _FIXTURE_DIRECTORY.cleanup()
    finally:
        TESTDATA_DIR = _SOURCE_TESTDATA_DIR
        _FIXTURE_DIRECTORY = None


def _attach_pyproject(testdata: Metadata, path: "Path | str") -> Metadata:
    """Point metadata at a directory the way projectdata would.

    ratings no longer reads from disk, so a test that supplies `_path` must
    also supply the parsed pyproject.toml, or every pyproject check silently
    skips and the test passes while asserting nothing.
    """
    testdata["_path"] = path
    table, error = projectdata.read_pyproject(path)
    if table is not None:
        testdata["_pyproject"] = table
    elif error is not None:
        testdata["_pyproject_error"] = error
    return testdata


long_description = (TESTDATA_DIR / "complete" / "README.txt").read_text(encoding="UTF-8")
# Translate newlines to universal format
long_description = io.StringIO(long_description, newline=None).read()

COMPLETE = {
    "metadata-version": "2.4",
    "name": "complete",
    "version": "1.0.dev1",
    "summary": "This is a test package for pyroma.",
    "description": long_description,
    "description-content-type": "text/plain",
    "classifier": [
        "Development Status :: 6 - Mature",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 2.6",
        "Programming Language :: Python :: 2.7",
        "Programming Language :: Python :: 3.1",
        "Programming Language :: Python :: 3.2",
        "Programming Language :: Python :: 3.3",
    ],
    "dynamic": "license-file",
    "keywords": "pypi,quality,example",
    "author-email": "Lennart Regebro <regebro@gmail.com>",
    "project-url": [
        "repository, https://github.com/regebro/pyroma",
        "homepage, https://github.com/regebro/pyroma",
    ],
    "requires-dist": "zope.event",
    "requires-python": ">=2.6",
    "license-expression": "MIT",
    "license-file": "LICENSE.txt",
}


_METADATA_TEXT = st.text(
    alphabet=st.characters(blacklist_categories=("Cc", "Cs")),
    max_size=80,
)
_CLASSIFIER_TEXT = st.one_of(
    st.sampled_from(
        (
            "Development Status",
            "Programming Language",
            "Programming Language :: Python",
            "Programming Language :: Python :: 3",
            "Programming Language :: Python :: 3.14",
        )
    ),
    _METADATA_TEXT,
)
_STRING_OR_LIST = st.one_of(_METADATA_TEXT, st.lists(_METADATA_TEXT, max_size=4))
_TOML_VALUE = st.recursive(
    st.one_of(_METADATA_TEXT, st.integers(), st.booleans()),
    lambda children: st.one_of(
        st.lists(children, max_size=3),
        st.dictionaries(
            st.sampled_from(("license", "readme", "requires-python", "requires", "build-backend")), children
        ),
    ),
    max_leaves=12,
)
_PYPROJECT = st.one_of(
    st.fixed_dictionaries({}, optional={"project": _TOML_VALUE, "build-system": _TOML_VALUE, "tool": _TOML_VALUE}),
    st.fixed_dictionaries(
        {
            "project": st.fixed_dictionaries(
                {},
                optional={
                    "license": st.sampled_from(
                        ("MIT", "MIT OR Apache-2.0", "(MIT)", "GPL-2.0-only WITH Classpath-exception-2.0")
                    ),
                    "requires-python": st.sampled_from((">=3.11", "==3.11.5", "!=3.11.*", "invalid")),
                    "readme": st.one_of(
                        _METADATA_TEXT, st.fixed_dictionaries({"file": _METADATA_TEXT, "content-type": _METADATA_TEXT})
                    ),
                    "license-files": _STRING_OR_LIST,
                },
            ),
            "build-system": st.fixed_dictionaries(
                {},
                optional={
                    "build-backend": st.sampled_from(
                        ("setuptools.build_meta", "flit_core.buildapi", "hatchling.build", "unknown")
                    ),
                    "requires": st.lists(
                        st.sampled_from(
                            (
                                "setuptools>=61",
                                "setuptools>=77; python_version >= '3.11'",
                                "flit_core>=3.11",
                                "hatchling>=1.27",
                                "invalid requirement",
                            )
                        ),
                        max_size=4,
                    ),
                },
            ),
        }
    ),
)


def _metadata_strategy():
    return st.fixed_dictionaries(
        {},
        optional={
            "metadata-version": _METADATA_TEXT,
            "name": _METADATA_TEXT,
            "version": st.one_of(_METADATA_TEXT, st.integers(), st.none()),
            "summary": _METADATA_TEXT,
            "description": _METADATA_TEXT,
            "description-content-type": _METADATA_TEXT,
            "classifier": st.lists(_CLASSIFIER_TEXT, max_size=4),
            "keywords": _STRING_OR_LIST,
            "author": _METADATA_TEXT,
            "author-email": _METADATA_TEXT,
            "maintainer": _METADATA_TEXT,
            "maintainer-email": _METADATA_TEXT,
            "home-page": _METADATA_TEXT,
            "download-url": _METADATA_TEXT,
            "project-url": st.one_of(
                st.lists(_METADATA_TEXT, max_size=4),
                st.dictionaries(_METADATA_TEXT, _METADATA_TEXT, max_size=4),
            ),
            "requires-dist": st.one_of(_METADATA_TEXT, st.lists(_METADATA_TEXT, max_size=4), st.none()),
            "requires-python": _METADATA_TEXT,
            "requires": _STRING_OR_LIST,
            "provides": _STRING_OR_LIST,
            "obsoletes": _STRING_OR_LIST,
            "license": _METADATA_TEXT,
            "license-expression": _METADATA_TEXT,
            "license-file": _STRING_OR_LIST,
            "dynamic": _STRING_OR_LIST,
            "platform": _STRING_OR_LIST,
            "provides-dist": _STRING_OR_LIST,
            "obsoletes-dist": _STRING_OR_LIST,
            "_sdist": st.booleans(),
            "_owners": st.lists(_METADATA_TEXT, max_size=4),
            "_wheel_build_failed": st.booleans(),
            "_missing_pyproject_toml": st.booleans(),
            "_missing_build_system": st.booleans(),
            "_no_config_found": st.booleans(),
            "_has_sdist": st.booleans(),
            "_py_typed": st.booleans(),
            "_stub_only": st.booleans(),
            "_pyproject": _PYPROJECT,
            "_pyproject_error": _METADATA_TEXT,
            "_license_files_unmatched": st.lists(_METADATA_TEXT, max_size=4),
        },
    )


class MetadataPropertyTest(unittest.TestCase):
    @settings(deadline=None)
    @given(_metadata_strategy())
    @example({"classifier": ["Programming Language"]})
    def test_rate_project_is_total_deterministic_and_non_mutating(self, testdata: Metadata) -> None:
        original = copy.deepcopy(testdata)

        first = ratings.rate_project(testdata)
        second = ratings.rate_project(testdata)

        self.assertEqual(testdata, original)
        self.assertEqual(first, second)
        self.assertEqual(first.name, testdata.get("name"))
        self.assertGreaterEqual(first.rating, 0)
        self.assertLessEqual(first.rating, 10)
        self.assertEqual(first.level, ratings.LEVELS[first.rating])
        for problem in [*first.problems, *first.advisories]:
            self.assertTrue(problem.test)
            self.assertTrue(problem.message)
            self.assertGreaterEqual(problem.weight, 0)
            self.assertIsInstance(problem.fatal, bool)

        # Strict mode scores the advisory checks; it must be just as total.
        strict = ratings.rate_project(testdata, strict=True)
        self.assertEqual(strict, ratings.rate_project(testdata, strict=True))
        self.assertEqual(testdata, original)
        self.assertEqual(strict.advisories, [])
        self.assertGreaterEqual(strict.rating, 0)
        self.assertLessEqual(strict.rating, 10)


class RatingsTest(unittest.TestCase):
    maxDiff = None

    def _get_file_rating(
        self,
        dirname: str,
        skip_tests: "list[str] | str | None" = None,
    ) -> "tuple[int, list[str]]":
        directory = TESTDATA_DIR / dirname
        if dirname in {"custom_test", "pep517", "private_classifier", "setup_config"}:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=BuildBackendWarning)
                data = projectdata.get_data(directory)
        else:
            data = projectdata.get_data(directory)
        return rate(data, skip_tests)

    def test_complete(self) -> None:
        data = projectdata.get_data(TESTDATA_DIR / "complete")
        rating = rate(data)
        # Should have a perfect score
        self.assertEqual(rating, (10, []))

    def test_setup_config(self) -> None:
        rating = self._get_file_rating("setup_config")
        self.assertEqual(
            rating,
            (
                8,
                [
                    "Your project does not have a pyproject.toml file, which is highly recommended.\n"
                    "You probably want to create one declaring your build backend, for example:\n\n"
                    "    [build-system]\n"
                    '    requires = ["setuptools>=77"]\n'
                    '    build-backend = "setuptools.build_meta"\n\n'
                    "Any PEP 517 build backend works, for example flit_core, hatchling or uv_build.\n"
                    "See https://packaging.python.org for more information on how to package your project.",
                    "Using license classifiers is deprecated in favour of the license-expression field.",
                    "The metadata field 'home-page' is deprecated; use 'project-url' instead.",
                ],
            ),
        )

    def test_only_config(self) -> None:
        # There is no legacy setup.py, nor a modern pyproject.toml, so there
        # is no way to build this project and no metadata to extract. Only
        # the build system problems are reported.
        rating = self._get_file_rating("only_config")

        self.assertEqual(
            rating,
            (
                1,
                [
                    "You seem to neither have a setup.py, nor a pyproject.toml, only setup.cfg.\n"
                    "This makes it unclear how your project should be built, and some packaging "
                    "tools may fail.\n"
                    "See https://packaging.python.org for more information on how to package your project.",
                    "Your project does not have a pyproject.toml file, which is highly "
                    "recommended.\n"
                    "You probably want to create one declaring your build backend, for example:\n\n"
                    "    [build-system]\n"
                    '    requires = ["setuptools>=77"]\n'
                    '    build-backend = "setuptools.build_meta"\n\n'
                    "Any PEP 517 build backend works, for example flit_core, hatchling or uv_build.\n"
                    "See https://packaging.python.org for more information on how to package your project.",
                ],
            ),
        )

    def test_skip_tests(self) -> None:
        # Find all errors
        all_errors = self._get_file_rating("lacking")[1]

        fewer_errors = self._get_file_rating(
            "lacking", skip_tests=["PythonRequiresVersion", "Description", "Summary", "Classifiers"]
        )[1]

        self.assertEqual(len(all_errors), 13)
        # Errors have been skipped!
        self.assertEqual(len(fewer_errors), 9)

    def test_pep517(self) -> None:
        rating = self._get_file_rating("pep517")
        self.assertGreaterEqual(rating[0], 9)

    def test_pep621(self) -> None:
        rating = self._get_file_rating("pep621")
        self.assertGreaterEqual(rating[0], 9)

    def test_uv_build(self) -> None:
        rating = self._get_file_rating("uv_build")
        self.assertGreaterEqual(rating[0], 9)

    def test_minimal(self) -> None:
        rating = self._get_file_rating("minimal")
        self.assertEqual(
            rating,
            (
                2,
                [
                    "The package's Summary should be longer than 10 characters.",
                    "The package's Description is quite short.",
                    "Your package does not have classifier data.",
                    "The classifiers should specify what Python versions you support. "
                    "You can find the list of standard classifiers here: https://pypi.org/classifiers/",
                    "You should specify what Python versions you support with the 'Requires-Python' metadata.",
                    "Your package does not have keywords data.",
                    "Your package does not have author data.",
                    "Your package does not have author-email data.",
                    "Your package should include links to the project home page and other resources. "
                    "Add them to the [project.urls] table in your pyproject.toml, using well-known labels "
                    "such as Homepage, Source, Documentation, Changelog or Issues.",
                    "You should specify a license for your package with the 'License-Expression' field. See "
                    "https://packaging.python.org/en/latest/specifications/core-metadata/#license-expression "
                    "for more information.",
                    "Specifying a development status in the classifiers gives users "
                    "a hint of how stable your software is. See https://pypi.org/classifiers/",
                    "Check-manifest returned errors",
                ],
            ),
        )

    def test_lacking(self) -> None:
        rating = self._get_file_rating("lacking")

        self.assertEqual(
            rating,
            (
                0,
                [
                    "Your project does not have a pyproject.toml file, which is highly recommended.\n"
                    "You probably want to create one declaring your build backend, for example:\n\n"
                    "    [build-system]\n"
                    '    requires = ["setuptools>=77"]\n'
                    '    build-backend = "setuptools.build_meta"\n\n'
                    "Any PEP 517 build backend works, for example flit_core, hatchling or uv_build.\n"
                    "See https://packaging.python.org for more information on how to package your project.",
                    "The package had no Summary!",
                    "The package's Description is quite short.",
                    "Your package does not have classifier data.",
                    "The classifiers should specify what Python versions you support. "
                    "You can find the list of standard classifiers here: https://pypi.org/classifiers/",
                    "You should specify what Python versions you support with the 'Requires-Python' metadata.",
                    "Your package does not have keywords data.",
                    "Your package does not have author data.",
                    "Your package does not have author-email data.",
                    "Your package should include links to the project home page and other resources. "
                    "Add them to the [project.urls] table in your pyproject.toml, using well-known labels "
                    "such as Homepage, Source, Documentation, Changelog or Issues.",
                    "You should specify a license for your package with the 'License-Expression' field. See "
                    "https://packaging.python.org/en/latest/specifications/core-metadata/#license-expression "
                    "for more information.",
                    "Your Description is not valid ReST: \n<string>:1: (WARNING/2) Inline literal "
                    "start-string without end-string.",
                    "Specifying a development status in the classifiers gives users "
                    "a hint of how stable your software is. See https://pypi.org/classifiers/",
                ],
            ),
        )

    def test_custom_test(self) -> None:
        rating = self._get_file_rating("custom_test")

        self.assertEqual(
            rating,
            (
                4,
                [
                    "The package's Summary should be longer than 10 characters.",
                    "The package's Description is quite short.",
                    "Your package does not have classifier data.",
                    "The classifiers should specify what Python versions you support. "
                    "You can find the list of standard classifiers here: https://pypi.org/classifiers/",
                    "You should specify what Python versions you support with the 'Requires-Python' metadata.",
                    "Your package does not have keywords data.",
                    "Your package does not have author data.",
                    "Your package does not have author-email data.",
                    "Your package should include links to the project home page and other resources. "
                    "Add them to the [project.urls] table in your pyproject.toml, using well-known labels "
                    "such as Homepage, Source, Documentation, Changelog or Issues.",
                    "You should specify a license for your package with the 'License-Expression' field. See "
                    "https://packaging.python.org/en/latest/specifications/core-metadata/#license-expression "
                    "for more information.",
                    "Specifying a development status in the classifiers gives users "
                    "a hint of how stable your software is. See https://pypi.org/classifiers/",
                ],
            ),
        )

    def test_private_classifier(self) -> None:
        rating = self._get_file_rating("private_classifier")
        self.assertGreaterEqual(rating[0], 9)

    def test_invalid_pyproject(self) -> None:
        # Use valid metadata so we exercise the rating check itself,
        # then point _path to a fixture with an invalid pyproject.toml.
        testdata = COMPLETE.copy()
        _attach_pyproject(testdata, TESTDATA_DIR / "invalid_pyproject")

        rating = rate(testdata)
        self.assertLess(rating[0], 10)
        self.assertTrue(any("pyproject.toml is invalid" in msg for msg in rating[1]))

    def test_markdown(self) -> None:
        # Markdown and text shouldn't get ReST errors
        testdata = COMPLETE.copy()
        testdata["description"] = "# Broken ReST\n\n``Valid  Markdown\n"
        testdata["description-content-type"] = "text/markdown"

        rating = rate(testdata)
        self.assertEqual(rating, (9, ["The package's Description is quite short."]))

        testdata["description-content-type"] = "text/plain"
        rating = rate(testdata)
        self.assertEqual(rating, (9, ["The package's Description is quite short."]))

    def test_deprecated_metadata_field_warning(self) -> None:
        testdata = COMPLETE.copy()
        testdata.pop("project-url", None)
        testdata["home-page"] = "https://example.com"

        rating = rate(testdata)

        self.assertTrue(
            any("The metadata field 'home-page' is deprecated; use 'project-url' instead." in msg for msg in rating[1])
        )

    def test_deprecated_license_warning_respects_metadata_version(self) -> None:
        old_metadata = COMPLETE.copy()
        old_metadata["metadata-version"] = "2.3"
        old_metadata["license"] = "MIT"
        old_metadata.pop("license-expression", None)

        old_rating = rate(old_metadata)
        self.assertFalse(any("The metadata field 'license' is deprecated" in msg for msg in old_rating[1]))

        new_metadata = COMPLETE.copy()
        new_metadata["metadata-version"] = "2.4"
        new_metadata["license"] = "MIT"
        new_metadata.pop("license-expression", None)

        new_rating = rate(new_metadata)
        self.assertTrue(any("The metadata field 'license' is deprecated" in msg for msg in new_rating[1]))


class SpecComplianceTest(unittest.TestCase):
    """Tests for the strict, spec-grounded rating checks."""

    maxDiff = None

    def _messages(self, testdata: Metadata) -> "list[str]":
        return rate(testdata)[1]

    def test_invalid_version(self) -> None:
        testdata = COMPLETE.copy()
        testdata["version"] = "1.0-foo"

        rating = rate(testdata)
        self.assertLess(rating[0], 10)
        self.assertTrue(any("'1.0-foo' is not a valid version" in msg for msg in rating[1]))

    def test_noncanonical_version(self) -> None:
        testdata = COMPLETE.copy()
        testdata["version"] = "1.0.DEV1"

        messages = self._messages(testdata)
        self.assertTrue(any("not in canonical form; it should be written as '1.0.dev1'" in msg for msg in messages))

    def test_version_epoch_and_local_segment(self) -> None:
        testdata = COMPLETE.copy()
        testdata["version"] = "1!1.0+ubuntu1"

        messages = self._messages(testdata)
        self.assertTrue(any("uses a version epoch" in msg for msg in messages))
        self.assertTrue(any("contains a local version segment" in msg for msg in messages))

    def test_invalid_metadata_version(self) -> None:
        testdata = COMPLETE.copy()
        testdata["metadata-version"] = "2.0"

        messages = self._messages(testdata)
        self.assertTrue(any("'2.0' is not a valid Metadata-Version" in msg for msg in messages))

    def test_invalid_name_is_fatal(self) -> None:
        testdata = COMPLETE.copy()
        testdata["name"] = "-not-valid-"

        rating = rate(testdata)
        self.assertEqual(rating[0], 0)
        self.assertTrue(any("'-not-valid-' is not a valid project name" in msg for msg in rating[1]))

    def test_license_and_license_expression_is_fatal(self) -> None:
        testdata = COMPLETE.copy()
        testdata["license"] = "MIT"

        rating = rate(testdata)
        self.assertEqual(rating[0], 0)
        self.assertTrue(
            any("Specifying both a License and a License-Expression is forbidden" in msg for msg in rating[1])
        )

    def test_invalid_license_expression_is_fatal(self) -> None:
        testdata = COMPLETE.copy()
        testdata["license-expression"] = "Bogus-License"

        rating = rate(testdata)
        self.assertEqual(rating[0], 0)
        self.assertTrue(any("'Bogus-License' is not a valid SPDX license expression" in msg for msg in rating[1]))

    def test_invalid_description_content_type(self) -> None:
        testdata = COMPLETE.copy()
        testdata["description-content-type"] = "text/html"

        messages = self._messages(testdata)
        self.assertTrue(any("The content type should be one of" in msg for msg in messages))

    def test_description_content_type_parameters(self) -> None:
        testdata = COMPLETE.copy()
        testdata["description-content-type"] = "text/plain; charset=latin-1; variant=GFM"

        messages = self._messages(testdata)
        message = next(msg for msg in messages if "Description-Content-Type" in msg)
        self.assertIn("The only accepted charset is UTF-8, not 'latin-1'.", message)
        self.assertIn("The 'variant' parameter is only valid for text/markdown.", message)

    def test_markdown_variant(self) -> None:
        testdata = COMPLETE.copy()
        testdata["description-content-type"] = "text/markdown; variant=GFM"
        self.assertFalse(any("Description-Content-Type" in msg for msg in self._messages(testdata)))

        testdata["description-content-type"] = "text/markdown; variant=Pandoc"
        messages = self._messages(testdata)
        self.assertTrue(
            any("The markdown variant should be GFM or CommonMark, not 'Pandoc'." in msg for msg in messages)
        )

    def test_invalid_requires_dist(self) -> None:
        testdata = COMPLETE.copy()
        testdata["requires-dist"] = ["zope.event", "broken =="]

        messages = self._messages(testdata)
        self.assertTrue(any("Requires-Dist entry #2 is not a valid dependency specifier." in msg for msg in messages))
        self.assertFalse(any("broken ==" in msg for msg in messages))

    def test_requires_dist_style_warnings(self) -> None:
        testdata = COMPLETE.copy()
        testdata["requires-dist"] = [
            "zope.event (>=4.0)",
            'zope.interface; sys_platform > "linux"',
        ]

        messages = self._messages(testdata)
        message = next(msg for msg in messages if "Requires-Dist" in msg)
        self.assertIn("puts the version specifier in parentheses", message)
        self.assertIn("ordered comparison on the 'sys_platform' environment marker", message)

    def test_url_label_too_long_is_fatal(self) -> None:
        testdata = COMPLETE.copy()
        testdata["project-url"] = ["this label is far too long to be legal, https://example.com"]

        rating = rate(testdata)
        self.assertEqual(rating[0], 0)
        self.assertTrue(any("Project-URL labels are limited to 32 characters" in msg for msg in rating[1]))

    def test_url_no_well_known_labels(self) -> None:
        testdata = COMPLETE.copy()
        testdata["project-url"] = ["weird stuff, https://example.com"]

        messages = self._messages(testdata)
        self.assertTrue(any("None of your Project-URL labels match the well-known labels" in msg for msg in messages))

    def test_console_scripts_in_entry_points_is_fatal(self) -> None:
        testdata = COMPLETE.copy()
        _attach_pyproject(testdata, TESTDATA_DIR / "bad_console_scripts")

        rating = rate(testdata)
        self.assertEqual(rating[0], 0)
        self.assertTrue(any("Console and GUI scripts must be defined in [project.scripts]" in msg for msg in rating[1]))

    def test_readme_both_file_and_text_is_fatal(self) -> None:
        testdata = COMPLETE.copy()
        _attach_pyproject(testdata, TESTDATA_DIR / "readme_both")

        rating = rate(testdata)
        self.assertEqual(rating[0], 0)
        self.assertTrue(any("The 'readme' table must not specify both 'file' and 'text'." in msg for msg in rating[1]))

    def test_dynamic_name_is_fatal(self) -> None:
        testdata = COMPLETE.copy()
        _attach_pyproject(testdata, TESTDATA_DIR / "dynamic_name")

        rating = rate(testdata)
        self.assertEqual(rating[0], 0)
        self.assertTrue(
            any("The 'name' key must be static, it must never be listed in 'dynamic'." in msg for msg in rating[1])
        )
        self.assertTrue(
            any(
                "The 'version' key must either be set statically or be listed in 'dynamic'." in msg for msg in rating[1]
            )
        )


class BugFixTest(unittest.TestCase):
    """Regression tests for behavior bugs found in review."""

    maxDiff = None

    def test_valid_rest_ignores_content_type_parameters(self) -> None:
        # A parameterized content type such as "text/markdown; charset=UTF-8"
        # must not fall through to ReST validation of a non-ReST description.
        testdata = COMPLETE.copy()
        testdata["description"] = "# Broken ReST\n\n``This is markdown, not ReST\n" + "x" * 100
        for content_type in ("text/markdown; charset=UTF-8", "text/plain; charset=UTF-8"):
            testdata["description-content-type"] = content_type
            messages = rate(testdata)[1]
            self.assertFalse(any("not valid ReST" in msg for msg in messages), content_type)

    def test_valid_rest_still_checks_rst_with_parameters(self) -> None:
        testdata = COMPLETE.copy()
        testdata["description"] = "``broken inline literal\n" + "x" * 100
        testdata["description-content-type"] = "text/x-rst; charset=UTF-8"
        messages = rate(testdata)[1]
        self.assertTrue(any("not valid ReST" in msg for msg in messages))

    @unittest.mock.patch("pyroma.ratings.publish_parts")
    def test_valid_rest_fails_on_system_message_without_warning_output(
        self,
        publishmock: unittest.mock.Mock,
    ) -> None:
        system_message = unittest.mock.Mock()
        system_message.astext.return_value = "severe parsing failure"
        publishmock.side_effect = ratings.SystemMessage(system_message, 4)

        result = ratings.ValidREST().test(
            {
                "description": "broken",
                "description-content-type": "text/x-rst",
            }
        )

        self.assertIs(result.outcome, False)
        self.assertIn("severe parsing failure", result.message)

    def test_bus_factor_zero_owners_fails(self) -> None:
        result = ratings.BusFactor().test({"_owners": []})
        self.assertIs(result.outcome, False)
        self.assertEqual(result.weight, 100)

    def test_skip_tests_exact_match_only(self) -> None:
        # Skipping NameFormat must not also skip the Name test through
        # substring matching when skip_tests is passed as a plain string.
        testdata = COMPLETE.copy()
        del testdata["name"]
        for skip in (["NameFormat"], "NameFormat"):
            rating, messages = rate(testdata, skip_tests=skip)
            self.assertEqual(rating, 0, skip)
            self.assertTrue(any("does not have name data" in msg for msg in messages), skip)

    def test_rate_project_all_tests_skipped_raises_configuration_error(self) -> None:
        testdata = COMPLETE.copy()
        with self.assertRaises(ratings.ConfigurationError):
            rate(testdata, skip_tests=pyroma.get_all_tests())

    def test_check_manifest_skips_sdist(self) -> None:
        # check-manifest needs a VCS checkout; an unpacked sdist is not one.
        result = ratings.CheckManifest().test({"_path": ".", "_sdist": True})
        self.assertIsNone(result.outcome)

    def test_parse_tests_trailing_separator(self) -> None:
        self.assertEqual(pyroma.parse_tests("Description,"), ["Description"])
        self.assertEqual(pyroma.parse_tests("Description, Summary;"), ["Description", "Summary"])

    def test_tb2_distribution(self) -> None:
        src = TESTDATA_DIR / "distributions" / "complete-1.0.dev1.tar.bz2"
        with tempfile.TemporaryDirectory() as tmp:
            tb2 = Path(tmp) / "complete-1.0.dev1.tb2"
            shutil.copyfile(src, tb2)
            data = distributiondata.get_data(tb2)
        try:
            self.assertEqual(data.get("name"), "complete")
        finally:
            distributiondata.cleanup(data)

    def test_no_config_found_in_empty_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = projectdata.get_data(tmp)
        self.assertIn("_no_config_found", data)

    def test_malformed_dynamic_value_returns_validation_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "pyproject.toml").write_text(
                '[project]\nname = "example"\nversion = "1.0"\ndynamic = 42\n',
                encoding="UTF-8",
            )
            testdata = COMPLETE.copy()
            _attach_pyproject(testdata, tmp)

            messages = rate(testdata)[1]

        self.assertTrue(any("pyproject.toml is invalid" in message for message in messages))

    def test_user_facing_diagnostics_have_word_boundaries(self) -> None:
        major_only = ratings.PythonClassifierVersion().test({"classifier": ["Programming Language :: Python :: 3"]})
        no_python = ratings.PythonClassifierVersion().test({"classifier": []})
        missing_build = ratings.MissingBuildSystem().test({"_missing_build_system": True})
        no_config = ratings.rate_project({"_no_config_found": True})

        self.assertIn("major version. You", major_only.message)
        self.assertIn("support. You", no_python.message)
        self.assertIn("may fail.\nSee", missing_build.message)
        self.assertIn("Are you checking", no_config.problems[0].message)


class CLITest(unittest.TestCase):
    """Exercise the `pyroma` command through `main()`."""

    def _run_cli(self, *argv: str) -> "tuple[int, str, str]":
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            unittest.mock.patch.object(sys, "argv", ["pyroma", *argv]),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit) as raised,
        ):
            pyroma.main()
        return int(raised.exception.code or 0), stdout.getvalue(), stderr.getvalue()

    @staticmethod
    def _write_malformed_project(directory: str) -> None:
        (Path(directory) / "pyproject.toml").write_text("[project\n", encoding="UTF-8")

    def test_malformed_pyproject_json_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._write_malformed_project(tmp)
            code, out, err = self._run_cli("--directory", "--format", "json", tmp)

        self.assertEqual(code, 3)
        self.assertNotIn("Traceback", err)
        document = json.loads(out)
        self.assertEqual(document["error"]["type"], "BuildError")
        self.assertIn("pyproject.toml", document["error"]["message"])
        self.assertEqual(document["_meta"]["mode"], "directory")
        self.assertEqual(document["_meta"]["pyroma"], pyroma.package_version("pyroma621"))

    def test_malformed_pyproject_text_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._write_malformed_project(tmp)
            code, _out, err = self._run_cli("--directory", tmp)

        self.assertEqual(code, 3)
        self.assertNotIn("Traceback", err)
        self.assertIn("Error: Failed to parse", err)
        self.assertIn("pyproject.toml", err)

    def test_malformed_pyproject_in_sdist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "broken-1.0"
            project.mkdir()
            self._write_malformed_project(str(project))
            sdist = Path(tmp) / "broken-1.0.tar.gz"
            with tarfile.open(sdist, "w:gz") as tar:
                tar.add(project, arcname=project.name)
            code, out, err = self._run_cli("--file", "--format", "json", str(sdist))

        self.assertEqual(code, 3)
        self.assertNotIn("Traceback", err)
        self.assertEqual(json.loads(out)["error"]["type"], "BuildError")

    def test_json_report_includes_version(self) -> None:
        code, out, _err = self._run_cli("--directory", "--format", "json", str(TESTDATA_DIR / "complete"))

        self.assertEqual(code, 0)
        document = json.loads(out)
        self.assertEqual(document["rating"], 10)
        self.assertEqual(document["_meta"]["pyroma"], pyroma.package_version("pyroma621"))

    def test_json_error_includes_version(self) -> None:
        code, out, _err = self._run_cli(
            "--directory",
            "--format",
            "json",
            "--skip-tests",
            ",".join(pyroma.get_all_tests()),
            str(TESTDATA_DIR / "complete"),
        )

        self.assertEqual(code, 3)
        document = json.loads(out)
        self.assertEqual(document["error"]["type"], "ConfigurationError")
        self.assertEqual(document["_meta"]["pyroma"], pyroma.package_version("pyroma621"))

    def test_json_meta_queries_the_installed_distribution(self) -> None:
        with unittest.mock.patch.object(pyroma, "package_version", return_value="1.2.3") as lookup:
            meta = pyroma._json_meta("directory", ".")

        lookup.assert_called_once_with("pyroma621")
        self.assertEqual(meta, {"package": ".", "mode": "directory", "pyroma": "1.2.3"})

    def test_json_meta_without_installed_distribution(self) -> None:
        with unittest.mock.patch.object(pyroma, "package_version", side_effect=PackageNotFoundError):
            meta = pyroma._json_meta("directory", ".")

        self.assertEqual(meta, {"package": ".", "mode": "directory"})


class BuildFailureTest(unittest.TestCase):
    """Map build-frontend failures to errors or the wheel-build-failed marker."""

    def _metadata_with(self, error: Exception) -> Metadata:
        with unittest.mock.patch.object(build.util, "project_wheel_metadata", side_effect=error):
            return projectdata.build_metadata(".", isolated=True)

    def test_backend_exception_is_not_fatal(self) -> None:
        data = self._metadata_with(build.BuildBackendException("Backend 'nope' is not available."))
        self.assertEqual(data, {"_wheel_build_failed": True})

    def test_isolated_install_failure_is_not_fatal(self) -> None:
        data = self._metadata_with(subprocess.CalledProcessError(1, ["pip", "install"]))
        self.assertEqual(data, {"_wheel_build_failed": True})

    def test_build_exception_becomes_build_error(self) -> None:
        with self.assertRaises(projectdata.BuildError) as raised:
            self._metadata_with(build.BuildException("Failed to parse pyproject.toml"))
        self.assertEqual(str(raised.exception), "Failed to parse pyproject.toml")

    def test_retry_in_isolation_handles_build_exception(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "pyproject.toml").write_text("[project\n", encoding="UTF-8")
            with self.assertRaises(projectdata.BuildError):
                projectdata.get_data(tmp)


class ReportTest(unittest.TestCase):
    maxDiff = None

    def _rated(self):
        return ratings.RatedProject(
            name="example",
            rating=9,
            level=ratings.LEVELS[9],
            problems=[
                ratings.Problem(
                    test="Description",
                    message="The package's Description is quite short.",
                    weight=50,
                    fatal=False,
                    category="metadata",
                )
            ],
        )

    def test_format_text(self) -> None:
        self.assertEqual(
            report.format_text(self._rated()),
            "------------------------------\n"
            "The package's Description is quite short.\n"
            "------------------------------\n"
            "Final rating: 9/10\n"
            "Cottage Cheese\n"
            "------------------------------",
        )

    def test_format_text_no_problems(self) -> None:
        rated = ratings.RatedProject(name="example", rating=10, level=ratings.LEVELS[10], problems=[])
        self.assertEqual(
            report.format_text(rated),
            "------------------------------\n"
            "Final rating: 10/10\n"
            "Your cheese is so fresh most people think it's a cream: Mascarpone\n"
            "------------------------------",
        )

    def test_format_json_error(self) -> None:
        error = ratings.ConfigurationError("nothing to rate")
        document = json.loads(report.format_json_error(error, meta={"mode": "directory"}))
        self.assertEqual(
            document,
            {
                "error": {"type": "ConfigurationError", "message": "nothing to rate"},
                "_meta": {"mode": "directory"},
            },
        )

    def test_format_json(self) -> None:
        document = json.loads(report.format_json(self._rated(), meta={"mode": "directory"}))
        self.assertEqual(
            document,
            {
                "name": "example",
                "rating": 9,
                "level": "Cottage Cheese",
                "problems": [
                    {
                        "test": "Description",
                        "message": "The package's Description is quite short.",
                        "weight": 50,
                        "fatal": False,
                        "category": "metadata",
                    }
                ],
                "advisories": [],
                "_meta": {"mode": "directory"},
            },
        )


class _AlwaysPasses(ratings.BaseTest):
    weight = 100

    def test(self, _data: Metadata) -> ratings.TestResult:
        return self._passed()


class _AlwaysFailsAdvisory(ratings.BaseTest):
    weight = 100
    advisory = True

    def test(self, _data: Metadata) -> ratings.TestResult:
        return self._failed("advisory finding")


class AdvisoryTest(unittest.TestCase):
    maxDiff = None

    def _rate(self, *, strict: bool = False) -> ratings.RatedProject:
        stand_ins: list[ratings.BaseTest] = [_AlwaysPasses(), _AlwaysFailsAdvisory()]
        with unittest.mock.patch.object(ratings, "ALL_TESTS", stand_ins):
            return ratings.rate_project({"name": "example"}, strict=strict)

    def test_advisory_failure_does_not_affect_the_rating(self) -> None:
        # The whole point of the advisory tier: a new check can never move an
        # existing package's score.
        rated = self._rate()

        self.assertEqual(rated.rating, 10)
        self.assertEqual(rated.problems, [])
        self.assertEqual([advisory.message for advisory in rated.advisories], ["advisory finding"])

    def test_strict_scores_advisory_failures(self) -> None:
        rated = self._rate(strict=True)

        self.assertEqual(rated.advisories, [])
        self.assertEqual([problem.message for problem in rated.problems], ["advisory finding"])
        # 100 good against 100 bad: (100 * 9) // 200 + 1
        self.assertEqual(rated.rating, 5)

    def test_rate_tuple_api_never_reports_advisories(self) -> None:
        # rate() is the documented backwards-compatible API; advisories must
        # not leak into its message list.
        stand_ins: list[ratings.BaseTest] = [_AlwaysPasses(), _AlwaysFailsAdvisory()]
        with unittest.mock.patch.object(ratings, "ALL_TESTS", stand_ins):
            self.assertEqual(rate({"name": "example"}), (10, []))

    def test_no_advisory_check_is_fatal(self) -> None:
        # Under --strict an advisory failure must lower the rating by its
        # weight, never zero it; see BaseTest.advisory.
        offenders = [test.__class__.__name__ for test in ratings.ALL_TESTS if test.advisory and test.fatal]
        self.assertEqual(offenders, [])

    def _advisory_rating(self) -> ratings.RatedProject:
        return ratings.RatedProject(
            name="example",
            rating=10,
            level=ratings.LEVELS[10],
            problems=[],
            advisories=[
                ratings.Problem(
                    test="Example",
                    message="first line\nsecond line",
                    weight=20,
                    fatal=False,
                    category="practice",
                )
            ],
        )

    def test_format_text_renders_the_advisory_section(self) -> None:
        text = report.format_text(self._advisory_rating())

        self.assertIn("Advisory (not scored, 1 finding) - run --strict to score these:", text)
        # Multi-line messages are indented in full, not just their first line.
        self.assertIn("  first line\n  second line", text)

    def test_format_text_can_suppress_advisories(self) -> None:
        text = report.format_text(self._advisory_rating(), show_advisories=False)

        self.assertNotIn("Advisory", text)
        # Suppressing them restores byte-identical traditional output.
        self.assertEqual(
            text,
            report.format_text(ratings.RatedProject(name="example", rating=10, level=ratings.LEVELS[10], problems=[])),
        )

    def test_format_json_always_includes_advisories(self) -> None:
        document = json.loads(report.format_json(self._advisory_rating()))

        self.assertEqual(
            document["advisories"],
            [
                {
                    "test": "Example",
                    "message": "first line\nsecond line",
                    "weight": 20,
                    "fatal": False,
                    "category": "practice",
                }
            ],
        )


class AdvisoryChecksTest(unittest.TestCase):
    maxDiff = None

    def _advisories(self, testdata: Metadata) -> "list[str]":
        return [advisory.message for advisory in ratings.rate_project(testdata).advisories]

    def _assert_advises(self, testdata: Metadata, fragment: str) -> None:
        messages = self._advisories(testdata)
        self.assertTrue(
            any(fragment in message for message in messages),
            f"no advisory contained {fragment!r}; got {messages}",
        )

    def _assert_silent(self, testdata: Metadata, fragment: str) -> None:
        self.assertFalse(any(fragment in message for message in self._advisories(testdata)))

    def _assert_fatal(self, testdata: Metadata, fragment: str) -> None:
        """Assert a scored, fatal finding: it zeroes the rating and is no advisory."""
        rating, messages = rate(testdata)
        self.assertEqual(rating, 0)
        self.assertTrue(
            any(fragment in message for message in messages),
            f"no problem contained {fragment!r}; got {messages}",
        )
        self._assert_silent(testdata, fragment)

    def test_direct_url_dependency(self) -> None:
        # Indices reject the upload outright, so this graduated from advisory
        # to scored and fatal.
        testdata = COMPLETE.copy()
        testdata["requires-dist"] = ["zope.event @ https://example.com/zope.event-1.0.tar.gz"]

        self._assert_fatal(testdata, "declares direct URL dependencies")

    def test_direct_url_dependency_is_not_also_reported_as_floorless(self) -> None:
        testdata = COMPLETE.copy()
        testdata["requires-dist"] = ["zope.event @ https://example.com/zope.event-1.0.tar.gz"]

        self._assert_silent(testdata, "have no lower bound")

    def test_provides_dist_urls_are_not_dependencies(self) -> None:
        # Provides-Dist and Obsoletes-Dist are not dependency fields, and
        # indices do not reject URLs in them.
        testdata = COMPLETE.copy()
        testdata["provides-dist"] = ["zope.event @ https://example.com/zope.event-1.0.tar.gz"]
        testdata["obsoletes-dist"] = ["zope.old @ https://example.com/zope.old-1.0.tar.gz"]

        rating, messages = rate(testdata)
        self.assertEqual(rating, 10)
        self.assertFalse(any("direct URL" in message for message in messages))
        self._assert_silent(testdata, "direct URL")

    def test_summary_must_be_a_single_line(self) -> None:
        testdata = COMPLETE.copy()
        testdata["summary"] = "First line.\nSecond line."

        self._assert_fatal(testdata, "must be a single line")

    def test_summary_length_limit(self) -> None:
        testdata = COMPLETE.copy()
        testdata["summary"] = "x" * 513

        self._assert_fatal(testdata, "characters long, but package indices limit")

    def test_project_url_labels_colliding_after_normalization(self) -> None:
        testdata = COMPLETE.copy()
        testdata["project-url"] = ["Repository, https://example.com/a", "Source, https://example.com/b"]

        self._assert_advises(testdata, "both normalize to 'source'")

    def test_distinct_project_url_labels_do_not_collide(self) -> None:
        # COMPLETE uses "repository" and "homepage", which are different links.
        self._assert_silent(COMPLETE.copy(), "normalize to")

    def test_requires_python_upper_bound(self) -> None:
        testdata = COMPLETE.copy()
        testdata["requires-python"] = ">=3.11,<4.0"

        self._assert_advises(testdata, "has an upper bound")

    def test_capped_runtime_dependency(self) -> None:
        testdata = COMPLETE.copy()
        testdata["requires-dist"] = ["requests>=2,<3"]

        self._assert_advises(testdata, "cap or pin their versions")

    def test_extras_are_not_treated_as_runtime_dependencies(self) -> None:
        testdata = COMPLETE.copy()
        testdata["requires-dist"] = ['pytest<9; extra == "test"']

        self._assert_silent(testdata, "cap or pin their versions")

    def test_marker_values_containing_extra_are_runtime_dependencies(self) -> None:
        # Only the ``extra`` variable marks an optional dependency; the word
        # appearing inside a quoted value means nothing.
        testdata = COMPLETE.copy()
        testdata["requires-dist"] = [
            'alpha; platform_system == "extraterrestrial"',
            "beta; os_name == 'extra'",
        ]

        message = next(m for m in self._advisories(testdata) if "have no lower bound" in m)
        self.assertIn("alpha, beta", message)

    def test_floorless_runtime_dependency(self) -> None:
        testdata = COMPLETE.copy()
        testdata["requires-dist"] = ["zope.event"]

        self._assert_advises(testdata, "have no lower bound")

    def test_relative_markdown_links(self) -> None:
        testdata = COMPLETE.copy()
        testdata["description-content-type"] = "text/markdown"
        testdata["description"] = "![badge](docs/badge.svg) and see [the docs](docs/index.md)."

        message = next(m for m in self._advisories(testdata) if "relative paths" in m)
        self.assertIn("docs/badge.svg", message)
        self.assertIn("docs/index.md", message)

    def test_absolute_links_are_not_flagged(self) -> None:
        testdata = COMPLETE.copy()
        testdata["description-content-type"] = "text/markdown"
        testdata["description"] = "![badge](https://example.com/badge.svg)"

        self._assert_silent(testdata, "relative paths")

    def test_malformed_link_targets_do_not_crash(self) -> None:
        # urlsplit raises on an unclosed IPv6 bracket; rating must stay total.
        for content_type, description in (
            ("text/x-rst", "See `docs <http://[::1>`_ for details."),
            ("text/markdown", "See [the docs](http://[::1) for details."),
        ):
            with self.subTest(content_type=content_type):
                testdata = COMPLETE.copy()
                testdata["description-content-type"] = content_type
                testdata["description"] = description

                self._assert_silent(testdata, "relative paths")

    def test_classifiers_contradicting_requires_python(self) -> None:
        testdata = COMPLETE.copy()
        # COMPLETE's classifiers name Python 2.6 through 3.3.
        testdata["requires-python"] = ">=3.12"

        self._assert_advises(testdata, "but your Requires-Python")

    def test_patch_level_requires_python_floor_is_coherent(self) -> None:
        # ">=3.8.1" admits 3.8.1 and later, so a 3.8 classifier is honest.
        testdata = COMPLETE.copy()
        testdata["classifier"] = [
            "Programming Language :: Python :: 3.8",
            "Programming Language :: Python :: 3.11",
        ]
        for requires_python in (">=3.8.1", ">=3.8.1,<3.12"):
            with self.subTest(requires_python=requires_python):
                testdata["requires-python"] = requires_python

                self._assert_silent(testdata, "but your Requires-Python")

    def test_requires_python_excluding_a_whole_series_is_flagged(self) -> None:
        testdata = COMPLETE.copy()
        testdata["classifier"] = ["Programming Language :: Python :: 3.9"]
        for requires_python in (">=3.10", "!=3.9.*", "<3.9"):
            with self.subTest(requires_python=requires_python):
                testdata["requires-python"] = requires_python

                self._assert_advises(testdata, "claim support for Python 3.9, but your Requires-Python")

    def test_end_of_life_python_classifiers(self) -> None:
        with unittest.mock.patch.object(ratings, "_TODAY", datetime.date(2026, 1, 1)):
            self._assert_advises(COMPLETE.copy(), "reached end of life")

    def test_supported_python_is_not_flagged_as_end_of_life(self) -> None:
        testdata = COMPLETE.copy()
        testdata["classifier"] = ["Programming Language :: Python :: 3.13"]

        # Pinned, so this keeps passing after 3.13 itself reaches end of life.
        with unittest.mock.patch.object(ratings, "_TODAY", datetime.date(2026, 1, 1)):
            self._assert_silent(testdata, "reached end of life")

    def test_mature_status_on_a_prerelease_version(self) -> None:
        # COMPLETE is "Development Status :: 6 - Mature" at version 1.0.dev1.
        self._assert_advises(COMPLETE.copy(), "is a pre-release")

    def test_stable_status_on_a_zero_version(self) -> None:
        testdata = COMPLETE.copy()
        testdata["version"] = "0.0.1"

        self._assert_advises(testdata, "A 0.x version tells installers")

    def test_alpha_status_on_a_final_release(self) -> None:
        testdata = COMPLETE.copy()
        testdata["classifier"] = ["Development Status :: 3 - Alpha"]
        testdata["version"] = "1.1.0"

        self._assert_advises(testdata, "a final release")

    def test_inactive_status_on_a_zero_version_is_coherent(self) -> None:
        # An abandoned 0.x project makes no claim about API stability.
        testdata = COMPLETE.copy()
        testdata["classifier"] = ["Development Status :: 7 - Inactive"]
        testdata["version"] = "0.5"

        self._assert_silent(testdata, "contradicts the classifier")

    def test_typing_classifier_without_a_marker(self) -> None:
        testdata = COMPLETE.copy()
        testdata["classifier"] = ["Typing :: Typed"]
        testdata["_py_typed"] = False

        self._assert_advises(testdata, "no py.typed marker file")

    def test_marker_without_a_typing_classifier(self) -> None:
        testdata = COMPLETE.copy()
        testdata["_py_typed"] = True

        self._assert_advises(testdata, "does not advertise it")

    def test_build_system_without_a_backend(self) -> None:
        testdata = _attach_pyproject(COMPLETE.copy(), TESTDATA_DIR / "no_build_backend")

        self._assert_advises(testdata, "no build-backend key")

    def test_build_backend_floor_too_low_for_pep639(self) -> None:
        # The complete fixture declares setuptools>=61 but uses license-files,
        # which needs setuptools>=77.
        testdata = _attach_pyproject(COMPLETE.copy(), TESTDATA_DIR / "complete")

        self._assert_advises(testdata, "licensing features need setuptools>=77.0.0")

    def test_unknown_build_backend_is_not_guessed_at(self) -> None:
        # pyroma itself uses uv_build, which is deliberately not in the table.
        testdata = _attach_pyproject(COMPLETE.copy(), Path(__file__).parent.parent)

        self._assert_silent(testdata, "build backend floor is too low")

    def _pep639_pyproject(self, requires: "list[str]", backend: str) -> "dict[str, object]":
        return {
            "build-system": {"requires": requires, "build-backend": backend},
            "project": {"name": "example", "license": "MIT"},
        }

    def test_flit_core_floor_for_pep639(self) -> None:
        # Simple SPDX identifiers and license-files arrived in Flit 3.11.
        testdata = COMPLETE.copy()
        testdata["_pyproject"] = self._pep639_pyproject(["flit_core>=3.4,<4"], "flit_core.buildapi")
        self._assert_advises(testdata, "licensing features need flit-core>=3.11")

        testdata["_pyproject"] = self._pep639_pyproject(["flit_core>=3.11,<4"], "flit_core.buildapi")
        self._assert_silent(testdata, "licensing features need")

    def test_missing_build_backend_floor_has_its_own_message(self) -> None:
        testdata = COMPLETE.copy()
        testdata["_pyproject"] = self._pep639_pyproject(["setuptools"], "setuptools.build_meta")

        message = next(m for m in self._advisories(testdata) if "licensing features need setuptools>=77.0.0" in m)
        self.assertIn("declares no minimum version for setuptools", message)
        # There is no floor to raise, so the message must not say there is.
        self.assertNotIn("too low", message)

    def test_license_files_pattern_matching_nothing(self) -> None:
        testdata = COMPLETE.copy()
        testdata["_license_files_unmatched"] = ["LICENCE-DOES-NOT-EXIST*"]

        message = next(m for m in self._advisories(testdata) if "match no file in your project" in m)
        self.assertIn("LICENCE-DOES-NOT-EXIST*", message)

    def test_license_files_pattern_that_matches(self) -> None:
        testdata = COMPLETE.copy()
        testdata["_license_files_unmatched"] = []

        self._assert_silent(testdata, "match no file in your project")

    def test_readme_content_type_mismatch(self) -> None:
        testdata = _attach_pyproject(COMPLETE.copy(), TESTDATA_DIR / "readme_type_mismatch")

        message = next(m for m in self._advisories(testdata) if "your readme table declares 'text/x-rst'" in m)
        self.assertIn("your build backend emitted 'text/plain'", message)

    def test_readme_txt_with_an_explicit_content_type_is_legitimate(self) -> None:
        # PEP 621 does not recognise .txt, so its content type must be
        # declared, and whatever is declared is deliberate.
        testdata = COMPLETE.copy()
        testdata["_pyproject"] = {"project": {"readme": {"file": "README.txt", "content-type": "text/x-rst"}}}
        testdata["description-content-type"] = "text/x-rst"

        self.assertIs(ratings.ReadmeContentTypeMatch().test(testdata).outcome, True)

    def test_development_dependencies_declared_as_extras(self) -> None:
        testdata = _attach_pyproject(COMPLETE.copy(), TESTDATA_DIR / "dev_extras")

        message = next(m for m in self._advisories(testdata) if "declared as extras" in m)
        self.assertIn("'docs', 'test'", message)
        # A genuine feature extra is not a development dependency.
        self.assertNotIn("colour", message)

    def test_a_whole_category_can_be_skipped(self) -> None:
        testdata = COMPLETE.copy()
        testdata["requires-python"] = ">=3.11,<4.0"

        # PythonRequiresUpperBound is an opinionated check.
        self._assert_advises(testdata, "has an upper bound")
        skipped = ratings.rate_project(testdata, ["practice"]).advisories
        self.assertFalse(any("has an upper bound" in problem.message for problem in skipped))

    def test_findings_carry_their_category(self) -> None:
        testdata = COMPLETE.copy()
        testdata["requires-python"] = ">=3.11,<4.0"

        advisories = ratings.rate_project(testdata).advisories
        upper_bound = next(a for a in advisories if "has an upper bound" in a.message)
        self.assertEqual(upper_bound.category, "practice")

    def test_skip_tests_accepts_categories_and_names(self) -> None:
        self.assertEqual(pyroma.parse_tests("practice,Licensing"), ["practice", "Licensing"])
        self.assertIsNone(pyroma.parse_tests("NoSuchThing"))

    def test_deprecated_classifier_names_its_replacement(self) -> None:
        testdata = COMPLETE.copy()
        testdata["classifier"] = [*COMPLETE["classifier"], "Framework :: Django CMS :: 4.2"]

        message = next(m for m in rate(testdata)[1] if "Some of your classifiers are deprecated" in m)
        self.assertIn("has been replaced by", message)
        # The old message wrongly told users to use the private namespace.
        self.assertNotIn("should start with 'Private :: '", message)

    def test_license_expression_with_classifiers_does_not_claim_rejection(self) -> None:
        testdata = COMPLETE.copy()
        testdata["classifier"] = [*COMPLETE["classifier"], "License :: OSI Approved :: MIT License"]

        message = next(m for m in rate(testdata)[1] if "License-Expression" in m)
        self.assertIn("two sources of truth", message)
        # Package indices do not in fact reject this combination.
        self.assertNotIn("rejected by package indices", message)


class ReviewFixRegressionTest(unittest.TestCase):
    @staticmethod
    def _run_subprocess(arguments: list[str], *, cwd: str | Path) -> "subprocess.CompletedProcess[str]":
        environment = os.environ.copy()
        environment["PYTHONPATH"] = os.pathsep.join(filter(None, (str(PYROMA_ROOT), environment.get("PYTHONPATH", ""))))
        return subprocess.run(  # noqa: S603 - fixed interpreter and package root, with test-controlled arguments
            [sys.executable, *arguments], cwd=cwd, env=environment, capture_output=True, text=True, check=False
        )

    def _backend_data(self, requirements: list[str], python: str | None = ">=3.11") -> Metadata:
        data = COMPLETE.copy()
        data.pop("requires-python")
        project = {"name": "example", "license": "MIT"}
        if python is not None:
            project["requires-python"] = python
        data["_pyproject"] = {
            "project": project,
            "build-system": {"build-backend": "setuptools.build_meta", "requires": requirements},
        }
        return data

    def test_credentials_are_absent_from_real_cli_reports(self) -> None:
        dependency = "example[feature] @ https://review-user:review-password@example.invalid/pkg.whl?token=review-token"
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory)
            (root / "example.py").write_text("", encoding="utf-8")
            dependencies = [dependency, dependency + ' ; extra > "test"']
            (root / "pyproject.toml").write_text(
                '[build-system]\nrequires = ["setuptools>=77"]\nbuild-backend = "setuptools.build_meta"\n'
                '[project]\nname = "credential-test"\nversion = "1.0"\n'
                f"dependencies = {json.dumps(dependencies)}\n",
                encoding="utf-8",
            )
            for output_format in ("text", "json"):
                with self.subTest(format=output_format):
                    result = self._run_subprocess(
                        [
                            "-m",
                            "pyroma",
                            "--format",
                            output_format,
                            "--skip-tests",
                            "CheckManifest",
                            directory,
                        ],
                        cwd=outside,
                    )
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self._assert_no_credentials(result)
                    self.assertIn("example", result.stdout)
                    if output_format == "json":
                        document = json.loads(result.stdout)
                        self.assertEqual(document["rating"], 0)
                        problem = next(p for p in document["problems"] if p["test"] == "DirectUrlDependency")
                        self.assertTrue(problem["fatal"])
                        self.assertEqual(problem["category"], "index")

    def _assert_no_credentials(self, result: "subprocess.CompletedProcess[str]") -> None:
        for secret in ("review-user", "review-password", "review-token", "example.invalid", "[feature]"):
            self.assertNotIn(secret, result.stdout + result.stderr)

    def test_subprocess_import_matches_parent_outside_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as outside:
            result = self._run_subprocess(
                ["-c", "import json, os, pyroma; print(json.dumps([os.getcwd(), pyroma.__file__]))"], cwd=outside
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            directory, package = json.loads(result.stdout)
            self.assertEqual(Path(directory).resolve(), Path(outside).resolve())
            self.assertEqual(Path(package).resolve(), Path(pyroma.__file__).resolve())

    def test_subprocess_prioritizes_parent_and_preserves_inherited_pythonpath(self) -> None:
        with tempfile.TemporaryDirectory() as outside, tempfile.TemporaryDirectory() as competing:
            package = Path(competing) / "pyroma"
            package.mkdir()
            (package / "__init__.py").write_text("raise RuntimeError('Wrong pyroma imported')\n", encoding="utf-8")
            probe = Path(competing) / "shadow_probe.py"
            probe.touch()
            with unittest.mock.patch.dict(os.environ, {"PYTHONPATH": competing}):
                result = self._run_subprocess(
                    [
                        "-c",
                        "import json, os, pyroma, shadow_probe; "
                        "print(json.dumps([os.getcwd(), pyroma.__file__, shadow_probe.__file__]))",
                    ],
                    cwd=outside,
                )
            self.assertEqual(result.returncode, 0, result.stderr)
            directory, imported_package, imported_probe = json.loads(result.stdout)
            self.assertEqual(Path(directory).resolve(), Path(outside).resolve())
            self.assertEqual(Path(imported_package).resolve(), Path(pyroma.__file__).resolve())
            self.assertEqual(Path(imported_probe).resolve(), probe.resolve())

    def test_malformed_dependencies_do_not_echo_inputs_or_parser_errors(self) -> None:
        malformed = (
            "example[feature] @ https://review-user:review-password@example.invalid/a?token=review-token"
            ' ; extra >> "test"'
        )
        result = ratings.DependencySpecifiers().test({"requires-dist": ["requests>=2", malformed, "bad > >"]})
        self.assertIs(result.outcome, False)
        self.assertEqual(
            result.message,
            "Your Requires-Dist entries have problems:\n"
            "Requires-Dist entry #2 is not a valid dependency specifier.\n"
            "Requires-Dist entry #3 is not a valid dependency specifier.",
        )

    def test_malformed_dependency_credentials_are_absent_from_real_cli_reports(self) -> None:
        dependency = "example[feature] @ https://review-user:review-password@example.invalid/a?token=review-token"
        metadata = (
            "Metadata-Version: 2.4\nName: credential-test\nVersion: 1.0\n"
            f'Requires-Dist: {dependency}\nRequires-Dist: {dependency} ; extra >> "test"\n\nDescription.\n'
        )
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory)
            (root / "pyproject.toml").write_text(
                '[build-system]\nrequires = []\nbuild-backend = "review_backend"\nbackend-path = ["."]\n',
                encoding="utf-8",
            )
            # Emit raw Core Metadata; ordinary backends reject malformed TOML dependencies before rating.
            (root / "review_backend.py").write_text(
                "from pathlib import Path\n\n"
                "def prepare_metadata_for_build_wheel(metadata_directory, config_settings=None):\n"
                "    destination = Path(metadata_directory) / 'credential_test-1.0.dist-info'\n"
                "    destination.mkdir()\n"
                f"    (destination / 'METADATA').write_text({metadata!r}, encoding='utf-8')\n"
                "    return destination.name\n",
                encoding="utf-8",
            )
            for output_format in ("text", "json"):
                with self.subTest(format=output_format):
                    result = self._run_subprocess(
                        ["-m", "pyroma", "--format", output_format, "--skip-tests", "CheckManifest", directory],
                        cwd=outside,
                    )
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self._assert_no_credentials(result)
                    self.assertIn("Requires-Dist entry #2", result.stdout)
                    self.assertNotIn("Expected a marker", result.stdout + result.stderr)
                    if output_format == "json":
                        problem = next(
                            p for p in json.loads(result.stdout)["problems"] if p["test"] == "DependencySpecifiers"
                        )
                        self.assertEqual(problem["category"], "spec")

    def test_backend_floors_include_wildcard_prereleases(self) -> None:
        cases = (
            (["setuptools==77.*"], False),
            (["setuptools==77.0.*"], False),
            (["setuptools>=77rc1"], False),
            (["setuptools>=77"], True),
            (["setuptools~=77.0"], True),
            (["setuptools==77.*", "setuptools>=77"], True),
        )
        for requirements, expected in cases:
            with self.subTest(requirements=requirements):
                self.assertIs(
                    ratings.BuildBackendVersionFloor().test(self._backend_data(requirements)).outcome, expected
                )

    def test_direct_url_names_are_sorted_and_deduplicated(self) -> None:
        data = COMPLETE.copy()
        data["requires-dist"] = [
            'zeta[x] @ https://user:password@example.invalid/a ; python_version > "3.11"',
            "alpha @ https://example.invalid/b?token=secret",
            "alpha[other] @ https://example.invalid/c",
        ]
        result = ratings.DirectUrlDependency().test(data)
        self.assertIs(result.outcome, False)
        self.assertTrue(result.fatal)
        self.assertIn("\n  alpha\n  zeta\n", result.message)
        for fragment in ("https", "password", "secret", "[x]", "python_version"):
            self.assertNotIn(fragment, result.message)

    def test_python_patch_intersections(self) -> None:
        cases = (
            (">=3.11.1,<3.11.10", True),
            ("==3.11.5", True),
            (">=3.11.1,!=3.11.999", True),
            ("==3.11.*,!=3.11.5", True),
            ("!=3.11.*", False),
            ("==3.12.*", False),
            (">=3.11.5,<3.11.5", False),
        )
        for specifier, expected in cases:
            with self.subTest(specifier=specifier):
                data = {"requires-python": specifier, "classifier": ["Programming Language :: Python :: 3.11"]}
                self.assertIs(ratings.PythonVersionCoherence().test(data).outcome, expected)

    def test_combined_backend_constraints_are_order_independent(self) -> None:
        for requirements in permutations(("setuptools>=61", "setuptools>=77", "setuptools<80")):
            with self.subTest(requirements=requirements):
                result = ratings.BuildBackendVersionFloor().test(self._backend_data(list(requirements)))
                self.assertIs(result.outcome, True)

    def test_backend_python_partitions(self) -> None:
        cases = (
            (["setuptools>=61; python_version < '3.9'", "setuptools>=77; python_version >= '3.9'"], ">=3.11", True),
            (["setuptools>=61; python_version < '3.9'", "setuptools>=77; python_version >= '3.9'"], ">=3.8", False),
            (
                ["setuptools>=77; (python_version >= '3.11' and python_version < '3.12') or python_version >= '3.13'"],
                "==3.11.5",
                True,
            ),
            (["setuptools>=77; '3.11' <= python_version"], ">=3.11", True),
            (["setuptools>=77; python_version > '3.11'"], "==3.11.5", False),
            (["setuptools>=77; python_full_version > '3.11'"], "==3.11.5", True),
            (["setuptools>=77; python_full_version >= '3.11.5'"], ">=3.11.5", True),
            (["setuptools>=77; python_full_version >= '3.11.5'"], ">=3.11.1", False),
            (["setuptools>=77; python_version == '3.11'"], "==3.11.*", True),
            (["setuptools>=77; python_version != '3.11'"], "==3.12.*", True),
            (["setuptools>=77; python_version <= '3.11'"], "==3.11.5", True),
            (["setuptools>=77; '3.11.5' <= python_full_version"], "==3.11.5", True),
            (["setuptools>=77; python_version >= '3.11'"], None, False),
            (["setuptools>=77"], ">3.12,<3.12", None),
            (
                ["setuptools>=61", "setuptools>=77; python_version < '3.9'", "setuptools<70; python_version < '3.9'"],
                ">=3.8,<3.9",
                None,
            ),
        )
        for requirements, python, expected in cases:
            with self.subTest(requirements=requirements, python=python):
                self.assertIs(
                    ratings.BuildBackendVersionFloor().test(self._backend_data(requirements, python)).outcome, expected
                )

    def test_unknown_backend_conditions_are_conservative(self) -> None:
        cases = (
            (["setuptools>=77; sys_platform == 'linux'"], None),
            (["setuptools>=61", "setuptools>=77; os_name == 'posix'"], None),
            (["setuptools>=77", "setuptools>=61; os_name == 'posix'"], True),
            (["setuptools @ https://example.invalid/backend.whl"], None),
            (["setuptools>=77", "setuptools @ https://example.invalid/backend.whl"], True),
            (["setuptools>=77,<70"], None),
            (["setuptools>=77", "setuptools<70; python_version >= '3.11'"], None),
            (["setuptools>=77.0.0rc1"], False),
            (["setuptools"], False),
            ([], False),
        )
        for requirements, expected in cases:
            with self.subTest(requirements=requirements):
                self.assertIs(
                    ratings.BuildBackendVersionFloor().test(self._backend_data(requirements)).outcome, expected
                )
        fallback = self._backend_data(["setuptools>=77; python_version >= '3.11'"], None)
        fallback["requires-python"] = ">=3.11"
        self.assertIs(ratings.BuildBackendVersionFloor().test(fallback).outcome, True)

    def test_flit_feature_floors(self) -> None:
        cases = (
            ({"license": "MIT"}, "3.11", True),
            ({"license-files": ["LICENSE"]}, "3.11", True),
            ({"license": "MIT", "license-files": ["LICENSE"]}, "3.10", False),
            ({"license": "MIT OR Apache-2.0"}, "3.11", False),
            ({"license": "MIT AND Apache-2.0"}, "3.12", True),
            ({"license": "(MIT)"}, "3.11", False),
            ({"license": "(MIT)"}, "3.12", True),
            ({"license": "GPL-2.0-only WITH Classpath-exception-2.0"}, "3.12", None),
            ({"license": "not-a-license"}, "3.12", None),
        )
        for features, floor, expected in cases:
            with self.subTest(features=features, floor=floor):
                data = {
                    "_pyproject": {
                        "project": features,
                        "build-system": {"build-backend": "flit_core.buildapi", "requires": [f"flit_core>={floor}"]},
                    }
                }
                self.assertIs(ratings.BuildBackendVersionFloor().test(data).outcome, expected)

    def test_runtime_extra_boolean_conditions(self) -> None:
        cases = (
            ('extra == "test"', False),
            ('extra != "test"', True),
            ('extra == ""', True),
            ('"test" == extra', False),
            ('extra == "test" or python_version >= "3.11"', True),
            ('(extra == "test" or extra == "docs") and os_name == "posix"', False),
            ('extra == "test" and (python_version >= "3.11" or os_name == "nt")', False),
            ('(extra == "test" and os_name == "nt") or (extra != "test" and os_name == "posix")', True),
            ('extra not in "test,docs"', False),
            ('extra in "test,docs"', True),
            ('os_name == "unknown-environment"', True),
        )
        for marker, expected in cases:
            with self.subTest(marker=marker):
                data = {"requires-dist": [f"example<3; {marker}", f"floorless; {marker}"]}
                self.assertIs(ratings.DependencyUpperBounds().test(data).outcome, not expected)
                self.assertIs(ratings.DependencyLowerBounds().test(data).outcome, not expected)

    def test_markdown_destinations(self) -> None:
        cases = (
            ('[docs](<https://example.com/docs> "Title")', []),
            ("![image](<https://example.com/i?a=1&amp;b=2>)", []),
            ('[docs](docs/a(b).md "Title")', ["docs/a(b).md"]),
            ("[docs](docs/a\\(b\\).md)", ["docs/a(b).md"]),
            ("[docs](docs/a&#46;md)", ["docs/a.md"]),
            ('[x][ D o C ]\n\n[d o c]: <docs/ref.md> "Title"', ["docs/ref.md"]),
            ("[docs][] and [docs]\n\n[docs]: docs/first.md\n[DOCS]: docs/second.md", ["docs/first.md"]),
            ("![image][img]\n\n[img]: images/example.png", ["images/example.png"]),
            ("[docs][a\\]b]\n\n[a\\]b]: docs/escaped.md", ["docs/escaped.md"]),
            ("[docs][a&b]\n\n[a&amp;b]: docs/ambiguous.md", []),
            ('<a href="docs/html.md">Docs</a><img src="images/a.png"/>', ["docs/html.md", "images/a.png"]),
            ("```markdown\n[x](docs/code.md)\n```\n~~~\n![x](images/code.png)\n~~~", []),
            ("> ~~~\n> [x](docs/code.md)\n> ~~~", []),
            ("- ~~~\n  [x](docs/code.md)\n  ~~~", []),
            (">     [x](docs/code.md)", []),
            ("    [x](docs/code.md)\n\t![x](images/code.png)", []),
            ("`[x](docs/code.md)` and `` code `[x](docs/code.md)` ``", []),
            ("`\n[ref]: docs/code.md\n`\n[x][ref]", []),
            ('<a href="docs/`literal`.md">docs</a>', ["docs/`literal`.md"]),
            ('<!-- [x](docs/comment.md) --><pre>[x](docs/pre.md)</pre><code><a href="code.md">x</a></code>', []),
            ("\\[x](docs/escaped.md)", []),
            ('\\<a href="docs/escaped.md">example</a>', []),
            ('<a title="[example](docs/literal.md)" href="https://example.com">example</a>', []),
            ('<![unknown]><a href="docs/actual.md">example</a>', ["docs/actual.md"]),
            ("[x](docs/unclosed(a.md)", []),
            ("[x](<docs/unclosed.md)", []),
            ('[x](docs/a.md "unclosed)', []),
            ("[unknown][missing]", []),
            ("[x](#section) [x](//example.com/x) [x](mailto:a@example.com)", []),
            ("[![logo](images/logo.svg)](https://example.org)", ["images/logo.svg"]),
            ("[![logo][image]][site]\n\n[image]: images/logo.svg\n[site]: https://example.org", ["images/logo.svg"]),
            ("stray [\n\n[docs](docs/index.md)", ["docs/index.md"]),
            ("stray [ before [docs](docs/index.md)", ["docs/index.md"]),
            ("stray `\n\n[docs](docs/index.md)", ["docs/index.md"]),
            ("stray `\n\n[docs](docs/index.md)\n\n` tail", ["docs/index.md"]),
            ("intro\n[label]: docs/ref.md\n\n[label]", []),
            ("# Heading\n[label]: docs/ref.md\n\n[label]", ["docs/ref.md"]),
            ('<code>[x](docs/code.md)<a href="docs/code.md">x</a></code> [x](docs/real.md)', ["docs/real.md"]),
            ('<pre><img src="images/code.png"/></pre>\n\n[x](docs/real.md)', ["docs/real.md"]),
            ('<script><img src="images/code.png"/></script>\n\n[x](docs/real.md)', ["docs/real.md"]),
            ('<style><a href="docs/code.md">x</a></style>\n\n[x](docs/real.md)', ["docs/real.md"]),
            ("<code><code>[x](docs/code.md)</code>[x](docs/code.md)</code> [x](docs/real.md)", ["docs/real.md"]),
            ("<code>[x](docs/code.md)</span>[x](docs/code.md)</code> [x](docs/real.md)", ["docs/real.md"]),
            ("<code/>[x](docs/real.md)", ["docs/real.md"]),
            ("<code>\n\n[x](docs/code.md)\n\n</code>\n\n[x](docs/real.md)", ["docs/real.md"]),
            ("<code>\n\n[ref]: docs/code.md\n\n</code>\n\n[ref]", []),
            ("<code>\n\n[ref]: docs/code.md\n\n</code>\n\n[ref]: docs/real.md\n\n[ref]", ["docs/real.md"]),
            ("> <code>\n>\n> [ref]: docs/code.md\n>\n> </code>\n\n[ref]", []),
            ("`<code>`\n\n[ref]: docs/real.md\n\n[ref]", ["docs/real.md"]),
            ("intro\u2028text\n\n<code>\n\n[ref]: docs/code.md\n\n</code>\n\n[ref]", []),
            ("<code>\r\n\r\n[ref]: docs/code.md\r\n\r\n</code>\r\n\r\n[ref]", []),
            ("![alt [x](docs/alt.md)](images/logo.svg)", ["images/logo.svg"]),
            (
                '[x](docs/first.md) <a href="docs/second.md">x</a> ![x](images/third.svg)',
                ["docs/first.md", "docs/second.md", "images/third.svg"],
            ),
            ('[x](docs/a.md) <a href="docs/b.md">x</a> [x](docs/a.md) [x](docs/b.md)', ["docs/a.md", "docs/b.md"]),
        )
        for description, expected in cases:
            with self.subTest(description=description):
                self.assertEqual(ratings._relative_description_targets(description, "text/markdown"), expected)

    def test_many_unique_markdown_and_html_targets_preserve_order(self) -> None:
        count = 1000
        description = " ".join(
            f'[x](docs/{number}.md) <img src="images/{number}.svg"> [x](docs/{number}.md)' for number in range(count)
        )
        expected = [target for number in range(count) for target in (f"docs/{number}.md", f"images/{number}.svg")]
        self.assertEqual(ratings._relative_description_targets(description, "text/markdown"), expected)

    def test_readme_content_type_overrides_and_emission(self) -> None:
        cases = (
            ("README.md", "text/markdown; charset=UTF-8", True),
            ("README.md", "text/plain", False),
            ({"file": "README.md", "content-type": "text/plain"}, "text/plain", True),
            ({"file": "README.md", "content-type": " Text/X-RST ; charset=utf-8"}, "TEXT/X-RST; variant=foo", True),
            ({"file": "README.md", "content-type": "text/plain"}, "text/markdown", False),
            ({"text": "inline", "content-type": "text/plain"}, "text/markdown", False),
            ({"file": "README.md"}, "text/plain", None),
        )
        for readme, emitted, expected in cases:
            with self.subTest(readme=readme, emitted=emitted):
                data = {"_pyproject": {"project": {"readme": readme}}, "description-content-type": emitted}
                self.assertIs(ratings.ReadmeContentTypeMatch().test(data).outcome, expected)


class ConfigTest(unittest.TestCase):
    maxDiff = None

    def test_defaults(self) -> None:
        settings = config.Config()

        self.assertEqual(settings.min_rating, 8)
        self.assertFalse(settings.strict)
        self.assertTrue(settings.show_advisories)
        self.assertEqual(settings.skip_tests, [])

    def test_from_table(self) -> None:
        settings = config.from_table(
            {"min-rating": 10, "strict": True, "advisories": False, "skip-tests": ["practice"]},
        )

        self.assertEqual(settings.min_rating, 10)
        self.assertTrue(settings.strict)
        self.assertFalse(settings.show_advisories)
        self.assertEqual(settings.skip_tests, ["practice"])

    def test_unknown_key_is_an_error(self) -> None:
        # Silently swallowing a typo'd key is the classic config failure mode.
        with self.assertRaisesRegex(config.ConfigError, "unknown key 'ignor'"):
            config.from_table({"ignor": ["practice"]})

    def test_out_of_range_min_rating(self) -> None:
        with self.assertRaisesRegex(config.ConfigError, "between 1 and 10"):
            config.from_table({"min-rating": 42})

    def test_wrong_type_is_an_error(self) -> None:
        with self.assertRaisesRegex(config.ConfigError, "must be true or false"):
            config.from_table({"strict": "yes"})

    def test_booleans_are_not_accepted_as_min_rating(self) -> None:
        # bool is an int subclass, so this needs an explicit guard.
        with self.assertRaisesRegex(config.ConfigError, "whole number"):
            config.from_table({"min-rating": True})

    def test_from_directory_reads_the_tool_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "pyproject.toml").write_text(
                '[tool.pyroma]\nmin-rating = 9\nskip-tests = ["practice"]\n',
                encoding="UTF-8",
            )
            settings = config.from_directory(tmp)

        self.assertEqual(settings.min_rating, 9)
        self.assertEqual(settings.skip_tests, ["practice"])

    def test_from_directory_without_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="UTF-8")
            self.assertEqual(config.from_directory(tmp), config.Config())

    def test_a_broken_pyproject_does_not_stop_pyroma(self) -> None:
        # The rating reports the broken file; configuration just falls back.
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "pyproject.toml").write_text("not toml at all {", encoding="UTF-8")
            self.assertEqual(config.from_directory(tmp), config.Config())

    def _resolve(self, argv: "list[str]", mode: str = "directory") -> config.Config:
        args = pyroma._build_parser().parse_args(argv)
        return pyroma._resolve_config(args, mode)

    def test_explicit_flag_beats_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "pyproject.toml").write_text("[tool.pyroma]\nmin-rating = 3\n", encoding="UTF-8")

            self.assertEqual(self._resolve([tmp]).min_rating, 3)
            self.assertEqual(self._resolve(["--min", "10", tmp]).min_rating, 10)
            # --no-config ignores the table entirely.
            self.assertEqual(self._resolve(["--no-config", tmp]).min_rating, 8)

    def test_configuration_is_only_read_in_directory_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "pyproject.toml").write_text("[tool.pyroma]\nmin-rating = 3\n", encoding="UTF-8")

            self.assertEqual(self._resolve([tmp], mode="pypi").min_rating, 8)

    def test_either_direction_of_a_boolean_flag_beats_configuration(self) -> None:
        # "An explicit flag always wins" needs a flag for both values, or a
        # project that sets strict = true can only be overridden by dropping
        # the whole configuration with --no-config.
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "pyproject.toml").write_text(
                "[tool.pyroma]\nstrict = true\nadvisories = false\n",
                encoding="UTF-8",
            )

            configured = self._resolve([tmp])
            overridden = self._resolve(["--no-strict", "--advisories", tmp])

        self.assertTrue(configured.strict)
        self.assertFalse(configured.show_advisories)
        self.assertFalse(overridden.strict)
        self.assertTrue(overridden.show_advisories)

    def test_unknown_skip_test_in_configuration_is_an_error(self) -> None:
        # The CLI rejects a typo'd --skip-tests; the configuration must too,
        # or the typo silently skips nothing while the user believes it does.
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "pyproject.toml").write_text('[tool.pyroma]\nskip-tests = ["Licencing"]\n', encoding="UTF-8")

            with self.assertRaisesRegex(config.ConfigError, "unknown tests: 'Licencing'.*Licensing"):
                self._resolve([tmp])


class PyPITest(unittest.TestCase):
    maxDiff = None

    @unittest.mock.patch("pyroma.pypidata.xmlrpc.client.ServerProxy")
    @unittest.mock.patch("pyroma.pypidata._get_project_data")
    @unittest.mock.patch("requests.get")
    def test_complete(
        self,
        requestmock: unittest.mock.Mock,
        projectdatamock: unittest.mock.Mock,
        proxymock: unittest.mock.Mock,
    ) -> None:
        datafile = TESTDATA_DIR / "jsondata" / "complete.json"
        with datafile.open(encoding="UTF-8") as file:
            projectdatamock.return_value = json.load(file)

        srcfile = TESTDATA_DIR / "distributions" / "complete-1.0.dev1.tar.gz"
        with srcfile.open("rb") as file:
            requestmock.return_value = unittest.mock.Mock(ok=True)
            requestmock.return_value.content = file.read()

        proxymock.return_value.__enter__.return_value.package_roles.return_value = [
            ["Owner", "someone"],
            ["Owner", "me"],
            ["Owner", "other"],
        ]

        data = pypidata.get_data("complete")
        rating = rate(data)

        self.assertEqual(rating, (10, []))
        extracted_path = data["_path"]
        self.assertTrue(Path(extracted_path).is_dir())
        distributiondata.cleanup(data)
        self.assertFalse(Path(extracted_path).exists())

    @unittest.mock.patch("pyroma.pypidata.requests.get")
    def test_get_project_data_custom_index_url(self, requestmock: unittest.mock.Mock) -> None:
        requestmock.return_value = unittest.mock.Mock()
        requestmock.return_value.ok = True
        requestmock.return_value.status_code = 200
        requestmock.return_value.json.return_value = {}

        pypidata._get_project_data("internalpkg", index_url="https://packages.example.com")

        requestmock.assert_called_once_with(
            "https://packages.example.com/pypi/internalpkg/json", timeout=pypidata.REQUEST_TIMEOUT
        )

    @unittest.mock.patch("pyroma.pypidata.requests.get")
    def test_get_project_data_custom_index_url_with_pypi_path(self, requestmock: unittest.mock.Mock) -> None:
        requestmock.return_value = unittest.mock.Mock()
        requestmock.return_value.ok = True
        requestmock.return_value.status_code = 200
        requestmock.return_value.json.return_value = {}

        pypidata._get_project_data("internalpkg", index_url="https://packages.example.com/pypi")

        requestmock.assert_called_once_with(
            "https://packages.example.com/pypi/internalpkg/json", timeout=pypidata.REQUEST_TIMEOUT
        )

    @unittest.mock.patch("pyroma.pypidata.requests.get")
    def test_custom_index_not_found_error_omits_url_secrets(self, requestmock: unittest.mock.Mock) -> None:
        requestmock.return_value = unittest.mock.Mock(ok=False, status_code=404)
        index_url = "https://user:password@packages.example.com/simple?token=secret"

        with self.assertRaises(ValueError) as caught:
            pypidata._get_project_data("internalpkg", index_url=index_url)

        error = str(caught.exception)
        self.assertEqual(error, "Did not find 'internalpkg' on the configured package index.")
        for secret in ("user", "password", "token", "secret", "packages.example.com"):
            self.assertNotIn(secret, error)

    def test_http_errors_omit_url_secrets_and_request_details(self) -> None:
        url = "https://user:password@packages.example.com/pypi?token=secret"
        errors = (
            pypidata.requests.exceptions.Timeout(f"Timed out fetching {url}"),
            pypidata.requests.exceptions.ConnectionError(f"Could not connect to {url}"),
        )

        for request_error in errors:
            with (
                self.subTest(error=type(request_error).__name__),
                unittest.mock.patch("pyroma.pypidata.requests.get", side_effect=request_error),
                self.assertRaises(ValueError) as caught,
            ):
                pypidata._http_get(url)

            error = str(caught.exception)
            for secret in ("user", "password", "token", "secret", "packages.example.com"):
                self.assertNotIn(secret, error)
            self.assertTrue(caught.exception.__suppress_context__)

    @unittest.mock.patch("pyroma.pypidata._get_project_data")
    def test_get_data_rejects_malformed_info(self, projectdatamock: unittest.mock.Mock) -> None:
        invalid_responses = ([], {}, {"info": None}, {"info": []}, {"info": "not metadata"})
        for invalid_response in invalid_responses:
            with self.subTest(response=invalid_response):
                projectdatamock.return_value = invalid_response
                with self.assertRaisesRegex(
                    ValueError,
                    "Invalid metadata format received from package index",
                ):
                    pypidata.get_data("example")

    @unittest.mock.patch("pyroma.pypidata.xmlrpc.client.ServerProxy")
    @unittest.mock.patch("pyroma.pypidata._get_project_data")
    def test_get_data_custom_index_url_uses_xmlrpc_endpoint(
        self,
        projectdatamock: unittest.mock.Mock,
        proxymock: unittest.mock.Mock,
    ) -> None:
        projectdatamock.return_value = {
            "info": {"name": "internalpkg", "version": "1.2.3"},
            "releases": {"1.2.3": []},
        }

        proxymock.return_value.__enter__.return_value.package_roles.return_value = [("Owner", "dev1")]

        data = pypidata.get_data("internalpkg", index_url="https://packages.example.com")

        self.assertEqual(data["_owners"], ["dev1"])
        proxymock.assert_called_once()
        args, kwargs = proxymock.call_args
        self.assertEqual(args, ("https://packages.example.com/pypi",))
        self.assertIsInstance(kwargs["transport"], pypidata._TimeoutSafeTransport)
        self.assertEqual(kwargs["transport"].timeout, pypidata.REQUEST_TIMEOUT)

    def test_xmlrpc_transports_apply_timeout_to_connections(self) -> None:
        variants = (
            (pypidata._TimeoutTransport, xmlrpclib.Transport),
            (pypidata._TimeoutSafeTransport, xmlrpclib.SafeTransport),
        )

        for transport_class, base_class in variants:
            with self.subTest(transport=transport_class.__name__):
                connection = unittest.mock.Mock()
                with unittest.mock.patch.object(base_class, "make_connection", return_value=connection):
                    transport = transport_class(pypidata.REQUEST_TIMEOUT)
                    result = transport.make_connection("packages.example.com")

                self.assertIs(result, connection)
                self.assertEqual(connection.timeout, pypidata.REQUEST_TIMEOUT)

        self.assertIsInstance(pypidata._xmlrpc_transport("http://packages.example.com"), pypidata._TimeoutTransport)
        self.assertIsInstance(
            pypidata._xmlrpc_transport("https://packages.example.com"), pypidata._TimeoutSafeTransport
        )

    @unittest.mock.patch("pyroma.pypidata.xmlrpc.client.ServerProxy")
    @unittest.mock.patch("pyroma.pypidata._get_project_data")
    def test_package_roles_fault_and_oserror_warn_only(
        self,
        projectdatamock: unittest.mock.Mock,
        proxymock: unittest.mock.Mock,
    ) -> None:
        projectdatamock.return_value = {
            "info": {"name": "example", "version": "1.0"},
            "releases": {"1.0": []},
        }
        for error in (xmlrpclib.Fault(1, "package_roles is not supported"), ConnectionError("connection refused")):
            proxymock.return_value.__enter__.return_value.package_roles.side_effect = error
            with self.assertLogs("pyroma.pypidata", level="WARNING"):
                data = pypidata.get_data("example")
            self.assertNotIn("_owners", data)

    @unittest.mock.patch("pyroma.pypidata.xmlrpc.client.ServerProxy")
    @unittest.mock.patch("pyroma.pypidata._get_project_data")
    def test_pypi_home_page_not_clobbered(
        self,
        projectdatamock: unittest.mock.Mock,
        proxymock: unittest.mock.Mock,
    ) -> None:
        # The PyPI JSON API synthesizes project_url/package_url/release_url
        # (they always point at pypi.org); they must not end up in the
        # metadata, and in particular must not overwrite home_page.
        projectdatamock.return_value = {
            "info": {
                "name": "example",
                "version": "1.0",
                "home_page": "https://example.com/home",
                "project_url": "https://pypi.org/project/example/",
                "package_url": "https://pypi.org/project/example/",
                "release_url": "https://pypi.org/project/example/1.0/",
                "docs_url": None,
                "bugtrack_url": None,
                "project_urls": {"Homepage": "https://example.com/home"},
            },
            "releases": {"1.0": []},
        }
        proxymock.return_value.__enter__.return_value.package_roles.return_value = []

        data = pypidata.get_data("example")

        self.assertEqual(data["home-page"], "https://example.com/home")
        self.assertEqual(data["project-url"], {"Homepage": "https://example.com/home"})
        for key in ("package-url", "release-url", "docs-url", "bugtrack-url"):
            self.assertNotIn(key, data)

    @unittest.mock.patch("pyroma.pypidata.xmlrpc.client.ServerProxy")
    @unittest.mock.patch("pyroma.pypidata.requests.get")
    @unittest.mock.patch("pyroma.pypidata._get_project_data")
    def test_sdist_download_http_failure_raises_value_error(
        self,
        projectdatamock: unittest.mock.Mock,
        requestmock: unittest.mock.Mock,
        proxymock: unittest.mock.Mock,
    ) -> None:
        download_url = "https://user:password@files.example.com/example-1.0.tar.gz?token=secret"
        projectdatamock.return_value = {
            "info": {"name": "example", "version": "1.0"},
            "releases": {"1.0": [{"packagetype": "sdist", "url": download_url}]},
        }
        proxymock.return_value.__enter__.return_value.package_roles.return_value = []
        requestmock.return_value = unittest.mock.Mock(ok=False, status_code=404, reason="Not Found")

        with self.assertRaises(ValueError) as caught:
            pypidata.get_data("example")

        error = str(caught.exception)
        self.assertEqual(error, "Could not download source distribution: 404 Not Found")
        for secret in ("user", "password", "token", "secret", "files.example.com"):
            self.assertNotIn(secret, error)

    @unittest.mock.patch("pyroma.pypidata.distributiondata.get_data")
    @unittest.mock.patch("pyroma.pypidata.requests.get")
    @unittest.mock.patch("pyroma.pypidata.xmlrpc.client.ServerProxy")
    @unittest.mock.patch("pyroma.pypidata._get_project_data")
    def test_signed_sdist_url_uses_decoded_path_basename(
        self,
        projectdatamock: unittest.mock.Mock,
        proxymock: unittest.mock.Mock,
        requestmock: unittest.mock.Mock,
        distributionmock: unittest.mock.Mock,
    ) -> None:
        projectdatamock.return_value = {
            "info": {"name": "example", "version": "1.0"},
            "releases": {
                "1.0": [
                    {
                        "packagetype": "sdist",
                        "url": "https://files.example.com/example%2D1.0.tar.gz?token=secret",
                    }
                ]
            },
        }
        proxymock.return_value.__enter__.return_value.package_roles.return_value = []
        requestmock.return_value = unittest.mock.Mock(ok=True, content=b"archive")
        distributionmock.return_value = {}

        pypidata.get_data("example")

        downloaded_path = distributionmock.call_args.args[0]
        self.assertEqual(Path(downloaded_path).name, "example-1.0.tar.gz")
        with self.assertRaisesRegex(ValueError, "no filename"):
            pypidata._download_filename("https://files.example.com/")

    @unittest.mock.patch("pyroma.pypidata.xmlrpc.client.ServerProxy")
    @unittest.mock.patch("pyroma.pypidata._get_project_data")
    def test_missing_releases_key_skips_sdist_checks(
        self,
        projectdatamock: unittest.mock.Mock,
        proxymock: unittest.mock.Mock,
    ) -> None:
        # Custom indexes may omit the (PyPI-deprecated) releases key, or
        # list different versions than the info dict claims.
        proxymock.return_value.__enter__.return_value.package_roles.return_value = []
        for extra in ({}, {"releases": {}}, {"releases": {"0.9": []}}):
            project_data = {"info": {"name": "example", "version": "1.0"}}
            project_data.update(extra)
            projectdatamock.return_value = project_data

            data = pypidata.get_data("example")

            self.assertNotIn("_has_sdist", data, extra)

    @unittest.mock.patch("pyroma.ratings.rate_project")
    @unittest.mock.patch("pyroma.pypidata.get_data")
    def test_run_forwards_custom_index_url(
        self,
        datamock: unittest.mock.Mock,
        ratemock: unittest.mock.Mock,
    ) -> None:
        datamock.return_value = {"name": "internalpkg"}
        ratemock.return_value = ratings.RatedProject(
            name="internalpkg", rating=10, level=ratings.LEVELS[10], problems=[]
        )

        result = pyroma.run("pypi", "internalpkg", quiet=True, index_url="https://packages.example.com")

        self.assertEqual(result, 10)
        datamock.assert_called_once_with("internalpkg", index_url="https://packages.example.com")


class MainTest(unittest.TestCase):
    """Tests for the command line entry point's error handling."""

    def _run_main(self, argv: "list[str]") -> "tuple[int | str | None, str, str]":
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            unittest.mock.patch("sys.argv", ["pyroma", *argv]),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit) as caught,
        ):
            pyroma.main()
        return caught.exception.code, stdout.getvalue(), stderr.getvalue()

    @unittest.mock.patch("pyroma.pypidata.get_data")
    def test_main_data_error_exits_3(self, datamock: unittest.mock.Mock) -> None:
        datamock.side_effect = ValueError("Did not find 'nosuch' on PyPI. Did you misspell it?")

        code, stdout, stderr = self._run_main(["-p", "nosuch"])

        self.assertEqual(code, 3)
        self.assertIn("Did not find 'nosuch'", stderr)
        self.assertNotIn("Traceback", stderr)

    @unittest.mock.patch("pyroma.pypidata.get_data")
    def test_main_json_error_document(self, datamock: unittest.mock.Mock) -> None:
        datamock.side_effect = ValueError("Did not find 'nosuch' on PyPI. Did you misspell it?")

        code, stdout, stderr = self._run_main(["--format", "json", "-p", "nosuch"])

        self.assertEqual(code, 3)
        document = json.loads(stdout)
        self.assertEqual(document["error"]["type"], "ValueError")
        self.assertIn("Did not find 'nosuch'", document["error"]["message"])
        self.assertEqual(document["_meta"]["mode"], "pypi")

    def test_main_rejects_unknown_skip_tests_in_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "pyproject.toml").write_text('[tool.pyroma]\nskip-tests = ["Licencing"]\n', encoding="UTF-8")

            code, _stdout, stderr = self._run_main([tmp])

        self.assertEqual(code, 3)
        self.assertIn("skip-tests names unknown tests: 'Licencing'", stderr)
        # The message lists what would have been accepted.
        self.assertIn("Licensing", stderr)

    @unittest.mock.patch("pyroma.projectdata.get_data")
    def test_strict_and_advisory_flags_on_the_command_line(self, datamock: unittest.mock.Mock) -> None:
        testdata = COMPLETE.copy()
        # PythonRequiresUpperBound is advisory.
        testdata["requires-python"] = ">=3.11,<4.0"
        datamock.return_value = testdata
        with tempfile.TemporaryDirectory() as tmp:
            _code, default_out, _stderr = self._run_main([tmp])
            _code, strict_out, _stderr = self._run_main(["--strict", tmp])
            _code, hidden_out, _stderr = self._run_main(["--no-advisories", tmp])

        self.assertIn("Advisory (not scored", default_out)
        self.assertIn("has an upper bound", default_out)
        # --strict scores the finding, so it is a problem, not an advisory.
        self.assertNotIn("Advisory (not scored", strict_out)
        self.assertIn("has an upper bound", strict_out)
        self.assertNotIn("Advisory", hidden_out)
        self.assertNotIn("has an upper bound", hidden_out)

    @unittest.mock.patch("pyroma.package_version", return_value="6.0.0")
    def test_json_meta_uses_published_distribution_name(self, versionmock: unittest.mock.Mock) -> None:
        self.assertEqual(
            pyroma._json_meta("pypi", "example"),
            {"package": "example", "mode": "pypi", "pyroma": "6.0.0"},
        )
        versionmock.assert_called_once_with("pyroma621")

    def test_main_all_tests_skipped_exits_3(self) -> None:
        skip = ",".join(pyroma.get_all_tests())

        code, stdout, stderr = self._run_main(["-d", "--skip-tests", skip, str(TESTDATA_DIR / "complete")])

        self.assertEqual(code, 3)
        self.assertIn("no rating can be calculated", stderr)

    def test_main_missing_distribution_exits_3(self) -> None:
        missing = TESTDATA_DIR / "distributions" / "does-not-exist.tar.gz"

        code, _stdout, stderr = self._run_main(["-f", str(missing)])

        self.assertEqual(code, 3)
        self.assertIn("does-not-exist.tar.gz", stderr)
        self.assertNotIn("Traceback", stderr)


class ProjectDataTest(unittest.TestCase):
    maxDiff = None

    def test_complete(self) -> None:
        directory = TESTDATA_DIR / "complete"

        data = projectdata.get_data(directory)
        del data["_path"]  # This changes, so I just ignore it
        del data["_pyproject"]  # Parsed on the fly, not part of the metadata
        # Gathered from the working tree for the coherence checks.
        self.assertFalse(data.pop("_py_typed"))
        self.assertFalse(data.pop("_stub_only"))
        self.assertEqual(data.pop("_license_files_unmatched"), [])

        self.assertEqual(data, COMPLETE)

    def test_typed_marker_is_found_in_source_tree(self) -> None:
        self.assertTrue(projectdata.has_typed_marker(TESTDATA_DIR / "typed_marker"))
        self.assertFalse(projectdata.has_typed_marker(TESTDATA_DIR / "complete"))

    def test_recursive_typing_layouts(self) -> None:
        for layout in ("pkg", "src/pkg", "src/acme/widget", "custom/python/acme/widget"):
            with self.subTest(layout=layout), tempfile.TemporaryDirectory() as directory:
                package = Path(directory) / layout
                package.mkdir(parents=True)
                (package / "py.typed").write_text("", encoding="utf-8")
                (package / "module.py").write_text("", encoding="utf-8")
                self.assertEqual(projectdata.typing_facts(directory), (True, False))

    def test_complete_and_partial_stub_layouts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "custom" / "example-stubs"
            nested = package / "subpackage"
            nested.mkdir(parents=True)
            (nested / "__init__.pyi").write_text("", encoding="utf-8")
            self.assertEqual(projectdata.typing_facts(directory), (False, True))
            (package / "py.typed").write_text("partial\n", encoding="utf-8")
            self.assertEqual(projectdata.typing_facts(directory), (True, False))
            (package / "py.typed").unlink()
            (package / "py.typed").mkdir()
            self.assertEqual(projectdata.typing_facts(directory), (False, False))
            (package / "py.typed").rmdir()
            (package / "py.typed").symlink_to(package / "absent-marker")
            self.assertEqual(projectdata.typing_facts(directory), (False, False))

    def test_excluded_directories_and_symlinks(self) -> None:
        excluded = (".hidden", ".venv", "venv", "env", "__pycache__", "build", "dist", "node_modules", "custom-env")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in excluded:
                package = root / name / "example-stubs"
                package.mkdir(parents=True)
                (package / "py.typed").write_text("", encoding="utf-8")
                (package / "__init__.pyi").write_text("", encoding="utf-8")
            for name in ("venv", "env", "custom-env"):
                (root / name / "pyvenv.cfg").write_text("", encoding="utf-8")
            (root / "linked").symlink_to(root / "build", target_is_directory=True)
            (root / "py.typed").symlink_to(root / "build" / "example-stubs" / "py.typed")
            self.assertEqual(projectdata.typing_facts(root), (False, False))

    def test_reserved_directory_names_can_be_source_packages(self) -> None:
        layouts = (
            "src/build",
            "src/dist",
            "src/_build",
            "src/env",
            "src/venv",
            "src/virtualenv",
            "build",
            "dist",
            "_build",
            "env",
            "venv",
            "virtualenv",
            "custom/python/pkg/build",
        )
        for layout in layouts:
            with self.subTest(layout=layout), tempfile.TemporaryDirectory() as directory:
                package = Path(directory) / layout
                package.mkdir(parents=True)
                (package / "module.py").touch()
                (package / "py.typed").touch()
                self.assertEqual(projectdata.typing_facts(directory), (True, False))

    def _assert_typing_tree_is_pruned(self, root: Path, excluded: Path) -> None:
        original_scandir = projectdata.os.scandir

        def guarded_scandir(path: projectdata.Pathish):
            self.assertFalse(Path(path).is_relative_to(excluded), f"Traversed excluded tree: {path}")
            return original_scandir(path)

        with unittest.mock.patch.object(projectdata.os, "scandir", new=guarded_scandir):
            self.assertEqual(projectdata.typing_facts(root), (False, False))

    def test_environment_markers_prune_trees_without_traversing_them(self) -> None:
        cases = (
            ("env", "conda-meta/history", False),
            ("custom-conda", "conda-meta/history", False),
            ("venv", "pyvenv.cfg", False),
            ("custom-venv", "pyvenv.cfg", True),
        )
        for name, marker, symlink in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                environment = root / name
                package = environment / "lib" / "python3.13" / "site-packages" / "example-stubs"
                package.mkdir(parents=True)
                (package / "py.typed").touch()
                (package / "__init__.pyi").touch()
                config = environment / marker
                config.parent.mkdir(parents=True, exist_ok=True)
                if symlink:
                    target = root / "shared-venv-config"
                    target.touch()
                    config.symlink_to(target)
                else:
                    config.touch()
                self._assert_typing_tree_is_pruned(root, environment)
                sibling = root / "src" / "valid"
                sibling.mkdir(parents=True)
                (sibling / "module.py").touch()
                (sibling / "py.typed").touch()
                self.assertEqual(projectdata.typing_facts(root), (True, False))

    def test_conda_history_directory_does_not_mark_an_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "env"
            (package / "conda-meta" / "history").mkdir(parents=True)
            (package / "module.py").touch()
            (package / "py.typed").touch()
            self.assertEqual(projectdata.typing_facts(directory), (True, False))

    def test_non_environment_markers_do_not_prune_sources(self) -> None:
        cases = (
            ("pyvenv.cfg", "missing"),
            ("pyvenv.cfg", "directory"),
            ("conda-meta/history", "missing"),
            ("conda-meta/history", "regular"),
        )
        for marker, target_kind in cases:
            with self.subTest(marker=marker, target_kind=target_kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                package = root / "env"
                package.mkdir()
                (package / "module.py").touch()
                (package / "py.typed").touch()
                target = root / "environment-marker"
                if target_kind == "regular":
                    target.touch()
                elif target_kind == "directory":
                    target.mkdir()
                link = package / marker
                link.parent.mkdir(exist_ok=True)
                link.symlink_to(target)
                self.assertEqual(projectdata.typing_facts(root), (True, False))

    def test_symlinked_conda_metadata_directory_does_not_prune_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "env"
            package.mkdir()
            (package / "module.py").touch()
            (package / "py.typed").touch()
            metadata = root / "external-metadata"
            metadata.mkdir()
            (metadata / "history").touch()
            (package / "conda-meta").symlink_to(metadata, target_is_directory=True)
            self.assertEqual(projectdata.typing_facts(root), (True, False))

    def test_conda_metadata_file_does_not_mark_an_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "env"
            package.mkdir()
            (package / "conda-meta").touch()
            (package / "module.py").touch()
            (package / "py.typed").touch()
            self.assertEqual(projectdata.typing_facts(directory), (True, False))

    def test_nested_project_output_is_pruned_without_traversing_descendants(self) -> None:
        for config_name in ("pyproject.toml", "setup.py", "setup.cfg"):
            for output in ("build", "dist", "_build"):
                with self.subTest(config=config_name, output=output), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory).resolve()
                    project = root / "packages" / "member"
                    generated = project / output / "lib"
                    package = generated / "example-stubs"
                    package.mkdir(parents=True)
                    (project / config_name).touch()
                    (package / "py.typed").touch()
                    (package / "__init__.pyi").touch()
                    self._assert_typing_tree_is_pruned(root, generated)
                    (project / output / "module.py").touch()
                    self.assertEqual(projectdata.typing_facts(root), (True, True))

    def test_reserved_namespace_packages_remain_source_directories(self) -> None:
        for name in ("build", "dist", "_build", "env", "venv"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                package = Path(directory) / "src" / name
                namespace = package / "widget"
                namespace.mkdir(parents=True)
                (namespace / "module.py").touch()
                (package / "py.typed").touch()
                self.assertEqual(projectdata.typing_facts(directory), (True, False))

    def test_project_boundary_requires_regular_configuration(self) -> None:
        for config_name in ("pyproject.toml", "setup.py", "setup.cfg"):
            for kind in ("directory", "symlink"):
                with self.subTest(config=config_name, kind=kind), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    project = root / "member"
                    package = project / "build"
                    namespace = package / "widget"
                    namespace.mkdir(parents=True)
                    (namespace / "module.py").touch()
                    (package / "py.typed").touch()
                    config = project / config_name
                    if kind == "directory":
                        config.mkdir()
                    else:
                        target = root / "configuration"
                        target.touch()
                        config.symlink_to(target)
                    self.assertEqual(projectdata.typing_facts(root), (True, False))

    def test_project_boundary_probes_are_limited_to_generated_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for member in ("generated", "source"):
                project = root / member
                project.mkdir()
                (project / "pyproject.toml").touch()
            for output in ("build", "dist", "_build"):
                (root / "generated" / output).mkdir()
            original_lstat = Path.lstat
            probes: list[Path] = []

            def counted_lstat(path: Path):
                probes.append(path)
                return original_lstat(path)

            with unittest.mock.patch.object(Path, "lstat", new=counted_lstat):
                self.assertEqual(projectdata.typing_facts(root), (False, False))
            self.assertEqual(probes.count(root / "generated" / "pyproject.toml"), 1)
            self.assertNotIn(root / "source" / "pyproject.toml", probes)

    def test_isolated_markers_need_source_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "module.py").touch()
            (root / "py.typed").touch()
            for name in ("docs", "tests", "pkg"):
                package = root / name
                package.mkdir()
                (package / "py.typed").touch()
            self.assertEqual(projectdata.typing_facts(root), (False, False))
            namespace = root / "pkg" / "namespace" / "subpackage"
            namespace.mkdir(parents=True)
            (namespace / "module.py").touch()
            self.assertEqual(projectdata.typing_facts(root), (True, False))
            (root / "pkg" / "py.typed").unlink()
            self.assertEqual(projectdata.typing_facts(root), (False, False))
            # A Python-bearing test package is eligible; this scan does not prove wheel inclusion.
            (root / "tests" / "module.py").touch()
            self.assertEqual(projectdata.typing_facts(root), (True, False))

    def test_source_symlinks_do_not_supply_marker_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "pkg"
            package.mkdir()
            (package / "py.typed").touch()
            (root / "real.py").touch()
            (package / "module.py").symlink_to(root / "real.py")
            self.assertEqual(projectdata.typing_facts(root), (False, False))
            (package / "module.py").unlink()
            (package / "module.py").touch()
            (package / "py.typed").unlink()
            (package / "py.typed").mkdir()
            self.assertEqual(projectdata.typing_facts(root), (False, False))

    def test_inaccessible_typing_entries_do_not_hide_valid_siblings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for name in ("blocked", "valid"):
                package = root / name
                package.mkdir()
                (package / "module.py").touch()
                (package / "py.typed").touch()
            (root / "blocked" / "conda-meta").mkdir()
            for name in ("py.typed", "pyvenv.cfg", "conda-meta", "conda-meta/history"):
                operation = "stat" if name == "pyvenv.cfg" else "lstat"
                original_probe = getattr(Path, operation)

                def denied_probe(
                    path: Path,
                    *,
                    follow_symlinks: bool = True,
                    denied: str = name,
                    probe: Callable[..., os.stat_result] = original_probe,
                    method: str = operation,
                ):
                    if path == root / "blocked" / denied:
                        raise PermissionError
                    if method == "stat":
                        return probe(path, follow_symlinks=follow_symlinks)
                    return probe(path)

                with self.subTest(name=name), unittest.mock.patch.object(Path, operation, new=denied_probe):
                    self.assertEqual(projectdata.typing_facts(root), (True, False))
                    (root / "valid" / "py.typed").unlink()
                    self.assertEqual(projectdata.typing_facts(root), (False, False))
                (root / "valid" / "py.typed").touch()

    def test_inaccessible_source_file_does_not_supply_marker_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            package = root / "pkg"
            package.mkdir()
            (package / "module.py").touch()
            (package / "py.typed").touch()
            original_lstat = Path.lstat

            def denied_lstat(path: Path):
                if path == package / "module.py":
                    raise PermissionError
                return original_lstat(path)

            with unittest.mock.patch.object(Path, "lstat", new=denied_lstat):
                self.assertEqual(projectdata.typing_facts(root), (False, False))

    def test_stub_marker_probe_and_read_errors_are_not_complete_stubs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            package = root / "example-stubs"
            package.mkdir()
            (package / "__init__.pyi").touch()
            marker = package / "py.typed"
            marker.touch()
            original_lstat = Path.lstat

            def denied_lstat(path: Path):
                if path == marker:
                    raise PermissionError
                return original_lstat(path)

            with unittest.mock.patch.object(Path, "lstat", new=denied_lstat):
                self.assertEqual(projectdata.typing_facts(root), (False, False))
            with unittest.mock.patch.object(Path, "read_text", side_effect=PermissionError("stub marker read denied")):
                self.assertEqual(projectdata.typing_facts(root), (True, False))
            marker.write_bytes(b"\xff")
            self.assertEqual(projectdata.typing_facts(root), (True, False))

    def test_missing_and_inaccessible_typing_roots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(projectdata.typing_facts(Path(directory) / "missing"), (False, False))
            with unittest.mock.patch.object(Path, "resolve", side_effect=PermissionError("root resolution denied")):
                self.assertEqual(projectdata.typing_facts(directory), (False, False))

    def test_inaccessible_generated_directory_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "build").mkdir()
            original_scandir = projectdata.os.scandir

            def denied_scandir(path: projectdata.Pathish):
                if Path(path) == root / "build":
                    raise PermissionError
                return original_scandir(path)

            with unittest.mock.patch.object(projectdata.os, "scandir", new=denied_scandir):
                self.assertEqual(projectdata.typing_facts(root), (False, False))

    def test_irrelevant_files_do_not_incidentally_get_stat_probes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            package = root / "pkg"
            package.mkdir()
            module = package / "module.py"
            marker = package / "py.typed"
            module.touch()
            marker.touch()
            original_lstat = Path.lstat
            probed: list[Path] = []

            def counted_lstat(path: Path):
                probed.append(path)
                return original_lstat(path)

            counts = []
            for count in (100, 1000):
                for number in range(count):
                    (package / f"unrelated-{number}.txt").touch()
                probed.clear()
                with unittest.mock.patch.object(Path, "lstat", new=counted_lstat):
                    self.assertEqual(projectdata.typing_facts(root), (True, False))
                self.assertEqual(probed.count(module), 1)
                self.assertEqual(probed.count(marker), 1)
                self.assertFalse(any(path.suffix == ".txt" for path in probed))
                counts.append(len(probed))
            self.assertEqual(counts[0], counts[1])

    @unittest.mock.patch("pyroma.projectdata.build_metadata")
    def test_typing_facts_flow_to_rating(self, metadata_mock: unittest.mock.Mock) -> None:
        metadata_mock.side_effect = lambda *_args, **_kwargs: {"classifier": ["Typing :: Typed"]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pyproject.toml").write_text('[project]\nname = "example"\n', encoding="utf-8")
            package = root / "src" / "example-stubs"
            package.mkdir(parents=True)
            (package / "__init__.pyi").write_text("", encoding="utf-8")
            data = projectdata.get_build_data(root)
            self.assertIs(data["_py_typed"], False)
            self.assertIs(data["_stub_only"], True)
            self.assertIs(ratings.TypedMarker().test(data).outcome, True)
            (package / "py.typed").write_text("partial\n", encoding="utf-8")
            self.assertIs(ratings.TypedMarker().test(projectdata.get_build_data(root)).outcome, True)
        self.assertIs(ratings.TypedMarker().test({"classifier": ["Typing :: Typed"]}).outcome, None)

    def test_unmatched_license_files(self) -> None:
        self.assertEqual(
            projectdata.unmatched_license_files(TESTDATA_DIR / "missing_license_file", ["LICENCE-DOES-NOT-EXIST*"]),
            ["LICENCE-DOES-NOT-EXIST*"],
        )
        self.assertEqual(projectdata.unmatched_license_files(TESTDATA_DIR / "complete", ["LICENSE.txt"]), [])
        # PEP 639 forbids these, and Path.glob would raise on them.
        forbidden = ["/etc/passwd", "../LICENSE.txt", ""]
        self.assertEqual(projectdata.unmatched_license_files(TESTDATA_DIR / "complete", forbidden), forbidden)

    @unittest.mock.patch("pyroma.projectdata.wheel_metadata")
    def test_single_classifier_remains_a_list(self, metadatamock: unittest.mock.Mock) -> None:
        metadata = Message()
        metadata["Name"] = "example"
        metadata["Version"] = "1.0"
        metadata["Classifier"] = "Programming Language :: Python :: 3.14"
        metadatamock.return_value = metadata

        data = projectdata.build_metadata(".")

        self.assertEqual(data["classifier"], ["Programming Language :: Python :: 3.14"])
        self.assertIs(ratings.ClassifierVerification().test(data).outcome, True)
        self.assertIs(ratings.PythonClassifierVersion().test(data).outcome, True)


class DistroDataTest(unittest.TestCase):
    maxDiff = None

    def test_complete(self) -> None:
        directory = TESTDATA_DIR / "distributions"

        for distribution_path in directory.iterdir():
            if distribution_path.name.startswith("complete"):
                data = distributiondata.get_data(distribution_path)
                try:
                    self.assertTrue(data.pop("_sdist"), distribution_path.name)
                    self.assertTrue(Path(data.pop("_path")).is_dir(), distribution_path.name)
                    self.assertTrue(data.pop("_pyproject"), distribution_path.name)
                    self.assertFalse(data.pop("_py_typed"), distribution_path.name)
                    self.assertFalse(data.pop("_stub_only"), distribution_path.name)
                    self.assertEqual(data.pop("_license_files_unmatched"), [], distribution_path.name)
                    self.assertEqual(data, COMPLETE)
                finally:
                    distributiondata.cleanup(data)

    def test_distribution_data_keeps_unpacked_tree(self) -> None:
        # The unpacked tree must stay around after get_data returns, so
        # the pyproject.toml rating tests can inspect it.
        src = TESTDATA_DIR / "distributions" / "complete-1.0.dev1.tar.gz"

        data = distributiondata.get_data(src)

        self.assertTrue(data.get("_sdist"))
        path = data["_path"]
        self.assertTrue(Path(path).is_dir())
        self.assertTrue((Path(path) / "pyproject.toml").exists())
        distributiondata.cleanup(data)
        self.assertFalse(Path(path).exists())

    def test_run_cleans_distribution_after_rating(self) -> None:
        src = TESTDATA_DIR / "distributions" / "complete-1.0.dev1.tar.gz"
        extracted_paths = []

        def rate_project(
            data: Metadata,
            _skip_tests: "list[str] | str | None",
            **_kwargs: object,
        ) -> ratings.RatedProject:
            extracted_paths.append(data["_path"])
            self.assertTrue(Path(data["_path"]).is_dir())
            return ratings.RatedProject(name="complete", rating=10, level=ratings.LEVELS[10], problems=[])

        with (
            unittest.mock.patch("pyroma.ratings.rate_project", side_effect=rate_project),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            results = [pyroma.run("file", str(src), quiet=True) for _ in range(3)]

        self.assertEqual(results, [10, 10, 10])
        self.assertEqual(len(extracted_paths), 3)
        self.assertTrue(all(not Path(path).exists() for path in extracted_paths))

    def test_run_cleans_distribution_when_rating_fails(self) -> None:
        src = TESTDATA_DIR / "distributions" / "complete-1.0.dev1.tar.gz"
        extracted_paths = []

        def fail_rating(
            data: Metadata,
            _skip_tests: "list[str] | str | None",
            **_kwargs: object,
        ) -> Never:
            extracted_paths.append(data["_path"])
            self.assertTrue(Path(data["_path"]).is_dir())
            message = "rating failed"
            raise ValueError(message)

        with (
            unittest.mock.patch("pyroma.ratings.rate_project", side_effect=fail_rating),
            self.assertRaisesRegex(ValueError, "rating failed"),
        ):
            pyroma.run("file", str(src), quiet=True)

        self.assertEqual(len(extracted_paths), 1)
        self.assertFalse(Path(extracted_paths[0]).exists())

    def test_fallback_tar_extractor_rejects_links_and_devices(self) -> None:
        unsafe_types = (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE)

        for member_type in unsafe_types:
            with self.subTest(member_type=member_type), tempfile.TemporaryDirectory() as tmp:
                member = tarfile.TarInfo("package/unsafe")
                member.type = member_type
                member.linkname = "../../outside"
                archive = unittest.mock.Mock(name="unsafe.tar")
                archive.name = "unsafe.tar"
                archive.getmembers.return_value = [member]

                with self.assertRaisesRegex(ValueError, "Unsafe member"):
                    distributiondata._safe_extract_tar(archive, tmp)

                archive.extractall.assert_not_called()

    def test_fallback_tar_extractor_preserves_regular_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            member = tarfile.TarInfo("package/data.txt")
            archive = unittest.mock.Mock(name="safe.tar")
            archive.getmembers.return_value = [member]

            distributiondata._safe_extract_tar(archive, tmp)

            archive.extractall.assert_called_once_with(tmp)
