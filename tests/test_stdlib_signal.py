# SPDX-License-Identifier: MIT
"""signal: the constants and the native handler surface (issues #1244, #1347).

The module carries the six POSIX constants the watchdog surface needs
(`SIGKILL`, `SIGTERM`, `SIGINT`, `SIGCONT`, `SIGUSR1`, `SIGUSR2`); they lower
as module constants, so `os.kill(pid, signal.SIGTERM)` takes them as plain
ints.

`signal.signal(signum, handler)` lowers the native handler surface (#1347): the
runtime installs a C-level `sigaction` handler that only records the signal
number, and the compiled handler runs at a safepoint, outside the signal
context. `SIG_IGN` and `SIG_DFL` install the two dispositions.

The rest of the registration API stays fenced with a named code: a native
binary has no Python handler table to read back from.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import compile_run, oracle, run_cli, write_case

_CONSTANTS = """
import signal

def run() -> int:
    print(signal.SIGKILL > 0)
    print(signal.SIGTERM > 0)
    print(signal.SIGINT > 0)
    print(signal.SIGCONT > 0)
    print(signal.SIGUSR1 > 0)
    print(signal.SIGUSR2 > 0)
    return 0
"""

_HANDLER = """
import os
import signal

def handler(signum: int) -> None:
    print("caught", signum)

def run() -> int:
    signal.signal(signal.SIGTERM, handler)
    print("installed")
    os.kill(os.getpid(), signal.SIGTERM)
    print("after-kill")
    return 0
"""

_DISPOSITIONS = """
import os
import signal

def handler(signum: int) -> None:
    print("caught", signum)

def run() -> int:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, handler)
    print("installed")
    os.kill(os.getpid(), signal.SIGTERM)
    os.kill(os.getpid(), signal.SIGINT)
    print("after-kill")
    return 0
"""


def test_signal_constants_match_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "constants.py", _CONSTANTS)
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


def test_signal_constants_carry_the_kernel_numbers(tmp_path: Path) -> None:
    source = (
        "import signal\n"
        "\n"
        "def run() -> int:\n"
        "    print(signal.SIGTERM == 15)\n"
        "    print(signal.SIGKILL == 9)\n"
        "    print(signal.SIGINT == 2)\n"
        "    return 0\n"
    )
    path = write_case(tmp_path, "numbers.py", source)
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    # The `--run` harness prints the return value too, so the oracle (which
    # prints the value the same way) is the comparison.
    assert got.stdout == oracle(path, "run")
    assert "True" in got.stdout


def test_signal_handler_runs_at_a_safepoint(tmp_path: Path) -> None:
    """The handler runs between statements, not inside the signal context."""
    path = write_case(tmp_path, "handler.py", _HANDLER)
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert "caught 15" in got.stdout
    # The deferred delivery: the handler runs at the safepoint after the
    # kill, so its line comes after "installed" and before "after-kill".
    lines = [line for line in got.stdout.splitlines() if line]
    assert lines.index("caught 15") > lines.index("installed")
    assert lines.index("caught 15") < lines.index("after-kill")


def test_signal_dispositions_install(tmp_path: Path) -> None:
    """`SIG_IGN` ignores the signal; the handler still runs for SIGINT."""
    path = write_case(tmp_path, "dispositions.py", _DISPOSITIONS)
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert "caught 2" in got.stdout
    # SIGTERM was ignored, so no handler ran for it.
    assert "caught 15" not in got.stdout


def test_signal_handler_forms_fence(tmp_path: Path) -> None:
    cases = [
        ("getsignal.py", "signal.getsignal(signal.SIGINT)", "no handler table"),
        ("alarm.py", "signal.alarm(5)", "no handler table"),
        ("raise_signal.py", "signal.raise_signal(signal.SIGCONT)", "use os.kill"),
    ]
    for name, expr, text in cases:
        source = (
            "import signal\n"
            "\n"
            "def handler(signum: int, frame: int) -> None:\n"
            "    print(signum)\n"
            "\n"
            "def run() -> int:\n"
            f"    {expr}\n"
            "    return 0\n"
        )
        path = write_case(tmp_path, name, source)
        got = run_cli(
            "compile",
            str(path),
            "--skip-check",
            "--emit",
            "llvm",
            "-o",
            str(tmp_path / f"{name}.ll"),
        )
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert "PS2001" in got.stderr, f"{name}\n{got.stderr}"
        assert text in got.stderr, f"{name}\n{got.stderr}"


def test_signal_handler_value_forms_fence(tmp_path: Path) -> None:
    cases = [
        ("enum.py", "signal.Signals.SIGKILL"),
    ]
    for name, expr in cases:
        source = f"import signal\n\ndef run() -> int:\n    print({expr})\n    return 0\n"
        path = write_case(tmp_path, name, source)
        got = run_cli(
            "compile",
            str(path),
            "--skip-check",
            "--emit",
            "llvm",
            "-o",
            str(tmp_path / f"{name}.ll"),
        )
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert "PS2001" in got.stderr, f"{name}\n{got.stderr}"


def test_signal_disposition_values_match_cpython(tmp_path: Path) -> None:
    """`SIG_DFL` is 0 and `SIG_IGN` is 1, the values CPython prints."""
    source = "import signal\n\ndef run() -> int:\n    print(signal.SIG_DFL)\n    print(signal.SIG_IGN)\n    return 0\n"
    path = write_case(tmp_path, "disposition_values.py", source)
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")
    assert "0" in got.stdout
    assert "1" in got.stdout


def test_signal_handler_shape_fences(tmp_path: Path) -> None:
    """A handler that does not take the signal number fences."""
    cases = [
        ("noargs.py", "def handler() -> None:\n    print(1)"),
        ("wrongtype.py", "def handler(signum: str) -> None:\n    print(signum)"),
        ("returns.py", "def handler(signum: int) -> int:\n    return signum"),
    ]
    for name, handler in cases:
        source = (
            "import signal\n"
            "\n"
            f"{handler}\n"
            "\n"
            "def run() -> int:\n"
            "    signal.signal(signal.SIGTERM, handler)\n"
            "    return 0\n"
        )
        path = write_case(tmp_path, name, source)
        got = run_cli(
            "compile",
            str(path),
            "--skip-check",
            "--emit",
            "llvm",
            "-o",
            str(tmp_path / f"{name}.ll"),
        )
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert "PS1003" in got.stderr, f"{name}\n{got.stderr}"
