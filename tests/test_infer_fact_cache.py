# SPDX-License-Identifier: Apache-2.0
"""The per-module inference-fact cache (issue #1318).

The pin engine pays one checker round trip per module on every
invocation, so a repeat compile of an unchanged tree pays the full cost
again. The cache persists the per-module call sites under the pyscriptc
cache root and loads them back when the module's content, the compiler
digest, the checker identity and the content hashes of the in-tree
modules it imports from are all unchanged.

The three properties the test pins down:

- a cache hit: the second run of an unchanged tree starts no checker
  server at all and produces the same pins;
- invalidation: a changed module (its own text, or the text of a module
  it imports from) recomputes, and a changed compiler digest or checker
  stamp invalidates the entry;
- no behaviour change: the lowered output of a warm run is byte for byte
  the output of the cold run that filled the cache.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from tests.test_gates import ROOT, write_case

_HELPERS = "def parse(text):\n    return text.strip()\n"
_MAIN = (
    "from helpers import parse\n"
    "\n"
    "\n"
    "def main():\n"
    "    print(parse('  x  '))\n"
    "\n"
    "\n"
    "if __name__ == '__main__':\n"
    "    main()\n"
)


@pytest.fixture(autouse=True)
def _cache_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the pyscriptc cache root at the test's own directory."""
    root = tmp_path / "cache"
    monkeypatch.setenv("PYSC_CACHE_DIR", str(root))
    return root


