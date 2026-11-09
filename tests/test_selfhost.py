"""Self-compilation: the compiler compiles itself (issue #814).

The whole compiler package compiles in program mode under the island
tier, and the resulting binary is the compiler: it compiles a hello world
and the binary runs. This is the self-host receipt.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "pyscriptc"

HELLO = """\
import sys


def main() -> int:
    print("hello from the self-built compiler")
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""

needs_cc = pytest.mark.skipif(
    not (ROOT / "src" / "pyscriptc" / "runtime").exists(),
    reason="runtime sources required",
)


def _cli(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pyscriptc", *args], cwd=ROOT, capture_output=True, text=True, timeout=1800
    )


@needs_cc
def test_compiler_compiles_itself_and_the_result_compiles_hello(tmp_path: Path) -> None:
    """The compiler compiles itself in program mode; the result compiles a
    hello world and both binaries run.

    The checker is skipped: the preflight checker (pyrefly) crashes on the
    compiler's own tree, which is a checker bug and not a compile refusal.
    The lowering, the island plan and the binary are what this pins.
    """
    binary = tmp_path / "selfc"
    comp = _cli(
        "compile",
        str(SRC),
        "--program",
        "--fallback=cpython",
        "--skip-check",
        "--emit",
        "bin",
        "-o",
        str(binary),
        cwd=ROOT,
    )
    assert comp.returncode == 0, comp.stderr[-4000:]
    assert binary.is_file()

    # The self-built binary is the compiler: it prints its own usage.
    usage = subprocess.run([str(binary), "--help"], capture_output=True, text=True, timeout=120)
    assert usage.returncode == 0, usage.stderr
    assert "Compile a typed Python subset to native code" in usage.stdout

    hello = tmp_path / "hello.py"
    hello.write_text(HELLO, encoding="utf-8")
    hello_bin = tmp_path / "hello"
    built = subprocess.run(
        [str(binary), "compile", str(hello), "--emit", "bin", "-o", str(hello_bin)],
        capture_output=True,
        text=True,
        timeout=600,
        cwd=tmp_path,
    )
    assert built.returncode == 0, built.stderr[-4000:]
    ran = subprocess.run([str(hello_bin)], capture_output=True, text=True, timeout=120)
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout == "hello from the self-built compiler\n"


@needs_cc
def test_self_compile_reports_no_import_cycle_without_the_island_tier(tmp_path: Path) -> None:
    """The compiler's own tree has no import-time cycle, so program mode
    never refuses with a circular-import fence.

    The compiler used to report three cycles (imports -> config -> imports,
    imports -> coverage -> imports, embed -> sitedeps -> imports ->
    config -> embed). Each one closed through a function-local import, which
    CPython runs at call time and therefore never at import time. The plan
    now follows only the import-time edges, so the tree has no cycle: what
    program mode reports instead are the ordinary construct fences (the
    `Any` annotations and the closed import table), which is the honest
    state of the tree.
    """
    comp = _cli(
        "compile",
        str(SRC),
        "--program",
        "--skip-check",
        "--emit",
        "ir",
        "-o",
        str(tmp_path / "out"),
        cwd=ROOT,
    )
    assert comp.returncode == 3, comp.stderr[-4000:]
    assert "PS2002" in comp.stderr
    assert "circular import" not in comp.stderr


@needs_cc
def test_a_module_level_cycle_still_refuses_in_the_compiler_tree_shape(tmp_path: Path) -> None:
    """The relaxation is scoped to lazy imports: a module-level cycle in
    the same shape as the compiler's own tree still refuses with the path
    that closes it.
    """
    root = tmp_path / "cyc"
    root.mkdir()
    (root / "__main__.py").write_text("import a\n\nprint(a.one())\n", encoding="utf-8")
    (root / "a.py").write_text("import b\n\n\ndef one() -> int:\n    return 1\n", encoding="utf-8")
    (root / "b.py").write_text("import a\n\n\ndef two() -> int:\n    return 2\n", encoding="utf-8")
    comp = _cli(
        "compile",
        str(root),
        "--program",
        "--skip-check",
        "--emit",
        "ir",
        "-o",
        str(tmp_path / "out"),
        cwd=ROOT,
    )
    assert comp.returncode == 3, comp.stderr[-4000:]
    assert "circular import: a -> b -> a" in comp.stderr


@needs_cc
def test_assess_over_the_compiler_reports_a_stable_summary(tmp_path: Path) -> None:
    """The assess summary and the per-file plane maps agree (issue #814).

    The summary used to count the coverage classifier's island route (2
    definitions over the compiler tree) while the plane maps counted the
    planner's island route (1,809). The two numbers are one number now.
    """
    comp = _cli("assess", str(SRC), "--json", cwd=ROOT)
    assert comp.returncode == 0, comp.stderr[-4000:]
    report = json.loads(comp.stdout)
    plane_island = 0
    for f in report["files"]:
        plane = f["planeMap"]
        plane_island += sum(1 for p in plane.get("functions", {}).values() if p == "island")
        plane_island += sum(1 for p in plane.get("classes", {}).values() if p == "island")
    assert report["summary"]["definitions"]["island"] == plane_island
    assert report["summary"]["definitions"]["total"] > 0
