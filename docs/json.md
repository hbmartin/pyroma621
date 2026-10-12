# JSON reports

Schema version 1, JSON argument errors, and `--version` require 6.2 or newer
and are available in development checkouts. They are unavailable in 6.1.0.
Install a development checkout with `python -m pip install .` from the repository
root. Checker versions in the examples are illustrative; actual reports identify
the installed version.

Use `pyroma --format json PACKAGE` to write one JSON document to stdout.
Both `--format json` and `--format=json` work. Argument errors honor the last
unambiguous format option before `--`, even if validation fails before reaching
that option. `--help` and `--version` still print text and exit successfully.
Unambiguous long-option abbreviations are accepted, including `--form=json`.
Prefer full names in scripts because new options can make a prefix ambiguous.

## Schemas and compatibility

Every report includes `_meta.schema_version: 1`. Download the
[rating schema](schemas/v1/rating.json) or [error schema](schemas/v1/error.json).
These self-contained JSON Schemas use Draft 2020-12 and are also packaged under
`pyroma/schemas/v1/` for offline validation.

Consumers should accept additional fields and unfamiliar rule, category, and
error identifiers. Optional fields and new identifiers can be added within
version 1. Removing or changing required fields, field types, or existing
identifier meanings requires a new schema version; previous schemas remain
available. Registered rule identifiers are the names accepted by `--skip-tests`
and remain stable within the schema version. Internal report identifiers such
as `NoConfigFound` and `WheelBuildFailed` are also stable, but are not individually
selectable with that option. The Pyroma-defined `UsageError`, `BuildError`,
`ConfigError`, and `ConfigurationError` identifiers follow the same policy.
Other error identifiers describe exception classes, such as `ValueError` or
`FileNotFoundError`, and may vary by platform or provider. Messages, finding
order, and cheese descriptions are human-readable and may change.

A schema version describes the document structure, not a fixed scoring policy.
New advisory checks and announced scoring changes follow the
[rule lifecycle](rules.md#scored-and-advisory-checks). Record `_meta.pyroma` to
identify the checker release that produced the findings.

## Rating documents

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
      "fatal": false,
      "category": "metadata"
    }
  ],
  "advisories": [],
  "_meta": {
    "schema_version": 1,
    "pyroma": "6.2.0.dev0",
    "package": ".",
    "mode": "directory",
    "policy": {
      "min_rating": 8,
      "strict": false,
      "show_advisories": true,
      "skip_tests": []
    },
    "config": {
      "enabled": true,
      "loaded": false,
      "source": "/workspace/example/pyproject.toml"
    }
  }
}
```

`name` is a string or null when metadata supplies no name. `rating` is an
integer from 0 to 10: fatal findings produce 0; otherwise weighted successful
and failed scored checks produce a score from 1 to 10. `level` is its text
description. Both `problems` and `advisories` are required arrays, including
when empty. Each finding has a stable `test` identifier, human-readable
`message`, nonnegative integer `weight`, boolean `fatal`, and string `category`.
The [category reference](rules.md#categories) explains the current categories.

`problems` contains scored failures. A fatal problem forces a zero rating;
nonfatal weights determine the score together with successful scored checks.
`advisories` contains unscored findings, which are never fatal. With `--strict`,
advisory failures are scored and appear in `problems`, leaving `advisories`
empty. `--no-advisories` changes text presentation only: JSON retains advisory
findings. `--quiet` also leaves JSON complete.

## Error documents and exit codes

```json
{
  "error": {
    "type": "UsageError",
    "message": "argument -n/--min: --min must be an integer between 1 and 10, not '11'"
  },
  "_meta": {
    "schema_version": 1,
    "pyroma": "6.2.0.dev0"
  }
}
```

Errors have an `error` object with string `type` and `message`, and no rating
fields. Invalid arguments exit with code 2 and type `UsageError`. A rating
below the requested minimum also exits with code 2, but contains `rating`.
Operational and configuration failures exit with code 3. Successful ratings
at or above the minimum exit with code 0.

JSON argument failures write one document to stdout without usage text or a
traceback. Text argument failures retain argparse's usage and error on stderr.
Index diagnostics may still use stderr without corrupting JSON stdout.

## Effective policy and configuration

CLI ratings record the resolved mode, original package argument, effective
minimum, strict setting, advisory presentation setting, and sorted unique
skip selectors. Selectors can be rule names or categories; a category skips
all its rules in that checker release. Explicit CLI flags override project
settings, which override defaults (minimum 8, strict false, advisories true,
and no skipped rules).

Only directory mode discovers settings, in the target directory's
`pyproject.toml`; discovery does not search parent directories or the invoking
directory. Source-distribution and PyPI modes use CLI settings and defaults,
even when an archive contains `[tool.pyroma]`. `--no-config` disables discovery.

`config.enabled` reports whether discovery was enabled. `config.loaded` is true
when a valid `[tool.pyroma]` table was applied, including an empty table.
`config.source` is the discovered TOML file's absolute path, even when it is
malformed, or null when absent, disabled, or running outside directory mode.
A file without `[tool.pyroma]` is recorded with `loaded: false`.

Operational errors retain effective policy if settings were resolved. Usage
errors and configuration-validation errors omit unresolved policy; usage
errors also omit target and discovery metadata. `_meta.pyroma` is omitted
when installed distribution metadata is unavailable. In that situation,
`pyroma --version` prints `pyroma621 unknown` without accessing a target.

The `_meta` object and its schema version are always required. Other metadata
fields are optional for library-generated reports. `format_json()` and
`format_json_error()` accept nested metadata; `run()` accepts optional
keyword-only `report_meta` without changing its positional API or integer
return value.
