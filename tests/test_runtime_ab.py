"""Runtime A/B harness: direct emitter vs opt-in MIR lane (epic #817, task B9).

The emit-flip release gate. Compiles each MIR leaf fixture through both lanes,
runs the binaries in isolated temp dirs, and requires stdout, stderr and the
return code to match. Skips cleanly when no C toolchain is on PATH. Does not
change either lane.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from scripts.runtime_ab import (
    LEAF_DIR,
    PASS,
    SKIP,
    Observation,
    ProgramVerdict,
    compare_program,
    compare_programs,
    default_programs,
    has_c_toolchain,
    lane_env,
    leaf_fixture_paths,
    main,
    render_table,
    slice_counts,
)

from pyscriptc.miremit import MIR_ENV

ROOT = Path(__file__).resolve().parents[1]


def test_leaf_fixtures_exist() -> None:
    paths = leaf_fixture_paths()
    assert paths, f"no MIR leaf fixtures under {LEAF_DIR}"
    names = {path.name for path in paths}
    assert "leafint.py" in names
    assert "mirmix.py" in names


def test_default_programs_are_in_slice_leaf_fixtures() -> None:
    defaults = default_programs()
    assert defaults
    assert defaults == sorted(defaults, key=lambda p: p.name)
    for path in defaults:
        assert path.parent == LEAF_DIR
        in_slice, total = slice_counts(path)
        assert in_slice > 0
        assert total >= in_slice


def test_mirmix_is_mixed_slice() -> None:
    """mirmix.py is the strangler sample: `g` is in-slice, `main` rides the direct path."""
    path = LEAF_DIR / "mirmix.py"
    in_slice, total = slice_counts(path)
    assert in_slice >= 1
    assert total > in_slice


def test_lane_env_isolates_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MIR_ENV, "1")
    direct = lane_env(False)
    mir = lane_env(True)
    assert direct[MIR_ENV] == "0"
    assert mir[MIR_ENV] == "1"
    assert "PYTHONHASHSEED" in direct and direct["PYTHONHASHSEED"] == "0"


def test_render_table_has_per_program_verdict() -> None:
    rows = [
        ProgramVerdict(
            LEAF_DIR / "leafint.py",
            PASS,
            3,
            4,
            "match",
            Observation(b"7\n", b"", 0),
            Observation(b"7\n", b"", 0),
        ),
        ProgramVerdict(LEAF_DIR / "skip.py", SKIP, 0, 1, "no function where supported_fn holds"),
    ]
    table = render_table(rows)
    assert "program" in table
    assert "verdict" in table
    assert "leafint.py" in table
    assert PASS in table
    assert SKIP in table
    assert "2 programs:" in table


def test_unsupported_program_skips(tmp_path: Path) -> None:
    source = tmp_path / "lists_only.py"
    source.write_text(
        "def tot(xs: list[int]) -> int:\n    n = 0\n    for x in xs:\n        n = n + x\n    return n\n",
        encoding="utf-8",
    )
    row = compare_program(source)
    assert row.verdict == SKIP
    assert row.in_slice == 0
    assert "supported_fn" in row.note


def test_harness_skips_without_c_toolchain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("scripts.runtime_ab.has_c_toolchain", lambda: False)
    code = main([])
    assert code == 2


@pytest.mark.skipif(not has_c_toolchain(), reason="no C toolchain")
def test_leaf_fixtures_runtime_match() -> None:
    rows = compare_programs()
    assert rows, "default corpus was empty"
    failed = [row for row in rows if row.verdict != PASS]
    assert not failed, render_table(rows)
    names = {row.name for row in rows}
    assert "leafint.py" in names
    assert "mirmix.py" in names


@pytest.mark.skipif(not has_c_toolchain(), reason="no C toolchain")
def test_tool_prints_verdict_table() -> None:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "runtime_ab.py"), str(LEAF_DIR / "leafint.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    assert proc.returncode == 0, proc.stderr
    assert "verdict" in proc.stdout
    assert "leafint.py" in proc.stdout
    assert PASS in proc.stdout
    assert "1 programs:" in proc.stdout
