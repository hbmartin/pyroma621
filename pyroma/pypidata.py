import logging
import os
import re
import tempfile
import xmlrpc.client
from typing import cast

import requests

from pyroma import distributiondata
from pyroma._types import Metadata

logger = logging.getLogger("pyroma.pypidata")

# MAP from old PyPI `info` keys to Core Metadata keys
INFO_MAP = {
    "classifiers": "classifier",
    "project-urls": "project-url",
    "project-url": "home-page",
}

# The base URL used both for the JSON API (<base>/<project>/json) and for the
# legacy XML-RPC API (used only for the package_roles call).
DEFAULT_PYPI_API_URL = "https://pypi.org/pypi"

# Timeout (in seconds) for all network requests. Generous, because downloading
# an sdist of a large package from a slow mirror can legitimately take a while,
# but it makes sure pyroma can't hang forever in CI.
REQUESTS_TIMEOUT = 30


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _get_api_url(index_url: str | None = None) -> str:
    """Return the base API URL for the package index.

    Note that despite PyPI serving both from the same /pypi base, this URL is
    used to build JSON API request paths as well as being the XML-RPC
    endpoint. PyPI has deprecated the XML-RPC API, and the JSON API's
    "releases" key; both may eventually need replacing with the Index API.
    """
    if not index_url:
        return DEFAULT_PYPI_API_URL

    base_url = index_url.rstrip("/")
    if base_url.endswith("/pypi"):
        return base_url
    return f"{base_url}/pypi"


def _get_project_data(project: str, index_url: str | None = None) -> dict:
    # I think I should be able to monkeypatch a mock-thingy here... I think.
    api_url = _get_api_url(index_url)
    try:
        response = requests.get(f"{api_url}/{project}/json", timeout=REQUESTS_TIMEOUT)
    except requests.exceptions.Timeout:
        raise ValueError(f"Timed out talking to package index {api_url} after {REQUESTS_TIMEOUT} seconds.")
    except requests.exceptions.ConnectionError as e:
        raise ValueError(f"Could not connect to package index {api_url}: {e}")
    if response.status_code == 404:
        if api_url == DEFAULT_PYPI_API_URL:
            raise ValueError(f"Did not find '{project}' on PyPI. Did you misspell it?")
        raise ValueError(f"Did not find '{project}' on package index {api_url}.")
    if not response.ok:
        raise ValueError(f"Unknown http error: {response.status_code} {response.reason}")

    return response.json()


def _get_owners(project: str, index_url: str | None = None) -> list[str] | None:
    """Get the PyPI package owners over the (deprecated) XML-RPC API.

    Returns None if the index doesn't support the XML-RPC roles API, which
    makes the BusFactor check skip instead of failing.
    """
    try:
        with xmlrpc.client.ServerProxy(_get_api_url(index_url)) as xmlrpc_client:
            roles = cast("list[tuple[str, str]]", xmlrpc_client.package_roles(project))
            return [user for (role, user) in roles if role == "Owner"]
    except xmlrpc.client.ProtocolError:
        logger.warning(
            "Could not get package roles from XMLRPC API. Not all custom indexes "
            "support this, and some may have it disabled. Skipping role checks."
        )
        return None


def get_data(project: str, index_url: str | None = None) -> Metadata:
    # Pick the latest release.
    project_data = _get_project_data(project, index_url=index_url)
    releases = project_data["releases"]
    data: Metadata = {}

    for key, value in project_data["info"].items():
        key = normalize(key)
        if key in INFO_MAP:
            key = INFO_MAP[key]
        data[key] = value

    release = data["version"]
    logger.debug(f"Found {project} version {release}")

    owners = _get_owners(project, index_url=index_url)
    if owners is not None:
        data["_owners"] = owners

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
                try:
                    response = requests.get(download["url"], timeout=REQUESTS_TIMEOUT)
                except requests.exceptions.Timeout:
                    raise ValueError(f"Timed out downloading {filename} after {REQUESTS_TIMEOUT} seconds.")
                except requests.exceptions.ConnectionError as e:
                    raise ValueError(f"Could not download {filename}: {e}")
                with open(tmp, "wb") as outfile:
                    outfile.write(response.content)
                ddata = distributiondata.get_data(tmp)

            # Combine them, with the PyPI data winning:
            ddata.update(data)
            data = ddata
            data["_source_download"] = True
            break

    return data
