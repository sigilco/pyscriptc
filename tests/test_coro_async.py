"""Coroutine frames (coro.py): CPython-oracle runs, fences, and IR shape.

Each sample runs under CPython and compiled; the compiled stdout must match
byte for byte. The samples cover the milestone ladder: run + nested await,
sequential awaits, the sleep timer queue, gather interleaving, and gathers
nested inside a coroutine.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from pyscriptc.coro import AWAIT_MARKER, RESULT, SLEEP_MARKER, STATE, TASK
from pyscriptc.ir import validate_module
from pyscriptc.lower import lower_source

ROOT = Path(__file__).resolve().parents[1]
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
ENV.pop("PYSC_CORO", None)

NESTED = """\
import asyncio


async def inner() -> int:
    return 7


async def outer() -> int:
    v: int = await inner()
    return v + 1


async def root() -> int:
    a: int = await outer()
    b: int = await inner()
    print("a", a, "b", b)
    return a * b


print(asyncio.run(root()))
"""

SEQUENTIAL = """\
import asyncio


async def one() -> int:
    return 1


async def two() -> int:
    return 2


async def root() -> int:
    x: int = await one()
    y: int = await two()
    print("x", x, "y", y)
    return x + y


print(asyncio.run(root()))
"""

SLEEP = """\
import asyncio


async def tick(name: str, delay: float) -> str:
    await asyncio.sleep(delay)
    print(name)
    return name


async def root() -> str:
    a: str = await tick("first", 0.03)
    b: str = await tick("second", 0.01)
    print("done", a, b)
    return b


print(asyncio.run(root()))
"""

GATHER = """\
import asyncio


async def work(n: int) -> int:
    print("start", n)
    await asyncio.sleep(0.05)
    print("wake", n)
    return n * 10


async def root() -> int:
    r: list[int] = await asyncio.gather(work(1), work(2), work(3))
    print("gather", r[0], r[1], r[2])
    return r[0] + r[1] + r[2]


print("sum", asyncio.run(root()))
"""

MIXED = """\
import asyncio


async def tick(name: str, delay: float) -> str:
    await asyncio.sleep(delay)
    print(name)
    return name


async def chain() -> int:
    a: str = await tick("first", 0.03)
    b: str = await tick("second", 0.01)
    both: list[str] = await asyncio.gather(tick("t1", 0.04), tick("t2", 0.02))
    print("both", both[0], both[1])
    return len(a) + len(b)


async def immediate() -> int:
    return 42


async def root() -> int:
    n: int = await chain()
    m: int = await immediate()
    print("n", n, "m", m)
    return n + m


print("result", asyncio.run(root()))
"""

CONDITIONAL = """\
import asyncio


async def small(n: int) -> int:
    if n < 0:
        await asyncio.sleep(0.01)
        return -n
    return n


async def root() -> int:
    total: int = 0
    i: int = 0
    while i < 4:
        v: int = await small(i - 1)
        total += v
        i += 1
    if total > 0:
        total += await small(100)
    print("total", total)
    return total


print(asyncio.run(root()))
"""

FENCED = """\
import asyncio


async def no_result():
    return 1


async def in_try() -> int:
    try:
        await asyncio.sleep(0.01)
    except ValueError:
        pass
    return 1


def plain_await() -> int:
    return 1


def demo() -> None:
    asyncio.sleep(0.01)
"""


def _run_both(tmp_path: Path, name: str, source: str) -> tuple[str, str]:
    path = tmp_path / f"{name}.py"
    path.write_text(source, encoding="utf-8")
    expected = subprocess.run([sys.executable, str(path)], capture_output=True, text=True, check=True).stdout
    built = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(path),
            "--skip-check",
            "--emit",
            "bin",
            "-o",
            str(tmp_path / name),
            "--run",
        ],
        cwd=ROOT,
        env=ENV,
        capture_output=True,
        text=True,
    )
    assert built.returncode == 0, built.stderr
    return built.stdout, expected


@pytest.mark.parametrize(
    ("name", "source"),
    [
        ("nested", NESTED),
        ("sequential", SEQUENTIAL),
        ("sleep", SLEEP),
        ("gather", GATHER),
        ("mixed", MIXED),
        ("conditional", CONDITIONAL),
    ],
)
def test_coroutine_matches_cpython(tmp_path: Path, name: str, source: str) -> None:
    got, expected = _run_both(tmp_path, name, source)
    assert expected
    assert got == expected


def test_coroutine_ir_is_plain_classes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYSC_CORO", raising=False)
    module = lower_source(NESTED, "nested.py")
    assert module["fences"] == []
    validate_module(module)
    dumped = str(module)
    assert AWAIT_MARKER not in dumped
    assert SLEEP_MARKER not in dumped
    frames = {cls["name"]: cls for cls in module["classes"] if cls["name"].startswith("coro.")}
    assert set(frames) == {"coro.inner", "coro.outer", "coro.root"}
    for frame in frames.values():
        assert {m["name"] for m in frame["methods"]} == {"resume"}
        names = [field["name"] for field in frame["fields"]]
        # The run loop reads the task cell at a fixed offset: state first,
        # task second, result third.
        assert names[:3] == [STATE, TASK, RESULT]
    # The forwarder the callable cell names is a module function.
    names = {fn["name"] for fn in module["functions"]}
    assert {"coro.inner.resume", "coro.outer.resume", "coro.root.resume"} <= names


def test_module_without_coroutines_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    source = "def f(n: int) -> int:\n    return n + 1\n\n\nprint(f(2))\n"
    monkeypatch.setenv("PYSC_CORO", "0")
    off = lower_source(source, "plain.py")
    monkeypatch.delenv("PYSC_CORO")
    assert lower_source(source, "plain.py") == off


def test_unsupported_shapes_fence_precisely(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYSC_CORO", raising=False)
    module = lower_source(FENCED, "fenced.py")
    got = sorted((f["span"]["line"], f["message"]) for f in module["fences"])
    assert got == [
        (4, "no_result needs a return annotation"),
        (10, "await inside try or with is not lowered yet"),
        (21, "asyncio.sleep is only lowered inside a coroutine"),
    ]
    assert {f["code"] for f in module["fences"]} <= {"PS1001", "PS2002"}


def test_kill_switch_restores_old_fence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYSC_CORO", "0")
    module = lower_source(NESTED, "nested.py")
    assert any(f["message"] == "Await is not lowered" for f in module["fences"])
