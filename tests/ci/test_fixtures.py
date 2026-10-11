"""Check standalone module fixtures without disturbing the active test module."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

import pyroma

_PREAMBLE = """
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch
import pyroma.tests as module

original = module.TESTDATA_DIR
assert original.is_dir()
assert module._FIXTURE_DIRECTORY is None
"""


def run_fixture_probe(tmp_path: Path, source: str, *arguments: str) -> None:
    root = str(Path(pyroma.__file__).resolve().parent.parent)
    inherited = os.environ.get("PYTHONPATH")
    environment = {**os.environ, "PYTHONPATH": root + (os.pathsep + inherited if inherited else "")}
    result = subprocess.run(  # noqa: S603 - fixed Python fixture probe using the parent's package
        [sys.executable, "-bb", "-X", "dev", "-c", _PREAMBLE + source, *arguments],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_fixture_module_supports_repeated_setup_and_teardown(tmp_path: Path) -> None:
    run_fixture_probe(
        tmp_path,
        """
source_readme = (original / 'complete' / 'README.txt').read_bytes()
directories = []
try:
    for _ in range(2):
        module.setUpModule()
        fixture = module._FIXTURE_DIRECTORY
        assert fixture is not None
        target = module.TESTDATA_DIR
        directory = Path(fixture.name)
        directories.append(directory)
        assert target.parent == directory
        assert target != original
        assert (target / 'complete' / 'README.txt').read_bytes() == source_readme
        subprocess.run(
            ['git', '-C', str(target), 'ls-files', '--error-unmatch', 'complete/README.txt'],
            check=True, capture_output=True, text=True,
        )
        module.tearDownModule()
        assert module.TESTDATA_DIR == original
        assert module._FIXTURE_DIRECTORY is None
        assert not directory.exists()
        module.tearDownModule()
        assert module.TESTDATA_DIR == original
        assert module._FIXTURE_DIRECTORY is None
    assert len(set(directories)) == 2
finally:
    module.tearDownModule()
""",
    )


@pytest.mark.parametrize("operation", ["copy", "git-init", "git-add"])
def test_fixture_setup_failure_cleans_up_without_publishing(tmp_path: Path, operation: str) -> None:
    run_fixture_probe(
        tmp_path,
        """
operation = sys.argv[1]
created = []
make_directory = module.tempfile.TemporaryDirectory
copy_tree = module.shutil.copytree
run = module.subprocess.run

def record_directory(*args, **kwargs):
    directory = make_directory(*args, **kwargs)
    created.append(directory)
    return directory

def check_copy(*args, **kwargs):
    assert module.TESTDATA_DIR == original
    assert module._FIXTURE_DIRECTORY is None
    if operation == 'copy':
        raise OSError('copy failed')
    return copy_tree(*args, **kwargs)

def check_git(arguments, **kwargs):
    assert module.TESTDATA_DIR == original
    assert module._FIXTURE_DIRECTORY is None
    if (operation == 'git-init' and arguments[1] == 'init') or (
        operation == 'git-add' and arguments[-2:] == ['add', '.']
    ):
        raise subprocess.CalledProcessError(1, arguments)
    return run(arguments, **kwargs)

with (
    patch.object(module.tempfile, 'TemporaryDirectory', side_effect=record_directory),
    patch.object(module.shutil, 'copytree', side_effect=check_copy),
    patch.object(module.subprocess, 'run', side_effect=check_git),
):
    try:
        module.setUpModule()
    except (OSError, subprocess.CalledProcessError) as error:
        assert isinstance(error, OSError if operation == 'copy' else subprocess.CalledProcessError)
    else:
        raise AssertionError('setup should fail')

assert len(created) == 1
assert not Path(created[0].name).exists()
assert module.TESTDATA_DIR == original
assert module._FIXTURE_DIRECTORY is None
try:
    module.setUpModule()
finally:
    module.tearDownModule()
assert module.TESTDATA_DIR == original
assert module._FIXTURE_DIRECTORY is None
""",
        operation,
    )


def test_fixture_teardown_restores_globals_when_cleanup_fails(tmp_path: Path) -> None:
    run_fixture_probe(
        tmp_path,
        """
module.setUpModule()
fixture = module._FIXTURE_DIRECTORY
directory = Path(fixture.name)
try:
    with patch.object(fixture, 'cleanup', side_effect=OSError('cleanup failed')):
        try:
            module.tearDownModule()
        except OSError as error:
            assert str(error) == 'cleanup failed'
        else:
            raise AssertionError('cleanup error should propagate')
    assert module.TESTDATA_DIR == original
    assert module._FIXTURE_DIRECTORY is None
    module.tearDownModule()
    assert module.TESTDATA_DIR == original
    assert module._FIXTURE_DIRECTORY is None
finally:
    fixture.cleanup()
assert not directory.exists()
""",
    )
