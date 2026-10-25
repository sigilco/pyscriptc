"""Regression tests for unmarshalling island function return values (issue #602).

A typed local receives the result of calling an island function across the seam
when annotated with int, float, bool, str, bytes, list[T], dict[K,V].
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pyscriptc", *args],
        cwd=cwd or ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_island_return_scalar_repro_fails_before_fix(tmp_path: Path) -> None:
    """Repro from issue #602: unmarshalling a function return value into typed int."""
    mod = tmp_path / "calc.py"
    mod.write_text("def first(items):\n    return items[0]\n", encoding="utf-8")
    (tmp_path / "pyscriptc.toml").write_text('[imports]\ncalc = "island"\n', encoding="utf-8")

    src = tmp_path / "repro.py"
    src.write_text(
        """import calc

def main() -> None:
    val: int = calc.first([10, 20])
    print(val)

if __name__ == "__main__":
    main()
""",
        encoding="utf-8",
    )
    binary = tmp_path / "repro_bin"
    comp = _run_cli("compile", str(src), "--emit", "bin", "-o", str(binary), cwd=tmp_path)
    assert comp.returncode == 0, comp.stderr

    native = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    cpython = subprocess.run([sys.executable, str(src)], capture_output=True, text=True, timeout=60, cwd=tmp_path)

    assert native.returncode == 0, native.stderr
    assert cpython.returncode == 0, cpython.stderr
    assert native.stdout == cpython.stdout
    assert native.stdout == "10\n"


def test_island_return_two_step_assign(tmp_path: Path) -> None:
    """Repro variant: res holds island handle first, then unmarshals into typed local."""
    mod = tmp_path / "calc.py"
    mod.write_text("def first(items):\n    return items[0]\n", encoding="utf-8")
    (tmp_path / "pyscriptc.toml").write_text('[imports]\ncalc = "island"\n', encoding="utf-8")

    src = tmp_path / "two_step.py"
    src.write_text(
        """import calc

def main() -> None:
    res = calc.first([42, 99])
    val: int = res
    print(val)

if __name__ == "__main__":
    main()
""",
        encoding="utf-8",
    )
    binary = tmp_path / "two_step_bin"
    comp = _run_cli("compile", str(src), "--emit", "bin", "-o", str(binary), cwd=tmp_path)
    assert comp.returncode == 0, comp.stderr

    native = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    cpython = subprocess.run([sys.executable, str(src)], capture_output=True, text=True, timeout=60, cwd=tmp_path)

    assert native.returncode == 0, native.stderr
    assert cpython.returncode == 0, cpython.stderr
    assert native.stdout == cpython.stdout
    assert native.stdout == "42\n"


def test_island_return_all_scalars(tmp_path: Path) -> None:
    """Scalars (int, float, bool, str, bytes) across the island boundary match CPython."""
    mod = tmp_path / "types_mod.py"
    mod.write_text(
        """def get_int():
    return 123456

def get_float():
    return 3.14159

def get_bool():
    return True

def get_str():
    return "hello world from island"

def get_bytes():
    return b"bytes-payload"
""",
        encoding="utf-8",
    )
    (tmp_path / "pyscriptc.toml").write_text('[imports]\ntypes_mod = "island"\n', encoding="utf-8")

    src = tmp_path / "scalars.py"
    src.write_text(
        """import types_mod

def main() -> None:
    i: int = types_mod.get_int()
    f: float = types_mod.get_float()
    b: bool = types_mod.get_bool()
    s: str = types_mod.get_str()
    by: bytes = types_mod.get_bytes()
    print(i, f, b, s, len(by))

if __name__ == "__main__":
    main()
""",
        encoding="utf-8",
    )
    binary = tmp_path / "scalars_bin"
    comp = _run_cli("compile", str(src), "--emit", "bin", "-o", str(binary), cwd=tmp_path)
    assert comp.returncode == 0, comp.stderr

    native = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    cpython = subprocess.run([sys.executable, str(src)], capture_output=True, text=True, timeout=60, cwd=tmp_path)

    assert native.returncode == 0, native.stderr
    assert cpython.returncode == 0, cpython.stderr
    assert native.stdout == cpython.stdout


