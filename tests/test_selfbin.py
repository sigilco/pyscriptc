"""The self-compiled pyscriptc: a single binary that compiles programs.

The binary under test is built from this checkout with
`pyscriptc compile src/pyscriptc --program --skip-check --island --emit
bin --embed-python bundle --embed-headers`, so it carries its own CPython
runtime, the C runtime sources and the package data: the host needs no
Python, no uv and no pip to run it. What it still needs is a C toolchain
(clang or zig) on PATH, because a program compile shells out to it.

The build takes about half a minute and the test compiles a hello world
through the binary, so the module is marked `slow` and CI can skip it. The
runtime pack is fetched from the network when it is not cached, so the test
skips when the pack is missing and no network is available.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from pyscriptc import embed

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.slow

HELLO = 'def main() -> None:\n    print("hello from a native binary")\n\n\nif __name__ == "__main__":\n    main()\n'


def _pack_ready() -> bool:
    """True when the runtime pack is unpacked, so the build needs no network."""
    try:
        pack = embed.lock_pack()
    except embed.EmbedError:
        return False
    return embed.pack_is_ready(pack)


def _build_selfbinary(out: Path) -> Path:
    """Build the self-compiled pyscriptc; returns the binary path."""
    from pyscriptc.cli import main as cli_main

    argv = [
        "compile",
        str(ROOT / "src" / "pyscriptc"),
        "--program",
        "--skip-check",
        "--island",
        "--emit",
        "bin",
        "--embed-python",
        "bundle",
        "--embed-headers",
        "--bundle-exclude",
        "mypy",
        "-o",
        str(out),
    ]
    saved = sys.argv
    sys.argv = ["pyscriptc", *argv]
    try:
        code = cli_main(argv)
    finally:
        sys.argv = saved
    assert code == 0, f"the self-compile failed with {code}"
    return out / "bin" / out.name


def _build_selfbinary_onefile(out: Path) -> Path:
    """Build the self-compiled pyscriptc as one executable; returns its path."""
    from pyscriptc.cli import main as cli_main

    argv = [
        "compile",
        str(ROOT / "src" / "pyscriptc"),
        "--program",
        "--skip-check",
        "--island",
        "--emit",
        "bin",
        "--embed-python",
        "bundle",
        "--embed-headers",
        "--embed-stdlib",
        "minimal",
        "--bundle-exclude",
        "mypy",
        "--onefile",
        "--strip-runtime",
        "-o",
        str(out),
    ]
    saved = sys.argv
    sys.argv = ["pyscriptc", *argv]
    try:
        code = cli_main(argv)
    finally:
        sys.argv = saved
    assert code == 0, f"the self-compile failed with {code}"
    return out.parent / f"{out.name}.onefile"


@pytest.fixture(scope="module")
def selfbinary(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The self-compiled pyscriptc, built once per module."""
    if sys.platform != "linux" and sys.platform != "darwin":
        pytest.skip("the self-compile runs on Linux and macOS")
    if not _pack_ready():
        pytest.skip("the runtime pack is not fetched; run `pyscriptc embed fetch` first")
    out = tmp_path_factory.mktemp("selfbin") / "pyscriptc"
    return _build_selfbinary(out)


@pytest.fixture(scope="module")
def selfbinary_onefile(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The self-compiled pyscriptc as one executable file, built once per module."""
    if sys.platform != "linux" and sys.platform != "darwin":
        pytest.skip("the self-compile runs on Linux and macOS")
    if not _pack_ready():
        pytest.skip("the runtime pack is not fetched; run `pyscriptc embed fetch` first")
    out = tmp_path_factory.mktemp("selfbin-onefile") / "pyscriptc"
    return _build_selfbinary_onefile(out)


def test_selfbinary_runs_without_python_on_path(selfbinary: Path, tmp_path: Path) -> None:
    """The binary prints its usage with no PATH at all: no Python needed."""
    proc = subprocess.run(
        [str(selfbinary)],
        capture_output=True,
        text=True,
        env={"PATH": "/nonexistent"},
        cwd=tmp_path,
    )
    assert proc.returncode != 0
    assert "usage: pyscriptc" in proc.stdout + proc.stderr


def test_selfbinary_compiles_hello_world(selfbinary: Path, tmp_path: Path) -> None:
    """The binary compiles a hello world and the binary prints hello."""
    source = tmp_path / "hello.py"
    source.write_text(HELLO, encoding="utf-8")
    out = tmp_path / "hello"
    toolchain = shutil.which("clang") or shutil.which("zig")
    if toolchain is None:
        pytest.skip("no C toolchain on PATH")
    env = dict(os.environ)
    env["PATH"] = str(Path(toolchain).parent)
    proc = subprocess.run(
        [str(selfbinary), "compile", str(source), "--skip-check", "--island", "--emit", "bin", "-o", str(out)],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    run = subprocess.run([str(out)], capture_output=True, text=True, cwd=tmp_path)
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.strip() == "hello from a native binary"


def test_selfbinary_assesses_a_program(selfbinary: Path, tmp_path: Path) -> None:
    """The `assess` command of the binary answers without a Python install."""
    source = tmp_path / "checked.py"
    source.write_text(HELLO, encoding="utf-8")
    proc = subprocess.run(
        [str(selfbinary), "assess", str(source)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "checked.py" in proc.stdout


def test_selfbinary_onefile_is_one_file(selfbinary_onefile: Path, tmp_path: Path) -> None:
    """Copied on its own into an empty directory, the onefile still runs."""
    assert selfbinary_onefile.is_file()
    alone = tmp_path / "pyscriptc"
    shutil.copy2(selfbinary_onefile, alone)
    proc = subprocess.run(
        [str(alone)],
        capture_output=True,
        text=True,
        env={"PATH": "/nonexistent", "HOME": str(tmp_path)},
        cwd=tmp_path,
    )
    assert proc.returncode != 0
    assert "usage: pyscriptc" in proc.stdout + proc.stderr


def test_selfbinary_onefile_compiles_hello_world(selfbinary_onefile: Path, tmp_path: Path) -> None:
    """The onefile binary compiles a hello world and the binary prints hello."""
    source = tmp_path / "hello.py"
    source.write_text(HELLO, encoding="utf-8")
    out = tmp_path / "hello"
    toolchain = shutil.which("clang") or shutil.which("zig")
    if toolchain is None:
        pytest.skip("no C toolchain on PATH")
    env = dict(os.environ)
    env["PATH"] = str(Path(toolchain).parent)
    env.pop("PYSC_ONEFILE_CACHE", None)
    proc = subprocess.run(
        [str(selfbinary_onefile), "compile", str(source), "--skip-check", "--island", "--emit", "bin", "-o", str(out)],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    run = subprocess.run([str(out)], capture_output=True, text=True, cwd=tmp_path)
    assert run.returncode == 0, run.stdout + proc.stderr
    assert run.stdout.strip() == "hello from a native binary"
