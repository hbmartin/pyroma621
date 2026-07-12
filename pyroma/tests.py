import io
import json
import os
import unittest
import unittest.mock
from pathlib import Path
from xmlrpc import client as xmlrpclib

import pyroma
from pyroma import distributiondata, projectdata, pypidata
from pyroma.ratings import Problem, Rating, rate
from pyroma.report import JsonReporter, TextReporter


def astuple(rating):
    """The (rating, messages) view of a Rating, as the old rate() returned."""
    return (rating.rating, rating.messages)


TESTDATA_DIR = Path(__file__).parent / "testdata"
long_description = (TESTDATA_DIR / "complete" / "README.txt").read_text(encoding="UTF-8")
# Translate newlines to universal format
long_description = io.StringIO(long_description, newline=None).read()

COMPLETE: dict = {
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
        data: dict = {}
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
        self.assertEqual(astuple(rating), (10, []))

    def test_setup_config(self):
        rating = self._get_file_rating("setup_config")
        self.assertEqual(
            astuple(rating),
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
            astuple(rating),
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
        all_errors = self._get_file_rating("lacking").messages

        fewer_errors = self._get_file_rating(
            "lacking", skip_tests=["PythonRequiresVersion", "Description", "Summary", "Classifiers"]
        ).messages

        self.assertEqual(len(all_errors), 13)
        # Errors have been skipped!
        self.assertEqual(len(fewer_errors), 9)

    def test_pep517(self):
        rating = self._get_file_rating("pep517")
        self.assertGreaterEqual(rating.rating, 9)

    def test_pep621(self):
        rating = self._get_file_rating("pep621")
        self.assertGreaterEqual(rating.rating, 9)

    def test_uv_build(self):
        rating = self._get_file_rating("uv_build")
        self.assertGreaterEqual(rating[0], 9)

    def test_minimal(self):
        rating = self._get_file_rating("minimal")
        self.assertEqual(
            astuple(rating),
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
            astuple(rating),
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
            astuple(rating),
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
        self.assertGreaterEqual(rating.rating, 9)

    def test_invalid_pyproject(self):
        # Use valid metadata so we exercise the rating check itself,
        # then point _path to a fixture with an invalid pyproject.toml.
        testdata = COMPLETE.copy()
        testdata["_path"] = TESTDATA_DIR / "invalid_pyproject"

        rating = rate(testdata)
        self.assertLess(rating.rating, 10)
        self.assertTrue(any("pyproject.toml is invalid" in msg for msg in rating.messages))

    def test_markdown(self):
        # Markdown and text shouldn't get ReST errors
        testdata = COMPLETE.copy()
        testdata["description"] = "# Broken ReST\n\n``Valid  Markdown\n"
        testdata["description-content-type"] = "text/markdown"

        rating = rate(testdata)
        self.assertEqual(astuple(rating), (9, ["The package's Description is quite short."]))

        testdata["description-content-type"] = "text/plain"
        rating = rate(testdata)
        self.assertEqual(astuple(rating), (9, ["The package's Description is quite short."]))

    def test_deprecated_metadata_field_warning(self):
        testdata = COMPLETE.copy()
        testdata.pop("project-url", None)
        testdata["home-page"] = "https://example.com"

        rating = rate(testdata)

        self.assertTrue(
            any(
                "The metadata field 'home-page' is deprecated; use 'project-url' instead." in msg
                for msg in rating.messages
            )
        )

    def test_deprecated_license_warning_respects_metadata_version(self):
        old_metadata = COMPLETE.copy()
        old_metadata["metadata-version"] = "2.3"
        old_metadata["license"] = "MIT"
        old_metadata.pop("license-expression", None)

        old_rating = rate(old_metadata)
        self.assertFalse(any("The metadata field 'license' is deprecated" in msg for msg in old_rating.messages))

        new_metadata = COMPLETE.copy()
        new_metadata["metadata-version"] = "2.4"
        new_metadata["license"] = "MIT"
        new_metadata.pop("license-expression", None)

        new_rating = rate(new_metadata)
        self.assertTrue(any("The metadata field 'license' is deprecated" in msg for msg in new_rating.messages))


class SpecComplianceTest(unittest.TestCase):
    """Tests for the strict packaging-specification checks.

    These all start from the COMPLETE metadata, which rates a perfect 10,
    and break one thing at a time.
    """

    maxDiff = None

    def _rate_with(self, **overrides):
        testdata = COMPLETE.copy()
        for key, value in overrides.items():
            key = key.replace("_", "-")
            if value is None:
                testdata.pop(key, None)
            else:
                testdata[key] = value
        return rate(testdata)

    def test_complete_baseline(self):
        self.assertEqual(astuple(self._rate_with()), (10, []))

    def test_invalid_version_is_fatal(self):
        rating = self._rate_with(version="1.0-broken-version!")
        self.assertEqual(rating.rating, 0)
        self.assertTrue(any("does not comply with the version specifiers" in msg for msg in rating.messages))

    def test_noncanonical_version(self):
        rating = self._rate_with(version="v1.0")
        self.assertEqual(rating.rating, 9)
        self.assertTrue(any("not in the canonical normalized form '1.0'" in msg for msg in rating.messages))

    def test_local_version_discouraged(self):
        rating = self._rate_with(version="1.0+ubuntu.1")
        self.assertTrue(any("local version segment" in msg for msg in rating.messages))

    def test_version_epoch_discouraged(self):
        rating = self._rate_with(version="2!1.0")
        self.assertTrue(any("version epoch" in msg for msg in rating.messages))

    def test_invalid_metadata_version(self):
        rating = self._rate_with(metadata_version="2.0")
        self.assertTrue(any("'2.0' is not a valid metadata version" in msg for msg in rating.messages))

    def test_invalid_name_is_fatal(self):
        rating = self._rate_with(name="-not-valid-")
        self.assertEqual(rating.rating, 0)
        self.assertTrue(any("not a valid project name" in msg for msg in rating.messages))

    def test_license_and_license_expression_is_fatal(self):
        rating = self._rate_with(license="MIT license text")
        self.assertEqual(rating.rating, 0)
        self.assertTrue(any("both a License and a License-Expression is ambiguous" in msg for msg in rating.messages))

    def test_invalid_spdx_license_expression_is_fatal(self):
        rating = self._rate_with(license_expression="LGPL")
        self.assertEqual(rating.rating, 0)
        self.assertTrue(any("not a valid SPDX license expression" in msg for msg in rating.messages))

    def test_noncanonical_spdx_license_expression(self):
        rating = self._rate_with(license_expression="mit")
        self.assertEqual(rating.rating, 9)
        self.assertTrue(any("canonical normalized form 'MIT'" in msg for msg in rating.messages))

    def test_invalid_description_content_type(self):
        rating = self._rate_with(description_content_type="text/html")
        self.assertTrue(any("'text/html' is not one of the allowed types" in msg for msg in rating.messages))

    def test_invalid_description_content_type_charset(self):
        rating = self._rate_with(description_content_type="text/markdown; charset=latin-1")
        self.assertTrue(any("charset must be UTF-8" in msg for msg in rating.messages))

    def test_invalid_markdown_variant(self):
        rating = self._rate_with(description_content_type="text/markdown; variant=Pandoc")
        self.assertTrue(any("'Pandoc' is not one of GFM or CommonMark" in msg for msg in rating.messages))

    def test_variant_only_valid_for_markdown(self):
        rating = self._rate_with(description_content_type="text/plain; variant=GFM")
        self.assertTrue(any("only valid for text/markdown" in msg for msg in rating.messages))

    def test_content_type_with_parameters_accepted(self):
        rating = self._rate_with(description_content_type="text/markdown; charset=UTF-8; variant=GFM")
        self.assertEqual(astuple(rating), (10, []))

    def test_invalid_dependency_specifier(self):
        rating = self._rate_with(requires_dist=["zope.event", "broken =="])
        self.assertTrue(any("not a valid dependency specifier" in msg for msg in rating.messages))

    def test_unknown_marker_variable(self):
        rating = self._rate_with(requires_dist=['foo; unknown_variable == "1"'])
        self.assertTrue(any("not a valid dependency specifier" in msg for msg in rating.messages))

    def test_arbitrary_equality_discouraged(self):
        rating = self._rate_with(requires_dist=["foo===1.0"])
        self.assertTrue(any("arbitrary equality operator '==='" in msg for msg in rating.messages))

    def test_project_url_label_too_long(self):
        rating = self._rate_with(
            project_url=["ThisLabelIsMuchTooLongForTheThirtyTwoCharacterLimit, https://example.com"]
        )
        self.assertTrue(any("longer than the allowed 32 characters" in msg for msg in rating.messages))

    def test_project_url_no_well_known_label(self):
        rating = self._rate_with(project_url=["weird, https://example.com"])
        self.assertTrue(any("well-known label" in msg for msg in rating.messages))

    def test_project_url_dict_form(self):
        # The PyPI JSON API represents project urls as a dictionary.
        rating = self._rate_with(project_url={"Home-page": "https://example.com"})
        self.assertEqual(astuple(rating), (10, []))


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

        self.assertEqual(astuple(rating), (10, []))

    @unittest.mock.patch("pyroma.pypidata.requests.get")
    def test_get_project_data_custom_index_url(self, requestmock):
        requestmock.return_value = unittest.mock.Mock()
        requestmock.return_value.ok = True
        requestmock.return_value.status_code = 200
        requestmock.return_value.json.return_value = {}

        pypidata._get_project_data("internalpkg", index_url="https://packages.example.com")

        requestmock.assert_called_once_with(
            "https://packages.example.com/pypi/internalpkg/json", timeout=pypidata.REQUESTS_TIMEOUT
        )

    @unittest.mock.patch("pyroma.pypidata.requests.get")
    def test_get_project_data_custom_index_url_with_pypi_path(self, requestmock):
        requestmock.return_value = unittest.mock.Mock()
        requestmock.return_value.ok = True
        requestmock.return_value.status_code = 200
        requestmock.return_value.json.return_value = {}

        pypidata._get_project_data("internalpkg", index_url="https://packages.example.com/pypi")

        requestmock.assert_called_once_with(
            "https://packages.example.com/pypi/internalpkg/json", timeout=pypidata.REQUESTS_TIMEOUT
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
        ratemock.return_value = Rating(10, [])

        result = pyroma.run("pypi", "internalpkg", quiet=True, index_url="https://packages.example.com")

        self.assertEqual(result, 10)
        datamock.assert_called_once_with("internalpkg", index_url="https://packages.example.com")


class ReporterTest(unittest.TestCase):
    maxDiff = None

    def _rating(self):
        return Rating(
            9,
            [Problem(test="Description", message="The package's Description is quite short.", weight=50, fatal=False)],
        )

    def test_text_reporter(self):
        buffer = io.StringIO()
        reporter = TextReporter(stream=buffer)
        reporter.start("complete")
        reporter.found("complete")
        reporter.finish(self._rating())

        self.assertEqual(
            buffer.getvalue(),
            "------------------------------\n"
            "Checking complete\n"
            "Found complete\n"
            "------------------------------\n"
            "The package's Description is quite short.\n"
            "------------------------------\n"
            "Final rating: 9/10\n"
            "Cottage Cheese\n"
            "------------------------------\n",
        )

    def test_text_reporter_quiet(self):
        buffer = io.StringIO()
        reporter = TextReporter(stream=buffer, quiet=True)
        reporter.start("complete")
        reporter.found("complete")
        reporter.finish(self._rating())

        # Quiet mode outputs only the rating.
        self.assertEqual(buffer.getvalue(), "9\n")

    def test_json_reporter(self):
        buffer = io.StringIO()
        reporter = JsonReporter(stream=buffer)
        reporter.start("complete")
        reporter.found("complete")
        reporter.finish(self._rating())

        document = json.loads(buffer.getvalue())
        self.assertEqual(
            document,
            {
                "checked": "complete",
                "name": "complete",
                "rating": 9,
                "max_rating": 10,
                "level": "Cottage Cheese",
                "problems": [
                    {
                        "test": "Description",
                        "message": "The package's Description is quite short.",
                        "weight": 50,
                        "fatal": False,
                    }
                ],
            },
        )

    def test_run_json_output(self):
        # End-to-end: run() in JSON mode emits a single parseable document.
        import contextlib

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            rating = pyroma.run(
                "directory",
                str(TESTDATA_DIR / "minimal"),
                skip_tests=["CheckManifest"],
                output_format="json",
            )

        document = json.loads(buffer.getvalue())
        self.assertEqual(document["rating"], rating)
        self.assertEqual(document["name"], "minimal")
        self.assertTrue(document["problems"])
        for problem in document["problems"]:
            self.assertEqual(sorted(problem), ["fatal", "message", "test", "weight"])


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
