# Changelog

## 6.2.0 (2026-10-13)

- Pin the built JSON guide's file identity before opening, recognize descriptor
  namespace case aliases by directory identity, and keep fallback schema errors
  relative to the package or site. Report unexpected built directory boundaries
  without scanning their subtrees. Assert which capability each race test uses,
  exercise no-search traversal explicitly on macOS PyPy, and label unavailable
  search anchoring even under optimized Python.

- Keep descriptor-backed POSIX schema traversal when search-only flags are
  unavailable, give changed ancestors precise logical paths, and report extra
  built JSON files without reading them. Resolve output aliases in one bounded
  component walk, expanding links before parent components and recognizing
  thread-specific descriptor namespaces. Cover macOS PyPy release validation
  and Python 3.14 CLI color detection with accurate stream doubles.

- Require complete inventory stdout writes, reporting nonblocking output with no
  progress instead of silently losing the manifest. Preserve existing ancestor
  directory links and search-only permissions, read schemas during discovery,
  and reject changed directory identities. Keep descriptor ownership explicit
  through stream construction, detect descriptor aliases through parent links,
  and identify symlink failures precisely. Update the JSON guide for 6.2.0.

- Stabilize inventory stdout output with exact binary bytes and exit 1 after
  write failures, distinguish `./-` file output, and reject descriptor aliases.
  Replace ordinary broken output links without touching their targets. Anchor
  schema discovery and reads to directory descriptors on supported POSIX
  platforms, protect the JSON guide, and restore blocking mode after regular-file
  validation. Keep portable, resource-labelled errors and bounded race tests.
- Preserve unambiguous long CLI option abbreviations, including JSON format
  selection, keep parser error formatting consistent across ordinary and
  intermixed parses, and resolve checker metadata once per CLI invocation.
- Record configuration as loaded only after its skip selectors are validated.
- Parse every marked pre-commit example as Markdown, including lists and
  blockquotes, reject malformed fences with source-line diagnostics, and require
  consistent literal release refs. Accept README files with a UTF-8 BOM,
  describe features by their introduction version, and rebuild downloadable
  schemas without stale generated files or installing the runtime package.
- Keep package publishing independent of live documentation; check JSON guide
  and all canonical schema contents before upload and availability after Pages
  deployment. Local release instructions clear stale artifacts and stop at the
  first failed validation.
  Offline release checks validate the local hook manifest, final releases accept
  Production/Stable or Mature, and fetch errors identify resources without URLs.
- Reject hook markers swallowed by larger HTML blocks, label malformed remote
  metadata structures and packaged schemas, and close HTTP error responses.
  Reject unreadable or empty schema inventories before building Pages.
- Label local documentation metadata and packaged-schema decoding failures,
  reject local-version PyPI evidence, compare upload dates in UTC, and preserve
  interrupts during HTTP error cleanup. Prepare and verify only regular schema
  files, reject built symlinks, and pass the verified build inventory to deployment.
  Validate all local schemas before fetching live evidence, label README,
  changelog and hook-manifest read failures with safe OS categories, and explain
  online changelog failures with the expected UTC date without remote release keys.
  Require calendar changelog dates in `YYYY-MM-DD` form and distinguish boolean
  values from numbers when comparing published schemas. Reject special schema
  filesystem entries and unexpected built directories; inventory diagnostics name
  sorted missing and extra relative paths. Generated-schema and inventory-output
  failures use safe resource labels, including symlinked generated directories.
  Explain missing, unreleased, and malformed changelog dates with actionable
  diagnostics. Use a consistent `schemas/` path base and JSON-style ASCII escaping,
  and replace inventory manifests only after their temporary siblings are written
  and closed successfully. Bound real FIFO regression checks so faulty file
  classification cannot hang the test suite.
  Preserve existing manifest rwx permissions and respect the umask for new output,
  add explicit inventory stdout output, and reject special output destinations.
  Validate opened schema descriptors without blocking on POSIX FIFOs introduced
  after discovery. Make root labels identify their package or site location,
  share changelog heading validation, and keep race tests independent of implicit
  interpreter import paths.
