# Testing

Run the test suite with pytest:

```console
$ python -m pytest
```

The test extra includes Hypothesis. Its generated metadata cases run as part
of the normal pytest and tox suites, including the cross-platform CI matrix.

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
