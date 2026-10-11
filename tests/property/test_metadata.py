"""Metadata boundaries and single-field defects on otherwise coherent inputs."""

import copy
import datetime
from unittest.mock import patch

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pyroma import ratings
from pyroma.metadata import Metadata

from .strategies import FIXED_TODAY, WORDS, frozen_rate, valid_metadata


def assert_rule(
    data: Metadata, rule: ratings.BaseTest, *, passed: bool, fatal: bool = False, strict: bool = False
) -> None:
    original = copy.deepcopy(data)
    with patch.object(ratings, "_TODAY", FIXED_TODAY):
        result = rule.test(data)
        rated = ratings.rate_project(data, strict=strict)
    assert result.outcome is passed
    assert (result.fatal and result.outcome is False) is fatal
    finding = next(
        (problem for problem in [*rated.problems, *rated.advisories] if problem.test == type(rule).__name__), None
    )
    assert (finding is None) is passed
    if finding is not None:
        assert finding.fatal is fatal
    if fatal:
        assert rated.rating == 0
    assert data == original


@given(valid_metadata())
def test_coherent_metadata_has_no_findings(data: Metadata) -> None:
    original = copy.deepcopy(data)
    summary, description = data["summary"], data["description"]
    assert 11 <= len(summary) <= 80
    assert 101 <= len(description) <= 200
    assert summary == summary.strip()
    assert description == description.strip()
    assert data["_pyproject"]["project"]["description"] == summary
    assert data["_pyproject"]["project"]["readme"]["text"] == description
    for strict in (False, True):
        rated = frozen_rate(data, strict=strict)
        assert rated.rating == 10
        assert rated.problems == []
        assert rated.advisories == []
    assert data == original


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize(("minor", "passed"), [(9, False), (11, True)])
@given(data=valid_metadata())
def test_assert_rule_handles_advisories_and_freezes_both_evaluations(
    data: Metadata, minor: int, *, passed: bool, strict: bool
) -> None:
    data["classifier"] = [f"Programming Language :: Python :: 3.{minor}"]
    outside_today = datetime.date(2030, 1, 1)
    with patch.object(ratings, "_TODAY", outside_today):
        assert_rule(data, ratings.EndOfLifePython(), passed=passed, strict=strict)
        assert outside_today == ratings._TODAY


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("description", [" " * 101, "\n" * 101])
@given(data=valid_metadata())
def test_whitespace_only_text_preserves_current_scoring(data: Metadata, description: str, *, strict: bool) -> None:
    data["summary"] = " " * 11
    data["description"] = description
    project = data["_pyproject"]["project"]
    project["description"] = data["summary"]
    project["readme"]["text"] = description
    assert_rule(data, ratings.Summary(), passed=True, strict=strict)
    assert_rule(data, ratings.Description(), passed=True, strict=strict)
    rated = frozen_rate(data, strict=strict)
    assert rated.rating == 10
    assert rated.problems == []
    assert rated.advisories == []


@pytest.mark.parametrize("length", [9, 10, 11, 511, 512, 513])
@given(data=valid_metadata(), character=st.sampled_from(("x", "é", "界")))
def test_summary_boundaries(data: Metadata, character: str, length: int) -> None:
    data["summary"] = character * length
    assert_rule(data, ratings.Summary(), passed=length > 10)
    assert_rule(data, ratings.SummaryFormat(), passed=length <= 512, fatal=length > 512)


@pytest.mark.parametrize("length", [99, 100, 101])
@given(data=valid_metadata(), character=st.sampled_from(("x", "é", "界")))
def test_description_boundaries(data: Metadata, character: str, length: int) -> None:
    data["description"] = character * length
    assert_rule(data, ratings.Description(), passed=length > 100)


@pytest.mark.parametrize("length", [31, 32, 33])
@given(data=valid_metadata(), character=st.sampled_from(("x", "é", "界")))
def test_url_label_boundaries(data: Metadata, character: str, length: int) -> None:
    data["project-url"] = {"Homepage": "https://example.invalid", character * length: "https://example.invalid/docs"}
    assert_rule(data, ratings.Url(), passed=length <= 32, fatal=length > 32)


@pytest.mark.parametrize("newline", ["\r", "\n", "\r\n"])
@given(data=valid_metadata(), prefix=WORDS, suffix=WORDS)
def test_summary_line_breaks(data: Metadata, prefix: str, suffix: str, newline: str) -> None:
    data["summary"] = prefix + " description " + suffix
    assert_rule(data, ratings.SummaryFormat(), passed=True)
    data["summary"] = prefix + newline + suffix
    assert_rule(data, ratings.SummaryFormat(), passed=False, fatal=True)


@pytest.mark.parametrize("rule", [ratings.SummaryFormat(), ratings.PythonVersionCoherence()])
@given(data=valid_metadata())
def test_missing_optional_inputs_skip_rule(data: Metadata, rule: ratings.BaseTest) -> None:
    data.pop("summary" if isinstance(rule, ratings.SummaryFormat) else "requires-python")
    original = copy.deepcopy(data)
    assert rule.test(data).outcome is None
    assert data == original
