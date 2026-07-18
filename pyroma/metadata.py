"""Types describing the metadata dictionaries pyroma works with.

The metadata dictionaries are produced by projectdata (project
directories), distributiondata (distribution files) and pypidata (PyPI),
with keys normalized to the lower-case Core Metadata field names. Keys
starting with an underscore are pyroma-internal sentinels, not metadata
fields.
"""

import os
import re
from typing import Any, TypedDict, Union


def normalize(name: str) -> str:
    """Normalize a field or project name as the packaging specifications do."""
    return re.sub(r"[-_.]+", "-", name).lower()


# total=False: every key is optional; which ones are present depends on the
# package being rated and on the data source.
Metadata = TypedDict(
    "Metadata",
    {
        # Core Metadata fields, normalized to lower case. Multi-use fields
        # are lists; the PyPI JSON API also returns dicts for project-url.
        "metadata-version": str,
        "name": str,
        # Should be a str; VersionIsString reports anything else.
        "version": Any,
        "summary": str,
        "description": str,
        "description-content-type": str,
        "classifier": "list[str]",
        "keywords": Union[str, "list[str]"],
        "author": str,
        "author-email": str,
        "maintainer": str,
        "maintainer-email": str,
        "home-page": str,
        "download-url": str,
        "project-url": Union["list[str]", "dict[str, str]"],
        "requires-dist": Union[str, "list[str]", None],
        "requires-python": str,
        "requires": Union[str, "list[str]"],
        "provides": Union[str, "list[str]"],
        "obsoletes": Union[str, "list[str]"],
        "license": str,
        "license-expression": str,
        "license-file": Union[str, "list[str]"],
        "dynamic": Union[str, "list[str]"],
        "platform": Union[str, "list[str]"],
        # Pyroma-internal sentinels.
        "_path": Union[str, "os.PathLike[str]"],
        "_sdist": bool,
        "_owners": "list[str]",
        "_wheel_build_failed": bool,
        "_missing_pyproject_toml": bool,
        "_missing_build_system": bool,
        "_no_config_found": bool,
        "_has_sdist": bool,
    },
    total=False,
)
