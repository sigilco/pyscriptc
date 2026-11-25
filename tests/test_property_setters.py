# SPDX-License-Identifier: Apache-2.0
"""Property setters (issue #439, D4a).

A class that defines a `@property` getter and the matching
`@<name>.setter` lowers the setter as the property's write path:
`c.a = v` calls the setter method, the way CPython's descriptor
protocol does, and the value is checked against the setter's own
parameter type. The getter keeps the property name (the read site
resolves it); the setter lowers under the mangled `name.__set__`
spelling, so the two bodies never collide in the emitted class record.

The class-level surface stays fenced with a named diagnostic: `C.a`
reads the property object in CPython and `C.a = v` writes the class
attribute, and the compiled subset has neither. An instance write on a
getter without a setter names the read-only property.

The committed differential probe
`probes/differential/property_setters.py` is the AGENTS.md
differential-probe row for the lowering; the test here is the
pinned-oracle replay of the same surface.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.test_gates import ROOT, run_cli, write_case
from tests.test_runtime_divergences import compile_and_run, oracle_run

PROBE = ROOT / "probes" / "differential" / "property_setters.py"


def _oracle() -> str:
    """The pinned CPython 3.14 interpreter, when it is on this machine."""
    pinned = Path.home() / ".local/share/uv/python"
    candidates = sorted(pinned.glob("cpython-3.14.*-*/bin/python3"))
    if candidates:
        return str(candidates[-1])
    return sys.executable


WITH_SETTER = """\
class Temperature:
    _celsius: float

    def __init__(self, celsius: float) -> None:
        self._celsius = celsius

    @property
    def celsius(self) -> float:
        return self._celsius

    @celsius.setter
    def celsius(self, value: float) -> None:
        self._celsius = value


def main() -> int:
    t = Temperature(20.0)
    print(t.celsius)
    t.celsius = 100.0
    print(t.celsius)
    print(t._celsius)
    return 0


main()
"""


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


def test_an_instance_write_dispatches_to_the_setter(tmp_path: Path) -> None:
    """`c.a = v` calls the setter, and the getter reads the setter's write."""
    got = compile_and_run(tmp_path, "setter.py", WITH_SETTER)
    want = oracle_run(tmp_path, "setter.py", WITH_SETTER)
    assert got == want


def test_the_setter_body_runs(tmp_path: Path) -> None:
    """The setter's own body is what the write runs, not a field write."""
    source = """\
class Box:
    _n: int

    def __init__(self, n: int) -> None:
        self._n = n

    @property
    def n(self) -> int:
        return self._n

    @n.setter
    def n(self, value: int) -> None:
        self._n = value * 2


def main() -> int:
    b = Box(3)
    b.n = 21
    print(b.n)
    print(b._n)
    return 0


main()
"""
    got = compile_and_run(tmp_path, "body.py", source)
    want = oracle_run(tmp_path, "body.py", source)
    assert got == want
    assert got[0] == b"42\n42\n"


def test_a_setter_in_a_base_class_serves_the_subclass(tmp_path: Path) -> None:
    """The write path follows the MRO: the base's setter serves the child."""
    source = """\
class Base:
    _n: int

    def __init__(self, n: int) -> None:
        self._n = n

    @property
    def n(self) -> int:
        return self._n

    @n.setter
    def n(self, value: int) -> None:
        self._n = value + 1


class Child(Base):
    pass


def main() -> int:
    c = Child(3)
    c.n = 10
    print(c.n)
    b = Base(3)
    b.n = 10
    print(b.n)
    return 0


main()
"""
    got = compile_and_run(tmp_path, "inherit.py", source)
    want = oracle_run(tmp_path, "inherit.py", source)
    assert got == want


def test_a_write_to_a_getter_without_a_setter_fences(tmp_path: Path) -> None:
    """CPython raises AttributeError there; the compile names the property."""
    source = """\
class Box:
    _n: int

    def __init__(self, n: int) -> None:
        self._n = n

    @property
    def n(self) -> int:
        return self._n


def main() -> int:
    b = Box(3)
    b.n = 21
    return 0


main()
"""
    path = write_case(tmp_path, "readonly.py", source)
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "out.ll"))
    assert got.returncode == 3, got.stderr
    assert "PS2001" in got.stderr, got.stderr
    assert "Box.n is a read-only property" in got.stderr, got.stderr


def test_the_class_level_read_fences_with_its_own_message(tmp_path: Path) -> None:
    """`C.a` reads the property object in CPython; the subset has none."""
    source = """\
class Box:
    _n: int

    def __init__(self, n: int) -> None:
        self._n = n

    @property
    def n(self) -> int:
        return self._n


def main() -> int:
    print(Box.n)
    return 0


main()
"""
    path = write_case(tmp_path, "classread.py", source)
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "out.ll"))
    assert got.returncode == 3, got.stderr
    assert "PS2002" in got.stderr, got.stderr
    assert "reads the property object" in got.stderr, got.stderr


def test_the_class_level_write_fences_with_its_own_message(tmp_path: Path) -> None:
    """`C.a = v` writes the class attribute; the subset keeps state on instances."""
    source = """\
class Box:
    _n: int

    def __init__(self, n: int) -> None:
        self._n = n

    @property
    def n(self) -> int:
        return self._n


def main() -> int:
    Box.n = 5
    return 0


main()
"""
    path = write_case(tmp_path, "classwrite.py", source)
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "out.ll"))
    assert got.returncode == 3, got.stderr
    assert "PS2002" in got.stderr, got.stderr
    assert "writes the property on the class" in got.stderr, got.stderr


def test_a_setter_without_a_property_fences(tmp_path: Path) -> None:
    """CPython's class body raises AttributeError; the compile fences."""
    source = """\
class Box:
    _n: int

    def __init__(self, n: int) -> None:
        self._n = n

    @n.setter
    def n(self, value: int) -> None:
        self._n = value


def main() -> int:
    b = Box(3)
    return 0


main()
"""
    path = write_case(tmp_path, "nosetter.py", source)
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "out.ll"))
    assert got.returncode == 3, got.stderr
    assert "PS2002" in got.stderr, got.stderr
    assert "no property n for the setter" in got.stderr, got.stderr


def test_the_setter_value_type_is_checked(tmp_path: Path) -> None:
    """The setter's parameter type is the write's type."""
    source = """\
class Box:
    _n: int

    def __init__(self, n: int) -> None:
        self._n = n

    @property
    def n(self) -> int:
        return self._n

    @n.setter
    def n(self, value: int) -> None:
        self._n = value


def main() -> int:
    b = Box(3)
    b.n = "x"
    return 0


main()
"""
    path = write_case(tmp_path, "mismatch.py", source)
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "out.ll"))
    assert got.returncode == 3, got.stderr
    assert "PS1003" in got.stderr, got.stderr


def test_a_getter_alone_still_lowers(tmp_path: Path) -> None:
    """The setter lift does not touch the getter-only class."""
    source = """\
class Box:
    _n: int

    def __init__(self, n: int) -> None:
        self._n = n

    @property
    def n(self) -> int:
        return self._n * 2


def main() -> int:
    b = Box(3)
    print(b.n)
    return 0


main()
"""
    got = compile_and_run(tmp_path, "getter.py", source)
    want = oracle_run(tmp_path, "getter.py", source)
    assert got == want
