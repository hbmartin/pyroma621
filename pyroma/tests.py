import io
import json
import os
import unittest
import unittest.mock
from pathlib import Path
from xmlrpc import client as xmlrpclib

import pyroma
from pyroma import distributiondata, projectdata, pypidata, ratings
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


class ProxyStub:
    def set_debug_context(self, dataname, real_class, developmode):
        filename = TESTDATA_DIR / "xmlrpcdata" / dataname
        data = {}
        with open(filename, encoding="UTF-8") as f:
            exec(f.read(), None, data)
        self.args = data["args"]
        self.kw = data["kw"]
        self._data = data["data"]

        if developmode:
            self._real = real_class(*self.args, **self.kw)
        else:
            self._real = None

    def __call__(self, *args, **kw):
        assert args == self.args
        assert kw == self.kw
        return self

    def _make_proxy(self, name):
        def _proxy_method(*args, **kw):
            return self._data[name][args]

        return _proxy_method

    def _make_unknown_proxy(self, name):
        def _proxy_method(*args, **kw):
            if self._real is None:
                raise AttributeError("ProxyStub unkown method " + name)
            print()
            print("== ProxyStub unknown method ==")
            print(name, ":", args, kw)
            result = getattr(self._real, name)(*args, **kw)
            print("Result :")
            print(result)
            return result

        return _proxy_method

    def __getattr__(self, attr):
        if attr in ("_data", "_make_proxy", "_make_unknown_proxy"):
            raise AttributeError("Break infinite recursion chain")
        if attr in self._data:
            return self._make_proxy(attr)
        return self._make_unknown_proxy(attr)

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        return


proxystub = ProxyStub()


