# SPDX-License-Identifier: Apache-2.0
"""Type promotion through the tagged dynamic value (issue #1280).

`isinstance(x, float)` (and int, str, bool, list, dict) on a value typed
`object` narrows x into the native type inside the taken branch: the read
becomes the arm's projection off the same cell, and the value is usable as
that type in the code that follows. Each probe compiles with the worktree's
own `pyscriptc`, runs the native binary, and byte-compares stdout, stderr
and the exit code against the pinned CPython 3.14 interpreter running the
same source. The committed differential probe
`probes/differential/dyn_isinstance_narrow.py` is the AGENTS.md
differential-probe row for the lowering; the tests here are the
pinned-oracle replay of the same surface.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from tests.test_gates import write_case
from tests.test_runtime_divergences import compile_and_run, oracle_run

ZIG = shutil.which("zig") is not None

PROBES = {
    # Every arm of the sum through the tag test and the projection: the
    # narrowed value is used as its arm type in the same branch.
    "narrow_all_arms": """
def describe(x: object) -> str:
    if isinstance(x, bool):
        if x:
            return "bool-true"
        return "bool-false"
    if isinstance(x, int):
        return "int:" + str(x + 1)
    if isinstance(x, float):
        return "float:" + str(x * 2.0)
    if isinstance(x, str):
        return "str:" + x.upper()
    if isinstance(x, list):
        return "list:" + str(len(x))
    if isinstance(x, dict):
        return "dict:" + str(len(x))
    return "other"

print(describe(True))
print(describe(False))
print(describe(41))
print(describe(2.5))
print(describe("hi"))
print(describe([1, 2, 3]))
print(describe({"a": 1}))
print(describe(None))
""",
    # The early-return shape: the branch that exits proves the arm for the
    # rest of the body, the way the optional narrowing does.
    "early_exit_narrow": """
def early(x: object) -> int:
    if not isinstance(x, int):
        return -1
    return x + 1

print(early(41))
print(early("s"))
print(early(2.5))
print(early(True))
""",
    # `assert isinstance` proves the arm for the rest of the body.
    "assert_narrow": """
def use(x: object) -> int:
    assert isinstance(x, int)
    return x * 2

print(use(21))
""",
    # The bool-is-an-int rule: a bool cell answers the int arm and the
    # projection gives the int value the comparison reads.
    "bool_is_int": """
def as_int(x: object) -> int:
    if isinstance(x, int):
        return x + 1
    return 0

print(as_int(True))
print(as_int(False))
print(as_int(7))
print(as_int(0.5))
""",
    # A dict[str, object] payload narrowed per value: the JSON-RPC shape.
    "payload_narrow": """
def field(msg: dict[str, object], key: str) -> int:
    v = msg.get(key)
    if isinstance(v, int):
        return v * 10
    if isinstance(v, str):
        return len(v)
    return -1

print(field({"a": 4}, "a"))
print(field({"a": "four"}, "a"))
print(field({}, "a"))
""",
    # The narrowing holds through the loop body and re-proves each pass.
    "while_narrow": """
def countdown(x: object) -> int:
    n = 0
    v: object = x
    while isinstance(v, int) and v > 0:
        n = n + v
        v = v - 1
    return n

print(countdown(4))
print(countdown("s"))
""",
    # The and/or proof rules: the taken branch of an and carries both
    # sides' proofs; the taken branch of an or carries neither.
    "boolop_narrow": """
def both(x: object, y: object) -> int:
    if isinstance(x, int) and isinstance(y, int):
        return x + y
    return -1

print(both(2, 3))
print(both(2, "s"))
print(both("s", 2))

def either(x: object) -> int:
    if isinstance(x, int) or isinstance(x, float):
        return 1
    return 0

print(either(1))
print(either(1.5))
print(either("s"))
""",
    # The value outside the branch is still the sum: two sequential
    # narrowings of the same name.
    "sequential_narrow": """
def twice(x: object) -> int:
    n = 0
    if isinstance(x, int):
        n = x
    if isinstance(x, int):
        n = n + 1
    return n

print(twice(5))
print(twice("s"))
""",
    # The str arm through a call that reads its payload (len, upper, +).
    "str_narrow": """
def shout(x: object) -> str:
    if isinstance(x, str):
        return x + "!"
    return "?"

print(shout("hey"))
print(shout(9))

def size(x: object) -> int:
    if isinstance(x, str):
        return len(x)
    return -1

print(size("four"))
print(size(1.5))
""",
    # A nested value out of the JSON codec: the load boxes the arms, the
    # test projects them back.
    "json_narrow": """
import json

def walk(msg: dict[str, object]) -> int:
    total = 0
    for k in msg:
        v = msg.get(k)
        if isinstance(v, int):
            total = total + v
        if isinstance(v, str):
            total = total + len(v)
        if isinstance(v, bool):
            total = total + 100
    return total

print(walk(json.loads('{"a": 1, "b": "two", "c": true, "d": null}')))
""",
}

# The fences that keep holding: a use the proof does not cover, and a
# class target, which is not an arm of the sum.
FENCES = {
    # A rebinding makes the name a fresh dynamic value; the use after it
    # has no proof.
    "rebound_use": (
        "def f(x: object) -> int:\n"
        "    if isinstance(x, int):\n"
        "        x = 5\n"
        "        return x + 100\n"
        "    return 0\n"
        "print(f(7))\n"
    ),
    # A class target is not an arm of the sum: the compile-time fence of
    # the tag test stays.
    "class_target": (
        "class P:\n"
        "    pass\n"
        "def f(x: object) -> int:\n"
        "    if isinstance(x, P):\n"
        "        return 1\n"
        "    return 0\n"
        "print(f(P()))\n"
    ),
}


@pytest.mark.skipif(not ZIG, reason="zig not on PATH")
@pytest.mark.parametrize("name", sorted(PROBES))
def test_matches_cpython(tmp_path: Path, name: str) -> None:
    got = compile_and_run(tmp_path, name + ".py", PROBES[name])
    want = oracle_run(tmp_path, name + ".py", PROBES[name])
    assert got == want


@pytest.mark.skipif(not ZIG, reason="zig not on PATH")
@pytest.mark.parametrize("name", sorted(FENCES))
def test_fences_hold(tmp_path: Path, name: str) -> None:
    """The uses the proof does not cover keep their compile-time fences."""
    path = write_case(tmp_path, name + ".py", FENCES[name])
    import os
    import subprocess
    import sys

    from tests.test_gates import ROOT

    proc = subprocess.run(
        [sys.executable, "-m", "pyscriptc", "compile", str(path), "-o", str(tmp_path / name)],
        cwd=ROOT,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    assert proc.returncode != 0
    assert b"PS" in proc.stderr
