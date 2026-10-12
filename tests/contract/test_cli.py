"""Exercise the CLI's JSON contract independently of package builds."""

import contextlib
import io
import json
import subprocess
import sys
from argparse import Namespace
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from unittest.mock import Mock

import pytest
from jsonschema import Draft202012Validator, ValidationError

import pyroma
from pyroma import config, projectdata, ratings, report
from pyroma.tests import COMPLETE


def invoke(monkeypatch: pytest.MonkeyPatch, arguments: list[str]) -> tuple[int, str, str]:
    monkeypatch.setattr(sys, "argv", ["pyroma", *arguments])
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), pytest.raises(SystemExit) as raised:
        pyroma.main()
    return raised.value.code, out.getvalue(), err.getvalue()


@pytest.mark.parametrize(
    ("arguments", "message"),
    [(["--min", value, "."], "integer between 1 and 10") for value in ("-1", "0", "11", "abc", "1.5")]
    + [
        (["--skip-tests", "not-a-rule", "."], "not-a-rule"),
        (["--skip-tests", "", "."], "Available categories"),
        (["--skip-tests", ", ;", "."], "Available tests"),
        (["--unknown", "."], "--unknown"),
        ([], "package"),
        (["--min"], "expected one argument"),
        ([".", "--skip-tests"], "expected one argument"),
        (["--directory", "--file", "."], "not allowed"),
    ],
)
@pytest.mark.parametrize("format_args", [["--format", "json"], ["--format=json"]])
def test_usage_errors(
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
    message: str,
    format_args: list[str],
    validator: Callable[[str], Draft202012Validator],
) -> None:
    blocked = Mock(side_effect=AssertionError("Usage errors must not read configuration or packages"))
    monkeypatch.setattr(pyroma, "_resolve_config", blocked)
    monkeypatch.setattr(pyroma, "_get_data", blocked)
    # Putting the format after a bad value must work too. Missing option
    # values need the format first so it does not become the missing value.
    argv = [*format_args, *arguments] if arguments == ["--min"] else [*arguments, *format_args]
    code, out, err = invoke(monkeypatch, argv)
    assert code == 2
    assert err == ""
    document = json.loads(out)
    validator("error").validate(document)
    assert document["error"]["type"] == "UsageError"
    assert message in document["error"]["message"]
    assert document["_meta"]["pyroma"] == pyroma.package_version("pyroma621")
    assert "policy" not in document["_meta"]
    assert "\x1b" not in out
    blocked.assert_not_called()


@pytest.mark.parametrize(
    ("arguments", "json_error"),
    [
        (["--format=json", "--format", "text", "--min=11", "."], False),
        (["--format=text", "--min=11", "--format=json", "."], True),
        (["--quiet", "--format=json", "--min=11", "."], True),
        (["--min=11", "--", "--format=json"], False),
        (["--format=json", "--min=11", "--", "--format=text"], True),
        (["--min=11", "."], False),
        (["--format=json", "--unknown", "--format=yaml", "."], False),
        (["--format=json", ".", "--format"], False),
    ],
)
def test_error_format_selection(monkeypatch: pytest.MonkeyPatch, arguments: list[str], json_error: bool) -> None:
    code, out, err = invoke(monkeypatch, arguments)
    assert code == 2
    if json_error:
        assert json.loads(out)["error"]["type"] == "UsageError"
        assert err == ""
    else:
        assert out == ""
        assert "usage:" in err


@pytest.mark.parametrize("arguments", [["--version"], ["--version", "."], ["--format=json", "--version"]])
def test_version_avoids_rating(monkeypatch: pytest.MonkeyPatch, arguments: list[str]) -> None:
    blocked = Mock(side_effect=AssertionError("Version must not rate a package"))
    monkeypatch.setattr(pyroma, "_resolve_mode", blocked)
    monkeypatch.setattr(pyroma, "_resolve_config", blocked)
    monkeypatch.setattr(pyroma, "_get_data", blocked)
    monkeypatch.setattr(pyroma.pypidata.requests, "get", blocked)
    code, out, err = invoke(monkeypatch, arguments)
    assert (code, out, err) == (0, f"pyroma621 {pyroma.package_version('pyroma621')}\n", "")
    blocked.assert_not_called()


