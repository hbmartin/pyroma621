# Upgrading from commit 3062c977

This guide describes the changes made after commit
`3062c977fab17aeb5fbf1af7251d97578be536af` (the 5.1b3 development
baseline) through commit `706376b55fd4a4ede6e87f0ad3bea944979e4288`.
It is intended for:

- people running pyroma locally or in CI;
- projects whose rating may change under the stricter checks;
- applications that call pyroma's Python APIs;
- maintainers of custom rating tests; and
- contributors working on pyroma itself.

The release is a modernization of the rating engine, command-line output,
packaging-specification checks, data collection, typing, and development
toolchain. The historical `ratings.rate()` API and the default text report
remain available, but several environmental and behavioral changes require
attention.

## Upgrade at a glance

The most important changes are:

- Python 3.11 or later is required. Python 3.10 support has been removed.
- `pyroma --format json` provides a machine-readable, structured report.
  `--quiet` now writes only the integer rating and no longer changes the
  root logger.
- operational or configuration failures now produce a clean error and exit
  status 3 instead of leaking a traceback. A low rating continues to use
  exit status 2.
- ratings are stateless and have a structured API:
  `ratings.rate_project()` returns a `RatedProject` with individual
  `Problem` objects. `ratings.rate()` remains as a compatibility wrapper.
- custom tests must return `TestResult` objects instead of returning
  booleans and mutating shared test instances.
- package names, versions, metadata versions, SPDX license expressions,
  description content types, dependencies, project URL labels, and the
  PEP 621 `[project]` table receive new or stricter validation.
- some specification violations are fatal and now force a rating of 0.
- setup.cfg-only projects are no longer parsed. Without `setup.py` or
  `pyproject.toml`, no standard build frontend can extract their metadata;
  pyroma reports only the build-system problems and rates them 1.
- setuptools and distutils are no longer runtime dependencies. Project
  validation uses `tomllib` and `validate-pyproject`.
- distribution and PyPI modes retain an extracted sdist while rating so that
  `pyproject.toml` checks run in every mode. Direct callers must release
  that extracted tree with `distributiondata.cleanup(data)`.
- network requests have 30-second timeouts and clearer failures. Missing or
  unavailable legacy XML-RPC data no longer prevents the rest of a PyPI
  rating.
- tar extraction is hardened, `.tb2` archives are recognized, and signed
  or query-bearing sdist URLs are handled correctly.
- pyroma is now a typed package and ships `py.typed`.
- contributors use uv, Ruff, ty, and pyrefly. The test suite enforces at
  least 80 percent coverage.

## Compatibility and breaking changes

### Python requirement

The package metadata now declares `requires-python = ">=3.11"`. Python 3.10
was removed from tox and CI. Upgrade the interpreter used to install and run
pyroma before upgrading the package:

```console
$ python3.11 -m pip install --upgrade pyroma
```

If pyroma runs in pre-commit, tox, a release job, or a dedicated CI image,
update that environment too.

### Ratings can change

The score formula is unchanged: non-fatal pass and failure weights are
converted to a score from 1 through 10, while any fatal failure produces 0.
The set of tests and some weights have changed, however. A package that
previously scored above a CI threshold can now:

- lose points because a newly checked field is missing or invalid;
- receive 0 for a newly fatal specification violation; or
- gain a more accurate score because test state no longer leaks between
  repeated ratings.

Run the new version against every package before enforcing the result in CI.
Do not assume that the old numeric threshold alone proves compatibility.

### New fatal conditions

In addition to the existing fatal checks for missing name, version, or
summary, the following conditions now force a rating of 0:

- a project name that violates the packaging name format;
- specifying both `License` and `License-Expression`;
- an invalid SPDX `License-Expression`;
- a `Project-URL` label longer than 32 characters;
- a PEP 621 `[project]` table that makes `name` dynamic;
- a PEP 621 project with neither a static nor dynamic `version`;
- a `readme` or `license` table containing both `file` and `text`;
- defining `console_scripts` or `gui_scripts` under
  `[project.entry-points]` instead of their dedicated tables;
