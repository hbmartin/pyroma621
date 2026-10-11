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
| `DependencySpecifiers` | `Requires-Dist` entries must be valid dependency specifiers. Malformed entries are identified by their one-based position, without echoing input or parser errors that may contain credentials. |
| `DescriptionContentType` | `Description-Content-Type` must be a known type with valid parameters. |

## `index` &mdash; a package index will reject this

| Check | Notes |
| --- | --- |
| `NameFormat` | The project name must match the name specification. Fatal. |
| `MetadataVersion` | `Metadata-Version` must be one an index accepts. |
| `PEPVersion` | The version must be valid under the version specifiers specification. |
| `Licensing` | `License` and `License-Expression` together are forbidden; the expression must be valid SPDX. |
| `DirectUrlDependency` | A `name @ https://…` dependency is rejected outright. Diagnostics list sorted, unique dependency names only, keeping credentials, URLs, extras and markers out of reports. `twine check` does not catch this. Fatal. |
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
| `PythonVersionCoherence` | *Advisory.* A Python classifier contradicts `Requires-Python` only when their version ranges have no intersection, including patch restrictions and wildcard exclusions. Requires `packaging>=26.3`. |
| `DevelopmentStatusCoherence` | *Advisory.* A stable Development Status contradicts a `0.x` or pre-release version. |
| `BuildBackendVersionFloor` | *Advisory.* PEP 639 licensing needs `setuptools>=77.0.0` or `hatchling>=1.27`. Flit needs 3.11 for simple SPDX identifiers and `license-files`, or 3.12 for supported compound or parenthesized expressions. See the conservative handling below. |
| `LicenseFilesExist` | *Advisory.* Every `license-files` pattern should match a real file. |
| `ReadmeContentTypeMatch` | *Advisory.* Emitted metadata must match the explicit content type in a readme table, or the extension inferred for a string declaration. Media types are compared independently of parameters. Explicit overrides are allowed. |
| `TypedMarker` | *Advisory.* Source-tree `py.typed` markers with Python/stub source evidence, or complete `*-stubs` packages containing `.pyi` files, should agree with `Typing :: Typed`. Partial stub packages require a marker. This does not verify wheel contents. |
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

Backend requirements are combined independently of their order. Python-only
markers divide the project's declared Python range (falling back to built
`Requires-Python`, or unrestricted when absent) into feasible partitions.
Conjunctions, disjunctions, parentheses and reversed comparisons are supported;
`python_version` compares major/minor series, while `python_full_version`
compares complete versions. A warning requires a feasible partition that
allows an older backend. Contradictory partitions are skipped. Direct references,
other environment variables and unsupported comparisons skip the advisory
unless unconditional constraints already prove the required floor. Unknown
backends and unknown Flit feature requirements, including `WITH` expressions,
are skipped rather than assigned a guessed minimum.

Floors are strict guarantees over admitted versions, including prereleases.
For example, `setuptools==77.*` admits `77rc1`, which is below the final
`77.0.0` floor, so it warns even though ordinary resolution prefers final
releases. `setuptools>=77` or `setuptools~=77.0` excludes those older
prereleases and passes.

Runtime dependency checks exclude a dependency only when every possible marker
branch requires a selected extra. Conditions such as `extra != "test"`,
`extra == ""`, and an extra condition joined by `or` to another environment
condition remain eligible. Uncertain conditions are retained, without using
the environment of the machine running pyroma to decide applicability.

Typing facts come from a recursive source scan, including namespace subpackages
and custom source directories. A marker must be a regular file below the
project root, with a regular `.py` or `.pyi` file in its directory or a scanned
descendant. Isolated documentation/test markers do not count; Python-bearing
documentation/test packages can still qualify, since this does not prove
wheel inclusion. Hidden directories, caches and `node_modules` are pruned.
Virtual environments are identified by `pyvenv.cfg`, not their directory
names. Root `build`, `dist` and `_build` directories are pruned unless they
contain direct Python/stub source files; nested packages with those names
are retained. Directory symlinks are not followed, and file symlinks do not
provide markers or source evidence. Inaccessible entries are skipped without
discarding accessible siblings, and unrelated filenames are not statted.
Missing source facts skip the typing advisory.

Markdown link extraction uses CommonMark tokens from `markdown-it-py`, without
rendering HTML. It covers inline links and images, images nested inside links,
full, collapsed and shortcut reference links, and HTML `href`/`src` attributes.
Reference labels ignore case and normalize whitespace; the first definition
wins. Angle-delimited destinations, balanced parentheses, optional titles,
escapes and entities are handled. Fenced and indented code, inline code, HTML
comments and literal HTML `pre`, `code`, `script` and `style` regions are
ignored. Reference definitions respect paragraph boundaries, and unmatched
brackets or backticks do not hide destinations in later paragraphs. Image
alt text is not scanned for links. Destinations retain document order, with
duplicates listed once. Extension-specific syntax is not enabled.

### Markdown extraction performance

The following local measurements use CPython 3.13.15 on macOS arm64 and report
the median of five calls to `_relative_description_targets`. Input construction
is outside the timed region. Markdown items are `[x](docs/N.md)`, HTML items
are `<a href="html/N.md">x</a>`, and mixed items contain one of each. Each
destination is unique, and items are separated by spaces.

| Input | 2,000 items | 4,000 items | 8,000 items |
| --- | ---: | ---: | ---: |
| Markdown links | 20.4 ms | 43.7 ms | 89.7 ms |
| HTML links | 27.0 ms | 56.8 ms | 131.6 ms |
| Mixed links | 51.6 ms | 113.9 ms | 246.1 ms |

These inputs show approximately linear scaling after removing repeated document
copies and list searches. Measurements are informational, rather than fixed CI
timing thresholds. Regression tests verify extraction order and that increasing
unrelated source files from 100 to 1,000 does not increase typing-file stat probes.

## `internal` &mdash; pyroma could not do its job

| Check | Notes |
| --- | --- |
| `MissingBuildSystem` | Neither `setup.py` nor `pyproject.toml`, only `setup.cfg`. |
| `MissingPyProjectToml` | No `pyproject.toml`, which is strongly recommended. |
