"""Check standalone module fixtures without disturbing the active test module."""

import os
import re
import shutil
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
    try:
        result = subprocess.run(  # noqa: S603 - fixed Python fixture probe using the parent's package
            [sys.executable, "-bb", "-X", "dev", "-c", _PREAMBLE + source, *arguments],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        pytest.fail(_probe_diagnostics(arguments, f"timed out after {error.timeout}s", error.stdout, error.stderr))
    assert result.returncode == 0, _probe_diagnostics(
        arguments, f"exit status {result.returncode}", result.stdout, result.stderr
    )


def _probe_diagnostics(
    arguments: tuple[str, ...], status: str, stdout: str | bytes | None, stderr: str | bytes | None
) -> str:
    def output(value: str | bytes | None) -> str:
        return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""

    return (
        f"Fixture probe: {status}\nInterpreter: {sys.executable}\nPython: {sys.version}\n"
        f"Arguments: {arguments!r}\nstdout:\n{output(stdout)}\nstderr:\n{output(stderr)}"
    )


def test_fixture_probe_failure_preserves_context_and_output(tmp_path: Path) -> None:
    with pytest.raises(AssertionError) as failure:
        run_fixture_probe(tmp_path, "print('probe output'); print('probe error', file=sys.stderr); sys.exit(7)", "home")
    diagnostic = str(failure.value)
    for expected in ("exit status 7", sys.executable, sys.version, "('home',)", "probe output", "probe error"):
        assert expected in diagnostic


def test_fixture_probe_timeout_preserves_partial_output(tmp_path: Path) -> None:
    error = subprocess.TimeoutExpired("probe", 30, output=b"partial output", stderr=b"partial error")
    with patch.object(subprocess, "run", side_effect=error), pytest.raises(pytest.fail.Exception) as failure:
        run_fixture_probe(tmp_path, "pass", "xdg")
    diagnostic = str(failure.value)
    for expected in ("timed out after 30s", sys.executable, "('xdg',)", "partial output", "partial error"):
        assert expected in diagnostic


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


def _index_change_hook_unavailable(version: str, shell: str | None) -> str | None:
    if shell is None:
        return "No sh shell is available for the hostile-hook probe"
    match = re.match(r"git version (\d+)\.(\d+)(?:\D|$)", version)
    if match is None:
        return f"Cannot determine Git version from {version!r}"
    if (int(match[1]), int(match[2])) < (2, 22):
        return f"The hostile-hook probe requires Git 2.22 or newer, got {version.strip()}"
    return None


def _require_index_change_hook() -> None:
    shell = shutil.which("sh")
    version = ""
    if shell is not None:
        version = subprocess.run(
            ["git", "--version"],  # noqa: S607 - fixed Git version query
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    reason = _index_change_hook_unavailable(version, shell)
    if reason is not None:
        pytest.skip(reason)


@pytest.mark.parametrize(
    ("version", "shell", "supported"),
    [
        ("git version 2.21.0", "/bin/sh", False),
        ("git version 2.22.0", "/bin/sh", True),
        ("git version 2.31.1.windows.1", "C:/Git/bin/sh.exe", True),
        ("git version 2.50.1 (Apple Git-155)", "/bin/sh", True),
        ("git version 3.0.0", "/bin/sh", True),
        ("git version 2.50.1", None, False),
        ("unknown Git", "/bin/sh", False),
        ("unknown Git", None, False),
        ("git version 2.50.1", "relative/bin/sh", True),
        ("git version 2.50.1", "/shell tools/sh", True),
    ],
)
def test_index_change_hook_requirements(version: str, shell: str | None, *, supported: bool) -> None:
    assert (_index_change_hook_unavailable(version, shell) is None) is supported


@pytest.mark.parametrize(
    ("version", "shell", "reason"),
    [
        ("git version 2.21.0", "/bin/sh", "requires Git 2.22"),
        ("unknown Git", "/bin/sh", "Cannot determine"),
        ("", None, "No sh shell"),
    ],
)
def test_unsupported_hook_coverage_reports_skip(version: str, shell: str | None, reason: str) -> None:
    result = subprocess.CompletedProcess(["git", "--version"], 0, stdout=version)
    with (
        patch.object(shutil, "which", return_value=shell),
        patch.object(subprocess, "run", return_value=result) as query,
        pytest.raises(pytest.skip.Exception, match=reason),
    ):
        _require_index_change_hook()
    if shell is None:
        query.assert_not_called()


@pytest.mark.parametrize("attributes_location", ["home", "xdg"])
def test_fixture_git_commands_isolate_environment_index_and_exclusions(
    tmp_path: Path, attributes_location: str
) -> None:
    run = subprocess.run

    def without_version(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert "--version" not in arguments, "General isolation must not query Git versions"
        return run(arguments, **kwargs)

    with (
        patch.object(shutil, "which", side_effect=AssertionError("General isolation must not query hook support")),
        patch.object(subprocess, "run", side_effect=without_version),
    ):
        _run_git_isolation_probe(tmp_path, attributes_location, hook=False)


@pytest.mark.parametrize("attributes_location", ["home", "xdg"])
def test_fixture_git_commands_isolate_hostile_hooks(tmp_path: Path, attributes_location: str) -> None:
    _require_index_change_hook()
    _run_git_isolation_probe(tmp_path, attributes_location, hook=True)


def test_hostile_hook_positive_control_failure_is_not_skipped(tmp_path: Path) -> None:
    _require_index_change_hook()
    probe = run_fixture_probe

    def inert_hook(path: Path, source: str, *arguments: str) -> None:
        source = source.replace('printf poisoned > "$PYROMA_HOOK_SENTINEL"', ":")
        probe(path, source, *arguments)

    with (
        patch(f"{__name__}.run_fixture_probe", side_effect=inert_hook),
        pytest.raises(AssertionError, match="The poison hook must actually run without isolation"),
    ):
        _run_git_isolation_probe(tmp_path, "home", hook=True)


def _run_git_isolation_probe(tmp_path: Path, attributes_location: str, *, hook: bool) -> None:
    run_fixture_probe(
        tmp_path,
        """
import os

root = Path.cwd()
foreign = Path.cwd() / 'foreign'
foreign.mkdir()
clean = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
for name in ('clean-home', 'clean-config'):
    (root / name).mkdir()
clean.update(
    HOME=str(root / 'clean-home'), XDG_CONFIG_HOME=str(root / 'clean-config'),
    GIT_CONFIG_NOSYSTEM='1', GIT_ATTR_NOSYSTEM='1',
)
subprocess.run(['git', 'init', '--quiet', '--template=', str(foreign)], env=clean, check=True)
(foreign / 'sentinel').write_text('keep')
subprocess.run(['git', '-C', str(foreign), 'add', 'sentinel'], env=clean, check=True)
index = foreign / '.git' / 'index'
config_root = Path.cwd() / 'config'
(config_root / 'git').mkdir(parents=True)
home = root / 'home'
(home / '.config' / 'git').mkdir(parents=True)
attributes_root = home / '.config' if sys.argv[1] == 'home' else config_root
(attributes_root / 'git' / 'attributes').write_text('*.txt working-tree-encoding=UTF-16\\n')
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
(home / '.gitconfig').write_text(global_config.read_text())
hook_sentinel = root / 'hook-ran'
if sys.argv[2]:
    hook = template / 'post-index-change'
    hook.write_bytes(b'#!/bin/sh\\nprintf poisoned > "$PYROMA_HOOK_SENTINEL"\\n')
    hook.chmod(0o755)
    subprocess.run(
        ['git', '-C', str(foreign), '-c', 'core.hooksPath=' + str(template), 'add', 'sentinel'],
        env={**clean, 'PYROMA_HOOK_SENTINEL': str(hook_sentinel)}, check=True,
    )
    assert hook_sentinel.exists(), 'The poison hook must actually run without isolation'
    hook_sentinel.unlink()
before = index.read_bytes()
variables = {
    'GIT_DIR': str(foreign / '.git'),
    'GIT_WORK_TREE': str(foreign),
    'GIT_INDEX_FILE': str(index),
    'GIT_CONFIG_COUNT': '1',
    'GIT_CONFIG_KEY_0': 'core.excludesFile',
    'GIT_CONFIG_VALUE_0': str(excludes),
    'GIT_CONFIG_GLOBAL': str(global_config),
    'GIT_CONFIG_NOSYSTEM': '0',
    'GIT_ATTR_NOSYSTEM': '0',
    'HOME': str(home),
    'XDG_CONFIG_HOME': '' if sys.argv[1] == 'home' else str(config_root),
    'PYROMA_HOOK_SENTINEL': str(hook_sentinel),
}
run = module.subprocess.run

def check_git(arguments, **kwargs):
    environment = kwargs['env']
    assert environment['GIT_CONFIG_NOSYSTEM'] == '1'
    assert environment['GIT_ATTR_NOSYSTEM'] == '1'
    assert 'GIT_DIR' not in environment
    assert 'GIT_CONFIG_GLOBAL' not in environment
    assert environment['HOME'] != str(home)
    assert environment['XDG_CONFIG_HOME'] != variables['XDG_CONFIG_HOME']
    return run(arguments, **kwargs)

with patch.dict(os.environ, variables):
    parent = dict(os.environ)
    try:
        with patch.object(module.subprocess, 'run', side_effect=check_git):
            module.setUpModule()
        target = module.TESTDATA_DIR
        fixture_root = target.parent
        assert (target / '.git').is_dir()
        assert not (target / '.git' / 'poison-template').exists()
        assert not hook_sentinel.exists()
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
        stored_readme = subprocess.run(
            ['git', '-C', str(target), 'show', ':complete/README.txt'],
            env=clean, capture_output=True, check=True,
        ).stdout
        assert stored_readme == (original / 'complete' / 'README.txt').read_bytes()
    finally:
        module.tearDownModule()
    assert not fixture_root.exists()
assert index.read_bytes() == before
assert module.TESTDATA_DIR == original
assert module._FIXTURE_DIRECTORY is None
""",
        attributes_location,
        "enabled" if hook else "",
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
