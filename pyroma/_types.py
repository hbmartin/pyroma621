"""Shared type definitions for pyroma.

The metadata dictionary that flows from the data-collection modules
(``projectdata``, ``distributiondata``, ``pypidata``) into ``ratings.rate()``
uses normalized Core Metadata field names as keys (lower-cased, with runs of
``-_.`` collapsed to ``-``), for example ``name``, ``version``, ``summary``,
``description``, ``description-content-type``, ``classifier``, ``keywords``,
``author``, ``author-email``, ``home-page``, ``project-url``, ``license``,
``license-expression``, ``requires-python``, ``requires-dist`` and
``metadata-version``.

In addition, pyroma uses underscore-prefixed sentinel keys that are not part
of any metadata specification:

``_path``
    Filesystem path of the project directory (directory mode only).
``_wheel_build_failed``
    The PEP 517 backend failed to produce wheel metadata.
``_missing_pyproject_toml``
    The project has no ``pyproject.toml`` file.
``_missing_build_system``
    The project has only a ``setup.cfg`` (no ``setup.py``/``pyproject.toml``).
``_no_config_found``
    No packaging configuration was found at all.
``_owners``
    List of PyPI owner usernames (PyPI mode only).
``_has_sdist``
    The PyPI release contains a source distribution (PyPI mode only).
``_source_download``
    An sdist was downloaded and analyzed (PyPI mode only).
``_pypi_downloads``
    The PyPI release has any downloadable files (PyPI mode only).

Values are heterogeneous (strings, lists of strings, booleans, paths), and
rating tests access fields dynamically, so this is expressed as a plain
``dict`` alias rather than a ``TypedDict``.
"""

from typing import Any

Metadata = dict[str, Any]