def test_island_return_list_and_dict(tmp_path: Path) -> None:
    """list of scalars and dict of scalars across the island boundary."""
    mod = tmp_path / "containers_mod.py"
    mod.write_text(
        """def make_list(n):
    return [i * 2 for i in range(n)]

def make_str_list():
    return ["alpha", "beta", "gamma"]

def make_dict():
    return {"one": 1, "two": 2, "three": 3}

def make_str_dict():
    return {"k1": "v1", "k2": "v2"}
""",
        encoding="utf-8",
    )
    (tmp_path / "pyscriptc.toml").write_text('[imports]\ncontainers_mod = "island"\n', encoding="utf-8")

    src = tmp_path / "containers.py"
    src.write_text(
        """import containers_mod

def main() -> None:
    nums: list[int] = containers_mod.make_list(4)
    words: list[str] = containers_mod.make_str_list()
    mapping: dict[str, int] = containers_mod.make_dict()
    smap: dict[str, str] = containers_mod.make_str_dict()

    print(len(nums), nums[0], nums[3])
    print(len(words), words[1])
    print(len(mapping), mapping["two"])
    print(len(smap), smap["k1"])

if __name__ == "__main__":
    main()
""",
        encoding="utf-8",
    )
    binary = tmp_path / "containers_bin"
    comp = _run_cli("compile", str(src), "--emit", "bin", "-o", str(binary), cwd=tmp_path)
    assert comp.returncode == 0, comp.stderr

    native = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    cpython = subprocess.run([sys.executable, str(src)], capture_output=True, text=True, timeout=60, cwd=tmp_path)

    assert native.returncode == 0, native.stderr
    assert cpython.returncode == 0, cpython.stderr
    assert native.stdout == cpython.stdout


def test_island_return_pass_to_func_and_return_from_func(tmp_path: Path) -> None:
    """Island return values passed directly as function arguments and returns."""
    mod = tmp_path / "helper.py"
    mod.write_text(
        """def get_double(x):
    return x * 2
""",
        encoding="utf-8",
    )
    (tmp_path / "pyscriptc.toml").write_text('[imports]\nhelper = "island"\n', encoding="utf-8")

    src = tmp_path / "pass_return.py"
    src.write_text(
        """import helper

def take_int(x: int) -> int:
    return x + 5

def calc_value() -> int:
    return helper.get_double(10)

def main() -> None:
    v1: int = calc_value()
    v2: int = take_int(helper.get_double(7))
    print(v1, v2)

if __name__ == "__main__":
    main()
""",
        encoding="utf-8",
    )
    binary = tmp_path / "pass_return_bin"
    comp = _run_cli("compile", str(src), "--emit", "bin", "-o", str(binary), cwd=tmp_path)
    assert comp.returncode == 0, comp.stderr

    native = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    cpython = subprocess.run([sys.executable, str(src)], capture_output=True, text=True, timeout=60, cwd=tmp_path)

    assert native.returncode == 0, native.stderr
    assert cpython.returncode == 0, cpython.stderr
    assert native.stdout == cpython.stdout
    assert native.stdout == "20 19\n"


