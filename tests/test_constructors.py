"""bool() and list(), and typing.Set unwrapped like typing.List."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, run_cli, write_case


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_bool_and_list_match_cpython(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "ctors.py",
        "\n".join(
            [
                "import math",
                "from typing import Set",
                "",
                "def take(*xs: int) -> int:",
                "    ys: list[int] = list(xs)",
                "    ys[0] = 9",
                "    return xs[0] * 10 + ys[0] + len(ys)",
                "",
                "def flags() -> int:",
                "    n: int = 0",
                "    if bool(0):",
                "        n = n + 1",
                "    if bool(1):",
                "        n = n + 2",
                "    if bool(0.0):",
                "        n = n + 4",
                "    if bool(1.5):",
                "        n = n + 8",
                "    if bool(math.nan):",
                "        n = n + 16",
                "    if bool(''):",
                "        n = n + 32",
                "    if bool('a'):",
                "        n = n + 64",
                "    if bool(b''):",
                "        n = n + 128",
                "    if bool(b'a'):",
                "        n = n + 256",
                "    empty: list[int] = []",
                "    if bool(empty):",
                "        n = n + 512",
                "    filled: list[int] = [1]",
                "    if bool(filled):",
                "        n = n + 1024",
                "    blank: dict[str, int] = {}",
                "    if bool(blank):",
                "        n = n + 2048",
                "    marked: dict[str, int] = {'a': 1}",
                "    if bool(marked):",
                "        n = n + 4096",
                "    if bool(True):",
                "        n = n + 8192",
                "    if bool(False):",
                "        n = n + 16384",
                "    seen: set[int] = {3, 1, 3}",
                "    if bool(seen):",
                "        n = n + 32768",
                "    missing: set[str] = set()",
                "    if bool(missing):",
                "        n = n + 65536",
                "    copied: list[int] = list(filled)",
                "    copied[0] = 7",
                "    members: Set[int] = {4, 4, 1}",
                "    as_list: list[int] = list(members)",
                "    total: int = 0",
                "    for item in as_list:",
                "        total = total + item",
                "    return n + filled[0] * 100000 + copied[0] * 1000000 + total + len(as_list) + take(4, 5)",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "flags")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "flags")


def test_bool_and_list_refuse_the_closed_cases(tmp_path: Path) -> None:
    cases = [
        (
            "class Box:\n    n: int\n    def __init__(self, n: int) -> None:\n        self.n = n\n"
            "def f() -> int:\n    if bool(Box(1)):\n        return 1\n    return 0\n",
            "bool() of class",
        ),
        (
            "def f() -> int:\n    text: str = 'ab'\n    ys: list[str] = list(text)\n    return len(ys)\n",
            "list() takes a list",
        ),
        ("def f() -> int:\n    return len(list())\n", "list expects 1 argument"),
    ]
    for source, needle in cases:
        path = write_case(tmp_path, "no.py", source)
        got = run_cli("compile", str(path), "--skip-check", "--emit", "ir", "-o", str(tmp_path / "no.ir.json"))
        assert got.returncode == 3, source
        assert needle in got.stderr, got.stderr
