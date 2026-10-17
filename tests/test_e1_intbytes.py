"""E1 item 3: `int(bytes)` parses at runtime.

The builtin table gains one entry: `int` over a single `bytes` value lowers
to `ps_int_from_bytes`, a pure parser in ps_help.c (base 10, CPython's
surrounding-whitespace strip, optional sign, all remaining bytes digits,
i64 range). The other `int()` overloads keep their fence: `str` (with or
without a base argument) and the no-arg form stay PS2003. `int(float)` is
pre-existing surface (ps_float_to_int) and is not touched here.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.test_gates import run_cli, write_case
from tests.test_spike import ROOT

from pyscriptc.lower import lower_source


def _entry(mod: dict) -> dict | None:
    return next((fn for fn in mod["functions"] if fn["name"] == mod["entry"]), None)


def test_int_bytes_lower_without_fences() -> None:
    mod = lower_source(
        """
def probe(p: bytes) -> int:
    return int(p) + int(b"-5") + int(b"  42  ")
"""
    )
    assert mod["fences"] == []
    probe = next(fn for fn in mod["functions"] if fn["name"] == "probe")
    ret = probe["body"][0]
    assert ret["kind"] == "return"
    calls: list[dict] = []

    def walk(expr: dict) -> None:
        if expr.get("symbol") == "ps_int_from_bytes":
            calls.append(expr)
            return
        for key in ("left", "right", "value"):
            if isinstance(expr.get(key), dict):
                walk(expr[key])

    walk(ret["value"])
    assert len(calls) == 3
    for call in calls:
        assert call["callee"] == {"kind": "module", "module": "builtins", "name": "int"}
        assert call["type"] == {"kind": "int"}
        assert call["args"][0]["type"] == {"kind": "bytes"}


def test_int_bytes_value_and_entry_matches_cpython(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "parse.py",
        "\n".join(
            [
                "def probe(p: bytes) -> int:",
                "    return int(p)",
                "",
                "",
                "print(int(b'123'))",
                "print(probe(b'-5'))",
                "print(probe(b'  42  '))",
                "print(int(b'007'))",
                "",
            ]
        ),
    )
    got = run_cli("compile", str(path), "--run", "--emit", "bin", "--call", "main", "-o", str(tmp_path / "parse"))
    assert got.returncode == 0, got.stderr
    expect = "123\n-5\n42\n7\n"
    assert got.stdout == expect == _oracle(path)


def _oracle(path: Path) -> str:
    proc = subprocess.run([sys.executable, str(path)], cwd=ROOT, capture_output=True, text=True, check=True)
    return proc.stdout


def test_int_bytes_whitespace_and_sign_matches_cpython(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "ws.py",
        "\n".join(
            [
                "def probe(p: bytes) -> int:",
                "    return int(p)",
                "",
                "",
                "print(probe(b'\\t\\n 5 \\r\\n'))",
                "print(probe(b'+7'))",
                "print(probe(b'9223372036854775807'))",
                "print(probe(b'-9223372036854775808'))",
                "",
            ]
        ),
    )
    got = run_cli("compile", str(path), "--run", "--emit", "bin", "--call", "main", "-o", str(tmp_path / "ws"))
    assert got.returncode == 0, got.stderr
    assert got.stdout == _oracle(path)


def test_int_bytes_invalid_literal_raises_value_error(tmp_path: Path) -> None:
    for literal in ("b'1x'", "b''", "b'  '", "b'- 1'", "b'1_0'"):
        path = write_case(tmp_path, "bad.py", f"print(int({literal}))\n")
        got = run_cli("compile", str(path), "--run", "--emit", "bin", "--call", "main", "-o", str(tmp_path / "bad"))
        assert got.returncode != 0, literal
        assert "ValueError" in got.stderr, literal


def test_int_bytes_out_of_i64_range_stays_a_runtime_error(tmp_path: Path) -> None:
    # CPython has bignums, so the value itself never errors there; the i64
    # subset fences at runtime with ValueError instead, like json integers.
    path = write_case(tmp_path, "big.py", "print(int(b'9223372036854775808'))\n")
    got = run_cli("compile", str(path), "--run", "--emit", "bin", "--call", "main", "-o", str(tmp_path / "big"))
    assert got.returncode != 0
    assert "ValueError" in got.stderr


def test_int_other_overloads_keep_fencing() -> None:
    # str (with or without a base argument) and the no-arg form fence.
    # int(float) is not part of this item: it was already lowered on master
    # (ps_float_to_int) and stays as-is.
    for source in ('int("ff", 16)\n', "int()\n"):
        mod = lower_source(source)
        assert len(mod["fences"]) == 1, source
        fence = mod["fences"][0]
        assert fence["code"] == "PS2003", source
        assert "int is not lowered for these arguments" in fence["message"], source
    mod = lower_source("int(2.5, 10)\n")
    assert [f["code"] for f in mod["fences"]] == ["PS2003"]
    assert "int is not lowered for these arguments" in mod["fences"][0]["message"]
