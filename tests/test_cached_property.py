# SPDX-License-Identifier: Apache-2.0
"""`functools.cached_property` (issue #440): the instance slot with the
first-read compute.

The decorator becomes its own method kind: the getter keeps its body, the
class gains a hidden value slot and a hidden computed flag, and the read
site calls a synthesized accessor that owns the one-shot compute. The
first read calls the getter once, the second read answers the stored
value, and every instance owns its own slot.

The committed differential probe `probes/differential/cached_property.py`
is the AGENTS.md differential-probe row for the lowering; the tests here
are the pinned-oracle replay of the same surface plus the refusals.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from tests.test_gates import ROOT, compile_run, write_case
from tests.test_runtime_divergences import compile_and_run

ZIG = shutil.which("zig") is not None
PROBE = ROOT / "probes" / "differential" / "cached_property.py"


def _oracle() -> str:
    """The pinned CPython 3.14 interpreter, when it is on this machine."""
    pinned = Path.home() / ".local/share/uv/python"
    candidates = sorted(pinned.glob("cpython-3.14.*-*/bin/python3"))
    if candidates:
        return str(candidates[-1])
    return sys.executable


def _fences(source: str) -> list[dict[str, str]]:
    from pyscriptc.lower import lower_source

    return lower_source(source, "case.py")["fences"]


def _probe_source() -> str:
    return PROBE.read_text(encoding="utf-8")


@pytest.mark.skipif(not ZIG, reason="zig not on PATH")
def test_the_committed_probe_matches_cpython(tmp_path: Path) -> None:
    """The differential probe: the compiled program prints what CPython prints."""
    source = _probe_source()
    path = tmp_path / "probe.py"
    path.write_text(source, encoding="utf-8")
    got = compile_and_run(tmp_path, "probe.py", source)
    want = subprocess.run(
        [_oracle(), str(path)],
        capture_output=True,
        timeout=120,
        cwd=tmp_path,
    )
    assert got == (want.stdout, want.stderr, want.returncode)


def test_first_read_computes_and_the_second_answers(tmp_path: Path) -> None:
    source = (
        "from functools import cached_property\n"
        "\n"
        "\n"
        "class C:\n"
        "    x: int\n"
        "\n"
        "    def __init__(self, x: int) -> None:\n"
        "        self.x = x\n"
        "\n"
        "    @cached_property\n"
        "    def y(self) -> int:\n"
        "        print('computing')\n"
        "        return self.x + 1\n"
        "\n"
        "\n"
        "def main() -> None:\n"
        "    c = C(1)\n"
        "    print(c.y, c.y, c.y)\n"
    )
    path = write_case(tmp_path, "first_read.py", source)
    proc = compile_run(tmp_path, path, "main")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "computing\n2 2 2\n", (proc.stdout, proc.stderr)


def test_each_instance_owns_its_slot(tmp_path: Path) -> None:
    source = (
        "from functools import cached_property\n"
        "\n"
        "\n"
        "class C:\n"
        "    x: int\n"
        "\n"
        "    def __init__(self, x: int) -> None:\n"
        "        self.x = x\n"
        "\n"
        "    @cached_property\n"
        "    def y(self) -> int:\n"
        "        return self.x * 10\n"
        "\n"
        "\n"
        "def main() -> None:\n"
        "    a = C(1)\n"
        "    b = C(2)\n"
        "    print(a.y, b.y, a.y, b.y)\n"
    )
    path = write_case(tmp_path, "per_instance.py", source)
    proc = compile_run(tmp_path, path, "main")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "10 20 10 20\n", (proc.stdout, proc.stderr)


def test_a_cached_property_can_read_another(tmp_path: Path) -> None:
    source = (
        "from functools import cached_property\n"
        "\n"
        "\n"
        "class C:\n"
        "    x: int\n"
        "\n"
        "    def __init__(self, x: int) -> None:\n"
        "        self.x = x\n"
        "\n"
        "    @cached_property\n"
        "    def a(self) -> int:\n"
        "        return self.x + 1\n"
        "\n"
        "    @cached_property\n"
        "    def b(self) -> int:\n"
        "        return self.a * 2\n"
        "\n"
        "\n"
        "def main() -> None:\n"
        "    c = C(1)\n"
        "    print(c.b, c.a)\n"
    )
    path = write_case(tmp_path, "chained.py", source)
    proc = compile_run(tmp_path, path, "main")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "4 2\n", (proc.stdout, proc.stderr)


def test_a_subclass_inherits_and_adds_cached_properties(tmp_path: Path) -> None:
    source = (
        "import functools\n"
        "\n"
        "\n"
        "class Base:\n"
        "    n: int\n"
        "\n"
        "    def __init__(self, n: int) -> None:\n"
        "        self.n = n\n"
        "\n"
        "    @functools.cached_property\n"
        "    def doubled(self) -> int:\n"
        "        return self.n * 2\n"
        "\n"
        "\n"
        "class Sub(Base):\n"
        "    @functools.cached_property\n"
        "    def tripled(self) -> int:\n"
        "        return self.n * 3\n"
        "\n"
        "\n"
        "def main() -> None:\n"
        "    s = Sub(4)\n"
        "    print(s.doubled, s.tripled, s.doubled)\n"
    )
    path = write_case(tmp_path, "subclass.py", source)
    proc = compile_run(tmp_path, path, "main")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "8 12 8\n", (proc.stdout, proc.stderr)


def test_a_str_slot_keeps_the_value(tmp_path: Path) -> None:
    source = (
        "from functools import cached_property\n"
        "\n"
        "\n"
        "class C:\n"
        "    x: int\n"
        "\n"
        "    def __init__(self, x: int) -> None:\n"
        "        self.x = x\n"
        "\n"
        "    @cached_property\n"
        "    def s(self) -> str:\n"
        "        return 'value-' + str(self.x)\n"
        "\n"
        "\n"
        "def main() -> None:\n"
        "    c = C(7)\n"
        "    print(c.s, c.s)\n"
    )
    path = write_case(tmp_path, "str_slot.py", source)
    proc = compile_run(tmp_path, path, "main")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "value-7 value-7\n", (proc.stdout, proc.stderr)


def test_a_dataclass_repr_and_eq_skip_the_slots() -> None:
    # The hidden slots ride the struct layout but not the declared field
    # set, so the generated `__repr__` and `__eq__` never see them.
    fences = _fences(
        "from dataclasses import dataclass\n"
        "from functools import cached_property\n"
        "\n"
        "\n"
        "@dataclass\n"
        "class C:\n"
        "    x: int\n"
        "\n"
        "    @cached_property\n"
        "    def y(self) -> int:\n"
        "        return self.x + 1\n"
    )
    assert fences == [], fences


def test_a_float_slot_keeps_the_ps2002_fence() -> None:
    # A float has no struct-field form: the cached value has no slot.
    for ret in ("float", "bool"):
        fences = _fences(
            "from functools import cached_property\n"
            "\n"
            "\n"
            "class C:\n"
            "\n"
            "    @cached_property\n"
            f"    def y(self) -> {ret}:\n"
            "        return 1\n"
        )
        assert any("storable return type" in fence["message"] for fence in fences), (ret, fences)


def test_the_getter_takes_only_self() -> None:
    fences = _fences(
        "from functools import cached_property\n"
        "\n"
        "\n"
        "class C:\n"
        "    @cached_property\n"
        "    def y(self, extra: int) -> int:\n"
        "        return extra\n"
    )
    assert any("only parameter is self" in fence["message"] for fence in fences), fences


def test_a_write_to_the_cached_name_is_a_named_refusal() -> None:
    fences = _fences(
        "from functools import cached_property\n"
        "\n"
        "\n"
        "class C:\n"
        "    x: int\n"
        "\n"
        "    def __init__(self, x: int) -> None:\n"
        "        self.x = x\n"
        "\n"
        "    @cached_property\n"
        "    def y(self) -> int:\n"
        "        return self.x + 1\n"
        "\n"
        "\n"
        "def go(c: C) -> None:\n"
        "    c.y = 5\n"
    )
    assert any("cached property" in fence["message"] for fence in fences), fences


def test_a_field_of_the_same_name_is_a_duplicate() -> None:
    fences = _fences(
        "from functools import cached_property\n"
        "\n"
        "\n"
        "class C:\n"
        "\n"
        "    @cached_property\n"
        "    def y(self) -> int:\n"
        "        return 1\n"
        "\n"
        "    y: int\n"
    )
    assert any("duplicate field y" in fence["message"] for fence in fences), fences


def test_the_call_form_is_not_a_spelling() -> None:
    # `@cached_property()` raises TypeError in CPython: the call form
    # never selects the kind, so the stack keeps the plain refusal.
    fences = _fences(
        "from functools import cached_property\n"
        "\n"
        "\n"
        "class C:\n"
        "    @cached_property()\n"
        "    def y(self) -> int:\n"
        "        return 1\n"
    )
    assert any(fence["code"] == "PS1201" for fence in fences), fences


def test_a_call_of_the_member_keeps_the_fence() -> None:
    # `cached_property` lowers as a decorator only: a call of it has no
    # meaning in the subset.
    fences = _fences("from functools import cached_property\n\n\ndef go() -> None:\n    cached_property(go)\n")
    assert any(fence["code"] == "PS2001" for fence in fences), fences


def test_the_dataclass_read_site_matches_cpython(tmp_path: Path) -> None:
    source = (
        "from dataclasses import dataclass\n"
        "from functools import cached_property\n"
        "\n"
        "\n"
        "@dataclass\n"
        "class C:\n"
        "    x: int\n"
        "\n"
        "    @cached_property\n"
        "    def y(self) -> int:\n"
        "        return self.x + 1\n"
        "\n"
        "\n"
        "def main() -> None:\n"
        "    print(C(1).y, C(1) == C(1), C(1) == C(2))\n"
        "    print(C(1))\n"
    )
    path = write_case(tmp_path, "dataclass_cached.py", source)
    proc = compile_run(tmp_path, path, "main")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "2 True False\nC(x=1)\n", (proc.stdout, proc.stderr)
