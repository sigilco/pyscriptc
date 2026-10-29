"""Class body constant assignments lower and read like CPython's.

`NAME = <literal>` (or a tuple of literals) in a class body is an
immutable class constant: the value inlines at every read site, resolved
through the receiver's static MRO. A subclass override wins for its own
receivers; a shared method reading through a base receiver fences
instead of silently reading the base's value.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from tests.test_gates import run_cli, write_case

ROOT = Path(__file__).resolve().parents[1]

CASES: dict[str, str] = {
    # read through the class, an instance, and a classmethod
    "read_shapes": """
class Base:
    NAME = "base"
    NUM = 3
    F = 2.5
    FLAG = True
    RAW = b"x"
    NOTHING = None
    PAIR = ("a", 2)

    def via_self(self) -> str:
        return self.NAME

    @classmethod
    def via_cls(cls) -> str:
        return cls.NAME


def by_class() -> str:
    return Base.NAME


def by_instance() -> str:
    c: Base = Base()
    return c.NAME


def by_self() -> str:
    c: Base = Base()
    return c.via_self()


def by_cls() -> str:
    return Base.via_cls()


def scalars() -> int:
    c: Base = Base()
    n: int = c.NUM
    f: float = c.F
    total: int = n + int(f)
    if c.FLAG:
        total = total + 1
    total = total + len(c.RAW)
    if c.NOTHING is None:
        total = total + 10
    return total


def pair() -> str:
    a: str
    b: int
    a, b = Base.PAIR
    return a + str(b)
""",
    # subclass override: the override wins for the subclass's receivers,
    # the base keeps its own, and a subclass that redefines the reading
    # method reads its own constant
    "subclass_override": """
class Base:
    NAME = "base"

    def via_self(self) -> str:
        return self.NAME

    @classmethod
    def via_cls(cls) -> str:
        return cls.NAME


class Sub(Base):
    NAME = "sub"

    def via_self(self) -> str:
        return self.NAME

    @classmethod
    def via_cls(cls) -> str:
        return cls.NAME


class Plain(Base):
    pass


def base_class() -> str:
    return Base.NAME


def sub_class() -> str:
    return Sub.NAME


def base_instance() -> str:
    b: Base = Base()
    return b.via_self()


def sub_instance() -> str:
    s: Sub = Sub()
    return s.via_self()


def plain_instance() -> str:
    p: Plain = Plain()
    return p.via_self()


def base_cls_method() -> str:
    return Base.via_cls()


def sub_cls_method() -> str:
    return Sub.via_cls()
""",
    # an extractor-style table class: constants beside dict tables and methods
    "extractor": """
_TABLE: dict[str, int] = {"a": 1}


class Extractor:
    _NAME = "ext"
    _VALID_URL = "https://example.com/watch"
    _TESTS = ("one", 2)
    KIND = True

    def suit(self) -> str:
        return self._NAME + ":" + self._VALID_URL

    @classmethod
    def table_key(cls) -> str:
        return cls._NAME


def suit() -> str:
    e: Extractor = Extractor()
    return e.suit()


def table_key() -> str:
    return Extractor.table_key()


def tests_pair() -> str:
    a: str
    b: int
    a, b = Extractor._TESTS
    return a + str(b) + str(len(_TABLE))
""",
}

OVERRIDE_FENCE = """
class Base:
    NAME = "base"

    def via_self(self) -> str:
        return self.NAME


class Sub(Base):
    NAME = "sub"


def main() -> str:
    s: Sub = Sub()
    return s.via_self()
"""

NON_CONST_FENCE = """
class C:
    x = len("y")


def make() -> int:
    return C.x + len("y")
"""

WRITE_FENCE = """
class C:
    NAME = "base"


def rename(c: C) -> None:
    c.NAME = "sub"
"""


CASES["non_const_fence"] = """
class C:
    x = 1


def make() -> int:
    return C.x + len("y")
"""


def _cpython(path: Path, func: str) -> str:
    source = (
        "import importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('mod', r'{path}')\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n"
        f"value = mod.{func}()\n"
        "print('%.17g' % value if isinstance(value, float) else value)\n"
    )
    proc = subprocess.run([sys.executable, "-c", source], cwd=ROOT, capture_output=True, text=True, check=True)
    return proc.stdout.strip()


@pytest.mark.parametrize("case", sorted(CASES), ids=str)
@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_class_constants_match_cpython(tmp_path: Path, case: str) -> None:
    source = CASES[case].strip() + "\n"
    path = write_case(tmp_path, f"{case}.py", source)
    functions = [line.split("def ")[1].split("(")[0] for line in source.splitlines() if line.startswith("def ")]
    assert functions, f"{case} declares no functions to compare"
    for func in functions:
        oracle_value = _cpython(path, func)
        result = run_cli(
            "compile",
            str(path),
            "--run",
            "--call",
            func,
            "--args",
            "[]",
            "-o",
            str(tmp_path / func),
        )
        assert result.returncode == 0, f"{case}.{func}: compile failed:\\n{result.stdout}\\n{result.stderr}"
        native = subprocess.run(
            [str(tmp_path / func)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert native.returncode == 0, f"{case}.{func}: native run failed:\\n{native.stderr}"
        assert native.stdout.strip() == oracle_value, (
            f"{case}.{func}: native {native.stdout.strip()!r} != cpython {oracle_value!r}"
        )


@pytest.mark.linux_container
def test_shared_method_reading_overridden_constant_fences(tmp_path: Path) -> None:
    path = write_case(tmp_path, "fence.py", OVERRIDE_FENCE.strip() + "\n")
    result = run_cli("coverage", str(path))
    assert result.returncode == 0
    assert "PS2001" in result.stdout
    assert "a subclass of Base redefines" in result.stdout


@pytest.mark.linux_container
def test_non_constant_rhs_keeps_ps1204(tmp_path: Path) -> None:
    path = write_case(tmp_path, "nonconst.py", NON_CONST_FENCE.strip() + "\n")
    result = run_cli("coverage", str(path))
    assert result.returncode == 0
    assert "PS1204" in result.stdout


@pytest.mark.linux_container
def test_class_constant_write_fences(tmp_path: Path) -> None:
    path = write_case(tmp_path, "write.py", WRITE_FENCE.strip() + "\n")
    result = run_cli("coverage", str(path))
    assert result.returncode == 0
    assert "read-only" in result.stdout


@pytest.mark.linux_container
def test_annotated_field_with_value_still_fences_ps1204(tmp_path: Path) -> None:
    path = write_case(tmp_path, "annot.py", 'class C:\n    x: str = "d"\n')
    result = run_cli("coverage", str(path))
    assert result.returncode == 0
    assert "PS1204" in result.stdout
