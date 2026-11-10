"""Engine capability manifest v1 and the imports/assess consumers (issue #669)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.test_gates import run_cli, write_case

from pyscriptc import engine_manifest
from pyscriptc.engine_manifest import EngineManifest, ManifestError, parse_manifest

MANIFEST = {
    "schema": 1,
    "engine": {
        "name": "pypy",
        "version": "7.3.16",
        "language_level": "3.11",
        "platform": "linux-x86_64",
        "free_threaded": False,
    },
    "stdlib": {"available": ["json", "math"], "missing": ["annotationlib", "tkinter"]},
    "features": {"walrus_pep572": True, "tstring_pep750": False},
}


def test_parse_valid_manifest() -> None:
    got = parse_manifest(dict(MANIFEST))
    assert got.manifest_version == 1
    assert got.name == "pypy"
    assert got.version == "7.3.16"
    assert got.language_level == "3.11"
    assert got.platform == "linux-x86_64"
    assert got.free_threaded is False
    assert got.stdlib_available == ["json", "math"]
    assert got.stdlib_missing == ["annotationlib", "tkinter"]
    assert got.features == {"walrus_pep572": True, "tstring_pep750": False}
    assert got.path is None


def test_parse_json_text_and_file(tmp_path: Path) -> None:
    path = tmp_path / "pypy.json"
    path.write_text(json.dumps(MANIFEST), encoding="utf-8")
    from_text = engine_manifest.parse_manifest_text(json.dumps(MANIFEST), source=path)
    from_file = engine_manifest.load_manifest_file(path)
    for got in (from_text, from_file):
        assert isinstance(got, EngineManifest)
        assert got.name == "pypy"
        assert got.path == path


def test_minimal_manifest_defaults() -> None:
    got = parse_manifest(
        {
            "schema": 1,
            "engine": {"name": "x", "version": "1", "language_level": "3.14", "platform": "p"},
            "stdlib": {"available": [], "missing": []},
            "features": {},
        }
    )
    assert got.free_threaded is False
    assert got.extra == {}
    assert got.stdlib_status("json") == "unknown"


def test_reject_malformed() -> None:
    bad: list[tuple[object, str]] = [
        ("not-an-object", "top level must be a JSON object"),
        ({}, "schema must be an integer"),
        ({"schema": "1"}, "schema must be an integer"),
        (
            {"schema": 2, "engine": MANIFEST["engine"], "stdlib": MANIFEST["stdlib"], "features": {}},
            "newer than the supported version",
        ),
        ({"schema": 1}, "missing required [engine] object"),
        ({"schema": 1, "engine": {"name": "x"}}, "version must be a non-empty string"),
    ]
    for data, message in bad:
        with pytest.raises(ManifestError, match=message.replace("[", r"\[")):
            parse_manifest(data)


def test_reject_wrong_field_types() -> None:
    engine = dict(MANIFEST["engine"])
    engine["name"] = ""
    with pytest.raises(ManifestError, match=r"\[engine\] name must be a non-empty string"):
        parse_manifest({"schema": 1, "engine": engine, "stdlib": MANIFEST["stdlib"], "features": {}})
    with pytest.raises(ManifestError, match=r"missing required \[stdlib\] object"):
        parse_manifest({"schema": 1, "engine": MANIFEST["engine"], "features": {}})
    with pytest.raises(ManifestError, match=r"\[stdlib\] missing must be a list of strings"):
        parse_manifest(
            {
                "schema": 1,
                "engine": MANIFEST["engine"],
                "stdlib": {"available": [], "missing": [3]},
                "features": {},
            }
        )
    with pytest.raises(ManifestError, match=r"\[features\] must be an object of booleans"):
        parse_manifest(
            {
                "schema": 1,
                "engine": MANIFEST["engine"],
                "stdlib": MANIFEST["stdlib"],
                "features": {"x": "yes"},
            }
        )


def test_reject_module_in_both_lists() -> None:
    with pytest.raises(ManifestError, match="listed in both stdlib lists: json"):
        parse_manifest(
            {
                "schema": 1,
                "engine": MANIFEST["engine"],
                "stdlib": {"available": ["json"], "missing": ["json"]},
                "features": {},
            }
        )


def test_unknown_keys_are_kept_for_forward_compat() -> None:
    data = dict(MANIFEST)
    data["future_table"] = {"anything": [1]}
    got = parse_manifest(data)
    assert got.extra == {"future_table": {"anything": [1]}}


def test_invalid_json_names_the_problem() -> None:
    with pytest.raises(ManifestError, match="invalid JSON"):
        engine_manifest.parse_manifest_text("{not json")


def test_stdlib_status() -> None:
    got = parse_manifest(dict(MANIFEST))
    assert got.stdlib_status("json") == "available"
    assert got.stdlib_status("annotationlib") == "missing"
    assert got.stdlib_status("requests") == "unknown"


def test_as_dict_round_trips_json() -> None:
    got = parse_manifest(dict(MANIFEST))
    data = got.as_dict()
    assert data["manifestVersion"] == 1
    assert data["stdlib"] == {"available": ["json", "math"], "missing": ["annotationlib", "tkinter"]}
    assert json.loads(json.dumps(data)) == data


DESCRIPTOR = "schema_version = 1\n\n[engine]\nname = 'pypy'\nversion = '7.3.16'\n"
DESCRIPTOR += "kind = 'interpreter'\nmanifest = 'pypy-manifest.json'\n"

PROGRAM = "import annotationlib\nimport json\n"


def _engine_fixture(tmp_path: Path) -> Path:
    descriptor = tmp_path / "pypy.toml"
    descriptor.write_text(DESCRIPTOR, encoding="utf-8")
    (tmp_path / "pypy-manifest.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    return descriptor


def test_imports_json_engine_section(tmp_path: Path) -> None:
    descriptor = _engine_fixture(tmp_path)
    source = write_case(tmp_path, "prog.py", PROGRAM)
    got = run_cli("imports", str(source), "--json", "--island-engine", str(descriptor))
    assert got.returncode == 0, got.stderr
    report = json.loads(got.stdout)
    engine = report["engine"]
    assert engine["name"] == "pypy"
    assert engine["languageLevel"] == "3.11"
    status = {row["name"]: row["engine"]["stdlib"] for row in report["imports"]}
    assert status["annotationlib"] == "missing"
    assert status["json"] == "available"


def test_imports_text_engine_note(tmp_path: Path) -> None:
    descriptor = _engine_fixture(tmp_path)
    source = write_case(tmp_path, "prog.py", "import json\n")
    got = run_cli("imports", str(source), "--island-engine", str(descriptor))
    assert got.returncode == 0, got.stderr
    assert "engine: pypy 7.3.16 (python 3.11" in got.stdout
    assert "stdlib 2 available, 2 missing" in got.stdout


def test_imports_without_engine_is_unchanged(tmp_path: Path) -> None:
    source = write_case(tmp_path, "prog.py", PROGRAM)
    got = run_cli("imports", str(source), "--json")
    assert got.returncode == 0, got.stderr
    report = json.loads(got.stdout)
    assert "engine" not in report
    for row in report["imports"]:
        assert "engine" not in row


def test_imports_engine_from_config(tmp_path: Path) -> None:
    descriptor = _engine_fixture(tmp_path)
    source = write_case(tmp_path, "prog.py", "import json\n")
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pyscriptc.island]\nengine = '" + str(descriptor) + "'\n",
        encoding="utf-8",
    )
    got = run_cli("imports", str(source), "--json")
    assert got.returncode == 0, got.stderr
    assert json.loads(got.stdout)["engine"]["name"] == "pypy"


def test_imports_builtin_engine_has_no_manifest(tmp_path: Path) -> None:
    source = write_case(tmp_path, "prog.py", PROGRAM)
    got = run_cli("imports", str(source), "--json", "--island-engine", "cpython")
    assert got.returncode == 0, got.stderr
    assert "engine" not in json.loads(got.stdout)


def test_imports_bad_manifest_warns_and_falls_back(tmp_path: Path) -> None:
    descriptor = tmp_path / "pypy.toml"
    descriptor.write_text(DESCRIPTOR, encoding="utf-8")
    (tmp_path / "pypy-manifest.json").write_text("{broken", encoding="utf-8")
    source = write_case(tmp_path, "prog.py", PROGRAM)
    got = run_cli("imports", str(source), "--json", "--island-engine", str(descriptor))
    assert got.returncode == 0, got.stderr
    assert "engine" not in json.loads(got.stdout)
    assert "engine manifest" in got.stderr


def test_assess_json_engine_section(tmp_path: Path) -> None:
    descriptor = _engine_fixture(tmp_path)
    source = write_case(tmp_path, "prog.py", PROGRAM)
    got = run_cli("assess", str(source), "--json", "--island-engine", str(descriptor))
    assert got.returncode == 0, got.stderr
    section = json.loads(got.stdout)["engine"]
    assert section["name"] == "pypy"
    assert section["stdlib"]["missing"] == ["annotationlib", "tkinter"]
    assert section["stdlibStatus"]["annotationlib"] == "missing"
    assert section["stdlibStatus"]["json"] == "available"


def test_assess_text_engine_note(tmp_path: Path) -> None:
    descriptor = _engine_fixture(tmp_path)
    source = write_case(tmp_path, "prog.py", PROGRAM)
    got = run_cli("assess", str(source), "--island-engine", str(descriptor))
    assert got.returncode == 0, got.stderr
    assert "engine: pypy 7.3.16" in got.stdout


def test_assess_without_engine_is_unchanged(tmp_path: Path) -> None:
    source = write_case(tmp_path, "prog.py", PROGRAM)
    got = run_cli("assess", str(source), "--json")
    assert got.returncode == 0, got.stderr
    assert "engine" not in json.loads(got.stdout)
