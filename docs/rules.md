# Rules

Every check pyroma runs is listed here. A check is identified by its class
name, which is what `--skip-tests` accepts and what the JSON reporter emits
as `test`.

## Scored and advisory checks

**Scored** checks decide the 1&ndash;10 rating. **Advisory** checks are reported
below the rating but kept out of the arithmetic.

A check is advisory because it is new. Every check lands as advisory and
stays that way for at least one minor release, so an upgrade shows you what
pyroma would say about your project before it can move your rating. Once a
check has been through a release without reports of false positives, it
graduates to scored, and the changelog says so. An upgrade can therefore
change your rating, but never unannounced.

An advisory check is never fatal. Under `--strict`, which scores the advisory
checks right away, a failing advisory check lowers the rating by its weight
like any other check; it cannot zero it. `--strict` scores opinions too, so
combine it with `--skip-tests practice` to score everything except the
`practice` category. `--no-advisories` hides advisory findings from the text
report. `--no-strict` and `--advisories` undo a `[tool.pyroma]` setting from
the command line.

Advisory findings always appear in `--format json`, under an `advisories`
key, regardless of `--no-advisories`.

## Categories

Each check has a category. A whole category can be skipped at once, for
example `--skip-tests practice`.

| Category | Meaning |
| --- | --- |
| `spec` | A packaging specification says MUST, and pyroma can prove it is broken. |
| `index` | A package index will reject the upload outright. |
| `metadata` | A metadata field is absent, empty or uninformative. |
| `coherence` | Two pieces of metadata are individually valid but contradict each other. |
| `practice` | An opinionated recommendation, not required by any specification. |
| `internal` | Pyroma could not do its job: no config found, or the build failed. |

## Configuration

Settings can live in the rated project's own `pyproject.toml`. An explicit
command-line flag always wins; configuration only fills in what you did not
ask for. Pass `--no-config` to ignore the table entirely.

```toml
[tool.pyroma]
min-rating = 10
strict = false
advisories = true
skip-tests = ["practice", "CheckManifest"]
```

Every boolean setting has a flag in both directions, `--strict`/`--no-strict`
and `--advisories`/`--no-advisories`, so the command line can always override
the table without resorting to `--no-config`. Names in `skip-tests` are
validated the same way `--skip-tests` is: an unknown test or category is an
error, not a silent no-op.

Configuration is read in directory mode only. In file and PyPI modes the
project is an archive that has not been unpacked when the settings are
needed, and a downloaded artifact is a poor source for settings that change
pyroma's own exit code.

## `spec` &mdash; specification violations

| Check | Notes |
| --- | --- |
| `PyprojectTomlValid` | `pyproject.toml` must parse and validate against the registered schemas. |
| `PyProjectProjectTable` | `[project]` table rules that schema validation does not catch. Fatal. |
| `DependencySpecifiers` | `Requires-Dist` entries must be valid dependency specifiers. |
| `DescriptionContentType` | `Description-Content-Type` must be a known type with valid parameters. |

## `index` &mdash; a package index will reject this

| Check | Notes |
| --- | --- |
| `NameFormat` | The project name must match the name specification. Fatal. |
| `MetadataVersion` | `Metadata-Version` must be one an index accepts. |
| `PEPVersion` | The version must be valid under the version specifiers specification. |
| `Licensing` | `License` and `License-Expression` together are forbidden; the expression must be valid SPDX. |
| `DirectUrlDependency` | A `name @ https://…` dependency is rejected outright. `twine check` does not catch this. Fatal. |
| `SummaryFormat` | The summary must be one line of at most 512 characters. Fatal. |

## `metadata` &mdash; missing or uninformative fields

| Check | Notes |
| --- | --- |
| `Name`, `Version` | Required. Fatal when absent. |
| `VersionIsString` | The version should be a string, not a number. |
| `Summary`, `Description` | Present, and long enough to say something. |
| `Classifiers`, `Keywords` | Present. |
| `Author`, `AuthorEmail` | Present. |
| `Url` | Project URLs should use well-known labels. |
| `PythonClassifierVersion` | Classifiers should name the minor Python versions supported. |
| `PythonRequiresVersion` | `Requires-Python` should be set. |
| `DevStatusClassifier` | A Development Status classifier communicates maturity. |
| `ValidREST` | A reStructuredText description must render. |

## `coherence` &mdash; metadata that contradicts itself

| Check | Notes |
| --- | --- |
| `ClassifierVerification` | Classifiers must be registered; deprecated ones name their replacement. |
| `DeprecatedMetadataFields` | Deprecated fields should give way to their replacements. |
| `PythonVersionCoherence` | *Advisory.* Python classifiers must agree with `Requires-Python`. Installers trust `Requires-Python` and ignore classifiers. |
| `DevelopmentStatusCoherence` | *Advisory.* A stable Development Status contradicts a `0.x` or pre-release version. |
| `BuildBackendVersionFloor` | *Advisory.* PEP 639 licensing needs `setuptools>=77.0.0`, `hatchling>=1.27` or `flit-core>=3.12`. Older backends emit the wrong metadata silently. |
| `LicenseFilesExist` | *Advisory.* Every `license-files` pattern should match a real file. |
| `ReadmeContentTypeMatch` | *Advisory.* The readme's content type should match its file extension. |
| `TypedMarker` | *Advisory.* A `py.typed` marker and the `Typing :: Typed` classifier should agree. |
| `ProjectUrlLabelCollision` | *Advisory.* Two labels that normalize alike collapse into one link. |

## `practice` &mdash; opinionated recommendations

| Check | Notes |
| --- | --- |
| `SDist` | A source distribution should be published. |
| `BusFactor` | A project should have three or more owners on PyPI. |
| `CheckManifest` | Version-controlled files and the sdist should match. Requires `check-manifest`. |
| `EndOfLifePython` | *Advisory.* Do not advertise support for end-of-life Python versions. The verdict depends on the date pyroma runs. |
| `PythonRequiresUpperBound` | *Advisory.* Capping `Requires-Python` breaks on every new Python release. |
| `DependencyUpperBounds` | *Advisory.* Capped or pinned runtime dependencies cause resolution conflicts downstream. Skip this if you publish an application. |
| `DependencyLowerBounds` | *Advisory.* A dependency with no floor may resolve to something far older than you tested. |
| `DevelopmentExtras` | *Advisory.* Development dependencies belong in `[dependency-groups]`, not in published extras. |
| `ReadmeRelativeLinks` | *Advisory.* Relative links and images do not resolve on PyPI. |
| `BuildBackendDeclared` | *Advisory.* A `[build-system]` table without `build-backend` falls back to legacy behaviour. |

## `internal` &mdash; pyroma could not do its job

| Check | Notes |
| --- | --- |
| `MissingBuildSystem` | Neither `setup.py` nor `pyproject.toml`, only `setup.cfg`. |
| `MissingPyProjectToml` | No `pyproject.toml`, which is strongly recommended. |