def test_island_type_mismatch_raises_typeerror(tmp_path: Path) -> None:
    """Runtime type mismatch on island return raises a catchable TypeError."""
    mod = tmp_path / "mismatch.py"
    mod.write_text(
        """from typing import Any

def ret_str() -> Any:
    return "unexpected string"

def ret_int() -> Any:
    return 123

def ret_bad_list() -> Any:
    return [1, "two", 3]

def ret_not_dict() -> Any:
    return [1, 2, 3]
""",
        encoding="utf-8",
    )
    (tmp_path / "pyscriptc.toml").write_text('[imports]\nmismatch = "island"\n', encoding="utf-8")

    src = tmp_path / "catch_mismatch.py"
    src.write_text(
        """import mismatch

def main() -> None:
    caught: int = 0
    try:
        val: int = mismatch.ret_str()
    except TypeError:
        caught = caught + 1

    try:
        b: bool = mismatch.ret_int()
    except TypeError:
        caught = caught + 1

    try:
        nums: list[int] = mismatch.ret_bad_list()
    except TypeError:
        caught = caught + 1

    try:
        mapping: dict[str, int] = mismatch.ret_not_dict()
    except TypeError:
        caught = caught + 1

    print("caught type mismatches:", caught)

if __name__ == "__main__":
    main()
""",
        encoding="utf-8",
    )
    binary = tmp_path / "catch_mismatch_bin"
    comp = _run_cli("compile", str(src), "--emit", "bin", "-o", str(binary), cwd=tmp_path)
    assert comp.returncode == 0, comp.stderr

    native = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    assert native.returncode == 0, native.stderr
    assert native.stdout == "caught type mismatches: 4\n"


def test_island_exception_propagation(tmp_path: Path) -> None:
    """Island exceptions propagate as catchable Python exceptions."""
    mod = tmp_path / "err_mod.py"
    mod.write_text(
        """def raise_val():
    raise ValueError("invalid value in island")

def raise_key():
    d = {"a": 1}
    return d["missing"]
""",
        encoding="utf-8",
    )
    (tmp_path / "pyscriptc.toml").write_text('[imports]\nerr_mod = "island"\n', encoding="utf-8")

    src = tmp_path / "catch_exc.py"
    src.write_text(
        """import err_mod

def main() -> None:
    caught_val: int = 0
    caught_key: int = 0
    try:
        v: int = err_mod.raise_val()
    except ValueError:
        caught_val = 1

    try:
        k: int = err_mod.raise_key()
    except KeyError:
        caught_key = 1

    print("exceptions caught:", caught_val, caught_key)

if __name__ == "__main__":
    main()
""",
        encoding="utf-8",
    )
    binary = tmp_path / "catch_exc_bin"
    comp = _run_cli("compile", str(src), "--emit", "bin", "-o", str(binary), cwd=tmp_path)
    assert comp.returncode == 0, comp.stderr

    native = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    assert native.returncode == 0, native.stderr
    assert native.stdout == "exceptions caught: 1 1\n"


def test_island_fences_unsupported_types_with_nearest_idiom(tmp_path: Path) -> None:
    """Unsupported return types (tuple, Optional, nested structures) fence with nearest idiom."""
    mod = tmp_path / "unsupported_mod.py"
    mod.write_text(
        """from typing import Any

def get_pair() -> Any:
    return (1, 2)

def get_opt() -> Any:
    return None
""",
        encoding="utf-8",
    )
    (tmp_path / "pyscriptc.toml").write_text('[imports]\nunsupported_mod = "island"\n', encoding="utf-8")

    # Tuple return
    tuple_src = tmp_path / "tuple_case.py"
    tuple_src.write_text(
        """import unsupported_mod

def main() -> None:
    res: tuple[int, int] = unsupported_mod.get_pair()
""",
        encoding="utf-8",
    )
    comp = _run_cli("compile", str(tuple_src), "--emit", "ir", cwd=tmp_path)
    assert comp.returncode != 0
    assert "PS1003" in comp.stderr
    assert "typed idiom: use list[T] to cross the island boundary" in comp.stderr

    # Optional return
    opt_src = tmp_path / "opt_case.py"
    opt_src.write_text(
        """import unsupported_mod

def main() -> None:
    res: int | None = unsupported_mod.get_opt()
""",
        encoding="utf-8",
    )
    comp = _run_cli("compile", str(opt_src), "--emit", "ir", cwd=tmp_path)
    assert comp.returncode != 0
    assert "PS1003" in comp.stderr
    assert "typed idiom: return a default scalar or handle None on the island" in comp.stderr
