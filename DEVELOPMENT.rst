Testing
=======

Run tests:

    $ python -m unittest pyroma.tests

Some notes on developing
========================

*Note: Since pyroma no longer runs tests, this is probably no longer true.*

For each Python version supported by Pyroma you need to make sure the
"complete" package that is used for testing also supports that version of
Python. The complete data supports both Python 2 and Python 3 and depends on
the "six" package. As such it's highly unlikely you'll have to change any
code. However, you have to mark the package as supporting the Python
version.

This is most easily done by searching the code for "Python :: 3.2" and
adding the Python version that you want to support to the lists of
Python versions that appear in several places in this package.

Otherwise Pyroma will not run the complete-packages tests with your Python
version, and you'll get errors when running Pyroma's test-suite.

You also have to make new test-distributions with the updated data.
You do it this way:

    $ cd pyroma/testdata/complete
    $ python setup.py sdist --formats=bztar,gztar,tar,zip
    $ cp dist/complete-1.0.dev1.* ../distributions/

Future ideas
------------

Two improvements were considered but deliberately deferred:

* A static fast-path that reads a fully-static ``[project]`` table from
  ``pyproject.toml`` with ``tomllib`` to skip the wheel metadata build.
  It is only a speed optimization: it cannot handle ``dynamic`` fields
  or setup.py-only projects, so the ``build``-based path has to remain
  as the fallback.

* Capturing the build backend subprocess's stdout/stderr by passing a
  custom ``runner`` to ``build.util.project_wheel_metadata`` (the
  default ``quiet_subprocess_runner`` discards it), so that backend
  deprecation warnings can be surfaced in a report section.
