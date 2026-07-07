import logging
import os
import re
import tempfile
import xmlrpc.client
from typing import Any, cast

import requests

from pyroma import distributiondata
from pyroma.metadata import Metadata

# Genuine diagnostics go to a named logger; program output is handled by
# pyroma.report.
logger = logging.getLogger(__name__)

# MAP from old PyPI `info` keys to Core Metadata keys
INFO_MAP = {
    "classifiers": "classifier",
    "project-urls": "project-url",
    "project-url": "home-page",
}

DEFAULT_PYPI_BASE_API_URL = "https://pypi.org/pypi"

# Seconds before a network request is aborted.
REQUEST_TIMEOUT = 30


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


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
    except requests.exceptions.Timeout as e:
        raise ValueError(f"Timed out after {REQUEST_TIMEOUT} seconds while fetching {url}.") from e
    except requests.exceptions.ConnectionError as e:
        raise ValueError(f"Could not connect to {url}: {e}") from e


def _get_project_data(project: str, index_url: "str | None" = None) -> "dict[str, Any]":
    # This uses the JSON REST API, not the (deprecated) XML-RPC API.
    base_api_url = _get_base_api_url(index_url)
    response = _http_get(f"{base_api_url}/{project}/json")
    if response.status_code == 404:
        if base_api_url == DEFAULT_PYPI_BASE_API_URL:
            raise ValueError(f"Did not find '{project}' on PyPI. Did you misspell it?")
        raise ValueError(f"Did not find '{project}' on package index {base_api_url}.")
    if not response.ok:
        raise ValueError(f"Unknown http error: {response.status_code} {response.reason}")

    return response.json()


def get_data(project: str, index_url: "str | None" = None) -> Metadata:
    # Pick the latest release.
    project_data = _get_project_data(project, index_url=index_url)
    # The `releases` key is deprecated on PyPI, but there is no JSON API
    # replacement for listing the files of the latest release, so we keep
    # using it while it lasts.
    releases = project_data["releases"]
    data: dict[str, Any] = {}

    for key, value in project_data["info"].items():
        key = normalize(key)
        if key in INFO_MAP:
            key = INFO_MAP[key]
        data[key] = value

    release = data["version"]
    logger.debug(f"Found {project} version {release}")

    try:
        # PyPI has deprecated the XML-RPC API, but package_roles (which the
        # BusFactor test needs) has no JSON API replacement yet.
        with xmlrpc.client.ServerProxy(_get_base_api_url(index_url)) as xmlrpc_client:
            roles = cast("list[tuple[str, str]]", xmlrpc_client.package_roles(project))
            data["_owners"] = [user for (role, user) in roles if role == "Owner"]
    except xmlrpc.client.ProtocolError:
        logger.warning(
            "Could not get package roles from XMLRPC API. Not all custom indexes "
            "support this, and some may have it disabled. Skipping role checks."
        )

    # Get download_urls:
    urls = releases[release]
    data["_pypi_downloads"] = bool(urls)

    # If there is a source download, download it, and get that data.
    # This is done mostly to do the imports check.
    data["_source_download"] = False
    data["_has_sdist"] = False

    for download in urls:
        if download["packagetype"] == "sdist":
            # Found a source distribution. Download and analyze it.
            data["_has_sdist"] = True
            filename = download["url"].split("/")[-1]
            logger.debug(f"Downloading {filename} to verify distribution")
            with tempfile.TemporaryDirectory(prefix="pyroma-") as tempdir:
                tmp = os.path.join(tempdir, filename)
                with open(tmp, "wb") as outfile:
                    outfile.write(_http_get(download["url"]).content)
                ddata = distributiondata.get_data(tmp)

            # Combine them, with the PyPI data winning:
            ddata_dict = cast("dict[str, Any]", ddata)
            ddata_dict.update(data)
            data = ddata_dict
            data["_source_download"] = True
            break

    return cast(Metadata, data)
