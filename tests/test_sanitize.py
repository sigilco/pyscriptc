"""`--sanitize`: the flag reaches every cc step, and the sanitized binary runs clean."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from pyscriptc import rtcache
from pyscriptc.errors import IRError
from pyscriptc.llvm import link_binary, sanitize_flags

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}

needs_clang = pytest.mark.skipif(shutil.which("clang") is None, reason="needs clang")


def run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pyscriptc", *args],
        cwd=ROOT,
        env=ENV if env is None else env,
        capture_output=True,
        text=True,
    )


def _hello(tmp_path: Path) -> Path:
    source = tmp_path / "hello.py"
    source.write_text(
        "def main() -> None:\n"
        "    xs: list[int] = [1, 2, 3]\n"
        "    total: int = 0\n"
        "    for x in xs:\n"
        "        total = total + x\n"
        '    print("total", total)\n'
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
        encoding="utf-8",
    )
    return source


def test_sanitize_flags_join_the_list() -> None:
    assert sanitize_flags(None) == []
    assert sanitize_flags([]) == []
    assert sanitize_flags(["address", "undefined"]) == ["-fsanitize=address,undefined"]


def test_sanitize_reaches_every_cc_step(tmp_path: Path) -> None:
    """The `.ll` unit, every runtime `.c` unit and the link line all carry the flag."""
    ll = tmp_path / "m.ll"
    ll.write_text("define i32 @main() {\n  ret i32 0\n}\n", encoding="utf-8")
    record: list[list[str]] = []
    link_binary(ll, tmp_path / "a", record=record, sanitize=["address", "undefined"])
    cmds = [cmd for cmd in record if cmd and cmd[0] != "#"]
    assert cmds, "no cc commands recorded"
    for cmd in cmds:
        assert "-fsanitize=address,undefined" in cmd, cmd
    # the link line is the last command and carries the flag too
    assert "-fsanitize=address,undefined" in cmds[-1]
    assert "-lm" in cmds[-1]


def test_sanitize_cache_key_differs_from_plain(tmp_path: Path, monkeypatch) -> None:
    """A sanitized build never reuses an unsanitized runtime object."""
    monkeypatch.setenv("PYSC_RT_CACHE_DIR", str(tmp_path / "rt"))
    runtime = Path(rtcache.__file__).parent / "runtime"
    src = runtime / "ps_argv.c"
    plain = rtcache.object_path(src, runtime, ["clang"], ["-O2"])
    san = rtcache.object_path(src, runtime, ["clang"], ["-O2", "-fsanitize=address"])
    assert plain != san


@needs_clang
def test_sanitized_binary_runs_clean(tmp_path: Path) -> None:
    """The ASan+UBSan build runs, prints the expected output and reports no error."""
    source = _hello(tmp_path)
    binary = tmp_path / "hello-san"
    proc = run_cli(
        "compile",
        str(source),
        "--emit",
        "bin",
        "-o",
        str(binary),
        "--sanitize=address,undefined",
    )
    assert proc.returncode == 0, proc.stderr
    assert binary.exists()
    ran = subprocess.run([str(binary)], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout == "total 6\n"
    assert "AddressSanitizer" not in ran.stderr
    assert "runtime error" not in ran.stderr


@needs_clang
def test_sanitize_env_default(tmp_path: Path) -> None:
    """PYSC_SANITIZE sets the same flag as the command line."""
    source = _hello(tmp_path)
    binary = tmp_path / "hello-env"
    proc = run_cli(
        "compile",
        str(source),
        "--emit",
        "bin",
        "-o",
        str(binary),
        env={**ENV, "PYSC_SANITIZE": "address"},
    )
    assert proc.returncode == 0, proc.stderr
    ran = subprocess.run([str(binary)], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout == "total 6\n"


def test_sanitize_needs_a_binary(tmp_path: Path) -> None:
    source = _hello(tmp_path)
    proc = run_cli("compile", str(source), "--emit", "llvm", "--sanitize=address")
    assert proc.returncode == 1
    assert "--sanitize needs a linked binary" in proc.stderr


def test_sanitize_rejects_an_empty_name(tmp_path: Path) -> None:
    source = _hello(tmp_path)
    proc = run_cli("compile", str(source), "--emit", "bin", "--sanitize=address,,undefined")
    assert proc.returncode == 1
    assert "--sanitize needs a comma separated list" in proc.stderr


def test_sanitize_refuses_cross_compilation() -> None:
    from pyscriptc.cli import _require_sanitize

    class Args:
        emit = "bin"
        target = "aarch64-macos"
        sanitize = "address"

    with pytest.raises(IRError, match="cannot cross-compile"):
        _require_sanitize(Args())
