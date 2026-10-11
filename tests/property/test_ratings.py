"""Public rating behavior across options and interleaved projects."""

import copy
from unittest.mock import patch

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pyroma import ratings
from pyroma.metadata import Metadata
from pyroma.rules import categories

from .strategies import FIXED_TODAY, frozen_rate, rating_metadata, rating_outcome, valid_metadata

NAMES = tuple(test.__class__.__name__ for test in ratings.ALL_TESTS)
CATEGORIES = tuple(categories())
SKIPS = st.lists(st.sampled_from((*NAMES, *CATEGORIES)), max_size=4)


@given(data=rating_metadata(), skip=SKIPS, strict=st.booleans())
def test_skip_category_expansion_order_and_duplicates(data: Metadata, skip: list[str], strict: bool) -> None:
    original = copy.deepcopy(data)
    expanded = [
        name
        for selection in skip
        for name in (
            [type(test).__name__ for test in ratings.ALL_TESTS if test.category == selection]
            if selection in CATEGORIES
            else [selection]
        )
    ]
    expected = rating_outcome(data, skip, strict=strict)
    assert rating_outcome(data, expanded, strict=strict) == expected
    assert rating_outcome(data, list(reversed(skip)), strict=strict) == expected
    assert rating_outcome(data, skip + skip, strict=strict) == expected
    if isinstance(expected, ratings.RatedProject):
        excluded = set(expanded)
        assert all(problem.test not in excluded for problem in [*expected.problems, *expected.advisories])
    assert data == original


@given(data=rating_metadata(), selection=st.sampled_from((*NAMES, *CATEGORIES)), strict=st.booleans())
def test_single_string_skip_matches_singleton_list(data: Metadata, selection: str, strict: bool) -> None:
    assert rating_outcome(data, selection, strict=strict) == rating_outcome(data, [selection], strict=strict)


@given(rating_metadata())
def test_strict_promotes_findings_and_legacy_api_matches_default(data: Metadata) -> None:
    original = copy.deepcopy(data)
    default = frozen_rate(data)
    strict = frozen_rate(data, strict=True)
    assert strict.advisories == []
    assert {problem.test: problem for problem in strict.problems} == {
        problem.test: problem for problem in [*default.problems, *default.advisories]
    }
    # Passing advisory checks also enter the arithmetic: no score monotonicity assertion.
    with patch.object(ratings, "_TODAY", FIXED_TODAY):
        legacy = ratings.rate(data)
    assert legacy == (default.rating, [problem.message for problem in default.problems])
    assert data == original


@given(first=rating_metadata(), second=rating_metadata(), skip=SKIPS, strict=st.booleans())
def test_interleaved_projects_and_options_do_not_leak_state(
    first: Metadata, second: Metadata, skip: list[str], strict: bool
) -> None:
    originals = copy.deepcopy((first, second))
    expected = rating_outcome(first, skip, strict=strict)
    rating_outcome(second, list(reversed(skip)), strict=not strict)
    frozen_rate(first, strict=not strict)
    assert rating_outcome(first, skip, strict=strict) == expected
    assert (first, second) == originals


@pytest.mark.parametrize("field", ["name", "summary", "license-expression"])
@given(data=valid_metadata())
def test_fatal_defects_always_zero_the_rating(data: Metadata, field: str) -> None:
    bad_values = {"name": "invalid name", "summary": "two\nlines", "license-expression": "not-a-license"}
    data[field] = bad_values[field]
    for strict in (False, True):
        rated = frozen_rate(data, strict=strict)
        assert rated.rating == 0
        assert any(problem.fatal for problem in rated.problems)


@given(data=valid_metadata(), strict=st.booleans())
def test_no_scoring_contribution_is_a_configuration_error(data: Metadata, strict: bool) -> None:
    selections = [list(NAMES), list(CATEGORIES)]
    if not strict:
        selections.append([type(test).__name__ for test in ratings.ALL_TESTS if not test.advisory])
    for skip in selections:
        with pytest.raises(ratings.ConfigurationError, match="no rating can be calculated"):
            frozen_rate(data, skip, strict=strict)