- Keep release lifecycle tests independent of the current checkout version,
  share CLI/config selector validation, and include test dependencies in the
  default development environment.

- Invalid command-line arguments now produce a JSON `UsageError` when JSON
  output is requested. Usage errors retain exit code 2; low-rating reports
  remain distinguishable by their `rating` field. Threshold and skip-list
  diagnostics identify accepted values.
- Added `pyroma --version`, which requires no target or package build.
- JSON reports now identify schema version 1 and include effective CLI policy
  and configuration provenance. Downloadable rating and error schemas ship
  with the package and documentation.
- Corrected documentation links, the fork's pre-commit installation example,
  and published release dates. Tagged-release validation checks documentation
  and installation evidence; installed-artifact tests also cover Python 3.13.

## 6.1.0 (2026-10-12)

- Removed the published `test` extra. Contributor dependencies now use
  `[dependency-groups].test`; install with `uv sync --group test`.
- CI requires 94% combined production coverage across statements and branches,
  strict pytest configuration, and unexpected warnings as errors. Garuff and
  strict self-rating now block.
- Release distributions are tested outside the checkout before publishing;
  dependency audits cover every locked registry package version and enforce
  expiring, reviewed exceptions. Malformed exception entries produce controlled
  audit failures. Conflicting locked versions are audited in separate batches
  and merged into one complete report. Tool failures retain their diagnostics;
  unused exceptions report new vulnerabilities alongside the cleanup error.
  Old reports and inventories are removed before input validation, successful
  batch diagnostics remain visible, and audit tests use a fixed UTC date.
  The audit dependency group declares its direct `packaging` dependency.
  Workflow linting and tool versions are pinned.
- Artifact validation rejects generated metadata, caches, and build outputs;
  builds exclude those files while retaining intentional distribution fixtures.
  Release and metadata versions use PEP 440 equivalence, including prerelease
  spellings, with an optional single lowercase `v` prefix for release tags.
  Invalid tag prefixes and whitespace report the release-tag policy before
  version parsing.
  Local artifact validation supports Windows executable paths and POSIX license
  paths in smoke-test fixtures.
- Package validation runs on pull requests, `master` pushes, manual dispatch,
  and release workflow calls, avoiding a second matrix run on tag pushes.
  Manual preflight defaults to the selected tag ref; an explicit `release-tag`
  must match that ref. The shared artifact validator rejects malformed tags,
  mismatched refs, and project-version mismatches before building;
  tagless branch validation remains available.
  Publishing runs when a GitHub release is published, including from a draft,
  and uploads only distributions that pass release validation.
  Redundant lock checks are removed where locked commands enforce the gate;
  missing diagnostic uploads warn without obscuring an earlier failure.
- Test fixture setup publishes its temporary path after preparation succeeds;
  teardown restores the original source path so repeated module runs work.
  Failed setup cleans up its temporary directory, and teardown restores state
  even when cleanup raises. Fixture Git commands isolate inherited repository
  variables and global/system Git configuration and attributes, including templates,
  hooks, and excludes. Temporary HOME/XDG directories support Git versions before
  2.32. Regression probes execute a hostile index-change hook when Git 2.22+
  and a shell are available, using the fixed `#!/bin/sh` shebang and a positive
  control. Unsupported hook coverage reports an explicit skip. Other isolation
  assertions run independently without querying Git versions or shell support.
  Probe failures include interpreter, arguments, exit status, stdout and stderr;
  timeouts retain partial output. CI saves JUnit reports for failure investigation.
  Backend-warning policy tests run in-process. Legacy fixture builds allow backend warnings
  by category without depending on setuptools warning prose.
- Hypothesis profiles load before pytest resolves command-line options,
  including invocations with `.` or individual test files. The default profile
  runs 100 examples without deadlines; the deep profile runs 1,000 and both
  preserve inherited CI settings. Installed-wheel validation stages the shared
  configuration. Header generation is reproducible across string hash seeds,
  and rule assertions share a fixed date and handle advisory findings. Coherent
  generated metadata excludes whitespace-only text; production scoring is unchanged.

