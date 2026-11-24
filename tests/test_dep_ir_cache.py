# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: F811
"""Issue #416: the pure-dependency IR cache.

A dependency module's lowered IR is keyed by its wheel sha256, the compiler
(version plus code digest) and the target triple. A hit skips the lowering of
the dependency's modules and reuses the cached IR; a compiler change, a new
wheel or a new target misses. The deps report and the audit events say which.

uv and the wheel downloads are faked (the `fake` fixture of
`test_deps_compile`), so nothing reaches the network.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from tests.test_deps_compile import (  # noqa: F401
    PURE_WHEEL,
    SCRIPT,
    _pylock,
    _script,
    _wheel_entry,
    fake,
    make_wheel,
    sha,
)

import pyscriptc
from pyscriptc import cli, depircache, deps, package
from pyscriptc.lower import lower_file

pytestmark = pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib")


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """The `deps.ir_cache` audit events of the test, in order."""
    seen: list[dict[str, Any]] = []

    def capture(level: str, event: str, fields: dict[str, Any]) -> None:
        if event == "deps.ir_cache":
            seen.append(dict(fields))

    monkeypatch.setattr(depircache, "emit_event", capture)
    return seen


@pytest.fixture
def dep_lowerings(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The source files the real lowerer was constructed for (unit lowerings)."""
    built: list[str] = []
    real = package.Lowerer

    def counting(path: str, *args: Any, **kwargs: Any) -> Any:
        built.append(path)
        return real(path, *args, **kwargs)

    monkeypatch.setattr(package, "Lowerer", counting)
    return built


def _lower(script: Path, **kwargs: Any) -> dict[str, Any]:
    roots = deps.source_roots(script)
    module, _text = lower_file(str(script), dep_roots=roots, **kwargs)
    return module


def _dep_files(built: list[str]) -> list[str]:
    return [path for path in built if "/src/" in path and "tinydep" in path]


def test_the_second_compile_hits_and_skips_the_dependency_lowering(
    fake: dict[str, Any], tmp_path: Path, events: list[dict[str, Any]], dep_lowerings: list[str]
) -> None:
    script = _script(tmp_path)
    cold = _lower(script)
    assert _dep_files(dep_lowerings), "the cold compile lowers the dependency modules"
    assert events[-1]["state"] == "miss"
    assert events[-1]["hits"] == 0 and events[-1]["misses"] > 0 and events[-1]["stored"] > 0
    assert events[-1]["imports"] == ["tinydep"]

    dep_lowerings.clear()
    warm = _lower(script)
    assert _dep_files(dep_lowerings) == [], "a hit lowers no dependency module"
    assert events[-1]["state"] == "hit"
    assert events[-1]["hits"] > 0 and events[-1]["misses"] == 0
    assert warm == cold, "the cached IR is the IR a fresh lowering produces"


def test_the_cache_key_is_wheel_sha_compiler_and_target(
    fake: dict[str, Any], tmp_path: Path, events: list[dict[str, Any]]
) -> None:
    script = _script(tmp_path)
    _lower(script)
    wheel = sha(PURE_WHEEL)
    key = depircache.compiler_key(depircache.target_triple(None))
    base = tmp_path / "cache" / "depir"
    assert (base / wheel / key).is_dir()
    assert list((base / wheel / key).glob("ir-*.json"))
    assert list((base / wheel / key).glob("cat-*.json"))
    assert events[-1]["sha256"] == wheel and events[-1]["key"] == key


