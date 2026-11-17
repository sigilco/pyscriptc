# SPDX-License-Identifier: Apache-2.0
"""`dataclasses.field(default_factory=...)` (issue #1279).

The lowering gives a dataclass field a per-instance default: every
construction that omits the field calls the factory, so two instances
never share a container. The three container factories (`list`, `dict`,
`set`) ride by name and build the empty container of the field's type; a
callable factory (a module function or a lambda) lowers to a callable
value and is called per construction.

The dataclass `__eq__` comes with it: CPython synthesizes it, so
`C(1) == C(1)` is True where the identity fallback answered False. The
generated body is the identity compare plus a short-circuiting chain over
the field pairs, so a container or nested-dataclass field compares by
value.

The committed differential probe
`probes/differential/dataclass_field_factory.py` is the AGENTS.md
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
from tests.test_runtime_divergences import compile_and_run, oracle_run

ZIG = shutil.which("zig") is not None
PROBE = ROOT / "probes" / "differential" / "dataclass_field_factory.py"


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
    got = compile_and_run(tmp_path, "probe.py", source)
    want = subprocess.run(
        [_oracle(), str(path)],
        capture_output=True,
        timeout=120,
        cwd=tmp_path,
    )
    assert got == (want.stdout, want.stderr, want.returncode)


CONTAINERS = """\
from dataclasses import dataclass, field


@dataclass
class Plain:
    tags: list[str] = field(default_factory=list)
    meta: dict[str, int] = field(default_factory=dict)
    flags: set[str] = field(default_factory=set)


one = Plain()
two = Plain()
one.tags.append("x")
one.meta["k"] = 1
one.flags.add("f")
print(one)
print(two)
print(one.tags is two.tags)
print(one.meta is two.meta)
"""


CALLABLE = """\
from dataclasses import dataclass, field


def make_tags() -> list[str]:
    return ["a", "b"]


@dataclass
class Factored:
    tags: list[str] = field(default_factory=make_tags)
    lams: list[int] = field(default_factory=lambda: [1, 2])


f1 = Factored()
f2 = Factored()
print(f1)
print(f1.tags is f2.tags)
f1.lams.append(3)
print(f2)
"""


MIXED = """\
from dataclasses import dataclass, field


@dataclass
class Mixed:
    name: str
    count: int = 0
    items: list[str] = field(default_factory=list)
    label: str = "d"


print(Mixed("n"))
print(Mixed("n", 2, ["a"], "b"))
"""


EQUALITY = """\
from dataclasses import dataclass, field


@dataclass
class Point:
    x: int
    y: int


@dataclass
class Box:
    corner: Point = field(default_factory=lambda: Point(0, 0))
    tags: list[str] = field(default_factory=list)


b1 = Box()
b2 = Box()
print(b1 == b2)
print(Box(Point(1, 2), ["t"]) == Box(Point(1, 2), ["t"]))
print(Box(Point(1, 2)) != Box(Point(1, 3)))
print(b1 == b1)
"""


@pytest.mark.skipif(not ZIG, reason="zig not on PATH")
@pytest.mark.parametrize(
    "name, source",
    sorted(
        {
            "containers": CONTAINERS,
            "callable": CALLABLE,
            "mixed": MIXED,
            "equality": EQUALITY,
        }.items(),
    ),
)
def test_matches_cpython(tmp_path: Path, name: str, source: str) -> None:
    got = compile_and_run(tmp_path, name + ".py", source)
    want = oracle_run(tmp_path, name + ".py", source)
    assert got == want


FENCES = {
    # A plain mutable default is CPython's ValueError, so the factory is
    # the only mutable spelling the subset carries.
    "plain_default": (
        "from dataclasses import dataclass, field\n\n\n@dataclass\nclass D:\n    xs: list[int] = field()\n\n\nD()\n"
    ),
    # A factory keyword other than `default_factory` is not lowered.
    "other_keyword": (
        "from dataclasses import dataclass, field\n"
        "\n"
        "\n"
        "@dataclass\n"
        "class D:\n"
        "    xs: list[int] = field(default=[1])\n"
        "\n"
        "\n"
        "D()\n"
    ),
    # A container factory needs a container annotation.
    "container_mismatch": (
        "from dataclasses import dataclass, field\n"
        "\n"
        "\n"
        "@dataclass\n"
        "class D:\n"
        "    n: int = field(default_factory=list)\n"
        "\n"
        "\n"
        "D()\n"
    ),
    # A callable factory takes no arguments.
    "factory_with_args": (
        "from dataclasses import dataclass, field\n"
        "\n"
        "\n"
        "def make(n: int) -> list[int]:\n"
        "    return [n]\n"
        "\n"
        "\n"
        "@dataclass\n"
        "class D:\n"
        "    xs: list[int] = field(default_factory=make)\n"
        "\n"
        "\n"
        "D()\n"
    ),
}


@pytest.mark.skipif(not ZIG, reason="zig not on PATH")
@pytest.mark.parametrize("name", sorted(FENCES))
def test_fences_hold(tmp_path: Path, name: str) -> None:
    """The shapes the subset does not carry keep their fences.

    The compile skips the checker preflight (`--skip-check`) so the
    lowering's own fences answer, not the checker's earlier complaint.
    """
    path = tmp_path / (name + ".py")
    path.write_text(FENCES[name], encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(path),
            "--skip-check",
            "-o",
            str(tmp_path / name),
        ],
        cwd=ROOT,
        capture_output=True,
        env={**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")},
    )
    assert proc.returncode != 0
    assert b"PS" in proc.stderr
