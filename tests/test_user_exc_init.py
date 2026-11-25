# SPDX-License-Identifier: Apache-2.0
"""A user exception class runs its own `__init__` on raise (issue #448, O4c).

`raise C(args)` over a user exception class builds the class instance
(the emitter allocates the record and runs the user's `__init__`) and
the runtime record carries that instance as its payload. `except C as e`
then reads the custom fields back off it, and a `raise e` over a bound
handler value re-raises the record as it is.

The class's own constructor arguments are the ones `__init__` takes:
the builtin exception's one-scalar arity does not apply to a user
class. A class without its own `__init__` inherits the builtin base's
arity and its fields read their zero value.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.test_gates import compile_run, oracle, run_cli, write_case

from pyscriptc.lower import lower_source


def _fences(source: str) -> list[dict[str, str]]:
    return lower_source(source, "case.py")["fences"]


def _oracle(tmp_path: Path, name: str, source: str) -> None:
    path = write_case(tmp_path, name, source)
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run"), got.stdout


def test_the_init_runs_and_the_fields_ride_the_exception(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "app_error.py",
        "\n".join(
            [
                "class AppError(Exception):",
                "    code: int",
                "    tag: str",
                "",
                "    def __init__(self, code: int, tag: str) -> None:",
                "        self.code = code",
                "        self.tag = tag",
                "",
                "def run() -> int:",
                "    try:",
                '        raise AppError(7, "seven")',
                "    except AppError as e:",
                "        print(e.code, e.tag)",
                "    return 0",
                "",
            ]
        ),
    )


def test_the_raise_inside_a_function_carries_the_fields(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "boom.py",
        "\n".join(
            [
                "class AppError(Exception):",
                "    code: int",
                "    tag: str",
                "",
                "    def __init__(self, code: int, tag: str) -> None:",
                "        self.code = code",
                "        self.tag = tag",
                "",
                "def boom(n: int) -> None:",
                '    raise AppError(n, "boom")',
                "",
                "def run() -> int:",
                "    try:",
                "        boom(9)",
                "    except AppError as e:",
                "        print(e.code, e.tag)",
                "    return 0",
                "",
            ]
        ),
    )


def test_a_chained_init_keeps_the_base_message(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "chained.py",
        "\n".join(
            [
                "class Chained(Exception):",
                "    code: int",
                "",
                "    def __init__(self, code: int) -> None:",
                '        super().__init__("chained")',
                "        self.code = code",
                "",
                "def run() -> int:",
                "    try:",
                "        raise Chained(3)",
                "    except Chained as e:",
                "        print(e.code, str(e))",
                "    return 0",
                "",
            ]
        ),
    )


def test_a_class_without_its_own_init_inherits_the_builtin_arity(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "plain.py",
        "\n".join(
            [
                "class Plain(Exception):",
                "    code: int = 0",
                "",
                "def run() -> int:",
                "    try:",
                "        raise Plain()",
                "    except Plain as e:",
                "        print(e.code)",
                "    return 0",
                "",
            ]
        ),
    )


def test_a_subclass_reads_the_base_init_field(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "sub.py",
        "\n".join(
            [
                "class Base(Exception):",
                "    code: int",
                "",
                "    def __init__(self, code: int) -> None:",
                "        self.code = code",
                "",
                "class Sub(Base):",
                '    tag: str = "sub"',
                "",
                "def run() -> int:",
                "    try:",
                "        raise Sub(5)",
                "    except Sub as e:",
                "        print(e.code, e.tag)",
                "    return 0",
                "",
            ]
        ),
    )


def test_a_reraise_over_a_bound_value_keeps_the_payload(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "reraise.py",
        "\n".join(
            [
                "class AppError(Exception):",
                "    code: int",
                "    tag: str",
                "",
                "    def __init__(self, code: int, tag: str) -> None:",
                "        self.code = code",
                "        self.tag = tag",
                "",
                "def run() -> int:",
                "    try:",
                '        raise AppError(1, "re")',
                "    except AppError as e:",
                "        try:",
                "            raise e",
                "        except AppError as again:",
                "            print(again.code, again.tag)",
                "    return 0",
                "",
            ]
        ),
    )


def test_a_user_class_takes_its_own_arguments() -> None:
    """A user class with its own `__init__` takes its own argument list."""
    fences = _fences(
        "\n".join(
            [
                "class AppError(Exception):",
                "    code: int",
                "    tag: str",
                "",
                "    def __init__(self, code: int, tag: str) -> None:",
                "        self.code = code",
                "        self.tag = tag",
                "",
                'raise AppError(1, "x")',
            ]
        )
    )
    assert not any(fence["code"] == "PS2003" for fence in fences), fences


def test_a_builtin_exception_keeps_the_one_scalar_arity() -> None:
    fences = _fences("raise ValueError(1, 2)")
    assert any(fence["code"] == "PS2003" for fence in fences), fences


def test_the_committed_probe_matches_cpython(tmp_path: Path) -> None:
    """The differential probe: the compiled program prints what CPython
    prints (probes/differential/user_exc_init.py, the AGENTS.md rule)."""
    probe = Path(__file__).resolve().parents[1] / "probes" / "differential" / "user_exc_init.py"
    expected = subprocess.run(
        [sys.executable, str(probe)],
        capture_output=True,
        text=True,
        encoding="latin-1",
        check=False,
    )
    got = run_cli("compile", str(probe), "--skip-check", "--run", "--emit", "bin", "-o", str(tmp_path / "probe.bin"))
    assert (got.returncode, got.stdout, got.stderr) == (
        expected.returncode,
        expected.stdout,
        expected.stderr,
    )
