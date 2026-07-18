# Testing

Run the test suite with pytest:

```console
$ python -m pytest
```

or across all supported Python versions:

```console
$ tox
```

## Linting and type checking

Ruff, ty and pyrefly run through uv, which reads the pinned tool
versions from the `lint` and `typecheck` dependency groups in
`pyproject.toml` — the same pins that CI and pre-commit use, so
versions never drift between environments:

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

## Test data

The "complete" package in `pyroma/testdata` is designed to score the
maximum rating. If you change it, regenerate the distribution files the
tests unpack:

```console
$ make generate
```

which rebuilds `pyroma/testdata/distributions/complete-1.0.dev1.*`
from the `pyroma/testdata/complete` directory.

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
