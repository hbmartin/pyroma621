import contextlib
import copy
import io
import json
import shutil
import tarfile
import tempfile
import unittest
import unittest.mock
from email.message import Message
from pathlib import Path
from typing import Never
from xmlrpc import client as xmlrpclib

from hypothesis import example, given, settings
from hypothesis import strategies as st

import pyroma
from pyroma import distributiondata, projectdata, pypidata, ratings, report
from pyroma.metadata import Metadata
from pyroma.ratings import rate

TESTDATA_DIR = Path(__file__).parent / "testdata"
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
            "_sdist": st.booleans(),
            "_owners": st.lists(_METADATA_TEXT, max_size=4),
            "_wheel_build_failed": st.booleans(),
            "_missing_pyproject_toml": st.booleans(),
            "_missing_build_system": st.booleans(),
            "_no_config_found": st.booleans(),
            "_has_sdist": st.booleans(),
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
        for problem in first.problems:
            self.assertTrue(problem.test)
            self.assertTrue(problem.message)
            self.assertGreaterEqual(problem.weight, 0)
            self.assertIsInstance(problem.fatal, bool)


class RatingsTest(unittest.TestCase):
    maxDiff = None

    def _get_file_rating(
        self,
        dirname: str,
        skip_tests: "list[str] | str | None" = None,
    ) -> "tuple[int, list[str]]":
        directory = TESTDATA_DIR / dirname
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
        testdata["_path"] = TESTDATA_DIR / "invalid_pyproject"

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
        self.assertTrue(any("'broken ==' is not a valid dependency specifier" in msg for msg in messages))

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
        testdata["_path"] = TESTDATA_DIR / "bad_console_scripts"

        rating = rate(testdata)
        self.assertEqual(rating[0], 0)
        self.assertTrue(any("Console and GUI scripts must be defined in [project.scripts]" in msg for msg in rating[1]))

    def test_readme_both_file_and_text_is_fatal(self) -> None:
        testdata = COMPLETE.copy()
        testdata["_path"] = TESTDATA_DIR / "readme_both"

        rating = rate(testdata)
        self.assertEqual(rating[0], 0)
        self.assertTrue(any("The 'readme' table must not specify both 'file' and 'text'." in msg for msg in rating[1]))

    def test_dynamic_name_is_fatal(self) -> None:
        testdata = COMPLETE.copy()
        testdata["_path"] = TESTDATA_DIR / "dynamic_name"

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
            testdata["_path"] = tmp

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
                    }
                ],
                "_meta": {"mode": "directory"},
            },
        )


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

        self.assertEqual(data, COMPLETE)

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
