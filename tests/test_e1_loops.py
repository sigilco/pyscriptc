"""E1 item 3: `break` and `continue` statements lower inside loops.

The jump statements ride the E0 loop-flag machinery (`_loop_flag`,
`_continue_loop_flag`, `_skip_rest`, `_break_init`): a `break` sets this
loop's flag and the rest of the body is guarded; a `continue` sets the
continue flag and the `for` step stays outside the guard. Nested loops
allocate one flag pair per loop, so a jump binds to the innermost loop -
the outer loop's flags are saved and restored around the body. Outside a
loop, in a function or in the synthesized entry, the statement is a
PS2002 fence naming the statement.
"""

from __future__ import annotations

import sys
from pathlib import Path

from tests.test_e1_entry import _entry
from tests.test_gates import run_cli, write_case
from tests.test_spike import ROOT

from pyscriptc.lower import lower_source


def oracle_script(path: Path) -> str:
    import subprocess

    proc = subprocess.run(
        [sys.executable, str(path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout


def test_entry_for_break_and_continue_run_like_cpython(tmp_path: Path) -> None:
    # A for loop at module level: break leaves before the print, continue
    # skips the print but the step still runs.
    path = write_case(
        tmp_path,
        "loops.py",
        "\n".join(
            [
                "for i in range(5):",
                "    if i == 3:",
                "        break",
                "    print(i)",
                "for n in range(5):",
                "    if n == 1:",
                "        continue",
                "    print(n)",
            ]
        ),
    )
    got = run_cli("compile", str(path), "--skip-check", "--emit", "bin", "-o", str(tmp_path / "loops"), "--run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle_script(path) == "0\n1\n2\n0\n2\n3\n4\n"


def test_entry_while_break_and_continue_run_like_cpython(tmp_path: Path) -> None:
    # A while loop in the entry: the counter lives in the entry body, break
    # ends the loop, continue skips the tail while the test still runs.
    path = write_case(
        tmp_path,
        "whiles.py",
        "\n".join(
            [
                "def one() -> int:",
                "    return 1",
                "",
                "",
                "k: int = one() - 1",
                "while True:",
                "    k = k + 1",
                "    if k == 4:",
                "        break",
                "    print(k)",
                "print(k)",
                "n: int = one() - 1",
                "while n < 8:",
                "    n = n + 1",
                "    if n == 4:",
                "        continue",
                "    print(n)",
            ]
        ),
    )
    got = run_cli("compile", str(path), "--skip-check", "--emit", "bin", "-o", str(tmp_path / "whiles"), "--run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle_script(path) == "1\n2\n3\n4\n1\n2\n3\n5\n6\n7\n8\n"


def test_nested_loops_bind_to_the_innermost_loop(tmp_path: Path) -> None:
    # The inner break leaves only the inner while; the outer for finishes
    # every row. The outer continue skips one outer tail without touching
    # the inner loop's flags.
    path = write_case(
        tmp_path,
        "nested.py",
        "\n".join(
            [
                "for i in range(4):",
                "    j: int = 0",
                "    while j < 6:",
                "        j = j + 1",
                "        if j == 2:",
                "            continue",
                "        if j == 5:",
                "            break",
                "        print(i, j)",
                "    if i == 1:",
                '        print("skip row", i)',
                "        continue",
                "    if i == 2:",
                "        break",
                '    print("outer", i)',
            ]
        ),
    )
    got = run_cli("compile", str(path), "--skip-check", "--emit", "bin", "-o", str(tmp_path / "nested"), "--run")
    assert got.returncode == 0, got.stderr
    expect = "0 1\n0 3\n0 4\nouter 0\n1 1\n1 3\n1 4\nskip row 1\n2 1\n2 3\n2 4\n"
    assert got.stdout == oracle_script(path) == expect


def test_nested_loop_flags_are_one_pair_per_loop() -> None:
    # Each loop allocates its own brk/cont temps, and the outer loop's pair
    # is restored around the body - that is the innermost-binding mechanism.
    mod = lower_source(
        """
for i in range(2):
    for j in range(4):
        if j == 1:
            break
    if i == 0:
        break
"""
    )
    assert mod["fences"] == []
    entry = _entry(mod)
    assert entry is not None
    flags = [local["name"] for local in entry["locals"] if ".ps_brk_" in local["name"]]
    assert len(flags) == 2


def test_break_continue_outside_a_loop_fence(tmp_path: Path) -> None:
    # Module top level, the synthesized entry after a live statement, and a
    # function body: all three name the statement and stay PS2002.
    cases = [
        ("break\n", "break outside a loop is not lowered"),
        ("continue\n", "continue outside a loop is not lowered"),
        ("print(1)\nbreak\n", "break outside a loop is not lowered"),
        ("def probe() -> int:\n    continue\n    return 0\n", "continue outside a loop is not lowered"),
    ]
    for source, text in cases:
        mod = lower_source(source)
        assert text in {f["message"] for f in mod["fences"]}, source
        assert all(f["code"] == "PS2002" for f in mod["fences"])
    for index, (source, text) in enumerate(cases):
        path = write_case(tmp_path, f"fence{index}.py", source)
        got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm")
        assert got.returncode == 3, source
        assert text in got.stderr, source
