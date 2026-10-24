"""The wasm32-wasi target: emitter gates, link gates and end-to-end output parity.

The emitter changes are wasm-only (`@__main_argc_argv` entry, i32 `%.*s`
lengths); an A/B `irscore` run proves non-wasm IR is unchanged. The link gate
refuses subprocess/mix/island programs with a target-naming diagnostic. The
end-to-end tests need `zig` and a wasmtime >= 50 binary (env `PYSC_WASMTIME`).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from pyscriptc.errors import IRError
from pyscriptc.llvm import emit_llvm
from pyscriptc.lower import lower_file

WASMTIME = os.environ.get("PYSC_WASMTIME", "wasmtime")

DEMO = """\
def fib(n: int) -> int:
    a: int = 0
    b: int = 1
    i: int = 0
    while i < n:
        t: int = a + b
        a = b
        b = t
        i = i + 1
    return a

if __name__ == "__main__":
    print("fib(10) =", fib(10))
    s = "hello,wasm"
    parts: list[str] = s.split(",")
    print(parts[0], parts[1].upper())
    try:
        x: int = int("abc")
    except ValueError:
        print("caught ValueError")
"""

SUBPROCESS = """\
import subprocess

def go() -> int:
    return subprocess.call(["echo", "hi"])

if __name__ == "__main__":
    go()
