# pyroma

[![PyPI - Version](https://img.shields.io/pypi/v/pyroma621)](https://pypi.org/project/pyroma621/)
[![Lint](https://github.com/hbmartin/pyroma621/actions/workflows/lint.yml/badge.svg)](https://github.com/hbmartin/pyroma621/actions/workflows/lint.yml)
[![Codecov](https://codecov.io/gh/hbmartin/pyroma621/branch/master/graph/badge.svg)](https://app.codecov.io/gh/hbmartin/pyroma621)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![ty](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ty/main/assets/badge/v0.json)](https://github.com/astral-sh/ty)
[![CodeFactor](https://www.codefactor.io/repository/github/hbmartin/pyroma621/badge)](https://www.codefactor.io/repository/github/hbmartin/pyroma621)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/hbmartin/pyroma621)
[![twitter](https://img.shields.io/badge/@hmartin-00aced.svg?logo=twitter&logoColor=black)](https://twitter.com/hmartin)


Pyroma rhymes with aroma, and is a product aimed at giving a rating of how well
a Python project complies with the best practices of the Python packaging
ecosystem, primarily PyPI, pip, Distribute etc, as well as a list of issues that
could be improved.

The aim of this is both to help people make a project that is nice and usable,
but also to improve the quality of Python third-party software, making it easier
and more enjoyable to use the vast array of available modules for Python.

It's written so that there are a library with methods to call from Python, as
well as a script, also called pyroma.

The complete documentation, including the upgrade guide and changelog, is
published at [hbmartin.github.io/pyroma621](https://hbmartin.github.io/pyroma621/).

Install this distribution with:

```console
$ pip install pyroma621
```

It can be run on a project directory before making a release:

```console
$ pyroma .
```

On a distribution before uploading it to the CheeseShop:

```console
$ pyroma pyroma-1.0.tar.gz
```

Or you can give it a package name on CheeseShop:

```console
$ pyroma pyroma
```

If you use an internal PyPI-compatible package index, specify it with
`--index-url`:

```console
$ pyroma --index-url https://packages.example.com internal-package
```

Giving it a name on CheeseShop is the most extensive test, as it will
test for several things isn't otherwise tested.

Note that pyroma extracts metadata by asking the project's build
backend for it, which — like installing the package with pip — can
execute code shipped with the package. Be as careful about running
pyroma on an untrusted package as you would be about installing it.

The high-level `pyroma.run()` API cleans up extracted distributions
after rating. If you call `distributiondata.get_data()` or
`pypidata.get_data()` directly, call `distributiondata.cleanup(data)`
when you no longer need the returned metadata's `_path`.

In all cases the output is similar:

```text
------------------------------
Checking .
Found pyroma
------------------------------
The packages long_description is quite short.
------------------------------
Final rating: 9/10
Cottage Cheese
------------------------------
```

For machine-readable output, pass `--format json`:

```console
$ pyroma --format json .
```

The schema version 1 contract, JSON argument errors, and `--version` require
6.2 or newer and are available in development checkouts. They are unavailable
in 6.1.0. Install a development checkout from the repository root with
`python -m pip install .`. Check the installed version without rating a package
(the version below is illustrative):

```console
$ pyroma --version
pyroma621 6.2.0.dev0
```

Rating and error reports in 6.2 and later include
`_meta.schema_version`, the installed checker version, and the effective policy
when settings have been resolved. See the
[JSON contract and downloadable schemas](https://hbmartin.github.io/pyroma621/json/).
Invalid arguments also produce JSON when explicitly requested, for example
`pyroma --format json --min 11 .` reports `UsageError`.
Unambiguous long-option abbreviations such as `--form` are accepted.
Prefer full names in scripts because new options can make a prefix ambiguous.

## Exit codes

- `0`: The package rated equal to or above the `--min` rating (default 8).
- `2`: The package rated below the `--min` rating, or command-line arguments
  were invalid. With `--format json`, a low rating contains `rating`. In
  6.2 and later, an argument failure contains
  `error.type = "UsageError"` and no rating.
- `1`: Used by the `zest.releaser` integration when you choose to abort
  the release after a low rating.
- `3`: An error prevented rating the package: it could not be found or
  downloaded, its `pyproject.toml` could not be parsed, or the
  configuration (for example a `--skip-tests` value that skips every
  rated test) made rating impossible. With `--format json` the error is
  reported as a JSON document with an `error` key.

## Tests

Every check is documented, with its category and whether it is scored, in
the [rules reference](https://hbmartin.github.io/pyroma621/rules/).

Checks are either **scored** or **advisory**. Scored checks decide the
1&ndash;10 rating. Advisory checks are reported below the rating but stay out
of the arithmetic. A new check always lands as advisory and only graduates to
scored, announced in the changelog, after a release without false positives,
so an upgrade never changes your score unannounced. Run `pyroma --strict` to
score them now, or `--no-advisories` to hide them.

Settings can live in the rated project's `pyproject.toml`:

```toml
[tool.pyroma]
min-rating = 10
skip-tests = ["practice"]
```

Only directory mode reads `[tool.pyroma]` from the target project's
`pyproject.toml`. File and PyPI modes use CLI settings and defaults. Explicit
flags override configuration; `--no-config` disables discovery. JSON reports
record whether configuration was loaded and its source. `--no-advisories`
hides findings in text output; JSON always retains them.

This is the list of checks that are currently performed:

- The package should have a name, a version and a Summary.
  If it does not, it will receive a rating of 0.
- The name must follow the project name format from the packaging
  specifications; package indices reject invalid names, so an invalid
  name is fatal.
- The version number should be a string that complies with the version
  specifiers specification (PEP 440). Non-canonical forms, version
  epochs and local version segments are warned about.
- The `Metadata-Version` must be a legal value.
- The Summary should be over 10 characters, and the Description
  should be over a 100 characters.
- If your Description is ReStructuredText (the default), pyroma will
  convert it to HTML using Docutils, to verify that it is possible.
  This guarantees pretty formatting of your description on PyPI.
- The `Description-Content-Type`, if given, must be a legal
  type/charset/variant combination.
- You should have the following meta data fields filled in:
  classifiers, keywords, author, author_email and project URLs.
- You should have classifiers specifying the supported Python versions
  and the development status.
- You should have `requires-python`/`python_requires`
  specifying the Python versions you support.
- You should specify your license with the `License-Expression`
  field. It must be a valid SPDX license expression; an invalid one, or
  combining it with the deprecated `License` field, is fatal since
  package indices reject such uploads.
- Every `Requires-Dist` entry must be a valid dependency specifier;
  legacy parenthesized version specifiers and ordered comparisons on
  non-version environment markers are warned about.
- No `Requires-Dist` entry may be a direct URL reference
  (`name @ https://…`); package indices reject them, so this is fatal.
- The Summary must be a single line of at most 512 characters; package
  indices reject longer or multi-line summaries, so this is fatal.
- Your project should have a `pyproject.toml` declaring your build
  backend (any PEP 517 backend works: setuptools, flit, hatchling,
  uv_build, etc.). The file is validated against the pyproject.toml
  specification, including the `[project]` table rules (static name,
  static-or-dynamic version, readme/license exclusivity, no
  `console_scripts`/`gui_scripts` entry-point groups).
- Your `Project-URL` labels should include well-known labels such as
  Homepage, Source, Documentation, Changelog or Issues; labels over 32
  characters are fatal since package indices reject them.
- Deprecated metadata fields (`Home-page`, `Download-URL`,
  `Requires`, `Provides`, `Obsoletes`, `License`) are warned
  about when your metadata version deprecates them.
- If you are checking on a PyPI package, and not a local directory or
  local package, pyroma will check the number of owners the package has
  on PyPI. It should be three or more, to minimize the "Bus factor",
  the risk of the index owners suddenly going off-line for whatever reason.
- If you are checking on a PyPI package, and not a local directory or
  local package, pyroma will check that you have uploaded a source
  distribution, and not just binary distributions.

The following are reported as advisory findings, and do not affect the
rating unless you run with `--strict`:

- Python classifiers that contradict `Requires-Python`, or that claim
  support for end-of-life Python versions.
- A Development Status classifier that contradicts the version number.
- A build backend floor too low for the PEP 639 licensing the project uses.
- `license-files` patterns matching no file, and a readme whose content type
  does not match its extension.
- A `py.typed` marker and the `Typing :: Typed` classifier disagreeing.
- Capped, pinned or floorless runtime dependencies, and an upper bound on
  `Requires-Python`.
- Development dependencies published as extras rather than declared in
  `[dependency-groups]`.
- Relative links and images in the description, which do not resolve on PyPI.
- Project URL labels that collapse into one link once normalized, and a
  `[build-system]` table with no `build-backend`.

## Version control integration

With [pre-commit](https://pre-commit.com), pyroma can be run whenever you
commit your work by adding the following to your `.pre-commit-config.yaml`:

<!-- pyroma-pre-commit -->
```yaml
repos:
  - repo: https://github.com/hbmartin/pyroma621
    rev: "6.1.0"
    hooks:
      - id: pyroma
```

## Credits

The project was created by Lennart Regebro, <regebro@gmail.com>

The name "Pyroma" was coined by Wichert Akkerman, <wichert@wiggy.net>

This PEP621 fork maintained by Harold Martin

[![Profile images of all the contributors](https://contrib.rocks/image?repo=hbmartin/pyroma621)](https://github.com/hbmartin/pyroma621/graphs/contributors)