- failure to build wheel metadata; and
- a directory with no recognizable package configuration.

These failures represent rejected or unusable package metadata, not merely a
style preference.

### setup.cfg-only projects

The old fallback parsed metadata directly from `setup.cfg` with setuptools.
That path and `projectdata.get_setupcfg_data()` have been removed. A project
with `setup.cfg` but no `setup.py` or `pyproject.toml` now has no
buildable metadata, receives only the two build-system diagnostics, and rates
1.

Add a `pyproject.toml`. A minimal setuptools bridge for an existing
`setup.cfg` project is:

```toml
[build-system]
requires = ["setuptools>=77"]
build-backend = "setuptools.build_meta"
```

The metadata can remain in `setup.cfg` during a staged migration. Moving it
to the PEP 621 `[project]` table is recommended but is a separate step.
Any PEP 517 backend is supported; setuptools is only an example.

### CLI error status

Automation must distinguish a low rating from an inability to calculate one:

| Status | Meaning | Required handling |
| --- | --- | --- |
| 0 | Rating met `--min` | Continue. |
| 1 | Release aborted by the zest.releaser integration | Treat as an explicit release cancellation. |
| 2 | Rating was below `--min` | Treat as a package-quality failure. `argparse` also conventionally uses 2 for invalid command-line syntax. |
| 3 | Rating could not be completed | Treat as an operational, data, or configuration error and inspect the error message. |

Status 3 covers such cases as a package not found on an index, a failed
download, a missing distribution file, a network failure, or skipping every
test that contributes to the score.

### Direct data API cleanup

`distributiondata.get_data()` now keeps its extracted tree alive because
rating tests need the returned `_path`. `pypidata.get_data()` can return
the same resource-owning metadata when it downloads an sdist.

`pyroma.run()` always cleans up in a `finally` block. Direct callers must
do so explicitly:

```python
from pyroma import distributiondata, ratings

data = distributiondata.get_data("dist/example-1.0.tar.gz")
try:
    result = ratings.rate_project(data)
finally:
    distributiondata.cleanup(data)
```

`cleanup()` is safe to call for metadata that does not own an extracted
tree, so the same pattern can be used with data from any pyroma source.

## Recommended migration sequence

### 1. Upgrade the execution environment

Use Python 3.11 or later. For development, install the repository's required
uv version, currently 0.7.13. The exact requirement is recorded under
`[tool.uv]` in `pyproject.toml`.

### 2. Make the project buildable

Ensure every rated project has a `pyproject.toml` with a valid
`[build-system]` table. Keep `setup.py` temporarily if a legacy build
still needs it, but do not rely on a standalone `setup.cfg`.

Pyroma invokes the declared build backend to obtain wheel metadata. This can
execute code from the project, just as installing it can. Run pyroma only on
packages you trust or inside an appropriately isolated environment.

### 3. Bring metadata into specification

Review the detailed checks below. A representative modern PEP 621
configuration is:

```toml
[build-system]
requires = ["setuptools>=77"]
build-backend = "setuptools.build_meta"

[project]
name = "example-project"
version = "1.2.0"
description = "A useful description longer than ten characters"
readme = {file = "README.md", content-type = "text/markdown"}
requires-python = ">=3.11"
license = "MIT"
license-files = ["LICENSE*"]
dependencies = [
    "httpx>=0.27",
]
classifiers = [
    "Development Status :: 5 - Production/Stable",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.11",
]
keywords = ["example", "packaging"]
authors = [
    {name = "Example Maintainer", email = "maintainer@example.com"},
]

[project.urls]
Homepage = "https://example.com"
Source = "https://github.com/example/example-project"
Issues = "https://github.com/example/example-project/issues"

[project.scripts]
example = "example.cli:main"
```