def test_uninstalled_version(monkeypatch: pytest.MonkeyPatch, validator: Callable[[str], Draft202012Validator]) -> None:
    monkeypatch.setattr(pyroma, "package_version", Mock(side_effect=PackageNotFoundError))
    assert invoke(monkeypatch, ["--version"]) == (0, "pyroma621 unknown\n", "")
    _, out, _ = invoke(monkeypatch, ["--format=json", "--min=11", "."])
    document = json.loads(out)
    assert document["_meta"] == {"schema_version": 1}
    validator("error").validate(document)


def test_module_version() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pyroma", "--version"], capture_output=True, text=True, check=True, timeout=30
    )
    assert result.stdout == f"pyroma621 {pyroma.package_version('pyroma621')}\n"
    assert result.stderr == ""


def test_help_remains_text(monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, err = invoke(monkeypatch, ["--format=json", "--help"])
    assert code == 0
    assert "usage:" in out
    assert "--version" in out
    assert err == ""


def test_parser_retains_namespace_and_iterable_support() -> None:
    namespace = Namespace(custom=True)
    parsed = pyroma._build_parser().parse_args(iter(["--min=9", "."]), namespace)
    assert parsed is namespace
    assert parsed.min == 9
    assert parsed.custom is True


@pytest.mark.parametrize("mode", ["directory", "file", "pypi"])
@pytest.mark.parametrize("disable", [False, True])
def test_policy_discovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mode: str,
    disable: bool,
    validator: Callable[[str], Draft202012Validator],
) -> None:
    source = tmp_path / "pyproject.toml"
    source.write_text('[tool.pyroma]\nmin-rating = 10\nstrict = true\nadvisories = false\nskip-tests = ["practice"]\n')
    reader = Mock(wraps=projectdata.read_pyproject)
    monkeypatch.setattr(projectdata, "read_pyproject", reader)
    monkeypatch.setattr(pyroma, "_get_data", lambda *_args, **_kwargs: COMPLETE.copy())
    arguments = ["--format=json", f"--{mode}", str(tmp_path)]
    if disable:
        arguments.append("--no-config")
    _, out, err = invoke(monkeypatch, arguments)
    document = json.loads(out)
    validator("rating").validate(document)
    enabled = mode == "directory" and not disable
    assert document["_meta"]["config"] == {
        "enabled": enabled,
        "loaded": enabled,
        "source": str(source) if enabled else None,
    }
    assert document["_meta"]["policy"] == {
        "min_rating": 10 if enabled else 8,
        "strict": enabled,
        "show_advisories": not enabled,
        "skip_tests": ["practice"] if enabled else [],
    }
    assert reader.call_count == int(enabled)
    assert err == ""


def test_cli_overrides_and_canonical_skips(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.pyroma]\nmin-rating=3\nstrict=true\nadvisories=false\n")
    monkeypatch.setattr(pyroma, "_get_data", lambda *_args, **_kwargs: COMPLETE.copy())
    _, out, _ = invoke(
        monkeypatch,
        [
            "--format=json",
            "--min=9",
            "--no-strict",
            "--advisories",
            "--skip-tests",
            "practice,Keywords,practice",
            str(tmp_path),
        ],
    )
    assert json.loads(out)["_meta"]["policy"] == {
        "min_rating": 9,
        "strict": False,
        "show_advisories": True,
        "skip_tests": ["Keywords", "practice"],
    }


@pytest.mark.parametrize("toml", [None, '[project]\nname="example"\n', "[project\n"])
def test_absent_and_malformed_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    toml: str | None,
    validator: Callable[[str], Draft202012Validator],
) -> None:
    source = tmp_path / "pyproject.toml"
    if toml is not None:
        source.write_text(toml)
    monkeypatch.setattr(pyroma, "_get_data", Mock(side_effect=projectdata.BuildError("could not build")))
    code, out, err = invoke(monkeypatch, ["--format=json", str(tmp_path)])
    document = json.loads(out)
    validator("error").validate(document)
    assert code == 3
    assert err == ""
    assert document["_meta"]["config"] == {
        "enabled": True,
        "loaded": False,
        "source": str(source) if toml is not None else None,
    }
    assert document["_meta"]["policy"]["min_rating"] == 8


@pytest.mark.parametrize("table", ["min-rating=11", 'skip-tests=["not-a-rule"]'])
def test_config_validation_has_no_effective_policy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    table: str,
    validator: Callable[[str], Draft202012Validator],
) -> None:
    source = tmp_path / "pyproject.toml"
    source.write_text(f"[tool.pyroma]\n{table}\n")
    code, out, err = invoke(monkeypatch, ["--format=json", str(tmp_path)])
    document = json.loads(out)
    validator("error").validate(document)
    assert code == 3
    assert err == ""
    assert document["error"]["type"] == "ConfigError"
    assert "policy" not in document["_meta"]
    assert document["_meta"]["config"]["loaded"] is False