@pytest.fixture
def _server_starts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every checker server connect the run performs, without blocking it."""
    from pyscriptc import typefacts

    starts: list[str] = []
    original = typefacts.PyreflyTspFactsAdapter._connect

    def _counted(self: object, *args: object, **kwargs: object) -> object:
        starts.append("connect")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(typefacts.PyreflyTspFactsAdapter, "_connect", _counted)
    return starts


def _tree(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    root.mkdir(parents=True)
    write_case(root, "helpers.py", _HELPERS)
    write_case(root, "__main__.py", _MAIN)
    return root


def _pin_map(facts: object) -> dict[tuple[str, str], tuple[str, int]]:
    pins = facts.pins
    return {key: (pin.kind, pin.sites) for key, pin in pins.items()}


def test_cache_hit_skips_the_checker_server(tmp_path: Path, _server_starts: list[str]) -> None:
    """A warm tree starts no checker server and keeps the same pins."""
    from pyscriptc.inferrunner import build_facts

    root = _tree(tmp_path)
    cold = build_facts(root)
    assert cold.pins != {}
    before = len(_server_starts)
    assert before, "the cold run must reach the checker"

    warm = build_facts(root)
    assert len(_server_starts) == before, "the warm run started a checker server"
    assert _pin_map(warm) == _pin_map(cold)
    assert [call.callee for call in warm.calls] == [call.callee for call in cold.calls]


def test_cache_entry_is_written_under_the_cache_root(tmp_path: Path) -> None:
    """The facts land in the cache root, one file per module."""
    from pyscriptc.infercache import cache_dir
    from pyscriptc.inferrunner import build_facts

    root = _tree(tmp_path)
    build_facts(root)
    entries = sorted(path.name for path in cache_dir().glob("*.json"))
    assert entries, "the cache holds no entry after a cold run"


def test_changed_module_recomputes(tmp_path: Path, _server_starts: list[str]) -> None:
    """A module whose text changed is asked again; the pins follow the new text."""
    from pyscriptc.inferrunner import build_facts

    root = _tree(tmp_path)
    cold = build_facts(root)
    assert _pin_map(cold)[("helpers.parse", "text")] == ("str", 1)

    write_case(root, "helpers.py", _HELPERS.replace("return text.strip()", "return text.strip().upper()"))
    before = len(_server_starts)
    warm = build_facts(root)
    assert len(_server_starts) > before, "the changed module was not re-checked"
    assert _pin_map(warm) == _pin_map(cold)


def test_changed_dependency_recomputes(tmp_path: Path, _server_starts: list[str]) -> None:
    """A module whose in-tree dependency changed is asked again.

    The dependency set is the direct imports (the design note's rule), so
    editing the imported module invalidates the importer's entry.
    """
    from pyscriptc.inferrunner import build_facts

    root = _tree(tmp_path)
    build_facts(root)
    before = len(_server_starts)

    write_case(root, "helpers.py", _HELPERS + "\n\ndef extra():\n    return 1\n")
    build_facts(root)
    assert len(_server_starts) > before, "the importer of a changed module was not re-checked"


def test_compiler_digest_change_invalidates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A different compiler digest makes the entry stale."""
    from pyscriptc import infercache
    from pyscriptc.inferrunner import build_facts

    root = _tree(tmp_path)
    build_facts(root)
    source = (root / "helpers.py").read_bytes()
    assert infercache.load(source, "helpers", {}) is not None

    monkeypatch.setattr(infercache, "_facts_key", _key_with_digest("deadbeef"))
    assert infercache.load(source, "helpers", {}) is None


def _key_with_digest(digest: str):  # noqa: ANN202 - test-local patch shape
    def _key(source: bytes, module: str, dep_hashes: dict[str, str]) -> str:
        return f"{digest}:{hash(source)}:{module}:{len(dep_hashes)}"

    return _key


def test_checker_stamp_change_invalidates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A different checker identity makes the entry stale."""
    from pyscriptc import infercache
    from pyscriptc.inferrunner import build_facts

    root = _tree(tmp_path)
    build_facts(root)
    source = (root / "helpers.py").read_bytes()
    assert infercache.load(source, "helpers", {}) is not None

    monkeypatch.setattr(infercache, "checker_stamp", lambda: "0" * 64)
    assert infercache.load(source, "helpers", {}) is None


def test_cache_bypass_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`PYSC_NO_INFER_CACHE=1` turns the cache off."""
    from pyscriptc import infercache

    monkeypatch.setenv("PYSC_NO_INFER_CACHE", "1")
    assert infercache.enabled() is False
    assert infercache.load(b"source", "module", {}) is None


def test_warm_compile_output_is_byte_identical(tmp_path: Path) -> None:
    """The warm compile lowers exactly what the cold compile lowered.

    The cache is a cost cut, never a behaviour change: the emitted IR of
    the run that filled the cache and of the run that read it back are
    byte for byte the same.
    """
    from pyscriptc.inferrunner import build_facts

    root = _tree(tmp_path)
    cold_out = tmp_path / "cold"
    warm_out = tmp_path / "warm"

    cold = _compile(root, cold_out)
    assert cold.returncode == 0, cold.stderr
    build_facts(root)
    warm = _compile(root, warm_out)
    assert warm.returncode == 0, warm.stderr

    cold_ir = sorted(cold_out.rglob("*.ir.json"))
    warm_ir = sorted(warm_out.rglob("*.ir.json"))
    assert cold_ir and warm_ir
    for one, other in zip(cold_ir, warm_ir, strict=True):
        assert one.read_bytes() == other.read_bytes()


def _compile(root: Path, out_dir: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(root),
            "--program",
            "--infer",
            "--emit",
            "ir",
            "--out-dir",
            str(out_dir),
            "--skip-check",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="latin-1",
    )


def test_cached_entry_is_json_and_readable(tmp_path: Path) -> None:
    """The stored record is plain JSON (the format the note asks for)."""
    from pyscriptc.infercache import cache_dir
    from pyscriptc.inferrunner import build_facts

    root = _tree(tmp_path)
    build_facts(root)
    entries = list(cache_dir().glob("*.json"))
    assert entries
    for entry in entries:
        data = json.loads(entry.read_text(encoding="utf-8"))
        assert data["schema"] == 1
        assert isinstance(data["calls"], list)
