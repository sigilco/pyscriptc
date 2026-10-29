"""Class body constant assignments lower and read like CPython's.

`NAME = <literal>` (or a tuple of literals) in a class body is an
immutable class constant: the value inlines at every read site, resolved
through the receiver's static MRO. A subclass override wins for its own
receivers; a shared method reading through a base receiver fences
instead of silently reading the base's value.

A list or dict display binds once to a module-level global slot, so every
read is the same object and a mutation through any alias is visible to
every other read, which is what CPython's single class attribute gives.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from tests.test_gates import compile_run, run_cli, write_case

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
    # a list or dict constant binds once: every read is the same object,
    # and a mutation through any alias is visible to every other read
    "container_shared": """
class Base:
    L = [1, 2]
    D = {"a": 1}


def read_len() -> int:
    return len(Base.L)


def read_dict() -> int:
    return Base.D["a"]


def mutate_alias() -> int:
    alias: list[int] = Base.L
    alias.append(3)
    return len(Base.L)


def mutate_then_read() -> int:
    alias: list[int] = Base.L
    alias.append(4)
    return len(Base.L)


def dict_mutate() -> int:
    d: dict[str, int] = Base.D
    d["b"] = 2
    return len(Base.D)


def read_dict_after() -> int:
    d: dict[str, int] = Base.D
    d["c"] = 3
    return len(Base.D)
""",
    # a container constant read through the class, an instance and a
    # classmethod is the same object
    "container_read_shapes": """
class Base:
    L = [1, 2]

    def via_self(self) -> int:
        return len(self.L)

    @classmethod
    def via_cls(cls) -> int:
        return len(cls.L)


def by_class() -> int:
    return len(Base.L)


def by_instance() -> int:
    b: Base = Base()
    return len(b.L)


def by_self() -> int:
    b: Base = Base()
    return b.via_self()


def by_cls() -> int:
    return Base.via_cls()


def instance_sees_class_mutation() -> int:
    alias: list[int] = Base.L
    alias.append(3)
    b: Base = Base()
    return len(b.L)
""",
    # a subclass that reassigns the container constant wins for its own
    # receivers, and the base keeps its own object
    "container_subclass_override": """
class Base:
    L = [1, 2]

    def via_self(self) -> int:
        return self.L[0]

    @classmethod
    def via_cls(cls) -> int:
        return cls.L[0]


class Sub(Base):
    L = [3, 4]

    def via_self(self) -> int:
        return self.L[0]

    @classmethod
    def via_cls(cls) -> int:
        return cls.L[0]


class Plain(Base):
    pass


def base_class() -> int:
    return Base.L[0]


def sub_class() -> int:
    return Sub.L[0]


def base_instance() -> int:
    b: Base = Base()
    return b.via_self()


def sub_instance() -> int:
    s: Sub = Sub()
    return s.via_self()


def plain_instance() -> int:
    p: Plain = Plain()
    return p.via_self()


def base_cls_method() -> int:
    return Base.via_cls()


def sub_cls_method() -> int:
    return Sub.via_cls()
""",
    # the base and the subclass hold separate objects: mutating one leaves
    # the other alone
    "container_override_isolated": """
class Base:
    L = [1, 2]


class Sub(Base):
    L = [3, 4]


def mutate_base() -> int:
    alias: list[int] = Base.L
    alias.append(9)
    return len(Base.L)


def base_after() -> int:
    alias: list[int] = Base.L
    alias.append(9)
    return len(Sub.L)


def sub_after() -> int:
    alias: list[int] = Sub.L
    alias.append(9)
    return len(Sub.L)


def base_len() -> int:
    return len(Base.L)
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


@pytest.mark.linux_container
def test_empty_container_display_still_fences_ps1204(tmp_path: Path) -> None:
    for source in ("class C:\n    x = []\n", "class C:\n    x = {}\n"):
        path = write_case(tmp_path, "empty.py", source)
        result = run_cli("coverage", str(path))
        assert result.returncode == 0
        assert "PS1204" in result.stdout, source


@pytest.mark.linux_container
def test_comprehension_constant_still_fences_ps1204(tmp_path: Path) -> None:
    path = write_case(tmp_path, "comp.py", "class C:\n    x = [i for i in [1, 2]]\n")
    result = run_cli("coverage", str(path))
    assert result.returncode == 0
    assert "PS1204" in result.stdout


@pytest.mark.linux_container
def test_container_constant_write_fences(tmp_path: Path) -> None:
    path = write_case(tmp_path, "write.py", "class C:\n    L = [1, 2]\n\n\ndef w(c: C) -> None:\n    c.L = [3]\n")
    result = run_cli("coverage", str(path))
    assert result.returncode == 0
    assert "read-only" in result.stdout


@pytest.mark.linux_container
def test_shared_method_reading_overridden_container_constant_fences(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "fence.py",
        "class Base:\n"
        "    L = [1, 2]\n"
        "\n"
        "    def first(self) -> int:\n"
        "        return self.L[0]\n"
        "\n"
        "\n"
        "class Sub(Base):\n"
        "    L = [3, 4]\n"
        "\n"
        "\n"
        "def main() -> int:\n"
        "    s: Sub = Sub()\n"
        "    return s.first()\n",
    )
    result = run_cli("coverage", str(path))
    assert result.returncode == 0
    assert "PS2001" in result.stdout
    assert "a subclass of Base redefines" in result.stdout


@pytest.mark.linux_container
def test_container_constant_crosses_modules_as_one_object(tmp_path: Path) -> None:
    """A consumer module reads the defining module's object, not a copy."""
    root = tmp_path / "labeler"
    root.mkdir()
    (root / "__init__.py").write_text("", encoding="utf-8")
    (root / "base.py").write_text(
        "class Base:\n"
        "    L = [1, 2]\n"
        "\n"
        "\n"
        "def grow() -> int:\n"
        "    alias: list[int] = Base.L\n"
        "    alias.append(3)\n"
        "    return len(Base.L)\n",
        encoding="utf-8",
    )
    (root / "app.py").write_text(
        "from .base import Base, grow\n"
        "\n"
        "\n"
        "def read() -> int:\n"
        "    return len(Base.L)\n"
        "\n"
        "\n"
        "def after_growth() -> int:\n"
        "    g: int = grow()\n"
        "    r: int = len(Base.L)\n"
        "    return g * 100 + r\n",
        encoding="utf-8",
    )
    read = compile_run(tmp_path, root, "labeler.app.read")
    assert read.returncode == 0, read.stderr
    assert read.stdout == "2\n"
    after = compile_run(tmp_path, root, "labeler.app.after_growth")
    assert after.returncode == 0, after.stderr
    assert after.stdout == "303\n"