@pytest.mark.parametrize("strict", [False, True])
def test_real_rating_schema(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    strict: bool,
    validator: Callable[[str], Draft202012Validator],
) -> None:
    data = COMPLETE.copy()
    data.pop("keywords")
    monkeypatch.setattr(pyroma, "_get_data", lambda *_args, **_kwargs: data)
    args = ["--format=json", "--no-advisories", "--min=10", str(tmp_path)]
    if strict:
        args.append("--strict")
    code, out, err = invoke(monkeypatch, args)
    document = json.loads(out)
    validator("rating").validate(document)
    assert code == 2
    assert err == ""
    assert any(problem["test"] == "Keywords" for problem in document["problems"])
    assert bool(document["advisories"]) is not strict
    if not strict:
        assert document["rating"] == 9


def test_fatal_rating_and_configuration_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, validator: Callable[[str], Draft202012Validator]
) -> None:
    data = COMPLETE.copy()
    data.pop("name")
    monkeypatch.setattr(pyroma, "_get_data", lambda *_args, **_kwargs: data)
    code, out, _ = invoke(monkeypatch, ["--format=json", str(tmp_path)])
    document = json.loads(out)
    validator("rating").validate(document)
    assert code == 2
    assert document["rating"] == 0
    assert any(problem["fatal"] for problem in document["problems"])
    monkeypatch.setattr(pyroma, "_get_data", lambda *_args, **_kwargs: COMPLETE.copy())
    code, out, _ = invoke(
        monkeypatch,
        [
            "--format=json",
            "--skip-tests",
            ",".join(pyroma.get_all_tests()),
            str(tmp_path),
        ],
    )
    document = json.loads(out)
    validator("error").validate(document)
    assert code == 3
    assert document["error"]["type"] == "ConfigurationError"
    assert document["_meta"]["policy"]["skip_tests"] == sorted(pyroma.get_all_tests())


@pytest.mark.parametrize("rating", [0, 10])
def test_rating_boundaries_and_additions(rating: int, validator: Callable[[str], Draft202012Validator]) -> None:
    rated = ratings.RatedProject(name=None, rating=rating, level=ratings.LEVELS[rating], problems=[])
    meta = {"schema_version": 99, "future": {"value": True}}
    document = json.loads(report.format_json(rated, meta))
    document["future"] = "allowed"
    validator("rating").validate(document)
    assert document["problems"] == document["advisories"] == []
    assert document["_meta"]["schema_version"] == 1
    assert meta["schema_version"] == 99


@pytest.mark.parametrize("field", ["rating", "problems", "advisories", "_meta"])
def test_schema_rejects_missing_or_wrong_fields(field: str, validator: Callable[[str], Draft202012Validator]) -> None:
    document = json.loads(report.format_json(ratings.RatedProject(name="x", rating=10, level="x", problems=[])))
    invalid = {key: value for key, value in document.items() if key != field}
    with pytest.raises(ValidationError):
        validator("rating").validate(invalid)
    document[field] = "wrong type"
    with pytest.raises(ValidationError):
        validator("rating").validate(document)


def test_schema_finding_and_document_discriminators(validator: Callable[[str], Draft202012Validator]) -> None:
    problem = ratings.Problem(test="FutureRule", message="x", weight=1, fatal=False, category="future")
    rated = ratings.RatedProject(name="x", rating=0, level="x", problems=[problem])
    document = json.loads(report.format_json(rated))
    validator("rating").validate(document)
    document["problems"][0]["weight"] = -1
    with pytest.raises(ValidationError):
        validator("rating").validate(document)
    error = json.loads(report.format_json_error(config.ConfigError("x")))
    validator("error").validate(error)
    error["rating"] = 0
    with pytest.raises(ValidationError):
        validator("error").validate(error)


def test_schemas_share_metadata_definition(validator: Callable[[str], Draft202012Validator]) -> None:
    assert validator("rating").schema["$defs"]["meta"] == validator("error").schema["$defs"]["meta"]


