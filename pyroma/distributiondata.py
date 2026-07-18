"""
Extract information from a distribution file by unpacking it in a temporary
directory and then using projectdata on that.
"""

import os
import pathlib
import tarfile
import tempfile
import zipfile
from typing import Any, cast

from pyroma import projectdata
from pyroma.metadata import Metadata


class _ExtractedMetadata(dict[str, Any]):
    """Metadata that owns the temporary directory backing its ``_path``."""

    def __init__(self, data: Metadata, temporary_directory: "tempfile.TemporaryDirectory[str]") -> None:
        super().__init__(cast("dict[str, Any]", data))
        self._temporary_directory = temporary_directory

    def cleanup(self) -> None:
        self._temporary_directory.cleanup()


def cleanup(data: Metadata) -> None:
    """Release an extracted tree owned by distribution metadata, if present."""
    if isinstance(data, _ExtractedMetadata):
        data.cleanup()


def _safe_extract_tar(tar: tarfile.TarFile, path: str) -> None:
    """Safely extract a tar w/o traversing parent dirs to fix CVE-2007-4559.

    Fallback for Python versions without extraction filters (< 3.11.4).
    """
    root = pathlib.Path(path).resolve()
    for member in tar.getmembers():
        member_path = (root / member.name).resolve()
        if member_path != root and root not in member_path.parents:
            raise ValueError(f"Attempted path traversal in tar file {tar.name!r}")
        if member.issym() or member.islnk() or member.isdev():
            raise ValueError(f"Unsafe member in tar file {tar.name!r}: {member.name!r}")
    tar.extractall(path)


def get_data(path: "str | os.PathLike[str]") -> Metadata:
    filename = os.path.split(path)[-1]
    basename, ext = os.path.splitext(filename)
    if basename.endswith(".tar"):
        basename, _ignored = os.path.splitext(basename)

    temporary_directory = tempfile.TemporaryDirectory(prefix="pyroma-", ignore_cleanup_errors=True)
    tempdir = temporary_directory.name

    try:
        if ext in (".bz2", ".tbz", ".tb2", ".gz", ".tgz", ".tar"):
            with tarfile.open(name=path, mode="r:*") as tar_file:
                try:
                    # The data filter rejects absolute paths, parent-directory
                    # traversal and links pointing outside the destination.
                    tar_file.extractall(tempdir, filter="data")
                except TypeError:
                    _safe_extract_tar(tar_file, tempdir)

        elif ext in (".zip", ".egg"):
            with zipfile.ZipFile(path, mode="r") as zip_file:
                zip_file.extractall(tempdir)

        else:
            raise ValueError("Unknown file type: " + ext)

        projectpath = os.path.join(tempdir, basename)
        data = projectdata.get_build_data(projectpath)
        data["_path"] = projectpath
        data["_sdist"] = True
    except Exception:
        temporary_directory.cleanup()
        raise

    return cast(Metadata, _ExtractedMetadata(data, temporary_directory))
