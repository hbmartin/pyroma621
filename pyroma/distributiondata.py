"""Extract information from a distribution file.

Distributions are unpacked into a temporary directory and then inspected with
the project-data loader.
"""

import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from pyroma import projectdata
from pyroma.metadata import Metadata

if TYPE_CHECKING:
    import os


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
    root = Path(path).resolve()
    for member in tar.getmembers():
        member_path = (root / member.name).resolve()
        if member_path != root and root not in member_path.parents:
            message = f"Attempted path traversal in tar file {tar.name!r}"
            raise ValueError(message)
        if member.issym() or member.islnk() or member.isdev():
            message = f"Unsafe member in tar file {tar.name!r}: {member.name!r}"
            raise ValueError(message)
    # Every member path and link type is validated immediately above.
    tar.extractall(path)  # noqa: S202


def get_data(path: "str | os.PathLike[str]") -> Metadata:
    """Extract and return metadata from a source distribution archive."""
    archive_path = Path(path)
    filename = archive_path.name
    basename = Path(filename).stem
    ext = archive_path.suffix
    if basename.endswith(".tar"):
        basename = Path(basename).stem

    tar_extensions = {".bz2", ".tbz", ".tb2", ".gz", ".tgz", ".tar"}
    zip_extensions = {".zip", ".egg"}
    if ext not in tar_extensions | zip_extensions:
        message = f"Unknown file type: {ext}"
        raise ValueError(message)

    temporary_directory = tempfile.TemporaryDirectory(prefix="pyroma-", ignore_cleanup_errors=True)
    tempdir = temporary_directory.name

    try:
        if ext in tar_extensions:
            with tarfile.open(name=path, mode="r:*") as tar_file:
                try:
                    # The data filter rejects absolute paths, parent-directory
                    # traversal and links pointing outside the destination.
                    tar_file.extractall(tempdir, filter="data")
                except TypeError:
                    _safe_extract_tar(tar_file, tempdir)

        else:
            with zipfile.ZipFile(path, mode="r") as zip_file:
                # zipfile sanitizes absolute paths and parent-directory
                # components before writing archive members.
                zip_file.extractall(tempdir)  # noqa: S202

        projectpath = str(Path(tempdir) / basename)
        data = projectdata.get_build_data(projectpath)
        data["_path"] = projectpath
        data["_sdist"] = True
    except Exception:
        temporary_directory.cleanup()
        raise

    return cast("Metadata", _ExtractedMetadata(data, temporary_directory))
