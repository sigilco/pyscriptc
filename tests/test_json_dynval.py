# SPDX-License-Identifier: Apache-2.0
"""The JSON codec over the tagged dynamic value (issue #1267).

`json.loads` under a `dict[str, object]`, `list[object]` or bare `object`
annotation builds the tagged cells instead of checking a shape,
`json.dumps` walks them back with the typed writer's flag word, and
`print` reads the arms the way CPython's `str()` does. Each probe
compiles with the worktree's own `pyscriptc`, runs the native binary,
and byte-compares stdout, stderr and the exit code against the pinned
CPython 3.14 interpreter running the same source. The committed
differential probe `probes/differential/json_dynval_roundtrip.py` is the
AGENTS.md differential-probe row for the lowering; the tests here are
the pinned-oracle replay of the same surface.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from tests.test_gates import ROOT, write_case
from tests.test_runtime_divergences import compile_and_run, oracle_run

ZIG = shutil.which("zig") is not None


def fence(tmp_path: Path, name: str, source: str) -> bytes:
    """Compile `source`, return the compiler's stderr (a fence is a nonzero exit)."""
    path = write_case(tmp_path, name + ".py", source)
    proc = subprocess.run(
        [sys.executable, "-m", "pyscriptc", "compile", str(path), "-o", str(tmp_path / name)],
        cwd=ROOT,
        capture_output=True,
        env={**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")},
    )
    assert proc.returncode != 0
    return proc.stderr


@pytest.mark.skipif(not ZIG, reason="zig not on PATH")
@pytest.mark.parametrize(
    "name, source",
    sorted(
        {
            # The issue's scenario: a JSON-RPC-shaped payload into
            # dict[str, object], printed.
            "loads_dict_object": (
                """
import json

def main() -> None:
    msg: dict[str, object] = json.loads('{"id":1,"method":"x","params":{"a":1}}')
    print(msg)

main()
"""
            ),
            # Every arm of the sum through loads, dumps and print.
            "loads_all_arms": (
                """
import json

d: dict[str, object] = json.loads(
    '{"a": 1, "b": "two", "c": 2.5, "d": true, "e": null, "f": [1, "x", null], "g": {"h": 3}}'
)
print(d)
print(json.dumps(d))
print(json.dumps(d, sort_keys=True))
print(json.dumps(d, indent=2))

s: object = json.loads('"strval"')
print(s)
n: object = json.loads("null")
print(n)
print(json.dumps(n))
f: object = json.loads("2.25")
print(f)
print(json.dumps(f))
t: object = json.loads("true")
print(t)
lst: object = json.loads('[1, "a", null, true]')
print(lst)
print(json.dumps(lst))
"""
            ),
            # A display annotated dict[str, object] rides the same dump and
            # print rows as the parsed map.
            "display_dump": (
                """
import json

built: dict[str, object] = {"x": 1, "y": "two", "z": None}
print(built)
print(json.dumps(built))

xs: list[object] = [1, "a", None, True]
print(xs)
print(json.dumps(xs))
"""
            ),
            # The runtime tag test narrows the arm after the parse.
            "loads_narrow": (
                """
import json

flat: dict[str, object] = json.loads('{"k": 7}')
v = flat.get("k")
if isinstance(v, int):
    print("int", v)

s: object = json.loads('"strval"')
if isinstance(s, str):
    print("str arm")

def walk(msg: dict[str, object]) -> int:
    total = 0
    for k in msg:
        item = msg.get(k)
        if isinstance(item, int):
            total = total + 1
        if isinstance(item, str):
            total = total + 10
        if isinstance(item, dict):
            total = total + 100
    return total

print(walk(json.loads('{"a": 1, "b": "two", "c": {"d": 4}}')))
"""
            ),
            # The error rows: a malformed document and trailing text raise
            # the same ValueError the typed loads path raises.
            "loads_errors": (
                """
import json

try:
    bad: dict[str, object] = json.loads('{"a": }')
    print("no error", bad)
except ValueError:
    print("caught malformed")

try:
    trail: object = json.loads("{} {}")
    print("no error", trail)
except ValueError:
    print("caught trailing")

try:
    nested: object = json.loads('{"a": [1, {"b": ]}]}')
    print("no error", nested)
except ValueError:
    print("caught nested")
"""
            ),
            # CPython's parse rule: an integral literal is the int arm, a
            # real literal (fraction or exponent) the float arm. A
            # too-wide integer stays the int arm in CPython (its json
            # grows a big int) and is the divergence the design note
            # records; this replay covers the in-range shapes and the
            # i64-boundary values on both sides.
            "loads_number_arms": (
                """
import json

a: object = json.loads("42")
print(a)
b: object = json.loads("42.0")
print(b)
c: object = json.loads("1e3")
print(c)
d: object = json.loads("-7")
print(d)
e: object = json.loads("-2.5e-3")
print(e)
print(json.dumps(a), json.dumps(b), json.dumps(c), json.dumps(d), json.dumps(e))
big: object = json.loads("9223372036854775807")
print(big)
small: object = json.loads("-9223372036854775808")
print(small)
"""
            ),
        }.items(),
    ),
)
def test_matches_cpython(tmp_path: Path, name: str, source: str) -> None:
    got = compile_and_run(tmp_path, name + ".py", source)
    want = oracle_run(tmp_path, name + ".py", source)
    assert got == want


FENCES = {
    # bytes has no arm in the sum and no JSON literal; the display fence
    # keeps holding (issue #1226's sum excludes it).
    "list_bool_value": "import json\nxs: list[bool] = json.loads('[true]')\nprint(xs)\n",
    "int_key_dict": "import json\nd: dict[int, object] = json.loads('{\"a\": 1}')\nprint(d)\n",
}


@pytest.mark.skipif(not ZIG, reason="zig not on PATH")
@pytest.mark.parametrize("name", sorted(FENCES))
def test_fences_hold(tmp_path: Path, name: str) -> None:
    """The shapes the sum does not carry keep their fences."""
    err = fence(tmp_path, name, FENCES[name])
    assert b"PS" in err
