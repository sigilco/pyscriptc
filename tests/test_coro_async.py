"""Coroutine frames (coro.py): CPython-oracle runs, fences, and IR shape.

Each sample runs under CPython and compiled; the compiled stdout must match
byte for byte. The samples cover the milestone ladder: run + nested await,
sequential awaits, the sleep timer queue, gather interleaving, and gathers
nested inside a coroutine.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pyscriptc.coro import AWAIT_MARKER, RESULT, SLEEP_MARKER, STATE, TASK
from pyscriptc.ir import validate_module
from pyscriptc.lower import lower_source
from pyscriptc.package import lower_directory

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

# Distinct deadlines, one late wake: if the loop sleeps past several deadlines
# at once, they must fire in deadline order (CPython pops a timer heap), not
# in arming order.
TIMER_ORDER = """\
import asyncio


async def leaf(n: int, delay: float) -> int:
    await asyncio.sleep(delay)
    print("leaf", n)
    return n * 10


async def mid(n: int) -> int:
    first: int = await leaf(n, 0.03)
    second: int = await leaf(n + 1, 0.01)
    return first + second


async def root() -> int:
    parts: tuple[int, ...] = await asyncio.gather(mid(1), mid(3), leaf(9, 0.02))
    print("gather [", ",".join(str(p) for p in parts), "]")
    await asyncio.sleep(0.01)
    print("done")
    return parts[0]


run_result: int = asyncio.run(root())
print("result", run_result)
"""

# Coroutine methods (issue #178, stage A): an `async def` in a class body
# lowers like a generator method, a frame class per `Class.method`, and the
# caller awaits the frame the call returns.
METHOD = """\
import asyncio


class Worker:
    async def run(self, n: int) -> int:
        await asyncio.sleep(0.01)
        print("run", n)
        return n + 10


async def main() -> int:
    w: Worker = Worker()
    r: int = await w.run(3)
    print("got", r)
    return r


asyncio.run(main())
"""

METHOD_NESTED = """\
import asyncio


async def helper(x: int) -> int:
    await asyncio.sleep(0.01)
    return x * 2


class Worker:
    async def run(self, n: int) -> int:
        v: int = await helper(n)
        return v + 1

    async def twice(self, n: int) -> int:
        v: int = await self.run(n)
        return v * 10


async def main() -> int:
    w: Worker = Worker()
    a: int = await w.run(3)
    b: int = await w.twice(4)
    print(a, b)
    return a + b


asyncio.run(main())
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
        ("timer_order", TIMER_ORDER),
        ("method", METHOD),
        ("method_nested", METHOD_NESTED),
    ],
)
def test_coroutine_matches_cpython(tmp_path: Path, name: str, source: str) -> None:
    got, expected = _run_both(tmp_path, name, source)
    assert expected
    assert got == expected


def test_equal_deadline_wakes_in_arm_order(tmp_path: Path) -> None:
    """A late wake past several equal deadlines fires them in arm order.

    The loop's nanosleep overshoots by scheduler granularity, so three equal
    0.05s sleeps usually become due in one pass; CPython's stable timer heap
    wakes them armed-first, and a swap-remove in the timer table must not
    reorder ties (regression: gather of equal sleeps woke 1, 3, 2)."""
    got, expected = _run_both(tmp_path, "equal_deadlines", GATHER)
    assert got == expected
    binary = tmp_path / "equal_deadlines"
    assert binary.exists()
    for _ in range(5):
        again = subprocess.run([str(binary)], capture_output=True, text=True, check=True).stdout
        assert again == expected


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


def test_coroutine_method_ir_is_plain_classes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYSC_CORO", raising=False)
    module = lower_source(METHOD_NESTED, "method.py")
    assert module["fences"] == []
    validate_module(module)
    dumped = str(module)
    assert AWAIT_MARKER not in dumped
    frames = {cls["name"]: cls for cls in module["classes"] if cls["name"].startswith("coro.")}
    assert set(frames) == {"coro.helper", "coro.Worker.run", "coro.Worker.twice", "coro.main"}
    for frame in frames.values():
        assert {m["name"] for m in frame["methods"]} == {"resume"}
        names = [field["name"] for field in frame["fields"]]
        # The same fixed layout the run loop reads, function or method frame.
        assert names[:3] == [STATE, TASK, RESULT]
    # A method frame's resume cell names the same module-level forwarder
    # shape a function frame uses, so ps_loop.c cannot tell them apart.
    names = {fn["name"] for fn in module["functions"]}
    assert {"coro.helper.resume", "coro.Worker.run.resume", "coro.Worker.twice.resume", "coro.main.resume"} <= names


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
    assert any(f["message"] == "await is not lowered" for f in module["fences"])


def test_kill_switch_restores_old_method_fence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYSC_CORO", "0")
    module = lower_source(METHOD, "method.py")
    assert any(f["message"] == "AsyncFunctionDef in a class body is not lowered" for f in module["fences"])


# Package-unit coroutines (issue #178, stage B): the coroutine lives in one
# package unit, the await happens in another. The frame class and its result
# type travel through the export table, so the consumer builds a foreign plan.
PKG_MOD = """\
async def value() -> int:
    return 40


async def add(n: int) -> int:
    return n + 2
"""

