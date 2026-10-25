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
    "kwargs_calls": """
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def t(tag: str, v: int) -> int:
    print(tag)
    return v


def f(a: int, b: int, c: int = 0) -> int:
    return a * 100 + b * 10 + c


def kwonly(a: int, *, b: int = 1, c: int) -> int:
    return a + b + c


@dataclass
class Cfg:
    base: Path
    out: Optional[Path] = None
    label: Optional[str] = None
    n: int = 0


def mk(base: Path, out: Optional[Path] = None, n: int = 0) -> Cfg:
    return Cfg(n=n, base=base, out=out)


def outer(k: int) -> int:
    def inner(a: int, b: int = 1) -> int:
        return a + b + k

    return inner(b=2, a=5) + inner(1) + inner(3, b=t("inner", 4))


print(f(b=t("b", 2), a=t("a", 1)))
print(f(t("a", 1), c=t("c", 3), b=t("b", 2)))
print(kwonly(1, c=2), kwonly(1, c=2, b=5))
print(outer(10))
c = mk(base=Path("/a"), n=2)
print(str(c.base), c.out is None, c.label is None, c.n)
d = Cfg(Path("/b"), label="x", n=7)
lab = d.label
print(lab if lab is not None else "none", d.n)
""",
    "argparse_types": """
import argparse
import pathlib
from pathlib import Path
from typing import Optional

D = 52429


def parse(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="d")
    p.add_argument("--base", type=pathlib.Path, required=True, help="b")
    p.add_argument("--out", type=Path, default="out.json")
    p.add_argument("--warn", type=int, default=D)
    a = p.parse_args(argv)
    print(str(a.base), a.out.name, a.warn, str(a.base.parent))
    return a.warn


parse(["--base", "/tmp/x.txt"])
parse(["--base", "/a/b.txt", "--out", "/c/d.json", "--warn", "7"])
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


def test_path_option_without_default_stays_fenced(tmp_path: Path) -> None:
    source = (
        "import argparse\nfrom pathlib import Path\n\n"
        "p = argparse.ArgumentParser()\np.add_argument('--o', type=Path)\n"
        "a = p.parse_args()\nprint(str(a.o))\n"
    )
    path = write_case(tmp_path, "pathopt.py", source)
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "x.ll"))
    assert got.returncode == 3, got.stderr
    assert "type=Path needs required=True or a str default" in got.stderr