def test_a_compiler_version_change_misses_and_drops_the_old_entries(
    fake: dict[str, Any], tmp_path: Path, events: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _script(tmp_path)
    _lower(script)
    _lower(script)
    assert events[-1]["state"] == "hit"
    old_key = events[-1]["key"]

    monkeypatch.setattr(pyscriptc, "__version__", "99.0.0-test")
    _lower(script)
    assert events[-1]["state"] == "miss", "a new compiler never serves the old compiler's IR"
    assert events[-1]["key"] != old_key
    wheel_dir = tmp_path / "cache" / "depir" / sha(PURE_WHEEL)
    assert not (wheel_dir / old_key).exists(), "the stale compiler's entries are dropped"
    _lower(script)
    assert events[-1]["state"] == "hit"


def test_a_new_wheel_hash_misses(fake: dict[str, Any], tmp_path: Path, events: list[dict[str, Any]]) -> None:
    script = _script(tmp_path)
    _lower(script)
    _lower(script)
    assert events[-1]["state"] == "hit"

    newer = make_wheel(
        "tinydep",
        {
            "tinydep/__init__.py": b"from .core import greet\n\nVERSION = '1.1'\n",
            "tinydep/core.py": b"def greet(name: str) -> str:\n    return 'hello ' + name\n",
        },
    )
    fake["lock"] = _pylock([_wheel_entry("tinydep", "1.1", "tinydep-1.1-py3-none-any.whl", sha(newer))])
    fake["blobs"]["tinydep-1.1-py3-none-any.whl"] = newer
    # the script's lock file pins the old wheel: drop it so uv re-resolves.
    for lock in tmp_path.glob("pylock.*.toml"):
        lock.unlink()
    module = _lower(script, target=None)
    assert events[-1]["state"] == "miss"
    assert events[-1]["sha256"] == sha(newer)
    assert any("hello" in json.dumps(fn) for fn in module["functions"])


def test_a_different_target_misses(fake: dict[str, Any], tmp_path: Path, events: list[dict[str, Any]]) -> None:
    script = _script(tmp_path)
    _lower(script)
    _lower(script)
    assert events[-1]["state"] == "hit"
    _lower(script, target="x86_64-unknown-linux-gnu")
    assert events[-1]["state"] == "miss"
    assert events[-1]["triple"] == "x86_64-unknown-linux-gnu"


def test_a_program_that_changes_what_a_dependency_reads_misses_and_stays_correct(
    fake: dict[str, Any], tmp_path: Path, events: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The context check: another program with the same wheel never serves the wrong IR."""
    first = _script(tmp_path)
    _lower(first)
    other = tmp_path / "other.py"
    other.write_text(
        SCRIPT.replace("print(tinydep.VERSION)", "class Boom(Exception):\n    pass\n\nprint(tinydep.VERSION)"),
        encoding="utf-8",
    )
    cached = _lower(other)
    monkeypatch.setenv("PYSC_NO_DEP_IR_CACHE", "1")
    fresh = _lower(other)
    assert cached == fresh


def test_the_bypass_variable_skips_the_cache(
    fake: dict[str, Any], tmp_path: Path, events: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYSC_NO_DEP_IR_CACHE", "1")
    script = _script(tmp_path)
    _lower(script)
    _lower(script)
    assert events == []
    assert not (tmp_path / "cache" / "depir").exists()


def test_deps_json_reports_the_cache_size_hit_rate_and_state(
    fake: dict[str, Any], tmp_path: Path, events: list[dict[str, Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    script = _script(tmp_path)
    before = deps.run_deps(script)["irCache"]
    assert before["entries"] == 0 and before["hitRate"] is None
    pure_row = next(p for p in deps.run_deps(script)["packages"] if p["lane"] == "pure")
    assert pure_row["ir_cache"] == "miss"

    _lower(script)
    _lower(script)
    capsys.readouterr()
    assert cli.main(["deps", str(script), "--json"]) == 3  # the fixture also locks a native wheel
    report = json.loads(capsys.readouterr().out)
    block = report["irCache"]
    assert block["entries"] > 0 and block["bytes"] > 0
    assert block["hits"] > 0 and block["misses"] > 0
    assert block["hitRate"] == round(block["hits"] / (block["hits"] + block["misses"]), 4)
    assert block["compiles"] == 2
    row = next(p for p in report["packages"] if p["lane"] == "pure")
    assert row["ir_cache"] == "hit" and row["ir_modules"] > 0

    text = deps.render_text(report)
    assert "ir cache: enabled" in text and "tinydep 1.0: hit" in text


def test_the_wheel_root_is_the_identity(fake: dict[str, Any], tmp_path: Path) -> None:
    (root,) = deps.source_roots(_script(tmp_path))
    found = depircache.wheel_sha(root / "tinydep" / "core.py")
    assert found == (sha(PURE_WHEEL), root.resolve())
    assert depircache.wheel_sha(tmp_path / "tool.py") is None
