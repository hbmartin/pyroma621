"""Validate and test the exact distributions that will be published."""

import argparse
import json
import shutil
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from email.parser import BytesParser
from importlib import metadata
from pathlib import Path

_MAX_RATING = 10


def inspect(dist: Path, project: Path, release_tag: str = "") -> tuple[Path, Path, str]:
    """Require one wheel/sdist, matching versions, and essential wheel contents."""
    wheels, sdists = list(dist.glob("*.whl")), list(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        message = "Expected exactly one wheel and one source distribution"
        raise ValueError(message)
    version = tomllib.loads(project.read_text())["project"]["version"]
    if release_tag and release_tag.removeprefix("v") != version:
        message = f"Release tag {release_tag!r} does not match project version {version}"
        raise ValueError(message)
    with zipfile.ZipFile(wheels[0]) as archive:
        required = {"pyroma/__init__.py", "pyroma/__main__.py", "pyroma/tests.py", "pyroma/py.typed"}
        if not required <= set(archive.namelist()):
            message = f"Missing wheel files: {required - set(archive.namelist())}"
            raise ValueError(message)
        metadatas = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        if len(metadatas) != 1:
            message = "Expected one wheel metadata document"
            raise ValueError(message)
        wheel_metadata = BytesParser().parsebytes(archive.read(metadatas[0]))
        if wheel_metadata["Name"] != "pyroma621" or wheel_metadata["Version"] != version:
            message = "Wheel name/version does not match the project"
            raise ValueError(message)
    with tarfile.open(sdists[0]) as archive:
        metadata_files = [
            member
            for member in archive.getmembers()
            if member.name.count("/") == 1 and member.name.endswith("/PKG-INFO")
        ]
        if len(metadata_files) != 1:
            message = "Expected one source-distribution metadata document"
            raise ValueError(message)
        stream = archive.extractfile(metadata_files[0])
        if stream is None:
            message = "Source-distribution metadata is not a file"
            raise ValueError(message)
        source_metadata = BytesParser().parsebytes(stream.read())
        if source_metadata["Name"] != "pyroma621" or source_metadata["Version"] != version:
            message = "Source-distribution name/version does not match the project"
            raise ValueError(message)
    return wheels[0].resolve(), sdists[0].resolve(), version


def stage(sdist: Path, work: Path) -> Path:
    """Stage tests and configuration without copying production source."""
    unpacked = work / "unpacked"
    with tarfile.open(sdist) as archive:
        archive.extractall(unpacked, filter="data")
    roots = list(unpacked.iterdir())
    if len(roots) != 1 or not roots[0].is_dir():
        message = "Source distribution must contain a single project directory"
        raise ValueError(message)
    source = roots[0]
    staging = work / "staging"
    staging.mkdir()
    for directory in ("tests", "ci"):
        shutil.copytree(source / directory, staging / directory)
    shutil.copy(source / "pyproject.toml", staging / "pyproject.toml")
    shutil.copy(source / "uv.lock", staging / "uv.lock")
    return staging


def run(arguments: list[str], cwd: Path) -> str:
    """Run a bounded subprocess, retaining its diagnostics on failure."""
    result = subprocess.run(
        arguments, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600, check=False
    )
    print(result.stdout, end="")
    result.check_returncode()
    return result.stdout


def environment(work: Path, python: str, wheel: Path, project: Path, *, tests: bool) -> Path:
    """Install hashed locked dependencies before installing the wheel itself."""
    uv = shutil.which("uv")
    if uv is None:
        message = "uv must be installed"
        raise ValueError(message)
    name = "test-env" if tests else "runtime-env"
    root = work / name
    run([uv, "venv", "--python", python, str(root)], work)
    executable = root / "bin" / "python"
    requirements = work / f"{name}.requirements.txt"
    export = [
        uv,
        "export",
        "--quiet",
        "--locked",
        "--no-default-groups",
        "--no-emit-project",
        "--output-file",
        str(requirements),
    ]
    if tests:
        export.extend(["--group", "test"])
    run(export, project.parent)
    run([uv, "pip", "install", "--python", str(executable), "--require-hashes", "-r", str(requirements)], work)
    run([uv, "pip", "install", "--python", str(executable), "--no-deps", str(wheel)], work)
    return executable


def smoke(version: str) -> None:
    """Check installed origin, metadata, runtime files, and both CLI forms."""
    import pyroma  # noqa: PLC0415 - import only inside the runtime-only environment

    distribution = metadata.distribution("pyroma621")
    package = Path(pyroma.__file__).resolve().parent
    if (
        not package.is_relative_to(Path(sys.prefix).resolve())
        or Path(pyroma.__file__).resolve() != distribution.locate_file("pyroma/__init__.py").resolve()
    ):
        message = "Imported pyroma is not the installed release distribution"
        raise ValueError(message)
    if distribution.version != version or not (package / "py.typed").is_file():
        message = "Installed version or typing marker is incorrect"
        raise ValueError(message)
    licenses = [file for file in distribution.files or [] if str(file).endswith("/licenses/LICENSE.txt")]
    if len(licenses) != 1 or not distribution.locate_file(licenses[0]).is_file():
        message = "Installed license file is missing"
        raise ValueError(message)
    entries = {(entry.group, entry.name, entry.value) for entry in distribution.entry_points}
    if (
        not {
            ("console_scripts", "pyroma", "pyroma:main"),
            ("zest.releaser.prereleaser.before", "pyroma", "pyroma:zester"),
        }
        <= entries
    ):
        message = "Installed entry points are incorrect"
        raise ValueError(message)
    if distribution.metadata.get_all("Provides-Extra"):
        message = "Development extras leaked into published metadata"
        raise ValueError(message)
    for invocation in ([str(Path(sys.executable).with_name("pyroma"))], [sys.executable, "-m", "pyroma"]):
        run([*invocation, "--help"], Path.cwd())
        output = run(
            [*invocation, "--directory", "--format", "json", "--min", "10", str(package / "testdata" / "complete")],
            Path.cwd(),
        )
        document = json.loads(output)
        if document["rating"] != _MAX_RATING or document["_meta"]["pyroma"] != version:
            message = "Installed CLI did not return the expected JSON rating/version"
            raise ValueError(message)


def validate(dist: Path, project: Path, work: Path, python: str, release_tag: str) -> None:
    """Test packaged code and run smoke checks in a separate runtime environment."""
    wheel, sdist, version = inspect(dist, project, release_tag)
    work.mkdir(parents=True)
    staging = stage(sdist, work)
    uv = shutil.which("uv")
    if uv is None:
        message = "uv must be installed"
        raise ValueError(message)
    executable = environment(work, python, wheel, project, tests=True)
    installed = run([str(executable), "-c", "import pyroma; print(pyroma.__file__)"], staging).strip()
    package = Path(installed).parent
    if not package.resolve().is_relative_to((work / "test-env").resolve()):
        message = "Artifact suite would import production source outside its environment"
        raise ValueError(message)
    config = tomllib.loads((staging / "pyproject.toml").read_text())["tool"]["pytest"]["ini_options"]
    targets = [str(package / "tests.py") if target == "pyroma/tests.py" else target for target in config["testpaths"]]
    run(
        [
            str(executable),
            "-bb",
            "-X",
            "dev",
            "-m",
            "pytest",
            "--import-mode=importlib",
            "--cov",
            "--cov-report=term-missing",
            f"--cov-report=xml:{work / 'coverage.xml'}",
            *targets,
        ],
        staging,
    )
    runtime = environment(work, python, wheel, project, tests=False)
    run([str(runtime), str(staging / "ci" / "package_artifacts.py"), "smoke", "--version", version], staging)


def main() -> None:
    """Expose inspection and validation for CI and local release dry runs."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("inspect", "validate"):
        command = subparsers.add_parser(name)
        command.add_argument("--dist", type=Path, default=Path("dist"))
        command.add_argument("--project", type=Path, default=Path("pyproject.toml"))
        command.add_argument("--release-tag", default="")
        if name == "validate":
            command.add_argument("--work", type=Path, required=True)
            command.add_argument("--python", default=sys.executable)
    subparsers.add_parser("smoke").add_argument("--version", required=True)
    args = parser.parse_args()
    try:
        if args.command == "validate":
            validate(args.dist.resolve(), args.project.resolve(), args.work.resolve(), args.python, args.release_tag)
        elif args.command == "inspect":
            inspect(args.dist, args.project, args.release_tag)
        else:
            smoke(args.version)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Artifact validation failed: {error}\n")


if __name__ == "__main__":
    main()
