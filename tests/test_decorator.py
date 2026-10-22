"""A module `@staticmethod` is the function, called with no instance.

A call from another function returns what the function returns. Other
decorators stay fenced.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, run_cli, write_case

_PROBE = """
@staticmethod  # type: ignore[misc]
def checklist() -> int:
    return 1

@staticmethod  # type: ignore[misc]
def probe() -> int:
    return 1

def caller() -> int:
    return probe()
"""


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_module_staticmethod_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "deco.py", _PROBE)
    expected = {"checklist": "1", "caller": "1"}
    for func, want in expected.items():
        got = compile_run(tmp_path, path, func)
        assert got.returncode == 0, f"{func}\n{got.stderr}"
        assert got.stdout == oracle(path, func)
        assert got.stdout.strip() == want


def test_module_decorators_stay_fenced(tmp_path: Path) -> None:
    cases = [
        (
            "prop.py",
            "@property\ndef probe() -> int:\n    return 1\n",
            "a module property is not lowered",
        ),
        (
            "cls.py",
            "@classmethod\ndef probe() -> int:\n    return 1\n",
            "a module classmethod is not lowered",
        ),
        (
            "stack.py",
            "@staticmethod\n@staticmethod\ndef probe() -> int:\n    return 1\n",
            "stacked decorators are not lowered",
        ),
        (
            "expr.py",
            "@staticmethod()\ndef probe() -> int:\n    return 1\n",
            "a decorator expression is not lowered",
        ),
        (
            "user.py",
            "def deco(fn: int) -> int:\n    return fn\n@deco\ndef probe() -> int:\n    return 1\n",
            "user decorators are not lowered",
        ),
    ]
    for name, source, text in cases:
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
        assert text in got.stderr, f"{name}\n{got.stderr}"
