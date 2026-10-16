"""Tests for the fence-message census tool."""

from __future__ import annotations

import json
from pathlib import Path

from benchmarks.compat2.fence_messages import census, iter_fail_sites, main


def _write_snippet(tmp_path: Path) -> Path:
    snippet = tmp_path / "mod.py"
    snippet.write_text(
        "class Lowerer:\n"
        "    def fail(self, node, code, message):\n"
        "        raise Fence(code, message, span_of(node))\n"
        "\n"
        "    def calls(self, node, name):\n"
        '        self.fail(node, "PS2002", "super is not lowered")\n'
        '        self.fail(node, "PS2003", f"{name} is not lowered for these arguments")\n'
        '        self.fail(node, "PS2001", "json.loads argument is " + show(node) + ", expected str")\n'
        "        other.notfail(node, 1, 2)\n"
        '        self.fail(node, "PS2002")\n',
        encoding="utf-8",
    )
    return snippet


def test_synthetic_snippet(tmp_path: Path) -> None:
    sites = iter_fail_sites(_write_snippet(tmp_path))
    assert [s["code"] for s in sites] == ["PS2002", "PS2003", "PS2001"]
    assert sites[0]["message"] == "super is not lowered"
    assert sites[1]["message"] == "{} is not lowered for these arguments"
    assert sites[2]["message"] == "json.loads argument is {}, expected str"
    assert sites[0]["line"] == 6


def test_output_is_byte_stable(tmp_path: Path, capsys) -> None:
    first = tmp_path / "one.json"
    second = tmp_path / "two.json"
    assert main(["--out", str(first)]) == 0
    assert main(["--out", str(second)]) == 0
    assert first.read_bytes() == second.read_bytes()
    assert first.read_bytes().endswith(b"\n")
    sites = json.loads(first.read_text())
    assert sites == sorted(sites, key=lambda s: (s["code"], s["file"], s["line"], s["message"]))


def test_real_tree_inventory() -> None:
    sites = census()
    assert len(sites) > 100
    for site in sites:
        assert site["code"]
        assert site["message"]
        assert site["file"].startswith("src/pyscriptc/")
        assert site["line"] > 0


def test_stdout_counts(capsys) -> None:
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "total: " in out
    assert "PS2001: " in out
