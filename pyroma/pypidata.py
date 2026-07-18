"""Retrieve and normalize project metadata from a Python package index."""

import logging
import tempfile
import xmlrpc.client
from http import HTTPStatus
from http.client import HTTPConnection, HTTPSConnection
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote, urlsplit

import requests

from pyroma import distributiondata
from pyroma.metadata import Metadata, normalize

# Genuine diagnostics go to a named logger; program output is handled by
# pyroma.report.
logger = logging.getLogger(__name__)

# MAP from old PyPI `info` keys to Core Metadata keys
INFO_MAP = {
    "classifiers": "classifier",
    "project-urls": "project-url",
}

# Keys (normalized) that the PyPI JSON API synthesizes itself — they always
# point at the index, not at anything the project declared, so folding them
# into the metadata would fabricate fields the package does not have.
SYNTHESIZED_INFO_KEYS = {
    "project-url",
    "package-url",
    "release-url",
    "docs-url",
    "bugtrack-url",
}

DEFAULT_PYPI_BASE_API_URL = "https://pypi.org/pypi"

# Seconds before a network request is aborted.
REQUEST_TIMEOUT = 30


class _TimeoutTransport(xmlrpc.client.Transport):
    def __init__(self, timeout: int) -> None:
        super().__init__()
        self.timeout = timeout

    def make_connection(self, host: "str | tuple[str, dict[str, str]]") -> HTTPConnection:
        connection = super().make_connection(host)
        connection.timeout = self.timeout
        return connection


class _TimeoutSafeTransport(xmlrpc.client.SafeTransport):
    def __init__(self, timeout: int) -> None:
        super().__init__()
        self.timeout = timeout

    def make_connection(self, host: "str | tuple[str, dict[str, str]]") -> HTTPSConnection:
        connection = super().make_connection(host)
        connection.timeout = self.timeout
        return connection


def _xmlrpc_transport(url: str) -> xmlrpc.client.Transport:
    if urlsplit(url).scheme.lower() == "https":
        return _TimeoutSafeTransport(REQUEST_TIMEOUT)
    return _TimeoutTransport(REQUEST_TIMEOUT)


def _download_filename(url: str) -> str:
    filename = Path(unquote(urlsplit(url).path)).name
    if not filename:
        message = "Source distribution URL has no filename in its path."
        raise ValueError(message)
    return filename


def _get_base_api_url(index_url: "str | None" = None) -> str:
    # PyPI serves both the JSON REST API (<base>/<project>/json) and the
    # legacy XML-RPC API on the same /pypi base path.
    if not index_url:
        return DEFAULT_PYPI_BASE_API_URL

    base_url = index_url.rstrip("/")
    if base_url.endswith("/pypi"):
        return base_url
    return f"{base_url}/pypi"


def _http_get(url: str) -> requests.Response:
    try:
        return requests.get(url, timeout=REQUEST_TIMEOUT)
    except requests.exceptions.Timeout:
        message = f"Timed out after {REQUEST_TIMEOUT} seconds while fetching package data."
        raise ValueError(message) from None
    except requests.exceptions.ConnectionError:
        message = "Could not connect to the package index."
        raise ValueError(message) from None


def _get_project_data(project: str, index_url: "str | None" = None) -> "dict[str, Any]":
    # This uses the JSON REST API, not the (deprecated) XML-RPC API.
    base_api_url = _get_base_api_url(index_url)
    response = _http_get(f"{base_api_url}/{project}/json")
    if response.status_code == HTTPStatus.NOT_FOUND:
        if base_api_url == DEFAULT_PYPI_BASE_API_URL:
            message = f"Did not find '{project}' on PyPI. Did you misspell it?"
            raise ValueError(message)
        message = f"Did not find '{project}' on the configured package index."
        raise ValueError(message)
    if not response.ok:
        message = f"Unknown http error: {response.status_code} {response.reason}"
        raise ValueError(message)

    return response.json()


def get_data(project: str, index_url: "str | None" = None) -> Metadata:
    """Return normalized metadata for the latest release of a project."""
    # Pick the latest release.
    project_data = _get_project_data(project, index_url=index_url)
    data: dict[str, Any] = {}

    if not isinstance(project_data, dict):
        message = f"Invalid metadata format received from package index for '{project}'."
        # Invalid external JSON is a value error, not a caller type error.
        raise ValueError(message)  # noqa: TRY004

    info = project_data.get("info")
    if not isinstance(info, dict):
        message = f"Invalid metadata format received from package index for '{project}'."
        # Invalid external JSON is a value error, not a caller type error.
        raise ValueError(message)  # noqa: TRY004

    for raw_key, value in info.items():
        key = normalize(raw_key)
        if key in SYNTHESIZED_INFO_KEYS:
            continue
        metadata_key = INFO_MAP.get(key, key)
        data[metadata_key] = value

    release = data["version"]
    logger.debug("Found %s version %s", project, release)

    try:
        # PyPI has deprecated the XML-RPC API, but package_roles (which the
        # BusFactor test needs) has no JSON API replacement yet.
        xmlrpc_url = _get_base_api_url(index_url)
        transport = _xmlrpc_transport(xmlrpc_url)
        with xmlrpc.client.ServerProxy(xmlrpc_url, transport=transport) as xmlrpc_client:
            roles = cast("list[tuple[str, str]]", xmlrpc_client.package_roles(project))
            data["_owners"] = [user for (role, user) in roles if role == "Owner"]
    except (xmlrpc.client.Error, OSError):
        logger.warning(
            "Could not get package roles from XMLRPC API. Not all custom indexes "
            "support this, and some may have it disabled. Skipping role checks."
        )

    # The `releases` key is deprecated on PyPI, but there is no JSON API
    # replacement for listing the files of the latest release, so we keep
    # using it while it lasts. Custom indexes may not provide it at all;
    # without it we cannot know whether an sdist was uploaded.
    urls = project_data.get("releases", {}).get(release)

    if urls is not None:
        # If there is a source download, download it, and get that data.
        data["_has_sdist"] = False

        for download in urls:
            if download["packagetype"] == "sdist":
                # Found a source distribution. Download and analyze it.
                data["_has_sdist"] = True
                filename = _download_filename(download["url"])
                logger.debug("Downloading %s to verify distribution", filename)
                response = _http_get(download["url"])
                if not response.ok:
                    message = f"Could not download source distribution: {response.status_code} {response.reason}"
                    raise ValueError(message)
                with tempfile.TemporaryDirectory(prefix="pyroma-") as tempdir:
                    tmp = Path(tempdir) / filename
                    with tmp.open("wb") as outfile:
                        outfile.write(response.content)
                    ddata = distributiondata.get_data(tmp)

                # Combine them, with the PyPI data winning:
                ddata_dict = cast("dict[str, Any]", ddata)
                ddata_dict.update(data)
                data = ddata_dict
                break

    return cast("Metadata", data)
