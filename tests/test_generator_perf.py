"""The generator performance receipt: the try unwind and the env packing.

Two things in the generator path were slow enough to change what a loop
costs, and both are regressions this file guards:

- POSIX `setjmp` saves the signal mask on every call, which cost about half
  a microsecond per try. A generator loop over ten million items spent five
  seconds in the mask save alone.
- The try outline's env array carried every local of the enclosing function,
  which kept every local address-taken and blocked register promotion.

The tests here measure the shape, not the wall clock: a timing test is
flaky on a shared runner, so the receipt tool does the timing and these
tests check that the code paths it depends on are the fast ones.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
ENV.pop("PYSC_CORO", None)


def _compile(source: str, tmp_path: Path, name: str = "prog") -> Path:
    path = tmp_path / f"{name}.py"
    path.write_text(source, encoding="utf-8")
    binary = tmp_path / name
    done = subprocess.run(
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
            str(binary),
        ],
        cwd=ROOT,
        env=ENV,
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr
    return binary


def _llvm_text(path: Path, tmp_path: Path) -> str:
    """The LLVM text the compiler emits for a source file."""
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(path),
            "--skip-check",
            "--emit",
            "llvm",
            "--out-dir",
            str(tmp_path),
        ],
        cwd=ROOT,
        env=ENV,
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr
    return (tmp_path / f"{path.stem}.ll").read_text(encoding="utf-8")


def test_the_try_unwind_does_not_save_the_signal_mask() -> None:
    """The runtime installs its jump buffer with `sigsetjmp(buf, 0)`.

    POSIX `setjmp` saves the signal mask, which costs about half a
    microsecond per call. The runtime never blocks signals around an
    unwind, so the mask save buys nothing and dominated every try body.
    """
    runtime = (ROOT / "src" / "pyscriptc" / "runtime" / "ps_rt.c").read_text(encoding="utf-8")
    assert "sigsetjmp(slot->buf, 0)" in runtime
    assert "siglongjmp(ps_top->buf" in runtime
    # A bare `setjmp` in the unwind path would bring the mask save back.
    body = runtime[runtime.index("int32_t ps_try_call") :]
    assert "= setjmp(" not in body


def test_the_jump_slot_comes_from_a_free_list() -> None:
    """A try runs once per iteration in hot code, so the slot is reused."""
    runtime = (ROOT / "src" / "pyscriptc" / "runtime" / "ps_rt.c").read_text(encoding="utf-8")
    assert "ps_jmp_free" in runtime
    body = runtime[runtime.index("int32_t ps_try_call") :]
    assert "malloc(sizeof(PsJmp))" in body
    # The malloc is the cold path: the slot comes off the free list first.
    assert body.index("ps_jmp_free") < body.index("malloc(sizeof(PsJmp))")


def test_a_long_generator_loop_does_not_overflow_the_stack(tmp_path: Path) -> None:
    """The try outline packs its env into an alloca.

    An alloca in a nested block is a stack allocation on every dynamic
    execution of that block, so a loop that runs the outline once per
    iteration grew the stack until it overflowed. The emitter hoists those
    allocas into the entry block now.
    """
    source = (
        "from typing import Iterator\n"
        "\n"
        "\n"
        "def gen(n: int) -> Iterator[int]:\n"
        "    i: int = 0\n"
        "    while i < n:\n"
        "        yield i\n"
        "        i += 1\n"
        "\n"
        "\n"
        "def main() -> None:\n"
        "    total: int = 0\n"
        "    for v in gen(400000):\n"
        "        total += v\n"
        "    print(total)\n"
        "\n"
        "\n"
        "main()\n"
    )
    binary = _compile(source, tmp_path, "long_loop")
    done = subprocess.run([str(binary)], capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == str(400000 * 399999 // 2)


def test_the_env_array_carries_only_the_slots_the_body_touches(tmp_path: Path) -> None:
    """The env array of a try outline holds one pointer per touched slot.

    Every packed slot keeps its caller storage address-taken, which blocks
    register promotion for the whole enclosing function. A function with a
    try and many unrelated locals must not pack the unrelated ones: the
    env array is one pointer per slot, plus the return slot.
    """
    source = (
        "def main() -> None:\n"
        "    total: int = 0\n"
        "    a: int = 1\n"
        "    b: int = 2\n"
        "    c: int = 3\n"
        "    d: int = 4\n"
        "    e: int = 5\n"
        "    f: int = 6\n"
        "    g: int = 7\n"
        "    h: int = 8\n"
        "    i: int = 9\n"
        "    try:\n"
        "        total = a + b\n"
        "    except ValueError:\n"
        "        total = c\n"
        "    print(total + d + e + f + g + h + i)\n"
        "\n"
        "\n"
        "main()\n"
    )
    path = tmp_path / "env_pack.py"
    path.write_text(source, encoding="utf-8")
    binary = _compile(source, tmp_path, "env_pack")
    done = subprocess.run([str(binary)], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    expected = subprocess.run(
        [sys.executable, str(path)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert done.stdout.strip() == expected.strip()

    # The body reads `a` and `b` and writes `total`: three slots and
    # nothing else. The nine untouched locals must not be packed.
    text = _llvm_text(path, tmp_path)
    arrays = re.findall(r"alloca \[(\d+) x ptr\]", text)
    assert arrays == ["3"], arrays
    slots = re.findall(r"store ptr %t\d+, ptr %t\d+\n", text)
    assert len(slots) == 3, text


def test_the_receipt_tool_reports_a_ratio() -> None:
    """The receipt tool times both programs and compares the medians."""
    done = subprocess.run(
        [sys.executable, "-m", "benchmarks.generators.run", "--runs", "1"],
        cwd=ROOT,
        env=ENV,
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert done.returncode == 0, done.stderr
    assert "while loop" in done.stdout
    assert "generator" in done.stdout
    assert "ratio" in done.stdout
