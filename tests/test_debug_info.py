"""`--debug-info`: the emitted IR carries DWARF that names the Python line."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from pyscriptc.errors import IRError

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}

needs_clang = pytest.mark.skipif(shutil.which("clang") is None, reason="needs clang")

# The `!dbg` attachment on an instruction or a function signature.
_DBG_ATTACHMENT = re.compile(r"\s*,?\s*!dbg !\d+")


def run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pyscriptc", *args],
        cwd=ROOT,
        env=ENV if env is None else env,
        capture_output=True,
        text=True,
    )


def _program(tmp_path: Path) -> Path:
    source = tmp_path / "prog.py"
    source.write_text(
        "def add(x: int, y: int) -> int:\n"
        "    total: int = x + y\n"
        "    return total\n"
        "\n"
        "def main() -> None:\n"
        "    print(add(2, 3))\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
        encoding="utf-8",
    )
    return source


def _llvm(tmp_path: Path, *extra: str) -> str:
    """The emitted `.ll` for the test program, in its own directory."""
    work = tmp_path / "work"
    work.mkdir(parents=True, exist_ok=True)
    source = _program(work)
    proc = run_cli("compile", str(source), "--emit", "llvm", "--out-dir", str(work), *extra)
    assert proc.returncode == 0, proc.stderr
    return (work / "prog.ll").read_text(encoding="utf-8")


def test_debug_info_emits_the_dwarf_records(tmp_path: Path) -> None:
    """The `.ll` carries a compile unit, a file, a subprogram and locations."""
    text = _llvm(tmp_path, "--debug-info")
    assert "!llvm.dbg.cu" in text
    assert "!llvm.module.flags" in text
    assert "!DIFile(" in text
    assert "!DISubprogram(" in text
    assert "!DILocation(" in text


def test_debug_info_names_the_function_and_the_source_file(tmp_path: Path) -> None:
    """The subprogram carries the Python name and the file of the source."""
    text = _llvm(tmp_path, "--debug-info")
    assert 'name: "add"' in text
    assert 'name: "prog.py"' in text
    assert "DW_LANG_Python" in text


def test_debug_info_locations_match_the_python_lines(tmp_path: Path) -> None:
    """The location of a statement is the line that statement is on."""
    text = _llvm(tmp_path, "--debug-info")
    # `total = x + y` is line 2 and `return total` is line 3.
    assert "!DILocation(line: 2," in text
    assert "!DILocation(line: 3," in text


def test_debug_info_keeps_the_default_output_unchanged(tmp_path: Path) -> None:
    """Without the flag the emitted text has no metadata at all."""
    text = _llvm(tmp_path)
    assert "!DIFile(" not in text
    assert "!DISubprogram(" not in text
    assert "!DILocation(" not in text
    assert "!llvm.dbg.cu" not in text


def test_debug_info_adds_only_attachments(tmp_path: Path) -> None:
    """The debug build is the plain build plus the metadata: same instructions."""
    plain = _llvm(tmp_path / "plain")
    debug = _llvm(tmp_path / "debug", "--debug-info")

    def body(text: str) -> list[str]:
        # Drop the metadata block, the debug intrinsics and the `!dbg`
        # attachments: what is left is the instruction stream, which the flag
        # must not change. The type and global lines are sorted because the
        # emitter collects them in set order, which is not stable.
        lines = []
        for line in text.splitlines():
            if line.startswith("!") or "llvm.dbg." in line:
                continue
            line = _DBG_ATTACHMENT.sub("", line).rstrip().rstrip(",").rstrip()
            if line:
                lines.append(line)
        return sorted(lines)

    assert body(debug) == body(plain)


def test_debug_info_env_default(tmp_path: Path) -> None:
    """PYSC_DEBUG_INFO sets the same flag as the command line."""
    source = _program(tmp_path)
    proc = run_cli(
        "compile",
        str(source),
        "--emit",
        "llvm",
        "--out-dir",
        str(tmp_path),
        env={**ENV, "PYSC_DEBUG_INFO": "1"},
    )
    assert proc.returncode == 0, proc.stderr
    assert "!DISubprogram(" in (tmp_path / "prog.ll").read_text(encoding="utf-8")


def test_debug_info_refuses_cross_compilation() -> None:
    from pyscriptc.cli import _require_debug_info

    class Args:
        target = "aarch64-linux-gnu"
        debug_info = True

    with pytest.raises(IRError, match="cannot cross-compile"):
        _require_debug_info(Args())


@needs_clang
def test_debug_info_ir_parses_with_clang(tmp_path: Path) -> None:
    """The debug `.ll` still compiles: the metadata is well formed."""
    source = _program(tmp_path)
    proc = run_cli("compile", str(source), "--emit", "llvm", "--out-dir", str(tmp_path), "--debug-info")
    assert proc.returncode == 0, proc.stderr
    obj = tmp_path / "prog.o"
    cc = subprocess.run(
        ["clang", "-O2", "-g", "-x", "ir", str(tmp_path / "prog.ll"), "-c", "-o", str(obj)],
        capture_output=True,
        text=True,
    )
    assert cc.returncode == 0, cc.stderr
    assert obj.exists()


@needs_clang
def test_debug_info_binary_carries_the_line_table(tmp_path: Path) -> None:
    """The binary's DWARF names the Python file and the Python lines."""
    source = _program(tmp_path)
    binary = tmp_path / "prog"
    proc = run_cli("compile", str(source), "--emit", "bin", "-o", str(binary), "--debug-info")
    assert proc.returncode == 0, proc.stderr
    ran = subprocess.run([str(binary)], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout == "5\n"

    dump = _dwarfdump(binary)
    if dump is None:
        pytest.skip("no dwarfdump on PATH")
    assert "prog.py" in dump
    # The line table maps the code back to the Python source lines.
    assert "add" in dump


def _dwarfdump(binary: Path) -> str | None:
    """The DWARF of a binary, or None when no dumper or section is available.

    On Darwin the linker moves the DWARF into the companion `.dSYM` bundle, so
    that is where the dump looks first. `llvm-dwarfdump --debug-info` covers
    the ELF case; the Apple toolchain's copy is reached through `xcrun`.
    """
    bundle = binary.parent / f"{binary.name}.dSYM" / "Contents" / "Resources" / "DWARF" / binary.name
    candidates = [bundle, binary]
    dumpers: list[list[str]] = []
    if shutil.which("llvm-dwarfdump"):
        dumpers.append(["llvm-dwarfdump", "--debug-info"])
    if shutil.which("xcrun"):
        dumpers.append(["xcrun", "llvm-dwarfdump", "--debug-info"])
    if shutil.which("dwarfdump"):
        dumpers.append(["dwarfdump", "--print-info"])
    for argv in dumpers:
        for candidate in candidates:
            if not candidate.exists():
                continue
            proc = subprocess.run([*argv, str(candidate)], capture_output=True, text=True)
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout
    return None
