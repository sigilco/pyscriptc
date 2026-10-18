"""Real-Python backlog R36: bare annotated declarations and `__name__`.

`x: int` (no value) types the slot now and is definitely assigned later;
the local starts at the type's zero value so a read before the assignment
is deterministic (CPython raises UnboundLocalError instead). `__name__`
lowers to the module name string: `__main__` for a compiled unit, the
dotted name for a package unit. The `x = None` half stays gated on R26
(union types) and is not covered here.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.test_gates import compile_run, oracle, run_cli, write_case


def test_r36_bare_decl_inside_function(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "r36_decl.py",
        "\n".join(
            [
                "def main() -> int:",
                "    x: int",
                "    x = 5",
                "    return x",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "main")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "main")


def test_r36_bare_decl_zero_initialized(tmp_path: Path) -> None:
    # A read before the definite assignment yields the type's zero value;
    # CPython raises UnboundLocalError here, so there is no oracle diff.
    path = write_case(
        tmp_path,
        "r36_zero.py",
        "\n".join(
            [
                "def main() -> int:",
                "    x: int",
                "    print(x)",
                "    x = 9",
                "    return x",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "main")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "0\n9\n"


def test_r36_bare_decl_each_kind(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "r36_kinds.py",
        "\n".join(
            [
                "def main() -> int:",
                "    a: str",
                "    b: list[int]",
                "    c: dict[str, int]",
                "    d: float",
                "    e: bool",
                "    y: set[int]",
                '    a = "hi"',
                "    b = [1, 2]",
                '    c = {"k": 3}',
                "    d = 1.5",
                "    e = True",
                "    y = {1, 2}",
                "    print(a)",
                "    print(b[0], b[1])",
                '    print(c["k"])',
                "    print(d)",
                "    print(e)",
                "    print(len(y))",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "main")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "main")


def test_r36_dunder_name_is_main(tmp_path: Path) -> None:
    # No oracle diff: the oracle imports the file as a module where
    # __name__ is "mod"; the compiled binary is the program, so "__main__".
    path = write_case(
        tmp_path,
        "r36_name.py",
        "\n".join(
            [
                "def main() -> int:",
                "    print(__name__)",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "main")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "__main__\n0\n"


def test_r36_dunder_name_module_constant(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "r36_modname.py",
        "\n".join(
            [
                "X = __name__",
                "",
                "def main() -> int:",
                "    print(X)",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "main")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "__main__\n0\n"


def test_r36_main_guard_inside_function(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "r36_guard.py",
        "\n".join(
            [
                "def main() -> int:",
                '    if __name__ == "__main__":',
                '        print("main")',
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "main")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "main\n0\n"


def test_r36_module_bare_decl_constant(tmp_path: Path) -> None:
    # `x = 5` still binds as a module constant; the bare decl is a no-op
    # and must not shadow it with an entry local.
    path = write_case(
        tmp_path,
        "r36_modconst.py",
        "\n".join(
            [
                "x: int",
                "x = 5",
                "print(x)",
                "",
            ]
        ),
    )
    got = run_cli("compile", str(path), "--run", "-o", str(tmp_path / "script"))
    assert got.returncode == 0, got.stderr
    assert got.stdout == "5\n"


def test_r36_module_bare_decl_follows_entry(tmp_path: Path) -> None:
    # `x = f()` cannot bind as a constant, so the script lowers into the
    # synthesized entry; the bare decl follows it there as the annotation.
    path = write_case(
        tmp_path,
        "r36_moddyn.py",
        "\n".join(
            [
                "def f() -> int:",
                "    return 7",
                "",
                "x: int",
                "x = f()",
                "print(x)",
                "",
            ]
        ),
    )
    got = run_cli("compile", str(path), "--run", "-o", str(tmp_path / "script"))
    assert got.returncode == 0, got.stderr
    expected = subprocess.run([sys.executable, str(path)], capture_output=True, text=True, check=True)
    assert got.stdout == expected.stdout


def test_r36_typing_name_still_fences(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "r36_any.py",
        "\n".join(
            [
                "from typing import Any",
                "",
                "def main() -> int:",
                "    x: Any",
                "    x = 1",
                "    return 0",
                "",
            ]
        ),
    )
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "r36_any.ll"))
    assert got.returncode == 3, got.stderr
    assert "PS1104" in got.stderr


def test_r36_none_assignment_still_fenced(tmp_path: Path) -> None:
    # The `x = None` half needs union types (R26): the checker rejects it
    # before lowering, so nothing lands here yet.
    path = write_case(
        tmp_path,
        "r36_none.py",
        "\n".join(
            [
                "def main() -> int:",
                "    x: int",
                "    x = None",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "main")
    assert got.returncode == 2, got.stderr
