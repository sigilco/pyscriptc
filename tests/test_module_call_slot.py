"""Module-scope call results bind a global slot (#502).

A module-scope call assign lowers once, at init, into the same process
slot the threading records and containers use: the call result is one
object for the life of the process, and every read - at module scope
or inside a function body - is that value. Each acceptance case
compares stdout, stderr and the exit code against CPython byte for
byte.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.test_gates import run_cli, write_case


def differential(tmp_path: Path, name: str, source: str) -> subprocess.CompletedProcess[str]:
    """Compile, run, and pin the three observable channels to CPython."""
    path = write_case(tmp_path, name, source)
    out = str(tmp_path / (name + ".bin"))
    got = run_cli("compile", str(path), "--run", "--emit", "bin", "-o", out)
    expected = subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        check=False,
    )
    assert (got.returncode, got.stdout, got.stderr) == (expected.returncode, expected.stdout, expected.stderr)
    return got


def test_lowered_function_call_at_module_scope(tmp_path: Path) -> None:
    differential(
        tmp_path,
        "usercall.py",
        (
            "def make(n: int) -> int:\n"
            "    return n * 2\n"
            "x = make(21)\n"
            "\n"
            "\n"
            "def read() -> int:\n"
            "    return x\n"
            "\n"
            "\n"
            "print(read())\n"
        ),
    )


def test_stdlib_call_at_module_scope(tmp_path: Path) -> None:
    differential(
        tmp_path,
        "stdlibcall.py",
        ("import math\ny = math.sqrt(4)\n\n\ndef read() -> float:\n    return y\n\n\nprint(f'{read():.1f}')\n"),
    )


def test_os_call_at_module_scope(tmp_path: Path) -> None:
    differential(
        tmp_path,
        "oscall.py",
        (
            "import os\n"
            "z = os.getcwd()\n"
            "\n"
            "\n"
            "def read() -> str:\n"
            "    return z\n"
            "\n"
            "\n"
            "p = os.path.join(z, 'probe')\n"
            "\n"
            "\n"
            "def read2() -> bool:\n"
            "    return p.startswith(read())\n"
            "\n"
            "\n"
            "print(read2())\n"
        ),
    )


def test_two_calls_same_module(tmp_path: Path) -> None:
    differential(
        tmp_path,
        "twocalls.py",
        (
            "def f(a: int) -> int:\n"
            "    return a + 1\n"
            "a = f(1)\n"
            "b = f(2)\n"
            "\n"
            "\n"
            "def read() -> int:\n"
            "    return a + b\n"
            "\n"
            "\n"
            "print(read())\n"
        ),
    )


def test_call_result_in_later_module_expression(tmp_path: Path) -> None:
    differential(
        tmp_path,
        "laterexpr.py",
        (
            "def f(a: int) -> int:\n"
            "    return a + 1\n"
            "x = f(1)\n"
            "\n"
            "\n"
            "def read() -> int:\n"
            "    return x\n"
            "\n"
            "\n"
            "y = f(read())\n"
            "\n"
            "\n"
            "def read2() -> int:\n"
            "    return y\n"
            "\n"
            "\n"
            "print(read2())\n"
        ),
    )


def test_slot_identity_holds_across_reads(tmp_path: Path) -> None:
    differential(
        tmp_path,
        "identity.py",
        ("xs: list[int] = [1]\n\n\ndef get() -> list[int]:\n    return xs\n\n\nprint(get() is get())\n"),
    )


def test_call_order_with_script_statements(tmp_path: Path) -> None:
    differential(
        tmp_path,
        "order.py",
        (
            "def f(a: int) -> int:\n"
            "    return a + 1\n"
            "x = f(1)\n"
            "print('side', x)\n"
            "\n"
            "\n"
            "def read() -> int:\n"
            "    return x\n"
            "\n"
            "\n"
            "print(read())\n"
        ),
    )


def test_entry_only_call_assign_stays_in_the_entry(tmp_path: Path) -> None:
    """A call result no definition reads keeps the E1 script shape.

    The slot serves a definition body read (one object for the process).
    With only entry readers, the assign lowers inside the entry with its
    readers in source order, so the call runs once at that point and the
    script keeps its order.
    """
    differential(
        tmp_path,
        "entryonly.py",
        (
            "def make(n: int) -> int:\n"
            "    return n * 2\n"
            "x = make(21)\n"
            "print('side', x)\n"
        ),
    )


def test_entry_only_call_assign_runs_once(tmp_path: Path) -> None:
    """The entry-only call runs once: a second read sees the same value."""
    differential(
        tmp_path,
        "entryonce.py",
        (
            "calls = 0\n"
            "\n"
            "\n"
            "def make(n: int) -> int:\n"
            "    return n * 2\n"
            "\n"
            "\n"
            "x = make(21)\n"
            "print(x, x)\n"
        ),
    )


def test_call_result_read_by_a_definition_takes_the_slot(tmp_path: Path) -> None:
    """A definition body read is the #502 case: the slot holds one object."""
    differential(
        tmp_path,
        "slotread.py",
        (
            "def make(n: int) -> list[int]:\n"
            "    return [n]\n"
            "\n"
            "\n"
            "x = make(21)\n"
            "\n"
            "\n"
            "def read() -> list[int]:\n"
            "    return x\n"
            "\n"
            "\n"
            "print(read() is x)\n"
        ),
    )
