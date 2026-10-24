"""Config surface: PEP 723 metadata, pyproject/pyscriptc.toml, policy table, precedence."""

from __future__ import annotations

from pathlib import Path

import pytest

from pyscriptc.config import ConfigError, lane_for, load_config, read_pep723


def test_pep723_basic() -> None:
    meta = read_pep723(
        '# /// script\n# requires-python = ">=3.12"\n# dependencies = ["orjson", "rich>=13"]\n# ///\nprint(\'hi\')\n'
    )
    assert meta == {"requires-python": ">=3.12", "dependencies": ["orjson", "rich>=13"]}


def test_pep723_absent_and_other_blocks() -> None:
    assert read_pep723("x = 1\n") is None
    # A non-comment line separates blocks (the PEP regex is greedy across
    # back-to-back comment blocks, matching the deps.py parser).
    meta = read_pep723("# /// something\n# x = 1\n# ///\nx = 0\n# /// script\n# y = 2\n# ///\n")
    assert meta == {"y": 2}


def test_pep723_duplicate_block_refused() -> None:
    text = "# /// script\n# x = 1\n# ///\nx = 0\n# /// script\n# y = 2\n# ///\n"
    with pytest.raises(ConfigError, match="more than one"):
        read_pep723(text)


def test_pep723_invalid_toml_refused() -> None:
    with pytest.raises(ConfigError, match="invalid TOML"):
        read_pep723("# /// script\n# x = = =\n# ///\n")


def _project(tmp_path: Path, table: str) -> Path:
    (tmp_path / "pyproject.toml").write_text(f'[project]\nname = "demo"\n\n{table}\n', encoding="utf-8")
    (tmp_path / "main.py").write_text("def main() -> int:\n    return 0\n", encoding="utf-8")
    return tmp_path / "main.py"


def test_pyproject_table(tmp_path: Path) -> None:
    script = _project(
        tmp_path,
        "[tool.pyscriptc]\n"
        'entry = "app"\n'
        'src = "src"\n'
        'checker = "pyrefly"\n'
        'driver = "zig"\n'
        'fail-on = "any"\n'
        "\n[tool.pyscriptc.imports]\n"
        'orjson = "replace"\n'
        'numpy = "island"\n'
        '"*" = "compile"\n',
    )
    cfg = load_config(script)
    assert cfg.source == "pyproject.toml"
    assert cfg.entry == "app"
    assert cfg.src == ("src",)
    assert cfg.checker == "pyrefly"
    assert cfg.driver == "zig"
    assert cfg.fail_on == "any"
    assert cfg.imports == {"orjson": "replace", "numpy": "island", "*": "compile"}


def test_pyscriptc_toml_whole_file(tmp_path: Path) -> None:
    (tmp_path / "pyscriptc.toml").write_text('[pyscriptc]\nentry = "cli"\n\n[pyscriptc.imports]\nsqlite3 = "extern"\n')
    script = tmp_path / "m.py"
    script.write_text("x = 1\n")
    cfg = load_config(script)
    assert cfg.source == "pyscriptc.toml"
    assert cfg.entry == "cli"
    assert cfg.imports == {"sqlite3": "extern"}


