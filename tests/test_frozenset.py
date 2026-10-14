"""frozenset of const str or int is the set list, copied at each use.

len and membership match CPython. Iteration order is not printed. .add and
.remove on a name bound to that value stay fenced. A plain set can still .add.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import compile_run, oracle, run_cli, write_case

_PROBE = """
REQUIRED = frozenset(["metadata_version", "name", "version"])
DOUBLED = frozenset(["name", "name", "version"])
NUMS = frozenset({1, 2, 2})

def probe() -> str:
    present: int = 0
    if "name" in REQUIRED:
        present = 1
    absent: int = 0
    if "missing" in REQUIRED:
        absent = 1
    return str(len(REQUIRED)) + str(present) + str(absent)

def dup() -> int:
    hit: int = 0
    if 2 in NUMS:
        hit = 1
    return len(DOUBLED) * 100 + len(NUMS) * 10 + hit
"""


def test_frozenset_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "frozen.py", _PROBE)
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")
    assert got.stdout.strip() == "310"
    doubled = compile_run(tmp_path, path, "dup")
    assert doubled.returncode == 0, doubled.stderr
    assert doubled.stdout == oracle(path, "dup")
    assert doubled.stdout.strip() == "221"


def test_frozenset_add_stays_fenced(tmp_path: Path) -> None:
    cases = [
        (
            "empty.py",
            "def probe() -> int:\n    return len(frozenset())\n",
            "call to frozenset is not lowered",
        ),
        (
            "named.py",
            "def probe() -> int:\n    xs: list[str] = ['a']\n    return len(frozenset(xs))\n",
            "call to frozenset is not lowered",
        ),
        (
            "comp.py",
            "def probe() -> int:\n    return len(frozenset(item for item in ['a']))\n",
            "call to frozenset is not lowered",
        ),
        (
            "mixed.py",
            "def probe() -> int:\n    return len(frozenset(['a', 1]))\n",
            "call to frozenset is not lowered",
        ),
        (
            "add.py",
            (
                "REQUIRED = frozenset(['name'])\n"
                "\n"
                "def probe() -> int:\n"
                "    local: set[str] = REQUIRED\n"
                "    alias: set[str] = local\n"
                "    alias.add('extra')\n"
                "    return len(alias)\n"
            ),
            "frozenset has no mutating methods",
        ),
        (
            "remove.py",
            (
                "REQUIRED = frozenset(['name'])\n"
                "\n"
                "def probe() -> int:\n"
                "    REQUIRED.remove('name')\n"
                "    return 0\n"
            ),
            "frozenset has no mutating methods",
        ),
    ]
    for name, source, text in cases:
        path = write_case(tmp_path, name, source)
        got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / f"{name}.ll"))
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert text in got.stderr