@pytest.mark.parametrize(
    ("arguments", "attribute", "expected"),
    [
        (["--skip", "Keywords", "."], "skip_tests", ["Keywords"]),
        (["--no-adv", "."], "show_advisories", False),
        (["--index", "https://example.com", "."], "index_url", "https://example.com"),
        (["--form=json", "."], "output_format", "json"),
        (["--fo", "json", "."], "output_format", "json"),
    ],
)
def test_unambiguous_abbreviations_are_accepted(arguments: list[str], attribute: str, expected: object) -> None:
    assert getattr(pyroma._build_parser().parse_args(arguments), attribute) == expected


def test_abbreviated_version_avoids_rating(monkeypatch: pytest.MonkeyPatch) -> None:
    blocked = Mock(side_effect=AssertionError("Version queries must not inspect a target"))
    monkeypatch.setattr(pyroma, "_resolve_config", blocked)
    code, out, err = invoke(monkeypatch, ["--vers"])
    assert code == 0
    assert out.startswith("pyroma621 ")
    assert err == ""
    blocked.assert_not_called()


@pytest.mark.parametrize("arguments", [["--f=json", "."], ["--no", "."]])
@pytest.mark.parametrize("json_requested", [False, True])
def test_ambiguous_abbreviations_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
    json_requested: bool,
    validator: Callable[[str], Draft202012Validator],
) -> None:
    blocked = Mock(side_effect=AssertionError("Invalid options must not inspect a target"))
    monkeypatch.setattr(pyroma, "_resolve_config", blocked)
    monkeypatch.setattr(pyroma, "_get_data", blocked)
    arguments = [*arguments, *(["--format=json"] if json_requested else [])]
    code, out, err = invoke(monkeypatch, arguments)
    assert code == 2
    if json_requested:
        document = json.loads(out)
        validator("error").validate(document)
        assert document["error"]["type"] == "UsageError"
        assert "ambiguous option" in document["error"]["message"]
        assert err == ""
    else:
        assert out == ""
        assert "usage:" in err
    blocked.assert_not_called()


@pytest.mark.parametrize(
    ("format_arguments", "json_error"),
    [
        (["--form=json"], True),
        (["--fo", "json"], True),
        (["--format=text", "--fo=json"], True),
        (["--fo=json", "--format=text"], False),
        (["--f=json"], False),
        (["--f=json", "--fo=json"], True),
        (["--", "--fo=json"], False),
    ],
)
def test_abbreviated_error_format_selection(
    monkeypatch: pytest.MonkeyPatch,
    format_arguments: list[str],
    json_error: bool,
    validator: Callable[[str], Draft202012Validator],
) -> None:
    code, out, err = invoke(monkeypatch, ["--min=11", ".", *format_arguments])
    assert code == 2
    if json_error:
        document = json.loads(out)
        validator("error").validate(document)
        assert document["error"]["type"] == "UsageError"
        assert err == ""
    else:
        assert out == ""
        assert "usage:" in err


@pytest.mark.parametrize(
    "first_method", ["parse_args", "parse_known_args", "parse_intermixed_args", "parse_known_intermixed_args"]
)
@pytest.mark.parametrize(
    "second_method", ["parse_args", "parse_known_args", "parse_intermixed_args", "parse_known_intermixed_args"]
)
@pytest.mark.parametrize("json_first", [False, True])
def test_parser_resets_error_format_and_color(
    capsys: pytest.CaptureFixture, first_method: str, second_method: str, json_first: bool
) -> None:
    parser = pyroma._build_parser()
    first_format = "json" if json_first else "text"
    getattr(parser, first_method)([f"--format={first_format}", "."])
    assert vars(parser)["color"] is not json_first
    next_arguments = ["--min=11", "."] if json_first else ["--format=json", "--min=11", "."]
    if json_first:
        with pytest.raises(SystemExit) as raised:
            getattr(parser, second_method)(next_arguments)
        assert raised.value.code == 2
        assert "usage:" in capsys.readouterr().err
    else:
        with pytest.raises(pyroma.UsageError, match="integer between 1 and 10"):
            getattr(parser, second_method)(next_arguments)
        assert capsys.readouterr().err == ""
    assert vars(parser)["color"] is json_first


@pytest.mark.parametrize("method", ["parse_known_args", "parse_known_intermixed_args"])
def test_known_args_retains_namespace_iterators_and_unknowns(method: str) -> None:
    namespace = Namespace(custom=True)
    parsed, unknown = getattr(pyroma._build_parser(), method)(iter(["--min=9", ".", "--future"]), namespace)
    assert parsed is namespace
    assert (parsed.min, parsed.custom, unknown) == (9, True, ["--future"])


