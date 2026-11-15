# SPDX-License-Identifier: Apache-2.0
"""The qualified annotation `mod.Thing` names a cross-module class (#1242).

The call path (`mod.Thing(...)`) already resolved a program module's class
through the package export table, but the annotation checker had no branch
for the same spelling: `def use(t: mod.Thing)` fenced with PS1106 while
the plain-name form (`from mod import Thing`) lowered.

Each acceptance case compiles a program directory, runs the binary, and
byte-compares stdout, stderr and the exit code against CPython 3.14.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.test_gates import run_cli

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "probes" / "differential" / "module_class_annotation"

MOD = "class Thing:\n    def __init__(self, n: str) -> None:\n        self.n = n\n"


def _program(tmp_path: Path, main: str, mod: str = MOD) -> Path:
    root = tmp_path / "prog"
    root.mkdir()
    (root / "mod.py").write_text(mod, encoding="utf-8")
    (root / "__main__.py").write_text(main, encoding="utf-8")
    return root


def _differential(tmp_path: Path, main: str, mod: str = MOD) -> subprocess.CompletedProcess[str]:
    """Compile the program directory, run it, and pin the three channels to CPython."""
    root = _program(tmp_path, main, mod)
    out = str(tmp_path / "prog.bin")
    got = run_cli("compile", str(root), "--run", "--emit", "bin", "-o", out)
    expected = subprocess.run(
        [sys.executable, "__main__.py"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="latin-1",
        check=False,
    )
    assert (got.returncode, got.stdout, got.stderr) == (
        expected.returncode,
        expected.stdout,
        expected.stderr,
    )
    return got


def test_the_qualified_annotation_lowers_and_matches_cpython(tmp_path: Path) -> None:
    # The issue's repro: the annotation spells the class through the module,
    # the construction spells it the same way, and the call reads the field.
    got = _differential(
        tmp_path,
        'import mod\n\ndef use(t: mod.Thing) -> str:\n    return t.n\n\nt = mod.Thing("y")\nprint(use(t))\n',
    )
    assert got.stdout == "y\n"


def test_the_plain_name_form_still_lowers(tmp_path: Path) -> None:
    # The plain-name spelling is the baseline the qualified one now matches.
    got = _differential(
        tmp_path,
        'from mod import Thing\n\ndef use(t: Thing) -> str:\n    return t.n\n\nt = Thing("y")\nprint(use(t))\n',
    )
    assert got.stdout == "y\n"


def test_the_committed_probe_matches_cpython(tmp_path: Path) -> None:
    # The differential probe under probes/differential/ (the AGENTS.md rule):
    # the compiled program prints what CPython prints.
    out = str(tmp_path / "probe.bin")
    got = run_cli("compile", str(PROBE), "--run", "--emit", "bin", "-o", out)
    expected = subprocess.run(
        [sys.executable, "__main__.py"],
        cwd=PROBE,
        capture_output=True,
        text=True,
        encoding="latin-1",
        check=False,
    )
    assert (got.returncode, got.stdout, got.stderr) == (
        expected.returncode,
        expected.stdout,
        expected.stderr,
    )
    assert got.stdout == "y\n"


def test_the_qualified_annotation_names_the_defining_module_class(tmp_path: Path) -> None:
    # The annotation resolves through the export table, so the consumer's
    # class record is the defining module's: one class, one name.
    from pyscriptc.package import lower_directory

    root = _program(
        tmp_path,
        "import mod\n\ndef use(t: mod.Thing) -> str:\n    return t.n\n",
    )
    module, _source = lower_directory(root)
    assert module["fences"] == []
    names = {cls["name"] for cls in module["classes"]}
    assert "Thing" in names
