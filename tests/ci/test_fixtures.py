"""Check standalone module fixtures without disturbing the active test module."""

import os
import subprocess
import sys
import warnings
from pathlib import Path
from unittest.mock import patch

import pytest
from pyproject_hooks import BuildBackendWarning

import pyroma
import pyroma.tests as module

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
    if (operation == 'git-init' and 'init' in arguments) or (
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


def test_fixture_git_commands_isolate_environment_index_and_exclusions(tmp_path: Path) -> None:
    run_fixture_probe(
        tmp_path,
        """
import os

foreign = Path.cwd() / 'foreign'
foreign.mkdir()
clean = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
clean.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')
subprocess.run(['git', 'init', '--quiet', str(foreign)], env=clean, check=True)
(foreign / 'sentinel').write_text('keep')
subprocess.run(['git', '-C', str(foreign), 'add', 'sentinel'], env=clean, check=True)
index = foreign / '.git' / 'index'
before = index.read_bytes()
config_root = Path.cwd() / 'config'
(config_root / 'git').mkdir(parents=True)
excludes = Path.cwd() / 'excludes'
excludes.write_text('*.tar*\\n*.zip\\nREADME.txt\\n')
(config_root / 'git' / 'config').write_text('[core]\\nexcludesFile = ' + str(excludes).replace('\\\\', '/') + '\\n')
template = Path.cwd() / 'template'
(template / 'info').mkdir(parents=True)
(template / 'info' / 'exclude').write_text('*\\n')
(template / 'poison-template').touch()
global_config = config_root / 'git' / 'config'
global_config.write_text(
    global_config.read_text()
    + 'hooksPath = ' + str(template).replace('\\\\', '/')
    + '\\n[init]\\ntemplateDir = ' + str(template).replace('\\\\', '/') + '\\n'
)
variables = {
    'GIT_DIR': str(foreign / '.git'),
    'GIT_WORK_TREE': str(foreign),
    'GIT_INDEX_FILE': str(index),
    'GIT_CONFIG_COUNT': '1',
    'GIT_CONFIG_KEY_0': 'core.excludesFile',
    'GIT_CONFIG_VALUE_0': str(excludes),
    'GIT_CONFIG_GLOBAL': str(global_config),
    'GIT_CONFIG_NOSYSTEM': '0',
    'XDG_CONFIG_HOME': str(config_root),
}
with patch.dict(os.environ, variables):
    parent = dict(os.environ)
    try:
        module.setUpModule()
        target = module.TESTDATA_DIR
        assert (target / '.git').is_dir()
        assert not (target / '.git' / 'poison-template').exists()
        assert dict(os.environ) == parent
        assert index.read_bytes() == before
        result = subprocess.run(
            ['git', '-C', str(target), 'ls-files', '-z'], env=clean,
            capture_output=True, text=True, check=True,
        )
        tracked = set(result.stdout.split('\\0'))
        assert 'complete/README.txt' in tracked
        archives = {
            str(path.relative_to(original)).replace('\\\\', '/')
            for path in (original / 'distributions').iterdir()
        }
        assert archives <= tracked
    finally:
        module.tearDownModule()
assert index.read_bytes() == before
assert module.TESTDATA_DIR == original
assert module._FIXTURE_DIRECTORY is None
""",
    )


@pytest.mark.parametrize("dirname", ["custom_test", "pep517", "private_classifier", "setup_config"])
@pytest.mark.parametrize("warning", ["changed", "absent", "unrelated"])
def test_fixture_backend_warning_policy(dirname: str, warning: str) -> None:
    def data(*_args: object, **_kwargs: object) -> dict[str, object]:
        if warning != "absent":
            category = BuildBackendWarning if warning == "changed" else UserWarning
            warnings.warn("New backend warning wording", category, stacklevel=1)
        return {}

    with (
        warnings.catch_warnings(),
        patch.object(module.projectdata, "get_data", side_effect=data),
        patch.object(module, "rate", return_value=(10, [])),
    ):
        warnings.simplefilter("error")
        if warning == "unrelated":
            with pytest.raises(UserWarning, match="New backend warning wording"):
                module.RatingsTest()._get_file_rating(dirname)
        else:
            assert module.RatingsTest()._get_file_rating(dirname) == (10, [])
