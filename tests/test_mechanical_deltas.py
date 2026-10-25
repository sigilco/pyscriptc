"""Mechanical deltas from the native ports: each source compiles and matches CPython 3.14.

The cases are the shapes the port work had to rewrite by hand (`.agents/notes/2026-10-25-port-deltas.md`):
narrowing an optional through compound conditions, keyword arguments, argparse option types.
Each program runs under `pyscriptc verify`, so stdout, stderr and the exit code all diff.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
from tests.test_gates import ROOT, run_cli, write_case

needs_cc = pytest.mark.skipif(
    shutil.which("clang") is None and shutil.which("zig") is None,
    reason="clang or zig required",
)

CASES: dict[str, str] = {
    "narrow_compound": """
import re
from pathlib import Path
from typing import Optional


def both(a: Optional[int], b: int) -> int:
    if a is not None and a > b:
        return a
    return b


def either(a: Optional[int], b: int) -> int:
    if a is None or a < b:
        return b
    return a


def truthy(s: Optional[str]) -> int:
    if s is not None and s != "x":
        return len(s)
    return 0


def negated(s: Optional[str]) -> int:
    if not s:
        return 0
    return len(s)


def path_guard(p: Optional[Path]) -> int:
    if p is None or not p.exists():
        return 1
    return 2


def match_if(s: str) -> int:
    m = re.match(r"(\\d+)", s)
    if m:
        return int(m.group(1))
    return -1


def match_and(s: str) -> int:
    m = re.match(r"(\\d+)-(\\w)", s)
    if m is not None and m.group(2) != "x":
        return int(m.group(1))
    return -1


def match_early(s: str) -> int:
    m = re.match(r"(\\d+)", s)
    if m is None:
        return -1
    return int(m.group(1))


def match_ternary(s: str) -> int:
    m = re.match(r"(\\d+)", s)
    return int(m.group(1)) if m else -1


def match_continue(lines: list[str]) -> int:
    total = 0
    for ln in lines:
        mm = re.match(r"\\w (\\d)", ln)
        if mm is None:
            continue
        total += int(mm.group(1))
    return total


def match_else(s: str) -> int:
    m = re.match(r"(\\d+)", s)
    if m is None:
        r = -1
    else:
        r = int(m.group(1))
    return r


print(both(5, 1), both(None, 1), both(0, 1))
print(either(5, 1), either(None, 1), either(0, 1))
print(truthy("abc"), truthy(None), truthy("x"))
print(negated("abc"), negated(None), negated(""))
print(path_guard(None), path_guard(Path("/")), path_guard(Path("/no/such/dir")))
print(match_if("12"), match_if("a"), match_and("12-a"), match_and("12-x"), match_and("a"))
print(match_early("12"), match_early("a"), match_ternary("12"), match_ternary("a"))
print(match_continue(["a 1", "b", "c 3"]), match_else("12"), match_else("a"))
""",
    "narrow_loop_exit": """
import re
import sys
from typing import Optional


def drain(s: str) -> int:
    t = 0
    m = re.match(r"(\\d)", s)
    while m is not None and t < 3:
        t += 1
    return t


def pick(xs: list[int]) -> Optional[int]:
    return xs[0] if xs else None


def head(xs: list[int]) -> int:
    v = pick(xs)
    if v is None:
        sys.exit(3)
    return v + 1


print(drain("1"), head([4]))
print(head([]))
""",
    "int_of_optional_group": """
import re


def second(s: str) -> int:
    m = re.match(r"(a)|(\\d)", s)
    if m is None:
        return -1
    return int(m.group(2))


print(second("zz"))
try:
    print(second("a"))
except TypeError:
    print("TypeError")
print(second("5"))
""",
}


@needs_cc
@pytest.mark.parametrize("name", sorted(CASES))
def test_matches_cpython(tmp_path: Path, name: str) -> None:
    path = write_case(tmp_path, f"{name}.py", CASES[name])
    got = run_cli("verify", str(path))
    assert got.returncode == 0, f"{name}\n{got.stdout}\n{got.stderr}"
    assert ": match" in got.stdout, got.stdout


def test_harness_uses_this_interpreter() -> None:
    assert ROOT.exists() and sys.executable
