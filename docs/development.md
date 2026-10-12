# Testing

Run the test suite with pytest:

The default `dev` dependency group includes `test`, so plain `uv run pytest`
works from a fresh checkout. CI uses the explicit test group shown below.

```console
$ uv sync --locked --no-default-groups --group test
$ uv run --locked --no-default-groups --group test python -bb -X dev -m pytest --cov --cov-report=term-missing --cov-report=xml
```

The test dependency group includes Hypothesis. Its generated metadata cases run as part
of the normal pytest and tox suites, including the Linux CI matrix.

The separate `tests/property` suite checks metadata boundaries, marker
semantics, backend constraints, Markdown destinations, rating options, and
metadata ingestion. It uses bounded generators and mocked external providers,
so generated cases do not fetch packages or build wheels. Calendar-dependent
ratings use a fixed date, October 11, 2026.

Run the property suite with statistics (100 examples per property by default),
or select the larger profile for 1,000 examples:

```console
$ python -m pytest tests/property --hypothesis-show-statistics
$ python -m pytest tests/property --hypothesis-profile=property-deep --hypothesis-show-statistics
```

The repository-root conftest registers and loads the `property` profile before
pytest resolves command-line options. It selects 100 examples and disables
timing deadlines, while preserving other settings from Hypothesis's initially
active profile, including its CI defaults. Explicit `--hypothesis-profile`
selection overrides this baseline. Both profiles work with `pytest .`, an
individual file, or any test directory; installed-wheel validation stages the
same configuration. The standalone property test in `pyroma/tests.py` also
disables deadlines when run outside the repository.

Use pytest's `--durations=10` to inspect runtime without making machine speed
a correctness requirement. Hypothesis keeps
minimized failures in its ignored `.hypothesis` database. Turn demonstrated
bugs into explicit regression examples and narrow fixes.

Header generators draw ordered, unique field names so a fixed Hypothesis seed
does not depend on Python's string hash seed. Coherent metadata has non-whitespace
text boundaries; whitespace-only inputs have separate cases documenting current
scoring. Rule assertions freeze both direct and aggregate evaluations and handle
scored and advisory findings, including strict mode.

The backend enumeration oracle is intentionally restricted to integer bounds
and minor-level Python conditions. Prereleases, wildcard pins, direct URLs,
and unknown environments have separate examples; finite enumeration does not
prove arbitrary version constraints.

or across all supported Python versions:

```console
$ tox
```

## Linting and type checking

Ruff, ty and pyrefly run through uv, which reads their constraints from the
`lint` and `typecheck` dependency groups in `pyproject.toml`. Ruff is pinned
exactly because the project enables every stable rule; CI and pre-commit use
the same group:

```console
$ uv run --locked --only-group lint ruff check --force-exclude .
$ uv run --locked --only-group lint ruff format --force-exclude .
$ uv run --locked --group test --group typecheck ty check
$ uv run --locked --group test --group typecheck pyrefly check
```

Or simply run all the pre-commit hooks:

```console
$ pre-commit run --all-files
```

Ruff is pinned and the package passes `select = ["ALL"]`. The global ignores
only resolve formatter conflicts; per-file ignores document the deliberate
unittest style, private test access, and CLI output. Apply safe fixes with
`ruff check --fix`, but review unsafe fixes individually instead of enabling
them repository-wide.

### Experimental garuff check

