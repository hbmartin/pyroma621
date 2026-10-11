"""Generated email and index inputs with all external providers mocked."""

import copy
import json
import os
import subprocess
import sys
from email.message import Message
from pathlib import Path
from unittest.mock import patch

import pytest
from hypothesis import given
from hypothesis import strategies as st
from packaging.utils import canonicalize_name

from pyroma import projectdata, pypidata
from pyroma.metadata import Metadata, normalize

from .strategies import TEXT, WORDS


@st.composite
def header_cases(draw: st.DrawFn) -> tuple[Message, dict[str, object]]:
    fields = {"Name": ["package-" + draw(WORDS)], "Version": ["1.0"]}
    for field in draw(
        st.lists(
            st.sampled_from(("Author-Email", "Classifier", "Requires-Dist", "License-File")), max_size=4, unique=True
        )
    ):
        fields[field] = draw(st.lists(TEXT.map(lambda text: "value-" + text[:1018]), min_size=1, max_size=4))
    variant = draw(st.sampled_from(("lower", "upper", "underscore", "dot")))
    message = Message()
    expected = {}
    for field, values in fields.items():
        raw = field.lower() if variant == "lower" else field.upper()
        if variant in {"underscore", "dot"}:
            raw = raw.replace("-", "_" if variant == "underscore" else ".")
        for value in values:
            message[raw] = value
        key = field.lower()
        expected[key] = values if field == "Classifier" or len(values) > 1 else values[0]
    return message, expected


def test_header_choices_are_reproducible_across_hash_seeds() -> None:
    source = """
import json
from hypothesis import given, settings, seed, Phase
from tests.property.test_ingestion import header_cases

snapshots = []
@seed(1234)
@settings(max_examples=25, deadline=None, database=None, phases=[Phase.generate])
@given(header_cases())
def probe(case):
    message, expected = case
    snapshots.append((list(message.raw_items()), expected))

probe()
print(json.dumps(snapshots, sort_keys=True))
"""
    snapshots = []
    for hash_seed in ("1", "2"):
        result = subprocess.run(  # noqa: S603 - fixed Python probe with an explicit hash seed
            [sys.executable, "-c", source],
            cwd=Path(__file__).resolve().parents[2],
            env={**os.environ, "PYTHONHASHSEED": hash_seed},
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        snapshots.append(json.loads(result.stdout))
    assert len(snapshots[0]) == 25
    assert any("classifier" in expected for _headers, expected in snapshots[0])
    assert snapshots[0] == snapshots[1]


def extract(message: Message) -> Metadata:
    with patch.object(projectdata, "wheel_metadata", return_value=message) as provider:
        result = projectdata.build_metadata("unused-property-path")
    provider.assert_called_once_with("unused-property-path", None)
    return result


def index_metadata(info: dict[str, object]) -> Metadata:
    with (
        patch.object(pypidata, "_get_project_data", return_value={"info": info}) as provider,
        patch.object(pypidata.xmlrpc.client, "ServerProxy") as server,
        patch.object(pypidata, "_http_get", side_effect=AssertionError("Unexpected network request")),
    ):
        server.return_value.__enter__.return_value.package_roles.return_value = [
            ("Owner", "owner"),
            ("Maintainer", "maintainer"),
        ]
        result = pypidata.get_data("example")
    provider.assert_called_once_with("example", index_url=None)
    assert result["_owners"] == ["owner"]
    return result


@given(header_cases())
def test_header_spellings_repeated_values_and_order(case: tuple[Message, dict[str, object]]) -> None:
    message, expected = case
    original = list(message.raw_items()), message.get_payload()
    assert extract(message) == expected
    # Reordering distinct fields leaves each field's values in their original order.
    reordered = Message()
    for field in reversed(list(dict.fromkeys(message.keys()))):
        for value in message.get_all(field):
            reordered[field] = value
    assert extract(reordered) == expected
    assert (list(message.raw_items()), message.get_payload()) == original


@given(first=WORDS, second=WORDS, separator=st.sampled_from(("-", "_", ".", "-_..")))
def test_name_normalization_is_idempotent_and_matches_packaging(first: str, second: str, separator: str) -> None:
    raw = first.upper() + separator + second.upper()
    normalized = normalize(raw)
    assert normalized == canonicalize_name(raw)
    assert normalize(normalized) == normalized


@pytest.mark.parametrize("field", ["Summary", "Classifier", "License-File", "Author-Email"])
@given(padding=st.sampled_from(("", " ", "  ")))
def test_singleton_unknown_is_omitted(field: str, padding: str) -> None:
    message = Message()
    message[field] = padding + "UNKNOWN" + padding
    assert extract(message) == {}
    message[field] = "real value"
    assert extract(message)[field.lower()] == [padding + "UNKNOWN" + padding, "real value"]


@pytest.mark.parametrize("has_header", [False, True])
@given(payload=TEXT, header=WORDS)
def test_description_header_precedence_and_payload_newline(payload: str, header: str, has_header: bool) -> None:
    message = Message()
    message.set_payload("\n\n" + payload + "\n\n")
    if has_header:
        message["Description"] = header
    result = extract(message)
    expected = header if has_header else (payload.strip() + "\n" if payload.strip() else None)
    assert result.get("description") == expected


@given(name=WORDS, classifiers=st.lists(WORDS, min_size=1, max_size=4), separator=st.sampled_from(("_", "-", ".")))
def test_pypi_aliases_and_synthesized_keys(name: str, classifiers: list[str], separator: str) -> None:
    info: dict[str, object] = {
        "NAME": name,
        "VERSION": "1.0",
        "CLASSIFIERS": classifiers,
        "PROJECT" + separator + "URLS": {"Homepage": "https://declared.invalid"},
    }
    synthesized = ("project-url", "package-url", "release-url", "docs-url", "bugtrack-url")
    info.update({key.upper().replace("-", separator): "https://index.invalid/synthesized" for key in synthesized})
    original = copy.deepcopy(info)
    result = index_metadata(info)
    assert result["name"] == name
    assert result["classifier"] == classifiers
    assert result["project-url"] == {"Homepage": "https://declared.invalid"}
    assert not any(key in result for key in synthesized[1:])
    assert info == original


@given(name=WORDS, classifiers=st.lists(WORDS, min_size=1, max_size=4), count=st.integers(1, 4))
def test_shared_metadata_matches_between_email_and_index(name: str, classifiers: list[str], count: int) -> None:
    requirements = [f"dependency{index}>=1" for index in range(count)]
    message = Message()
    message["Name"] = name
    message["Version"] = "1.0"
    message["Author-Email"] = "Author <author@example.invalid>"
    for classifier in classifiers:
        message["Classifier"] = classifier
    for requirement in requirements:
        message["Requires-Dist"] = requirement
    info: dict[str, object] = {
        "name": name,
        "version": "1.0",
        "author_email": "Author <author@example.invalid>",
        "classifiers": classifiers,
        "requires_dist": requirements,
    }
    wheel = extract(message)
    index = index_metadata(info)
    for key in ("name", "version", "author-email", "classifier"):
        assert wheel[key] == index[key]
    wheel_requirements = wheel["requires-dist"]
    assert (wheel_requirements if isinstance(wheel_requirements, list) else [wheel_requirements]) == index[
        "requires-dist"
    ]
