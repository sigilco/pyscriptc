# SPDX-License-Identifier: Apache-2.0
"""Dataclass field flags, a dataclass base and `__post_init__` (issue #452).

`field(repr=False)` and `field(compare=False)` drop the field from the
synthesized `__repr__` and `__eq__` the way CPython's flags do. A
dataclass extends a dataclass base: the base's fields lead the
constructor, the child's follow, and the child regenerates the
synthesized dunders over the whole field list. `__post_init__` runs
after the fields are set; the child's own hook wins over the base's,
and `super().__post_init__()` calls the base's.

The committed differential probe
`probes/differential/dataclass_inherit_post_init.py` is the AGENTS.md
differential-probe row for the lowering; the tests here are the
pinned-oracle replay of the same surface.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from tests.test_gates import ROOT
from tests.test_runtime_divergences import compile_and_run

ZIG = shutil.which("zig") is not None
PROBE = ROOT / "probes" / "differential" / "dataclass_inherit_post_init.py"


def _oracle() -> str:
    """The pinned CPython 3.14 interpreter, when it is on this machine."""
    pinned = Path.home() / ".local/share/uv/python"
    candidates = sorted(pinned.glob("cpython-3.14.*-*/bin/python3"))
    if candidates:
        return str(candidates[-1])
    return sys.executable


@pytest.mark.skipif(not ZIG, reason="zig not on PATH")
def test_the_committed_probe_matches_cpython(tmp_path: Path) -> None:
    """The differential probe: the compiled program prints what CPython prints."""
    source = PROBE.read_text(encoding="utf-8")
    path = tmp_path / "probe.py"
    path.write_text(source, encoding="utf-8")
    got = compile_and_run(tmp_path, "probe", source)
    want = subprocess.run(
        [_oracle(), str(path)],
        capture_output=True,
        timeout=120,
        cwd=tmp_path,
    )
    assert got == (want.stdout, want.stderr, want.returncode)


FLAGS = """\
from dataclasses import dataclass, field


@dataclass
class Flags:
    a: int
    hidden: str = field(default="h", repr=False)
    ignored: int = field(default=7, compare=False)
    both: str = field(default="b", repr=False, compare=False)


f = Flags(1)
print(f)
print(Flags(1, "z", 9, "q"))
print(f == Flags(1, "z", 9, "q"))
print(f == Flags(1))
"""


@pytest.mark.skipif(not ZIG, reason="zig not on PATH")
def test_field_flags(tmp_path: Path) -> None:
    """`repr=False` and `compare=False` drop the field from the dunders."""
    got = compile_and_run(tmp_path, "flags.py", FLAGS)
    assert got[1] == b""
    assert got[2] == 0
    assert [
        b"Flags(a=1, ignored=7)",
        b"Flags(a=1, ignored=9)",
        b"False",
        b"True",
    ] == got[0].splitlines()


INHERIT = """\
from dataclasses import dataclass


@dataclass
class Base:
    x: int
    label: str = "base"


@dataclass
class Child(Base):
    y: int = 3


child = Child(1)
print(child)
print(Child(1) == Child(1))
print(Child(1) == Child(2))
print(Child(1, "l", 5))
"""


@pytest.mark.skipif(not ZIG, reason="zig not on PATH")
def test_dataclass_base_inheritance(tmp_path: Path) -> None:
    """The base's fields lead the constructor; the child regenerates the dunders."""
    got = compile_and_run(tmp_path, "inherit.py", INHERIT)
    assert got[1] == b""
    assert got[2] == 0
    assert [
        b"Child(x=1, label='base', y=3)",
        b"True",
        b"False",
        b"Child(x=1, label='l', y=5)",
    ] == got[0].splitlines()


INHERIT_NON_DEFAULT = """\
from dataclasses import dataclass


@dataclass
class Base:
    x: int = 1


@dataclass
class Child(Base):
    y: int
"""


@pytest.mark.skipif(not ZIG, reason="zig not on PATH")
def test_non_default_after_defaulted_base_field_fences(tmp_path: Path) -> None:
    """CPython's ordering rule spans base and child."""
    from tests.test_gates import write_case

    path = write_case(tmp_path, "order.py", INHERIT_NON_DEFAULT)
    proc = compile_fences(path)
    assert "PS2002" in proc
    assert "a non-default field follows a default" in proc


def compile_fences(path: Path) -> str:
    """Compile `path` and return stderr (the fence text).

    The checker preflight is skipped: these tests exercise the
    compiler's own fences, and the checker's own diagnostic for the
    same source is a separate lane.
    """
    import os
    import subprocess

    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    proc = subprocess.run(
        [sys.executable, "-m", "pyscriptc", "compile", "--skip-check", str(path)],
        cwd=ROOT,
        env=env,
        capture_output=True,
    )
    assert proc.returncode != 0
    return proc.stderr.decode("utf-8", "replace")


NO_INIT = """\
from dataclasses import dataclass, field


@dataclass
class NoInit:
    x: int
    y: int = field(default=1, init=False)
    zs: list[int] = field(default_factory=list, init=False)


one = NoInit(5)
two = NoInit(6)
one.zs.append(1)
print(one)
print(two)
print(one.zs is two.zs)
"""