PKG_USER = """\
import asyncio

from .mod import add, value


async def run() -> int:
    a: int = await value()
    b: int = await add(a)
    print("a", a, "b", b)
    return b


def drive() -> int:
    return asyncio.run(run())
"""

PKG_MAIN = """\
import asyncio

from pkg.user import run

print(asyncio.run(run()))
"""


def _run_both_package(tmp_path: Path, pkg: str, sources: dict[str, str], main: str | None) -> tuple[str, str]:
    root = tmp_path / pkg
    root.mkdir()
    (root / "__init__.py").write_text("", encoding="utf-8")
    for name, source in sources.items():
        (root / f"{name}.py").write_text(source, encoding="utf-8")
    source_root = tmp_path
    if main is not None:
        (source_root / "main.py").write_text(main, encoding="utf-8")
        cpython = source_root
        compile_dir: Path = source_root
    else:
        cpython = source_root / pkg
        compile_dir = root
    expected = subprocess.run(
        [sys.executable, "-m", f"{pkg}.user"] if main is None else [sys.executable, str(source_root / "main.py")],
        cwd=cpython if main is None else cpython,
        capture_output=True,
        text=True,
        check=True,
        env={**ENV, "PYTHONPATH": str(cpython)},
    ).stdout
    built = subprocess.run(
        [sys.executable, "-m", "pyscriptc", "compile", str(compile_dir), "--run", "-o", str(tmp_path / "out")],
        cwd=ROOT,
        env=ENV,
        capture_output=True,
        text=True,
    )
    assert built.returncode == 0, built.stderr
    return built.stdout, expected


def test_package_coroutine_cross_module_await_runs(tmp_path: Path) -> None:
    """Stage B: a coroutine awaited from another package unit matches CPython."""
    got, expected = _run_both_package(tmp_path, "pkg", {"mod": PKG_MOD, "user": PKG_USER}, PKG_MAIN)
    assert expected == "a 40 b 42\n42\n"
    assert got == expected


def test_package_coroutine_library_entry_runs(tmp_path: Path) -> None:
    """The same shape compiled as a library package, driven through --call.

    The `--call` entry is the sync driver: a bare `--call` of a coroutine
    returns the frame, which the process entry cannot print (the honest
    boundary for today; awaiting it is the lowered path)."""
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "__init__.py").write_text("", encoding="utf-8")
    (root / "mod.py").write_text(PKG_MOD, encoding="utf-8")
    (root / "user.py").write_text(PKG_USER, encoding="utf-8")
    built = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(root),
            "--run",
            "--call",
            "pkg.user.drive",
            "-o",
            str(tmp_path / "out"),
        ],
        cwd=ROOT,
        env=ENV,
        capture_output=True,
        text=True,
    )
    assert built.returncode == 0, built.stderr
    assert built.stdout == "a 40 b 42\n42\n"


def test_package_coroutine_ir_carries_foreign_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    """The consumer's cross-module await lowers through the export table.

    The defining module keeps the frame class and the wrapper; the consumer
    only polls. Its await marker names the foreign frame, and the plan the
    consumer builds carries the exported result type (`int` here).
    """
    monkeypatch.delenv("PYSC_CORO", raising=False)
    module, _ = _lower_stageb()
    assert module["fences"] == []
    validate_module(module)
    names = {fn["name"] for fn in module["functions"]}
    assert {"pkg.mod.value", "coro.value.resume", "pkg.user.run", "coro.run.resume"} <= names
    frames = {cls["name"]: cls for cls in module["classes"] if cls["name"].startswith("coro.")}
    assert set(frames) == {"coro.value", "coro.add", "coro.run"}
    # The consumer's await never emits a raw marker into the IR.
    assert AWAIT_MARKER not in json.dumps(module)
    # The awaited foreign frame travels in the resume body as a local typed
    # as the exporting module's frame class, and the result local carries the
    # exported result type.
    resume = next(meth for meth in frames["coro.run"]["methods"] if meth["name"] == "resume")
    locals_ = {local["name"]: local["type"] for local in resume["locals"]}
    assert locals_[".ps_aw_1"] == {"kind": "class", "name": "coro.value"}
    assert locals_[".ps_awv_1"] == {"kind": "int"}


def _lower_stageb():
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "pkg"
        root.mkdir()
        (root / "__init__.py").write_text("", encoding="utf-8")
        (root / "mod.py").write_text(PKG_MOD, encoding="utf-8")
        (root / "user.py").write_text(PKG_USER, encoding="utf-8")
        return lower_directory(root)


def test_method_frame_fences_precisely(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYSC_CORO", raising=False)
    fenced = """\\
import asyncio


class Worker:
    async def run(self, n: int) -> int:
        try:
            await asyncio.sleep(0.01)
        finally:
            print("done")
        return n
"""
    module = lower_source(fenced, "fenced_method.py")
    assert any(f["message"] == "await inside try or with is not lowered yet" for f in module["fences"])
