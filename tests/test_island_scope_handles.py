"""Island module handles across lowering scopes (issue #702).

`_island_module_handle` caches one hidden import local per module for the
whole compile, but the hidden local belongs to one function scope. A nested
def whose body makes an island call after the outer body already made one
used to re-enter the cache, return the outer scope's handle local, and crash
with `KeyError: '_ps_imod_1'` at `_stdlib.py` (compile time, no runtime
needed). The closure scope now swaps the island bookkeeping with the rest of
`_FN_STATE`, owns and frees its own handles, and a cache entry whose local
is not in the active `locals` is stale and rebinds.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from pyscriptc.ir import validate_module
from pyscriptc.lower import lower_source
from pyscriptc.stdlib_queue import island_policy

ROOT = Path(__file__).resolve().parents[1]

NESTED_ISLAND = """\
import multiprocessing


def outer() -> None:
    ctx = multiprocessing.get_context("fork")

    def inner() -> None:
        ctx2 = multiprocessing.get_context("fork")

    inner()


outer()
"""


def _lower(source: str) -> dict:
    with island_policy(["multiprocessing"], default=True):
        return lower_source(source, "nest.py")


def test_nested_def_island_call_after_outer_island_call_lowers() -> None:
    """The #702 repro: no `KeyError`, no fences, and the nested def lowers."""
    module = _lower(NESTED_ISLAND)
    assert module["fences"] == []
    validate_module(module)
    frames = {fn["name"]: fn for fn in module["functions"]}
    assert {"outer", "outer.inner"} <= set(frames)
    # Each scope owns its own hidden import local: distinct temps, no
    # cross-scope reference.
    assert "_ps_imod_1" in [item["name"] for item in frames["outer"]["locals"]]
    assert "_ps_imod_2" in [item["name"] for item in frames["outer.inner"]["locals"]]


def test_nested_scope_frees_only_its_own_island_handles() -> None:
    """The parent's scope-exit free list never mentions the closure's handles."""
    module = _lower(NESTED_ISLAND)
    frames = {fn["name"]: fn for fn in module["functions"]}

    def frees(body: list[dict]) -> list[str]:
        out: list[str] = []
        for stmt in body:
            if stmt["kind"] == "try":
                for item in stmt.get("finalbody", []):
                    expr = item.get("expr", {})
                    if expr.get("symbol") == "ps_island_free":
                        out.append(expr["args"][0]["id"])
                out.extend(frees(stmt["body"]))
        return out

    assert sorted(frees(frames["outer"]["body"])) == ["_ps_imod_1", "ctx"]
    assert sorted(frees(frames["outer.inner"]["body"])) == ["_ps_imod_2", "ctx2"]


def test_plain_scope_island_call_still_lowers() -> None:
    """The control: plain `mp.get_context` in one scope lowers as before."""
    module = _lower(
        """\
import multiprocessing


def main() -> None:
    ctx = multiprocessing.get_context("fork")


main()
"""
    )
    assert module["fences"] == []
    validate_module(module)


def test_nested_def_island_call_compiles_cli(tmp_path: Path) -> None:
    """The compile CLI produces a binary for the #702 repro."""
    (tmp_path / "pyscriptc.toml").write_text('[imports]\nmultiprocessing = "island"\n', encoding="utf-8")
    src = tmp_path / "nest.py"
    src.write_text(NESTED_ISLAND, encoding="utf-8")
    binary = tmp_path / "nest_bin"
    comp = subprocess.run(
        [sys.executable, "-m", "pyscriptc", "compile", str(src), "--emit", "bin", "-o", str(binary)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert comp.returncode == 0, comp.stderr
    assert binary.exists()


NESTED_ISLAND_RUNTIME = """\
import multiprocessing


def outer() -> None:
    ctx = multiprocessing.get_context("fork")

    def inner() -> int:
        inner_ctx = multiprocessing.get_context("fork")
        pool = inner_ctx.Pool(2)
        got: int = pool.apply(work, (2,))
        pool.close()
        pool.join()
        return got

    got: int = inner()
    print(got)


def work(n: int) -> int:
    return n * n


if __name__ == "__main__":
    outer()
"""


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_nested_def_island_pool_matches_cpython(tmp_path: Path) -> None:
    """The #702 repro runs and matches CPython (fork Pool inside a nested def).

    The macOS host cannot run this: multiprocessing's POSIX semaphore pool
    is exhausted there, so the case is `linux_container` and runs through
    `HEAVY=1 docker/run.sh`.
    """
    (tmp_path / "pyscriptc.toml").write_text('[imports]\nmultiprocessing = "island"\n', encoding="utf-8")
    src = tmp_path / "nest_pool.py"
    src.write_text(NESTED_ISLAND_RUNTIME, encoding="utf-8")
    binary = tmp_path / "nest_pool_bin"
    comp = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(src),
            "--fallback=cpython",
            "--emit",
            "bin",
            "-o",
            str(binary),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert comp.returncode == 0, comp.stderr
    got = subprocess.run([str(binary)], capture_output=True, text=True, timeout=120)
    want = subprocess.run([sys.executable, str(src)], capture_output=True, text=True, timeout=120)
    assert got.returncode == want.returncode == 0, (got.returncode, got.stdout, got.stderr, want.stderr)
    assert got.stdout == want.stdout == "4\n"