- Malformed dependency diagnostics identify the one-based `Requires-Dist`
  entry instead of echoing its contents or the parser error, preventing URL
  credentials and tokens from appearing in text and JSON reports.
- Typing markers require Python or stub sources in their directory or a
  descendant. Isolated markers in documentation and test directories no
  longer imply package typing. Inaccessible entries are skipped individually,
  and unrelated files do not incur stat probes. Virtual and Conda environments
  are identified by `pyvenv.cfg` files (including symlinks to regular files) and
  regular `conda-meta/history` files. Missing, looping,
  or directory-target venv symlinks,
  symlinked Conda history markers, and symlinked `conda-meta` directories do not
  prune sources. Permission failures and uncertain I/O errors on venv markers
  or their symlink targets conservatively prune environment contents.
  Build-output names
  are pruned at the scan root and nested project boundaries unless they contain
  direct Python or stub sources. Packages and namespaces named `build`, `dist`,
  `_build`, `env`, or `venv` elsewhere are retained. These remain source
  heuristics, not verification of wheel contents.
- README links and images now use CommonMark parsing through
  `markdown-it-py>=4.2.0`, including images inside links and block-aware
  reference definitions. Stray brackets and backticks no longer hide links
  in subsequent paragraphs. HTML destinations remain supported, excluding
  comments and literal code regions. Misnested literal tags preserve other
  open regions, and HTML-looking image alt text cannot change their state.
  Markdown and HTML URLs normalize consistently before deduplication; findings
  display readable Unicode while preserving reserved escapes, invalid UTF-8,
  and encoded Unicode controls, non-ASCII spaces, and line/paragraph separators.
  Zero-width joiners and non-joiners remain readable for emoji and script shaping,
  and every Unicode destination has an encoded URL suffix that identifies its spelling;
  other format characters, including soft hyphens and bidi controls, stay encoded.
  Findings JSON-quote paths, follow document order and deduplicate canonical URLs, including
  destinations that differ in percent-escape case or unreserved ASCII escaping
  even with invalid UTF-8. Reserved escapes and Unicode normalization forms
  remain distinct; readable spaces and valid Unicode are decoded even beside invalid bytes,
  without repeatedly classifying or decoding identical normalized targets.
  Duplicate spellings do not consume the five-item diagnostic limit.
- Source-test CI uploads coverage to Codecov with the configured token and a
  README badge. Upload failures are nonblocking; the 94% local coverage gate
  remains authoritative. External fork PRs retain coverage artifacts without
  authenticated Codecov uploads.
- Backend version floors remain strict: wildcard pins can admit prereleases
  below a required final release and therefore trigger an advisory. Explicit
  README content-type overrides remain supported.
- CLI credential regressions now exercise the package under test even when
  invoked outside its checkout, including a competing import path and a backend
  emitting malformed Core Metadata. Subprocess tests assert their working
  directory and preserve inherited import paths. README content-type regressions
  assert rule outcomes directly.
- Source distributions include `uv.lock` and exclude macOS Finder metadata,
  keeping manifest checks consistent with version-controlled files. Repository
  ignores also exclude `.DS_Store` for contributors without global ignores.

## 6.0.0 (2026-08-01)

- Result dataclasses `TestResult`, `Problem`, and `RatedProject` now require
  keyword arguments when constructed directly. `ratings.rate()` and
  `pyroma.run()` retain their existing calling conventions.
- Upgraded the build and test backend to `uv_build>=0.13.0,<0.14.0`.
  Manifest checks allow its generated `pyproject.toml.orig` file.
- Direct URL dependency diagnostics now contain sorted, unique names only,
  keeping credentials and tokens out of text and JSON output.
- Raised the existing `packaging` minimum to 26.3. Python classifier coherence
  now uses version-range intersection, including bounded patch intervals.
