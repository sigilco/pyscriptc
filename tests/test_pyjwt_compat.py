"""PyJWT 2.15.1 HMAC surface: the vendored files stay fenced, the compat port round-trips.

Two halves:

- ``benchmarks/compat/pyjwt/src/jwt`` is the minimal import closure of
  ``jwt.encode``/``jwt.decode`` for HS256, byte-identical to the top100 pin.
  Its compile still fences; the test pins today's fence count so progress
  toward compiling real PyJWT unchanged is measurable.
- ``benchmarks/compat/pyjwt/src/jwtapp`` compiles ``jwt_compat.py``, the
  HMAC-only adaptation, and runs an encode/decode round-trip. The produced
  token must equal the one real PyJWT emits for the same payload, so the test
  generates the expectation with the vendored package under CPython.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from tests.test_gates import run_cli

ROOT = Path(__file__).resolve().parents[1]
TOP100 = ROOT / "benchmarks" / "compat" / "top100" / "src"
FIXTURE = ROOT / "benchmarks" / "compat" / "pyjwt" / "src"


def test_real_pyjwt_hmac_tree_still_fences() -> None:
    """The byte-identical import closure does not compile today.

    Pin the fence count, not the code list: shrinking or reshaping it is the
    signal that backlog items are landing. Update the number when a lowering
    clears fences here; the README's family table explains what remains.
    """

    proc = run_cli("compile", str(FIXTURE / "jwt"), "--emit", "ir", "-o", os.devnull, "--skip-check")
    assert proc.returncode == 3
    # 105 fences after T2-2 (`abc` import, `ABC` base, and `@abstractmethod`
    # erase, -2 from the 107 left by T2-3 kw-only normalization).
    # 100 fences after the E2 M2 builtin exception hierarchy: `jwt/warnings.py`
    # defines warning classes on `DeprecationWarning` and `UserWarning`, which
    # the hierarchy table now lowers, so five export fences cleared (105 - 5).
    # 96 after the generator work (#165): the `collections.abc` import and
    # the Iterator/Sequence annotations lower, so four fences clear; one
    # SigOptions annotation fence reshapes.
    # 95 after the str model S1 (#473): the AnnAssign fence branch binds the
    # name on a fenced annotation, clearing the `byte_length` PS1001
    # follow-on fence in jwt/utils.py.
    # 93 after the local, parameter and return inference (#500): the vendored
    # tree's own unannotated locals and defaulted parameters now bind from
    # their values, clearing two fences in jwt/math_.py.
    # Shrinking means a backlog lowering cleared fences here; update the pin
    # when that happens. `compile` prints at most 3 root fences and counts the rest.
    assert proc.stderr.count("PS") == 3
    assert "(and 90 more fences)" in proc.stderr


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_jwt_compat_roundtrip_matches_real_pyjwt(tmp_path: Path) -> None:
    package = FIXTURE / "jwtapp"
    out = tmp_path / "jwtapp-bin"
    proc = run_cli(
        "compile",
        str(package),
        "--run",
        "--call",
        "jwtapp.main.probe",
        "--args",
        "[]",
        "-o",
        str(out),
        "--skip-check",
    )
    assert proc.returncode == 0, proc.stderr

    oracle = subprocess.run(
        [
            sys.executable,
            "-W",
            "ignore",
            "-c",
            "import jwt\n"
            "t = jwt.encode({'exp': '2000000000', 'sub': 'u1'}, 'secret', algorithm='HS256')\n"
            "d = jwt.decode(t, 'secret', algorithms=['HS256'])\n"
            "print(t)\n"
            "print(f\"{d['sub']}:{d['exp']}\")",
        ],
        env={"PYTHONPATH": str(TOP100), "PATH": os.environ.get("PATH", "")},
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.stdout == oracle.stdout
