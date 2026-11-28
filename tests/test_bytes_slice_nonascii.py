"""Issue #1416: a bytes slice and index count bytes, not code points.

`b"caf\\xc3\\xa9 xyz"[2:4]` is `b"f\\xc3"` (2 bytes) in CPython; the
lowering used to send every buffer slice through `ps_str_slice`, whose
non-ASCII path re-maps the bounds through the code-point scan and split
the sequence. Bytes now dispatch to `ps_buf_slice`, the byte-bounds
form. The differential probe is `probes/differential/bytes_slice_nonascii.py`;
this file adds the compile-level seams the probe does not see: the IR
symbol, the lowered LLVM and the value checks over the non-ASCII, NUL
and high-byte payloads.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from tests.test_gates import ENV, ROOT, compile_run, oracle, write_case


def _compile_ir(source: str) -> dict:
    """Compile the source to typed IR and read the JSON record back."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "probe.py"
        path.write_text(source, encoding="utf-8")
        out = Path(tmp) / "probe.ir.json"
        proc = subprocess.run(
            [sys.executable, "-m", "pyscriptc", "compile", str(path), "--emit", "ir", "-o", str(out)],
            cwd=ROOT,
            env=ENV,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        return json.loads(out.read_text())


def _collect_call_symbols(module: dict) -> list[str]:
    """Every `symbol` payload a call record in the module spells."""
    symbols: list[str] = []

    def walk(node: dict | list) -> None:
        if isinstance(node, dict):
            if node.get("kind") == "call" and "symbol" in node:
                symbols.append(node["symbol"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(module)
    return symbols


def _compile_text(source: str, emit: str, suffix: str) -> str:
    """Compile the source to one of the text artifacts and read it back."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "probe.py"
        path.write_text(source, encoding="utf-8")
        out = Path(tmp) / f"probe{suffix}"
        proc = subprocess.run(
            [sys.executable, "-m", "pyscriptc", "compile", str(path), "--emit", emit, "-o", str(out)],
            cwd=ROOT,
            env=ENV,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        return out.read_text()


_PROBE = """
def parts() -> int:
    data: bytes = b"caf\\xc3\\xa9 xyz"
    piece: bytes = data[2:4]
    return len(piece) * 100 + piece[0] + piece[1]

def stepped() -> int:
    data: bytes = b"caf\\xc3\\xa9 xyz"
    return len(data[2:8:2])

def backwards() -> int:
    data: bytes = b"caf\\xc3\\xa9 xyz"
    return data[::-1][0]

def from_end() -> int:
    data: bytes = b"caf\\xc3\\xa9 xyz"
    return data[-2]

def nul_index() -> int:
    nul: bytes = b"a\\x00b\\xff"
    return nul[1] + nul[3]

def nul_slice() -> int:
    nul: bytes = b"a\\x00b\\xff"
    return len(nul[0:3]) * 100 + len(nul[1:])

def high_only() -> int:
    high: bytes = b"\\x80\\x81\\xff\\xfe"
    return len(high[1:3]) * 100 + high[1]

def empty_bounds() -> int:
    data: bytes = b"caf\\xc3\\xa9 xyz"
    return len(data[8:2]) + len(data[100:200]) + len(data[5:5])

def zero_step_raises() -> int:
    data: bytes = b"caf\\xc3\\xa9 xyz"
    try:
        piece: bytes = data[::0]
        return len(piece)
    except ValueError:
        return 7
"""


def test_bytes_slice_ir_dispatches_to_ps_buf_slice() -> None:
    # The IR record spells the symbol: the bytes kind must never lower to
    # the code-point scanner again.
    source = (
        'def probe() -> int:\n    data: bytes = b"caf\\xc3\\xa9"\n    piece: bytes = data[1:3]\n    return len(piece)\n'
    )
    module = _compile_ir(source)
    symbols = _collect_call_symbols(module)
    assert "ps_buf_slice" in symbols
    assert "ps_str_slice" not in symbols


def test_bytes_slice_llvm_names_ps_buf_slice() -> None:
    # The emitted LLVM declares and calls the byte-bounds helper, so the
    # serializer sees a distinct symbol and the runtime never re-maps the
    # bounds.
    text = _compile_text(
        "def probe() -> int:\n"
        '    data: bytes = b"caf\\xc3\\xa9"\n'
        "    piece: bytes = data[1:3]\n"
        "    return len(piece)\n",
        "llvm",
        ".ll",
    )
    assert "declare void @ps_buf_slice" in text
    assert "call void @ps_buf_slice" in text
    assert "ps_str_slice" not in text


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_bytes_slice_nonascii_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "bytes_slice.py", _PROBE)
    expected = {
        # data[2:4] is b"f\xc3": len 2, bytes 102 + 195.
        "parts": 2 * 100 + 102 + 195,
        # data[2:8:2] picks bytes 2, 4, 6: b"f\xa9 ", 3 bytes.
        "stepped": 3,
        # data[::-1][0] is the last byte 'z'.
        "backwards": ord("z"),
        # data[-2] is 'y'.
        "from_end": ord("y"),
        "nul_index": 0 + 255,
        "nul_slice": 3 * 100 + 3,
        "high_only": 2 * 100 + 0x81,
        "empty_bounds": 0,
        "zero_step_raises": 7,
    }
    for func, want in expected.items():
        got = compile_run(tmp_path, path, func)
        assert got.returncode == 0, f"{func}\n{got.stderr}"
        assert got.stdout.strip() == str(want), f"{func}: {got.stdout!r} != {want}"
        assert got.stdout == oracle(path, func), func


def test_bytes_slice_mir_lane_stays_direct() -> None:
    # The MIR leaf slice never admits a bytes subscript, so the MIR text
    # for a bytes slice says "skipped (direct path)" and the ABI shape
    # never reaches the leaf emitter's table.
    text = _compile_text(
        "def probe() -> int:\n"
        '    data: bytes = b"caf\\xc3\\xa9"\n'
        "    piece: bytes = data[1:3]\n"
        "    return len(piece)\n",
        "mir",
        ".mir",
    )
    assert "skipped (direct path): probe" in text


def test_str_slice_ir_keeps_ps_str_slice() -> None:
    # The str path is unchanged: a str slice still names ps_str_slice.
    module = _compile_ir(
        'def probe() -> int:\n    text: str = "caf\\u00e9"\n    piece: str = text[1:3]\n    return len(piece)\n'
    )
    symbols = _collect_call_symbols(module)
    assert "ps_str_slice" in symbols
    assert "ps_buf_slice" not in symbols