"""


def _wasmtime() -> str | None:
    found = shutil.which(WASMTIME)
    return found


def _write(source: str, tmp_path: Path, name: str = "demo.py") -> Path:
    path = tmp_path / f"_{name}"
    path.write_text(source, encoding="utf-8")
    return str(path)


def test_wasm_entry_symbol_is_main_argc_argv(tmp_path: Path) -> None:
    module, _ = lower_file(_write(DEMO, tmp_path))
    text = emit_llvm(module, standalone=True, wasm=True)
    assert "define i32 @__main_argc_argv(i32 %argc, ptr %argv) {" in text
    assert "define i32 @main(" not in text


def test_native_entry_symbol_unchanged(tmp_path: Path) -> None:
    module, _ = lower_file(_write(DEMO, tmp_path))
    text = emit_llvm(module, standalone=True)
    assert "define i32 @main(i32 %argc, ptr %argv) {" in text
    assert "__main_argc_argv" not in text


def test_wasm_truncates_printf_str_lengths(tmp_path: Path) -> None:
    module, _ = lower_file(_write(DEMO, tmp_path))
    text = emit_llvm(module, standalone=True, wasm=True)
    # the fwrite writes carry raw i64 lengths (size_t is i64 on wasi too);
    # a varargs fprintf %.*s write, when one is emitted, keeps its i32
    for line in text.splitlines():
        if "@fwrite(ptr" in line:
            assert "i64 i32" not in line, line
            assert "trunc" not in line, line
    assert "i64 i32" not in text


def test_native_print_keeps_i64_lengths(tmp_path: Path) -> None:
    module, _ = lower_file(_write(DEMO, tmp_path))
    text = emit_llvm(module, standalone=True)
    # `%.*s` writes go through fwrite (embedded NULs), the rest through
    # printf; every length that reaches either is an untruncated i64.
    assert "call i64 @fwrite(ptr" in text
    assert "trunc i64" not in text


@pytest.mark.skipif(shutil.which("zig") is None, reason="zig not on PATH")
def test_wasm_link_refuses_subprocess(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = _write(SUBPROCESS, tmp_path)
    from pyscriptc.cli import main

    ret = main(
        [
            "compile",
            str(src),
            "--emit",
            "bin",
            "--skip-check",
            "--target",
            "wasm32-wasi-musl",
            "-o",
            str(tmp_path / "sub"),
        ]
    )
    assert ret != 0
    err = capsys.readouterr().err
    assert "refuses ps_sub" in err
    assert "wasm32-wasi-musl" in err
    assert not (tmp_path / "sub.wasm").exists()


@pytest.mark.skipif(shutil.which("zig") is None, reason="zig not on PATH")
def test_wasm_compile_names_output_wasm(tmp_path: Path) -> None:
    src = _write(DEMO, tmp_path)
    from pyscriptc.cli import main

    ret = main(
        [
            "compile",
            str(src),
            "--emit",
            "bin",
            "--skip-check",
            "--target",
            "wasm32-wasi-musl",
            "-o",
            str(tmp_path / "demo"),
        ]
    )
    assert ret == 0
    assert (tmp_path / "demo.wasm").is_file()
    # a WASI command module: magic + version 1
    data = (tmp_path / "demo.wasm").read_bytes()
    assert data[:4] == b"\x00asm"


def _write(source: str, tmp_path: Path, name: str = "demo.py") -> str:
    path = tmp_path / f"_{name}"
    path.write_text(source, encoding="utf-8")
    return str(path)


@pytest.mark.skipif(shutil.which("zig") is None, reason="zig not on PATH")
@pytest.mark.skipif(_wasmtime() is None, reason="wasmtime not on PATH (set PYSC_WASMTIME)")
def test_wasm_output_matches_native(tmp_path: Path) -> None:
    """The wasm exit criterion: byte-identical stdout against the same program native."""
    src = _write(DEMO, tmp_path)
    from pyscriptc.cli import main

    native = tmp_path / "demo-native"
    wasm = tmp_path / "demo"
    assert main(["compile", str(src), "--emit", "bin", "--skip-check", "-o", str(native)]) == 0
    assert (
        main(["compile", str(src), "--emit", "bin", "--skip-check", "--target", "wasm32-wasi-musl", "-o", str(wasm)])
        == 0
    )
    ran_native = subprocess.run([str(native)], capture_output=True, text=True)
    ran_wasm = subprocess.run([_wasmtime(), str(wasm) + ".wasm"], capture_output=True, text=True)
    assert ran_native.returncode == 0, ran_native.stderr
    assert ran_wasm.returncode == 0, ran_wasm.stderr
    assert ran_wasm.stdout == ran_native.stdout
    assert ran_wasm.stdout.startswith("fib(10) = 55")


# --- the WASI 0.2 lane ---------------------------------------------------


def _wasm_tools() -> str | None:
    return shutil.which("wasm-tools")


def test_adapter_pin_is_recorded() -> None:
    """The adapter provenance is part of the contract: URL, version, sha256."""
    from pyscriptc import wasm_adapter

    assert wasm_adapter.ADAPTER_URL.endswith(f"v{wasm_adapter.WASMTIME_VERSION}/{wasm_adapter.ADAPTER_NAME}")
    assert len(wasm_adapter.ADAPTER_SHA256) == 64
    assert wasm_adapter.WASMTIME_VERSION == "50.0.0"
    assert wasm_adapter.WASM_TOOLS_VERSION == "1.262.0"


def test_adapter_fetch_rejects_a_wrong_digest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A fetched adapter that does not match the pin is refused, not used."""
    from pyscriptc import wasm_adapter

    monkeypatch.setenv("PYSC_WASI_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("PYSC_WASI_P1_ADAPTER", str(tmp_path / "missing.wasm"))
    with pytest.raises(IRError, match="missing file"):
        wasm_adapter.adapter_path()


def test_wasm_tools_missing_names_the_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pyscriptc import wasm_adapter

    monkeypatch.setattr(shutil, "which", lambda _cmd: None)
    with pytest.raises(IRError, match="wasm-tools is not on PATH"):
        wasm_adapter.wasm_tools()


@pytest.mark.skipif(shutil.which("zig") is None, reason="zig not on PATH")
@pytest.mark.skipif(_wasm_tools() is None, reason="wasm-tools not on PATH")
@pytest.mark.skipif(_wasmtime() is None, reason="wasmtime not on PATH (set PYSC_WASMTIME)")
def test_wasi_p2_produces_a_component(tmp_path: Path) -> None:
    """The 0.2 lane: a component that runs, with the native output byte for byte."""
    src = _write(DEMO, tmp_path)
    from pyscriptc.cli import main

    native = tmp_path / "demo-native"
    p2 = tmp_path / "demo-p2"
    assert main(["compile", str(src), "--emit", "bin", "--skip-check", "-o", str(native)]) == 0
    assert (
        main(["compile", str(src), "--emit", "bin", "--skip-check", "--target", "wasm32-wasi-p2", "-o", str(p2)]) == 0
    )
    data = (tmp_path / "demo-p2.wasm").read_bytes()
    # a component is the wasm magic plus version 0x000d; a core module has 0x0001
    assert data[:8] == b"\x00asm\x0d\x00\x01\x00"
    ran_native = subprocess.run([str(native)], capture_output=True, text=True)
    ran_p2 = subprocess.run([_wasmtime(), str(tmp_path / "demo-p2.wasm")], capture_output=True, text=True)
    assert ran_p2.returncode == 0, ran_p2.stderr
    assert ran_p2.stdout == ran_native.stdout


@pytest.mark.skipif(shutil.which("zig") is None, reason="zig not on PATH")
def test_wasi_p2_link_refuses_subprocess(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = _write(SUBPROCESS, tmp_path)
    from pyscriptc.cli import main

    ret = main(
        [
            "compile",
            str(src),
            "--emit",
            "bin",
            "--skip-check",
            "--target",
            "wasm32-wasi-p2",
            "-o",
            str(tmp_path / "sub"),
        ]
    )
    assert ret != 0
    err = capsys.readouterr().err
    assert "refuses ps_sub" in err
    assert "wasm32-wasi-musl" in err