- Backend floors combine all applicable requirements across the project's
  supported Python range. Unknown markers and direct references are skipped
  unless unconditional constraints prove the floor; contradictory partitions
  are ignored. Flit 3.11 supports simple SPDX identifiers and license files,
  while supported compound or parenthesized expressions need 3.12. Unknown
  Flit feature requirements, including `WITH`, skip this advisory.
- Runtime dependency checks now retain markers active with the empty extra,
  including negative comparisons and mixed Boolean expressions.
- Typing checks recognize recursive namespace/custom source layouts and
  complete stub packages. Generated trees, environments, caches and symlinks
  are excluded. These are source facts, not wheel-content verification.
- README link checks conservatively extract inline/reference Markdown and
  HTML links outside code and comments without adding a Markdown dependency.
  Malformed or ambiguous constructs are skipped. Content-type checks honor
  explicit readme table overrides and compare them with emitted metadata;
  extension inference applies only to string declarations.
- Added an advisory tier. Checks are now either scored, deciding the 1-10
  rating as before, or advisory, reported below the rating but kept out of
  the arithmetic. A new check lands as advisory and graduates to scored,
  announced here, once it has been through a minor release without
  false-positive reports, so an upgrade never changes a rating unannounced.
  An advisory check is never fatal. `--strict` scores the advisory checks
  right away, `--no-advisories` hides them, and `--no-strict` and
  `--advisories` override the `[tool.pyroma]` settings from the command
  line. Advisory findings always appear in JSON output under an
  `advisories` key.
- Added two scored, fatal checks for conditions package indices reject
  outright and `twine check` does not catch: direct URL dependencies, and a
  Summary longer than 512 characters or spanning more than one line.
- Added fourteen advisory checks: Python classifiers contradicting
  `Requires-Python`; end-of-life Python versions; a Development Status
  classifier contradicting the version; a build backend floor too low for
  the PEP 639 licensing in use; `license-files` patterns matching no file; an
  emitted readme content type disagreeing with its declaration; source typing
  facts disagreeing with the `Typing :: Typed` classifier; capped, pinned and
  floorless runtime dependencies; an upper bound on `Requires-Python`;
  development dependencies published as extras rather than declared in
  `[dependency-groups]`; relative links in the description, which do not
  resolve on PyPI; colliding project URL labels; and a `[build-system]`
  table with no `build-backend`.
- Every check now has a category: `spec`, `index`, `metadata`, `coherence`,
  `practice` or `internal`. A whole category can be skipped at once, for
  example `--skip-tests practice`, and the category appears in JSON output.
- Added `[tool.pyroma]` configuration, read from the rated project's own
  `pyproject.toml` in directory mode. Supports `min-rating`, `strict`,
  `advisories` and `skip-tests`. An explicit command-line flag always wins,
  and every boolean setting has a flag in both directions; `--no-config`
  ignores the table entirely. Unknown names in `skip-tests` are rejected the
  same way `--skip-tests` rejects them, instead of silently skipping nothing.
- Corrected the `Licensing` message. It claimed that a `License-Expression`
  alongside license classifiers may be rejected by package indices; PyPI
  implements no such check. It is now worded as the opinionated
  recommendation it is.
- A deprecated classifier now names its replacement, instead of being
  reported as a non-standard classifier that should use the `Private ::`
  namespace.
- `pyproject.toml` is now parsed once, by `projectdata`, and handed to the
  checks, as are the `py.typed` and `license-files` lookups. The checks are
  a pure function of the metadata they are given; `CheckManifest`, which
  runs check-manifest against the working tree, is the one exception.
- Connection failures against a package index now name the exception class,
  for example `SSLError`, and log the original error at debug level. The URL
  stays out of both, since it may carry credentials; a Semgrep rule in CI
  keeps URL-bearing values out of user-facing exceptions.
- CI runs pyroma on itself with `--strict` as a non-blocking job, to surface
  what the advisory checks say before any of them graduate.
- Added a rules reference page documenting every check, its category and
  whether it is scored.

## 5.1b3 (unreleased)