class RatingsTest(unittest.TestCase):
    maxDiff = None

    def _get_file_rating(self, dirname, skip_tests=None):
        directory = TESTDATA_DIR / dirname
        data = projectdata.get_data(directory)
        return rate(data, skip_tests)

    def test_complete(self):
        data = projectdata.get_data(TESTDATA_DIR / "complete")
        rating = rate(data)
        # Should have a perfect score
        self.assertEqual(rating, (10, []))

    def test_setup_config(self):
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

    def test_only_config(self):
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
                    "tools may fail."
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

    def test_skip_tests(self):
        # Find all errors
        all_errors = self._get_file_rating("lacking")[1]

        fewer_errors = self._get_file_rating(
            "lacking", skip_tests=["PythonRequiresVersion", "Description", "Summary", "Classifiers"]
        )[1]

        self.assertEqual(len(all_errors), 13)
        # Errors have been skipped!
        self.assertEqual(len(fewer_errors), 9)

    def test_pep517(self):
        rating = self._get_file_rating("pep517")
        self.assertGreaterEqual(rating[0], 9)

    def test_pep621(self):
        rating = self._get_file_rating("pep621")
        self.assertGreaterEqual(rating[0], 9)

    def test_uv_build(self):
        rating = self._get_file_rating("uv_build")
        self.assertGreaterEqual(rating[0], 9)

    def test_minimal(self):
        rating = self._get_file_rating("minimal")
        self.assertEqual(
            rating,
            (
                2,
                [
                    "The package's Summary should be longer than 10 characters.",
                    "The package's Description is quite short.",
                    "Your package does not have classifier data.",
                    "The classifiers should specify what Python versions you support."
                    "You can find the list of standard classifiers here: https://pypi.org/classifiers/",
                    "You should specify what Python versions you support with the 'Requires-Python' metadata.",
                    "Your package does not have keywords data.",
                    "Your package does not have author data.",
                    "Your package does not have author-email data.",
                    "Your package should have a 'url' field with a link to the project home page, or a "
                    "'project_urls' field, with a dictionary of links, or both.",
                    "You should specify a license for your package with the 'License-Expression' field. See "
                    "https://packaging.python.org/en/latest/specifications/core-metadata/#license-expression "
                    "for more information.",
                    "Specifying a development status in the classifiers gives users "
                    "a hint of how stable your software is. See https://pypi.org/classifiers/",
                    "Check-manifest returned errors",
                ],
            ),
        )

    def test_lacking(self):
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
                    "The classifiers should specify what Python versions you support."
                    "You can find the list of standard classifiers here: https://pypi.org/classifiers/",
                    "You should specify what Python versions you support with the 'Requires-Python' metadata.",
                    "Your package does not have keywords data.",
                    "Your package does not have author data.",
                    "Your package does not have author-email data.",
                    "Your package should have a 'url' field with a link to the project home page, or a "
                    "'project_urls' field, with a dictionary of links, or both.",
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

    def test_custom_test(self):
        rating = self._get_file_rating("custom_test")

        self.assertEqual(
            rating,
            (
                4,
                [
                    "The package's Summary should be longer than 10 characters.",
                    "The package's Description is quite short.",
                    "Your package does not have classifier data.",
                    "The classifiers should specify what Python versions you support."
                    "You can find the list of standard classifiers here: https://pypi.org/classifiers/",
                    "You should specify what Python versions you support with the 'Requires-Python' metadata.",
                    "Your package does not have keywords data.",
                    "Your package does not have author data.",
                    "Your package does not have author-email data.",
                    "Your package should have a 'url' field with a link to the project home page, or a "
                    "'project_urls' field, with a dictionary of links, or both.",
                    "You should specify a license for your package with the 'License-Expression' field. See "
                    "https://packaging.python.org/en/latest/specifications/core-metadata/#license-expression "
                    "for more information.",
                    "Specifying a development status in the classifiers gives users "
                    "a hint of how stable your software is. See https://pypi.org/classifiers/",
                ],
            ),
        )

    def test_private_classifier(self):
        rating = self._get_file_rating("private_classifier")
        self.assertGreaterEqual(rating[0], 9)

    def test_invalid_pyproject(self):
        # Use valid metadata so we exercise the rating check itself,
        # then point _path to a fixture with an invalid pyproject.toml.
        testdata = COMPLETE.copy()
        testdata["_path"] = TESTDATA_DIR / "invalid_pyproject"

        rating = rate(testdata)
        self.assertLess(rating[0], 10)
        self.assertTrue(any("pyproject.toml is invalid" in msg for msg in rating[1]))

    def test_markdown(self):
        # Markdown and text shouldn't get ReST errors
        testdata = COMPLETE.copy()
        testdata["description"] = "# Broken ReST\n\n``Valid  Markdown\n"
        testdata["description-content-type"] = "text/markdown"

        rating = rate(testdata)
        self.assertEqual(rating, (9, ["The package's Description is quite short."]))

        testdata["description-content-type"] = "text/plain"
        rating = rate(testdata)
        self.assertEqual(rating, (9, ["The package's Description is quite short."]))

    def test_deprecated_metadata_field_warning(self):
        testdata = COMPLETE.copy()
        testdata.pop("project-url", None)
        testdata["home-page"] = "https://example.com"

        rating = rate(testdata)

        self.assertTrue(
            any("The metadata field 'home-page' is deprecated; use 'project-url' instead." in msg for msg in rating[1])
        )

    def test_deprecated_license_warning_respects_metadata_version(self):
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

    def _messages(self, testdata):
        return rate(testdata)[1]

    def test_invalid_version(self):
        testdata = COMPLETE.copy()
        testdata["version"] = "1.0-foo"

        rating = rate(testdata)
        self.assertLess(rating[0], 10)
        self.assertTrue(any("'1.0-foo' is not a valid version" in msg for msg in rating[1]))

    def test_noncanonical_version(self):
        testdata = COMPLETE.copy()
        testdata["version"] = "1.0.DEV1"

        messages = self._messages(testdata)
        self.assertTrue(any("not in canonical form; it should be written as '1.0.dev1'" in msg for msg in messages))

    def test_version_epoch_and_local_segment(self):
        testdata = COMPLETE.copy()
        testdata["version"] = "1!1.0+ubuntu1"

        messages = self._messages(testdata)
        self.assertTrue(any("uses a version epoch" in msg for msg in messages))
        self.assertTrue(any("contains a local version segment" in msg for msg in messages))

    def test_invalid_metadata_version(self):
        testdata = COMPLETE.copy()
        testdata["metadata-version"] = "2.0"

        messages = self._messages(testdata)
        self.assertTrue(any("'2.0' is not a valid Metadata-Version" in msg for msg in messages))

    def test_invalid_name_is_fatal(self):
        testdata = COMPLETE.copy()
        testdata["name"] = "-not-valid-"

        rating = rate(testdata)
        self.assertEqual(rating[0], 0)
        self.assertTrue(any("'-not-valid-' is not a valid project name" in msg for msg in rating[1]))

    def test_license_and_license_expression_is_fatal(self):
        testdata = COMPLETE.copy()
        testdata["license"] = "MIT"

        rating = rate(testdata)
        self.assertEqual(rating[0], 0)
        self.assertTrue(
            any("Specifying both a License and a License-Expression is forbidden" in msg for msg in rating[1])
        )

    def test_invalid_license_expression_is_fatal(self):
        testdata = COMPLETE.copy()
        testdata["license-expression"] = "Bogus-License"

        rating = rate(testdata)
        self.assertEqual(rating[0], 0)
        self.assertTrue(any("'Bogus-License' is not a valid SPDX license expression" in msg for msg in rating[1]))

    def test_invalid_description_content_type(self):
        testdata = COMPLETE.copy()
        testdata["description-content-type"] = "text/html"

        messages = self._messages(testdata)
        self.assertTrue(any("The content type should be one of" in msg for msg in messages))

    def test_description_content_type_parameters(self):
        testdata = COMPLETE.copy()
        testdata["description-content-type"] = "text/plain; charset=latin-1; variant=GFM"

        messages = self._messages(testdata)
        message = next(msg for msg in messages if "Description-Content-Type" in msg)
        self.assertIn("The only accepted charset is UTF-8, not 'latin-1'.", message)
        self.assertIn("The 'variant' parameter is only valid for text/markdown.", message)

    def test_markdown_variant(self):
        testdata = COMPLETE.copy()
        testdata["description-content-type"] = "text/markdown; variant=GFM"
        self.assertFalse(any("Description-Content-Type" in msg for msg in self._messages(testdata)))

        testdata["description-content-type"] = "text/markdown; variant=Pandoc"
        messages = self._messages(testdata)
        self.assertTrue(
            any("The markdown variant should be GFM or CommonMark, not 'Pandoc'." in msg for msg in messages)
        )

    def test_invalid_requires_dist(self):
        testdata = COMPLETE.copy()
        testdata["requires-dist"] = ["zope.event", "broken =="]

        messages = self._messages(testdata)
        self.assertTrue(any("'broken ==' is not a valid dependency specifier" in msg for msg in messages))

    def test_requires_dist_style_warnings(self):
        testdata = COMPLETE.copy()
        testdata["requires-dist"] = [
            "zope.event (>=4.0)",
            'zope.interface; sys_platform > "linux"',
        ]

        messages = self._messages(testdata)
        message = next(msg for msg in messages if "Requires-Dist" in msg)
        self.assertIn("puts the version specifier in parentheses", message)
        self.assertIn("ordered comparison on the 'sys_platform' environment marker", message)

    def test_url_label_too_long_is_fatal(self):
        testdata = COMPLETE.copy()
        testdata["project-url"] = ["this label is far too long to be legal, https://example.com"]

        rating = rate(testdata)
        self.assertEqual(rating[0], 0)
        self.assertTrue(any("Project-URL labels are limited to 32 characters" in msg for msg in rating[1]))

    def test_url_no_well_known_labels(self):
        testdata = COMPLETE.copy()
        testdata["project-url"] = ["weird stuff, https://example.com"]

        messages = self._messages(testdata)
        self.assertTrue(any("None of your Project-URL labels match the well-known labels" in msg for msg in messages))

    def test_console_scripts_in_entry_points_is_fatal(self):
        testdata = COMPLETE.copy()
        testdata["_path"] = TESTDATA_DIR / "bad_console_scripts"

        rating = rate(testdata)
        self.assertEqual(rating[0], 0)
        self.assertTrue(any("Console and GUI scripts must be defined in [project.scripts]" in msg for msg in rating[1]))

    def test_readme_both_file_and_text_is_fatal(self):
        testdata = COMPLETE.copy()
        testdata["_path"] = TESTDATA_DIR / "readme_both"

        rating = rate(testdata)
        self.assertEqual(rating[0], 0)
        self.assertTrue(any("The 'readme' table must not specify both 'file' and 'text'." in msg for msg in rating[1]))

    def test_dynamic_name_is_fatal(self):
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