For a dynamic version, omit `version` and include it in
`dynamic = ["version"]`. `name` must always be static.

### 4. Update command-line automation

Prefer JSON over scraping human-readable output:

```console
$ pyroma --format json --min 8 .
```

A successful JSON document has this shape:

```json
{
  "name": "example-project",
  "rating": 9,
  "level": "Cottage Cheese",
  "problems": [
    {
      "test": "Description",
      "message": "The package's Description is quite short.",
      "weight": 50,
      "fatal": false
    }
  ],
  "_meta": {
    "package": ".",
    "mode": "directory",
    "pyroma": "5.1b3.dev0"
  }
}
```

The `pyroma` metadata value is omitted when package-version metadata is not
available, such as some source-checkout executions. An error document has an
`error` key instead of `rating`:

```json
{
  "error": {
    "type": "ValueError",
    "message": "Did not find 'missing-project' on PyPI. Did you misspell it?"
  },
  "_meta": {
    "package": "missing-project",
    "mode": "pypi"
  }
}
```

Machine-readable output is written to stdout. Text-mode operational errors
are written to stderr. Index-role warnings use the `pyroma.pypidata` logger
and do not corrupt JSON stdout.

If only the score is needed, `pyroma --quiet .` now prints one integer.
Combining `--quiet` with `--format json` still produces the complete JSON
document; JSON formatting takes precedence.

### 5. Update Python integrations

Code that only needs the legacy tuple can continue unchanged:

```python
rating, messages = ratings.rate(metadata)
```

New code should use the structured result:

```python
result = ratings.rate_project(metadata)

print(result.name, result.rating, result.level)
for problem in result.problems:
    print(problem.test, problem.weight, problem.fatal, problem.message)
```

The result types are frozen dataclasses:

- `TestResult(outcome, weight, fatal, message)` describes one test;
- `Problem(test, message, weight, fatal)` describes one reported failure;
- `RatedProject(name, rating, level, problems)` describes the full rating.

`pyroma.run()` still prints a report and returns the integer rating. Its
signature gains `output_format="text"` after `index_url`.

### 6. Update custom rating tests

The old custom-test protocol was stateful:

```python
class MyCheck(BaseTest):
    weight = 20

    def test(self, data):
        self._message = "Explain the failure"
        return False

    def message(self):
        return self._message
```

The new protocol returns all per-run state:

```python
class MyCheck(BaseTest):
    weight = 20

    def test(self, data):
        if condition_is_met(data):
            return self._passed()
        return self._failed("Explain the failure")
```

Tests that do not apply should return `self._skipped()`. A test can override
weight or fatality for one result without mutating the shared instance:

```python
return self._failed("Rejected metadata", weight=100, fatal=True)
```

Do not store input-dependent state on a test object. Instances in
`ALL_TESTS` are shared across ratings.

### 7. Rebaseline and verify

Exercise all modes used in production:

```console
$ pyroma --format json .
$ python -m build
$ pyroma --format json dist/example-1.2.0.tar.gz
$ pyroma --format json example-project
```

The PyPI form performs additional owner and sdist checks. Use
`--index-url` for an internal PyPI-compatible index.

## Detailed rating changes

### Version validation

The hand-written PEP 386/440 regular expressions have been replaced by
`packaging.version.Version`:

- an unparseable version is a 100-weight failure;
- a valid but non-canonical spelling is a 20-weight warning and includes the
  canonical spelling;
- version epochs and local version segments are 20-weight warnings; and
- the separate `VersionIsString` check remains.

Use a canonical public PEP 440 version such as `1.2.0`, `1.2.0rc1`, or
`1.2.0.dev1`. Avoid publishing local versions such as `1.2.0+vendor.1`
and use epochs only to recover from an otherwise unfixable version sequence.

### Metadata-Version

