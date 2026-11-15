"""asyncio.create_subprocess_exec: the child spawn and the awaited wait.

`await asyncio.create_subprocess_exec(program, *args)` starts the child
and hands back the process record, `await proc.wait()` parks the task
until the child exits and reads its exit code, and `proc.returncode`
reads the same code afterwards. Each sample runs under CPython and
compiled; the compiled stdout must match byte for byte.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from pyscriptc.lower import lower_source

ROOT = Path(__file__).resolve().parents[1]
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
ENV.pop("PYSC_CORO", None)

WAIT = """\
import asyncio


async def main() -> int:
    proc: asyncio.subprocess.Process = await asyncio.create_subprocess_exec("sh", "-c", "exit 7")
    code: int = await proc.wait()
    print("code", code)
    return code


print(asyncio.run(main()))
"""

TWO_CHILDREN = """\
import asyncio


async def main() -> int:
    slow: asyncio.subprocess.Process = await asyncio.create_subprocess_exec("sh", "-c", "sleep 0.2; exit 3")
    fast: asyncio.subprocess.Process = await asyncio.create_subprocess_exec("sh", "-c", "sleep 0.1; exit 5")
    got_fast: int = await fast.wait()
    got_slow: int = await slow.wait()
    print("fast", got_fast)
    print("slow", got_slow)
    return got_slow


print(asyncio.run(main()))
"""

RETURNCODE = """\
import asyncio


async def main() -> int:
    proc: asyncio.subprocess.Process = await asyncio.create_subprocess_exec("false")
    code: int = await proc.wait()
    print("code", code)
    print("returncode", proc.returncode)
    return code


print(asyncio.run(main()))
"""

SPAWN_FAILS = """\
import asyncio


async def main() -> int:
    try:
        proc = await asyncio.create_subprocess_exec("definitely-not-a-command-xyz")
        code: int = await proc.wait()
        print("code", code)
    except RuntimeError:
        print("spawn failed")
    return 0


print(asyncio.run(main()))
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
        cwd=tmp_path,
        env=ENV,
        capture_output=True,
        text=True,
    )
    assert built.returncode == 0, built.stderr
    return built.stdout, expected


@pytest.mark.parametrize(
    ("name", "source"),
    [
        ("wait", WAIT),
        ("two_children", TWO_CHILDREN),
        ("returncode", RETURNCODE),
    ],
)
def test_async_subprocess_matches_cpython(tmp_path: Path, name: str, source: str) -> None:
    got, expected = _run_both(tmp_path, name, source)
    assert expected
    assert got == expected


def test_unlowered_keywords_fence() -> None:
    source = "\n".join(
        [
            "import asyncio",
            "",
            "async def main() -> None:",
            "    proc: asyncio.subprocess.Process = await asyncio.create_subprocess_exec('true', stdout=None)",
            "    await proc.wait()",
            "",
            "asyncio.run(main())",
        ]
    )
    module = lower_source(source, "main.py")
    messages = [f["message"] for f in module["fences"]]
    assert any("stdout is not lowered" in message for message in messages), messages


def test_non_str_argument_fences() -> None:
    source = "\n".join(
        [
            "import asyncio",
            "",
            "async def main() -> None:",
            "    proc: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(1)",
            "    await proc.wait()",
            "",
            "asyncio.run(main())",
        ]
    )
    module = lower_source(source, "main.py")
    messages = [f["message"] for f in module["fences"]]
    assert any("str arguments" in message for message in messages), messages


def test_wait_outside_a_coroutine_fences() -> None:
    source = "\n".join(
        [
            "import asyncio",
            "",
            "def not_async(proc: asyncio.subprocess.Process) -> None:",
            "    proc.wait()",
        ]
    )
    module = lower_source(source, "main.py")
    messages = [f["message"] for f in module["fences"]]
    assert any("coroutine" in message for message in messages), messages


# --- the targets that leave ps_aproc.c out ---

