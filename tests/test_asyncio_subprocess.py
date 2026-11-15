"""asyncio.create_subprocess_exec: the child spawn and the awaited wait.

`await asyncio.create_subprocess_exec(program, *args)` starts the child
and hands back the process record, `await proc.wait()` parks the task
until the child exits and reads its exit code, and `proc.returncode`
reads the same code afterwards. Each sample runs under CPython and
compiled; the compiled stdout must match byte for byte.
"""

from __future__ import annotations

import os
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