- A project whose `pyproject.toml` cannot be parsed no longer crashes the
  CLI with a traceback and exit status 1. The failure is now reported like
  other fatal errors: a short message in text mode, a JSON error document
  with `--format json`, and exit status 3. A build backend that cannot be
  installed into the isolated build environment is treated like any other
  failing backend and rated accordingly instead of crashing.
- JSON reports and error documents again carry the checker's version under
  `_meta.pyroma`. The lookup used the upstream `pyroma` distribution name,
  which is not installed, so the key was silently omitted.
- Pyroma itself is now built by the native `uv_build` backend. Its flat
  `pyroma` module and source-distribution extras are declared directly in
  `pyproject.toml`; the Setuptools-only root configuration and `MANIFEST.in`
  are gone. Setuptools-based test fixtures remain for compatibility coverage.
- Hypothesis now fuzzes bounded metadata dictionaries and verifies that rating
  is deterministic, non-mutating and structurally valid. This found and fixed
  a crash on malformed single-component Python classifiers.
- The package now passes Ruff's full stable rule set. Ruff is pinned for
  reproducible CI, formatter-incompatible rules are explicitly disabled, and
  narrow per-file exceptions preserve the unittest suite, public CLI behavior,
  and compatibility APIs.
- CI now runs Pyroma on itself at a required 10/10, enforces Lizard's strict
  complexity threshold, and checks dependency declarations with deptry.
  GitHub releases build distributions in an unprivileged job and publish the
  resulting artifact to the `pyroma621` PyPI project through Trusted
  Publishing.
- Rating tests are now stateless: each test returns a `TestResult`
  instead of mutating shared test instances, and the new
  `ratings.rate_project()` returns a structured `RatedProject`
  with per-test problems. `ratings.rate()` keeps its old
  `(rating, messages)` return value.
- Added a `--format json` option for machine-readable output.
  Program output no longer goes through the root logger, and
  `--quiet` simply prints the rating.
- pyroma no longer depends on setuptools or distutils at runtime.
  pyproject.toml validation now uses `validate-pyproject`, and
  pyroma's own metadata lives in `pyproject.toml`.
- setup.cfg-only projects (no setup.py, no pyproject.toml) are now
  rated 1 on their build-system problems alone, since no standard tool
  can build them and no metadata can be extracted.
- New and stricter checks based on the packaging specifications:
  version numbers are validated with `packaging.version` (warning
  about non-canonical forms, epochs and local version segments),
  `Metadata-Version` must be a legal value, project names must match
  the packaging name format (fatal), `License-Expression` must be
  valid SPDX (fatal, as is combining it with `License`),
  `Description-Content-Type` and every `Requires-Dist` entry are
  validated, the pyproject.toml `[project]` table MUST-rules are
  enforced (fatal), and `Project-URL` labels are checked against the
  well-known labels (with the 32 character limit fatal).
- Official support for the `uv_build` backend, and backend-neutral
  advice when pyproject.toml is missing.
- PyPI requests now use timeouts and clear error messages, and
  downloaded sdists are always cleaned up.
- All code is now type-annotated and checked with ty and pyrefly; a
  `py.typed` marker ships with the package.
- Linting moved from black + flake8 to Ruff; the test suite runs with
  a coverage gate.
- Exit codes are now documented in the README.
- Bug fixes found by review: descriptions with a parameterized
  `Description-Content-Type` (for example `text/markdown; charset=UTF-8`) are no longer validated as ReStructuredText; the
  BusFactor test no longer passes for packages with zero owners;
  `--skip-tests` names are matched exactly instead of by substring,
  and trailing separators no longer reject the whole list; `.tb2`
  archives are recognized; tar archives are extracted with the "data"
  filter, which also protects against symlink attacks.
- More robust index handling: XML-RPC faults and network errors while
  fetching package roles are now warnings instead of crashes, a failed
  sdist download raises a clear error, and index data without a
  `releases` listing no longer crashes. Keys the PyPI JSON API
  synthesizes itself (`project_url`, `package_url`, `release_url`
  and friends) are no longer folded into the metadata, so they cannot
  clobber the real `Home-page` or hide a missing project URL.
