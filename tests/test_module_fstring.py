"""Module-level f-strings of str constants are one str constant."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, run_cli, write_case


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_module_fstring_of_str_constants_matches_cpython(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "copy.py",
        "\n".join(
            [
                '__author__ = "Ada"',
                '__year__ = "2014"',
                '__copyright__ = f"{__year__} {__author__}"',
                "",
                "def read() -> str:",
                "    return __copyright__",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "read")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "read")
    assert got.stdout.strip() == "2014 Ada"


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_function_fstring_still_matches_cpython(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "local.py",
        "\n".join(
            [
                "def read() -> str:",
                '    name: str = "Ada"',
                '    return f"2014 {name}"',
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "read")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "read")
    assert got.stdout.strip() == "2014 Ada"


def test_module_fstring_fences(tmp_path: Path) -> None:
    cases = [
        (
            "number.py",
            "\n".join(
                [
                    'label = f"n{1}"',
                    "def read() -> str:",
                    "    return label",
                    "",
                ]
            ),
            "a module assignment must be a constant",
        ),
        (
            "spec.py",
            "\n".join(
                [
                    '__author__ = "Ada"',
                    '__copyright__ = f"2014 {__author__:>4}"',
                    "def read() -> str:",
                    "    return __copyright__",
                    "",
                ]
            ),
            # The spec lowers through the R16 engine now; the module constant
            # rule is what still fences the assignment.
            "a module assignment must be a constant",
        ),
    ]
    for name, source, text in cases:
        path = write_case(tmp_path, name, source)
        got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / f"{name}.ll"))
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert text in got.stderr
