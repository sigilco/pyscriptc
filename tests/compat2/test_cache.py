from __future__ import annotations

from pathlib import Path

import pytest
from benchmarks.compat2 import cache
from benchmarks.compat2.outcomes import Probe

SRC = "def probe() -> int:\n    return 1\n"


def make(pid: str = "a", source: str = SRC) -> Probe:
    return Probe(id=pid, area="builtins", category="function", kind="value", source=source)


def test_key_changes_with_probe_source() -> None:
    hashes = cache.FileHashes()
    assert cache.cache_key(make(), {}, hashes) != cache.cache_key(make(source=SRC + "\n"), {}, hashes)


def test_key_ignores_compiler_files_off_the_map() -> None:
    hashes = cache.FileHashes()
    narrow = cache.cache_key(make(), {"a": ["ir.py"]}, hashes)
    assert narrow == cache.cache_key(make(), {"a": ["ir.py"]}, cache.FileHashes())
    assert narrow != cache.cache_key(make(), {"a": ["llvm.py"]}, hashes)
    assert narrow != cache.cache_key(make(), {}, hashes)  # no map entry: whole tree


def test_key_follows_the_oracle_version(monkeypatch: pytest.MonkeyPatch) -> None:
    before = cache.cache_key(make(), {}, cache.FileHashes())
    monkeypatch.setattr(cache, "SCORER_VERSION", "other")
    assert before != cache.cache_key(make(), {}, cache.FileHashes())


def test_store_is_atomic_and_load_survives_truncation(tmp_path: Path) -> None:
    cache.store(tmp_path, "k", {"row": 1})
    assert cache.load(tmp_path, "k") == {"row": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["k.json"]
    (tmp_path / "k.json").write_text('{"row"')
    assert cache.load(tmp_path, "k") is None
    assert cache.load(tmp_path, "missing") is None