def test_discovery_walks_up_to_nearest(tmp_path: Path) -> None:
    _project(tmp_path, "[tool.pyscriptc]\nentry = 'outer'\n")
    sub = tmp_path / "pkg"
    sub.mkdir()
    (sub / "pyscriptc.toml").write_text('[pyscriptc]\nentry = "inner"\n')
    cfg = load_config(sub / "deep.py")
    assert cfg.source == "pyscriptc.toml"
    assert cfg.entry == "inner"
    # pyproject without [tool.pyscriptc] does not shadow anything
    other = tmp_path / "plain"
    other.mkdir()
    (other / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    cfg = load_config(other / "deep.py")
    # Walking up continues past a pyproject without the table.
    assert cfg.source == "pyproject.toml"
    assert cfg.entry == "outer"


def test_no_config_is_default() -> None:
    cfg = load_config(Path("/nonexistent/path.py"))
    assert cfg.source == "default"
    assert cfg.entry is None


def test_pep723_and_project_precedence(tmp_path: Path) -> None:
    script = _project(tmp_path, "[tool.pyscriptc]\nchecker = 'mypy'\n")
    script.write_text(
        "# /// script\n"
        '# requires-python = ">=3.12"\n'
        '# dependencies = ["orjson"]\n'
        "# ///\n"
        "def main() -> int:\n    return 0\n",
        encoding="utf-8",
    )
    cfg = load_config(script)
    assert cfg.source == "pyproject.toml"
    assert cfg.checker == "mypy"
    assert cfg.requires_python == ">=3.12"
    assert cfg.dependencies == ("orjson",)


def test_pep723_alone(tmp_path: Path) -> None:
    script = tmp_path / "s.py"
    script.write_text("# /// script\n# dependencies = ['rich']\n# ///\nx = 1\n", encoding="utf-8")
    cfg = load_config(script)
    assert cfg.source == "pep723"
    assert cfg.dependencies == ("rich",)
    assert cfg.entry is None


def test_unknown_key_refused(tmp_path: Path) -> None:
    script = _project(tmp_path, "[tool.pyscriptc]\nentryy = 'x'\n")
    with pytest.raises(ConfigError, match="unknown key"):
        load_config(script)


def test_bad_lane_refused(tmp_path: Path) -> None:
    script = _project(tmp_path, "[tool.pyscriptc.imports]\nnumpy = 'native'\n")
    with pytest.raises(ConfigError, match="must be one of"):
        load_config(script)


def test_bad_key_type_refused(tmp_path: Path) -> None:
    script = _project(tmp_path, "[tool.pyscriptc]\nentry = 3\n")
    with pytest.raises(ConfigError, match="entry must be str"):
        load_config(script)


def test_bad_checker_choice_refused(tmp_path: Path) -> None:
    script = _project(tmp_path, "[tool.pyscriptc]\nchecker = 'ctags'\n")
    with pytest.raises(ConfigError, match="checker"):
        load_config(script)


def test_invalid_import_name_refused(tmp_path: Path) -> None:
    script = _project(tmp_path, "[tool.pyscriptc.imports]\n'not a name' = 'island'\n")
    with pytest.raises(ConfigError, match="invalid module name"):
        load_config(script)


def test_invalid_toml_refused(tmp_path: Path) -> None:
    (tmp_path / "pyscriptc.toml").write_text("[pyscriptc\nentry =")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_config(tmp_path / "m.py")


LANE_CASES = [
    ({"orjson": "replace"}, "orjson", "replace", "orjson"),
    ({"orjson": "replace"}, "rich", "compile", "default"),
    ({}, "rich", "compile", "default"),
    ({"*": "extern", "orjson": "replace"}, "rich", "extern", "*"),
    ({"*": "extern"}, "orjson", "extern", "*"),
]


@pytest.mark.parametrize(("imports", "name", "lane", "key"), LANE_CASES)
def test_lane_lookup(imports: dict, name: str, lane: str, key: str) -> None:
    assert lane_for(name, imports) == (lane, key)


def test_stub_path_and_ffi_keys(tmp_path: Path) -> None:
    script = _project(tmp_path, "[tool.pyscriptc]\nstub-path = 'stubs'\nffi = ['a/ffi.toml', 'b.toml']\n")
    cfg = load_config(script)
    assert cfg.stub_path == ("stubs",)
    assert cfg.ffi == ("a/ffi.toml", "b.toml")
    assert cfg.stub_paths == ((tmp_path / "stubs").resolve(),)
    assert cfg.ffi_manifests[0] == (tmp_path / "a" / "ffi.toml").resolve()


def test_stub_path_wrong_type_refused(tmp_path: Path) -> None:
    script = _project(tmp_path, "[tool.pyscriptc]\nffi = 3\n")
    with pytest.raises(ConfigError, match="ffi"):
        load_config(script)
    script = _project(tmp_path, "[tool.pyscriptc]\nstub-path = [1]\n")
    with pytest.raises(ConfigError, match="list of strings"):
        load_config(script)


def test_dependencies_mapped_to_lanes(tmp_path: Path) -> None:
    script = _project(tmp_path, "[tool.pyscriptc.imports]\nrich = 'refuse'\nattrs = 'island'\n")
    script.write_text(
        "# /// script\n"
        "# dependencies = ['orjson>=3', 'rich', 'attrs', 'My-Pure.Pkg[x]; python_version>\"3\"']\n"
        "# ///\nx = 1\n",
        encoding="utf-8",
    )
    lanes = load_config(script).dependency_lanes()
    assert lanes["orjson>=3"] == "pack"
    assert lanes["rich"] == "refused"
    assert lanes["attrs"] == "island"
    assert lanes['My-Pure.Pkg[x]; python_version>"3"'] == "native"


def test_config_exposes_island_import_helpers(tmp_path: Path) -> None:
    from pyscriptc.config import load_config

    script = _project(tmp_path, "[tool.pyscriptc.imports]\nrequests = 'island'\nattrs = 'island'\n'*' = 'compile'\n")
    cfg = load_config(script)
    assert cfg.island_imports() == ("attrs", "requests")
    assert cfg.wildcard_island() is False
    b = tmp_path / "b"
    b.mkdir()
    script2 = _project(b, "[tool.pyscriptc.imports]\n'*' = 'island'\n")
    (b / "main.py").write_text("def main() -> int:\n    return 0\n", encoding="utf-8")
    assert load_config(script2).wildcard_island() is True


def test_dependency_lane_star_and_invalid() -> None:
    from pyscriptc.config import dependency_lanes

    assert dependency_lanes(("zzzpure",), {"*": "refuse"}) == {"zzzpure": "refused"}
    assert dependency_lanes(("zzzpure",), {"*": "compile"}) == {"zzzpure": "native"}
    with pytest.raises(ConfigError, match="not a valid requirement"):
        dependency_lanes(("==1",))


def test_refuse_lane_lower_import(tmp_path: Path) -> None:
    from pyscriptc.lower import lower_file

    script = _project(tmp_path, "[tool.pyscriptc.imports]\nrich = 'refuse'\n")
    script.write_text("import rich\n", encoding="utf-8")
    mod, _ = lower_file(str(script))
    assert len(mod["fences"]) == 1
    fence = mod["fences"][0]
    assert fence["code"] == "PS2002"
    assert fence["message"] == "import `rich` is refused by the `imports` policy"


def test_refuse_lane_lower_from_import(tmp_path: Path) -> None:
    from pyscriptc.lower import lower_file

    script = _project(tmp_path, "[tool.pyscriptc.imports]\nrich = 'refuse'\n")
    script.write_text("from rich.tree import Tree\n", encoding="utf-8")
    mod, _ = lower_file(str(script))
    assert len(mod["fences"]) == 1
    fence = mod["fences"][0]
    assert fence["code"] == "PS2002"
    assert fence["message"] == "import `rich` is refused by the `imports` policy"


def test_refuse_lane_wildcard_and_override(tmp_path: Path) -> None:
    from pyscriptc.lower import lower_file

    script = _project(tmp_path, "[tool.pyscriptc.imports]\n'*' = 'refuse'\nrich = 'compile'\n")
    script.write_text("import rich\nimport httpx\n", encoding="utf-8")
    mod, _ = lower_file(str(script))
    assert len(mod["fences"]) == 2
    fences_by_msg = {f["message"]: f["code"] for f in mod["fences"]}
    assert fences_by_msg["rich is not in the closed import table"] == "PS2002"
    assert fences_by_msg["import `httpx` is refused by the `imports` policy"] == "PS2002"


def test_other_lanes_unchanged_at_lower(tmp_path: Path) -> None:
    from pyscriptc.lower import lower_file

    script = _project(
        tmp_path,
        "[tool.pyscriptc.imports]\nrich = 'compile'\nfastapi = 'replace'\nclick = 'extern'\n",
    )
    script.write_text("import rich\nimport fastapi\nimport click\n", encoding="utf-8")
    mod, _ = lower_file(str(script))
    assert len(mod["fences"]) == 3
    for fence in mod["fences"]:
        assert fence["code"] == "PS2002"
        assert "is not in the closed import table" in fence["message"]
        assert "refused by" not in fence["message"]


def test_no_policy_byte_identical_fence(tmp_path: Path) -> None:
    from pyscriptc.lower import lower_file

    plain = tmp_path / "plain.py"
    plain.write_text("import rich\n", encoding="utf-8")
    mod, _ = lower_file(str(plain))
    assert len(mod["fences"]) == 1
    assert mod["fences"][0]["code"] == "PS2002"
    assert mod["fences"][0]["message"] == "rich is not in the closed import table"


def test_pep723_refuse_lane(tmp_path: Path) -> None:
    from pyscriptc.lower import lower_file

    script = tmp_path / "script.py"
    script.write_text(
        "# /// script\n# [tool.pyscriptc.imports]\n# rich = 'refuse'\n# ///\nimport rich\n",
        encoding="utf-8",
    )
    mod, _ = lower_file(str(script))
    assert len(mod["fences"]) == 1
    assert mod["fences"][0]["code"] == "PS2002"
    assert mod["fences"][0]["message"] == "import `rich` is refused by the `imports` policy"
