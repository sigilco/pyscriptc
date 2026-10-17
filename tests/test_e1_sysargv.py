"""E1: `sys.argv` lowers as `list[str]` reads from the process argv.

The emitted entry is `main(i32 %argc, ptr %argv)` and materializes the
argument vector into a PsSeq via `ps_argv_init`/`ps_argv_get` in ps_argv.c.
Reads (`sys.argv`, `sys.argv[i]`, `len(sys.argv)`, iteration) work; element 0
is the real process path. `sys.argv` as a call target and `from sys import
argv` stay fenced.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from tests.test_gates import run_cli, write_case

from pyscriptc.lower import lower_source


def test_sys_argv_lowers_without_fences() -> None:
    mod = lower_source(
        """import sys


def probe() -> int:
    r: list[str] = sys.argv
    return len(r) * 1000003 + len(r[0])
"""
    )
    assert mod["fences"] == []
    probe = next(fn for fn in mod["functions"] if fn["name"] == "probe")
    calls: list[dict] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            if node.get("symbol") == "ps_argv_get":
                calls.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(probe["body"])
    assert calls
    for call in calls:
        assert call["callee"] == {"kind": "module", "module": "sys", "name": "argv"}
        assert call["type"] == {"kind": "list", "elem": {"kind": "str"}}
        assert call["args"] == []


def test_sys_argv_reads_real_process_argv(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "argv.py",
        "\n".join(
            [
                "import sys",
                "",
                "",
                "def main() -> int:",
                "    print(len(sys.argv))",
                "    print(sys.argv[0])",
                "    print(sys.argv[1])",
                "    for arg in sys.argv:",
                "        print(arg)",
                "    return 0",
                "",
            ]
        ),
    )
    got = run_cli("compile", str(path), "--emit", "bin", "--call", "main", "-o", str(tmp_path / "argv"))
    assert got.returncode == 0, got.stderr
    binary = str(tmp_path / "argv")
    proc = subprocess.run([binary, "one", "two three"], capture_output=True, text=True, check=True)
    lines = proc.stdout.splitlines()
    assert lines[0] == "3"
    assert lines[1] == binary
    assert lines[2] == "one"
    # The emitted binary prints main()'s return value as its last line.
    assert lines[3:-1] == [binary, "one", "two three"]
    assert lines[-1] == "0"


def test_sys_argv_call_and_from_import_stay_fenced() -> None:
    mod = lower_source("import sys\nsys.argv()\n")
    assert [f["code"] for f in mod["fences"]] == ["PS2001"]
    assert "sys.argv is not in the lowered subset of sys" in mod["fences"][0]["message"]
    mod = lower_source("from sys import argv\n")
    assert [f["code"] for f in mod["fences"]] == ["PS2001"]
    assert "sys.argv is not in the lowered subset of sys" in mod["fences"][0]["message"]