The new `MetadataVersion` check accepts `1.0`, `1.1`, `1.2`, and
`2.1` through `2.5`. A valid value contributes 20 points; an invalid value
costs 100. A missing value is ignored because some direct API inputs do not
have one. Build backends normally generate this field.

### Project name

The new fatal `NameFormat` check enforces the packaging name syntax. Names
may contain ASCII letters, digits, dots, underscores, and hyphens, and must
start and end with a letter or digit.

### Licensing

License checks now use `packaging.licenses`:

- `License-Expression` must be a valid SPDX expression;
- combining `License` with `License-Expression` is fatal;
- combining a license expression with license classifiers remains a
  non-fatal deprecation failure;
- license classifiers alone remain a non-fatal failure; and
- a legacy `License` value can still be rated, but
  `DeprecatedMetadataFields` warns when its Core Metadata version makes it
  deprecated.

Prefer a PEP 639 expression such as `MIT`, `Apache-2.0`, or
`MIT OR Apache-2.0` and remove the legacy license field and license
classifiers.

### Description content type and ReST

The new `DescriptionContentType` check validates:

- media type: `text/plain`, `text/x-rst`, or `text/markdown`;
- charset: only UTF-8;
- Markdown variant: only `GFM` or `CommonMark`; and
- parameters: unknown parameters are reported.

The ReStructuredText check now compares only the media type portion, so
`text/markdown; charset=UTF-8` is not incorrectly parsed as ReST.
Parameterized `text/x-rst` is still checked. A Docutils `SystemMessage`
is reported even when Docutils did not also write to its warning stream.

### Dependency specifiers

Every `Requires-Dist` value is parsed with `packaging.requirements`.
Invalid requirements cost 100 points. Two accepted but discouraged forms cost
10 points:

- wrapping a version specifier in parentheses, for example
  `dependency (>=1)`; and
- ordered comparisons on non-version markers such as `sys_platform > "linux"`.

Use `dependency>=1` and equality or membership operations for string-valued
environment markers.

### PEP 621 project table

`validate-pyproject` validates the TOML document and registered schemas. A
second, fatal `PyProjectProjectTable` check covers mandatory PEP 621 rules
not guaranteed by those schemas:

- `name` cannot appear in `dynamic`;
- `version` must be static or listed in `dynamic`;
- `readme` cannot contain both `file` and `text`;
- `license` cannot contain both `file` and `text`; and
- console and GUI scripts must use `[project.scripts]` and
  `[project.gui-scripts]`, not
  `[project.entry-points.console_scripts]` or
  `[project.entry-points.gui_scripts]`.

A malformed value such as a non-list `dynamic` is left to the schema
validator and no longer crashes the second check.

### Project URLs

`Project-URL` now follows the well-known project URL specification:

- punctuation and whitespace are ignored and labels are case-insensitive for
  matching;
- aliases are recognized for Homepage, Source, Download, Changelog,
  Release Notes, Documentation, Issues, and Funding;
- a label longer than 32 characters is fatal; and
- packages with URLs but no recognized label receive a 10-weight
  recommendation.

Common accepted aliases include Repository, Source Code, GitHub, Changes,
History, Docs, Bugs, Tracker, Sponsor, and Donate. A legacy `Home-page`
still satisfies the homepage part of this check, although it may separately
receive a deprecation warning.

### Build-system ratings

`PyprojectTomlValid` now uses stdlib `tomllib` and
`validate-pyproject` rather than setuptools internals. The validator is
created lazily and reused.

Advice for a missing `pyproject.toml` is backend-neutral and mentions
setuptools, flit, hatchling, and uv_build. A new fixture verifies a src-layout
project using `uv_build>=0.8.0,<1.0`; the metadata path remains compatible
with any conforming PEP 517 backend.

When a setup.cfg-only project has no extractable metadata, pyroma now limits
the result to build-system problems instead of adding a misleading failure
for every absent metadata field.

### Other correctness fixes

