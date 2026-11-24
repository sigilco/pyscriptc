# SPDX-License-Identifier: Apache-2.0
"""The numeric tower and container algebra (issue #504).

True division, int and float modulo and floor division with CPython's sign
rules, exact int/float comparison, sequence repetition, set algebra with the
binary and in-place forms, and element-wise container equality all lower.
The committed differential probe
`probes/differential/numeric_tower_algebra.py` runs every form and must
byte-match the pinned CPython on stdout, stderr and the exit code. The
edge cases below pin the CPython answers the probe's shape does not reach
on its own, and the forms that stay fenced are named so a change to them is
a reviewed change.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from scripts.gen_divergence_ledger import oracle_bin
from tests.test_gates import ROOT, run_cli, write_case

PROBE = ROOT / "probes" / "differential" / "numeric_tower_algebra.py"


def _cpython(path: Path) -> subprocess.CompletedProcess[str]:
    """Run the program under the pinned CPython 3.14 oracle (the ledger's choice)."""
    return subprocess.run([oracle_bin(), str(path)], capture_output=True, text=True, encoding="latin-1", cwd=str(ROOT))


def test_probe_matches_cpython(tmp_path: Path) -> None:
    want = _cpython(PROBE)
    assert want.returncode == 0, want.stderr
    got = run_cli("compile", str(PROBE), "--run", "-o", str(tmp_path / "probe"))
    assert got.returncode == 0, got.stderr
    assert got.stdout == want.stdout
    assert got.stderr == want.stderr


_EDGE = """\
nan: float = float("nan")
print(nan == nan, nan != nan, nan < nan)
print(0.0 == -0.0, -0.0 // 3.0, -0.0 % 3.0)
print(1.0 // 0.1, -6.0 % 3.0, 7.5 % -2)
big: int = 9007199254740993
near: float = 9007199254740992.0
print(big == near, big < near, near <= big)
print(-7 // 2, -7 % 2, 7 // -2)
print(1e308 * 10, 2 ** -1)
"""

_EDGE_EXPECTED = [
    "False True False",
    "True -0.0 0.0",
    "9.0 0.0 -0.5",
    "False False True",
    "-4 1 -4",
    "inf 0.5",
]


def test_edge_cases_match_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "edge.py", _EDGE)
    want = _cpython(path)
    assert want.returncode == 0, want.stderr
    assert want.stdout.splitlines() == _EDGE_EXPECTED
    got = run_cli("compile", str(path), "--run", "-o", str(tmp_path / "edge"))
    assert got.returncode == 0, got.stderr
    assert got.stdout == want.stdout


@pytest.mark.parametrize(
    ("name", "source", "code"),
    [
        (
            "set_subset.py",
            "st: set[int] = {1}\nprint(st <= st)\n",
            "PS2004",
        ),
        (
            "tuple_mixed_eq.py",
            "n: int = 1\nx: float = 1.0\ns: str = 'a'\nprint((n, s) == (x, s))\n",
            "PS2004",
        ),
    ],
)
def test_remaining_forms_stay_fenced(tmp_path: Path, name: str, source: str, code: str) -> None:
    path = write_case(tmp_path, name, source)
    got = run_cli("compile", str(path), "--emit", "ir", "-o", str(tmp_path / name))
    assert got.returncode != 0
    assert code in got.stderr, got.stderr
