pyroma
======

Pyroma rhymes with aroma, and is a product aimed at giving a rating of how well
a Python project complies with the best practices of the Python packaging
ecosystem, primarily PyPI, pip, Distribute etc, as well as a list of issues that
could be improved.

The aim of this is both to help people make a project that is nice and usable,
but also to improve the quality of Python third-party software, making it easier
and more enjoyable to use the vast array of available modules for Python.

It's written so that there are a library with methods to call from Python, as
well as a script, also called pyroma.

It can be run on a project directory before making a release:

    $ pyroma .

On a distribution before uploading it to the CheeseShop:

    $ pyroma pyroma-1.0.tar.gz

Or you can give it a package name on CheeseShop:

    $ pyroma pyroma

If you use an internal PyPI-compatible package index, specify it with
``--index-url``:

  $ pyroma --index-url https://packages.example.com internal-package

Giving it a name on CheeseShop is the most extensive test, as it will
test for several things isn't otherwise tested.

Pyroma extracts the metadata by asking the project's own PEP 517 build
backend for it, so every standards-compliant build backend is supported:
setuptools, hatchling, flit-core, uv_build, poetry-core and so on.

In all cases the output is similar::

    ------------------------------
    Checking .
    Found pyroma
    ------------------------------
    The packages long_description is quite short.
    ------------------------------
    Final rating: 9/10
    Cottage Cheese
    ------------------------------

For machine consumption, for example in CI pipelines, ``--format json``
outputs the result as a single JSON document instead::

    $ pyroma --format json .
    {
      "checked": ".",
      "name": "pyroma",
      "rating": 9,
      "max_rating": 10,
      "level": "Cottage Cheese",
      "problems": [
        {
          "test": "Description",
          "message": "The package's Description is quite short.",
          "weight": 50,
          "fatal": false
        }
      ]
    }


Exit codes
----------

Pyroma communicates the result through its exit code, so it can be used
as a quality gate in CI:

* ``0`` — the rating was equal to or higher than the minimum rating,
  which is set with ``-n``/``--min`` and defaults to 8.
* ``2`` — the rating was below the minimum, or the command line was
  invalid (the standard library's argument parser also exits with 2 on
  usage errors).


Tests
-----

This is the list of checks that are currently performed:

* The package should have a name, a version and a Summary.
  If it does not, it will receive a rating of 0.

* The name must follow the project name format specification; an invalid
  name is fatal, as package indices will reject it.

* The version number should be a string. A floating point number will
  work with distutils, but most other tools will fail.

* The version number must comply with the version specifiers
  specification (PEP 440); an unparseable version is fatal. Versions
  that are valid but not in canonical normalized form, versions with
  local version segments, and version epochs are warned about.

* The Metadata-Version must be one that actually exists.

* The Description-Content-Type, if given, must be one of text/plain,
  text/x-rst or text/markdown, with a UTF-8 charset, and for Markdown
  a GFM or CommonMark variant.

* Licensing must be unambiguous: the modern License-Expression field
  should be used, it must be a valid SPDX license expression (an invalid
  one is fatal, as PyPI rejects it), and it must not be combined with the
  deprecated License field (also fatal) or license classifiers.

* Dependency specifiers (Requires-Dist) must be valid according to the
  dependency specifiers specification (PEP 508).

* Project URLs should use well-known labels (Homepage, Source,
  Documentation, Issues, Changelog, ...) no longer than 32 characters.

* The ``Metadata-Version`` must be a legal value.

* The Summary should be over 10 characters, and the Description
  should be over a 100 characters.

* If your Description is ReStructuredText (the default), pyroma will
  convert it to HTML using Docutils, to verify that it is possible.
  This guarantees pretty formatting of your description on PyPI.

* The ``Description-Content-Type``, if given, must be a legal
  type/charset/variant combination.

* You should have the following meta data fields filled in:
  classifiers, keywords, author, author_email and project URLs.

* You should have classifiers specifying the supported Python versions
  and the development status.

* You should have ``requires-python``/``python_requires``
  specifying the Python versions you support.

* You should specify your license with the ``License-Expression``
  field. It must be a valid SPDX license expression; an invalid one, or
  combining it with the deprecated ``License`` field, is fatal since
  package indices reject such uploads.

* Every ``Requires-Dist`` entry must be a valid dependency specifier;
  legacy parenthesized version specifiers and ordered comparisons on
  non-version environment markers are warned about.

* Your project should have a ``pyproject.toml`` declaring your build
  backend (any PEP 517 backend works: setuptools, flit, hatchling,
  uv_build, etc.). The file is validated against the pyproject.toml
  specification, including the ``[project]`` table rules (static name,
  static-or-dynamic version, readme/license exclusivity, no
  ``console_scripts``/``gui_scripts`` entry-point groups).

* Your ``Project-URL`` labels should include well-known labels such as
  Homepage, Source, Documentation, Changelog or Issues; labels over 32
  characters are fatal since package indices reject them.

* Deprecated metadata fields (``Home-page``, ``Download-URL``,
  ``Requires``, ``Provides``, ``Obsoletes``, ``License``) are warned
  about when your metadata version deprecates them.

* If you are checking on a PyPI package, and not a local directory or
  local package, pyroma will check the number of owners the package has
  on PyPI. It should be three or more, to minimize the "Bus factor",
  the risk of the index owners suddenly going off-line for whatever reason.

* If you are checking on a PyPI package, and not a local directory or
  local package, pyroma will check that you have uploaded a source
  distribution, and not just binary distributions.


Version control integration
---------------------------

With `pre-commit <https://pre-commit.com>`_, pyroma can be run whenever you
commit your work by adding the following to your ``.pre-commit-config.yaml``:

.. code-block:: yaml

    repos:
    -   repo: https://github.com/regebro/pyroma
        rev: "3.2"
        hooks:
        -   id: pyroma


Credits
-------

The project was created by Lennart Regebro, regebro@gmail.com

The name "Pyroma" was coined by Wichert Akkerman, wichert@wiggy.net

Contributors:

  * David Andreoletti
  * Godefroid Chapelle
  * Dmitry Vakhrushev
  * Hugo van Kemenade
  * Jeff Quast
  * Maurits van Rees
  * Hervé Beraud
  * Érico Andrei
  * Jakub Wilk
  * Andreas Lutro
  * Scott Colby
  * Andrew Murray
  * Nikita Sobolev
  * Charles Tapley Hoyt
  * Max Tyulin
  * Michael Howitz
  * Florian Bruhin
  * Christopher A.M. Gerlach
  * RuRo
  * Wesley Barroso Lopes
  * Alexander Bessman
  * Matt Norton
