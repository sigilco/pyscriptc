"""T2-1: TypeVar/Generic annotation erasure (tier-2 typed lowering).

`T = TypeVar('T')` declares a compile-time name, not a value. Annotations
on an unbound TypeVar are placeholders resolved per call site: each call
monomorphizes a concrete copy (`ident(1)` calls `ident.int`; `Box[int]`
instantiates `Box_int`). Bound TypeVars erase to their bound outright.
Runtime machinery stays fenced: a TypeVar is not a value, a generic
function is not a value, and a `none` field cannot instantiate.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import compile_run, oracle, run_cli, write_case


def test_t2_typevar_decl_is_a_noop(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "t2_decl.py",
        "\n".join(
            [
                "from typing import TypeVar",
                "",
                "T = TypeVar('T')",
                "",
                "def main() -> int:",
                "    return 1",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "main")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "main")


def test_t2_ident_on_int(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "t2_ident_int.py",
        "\n".join(
            [
                "from typing import TypeVar",
                "",
                "T = TypeVar('T')",
                "",
                "def ident(x: T) -> T:",
                "    return x",
                "",
                "def main() -> int:",
                "    return ident(7)",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "main")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "main")


def test_t2_ident_on_str(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "t2_ident_str.py",
        "\n".join(
            [
                "from typing import TypeVar",
                "",
                "T = TypeVar('T')",
                "",
                "def ident(x: T) -> T:",
                "    return x",
                "",
                "def main() -> str:",
                "    return ident('seven')",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "main")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "main")


def test_t2_ident_on_none(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "t2_ident_none.py",
        "\n".join(
            [
                "from typing import TypeVar",
                "",
                "T = TypeVar('T')",
                "",
                "def ident(x: T) -> T:",
                "    return x",
                "",
                "def main() -> str:",
                "    return str(ident(None))",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "main")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "main")


def test_t2_bound_typevar_erases_to_bound(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "t2_bound.py",
        "\n".join(
            [
                "from typing import TypeVar",
                "",
                "N = TypeVar('N', bound=int)",
                "",
                "def dbl(x: N) -> N:",
                "    return x",
                "",
                "def main() -> int:",
                "    return dbl(21)",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "main")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "main")


def test_t2_variance_flags_ignored(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "t2_variance.py",
        "\n".join(
            [
                "from typing import TypeVar",
                "",
                "V = TypeVar('V', covariant=True)",
                "W = TypeVar('W', contravariant=True)",
                "",
                "def take(x: W) -> int:",
                "    return 0",
                "",
                "def main() -> int:",
                "    return take(1)",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "main")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "main")


def test_t2_generic_class_alias_and_ctor(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "t2_box.py",
        "\n".join(
            [
                "from typing import TypeVar, Generic",
                "",
                "T = TypeVar('T')",
                "",
                "class Box(Generic[T]):",
                "    item: T",
                "",
                "    def __init__(self, item: T) -> None:",
                "        self.item = item",
                "",
                "    def get(self) -> T:",
                "        return self.item",
                "",
                "def pick(b: Box[int]) -> int:",
                "    return b.get()",
                "",
                "def main() -> int:",
                "    b: Box[int] = Box(3)",
                "    b.item = 4",
                "    return pick(b) + b.item",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "main")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "main")


def test_t2_generic_class_two_instantiations(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "t2_box_two.py",
        "\n".join(
            [
                "from typing import TypeVar, Generic",
                "",
                "T = TypeVar('T')",
                "",
                "class Box(Generic[T]):",
                "    item: T",
                "",
                "    def __init__(self, item: T) -> None:",
                "        self.item = item",
                "",
                "    def get(self) -> T:",
                "        return self.item",
                "",
                "def main() -> str:",
                "    n: Box[int] = Box(3)",
                "    s: Box[str] = Box('x')",
                "    return str(n.get()) + s.get()",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "main")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "main")


def test_t2_builtin_generic_alias_untouched(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "t2_list.py",
        "\n".join(
            [
                "from typing import TypeVar",
                "",
                "T = TypeVar('T')",
                "",
                "def main() -> int:",
                "    xs: list[int] = [1, 2, 3]",
                "    return len(xs)",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "main")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "main")


def test_t2_typevar_as_value_fenced(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "t2_tv_value.py",
        "\n".join(
            [
                "from typing import TypeVar",
                "",
                "T = TypeVar('T')",
                "",
                "def main() -> int:",
                "    return T(1)",
                "",
            ]
        ),
    )
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "t2"))
    assert got.returncode == 3
    assert "PS2002" in got.stderr


def test_t2_typevar_read_fenced(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "t2_tv_read.py",
        "\n".join(
            [
                "from typing import TypeVar",
                "",
                "T = TypeVar('T')",
                "",
                "def main() -> int:",
                "    print(T)",
                "    return 0",
                "",
            ]
        ),
    )
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "t2"))
    assert got.returncode == 3
    assert "PS2002" in got.stderr


def test_t2_generic_fn_as_value_fenced(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "t2_fn_value.py",
        "\n".join(
            [
                "from typing import TypeVar",
                "",
                "T = TypeVar('T')",
                "",
                "def ident(x: T) -> T:",
                "    return x",
                "",
                "def main() -> int:",
                "    print(ident)",
                "    return 0",
                "",
            ]
        ),
    )
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "t2"))
    assert got.returncode == 3
    assert "PS2002" in got.stderr


def test_t2_bare_typevar_local_fenced(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "t2_bare_tv.py",
        "\n".join(
            [
                "from typing import TypeVar",
                "",
                "T = TypeVar('T')",
                "",
                "def main() -> int:",
                "    x: T = 1",
                "    return x",
                "",
            ]
        ),
    )
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "t2"))
    assert got.returncode == 3
    assert "PS1001" in got.stderr


def test_t2_conflicting_typevar_binding_fenced(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "t2_conflict.py",
        "\n".join(
            [
                "from typing import TypeVar",
                "",
                "T = TypeVar('T')",
                "",
                "def same(x: T, y: T) -> T:",
                "    return x",
                "",
                "def main() -> int:",
                "    return same(1, 'two')",
                "",
            ]
        ),
    )
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "t2"))
    assert got.returncode == 3
    assert "PS1003" in got.stderr


def test_t2_none_instantiated_class_fenced(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "t2_none_class.py",
        "\n".join(
            [
                "from typing import TypeVar, Generic",
                "",
                "T = TypeVar('T')",
                "",
                "class Box(Generic[T]):",
                "    item: T",
                "",
                "    def __init__(self, item: T) -> None:",
                "        self.item = item",
                "",
                "def main() -> int:",
                "    b = Box(None)",
                "    return 0",
                "",
            ]
        ),
    )
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "t2"))
    assert got.returncode == 3
    assert "PS1003" in got.stderr


def test_t2_isinstance_on_generic_alias_fenced(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "t2_isinstance.py",
        "\n".join(
            [
                "from typing import TypeVar, Generic",
                "",
                "T = TypeVar('T')",
                "",
                "class Box(Generic[T]):",
                "    item: T",
                "",
                "    def __init__(self, item: T) -> None:",
                "        self.item = item",
                "",
                "def main() -> int:",
                "    b = Box(1)",
                "    print(isinstance(b, Box[int]))",
                "    return 0",
                "",
            ]
        ),
    )
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "t2_isinstance"))
    assert got.returncode == 3
    assert "PS" in got.stderr
