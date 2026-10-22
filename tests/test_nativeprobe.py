"""A C ABI .so and a PyInit extension agree with CPython, and link differently."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from benchmarks.native.build import ensure

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "benchmarks" / "native" / "workloads"


def _env(lib: Path) -> dict[str, str]:
    native = str(lib.parent)
    previous = os.environ.get("PYTHONPATH", "")
    pythonpath = native if not previous else native + os.pathsep + previous
    return {
        **os.environ,
        "PYSCRIPTC_NATIVEPROBE": "1",
        "PYSCRIPTC_MIX_SO": str(lib),
        "PYTHONPATH": pythonpath,
    }


def _compile(env: dict[str, str], source: Path, call: str, args: str, binary: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(source),
            "--skip-check",
            "--run",
            "--call",
            call,
            "--args",
            args,
            "-o",
            str(binary),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _oracle(env: dict[str, str], source: Path, call: str, args: str) -> str:
    script = (
        "import ast, importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('w', r'{source}')\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n"
        f"value = getattr(mod, '{call}')(*ast.literal_eval({args!r}))\n"
        "print(value)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_nativeprobe_stays_fenced_without_the_probe_flag(tmp_path: Path) -> None:
    source = tmp_path / "use.py"
    source.write_text("import nativeprobe\n\ndef main() -> int:\n    return 0\n", encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if key != "PYSCRIPTC_NATIVEPROBE"}
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(source),
            "--skip-check",
            "--emit",
            "llvm",
            "-o",
            str(tmp_path / "use.ll"),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 3, proc.stderr
    assert "PS2002" in proc.stderr
    assert "nativeprobe" in proc.stderr


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_extension_exports_pyinit_not_the_c_abi() -> None:
    lib, ext = ensure()
    mix = subprocess.run(["nm", "-D", str(lib)], capture_output=True, text=True, check=True)
    probe = subprocess.run(["nm", "-D", str(ext)], capture_output=True, text=True, check=True)
    assert " T mix_crc32\n" in mix.stdout or " T mix_crc32" in mix.stdout
    assert "PyInit_nativeprobe" in probe.stdout
    assert " U mix_crc32\n" in probe.stdout or " U mix_crc32" in probe.stdout


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_direct_and_island_match_and_link_differently(tmp_path: Path) -> None:
    lib, _ext = ensure()
    env = _env(lib)
    cases = [
        ("py_direct.py", "python_heavy", "[20]", False),
        ("py_island.py", "python_heavy", "[20]", True),
        ("call_direct.py", "call_heavy", "[5]", False),
        ("call_island.py", "call_heavy", "[5]", True),
        ("so_direct.py", "so_heavy", "[30]", False),
        ("so_island.py", "so_heavy", "[30]", True),
    ]
    for name, call, args, island in cases:
        source = WORK / name
        binary = tmp_path / name
        got = _compile(env, source, call, args, binary)
        assert got.returncode == 0, f"{name}\n{got.stderr}"
        assert got.stdout == _oracle(env, source, call, args)
        linked = subprocess.run(["ldd", str(binary)], capture_output=True, text=True, check=True)
        if island:
            assert "libpython" in linked.stdout
        else:
            assert "libpython" not in linked.stdout
