"""Tests for `pyscriptc compile ... --emit binding` (issue #457)."""

from __future__ import annotations

import importlib
import json
import shutil
import sys
from pathlib import Path

import pytest
from tests.test_gates import run_cli, write_case

needs_clang = pytest.mark.skipif(shutil.which("clang") is None, reason="clang required")

TYPED_SOURCE = """
X: int = 10


def add(a: int, b: int) -> int:
    return a + b + X


def multiply(x: float, y: float) -> float:
    return x * y


def is_positive(x: float) -> bool:
    return x > 0.0


def negate(b: bool) -> bool:
    return not b


def scale(a: int, factor: int = 2) -> int:
    return a * factor


def noop() -> None:
    pass
"""

MIXED_SOURCE = """
def add(a: int, b: int) -> int:
    return a + b


def greet(name: str) -> str:
    return name


def process_items(items: list[int]) -> int:
    return len(items)


class Counter:
    val: int

    def inc(self) -> int:
        self.val = self.val + 1
        return self.val
"""


@needs_clang
def test_binding_compile_import_and_compare(tmp_path: Path) -> None:
    path = write_case(tmp_path, "mathmod.py", TYPED_SOURCE)
    got = run_cli("compile", str(path), "--emit", "binding", "--skip-check")
    assert got.returncode == 0, got.stderr

    binding_path = tmp_path / "mathmod_binding.py"
    assert binding_path.is_file()
    assert got.stdout.strip() == str(binding_path)

    # The shared library was produced next to the binding module
    shlibs = [f for f in tmp_path.glob("libmathmod.*") if f.suffix in (".dylib", ".so", ".dll")]
    assert len(shlibs) == 1
    assert shlibs[0].suffix in (".dylib", ".so", ".dll")

    # Import both the original source and the binding, and compare results
    sys.path.insert(0, str(tmp_path))
    try:
        source_mod = importlib.import_module("mathmod")
        binding_mod = importlib.import_module("mathmod_binding")

        # Compare int operations
        assert binding_mod.add(5, 7) == source_mod.add(5, 7)
        assert binding_mod.add(a=5, b=7) == 22

        # Compare float operations
        assert binding_mod.multiply(3.5, 2.0) == source_mod.multiply(3.5, 2.0)
        assert binding_mod.is_positive(1.5) == source_mod.is_positive(1.5)
        assert binding_mod.is_positive(-1.5) == source_mod.is_positive(-1.5)

        # Compare bool operations
        assert binding_mod.negate(True) == source_mod.negate(True)
        assert binding_mod.negate(False) == source_mod.negate(False)

        # Compare default arguments and keyword calls
        assert binding_mod.scale(10) == source_mod.scale(10)
        assert binding_mod.scale(10, 5) == source_mod.scale(10, 5)
        assert binding_mod.scale(a=10, factor=5) == source_mod.scale(a=10, factor=5)

        # None return
        assert binding_mod.noop() is None

        # Check argtypes and restype exposed on plain callables
        import ctypes

        assert binding_mod.add.argtypes == [ctypes.c_int64, ctypes.c_int64]
        assert binding_mod.add.restype == ctypes.c_int64
        assert binding_mod.is_positive.argtypes == [ctypes.c_double]
        assert binding_mod.is_positive.restype == ctypes.c_bool
        assert binding_mod.negate.argtypes == [ctypes.c_bool]
        assert binding_mod.negate.restype == ctypes.c_bool

        # Check __all__ and CompiledError
        assert issubclass(binding_mod.CompiledError, RuntimeError)
        assert "add" in binding_mod.__all__
        assert "CompiledError" in binding_mod.__all__
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("mathmod", None)
        sys.modules.pop("mathmod_binding", None)


@needs_clang
def test_binding_skipped_functions_and_summary(tmp_path: Path) -> None:
    path = write_case(tmp_path, "mixed.py", MIXED_SOURCE)
    dbg_dir = tmp_path / "dbg"
    got = run_cli(
        "compile",
        str(path),
        "--emit",
        "binding",
        "--skip-check",
        "--debug-dir",
        str(dbg_dir),
    )
    assert got.returncode == 0, got.stderr

    binding_path = tmp_path / "mixed_binding.py"
    assert binding_path.is_file()
    content = binding_path.read_text()

    # Docstring contains named notes for skipped items
    assert "Skipped:" in content
    assert "greet" in content
    assert "process_items" in content
    assert "Counter" in content

    # Compatible function is exported and callable
    sys.path.insert(0, str(tmp_path))
    try:
        binding_mod = importlib.import_module("mixed_binding")
        assert binding_mod.add(10, 20) == 30
        assert not hasattr(binding_mod, "greet")
        assert not hasattr(binding_mod, "process_items")
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("mixed_binding", None)

    # Check compile summary carries nonzero skipped count
    summary = json.loads((dbg_dir / "summary.json").read_text())
    assert "binding" in summary
    assert summary["binding"]["emitted"] == ["add"]
    assert summary["binding"]["skippedCount"] >= 3
    assert summary["bindingSkipped"] >= 3


@needs_clang
def test_binding_out_dir_option(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    out_dir = tmp_path / "dist"
    src_dir.mkdir()
    out_dir.mkdir()
    path = write_case(src_dir, "libmod.py", TYPED_SOURCE)

    got = run_cli(
        "compile",
        str(path),
        "--emit",
        "binding",
        "--skip-check",
        "--out-dir",
        str(out_dir),
    )
    assert got.returncode == 0, got.stderr

    expected_binding = out_dir / "libmod_binding.py"
    assert expected_binding.is_file()
    assert got.stdout.strip() == str(expected_binding)
    shlibs = [f for f in out_dir.glob("liblibmod.*") if f.suffix in (".dylib", ".so", ".dll")]
    assert len(shlibs) == 1


@needs_clang
def test_binding_explicit_output_path(tmp_path: Path) -> None:
    src = tmp_path / "calc.py"
    out = tmp_path / "custom" / "mycalc_binding.py"
    write_case(tmp_path, "calc.py", "def add(a: int, b: int) -> int: return a + b\n")

    got = run_cli(
        "compile",
        str(src),
        "--emit",
        "binding",
        "--skip-check",
        "-o",
        str(out),
    )
    assert got.returncode == 0, got.stderr
    assert out.is_file()
    assert got.stdout.strip() == str(out)
    shlibs = [f for f in out.parent.glob("libmycalc.*") if f.suffix in (".dylib", ".so", ".dll")]
    assert len(shlibs) == 1


@needs_clang
def test_binding_sanitize_smoke(tmp_path: Path) -> None:
    src = tmp_path / "san.py"
    write_case(tmp_path, "san.py", "def add(a: int, b: int) -> int: return a + b\n")

    got = run_cli(
        "compile",
        str(src),
        "--emit",
        "binding",
        "--skip-check",
        "--sanitize",
        "address",
    )
    assert got.returncode == 0, got.stderr
    binding = tmp_path / "san_binding.py"
    assert binding.is_file()
