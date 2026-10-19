"""End-to-end checks for the builtin-subset spike."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}


def run_cli(
    *args: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pyscriptc", *args],
        cwd=ROOT if cwd is None else cwd,
        env=ENV if env is None else env,
        capture_output=True,
        text=True,
    )


def oracle(source: str) -> str:
    proc = subprocess.run(
        [sys.executable, "-c", source],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout


def test_sum_squares_ir_is_python_not_javascript() -> None:
    from pyscriptc.lower import lower_file

    module, _source = lower_file(str(EXAMPLES / "sum_squares.py"))
    assert module["irVersion"] == 1
    assert module["subset"] == "gates-v1"
    assert module["cpython"].startswith("3.14.")
    # E1 items 1+2: the module-level print lowers into the synthesized entry
    # and to a stdout write, so the script compiles with no fences.
    assert module["fences"] == []
    entry = next(fn for fn in module["functions"] if fn["name"] == module["entry"])
    assert entry["name"] == "main"
    assert [stmt["kind"] for stmt in entry["body"]] == ["expr"]
    fn = module["functions"][0]
    assert fn["name"] == "sum_squares"
    assert fn["returnType"] == {"kind": "int"}
    assert any(stmt["kind"] == "while" for stmt in fn["body"])
    dumped = json.dumps(module)
    assert "f64" not in dumped
    assert "nullT" not in dumped


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_relative_output_is_cwd_not_path(tmp_path: Path) -> None:
    work = tmp_path / "work"
    decoy_dir = tmp_path / "decoy-bin"
    work.mkdir()
    decoy_dir.mkdir()
    decoy = decoy_dir / "prog.bin"
    decoy.write_text("#!/bin/sh\necho DECOY\n", encoding="utf-8")
    decoy.chmod(0o755)
    env = {**ENV, "PATH": f"{decoy_dir}{os.pathsep}{os.environ['PATH']}"}
    proc = run_cli(
        "compile",
        str(EXAMPLES / "sum_squares.py"),
        "--run",
        "--call",
        "sum_squares",
        "--args",
        "[10]",
        "-o",
        "prog.bin",
        cwd=work,
        env=env,
    )
    written = work / "prog.bin"
    assert proc.returncode == 0, proc.stderr
    assert written.is_file()
    assert decoy.read_text(encoding="utf-8").startswith("#!/bin/sh")
    assert proc.stdout == "285\n"


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_sum_squares_matches_cpython(tmp_path: Path) -> None:
    binary = tmp_path / "sum_squares"
    proc = run_cli(
        "compile",
        str(EXAMPLES / "sum_squares.py"),
        "--run",
        "--call",
        "sum_squares",
        "--args",
        "[10]",
        "-o",
        str(binary),
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "285\n"
    ll = Path(str(binary) + ".ll").read_text()
    assert "llvm.sadd.with.overflow" in ll
    assert "llvm.smul.with.overflow" in ll
    for n in (0, 1, 2, 20):
        got = run_cli(
            "compile",
            str(EXAMPLES / "sum_squares.py"),
            "--run",
            "--call",
            "sum_squares",
            "--args",
            f"[{n}]",
            "-o",
            str(tmp_path / f"ss{n}"),
        )
        expect = oracle(
            "import importlib.util; "
            f"spec = importlib.util.spec_from_file_location('ss', r'{EXAMPLES / 'sum_squares.py'}'); "
            "mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); "
            f"print(mod.sum_squares({n}))"
        )
        assert got.returncode == 0, got.stderr
        assert got.stdout == expect


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_longer_matches_cpython(tmp_path: Path) -> None:
    proc = run_cli(
        "compile",
        str(EXAMPLES / "longer.py"),
        "--run",
        "--call",
        "longer",
        "--args",
        '["bb", "a"]',
        "-o",
        str(tmp_path / "longer"),
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "bb\n"
    swapped = run_cli(
        "compile",
        str(EXAMPLES / "longer.py"),
        "--run",
        "--call",
        "longer",
        "--args",
        '["a", "bb"]',
        "-o",
        str(tmp_path / "longer2"),
    )
    assert swapped.stdout == "bb\n"


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_floor_div_matches_cpython(tmp_path: Path) -> None:
    cases = [(-3, 2, "-2"), (5, -2, "-3"), (-5, -2, "2"), (5, 2, "2")]
    for a, b, expect in cases:
        proc = run_cli(
            "compile",
            str(EXAMPLES / "floor_div.py"),
            "--run",
            "--call",
            "floor_div",
            "--args",
            f"[{a}, {b}]",
            "-o",
            str(tmp_path / f"div{a}_{b}"),
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout == f"{expect}\n"


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_divide_by_zero_traps(tmp_path: Path) -> None:
    proc = run_cli(
        "compile",
        str(EXAMPLES / "floor_div.py"),
        "--run",
        "--call",
        "floor_div",
        "--args",
        "[1, 0]",
        "-o",
        str(tmp_path / "div0"),
    )
    assert proc.returncode != 0
    assert proc.stdout == ""


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_overflow_traps_instead_of_wrapping(tmp_path: Path) -> None:
    proc = run_cli(
        "compile",
        str(EXAMPLES / "add.py"),
        "--run",
        "--call",
        "add",
        "--args",
        "[9223372036854775807, 1]",
        "-o",
        str(tmp_path / "add"),
    )
    assert proc.returncode != 0
    assert "-9223372036854775808" not in proc.stdout


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_float_add_matches_cpython(tmp_path: Path) -> None:
    proc = run_cli(
        "compile",
        str(EXAMPLES / "add_float.py"),
        "--run",
        "--call",
        "add_float",
        "--args",
        "[0.5, 0.25]",
        "-o",
        str(tmp_path / "addf"),
    )
    assert proc.returncode == 0, proc.stderr
    expect = oracle("print(0.5 + 0.25)")
    assert proc.stdout == expect


def test_oversized_int_literal_is_refused(tmp_path: Path) -> None:
    out = tmp_path / "big.ll"
    proc = run_cli("compile", str(EXAMPLES / "big_literal.py"), "--emit", "llvm", "-o", str(out))
    assert proc.returncode == 4
    assert "PS3002" in proc.stderr
    assert not out.exists()


def test_method_and_outside_table_are_fences(tmp_path: Path) -> None:
    method_ll = tmp_path / "method.ll"
    method = run_cli("compile", str(EXAMPLES / "fence_method.py"), "--emit", "llvm", "-o", str(method_ll))
    assert method.returncode == 3
    assert "PS2003" in method.stderr
    assert "encode" in method.stderr
    assert not method_ll.exists()

    outside_ll = tmp_path / "pickle.ll"
    loaded = run_cli("compile", str(EXAMPLES / "fence_pickle.py"), "--emit", "llvm", "-o", str(outside_ll))
    assert loaded.returncode == 3
    assert "PS2002" in loaded.stderr
    assert "pickle" in loaded.stderr
    assert not outside_ll.exists()


def test_bad_annotation_fails_in_checker_before_llvm(tmp_path: Path) -> None:
    out = tmp_path / "bad.ll"
    proc = run_cli("compile", str(EXAMPLES / "bad_annotation.py"), "--emit", "llvm", "-o", str(out))
    assert proc.returncode == 2
    assert "bad-return" in proc.stderr
    assert not out.exists()
    assert not Path(str(out) + ".ir.json").exists()


def test_llvm_refuses_a_different_cpython_stamp() -> None:
    from pyscriptc.errors import VersionError
    from pyscriptc.llvm import emit_llvm
    from pyscriptc.lower import lower_file

    module, _source = lower_file(str(EXAMPLES / "add.py"))
    module["cpython"] = "3.13.0"
    with pytest.raises(VersionError) as caught:
        emit_llvm(module)
    assert caught.value.code == "PS3001"


def test_scriptc_kinds_are_rejected() -> None:
    from pyscriptc.errors import IRError
    from pyscriptc.ir import validate_module
    from pyscriptc.lower import lower_file

    module, _source = lower_file(str(EXAMPLES / "add.py"))
    module["functions"][0]["returnType"] = {"kind": "f64"}
    with pytest.raises(IRError, match="f64"):
        validate_module(module)
