"""`del` of one unread local is dropped.

The function still returns the value it returns. A name that is read after
the `del` stays fenced, and so do attribute, subscript, slice, and tuple
targets.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, run_cli, write_case

_PROBE = """
def checklist() -> int:
    x: int = 1
    del x
    return 1

def after() -> int:
    n: int = 1
    x: int = 4
    del x
    n = n + 1
    return n

def before() -> int:
    x: int = 7
    n: int = x
    del x
    return n
"""


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_del_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "del_local.py", _PROBE)
    expected = {"checklist": "1", "after": "2", "before": "7"}
    for func, want in expected.items():
        got = compile_run(tmp_path, path, func)
        assert got.returncode == 0, f"{func}\n{got.stderr}"
        assert got.stdout == oracle(path, func)
        assert got.stdout.strip() == want


def test_del_stays_fenced(tmp_path: Path) -> None:
    cases = [
        (
            "attr.py",
            "def probe() -> int:\n    del box.n\n    return 1\n",
            "del of an attribute is not lowered",
        ),
        (
            "sub.py",
            "def probe() -> int:\n    del xs[0]\n    return 1\n",
            "del of a subscript is not lowered",
        ),
        (
            "slice.py",
            "def probe() -> int:\n    del xs[1:2]\n    return 1\n",
            "del of a slice is not lowered",
        ),
        (
            "tuple.py",
            "def probe() -> int:\n    del (x, y)\n    return 1\n",
            "del of a tuple of names is not lowered",
        ),
        (
            "later.py",
            "def probe() -> int:\n    x: int = 1\n    del x\n    return x\n",
            "del of a name that is read later is not lowered",
        ),
        (
            "many.py",
            "def probe() -> int:\n    del y, x\n    return 1\n",
            "del of more than one name is not lowered",
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