@pytest.mark.parametrize(
    "method", ["parse_args", "parse_known_args", "parse_intermixed_args", "parse_known_intermixed_args"]
)
def test_missing_package_keeps_json_through_nested_parsing(method: str, capsys: pytest.CaptureFixture) -> None:
    parser = pyroma._build_parser()
    with pytest.raises(pyroma.UsageError, match=r"required.*package"):
        getattr(parser, method)(["--fo=json"])
    assert capsys.readouterr().err == ""
    # A failed parse must also leave the parser ready for its next invocation.
    with pytest.raises(SystemExit) as raised:
        getattr(parser, method)([])
    assert raised.value.code == 2
    assert "usage:" in capsys.readouterr().err


def test_cli_and_configuration_share_selector_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    names = [*pyroma.get_all_tests(), *pyroma.get_all_categories()]
    source = tmp_path / "pyproject.toml"
    source.write_text(f"[tool.pyroma]\nskip-tests = {json.dumps(names)}\n", encoding="utf-8")
    assert config.from_directory(tmp_path).skip_tests == pyroma.skip_tests(",".join(names)) == names

    class CustomRule:
        pass

    monkeypatch.setattr(ratings, "ALL_TESTS", [CustomRule()])
    source.write_text('[tool.pyroma]\nskip-tests = ["CustomRule", "practice"]\n', encoding="utf-8")
    assert config.from_directory(tmp_path).skip_tests == pyroma.skip_tests("CustomRule,practice")

    source.write_text('[tool.pyroma]\nskip-tests = ["Keywords"]\n', encoding="utf-8")
    with pytest.raises(config.ConfigError) as configuration_error:
        config.from_directory(tmp_path)
    with pytest.raises(pyroma.ArgumentTypeError) as cli_error:
        pyroma.skip_tests("Keywords")
    config_choices = str(configuration_error.value).split("Available tests:", 1)[1]
    cli_choices = str(cli_error.value).split("Available tests:", 1)[1]
    assert config_choices == cli_choices
    assert "CustomRule" in config_choices


@pytest.mark.parametrize("arguments", [["--version"], ["--format=json", "--min=11", "."], ["--format=json", "package"]])
@pytest.mark.parametrize("installed", [False, True])
def test_version_is_resolved_once_per_invocation(
    monkeypatch: pytest.MonkeyPatch, arguments: list[str], installed: bool
) -> None:
    versions = ["6.2.0.dev0", "6.3.0.dev0"]
    lookup = Mock(side_effect=versions if installed else [PackageNotFoundError(), PackageNotFoundError()])
    monkeypatch.setattr(pyroma, "package_version", lookup)
    monkeypatch.setattr(pyroma, "_get_data", lambda *_args, **_kwargs: COMPLETE.copy())
    for count, version in enumerate(versions, start=1):
        _, out, err = invoke(monkeypatch, arguments)
        assert err == ""
        assert lookup.call_count == count
        if arguments == ["--version"]:
            assert out == f"pyroma621 {version if installed else 'unknown'}\n"
        else:
            meta = json.loads(out)["_meta"]
            if installed:
                assert meta["pyroma"] == version
            else:
                assert "pyroma" not in meta
    assert all(call.args == ("pyroma621",) for call in lookup.call_args_list)


@pytest.mark.parametrize("table", ["min-rating=11", 'skip-tests=["not-a-rule"]'])
def test_configuration_provenance_is_validated_during_loading(tmp_path: Path, table: str) -> None:
    source = tmp_path / "pyproject.toml"
    source.write_text(f"[tool.pyroma]\n{table}\n", encoding="utf-8")
    provenance = config.ConfigSource(loaded=True, source="previous-project")
    with pytest.raises(config.ConfigError):
        config.from_directory(tmp_path, provenance=provenance)
    assert provenance == config.ConfigSource(enabled=True, loaded=False, source=str(source))


def test_empty_configuration_is_loaded(tmp_path: Path) -> None:
    source = tmp_path / "pyproject.toml"
    source.write_text("[tool.pyroma]\n", encoding="utf-8")
    provenance = config.ConfigSource()
    assert config.from_directory(tmp_path, provenance=provenance) == config.Config()
    assert provenance == config.ConfigSource(enabled=True, loaded=True, source=str(source))
