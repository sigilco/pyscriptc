"""Island engine descriptor schema v1, loader and `--island-engine` (issue #666)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pyscriptc import island_engine
from pyscriptc.config import ConfigError

VALID = """\
schema_version = 1

capabilities = ["fallback", "reflection"]

[engine]
name = "pypy"
version = "7.3.16"
kind = "interpreter"

[abi]
libpython = "3.14"
stable_abi = "no"
"""


def test_parse_valid_descriptor() -> None:
    d = island_engine.parse_descriptor_text(VALID)
    assert d.name == "pypy"
    assert d.version == "7.3.16"
    assert d.kind == "interpreter"
    assert d.capabilities == ["fallback", "reflection"]
    assert d.abi == {"libpython": "3.14", "stable_abi": "no"}
    assert d.extra == {}


def test_minimal_descriptor_has_defaults() -> None:
    d = island_engine.parse_descriptor_text(
        'schema_version = 1\n[engine]\nname = "x"\nversion = "1"\nkind = "interpreter"\n'
    )
    assert d.capabilities == []
    assert d.abi == {}


@pytest.mark.parametrize(
    "text, message",
    [
        ('schema_version = "1"\n[engine]\nname = "x"\nversion = "1"\nkind = "k"\n', "integer"),
        (
            'schema_version = 99\n[engine]\nname = "x"\nversion = "1"\nkind = "k"\n',
            "newer than the supported",
        ),
        ('schema_version = 1\nversion = "1"\n', "[engine]"),
        ('schema_version = 1\n[engine]\nversion = "1"\nkind = "k"\n', "name"),
        ('schema_version = 1\n[engine]\nname = ""\nversion = "1"\nkind = "k"\n', "non-empty"),
        (
            'schema_version = 1\ncapabilities = "fallback"\n[engine]\nname = "x"\nversion = "1"\nkind = "k"\n',
            "list of strings",
        ),
        ('schema_version = 1\n[engine]\nname = "x"\nversion = "1"\nabi = "no"\n', "kind"),
        ("schema_version = 1\n[engine\n", "invalid TOML"),
    ],
)
def test_reject_malformed(text: str, message: str) -> None:
    with pytest.raises(island_engine.EngineError, match=message):
        island_engine.parse_descriptor_text(text)


def test_unknown_keys_are_kept_for_forward_compat() -> None:
    d = island_engine.parse_descriptor_text(VALID + '\n[future]\nthing = "later"\n')
    assert d.extra == {"future": {"thing": "later"}}


def test_builtin_descriptor() -> None:
    d = island_engine.builtin_descriptor()
    assert d.name == island_engine.BUILTIN_NAME
    assert d.path is None
    assert d.as_summary()["source"] == "builtin"


def test_resolve_by_path(tmp_path: Path) -> None:
    path = tmp_path / "pypy.toml"
    path.write_text(VALID, encoding="utf-8")
    d = island_engine.resolve(str(path))
    assert d.name == "pypy"
    assert d.path == path


def test_resolve_by_name_searches_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "pypy.toml").write_text(VALID, encoding="utf-8")
    monkeypatch.setenv(island_engine.SEARCH_PATH_ENV, str(tmp_path))
    assert island_engine.resolve("pypy").name == "pypy"


def test_resolve_builtin_without_configuration() -> None:
    assert island_engine.resolve("cpython").name == "cpython"


def test_resolve_unknown_name_names_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(island_engine.SEARCH_PATH_ENV, raising=False)
    with pytest.raises(island_engine.EngineError, match=island_engine.SEARCH_PATH_ENV):
        island_engine.resolve("pypy")


def test_resolve_missing_path(tmp_path: Path) -> None:
    with pytest.raises(island_engine.EngineError, match="no descriptor file"):
        island_engine.resolve(str(tmp_path / "nope.toml"))


# boxed:begin
def test_config_island_engine_key(tmp_path: Path) -> None:
    from pyscriptc.config import load_config

    toml = tmp_path / "pyscriptc.toml"
    toml.write_text('[pyscriptc.island]\nengine = "pypy"\n', encoding="utf-8")
    config = load_config(toml)
    assert config.island_engine() == "pypy"


def test_config_island_engine_rejects_non_string(tmp_path: Path) -> None:
    from pyscriptc.config import load_config

    toml = tmp_path / "pyscriptc.toml"
    toml.write_text("[pyscriptc.island]\nengine = 3\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="must be str"):
        load_config(toml).island_engine()


# boxed:end
