"""Configure shared report validators and Hypothesis profiles."""

import json
from collections.abc import Callable
from importlib.resources import files

import pytest
from hypothesis import settings
from jsonschema import Draft202012Validator

settings.register_profile("property", max_examples=100, deadline=None)
settings.register_profile("property-deep", parent=settings.get_profile("property"), max_examples=1000)
settings.load_profile("property")


@pytest.fixture(scope="session")
def validator() -> Callable[[str], Draft202012Validator]:
    """Load and check each installed schema once per test session."""
    validators = {}
    for kind in ("rating", "error"):
        schema = json.loads(files("pyroma").joinpath("schemas", "v1", f"{kind}.json").read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        validators[kind] = Draft202012Validator(schema)
    return validators.__getitem__
