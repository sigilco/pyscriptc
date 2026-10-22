"""Membership in a tuple display of one const type is an or of == compares.

The probe prints four digits: present str, absent str, not-in of the present
str, and the empty-tuple test. A tuple name, a list, a mix, and a non-constant
element stay on the old path. Tuple equality stays fenced.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, run_cli, write_case

_PROBE = """
def probe() -> str:
    env_var: str = "python_implementation"
    present: int = 0
    if env_var in ("platform_python_implementation", "python_implementation"):
        present = 1
    absent: int = 0
    if "nope" in ("platform_python_implementation", "python_implementation"):
        absent = 1
    outside: int = 0
    if env_var not in ("platform_python_implementation", "python_implementation"):
        outside = 1
    empty: int = 0
    if env_var in ():
        empty = 1
    return str(present) + str(absent) + str(outside) + str(empty)


def more() -> str:
    one: int = 0
    if "a" in ("a",):
        one = 1
    three: int = 0
    if 2 in (0, 1, 2):
        three = 1
    none: int = 0
    if 9 in (0, 1, 2):
        none = 1
    return str(one) + str(three) + str(none)


def listed() -> int:
    hit: int = 0
    if "a" in ["a", "b"]:
        hit = 1
    return hit
"""


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_tuple_membership_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "member.py", _PROBE)
    for func, expected in (("probe", "1000"), ("more", "110"), ("listed", "1")):
        got = compile_run(tmp_path, path, func)
        assert got.returncode == 0, f"{func}\n{got.stderr}"
        assert got.stdout == oracle(path, func)
        assert got.stdout.strip() == expected


def test_tuple_membership_stays_fenced(tmp_path: Path) -> None:
    cases = [
        (
            "name.py",
            'PAIR = ("a", "b")\n\ndef probe() -> int:\n    if "a" in PAIR:\n        return 1\n    return 0\n',
            "in is lowered for str, bytes, list, and dict",
        ),
        (
            "mixed.py",
            'def probe() -> int:\n    if "a" in ("a", 1):\n        return 1\n    return 0\n',
            "in is lowered for str, bytes, list, and dict",
        ),
        (
            "call.py",
            (
                "def piece() -> str:\n"
                '    return "c"\n'
                "\n"
                "def probe() -> int:\n"
                '    if "a" in ("a", piece()):\n'
                "        return 1\n"
                "    return 0\n"
            ),
            "in is lowered for str, bytes, list, and dict",
        ),
        (
            "one.py",
            "def probe(extra: str) -> int:\n    if extra in (extra,):\n        return 1\n    return 0\n",
            "tuples need at least two fields",
        ),
    ]
    for name, source, text in cases:
        path = write_case(tmp_path, name, source)
        got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / f"{name}.ll"))
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert text in got.stderr, f"{name}\n{got.stderr}"