- Rating a distribution file or PyPI package now keeps the unpacked
  sdist around for the duration of the run, so the pyproject.toml
  validation tests run in those modes too. The check-manifest test
  still requires a VCS checkout and is skipped for sdists.
- A configuration that skips every rated test now raises a
  `ConfigurationError`. The command line reports errors (bad
  configuration, package not found, network problems) cleanly on
  stderr — or as a JSON error document with `--format json` — and
  exits with the new exit code 3.
- Python 3.11 or later is now required; the `tomli` fallback
  dependency is gone.
- Ruff is pinned in one place (the `lint` dependency group) and run
  through uv both in CI and in pre-commit. mypy has been replaced by
  the ty and pyrefly type checkers, run the same way through the
  `typecheck` dependency group.

## 5.1b2 (2026-06-14)

- Added deprecation warnings for deprecated metadata.

## 5.1b1 (2026-06-14)

- Adds a rating that verifies pyproject.toml data with setuptools.
- Added helpful links to some error messages.
- Added support for checking packages on a custom PyPI-compatible index with
  the `--index-url` option.

## 5.0.1 (2025-12-09)

- check-manifest was missing from the requirements list.
- Passing in multiple tests to be skipped now actually works. \[Matt Norton\]

## 5.0 (2025-07-15)

- Re-releasing as final.

## 5.0b2 (2025-07-10)

- Fixed a bug that failed to find license-expression fields.

## 5.0b1 (2025-07-04)

- Changed the internal metadata names to match Core Metadata. This brings
  less confusion, especially since setuptools and PyPI aren't exactly the
  same either, so let's pick the offical standard and run with it.
- Added a rating for if the wheel fails to build. Unfortunately it's
  impossible to get a proper error out of it, so the message tells
  you to run python -m build.

## 5.0a1 (2025-07-02)

- Removed support for fetching data via monkey-patching setup.py.
- Added a warning if you have only a setup.cfg, something that is
  working with some tools, but is not officially supported.
- Some general cleanups and simplifications thanks to those changes.

## 4.3.3 (2025-07-03)

- Licensing specifications are less flexible than I thought.
  Updated to reflect that.

## 4.3.2 (2025-07-02)

- Added support for License-Expression as per PEP 639.

## 4.3.1 (2025-07-01)

- Deprecation warnings? For the packaging configuration? What was I thinking?
  I'm instead adding a rating telling people that they should add a
  pyproject.toml. Much nicer.

## 4.3 (2025-07-01)

- Added a --skip-tests parameter to allow skipping of certain tests.
  You shouldn't skip tests, of course, but now you can. This is actually
  only implemented so that I can add a test using check-manifest and
  skip it when run from the zest.releaser hook.
- If you also have check-manifest installed, pyroma will run that as a test.
  No longer do you need to call it separately! However, if Pyroma is invoked
  from zest.releaser, it will not be run, because check-manifest has a separate
  hook for zest.releaser, so that would run it twice.
- Allow private classifiers \[hugovk\]
- Using tox to run tests \[hugovk\]
- Deperating the support for setup.py

## 4.2 (2023-02-25)

- Fall back to installing project's build backend in an isolated environment
  if a compatible version isn't installed in the current env \[CAM-Gerlach\]
- Fix metadata extraction failure when project `long_description` is included
  as a header rather than a payload in the `METADTA` file \[CAM-Gerlach\]
- Add a fallback to restore compatibility with Setuptools \<61 \[CAM-Gerlach\]
- Fix tests failing due to invalid versions on Setuptools \>=66 \[CAM-Gerlach\]
- Add `python_requires`, update classifiers, add implicit dependencies
  and remove unused deps in Pyroma's own packaging metadata \[CAM-Gerlach\]

## 4.1 (2022-11-24)

- Moved from a custom trove classifiers list to using the trove-classifiers
  package \[hugovk\]
