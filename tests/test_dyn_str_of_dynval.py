# SPDX-License-Identifier: Apache-2.0
"""`str()` of a tagged dynamic value, and a class attribute typed `object`.

`str(v)` on a value typed `object` was fenced (PS2003): the tagged cell has
no `str` lowering of its own. The value now takes the runtime's own str form
of the cell (`ps_dyn_str_form`), the same one `print` uses: the str arm's
text, every other arm's repr.

A class attribute annotated `object` was fenced too (PS1003), because the
const-default rule wants a compile-time constant and the tagged cell has no
const spelling. The attribute now routes through the runtime-attribute pass
and lowers in the class's attribute initializer, so `C.v` reads it back the
way CPython does. A dataclass field default is the class attribute CPython
leaves behind, so it answers the same value.

Each probe compiles with the worktree's own `pyscriptc`, runs the native
binary, and byte-compares stdout, stderr and the exit code against the
pinned CPython 3.14 interpreter running the same source. The committed
differential probe `probes/differential/dyn_str_of_dynval.py` is the
AGENTS.md differential-probe row for the lowering.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from tests.test_runtime_divergences import compile_and_run, oracle_run

ZIG = shutil.which("zig") is not None

PROBES = {
    # Every arm of the sum through str(): the str arm's text, every other
    # arm's repr.
    "str_every_arm": """
def show(v: object) -> str:
    return str(v)

print(show(None))
print(show(True))
print(show(False))
print(show(41))
print(show(-7))
print(show(2.5))
print(show("hi"))
print(show(""))
print(show([1, "x", None]))
print(show({"a": 1, "b": "two"}))
""",
    # A nested container: the container's repr with quoted nested strings.
    "str_nested": """
def show(v: object) -> str:
    return str(v)

print(show([[1, 2], {"k": "v"}]))
""",
    # A class attribute typed object: not a compile-time constant, so it
    # lowers in the class's attribute initializer and reads through the slot.
    "class_attribute": """
class Holder:
    v: object = 1
    w: object = "seed"
    n: object = None
    f: object = 2.5
    b: object = True

print(Holder.v, Holder.w, Holder.n, Holder.f, Holder.b)
print(Holder().v, Holder().w)
""",
    # A dataclass field default is the class attribute CPython leaves
    # behind, so the field and the class attribute answer the same value.
    "dataclass_field_default": """
from dataclasses import dataclass

@dataclass
class Rec:
    v: object = 5
    s: object = "text"

print(Rec.v, Rec.s)
print(Rec().v, Rec().s)
print(Rec(9, "other").v, Rec(9, "other").s)
""",
    # A parameter default is the box call replayed per call site.
    "parameter_default": """
def with_default(v: object = 3) -> str:
    return str(v)

print(with_default(), with_default(4))
""",
    # The dynamic value inside a container: a dict[str, object] display
    # boxes each value, and str() of the dict reads the arms back.
    "container_display": """
msg: dict[str, object] = {"a": 1, "b": "two", "c": None}
print(msg)
print(str(msg["b"]))
xs: list[object] = [1, "x", None]
print(xs)
""",
}

# The fences that keep holding: an arm outside the closed sum refuses the
# box, and the const-default rule still refuses a non-dynamic default.
FENCES = {
    # A tuple is not an arm of the sum: the box refuses it.
    "tuple_arm": ("def f(v: object) -> int:\n    return 1\nt: tuple[int, int] = (1, 2)\nprint(f(t))\n"),
    # A bytes value is not an arm of the sum either.
    "bytes_arm": ('def f(v: object) -> int:\n    return 1\nb: bytes = b"x"\nprint(f(b))\n'),
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
    """The arms outside the closed sum keep their compile-time fences."""
    import os
    import subprocess
    import sys

    from tests.test_gates import ROOT, write_case

    path = write_case(tmp_path, name + ".py", FENCES[name])
    proc = subprocess.run(
        [sys.executable, "-m", "pyscriptc", "compile", str(path), "-o", str(tmp_path / name)],
        cwd=ROOT,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    assert proc.returncode != 0
    assert b"PS" in proc.stderr
