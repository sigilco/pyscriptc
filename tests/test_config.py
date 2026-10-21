"""Config surface: PEP 723 metadata, pyproject/pyscriptc.toml, policy table, precedence."""

from __future__ import annotations

from pathlib import Path

import pytest

from pyscriptc.config import ConfigError, lane_for, load_config, read_pep723


def test_pep723_basic() -> None:
    meta = read_pep723(
        "# /// script\n"
        "# requires-python = \">=3.12\"\n"
        '# dependencies = ["orjson", "rich>=13"]\n'
        "# ///\n"
        "print('hi')\n"
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