- Support checking the Requires-Python metadata \[davidandreoletti\]
- Silence noisy build backend output when building metadata
  \[CAM-Gerlach, wesleybl\]
- Official support for Python 3.11 \[hugovk, radarhere\]
- Some string formatting bugs \[hugovk, CAM-Gerlach\]
- Now follows black and flake8 rules
- Check if author_email field contains author name \[bessman\]

## 4.0 (2022-04-14)

- No changes from 4.0b2.

## 4.0b2 (2022-03-29)

- The deprecation test for test_suite is no longer needed, as the patched
  setuptools method of gathering metadata as a whole is deprecated.
- `description_content_type` also needs a mapping in the metadata map.

## 4.0b1 (2022-03-28)

- Added support for PEP517 by using `build` to build the metadata as a
  preferred build method over the old patched setuptools method.
  The old way is depracated and will be removed in 5.0, which also is
  planned to support PEP621.
  Thanks to Christopher A.M. Gerlach for valuable insight into the
  modern packaging systems.

## 3.3 (2022-03-28)

- Add a deprecation for `test_suite`

## 3.2.1 (2022-03-27)

- Fixed \_\_getattr\_\_-related crash in tests with python3.7 \[Robert T. McGibbon\]

## 3.2 (2021-06-22)

- Added support for pre-commit \[CAM Gerlach\]
- Rewrote parts of the PyPI support, to avoid using the xmlrpc API,
  since it's being heavily rate-limited at the moment. Only one call
  is using it now, which seems to work fine.
- Backwards incompatible change: As a result of the above change, when
  looking for packages on PyPI, the package name must now be spelled
  exactly correct, including capitalization.
- Some more code cleanup/modernization \[CAM Gerlach\]
- Added --quiet option to output only the rating \[Hugo van Kemenade\]
- Pyroma is now an executable module, and can be called with `python -m pyroma` \[RuRo\]

## 3.1 (2021-03-06)

- Added correct detection of setup.py encoding
- Code cleanup \[CAM Gerlach\]

## 3.0.1 (2021-03-02)

- Drop support for Python 2 \[Florian Bruhin\]
- Add back official support for Python 3.6, I didn't realize it was still officially supported

## 2.6.1 (2021-02-16)

- Update to the current list of Trove classifiers.
- Officially support Python 3.8 and 3.9
- Drop official support for Python 3.5 and 3.6 (still works though).

## 2.6 (2019-11-02)

- Ran flake8 and black on the code, better formatting for
  classifiers.py generation.
- Support for PEP 517, setup.cfg-only packages. \[Max Tyulin\]
- Adds support for Markdown long_descriptions, if you set
  long_description_content_type to 'text/markdown'
- Installs Pygments to avoid an error message of you have
  syntax highlighted code in the long_description.

## 2.5 (2019-06-01)

- Fixed \#35: Correctly restore `sys.argv`. \[maurits\]
- Added back the BusFactor test, the XMLRPC API supports it.
- \#26: Adding a test to check for a development status classifier.
- \#21: Accept the new project_urls distribution data.
- Now verifies that classifiers are valid, and that the license field
  and license classifiers match.
- The rating now again reflects if the long_description isn't valid ReST.
- \#38: Recommendations for the License field has changed.

## 2.4 (2018-08-15)

- Get rid of the tests that rely on HTML scraping, it's too brittle.
- Update to use pypi.org instead of pypi.python.org
  \[Andreas Lutro, Lennart Regebro\]
- Added -n command line option to set the minimum rating needed for pyroma
  to return success, useful in scripts.
  \[Scott Colby\]

## 2.3.1 (2018-05-28)

- Fixed \#12: Installation fails with a non-UTF8 locale under Python 3.
  \[ericof, 4383, regebro\]

## 2.3 (2017-11-28)

- Check that a classifier specifies the project license. \[4383\]
- Dropped support for Python 2.6, 3.3 and 3.4.
- Fixed a unicode issue \[gotcha\]
- Stopped recommending places to keep documentation.

## 2.2 (2016-10-26)

