"""Test native ports of real scripts against CPython on fixed inputs."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tests.test_gates import run_cli

ROOT = Path(__file__).resolve().parents[1]
CLASSIFY_DIR = ROOT / "benchmarks" / "ports" / "classify"
BINARY_SIZE_DIR = ROOT / "benchmarks" / "ports" / "binary_size"


def test_classify_port(tmp_path: Path) -> None:
    port_py = CLASSIFY_DIR / "port.py"
    orig_py = CLASSIFY_DIR / "original.py"
    binary = tmp_path / "classify_bin"

    compiled = run_cli("compile", str(port_py), "--emit", "bin", "-o", str(binary))
    assert compiled.returncode == 0, compiled.stderr

    log1 = tmp_path / "launch_graphics.log"
    log1.write_text(
        "err: loader_init c0000135\nDXVK initialized\nwinerosetta active\n",
        encoding="utf-8",
    )

    proc_native = subprocess.run(
        [str(binary), str(log1)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    proc_oracle = subprocess.run(
        [sys.executable, str(orig_py), str(log1)],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc_native.returncode == 0
    assert proc_oracle.returncode == 0
    assert proc_native.returncode == proc_oracle.returncode
    assert json.loads(proc_native.stdout) == json.loads(proc_oracle.stdout)

    log2 = tmp_path / "launch_success.log"
    log2.write_text(
        "GlueXML login screen ready\ncharacter select\nwinerosetta present\n",
        encoding="utf-8",
    )

    proc_native2 = subprocess.run(
        [str(binary), str(log2)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    proc_oracle2 = subprocess.run(
        [sys.executable, str(orig_py), str(log2)],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc_native2.returncode == 0
    assert proc_oracle2.returncode == 0
    assert proc_native2.returncode == proc_oracle2.returncode
    assert json.loads(proc_native2.stdout) == json.loads(proc_oracle2.stdout)

    proc_native_err = subprocess.run(
        [str(binary), str(tmp_path / "nonexistent.log")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    proc_oracle_err = subprocess.run(
        [sys.executable, str(orig_py), str(tmp_path / "nonexistent.log")],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc_native_err.returncode == 1
    assert proc_oracle_err.returncode == 1
    assert proc_native_err.returncode == proc_oracle_err.returncode
    native_err_json = json.loads(proc_native_err.stdout)
    oracle_err_json = json.loads(proc_oracle_err.stdout)
    assert native_err_json["error"] == oracle_err_json["error"]


def test_binary_size_port_macho(tmp_path: Path) -> None:
    port_py = BINARY_SIZE_DIR / "port.py"
    orig_py = BINARY_SIZE_DIR / "original.py"
    binary = tmp_path / "binary_size_bin"

    compiled = run_cli("compile", str(port_py), "--emit", "bin", "-o", str(binary))
    assert compiled.returncode == 0, compiled.stderr

    base_bin = tmp_path / "base.bin"
    head_bin = tmp_path / "head.bin"
    base_sec = tmp_path / "base.sec"
    head_sec = tmp_path / "head.sec"

    base_bin.write_bytes(b"A" * 2048)
    head_bin.write_bytes(b"B" * 2560)
    base_sec.write_text(
        "Segment __TEXT: 1500\nSection __text: 1500\nSegment __DATA: 548\nSection __data: 548\n",
        encoding="utf-8",
    )
    head_sec.write_text(
        "Segment __TEXT: 1800\nSection __text: 1800\nSegment __DATA: 760\nSection __data: 760\n",
        encoding="utf-8",
    )

    native_json = tmp_path / "native.json"
    native_md = tmp_path / "native.md"
    oracle_json = tmp_path / "oracle.json"
    oracle_md = tmp_path / "oracle.md"

    base_sha = "0123456789abcdef0123456789abcdef01234567"
    head_sha = "abcdef0123456789abcdef0123456789abcdef01"

    argv_native = [
        str(binary),
        "--base-binary",
        str(base_bin),
        "--head-binary",
        str(head_bin),
        "--base-sections",
        str(base_sec),
        "--head-sections",
        str(head_sec),
        "--base-sha",
        base_sha,
        "--head-sha",
        head_sha,
        "--target",
        "aarch64-macos",
        "--output-json",
        str(native_json),
        "--output-markdown",
        str(native_md),
    ]

    argv_oracle = [
        sys.executable,
        str(orig_py),
        "--base-binary",
        str(base_bin),
        "--head-binary",
        str(head_bin),
        "--base-sections",
        str(base_sec),
        "--head-sections",
        str(head_sec),
        "--base-sha",
        base_sha,
        "--head-sha",
        head_sha,
        "--target",
        "aarch64-macos",
        "--output-json",
        str(oracle_json),
        "--output-markdown",
        str(oracle_md),
    ]

    proc_native = subprocess.run(argv_native, capture_output=True, text=True, timeout=60)
    proc_oracle = subprocess.run(argv_oracle, capture_output=True, text=True, timeout=60)

    assert proc_native.returncode == 0, proc_native.stderr
    assert proc_oracle.returncode == 0, proc_oracle.stderr
    assert proc_native.returncode == proc_oracle.returncode

    assert native_json.read_text(encoding="utf-8") == oracle_json.read_text(encoding="utf-8")
    assert native_md.read_text(encoding="utf-8") == oracle_md.read_text(encoding="utf-8")


def test_binary_size_port_elf(tmp_path: Path) -> None:
    port_py = BINARY_SIZE_DIR / "port.py"
    orig_py = BINARY_SIZE_DIR / "original.py"
    binary = tmp_path / "binary_size_bin"

    compiled = run_cli("compile", str(port_py), "--emit", "bin", "-o", str(binary))
    assert compiled.returncode == 0, compiled.stderr

    base_bin = tmp_path / "base_elf.bin"
    head_bin = tmp_path / "head_elf.bin"
    base_sec = tmp_path / "base_elf.sec"
    head_sec = tmp_path / "head_elf.sec"

    base_bin.write_bytes(b"X" * 1024)
    head_bin.write_bytes(b"Y" * 1024)
    base_sec.write_text(".text 800 0x1000\n.rodata 224 0x2000\n", encoding="utf-8")
    head_sec.write_text(".text 850 0x1000\n.rodata 174 0x2000\n", encoding="utf-8")

    native_json = tmp_path / "native_elf.json"
    native_md = tmp_path / "native_elf.md"
    oracle_json = tmp_path / "oracle_elf.json"
    oracle_md = tmp_path / "oracle_elf.md"

    base_sha = "1111111111111111111111111111111111111111"
    head_sha = "2222222222222222222222222222222222222222"

    argv_native = [
        str(binary),
        "--base-binary",
        str(base_bin),
        "--head-binary",
        str(head_bin),
        "--base-sections",
        str(base_sec),
        "--head-sections",
        str(head_sec),
        "--base-sha",
        base_sha,
        "--head-sha",
        head_sha,
        "--target",
        "x86_64-linux",
        "--output-json",
        str(native_json),
        "--output-markdown",
        str(native_md),
    ]

    argv_oracle = [
        sys.executable,
        str(orig_py),
        "--base-binary",
        str(base_bin),
        "--head-binary",
        str(head_bin),
        "--base-sections",
        str(base_sec),
        "--head-sections",
        str(head_sec),
        "--base-sha",
        base_sha,
        "--head-sha",
        head_sha,
        "--target",
        "x86_64-linux",
        "--output-json",
        str(oracle_json),
        "--output-markdown",
        str(oracle_md),
    ]

    proc_native = subprocess.run(argv_native, capture_output=True, text=True, timeout=60)
    proc_oracle = subprocess.run(argv_oracle, capture_output=True, text=True, timeout=60)

    assert proc_native.returncode == 0, proc_native.stderr
    assert proc_oracle.returncode == 0, proc_oracle.stderr
    assert proc_native.returncode == proc_oracle.returncode

    assert native_json.read_text(encoding="utf-8") == oracle_json.read_text(encoding="utf-8")
    assert native_md.read_text(encoding="utf-8") == oracle_md.read_text(encoding="utf-8")