class PyPITest(unittest.TestCase):
    maxDiff = None

    @unittest.mock.patch("xmlrpc.client.ServerProxy", proxystub)
    @unittest.mock.patch("pyroma.pypidata._get_project_data")
    @unittest.mock.patch("requests.get")
    def test_complete(self, requestmock, projectdatamock):
        datafile = TESTDATA_DIR / "jsondata" / "complete.json"
        with open(datafile, encoding="UTF-8") as file:
            projectdatamock.return_value = json.load(file)

        srcfile = TESTDATA_DIR / "distributions" / "complete-1.0.dev1.tar.gz"
        with open(srcfile, "rb") as file:
            requestmock.return_value = unittest.mock.Mock()
            requestmock.return_value.content = file.read()

        proxystub.set_debug_context("completedata.py", xmlrpclib.ServerProxy, False)
        data = pypidata.get_data("complete")
        rating = rate(data)

        self.assertEqual(rating, (10, []))

    @unittest.mock.patch("pyroma.pypidata.requests.get")
    def test_get_project_data_custom_index_url(self, requestmock):
        requestmock.return_value = unittest.mock.Mock()
        requestmock.return_value.ok = True
        requestmock.return_value.status_code = 200
        requestmock.return_value.json.return_value = {}

        pypidata._get_project_data("internalpkg", index_url="https://packages.example.com")

        requestmock.assert_called_once_with(
            "https://packages.example.com/pypi/internalpkg/json", timeout=pypidata.REQUEST_TIMEOUT
        )

    @unittest.mock.patch("pyroma.pypidata.requests.get")
    def test_get_project_data_custom_index_url_with_pypi_path(self, requestmock):
        requestmock.return_value = unittest.mock.Mock()
        requestmock.return_value.ok = True
        requestmock.return_value.status_code = 200
        requestmock.return_value.json.return_value = {}

        pypidata._get_project_data("internalpkg", index_url="https://packages.example.com/pypi")

        requestmock.assert_called_once_with(
            "https://packages.example.com/pypi/internalpkg/json", timeout=pypidata.REQUEST_TIMEOUT
        )

    @unittest.mock.patch("pyroma.pypidata.xmlrpc.client.ServerProxy")
    @unittest.mock.patch("pyroma.pypidata._get_project_data")
    def test_get_data_custom_index_url_uses_xmlrpc_endpoint(self, projectdatamock, proxymock):
        projectdatamock.return_value = {
            "info": {"name": "internalpkg", "version": "1.2.3"},
            "releases": {"1.2.3": []},
        }

        proxymock.return_value.__enter__.return_value.package_roles.return_value = [("Owner", "dev1")]

        data = pypidata.get_data("internalpkg", index_url="https://packages.example.com")

        self.assertEqual(data["_owners"], ["dev1"])
        proxymock.assert_called_once_with("https://packages.example.com/pypi")

    @unittest.mock.patch("pyroma.ratings.rate_project")
    @unittest.mock.patch("pyroma.pypidata.get_data")
    def test_run_forwards_custom_index_url(self, datamock, ratemock):
        datamock.return_value = {"name": "internalpkg"}
        ratemock.return_value = ratings.RatedProject(
            name="internalpkg", rating=10, level=ratings.LEVELS[10], problems=[]
        )

        result = pyroma.run("pypi", "internalpkg", quiet=True, index_url="https://packages.example.com")

        self.assertEqual(result, 10)
        datamock.assert_called_once_with("internalpkg", index_url="https://packages.example.com")


class ProjectDataTest(unittest.TestCase):
    maxDiff = None

    def test_complete(self):
        directory = TESTDATA_DIR / "complete"

        data = projectdata.get_data(directory)
        del data["_path"]  # This changes, so I just ignore it

        self.assertEqual(data, COMPLETE)


class DistroDataTest(unittest.TestCase):
    maxDiff = None

    def test_complete(self):
        directory = TESTDATA_DIR / "distributions"

        for filename in os.listdir(directory):
            if filename.startswith("complete"):
                data = distributiondata.get_data(directory / filename)
                self.assertEqual(data, COMPLETE)