- Removed the TestSuite rating. I decided it was too close to looking at
  code quality. Pyroma no longer cares if you have tests or not.
- Fixed \#36: PyPI now requires https.

## 2.1 (2016-10-18)

- \#35: Support for PEP-440.

## 2.0.2 (2016-03-06)

- Faked the \_\_name\_\_ variable to allow you to have a "if \_\_name\_\_" construct
  in the setup.py.

## 2.0.1 (2016-03-06)

- Fixed a bug under Python 3 with certain imports.

## 2.0.0 (2016-02-28)

- Stable release.

## 2.0.0b2 (2015-11-09)

- Made it run under Python 3 again.
- PEP8

## 2.0.0b1 (2015-11-08)

- Big rewrite of how data is extracted from Distutils/Setuptools.

## 1.8.3 (2015-11-08)

- Issue \#26: Checking a PyPI package could fail under Python 3.

## 1.8.2 (2015-06-14)

- Do not complain that the version number should be a string, when it
  is a basestring.
  \[maurits\]

## 1.8.1 (2015-04-27)

- This is what happens when you don't run the tests after merging.

## 1.8 (2015-04-27)

- More robust rating. \[Jeff Quast\]
- Closed \#24.

## 1.7 (2014-10-19)

- Package name lookup is now case insensitive. \[Dmitry Vakhrushev\]
- Fixed yet another error in return value. \[Dmitry Vakhrushev\]

## 1.6 (2014-04-17)

- Fixed issue \#17: Integration with zest.releaser stopped working.
- Fixed issue \#18: Pyroma returns the rating as an exit code, this was a
  mistake. It now returns 0 on success, 1 on incorrect options and 2 if
  the rating is below 8.
- Fixed issue \#19: Implementing a custom test class counts as having tests.
- 8: Philadelphia is now considered a "success" based on practical experience.

## 1.5 (2013-10-18)

- Fixed issue \#13: Pyroma would fail if there was no description.
- Dropping support for Python 3.1. It still works, but it is unsupported.
- Added support for command line options, implementing \#14 and \#15.

## 1.4 (2013-05-29)

- Issue \#13: Pyroma would fail when checking a package name if no source
  distribution could be found.
- Added a check that the package has a source distribution on PyPI.

## 1.3.1 (2013-05-29)

- Issue \#11: pyroma would fail if long_description was a non-string.
- zest.releaser now only runs Pyroma on Python packages.
- Because packages that use external test-runners can not get more
  that 9/10, this value is now also seen as acceptable when running
  Pyroma through zest.releaser.

## 1.3 (2013-03-15)

- Added a test to make sure the version number is a string.
- Made sure errors were printed also when a fatal error was encountered.
- Better log messages.
- The zest.releaser hook is now done before tagging, as it's more useful there.
  Especially in conjunction with check-manifest.
- Having no long_description no longer causes pyroma to fail.

## 1.2 (2013-03-06)

- Removed the running of tests. I always found it a bit iffy, and getting
  rid of it solved a lot of issues. Pyroma is now focused solely on
  packaging quality.
- A package on PyPI with several versions will no longer return an error.
- Now looks for documentation on pythonhosted.org or readthedocs.org.
- Adds a hook for zest.releaser, so it can be run after doing the checkout,
  before uploading to PyPI.

## 1.1 (2013-03-05)

- Better handling if there is no package data, for example if setup.py doesn't
  call setup() unless you run it as the "\_\_main\_\_" script.
- If setup.py doesn't call setup() look for a main() script and try to run it.

## 1.0 (2013-03-05)

- Support for Python 3.3
- Added test for PEP 386 compliance.

## 0.9.3 (2011-03-17)

- It's now using a ProxyStub for the PyPI xmlrpclib during tests.
- Removed the Dependency rating.
- Added a rating that runs tests, to see if they run. This will also take
  care of checking for dependencies.

## 0.9.2 (2011-03-13)

- Commented out the dependency test, it was too unreliable.
- Fixed the ReST.
- Python 3 support.

## 0.9.1 (2011-03-08)

- Initial release