[garuff](https://github.com/gahjelle/garuff) runs in a separate, blocking CI
job. Run the identical check locally with uv installed:

```console
$ make garuff
```

The Makefile pins upstream commit
`7d126c00f13b4d94f39f1107934089241ab7a6a1` (package version `2026.7.0`).
`uvx --python 3.14` gives it the Python 3.14+ interpreter it requires without
adding it to the project's Python 3.11+ dependency groups. The check includes
the package's Python modules, `pyroma/tests.py`, documentation, and README;
packaging fixtures in `pyroma/testdata` are deliberately excluded by passing
the top-level modules explicitly. The project's `[tool.garuff]` configuration
disables `GAC010` (function docstrings), `GAC008` (positional parameter limits),
and `GAC011` (possessive example names). The remaining default rules stay enabled.

CI retains the check's nonzero exit status and blocks on findings or setup/checker
failures. Standard output and errors appear in the job summary and downloadable
`garuff-report` artifact. Inspect the report to distinguish code findings from
installation errors, configuration failures, or checker crashes.

Initial evaluation on October 10, 2026, reported **275 findings**:

| Rule | Initial findings | Resolution |
| --- | ---: | --- |
| GAC010 | 205 | Disabled; retain existing helper and test documentation conventions. |
| GAC008 | 48 | Disabled; retain the existing function signatures. |
| GAC004 | 15 | Fixed; docstrings use single backticks. |
| GAC009 | 5 | Fixed; all dataclasses use `kw_only=True` and callers name their arguments. |
| GAC011 | 2 | Disabled; retain the existing documentation example names. |

Of these, 190 findings are in `pyroma/tests.py`. They are convention checks,
not evidence of runtime bugs. A whole-tree baseline produced 276 findings;
excluding the packaging fixtures removes one fixture docstring finding.
After the configured ignores and code changes, `make garuff` reports **zero
violations**. The public result dataclass constructors now require keyword
arguments; this is documented in the upgrade guide and changelog.

The initial scoped check completed without a crash on CPython 3.14.8 and 3.15.0,
with identical findings and exit code 1. `make garuff` translates a
nonzero checker status to make's exit code 2, so the CI pipeline must retain
`pipefail` when capturing output with `tee`.

Isolated smoke checks exposed two configuration error-handling issues at
the pinned commit:

- Malformed TOML, such as an unterminated `[tool.garuff` header, raises an
  uncaught `tomllib.TOMLDecodeError` traceback.
- Valid TOML with `[tool]` followed by `garuff = 1` raises an uncaught
  `TypeError` rather than a configuration diagnostic.

Both crashes return exit code 1, the same as ordinary lint findings.
A clean one-file project returns 0, and missing paths or an unknown
configuration key return 2 with a diagnostic. These probes were run in
temporary projects; this repository's configuration did not trigger either
crash. The blocking job and saved stderr make these failures visible; checker crashes
fail CI just as violations do.

The first source installation succeeded but uv 0.13.0 warned that upstream's
`uv_build>=0.11.26,<0.12.0` build requirement does not include its current
version. The project's own build/test backend and uv-build fixture now use
`uv_build>=0.13.0,<0.14.0`; the pinned upstream garuff source still declares
its older backend constraint. The pinned install remains dependent on GitHub
and the build backend being available. Re-evaluate these results when updating
the commit pin.

The initial Python 3.11 suite reported 151 passes and one failure with 97%
coverage: `RatingsTest.test_uv_build` found an extra `pyproject.toml.orig`
in its generated sdist, also reproduced on the starting revision `54f5558`.
The backend deliberately includes that generated file; manifest configuration
now allows exactly that filename in the project and the uv-build fixture.
Final validation passes all 152 tests on Python 3.11 with 97% coverage, Ruff
linting and formatting, ty, pyrefly, actionlint, TOML/YAML/whitespace hooks,
and wheel/sdist builds. The package rates itself 10/10 with matching source
and manifest files. Garuff reports zero violations on Python 3.14 and 3.15.
At that initial evaluation, the workflow had been checked locally but had
not yet run on hosted GitHub Actions.

## Packaging and quality checks

Build the wheel and source distribution with uv:

```console
uv build
```

Run the same self-rating, complexity, and dependency checks used in CI:

```console
uv run --locked --no-default-groups --group test pyroma --no-config --strict --min 10 .
uv run --locked --only-group quality lizard pyroma --exclude "pyroma/testdata/*" --warnings_only
uv run --locked --no-default-groups --group quality deptry . --github-output
```

Lizard uses its default strict threshold of 15 and must report no warnings.
Deptry excludes bundled compatibility fixtures and has narrow exceptions for
optional or indirectly loaded dependencies documented in `pyproject.toml`.
Project-specific Semgrep rules prevent URL credentials and signed query
parameters from being copied into user-facing exceptions in both runtime and
CI code. Behavioral tests cover release preflight requirements:

```console
uv run --locked --only-group quality semgrep scan --config .semgrep.yml --error pyroma ci
```

## Adding a check

A check is a `BaseTest` subclass in `pyroma/ratings.py`, registered in
`ALL_TESTS`. Give it a `category` from `pyroma/rules.py`, and start it with
`advisory = True`: a new check is reported but not scored until it has been
through a minor release without false-positive reports. To graduate it, drop
the `advisory` attribute, move it out of the advisory block in `ALL_TESTS`,
and note the graduation in the changelog. An advisory check must never set
`fatal = True`; the test suite enforces this.

Anything a check needs from the working tree is gathered in
`pyroma/projectdata.py` and attached to the metadata as an underscore
sentinel declared in `pyroma/metadata.py`, so that rating stays a pure
function of the metadata dict and the Hypothesis property test can cover it.
Add a row to `docs/rules.md`, and a regression test in `AdvisoryChecksTest`
for each message the check can produce.

## Test data

The "complete" package in `pyroma/testdata` is designed to score the
maximum rating. If you change it, regenerate the distribution files the
tests unpack:

```console
$ make generate
```

which rebuilds `pyroma/testdata/distributions/complete-1.0.dev1.*`
from the `pyroma/testdata/complete` directory.

## Publishing

Publishing a GitHub release, directly or from a saved draft, starts
`.github/workflows/publish.yml`. Its reusable
package-validation workflow checks the lockfile, builds an sdist and a wheel from
that sdist, and tests the installed wheel outside the checkout on Python 3.11,
3.13, and 3.14. Property and CI gate tests, including the documentation workflow
they exercise, come from the sdist; unit tests and fixtures come
from the wheel. A separate environment installs only runtime dependencies and
checks imports, typing/license files, entry points, and both CLI invocation forms.

The release tag (with an optional single lowercase leading `v`) must match project, wheel, and
sdist versions under PEP 440 comparison; for example, `6.2.0-rc1` and `6.2.0rc1`
are equivalent. Installed metadata and CLI reports use the same comparison.
Version tags begin with an ASCII digit after the optional prefix and contain no
whitespace. Tag policy is checked before version parsing so invalid prefixes
and whitespace produce a release-tag diagnostic. Uppercase and repeated prefixes
are rejected; release-component
aliases such as `v6.2` and `v6.2.0.0` remain equivalent to version `6.2.0`.
The documented pre-commit `rev` must equal the literal release tag, since Git
does not apply PEP 440 equivalence to refs.
Publishing consumes the same downloaded distributions after validation succeeds,
from the `pypi` environment using Trusted Publishing. No
build/test job receives OIDC credentials. PyPI's publisher must identify this
repository, `publish.yml`, and the `pypi` environment.

Before publishing a GitHub release, check out its intended tag or commit and
exercise the same release gates locally without publishing. Replace the
illustrative `6.2.0` below with the exact intended Git tag. The checkout must
have that release version, a dated changelog heading, and the matching literal
README hook revision:

<!-- pyroma-release-checks -->

```console
(
  set -e
  RELEASE_TAG=6.2.0
  uv lock --check
  uv run --locked --only-group release python ci/package_artifacts.py preflight --release-tag "$RELEASE_TAG"
  uv run --locked --only-group release python ci/release_docs.py check --release-tag "$RELEASE_TAG"
  uv build --clear
  for release_python in 3.11 3.13 3.14; do
    uv run --locked --only-group release python ci/package_artifacts.py validate --work "$(mktemp -d)/release-check" --python "$release_python" --release-tag "$RELEASE_TAG"
  done
)
```

The subshell stops at the first failed gate without closing your interactive
shell. The build clears old distributions before validating the new artifacts.
The work directory must not already exist and should be outside the checkout,
as in the example above, to avoid test module names colliding with installed
dependencies such as `build`. GitHub's "Validate package artifacts"
workflow also accepts a release tag through manual dispatch and never publishes.
Select the intended tag as the dispatch ref; `release-tag` defaults to that tag.
An explicit input takes precedence and must match the selected ref. When a
release tag is effective, the
dispatch ref must be exactly `refs/tags/<release-tag>`; selecting a branch or
another tag fails before distributions are built. The shared `preflight` command
also checks tag syntax and project-version agreement before building, using the
same parser as artifact inspection. Its `--event-name` and `--ref` default to
`GITHUB_EVENT_NAME` and `GITHUB_REF`, and can be overridden for local checks.
Tagless branch dispatch remains available for general validation. Wait for validation to succeed before
publishing the release. This preflight is manual: the automated gate blocks
PyPI upload, while a failed release validation can leave an existing GitHub
release without a PyPI upload.
It runs on pull requests, `master` pushes, manual dispatch, and release workflow
calls. Tag pushes do not run a separate validation matrix before release
validation. The test and lint workflow triggers are unchanged.
Locked invocations provide the inspector's `packaging` dependency. Local
validation selects POSIX or Windows environment and console-script paths.
Source-test CI saves JUnit reports on successful and failed runs, alongside
coverage XML artifacts. Codecov uploads use the repository's `CODECOV_TOKEN`
on repository-owned runs, skip external fork PRs, and remain nonblocking.
The existing pytest coverage threshold remains the enforcement gate.
Both archives reject egg-info, bytecode caches, Finder metadata, and `build` or
`dist` output directories. Build exclusions prevent those members even in a
dirty source tree; intentional archives under `testdata/distributions` remain.

## CI gates and audits

Coverage measures production `pyroma` modules, including the CLI entry module,
with branches enabled. Tests and packaging fixtures are excluded. Every matrix
entry must reach 94% combined coverage across statements and branches, displayed
with two decimal places, and uploads coverage XML. The threshold applies to the
combined metric; statement and branch percentages have no independent floors.
Pytest rejects unknown configuration, unknown markers, and unexpected warnings.
The four legacy fixture builds allow `BuildBackendWarning` by category without
requiring warning prose or presence; other warnings remain errors.
Fixtures are copied into temporary Git repositories so manifest tests work with
an installed wheel as well as a checkout. Setup publishes the temporary path
after copying and Git initialization succeed. Teardown restores the original
source path, supporting repeated module runs and harmless repeated cleanup.
Fixture Git subprocesses strip inherited `GIT_*` variables and use temporary
HOME/XDG directories, `GIT_CONFIG_NOSYSTEM=1`, and `GIT_ATTR_NOSYSTEM=1`. They
explicitly select empty template/hook directories and null external attributes
and exclusions files. This isolation works with Git versions before 2.32,
without depending on `GIT_CONFIG_GLOBAL`. Hostile configuration probes cover
global attributes and executable `post-index-change` hooks. The executable-hook
portion requires Git 2.22 or newer and a discoverable `sh` interpreter; earlier
Git versions and environments without that shell still run the other isolation
assertions for both HOME and XDG configuration. Unsupported hook probes report
an explicit skip, including when the Git version is unknown. Supported hook
probes use `#!/bin/sh` and require a positive control that executes the hostile
hook before verifying isolation.
Caller indexes and
environments remain untouched and all fixture archives are tracked.

Typing scans recognize regular `pyvenv.cfg` files and symlinks to regular files
without entering the environment's installed packages, including config targets
outside the project. Missing, looping, and directory-target
config symlinks do not identify an environment; Conda markers are still checked
after an unsuccessful config symlink check. Permission failures and uncertain
I/O errors inspecting either a marker or its symlink target conservatively prune
that directory. Conda detection still requires a
regular `conda-meta` directory and regular `history` file; symlinks there do not
prune sources. Symlinked sources and typing markers do not supply typing evidence.

CI pins uv 0.13.0, actionlint 1.7.12, and ShellCheck 0.11.0. Workflow-checker
archives are verified against pinned SHA-256 digests. Strict self-rating and
garuff are blocking. Project commands use `--locked`, so stale `uv.lock` files
fail rather than being silently updated. Update dependencies intentionally with
`uv lock` and commit the resulting lockfile.
Standalone checks remain where pre-commit, garuff, or release building cannot
guarantee an unconditional locked invocation. Diagnostic uploads warn if setup
failed before producing a report; required release distributions still block.
The stale-lock regression uses a dependency-free project and an empty cache
with network access disabled, rather than resolving the full project again.

Audit every locked registry package/version, including versions selected only on
other platforms or Python versions:

```console
uv run --locked --only-group audit python ci/audit_dependencies.py
```

The gate removes old output files before validating inputs, then writes
`audit-report.json` and a hashed
`audit-report.requirements.txt` inventory. The inventory includes every locked
version and may contain conflicting pins; it is not passed directly to
pip-audit. Temporary batches contain at most one version per package name and
their results merge into the existing report schema. Only complete audits
publish a merged report. The gate rejects skipped, duplicate, or incomplete
results and unsupported sources, and blocks every unaccepted advisory. Prefer
compatible upgrades. `audit-exceptions.toml` starts empty. An exception must name
an advisory (or alias), package, exact version, evidence-backed reason, owner
(default `hbmartin`), and TOML `created`/`expires` dates. Dates use UTC; lifetimes
cannot exceed 30 days. Expired, malformed, duplicate, and unused exceptions fail.
Audit service failures also fail the job with captured tool diagnostics.
Accepted batches replay their stdout and stderr, including batches whose findings
are covered by exceptions. Complete reports remain available when findings or
unused exceptions fail the policy gate; failed inputs and partial audits cannot
leave a stale report that appears current. The CLI computes today's UTC date once
and passes it explicitly to the audit helper. Audit tests supply fixed dates;
a separate CLI regression verifies the UTC/local date boundary.
Unused exceptions list any new unaccepted findings alongside the cleanup error.
No automatic fixes or blanket package exclusions are performed.

The published `.[test]` extra has been removed. Use
`uv sync --locked --no-default-groups --group test` for contributor tools.
Plain `zest.releaser`, explicit `check-manifest`, and `pep440` replace its
`recommended` extra, which installed upstream `pyroma` into the same namespace.
Local tox uses the test dependency group and requires tox 4.22 or newer.

## Future ideas

Two improvements were considered but deliberately deferred:

- A static fast-path that reads a fully-static `[project]` table from
  `pyproject.toml` with `tomllib` to skip the wheel metadata build.
  It is only a speed optimization: it cannot handle `dynamic` fields
  or setup.py-only projects, so the `build`-based path has to remain
  as the fallback.
- Capturing the build backend subprocess's stdout/stderr by passing a
  custom `runner` to `build.util.project_wheel_metadata` (the
  default `quiet_subprocess_runner` discards it), so that backend
  deprecation warnings can be surfaced in a report section.

## JSON contract and release documentation

Contract tests in `tests/contract` validate reports against the packaged
Draft 2020-12 schemas with the test-only `jsonschema` dependency. They cover
usage errors, policy precedence, discovery provenance, advisory presentation,
and version discovery without builds or network requests.
The shared session fixture loads each installed schema and checks it once;
the two self-contained schemas must have identical metadata definitions.

Prepare downloadable schemas before a strict documentation build:

```console
$ uv run --locked --only-group docs python ci/release_docs.py schemas
$ uv run --locked --only-group docs zensical build --clean --strict
```

The packaged JSON files are the source of truth; `docs/schemas/` is generated
and ignored. Preparation replaces this generated tree, including all canonical
schema versions, so repeated builds remove stale downloads. The docs-only
environment does not install the runtime project. Offline tests validate every
JSON documentation example and the PEP 440 syntax of its illustrative checker version.
After the strict build, CI requires a nonempty JSON guide and compares the
complete generated schema tree with the canonical packaged tree before uploading
it; this also runs on pull requests and covers every schema version.
Check documentation consistency locally, or include live release evidence:

```console
$ uv run --locked --only-group release python ci/release_docs.py check
$ uv run --locked --only-group release python ci/release_docs.py check --online
```

Tagged package validation runs the offline check before building. It requires
a dated changelog heading, a documented hook revision equal to the literal tag,
and a Production/Stable or Mature classifier for final releases. Every offline
check also requires the local `.pre-commit-hooks.yaml` to provide a `pyroma` hook
with `entry: pyroma` in the same entry. Publishing is independent
of the current Pages deployment. After deploying Pages, the docs workflow checks
availability of the JSON guide and every canonical schema download with bounded curl
retries. Schema inventory checkout follows deployment, and a missing or empty
inventory fails the availability check; it does not compare contents with a release tag.

The optional `--online` maintainer audit probes live documentation, compares
downloaded schemas with the checkout, verifies the documented release's
distribution and hook, and checks PyPI upload dates against changelog headings.
It is not part of the publish gate and can fail for old checkouts after the live
site or published release list changes.
For a tagged offline check, pass `--release-tag TAG` from the corresponding release
checkout after dating its changelog heading and updating its documented hook.
Only README examples preceded by a standalone `<!-- pyroma-pre-commit -->`
outside code blocks are parsed as YAML, including examples inside lists and
blockquotes; literal marker mentions and unrelated YAML blocks are ignored.
Each marker must precede a complete YAML fence in the same Markdown container,
with optional blank lines. Markers on fence lines are rejected, and unclosed
fences identify their opening README line. A marker merged into a larger HTML
block is rejected at its source line. Separate markers inside HTML wrappers
such as `<details>` from surrounding HTML with blank lines.
Every marked example must contain at least one entry with the fork's exact
`pyroma` hook, and all matching revisions must agree. Other repository or hook
entries in that block are allowed and do not contribute revisions.
Repository URLs accept an optional `.git`
suffix and trailing slash. Published hook definitions are also parsed as YAML; repository,
hook ID, revision, and entry must belong to the matching entry. The `release`
dependency group supplies the Markdown parser, PyYAML, and packaging without
installing the runtime project. README input accepts an initial UTF-8 BOM,
and diagnostics use Markdown source lines.
Requests have 15-second timeouts and 2 MiB response limits; fetch, decoding,
and remote JSON/TOML parsing or structure errors identify the resource and
invalid field without printing its URL or response values. HTTP error responses
are closed, and malformed packaged schemas identify their path. Unit tests mock all
remote evidence.
Installed-artifact validation covers Python 3.11, 3.13, and 3.14, including
both CLI entry points, schema resources, version output, usage-error JSON,
and effective policy. The corrected README ships with the next PyPI release;
GitHub Pages uses the normal documentation deployment workflow.
Development versions use a versioned unreleased changelog heading, which
zest.releaser dates during prerelease preparation. Development and prerelease
metadata may use `4 - Beta`; final tagged releases require
`5 - Production/Stable` or `6 - Mature`.
The zest lifecycle tests use synthetic history and versions, so they also pass
on dated release commits and after the next development version bump.
