# Testing

Run the test suite with pytest:

```console
$ python -m pytest
```

The test extra includes Hypothesis. Its generated metadata cases run as part
of the normal pytest and tox suites, including the cross-platform CI matrix.

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

The explicit `property` profile also selects 100 examples. Generated tests
disable timing deadlines; use pytest's `--durations=10` to inspect runtime
without making machine speed a correctness requirement. Hypothesis keeps
minimized failures in its ignored `.hypothesis` database. Turn demonstrated
bugs into explicit regression examples and narrow fixes.

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
$ uv run --only-group lint ruff check --force-exclude .
$ uv run --only-group lint ruff format --force-exclude .
$ uv run --extra test --group typecheck ty check
$ uv run --extra test --group typecheck pyrefly check
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

[garuff](https://github.com/gahjelle/garuff) runs in a separate, advisory CI
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

CI retains the check's nonzero exit status, while job-level
`continue-on-error` keeps it advisory. Standard output and errors appear in
the job summary and the downloadable `garuff-report` artifact. Check the
report before treating a failed run as a code defect: installation errors,
configuration failures, and checker crashes are also possible. Promote the
job to a merge gate only after evaluating its rules and stability.

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
crash. The advisory job and saved stderr make these failures visible without
blocking unrelated work.

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
The workflow has been checked locally; a hosted GitHub Actions run has not
yet been performed.

## Packaging and quality checks

Build the wheel and source distribution with uv:

```console
uv build
```

Run the same self-rating, complexity, and dependency checks used in CI:

```console
uv run --no-default-groups --extra test pyroma --min 10 .
uv run --only-group quality lizard pyroma --exclude "pyroma/testdata/*" --warnings_only
uv run --no-default-groups --group quality deptry . --github-output
```

Lizard uses its default strict threshold of 15 and must report no warnings.
Deptry excludes bundled compatibility fixtures and has narrow exceptions for
optional or indirectly loaded dependencies documented in `pyproject.toml`.
The project-specific Semgrep rule prevents URL credentials and signed query
parameters from being copied into user-facing exceptions:

```console
uv run --only-group quality semgrep scan --config .semgrep.yml --error pyroma
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

Creating a GitHub release starts `.github/workflows/publish.yml`. The workflow
builds the distributions without OIDC credentials, then publishes the
uploaded artifact from the protected `pypi` environment using PyPI Trusted
Publishing. PyPI must have a trusted publisher configured for this repository,
the `publish.yml` workflow, and the `pypi` environment.

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