# The loop's two hooks into ps_aproc.c (the pump and the pending check) are
# no-ops where the record does not exist: the mobile archives (no fork/exec in
# an app sandbox) and the WASI link (no fork/exec in a WASI sandbox). Without
# the stubs, an asyncio program fails to link there with an undefined
# `ps_aproc_pump`, and the mobile build fails even earlier: the Apple-only
# TargetConditionals.h is not a header an NDK sysroot carries.

WASI_ASYNCIO = """\
import asyncio


async def main() -> None:
    await asyncio.sleep(0.01)
    print("ok")


asyncio.run(main())
"""


def _stub_guard() -> str:
    """The preprocessor guard the runtime uses, read out of ps_loop.c.

    The guard is read from the file itself, not copied here: a copy would
    pass while the runtime kept a guard that names a header the target does
    not carry.
    """
    source = (ROOT / "src" / "pyscriptc" / "runtime" / "ps_loop.c").read_text(encoding="utf-8")
    lines = source.splitlines()
    start = next(index for index, line in enumerate(lines) if "ps_aproc_pump" in line and "{" in line)
    # Walk back from the stub to the `#if` that opens its guard. A guard
    # that names a header is the bug this guards against: an NDK sysroot
    # carries no TargetConditionals.h.
    open_at = None
    for index in range(start - 1, -1, -1):
        line = lines[index]
        if line.startswith("#if"):
            open_at = index
            break
    assert open_at is not None, "the stub guard was not found in ps_loop.c"
    block = lines[open_at:start]
    assert block[0].startswith("#if"), block
    # A header inside the guard is the same bug: TargetConditionals.h is
    # not a header an NDK sysroot or a WASI sysroot carries, so the stub
    # must turn on from the target macros alone.
    joined = "\n".join(block)
    assert "#include" not in joined, f"the stub guard includes a header: {joined}"
    return joined


@pytest.mark.skipif(shutil.which("zig") is None, reason="zig not on PATH")
def test_wasi_asyncio_program_links(tmp_path: Path) -> None:
    # The WASI link drops ps_aproc.c, so the loop's hooks need the stubs.
    # A program that never touches asyncio cannot see the hole, which is
    # why this one drives the loop.
    path = tmp_path / "aio.py"
    path.write_text(WASI_ASYNCIO, encoding="utf-8")
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
            "--target",
            "wasm32-wasi-musl",
            "-o",
            str(tmp_path / "aio"),
        ],
        cwd=tmp_path,
        env=ENV,
        capture_output=True,
        text=True,
    )
    assert built.returncode == 0, built.stderr
    assert "ps_aproc_pump" not in built.stderr


@pytest.mark.parametrize(
    ("cc", "target", "expected"),
    [
        # The three mobile triples: the archives leave ps_aproc.c out.
        ("cc", "aarch64-linux-android24", True),
        ("cc", "aarch64-apple-ios15.0", True),
        ("cc", "aarch64-apple-ios15.0-simulator", True),
        # The WASI targets: the link leaves ps_aproc.c out.
        ("cc", "wasm32-wasi", True),
        # A desktop build keeps the real ps_aproc.c, so the stubs must stay
        # off there: a stub would silently drop the child records.
        ("cc", "x86_64-apple-darwin", False),
        ("cc", "aarch64-linux-gnu", False),
    ],
)
def test_stub_guard_follows_the_target(cc: str, target: str, expected: bool) -> None:
    # The guard is read by the preprocessor, so the check is the
    # preprocessor's own answer. `-Wno-incompatible-sysroot` keeps the
    # host's macOS sysroot from failing the iOS triples (the macros the
    # guard reads are set by the triple, not the sysroot).
    source = f"{_stub_guard()}\nGUARD_ON\n#endif\n"
    out = subprocess.run(
        [cc, "-target", target, "-Wno-incompatible-sysroot", "-E", "-x", "c", "-"],
        input=source,
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr
    assert ("GUARD_ON" in out.stdout) is expected