- `BusFactor` now fails with weight 100 for zero or one owner, weight 50
  for two owners, and passes for three or more. Zero owners previously passed.
- a string passed directly as `skip_tests` is treated as one exact test
  name. It no longer performs accidental substring matches such as treating
  `"NameFormat"` as a request to skip `Name`.
- CLI `--skip-tests` accepts trailing or doubled separators after removing
  empty tokens. Spaces, commas, and semicolons remain accepted separators.
- skipping every test that contributes weight raises `ConfigurationError`
  instead of dividing by zero.
- user-facing messages received missing spaces, line breaks, and typo fixes.
- `check-manifest` runs with a quiet UI and is skipped for extracted sdists,
  where no VCS checkout exists to compare.

## Output and reporting changes

Program output no longer uses the root logging configuration. The new
`pyroma.report` module owns formatting:

- `format_text(rated)` produces the traditional human-readable report;
- `format_json(rated, meta=None)` produces a success document;
- `format_json_error(error, meta=None)` produces an error document; and
- `FORMATS` currently contains `"text"` and `"json"`.

This avoids two old integration hazards: importing pyroma no longer installs
a root logging configuration, and `--quiet` no longer disables the
application's root logger.

The text report remains the default. Its rating messages can change as checks
evolve, so consumers should use JSON fields rather than exact text matching.

## Data collection and reliability changes

### Project directories

Build-configuration detection now looks directly for `pyproject.toml`,
`setup.py`, and `setup.cfg`. It no longer depends on matching the English
text of an exception from the `build` library.

Metadata extraction remains backend-driven:

1.  try the project's PEP 517 backend without isolation for efficiency;
2.  retry in an isolated build environment if dependencies are missing; and
3.  normalize Core Metadata field names before rating.

Because the backend runs, metadata extraction is not a static or sandboxed
inspection.

### Distribution files

Distribution mode now:

- recognizes `.tb2` in addition to the existing tar, compressed tar, zip,
  and egg extensions;
- extracts tar files with Python's `data` filter;
- uses a fallback that rejects parent traversal, symbolic links, hard links,
  devices, and FIFOs when extraction filters are unavailable;
- raises `ValueError` for an unsafe member or unknown archive type;
- adds `_path` and `_sdist` to the returned metadata;
- keeps the extracted directory alive through rating; and
- releases it explicitly after high-level `run()` completes, including
  exceptional paths.

Keeping `_path` available means both generic TOML validation and the new
PEP 621 checks now run against distribution files.

### PyPI and custom indexes

HTTP JSON requests, sdist downloads, and the legacy XML-RPC owner request now
use a 30-second timeout. JSON timeouts and connection failures become clear
`ValueError` messages. XML-RPC faults, protocol errors, and network errors
only log a warning and omit `_owners` so the owner check is skipped.

The JSON API's synthesized keys are discarded:

- `project_url`;
- `package_url`;
- `release_url`;
- `docs_url`; and
- `bugtrack_url`.

These point to pages created by the index rather than URLs declared by the
project. Previously, normalized `project_url` could overwrite the real
`Home-page` value. The declared plural `project_urls` mapping is still
preserved as Core Metadata `project-url`.

The PyPI JSON `releases` key is deprecated and some compatible indexes omit
it. If it or the current release is unavailable, pyroma now omits
`_has_sdist` and skips the sdist test rather than crashing or claiming that
no sdist exists.

When an sdist is listed:

- a non-successful download raises a clear error;
- the temporary download file is always cleaned up;
- the filename is taken from the decoded URL path, excluding query strings;
- a URL with no path filename is rejected; and
- metadata from the index continues to take precedence over metadata
  extracted from the distribution.

Custom `--index-url` values still accept either the index root or a URL
already ending in `/pypi`.

## Python API and typing changes

All pyroma modules are annotated. The new `pyroma.metadata` module provides:

- `Metadata`, a non-total `TypedDict` for normalized Core Metadata and
  pyroma's underscore-prefixed sentinels; and
- `normalize()`, the shared packaging-style normalization helper.

The package ships `py.typed`, so downstream type checkers inspect these
annotations. Metadata from external JSON remains defensive at runtime; the
type describes expected values but does not replace validation.

Internal helper changes that may affect integrations using undocumented
symbols include:

- `projectdata.normalize` and `pypidata.normalize` moved to
  `metadata.normalize`;
- `projectdata.get_setupcfg_data` and its legacy metadata map were removed;
- `pypidata._get_xmlrpc_url` became `_get_base_api_url`;
- `DEFAULT_PYPI_XMLRPC_URL` became `DEFAULT_PYPI_BASE_API_URL`; and
- old internal `_pypi_downloads` and `_source_download` sentinels are no
  longer produced.

These were not documented public APIs; migrate to the supported entry points
where possible.

## Packaging and dependency changes

Pyroma's own package metadata moved from `setup.cfg` to PEP 621 and PEP 639
tables in `pyproject.toml`. `setup.py` and `setup.cfg` were removed.
Pyroma itself now uses `uv_build>=0.11.28,<0.12` with an explicit flat-layout
module configuration. Setuptools remains a test-only dependency because the
compatibility fixtures intentionally exercise Setuptools projects.

Runtime dependency changes:

- removed `setuptools>=61`;
- removed every `distutils` import;
- added `validate-pyproject>=0.16`;
- raised `packaging` to `>=24.2` for SPDX license support; and
- removed the Python 3.10-only `tomli` fallback in favor of stdlib
  `tomllib`.

Test/development dependency changes:

- added `check-manifest` explicitly to the test extra;
- added Hypothesis metadata fuzzing;
- added `pytest-cov`;
- raised test-time setuptools to `>=77`;
- added `uv_build>=0.8.0,<1.0`; and
- defined uv dependency groups for lint, type-check and quality tools.

The uv build configuration includes the complete `pyroma` module (including
`pyroma/py.typed` and compatibility fixtures) plus the documentation and
development files needed in the source distribution. Generated Hypothesis,
coverage, pytest, Ruff, mypy, uv lock, and virtual-environment artifacts are
ignored.

## Contributor and CI migration

### Toolchain

Black and Flake8 have been replaced by Ruff. Mypy was introduced during the
typing work and then replaced in the final toolchain by ty and pyrefly. The
current pinned tools are:

- uv 0.7.13;
- Ruff 0.15.22;
- ty 0.0.61; and
- pyrefly 1.1.1.

Run the same commands as CI:

```console
$ uv run --only-group lint ruff check --force-exclude .
$ uv run --only-group lint ruff format --force-exclude .
$ uv run --extra test --group typecheck ty check
$ uv run --extra test --group typecheck pyrefly check
$ uv run --extra test python -m pytest
```

Pre-commit uses local system hooks that invoke the same Ruff pin through uv.
It therefore requires `uv` to be available on `PATH`.

Ruff now enables import sorting, pyupgrade, Bugbear, built-in-shadowing,
private-return annotation, boolean positional-value, unittest-assertion, and
unused-unpacking rules in addition to its defaults. Tests have narrow ignores
for rules that conflict with established unittest patterns.

### Tests and coverage

The documented direct test command is now `python -m pytest` instead of
`python -m unittest pyroma.tests`. Tox runs pytest with:

- CPython 3.11 through 3.14 plus the generic current Python environment;
- development-mode and bytes warnings enabled;
- terminal missing-line coverage reporting; and
- `--cov-fail-under=80`.

CI exercises Linux, macOS, and Windows with CPython 3.11, CPython 3.14, and
PyPy 3.11 matrix entries.

