root_dir := $(shell dirname $(realpath $(lastword $(MAKEFILE_LIST))))
bin_dir := $(root_dir)/.venv/bin

define PRINT_HELP_PYSCRIPT
import re, sys

for line in sys.stdin:
	match = re.match(r'^([a-zA-Z_-]+):.*?## (.*)$$', line)
	if match:
		target, help = match.groups()
		print("%-20s %s" % (target, help))
endef
export PRINT_HELP_PYSCRIPT

all: tests

.PHONY: all devenv tests docs garuff

# Keep the experimental checker isolated from the project's Python 3.11+ tools.
# Pin its source until a stable ruleset is available; CI uses this same target.
garuff: ## run the experimental garuff checker (blocking in CI)
	uvx --python 3.14 --from 'git+https://github.com/gahjelle/garuff.git@7d126c00f13b4d94f39f1107934089241ab7a6a1' garuff check pyroma/*.py docs README.md

devenv:
	uv sync --locked --no-default-groups --group test

help: ## display this message
	@python -c "$$PRINT_HELP_PYSCRIPT" < $(MAKEFILE_LIST)

generate: ## generate environment for tests
	cd pyroma/testdata/complete;$(bin_dir)/python setup.py sdist --formats=bztar,gztar,tar,zip
	cp pyroma/testdata/complete/dist/complete-1.0.dev1.* pyroma/testdata/distributions/

tests: devenv ## run tests
	uv run --locked --no-default-groups --group test tox -e py

clean: clean-pyc ## remove all

clean-pyc: ## remove Python file artifacts
	find . -name '*.pyc' -exec rm -f {} +
	find . -name '*.pyo' -exec rm -f {} +
	find . -name '*~' -exec rm -f {} +
	find . -name '__pycache__' -exec rm -fr {} +
	find . -name 'pip-selfcheck.json' -exec rm -fr {} +
	find . -name 'pyvenv.cfg' -exec rm -fr {} +
