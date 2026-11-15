# SPDX-License-Identifier: MIT
"""signal: the constants-only closed subset (issue #1244).

The module carries the six POSIX constants the watchdog surface needs
(`SIGKILL`, `SIGTERM`, `SIGINT`, `SIGCONT`, `SIGUSR1`, `SIGUSR2`); they lower
as module constants, so `os.kill(pid, signal.SIGTERM)` takes them as plain
ints. The handler-registration API stays fenced with a named code: a native
binary has no Python handler table to register into.
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


def test_signal_handler_forms_fence(tmp_path: Path) -> None:
    cases = [
        ("register.py", "signal.signal(signal.SIGINT, handler)", "no handler table"),
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
        ("dfl.py", "signal.SIG_DFL"),
        ("ign.py", "signal.SIG_IGN"),
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