The old executable XML-RPC response fixture was deleted. Network behavior now
uses ordinary mocks, and new fixtures cover invalid PEP 621 tables and an
uv_build src-layout project. The captured PyPI JSON fixture's ambiguous
`LGPL` license value was corrected to the valid SPDX expression
`LGPL-3.0-or-later`.

### Workflow hardening

GitHub Actions workflows now:

- declare read-only `contents` permission;
- disable persisted checkout credentials;
- pin checkout, setup-python, setup-uv, and pre-commit/action by full commit
  SHA; and
- run ty and pyrefly as a dedicated job through uv.

## Repository-level changes

New production files:

- `pyroma/metadata.py` for shared metadata typing and normalization;
- `pyroma/report.py` for text and JSON rendering; and
- `pyroma/py.typed` for PEP 561 type information.

New test fixtures cover:

- console scripts in the wrong PEP 621 table;
- a dynamic project name and missing version declaration;
- simultaneous `readme.file` and `readme.text`; and
- a complete uv_build project using a src layout.

Removed files:

- pyroma's root `setup.py` and `setup.cfg`;
- the executable `completedata.py` XML-RPC stub; and
- its large captured XML-RPC HTML response.

Documentation now explains JSON output, exit statuses, build-backend code
execution, backend-neutral build configuration, the current checks, the uv
toolchain, fixture regeneration, and two deliberately deferred improvements.
The README's pre-commit example was refreshed from revision 3.2 to 5.0b2, and
the changelog's `depracation` typo was corrected. This upgrade guide is
included in source distributions.

## Deferred work

Two ideas were considered but are not part of this upgrade:

- a static fast path for fully static PEP 621 metadata; and
- capturing build-backend subprocess warnings in the report.

The build-based metadata path therefore remains authoritative, and backend
stdout/stderr warnings may still be hidden by the build library's quiet
runner.

## Commit chronology

The implementation sequence after the baseline was:

- `3196a74` — make rating tests stateless and add structured results;
- `099b3eb` — add text/JSON reporters and correct quiet output;
- `1590c08` — add HTTP timeouts and temporary-download cleanup;
- `9e291e1` — move formatting/linting to Ruff and add coverage;
- `269c8ac` — remove an accidentally committed coverage database;
- `2975973` — remove setuptools/distutils at runtime and migrate pyroma's
  metadata to `pyproject.toml`;
- `ce99b05` — add specification-grounded rating checks;
- `b7ef1b5` — add tested uv_build support;
- `da40b9f` — annotate the package, add `Metadata`, and ship
  `py.typed`;
- `ebfc221` — expand changelog, reporter tests, and deferred-work notes;
- `d10aba6` — merge the modernization series; and
- `9da441b` — fix review findings and finalize the uv/Ruff/ty/pyrefly
  toolchain; and
- `706376b` — harden archive extraction, XML-RPC timeout handling, signed
  sdist URLs, explicit extracted-tree cleanup, CLI filesystem errors, typing
  edge cases, diagnostics, and CI action pinning.

## Final rollout checklist

Before adopting this version:

- [ ] Run pyroma with Python 3.11 or later.

- [ ] Add `pyproject.toml` to every setup.cfg-only project.

- [ ] Fix all fatal name, license, URL-label, and PEP 621 failures.

- [ ] Canonicalize versions and validate dependencies and description types.

- [ ] Replace deprecated license and URL metadata where practical.

- [ ] Rebaseline expected ratings and problem lists.

- [ ] Handle CLI exit status 3 separately from low-rating status 2.

- [ ] Use `--format json` instead of parsing the text report.

- [ ] Migrate custom tests to return `TestResult`.

- [ ] Add cleanup around direct distribution or PyPI data API calls.

- [ ] Replace Black/Flake8/mypy contributor commands with
  Ruff/ty/pyrefly through uv.

- [ ] Run pytest, both type checkers, Ruff, and the 80-percent coverage gate.

- [ ] Validate directory, built-sdist, and index modes used by the release
  pipeline.