@pytest.mark.skipif(not ZIG, reason="zig not on PATH")
def test_init_false_takes_the_field_out_of_the_constructor(tmp_path: Path) -> None:
    """`init=False`: no parameter, but the default (or factory) is written."""
    got = compile_and_run(tmp_path, "noinit.py", NO_INIT)
    assert got[1] == b""
    assert got[2] == 0
    assert [
        b"NoInit(x=5, y=1, zs=[1])",
        b"NoInit(x=6, y=1, zs=[])",
        b"False",
    ] == got[0].splitlines()


NO_INIT_NO_DEFAULT = """\
from dataclasses import dataclass, field


@dataclass
class NoInit:
    x: int
    y: int = field(init=False)
"""


@pytest.mark.skipif(not ZIG, reason="zig not on PATH")
def test_init_false_without_a_default_fences(tmp_path: Path) -> None:
    """CPython leaves the field unset; the lowered struct always has the slot."""
    from tests.test_gates import write_case

    path = write_case(tmp_path, "noinit2.py", NO_INIT_NO_DEFAULT)
    proc = compile_fences(path)
    assert "PS2002" in proc
    assert "field(init=False) needs a default" in proc


POST_INIT = """\
from dataclasses import dataclass


@dataclass
class Hooked:
    v: int

    def __post_init__(self) -> None:
        self.v = self.v * 2


@dataclass
class Child(Hooked):
    w: int = 1

    def __post_init__(self) -> None:
        super().__post_init__()
        self.w = self.w + 10


print(Hooked(3))
print(Child(3))
"""


@pytest.mark.skipif(not ZIG, reason="zig not on PATH")
def test_post_init_runs_after_the_fields(tmp_path: Path) -> None:
    """The hook reads the fields the generated init just wrote."""
    got = compile_and_run(tmp_path, "postinit.py", POST_INIT)
    assert got[1] == b""
    assert got[2] == 0
    assert [b"Hooked(v=6)", b"Child(v=6, w=11)"] == got[0].splitlines()


POST_INIT_INHERIT = """\
from dataclasses import dataclass


@dataclass
class Hooked:
    v: int

    def __post_init__(self) -> None:
        self.v = self.v * 2


@dataclass
class Child(Hooked):
    w: int = 1


print(Child(3))
"""


@pytest.mark.skipif(not ZIG, reason="zig not on PATH")
def test_post_init_inherits_through_the_mro(tmp_path: Path) -> None:
    """A child without its own hook runs the base's, the MRO answer."""
    got = compile_and_run(tmp_path, "postinherit.py", POST_INIT_INHERIT)
    assert got[1] == b""
    assert got[2] == 0
    assert [b"Child(v=6, w=1)"] == got[0].splitlines()


NON_DATACLASS_BASE = """\
class Plain:
    pass


from dataclasses import dataclass


@dataclass
class Child(Plain):
    x: int = 1
"""


@pytest.mark.skipif(not ZIG, reason="zig not on PATH")
def test_plain_base_still_fences(tmp_path: Path) -> None:
    """Only a dataclass base lowers; a plain class base keeps the fence."""
    from tests.test_gates import write_case

    path = write_case(tmp_path, "plain.py", NON_DATACLASS_BASE)
    proc = compile_fences(path)
    assert "PS2002" in proc


CACHED_PROPERTY_DATACLASS = """\
from dataclasses import dataclass
from functools import cached_property


@dataclass
class C:
    x: int

    @cached_property
    def y(self) -> int:
        return self.x + 1


print(C(1).y)
print(C(1) == C(1))
print(C(1))
"""


@pytest.mark.skipif(not ZIG, reason="zig not on PATH")
def test_a_cached_property_is_not_a_dataclass_field(tmp_path: Path) -> None:
    """The cached property's hidden slots ride the layout, not the fields.

    The hidden value slot and its computed flag are struct slots (issue
    #440), not dataclass fields: they take no constructor parameter,
    they stay out of the synthesized `__repr__` and `__eq__`, and the
    dataclass rules never see them.
    """
    got = compile_and_run(tmp_path, "cached.py", CACHED_PROPERTY_DATACLASS)
    assert got[1] == b""
    assert got[2] == 0
    assert [b"2", b"True", b"C(x=1)"] == got[0].splitlines()


BASE_CHILD_REPR = """\
from dataclasses import dataclass


@dataclass
class Base:
    x: int


@dataclass
class Child(Base):
    y: int


print(Child(1, 2))
print(Child(1, 2) == Child(1, 2))
print(Child(1, 2) == Child(1, 3))
"""


@pytest.mark.skipif(not ZIG, reason="zig not on PATH")
def test_the_child_repr_and_eq_cover_the_base_fields(tmp_path: Path) -> None:
    """CPython regenerates the child's dunders over the whole field list."""
    got = compile_and_run(tmp_path, "baserepr.py", BASE_CHILD_REPR)
    assert got[1] == b""
    assert got[2] == 0
    assert [b"Child(x=1, y=2)", b"True", b"False"] == got[0].splitlines()
