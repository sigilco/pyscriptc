"""E1 item 5: `del` in the synthesized entry.

Pins what works after item 1 (entry lowering) against the CPython oracle,
and the fence surface that stays:

- A local del inside an entry-declared variable is the same drop a
  function body gets (`_del` lowers to `pass` once the name is provably
  unread afterwards): entry variables live in the entry's `locals`, so
  the local path applies unchanged.
- A del of a module constant (a script's `x: int = 5` binds a constant,
  which every read inlines) stays fenced PS2002. The real fix - unbinding
  a name that later function reads may have inlined - is module-del
  machinery, descoped to a later epic. The fence does not name the
  statement twice or blame the name as "unbound" at module level; the
  wording is pinned below so any precision change updates this test
  deliberately.
"""

from __future__ import annotations

import sys
from pathlib import Path

from tests.test_gates import run_cli, write_case
from tests.test_spike import ROOT


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


def test_entry_local_del_runs_like_cpython(tmp_path: Path) -> None:
    # n is bound inside the entry by a rolled-in AnnAssign (its right side
    # is a call, so it is not a module constant), and it is not read after
    # the del: the ordinary local drop applies and the program output
    # matches CPython byte for byte.
    path = write_case(
        tmp_path,
        "entry_del.py",
        "\n".join(
            [
                "def f() -> int:",
                "    return 3",
                "",
                "",
                "n: int = f()",
                "del n",
                "m: int = f()",
                'print("kept", m)',
            ]
        ),
    )
    got = run_cli("compile", str(path), "--skip-check", "--emit", "bin", "-o", str(tmp_path / "entry_del"), "--run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle_script(path) == "kept 3\n"


def test_module_constant_del_stays_fenced(tmp_path: Path) -> None:
    # x: int = 5 binds a module constant (reads inline it), so the entry's
    # del cannot unbind it without module-del machinery. The fence names
    # the statement and the name; the wording is pinned.
    path = write_case(tmp_path, "const_del.py", "x: int = 5\ndel x\nprint(1)\n")
    got = run_cli(
        "compile",
        str(path),
        "--skip-check",
        "--emit",
        "llvm",
        "-o",
        str(tmp_path / "const_del.ll"),
    )
    assert got.returncode == 3, got.stderr
    assert "PS2002" in got.stderr
    assert "del of an unbound name is not lowered" in got.stderr
