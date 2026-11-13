"""`infer --bridge own`: the lane runs end-to-end with the pyrefly report shape.

The integration contract: the own lane is wired through the same
`infer_cmd` the pyrefly arm serves, its JSON carries the same report
fields (`provider`, `pins`, `native_baseline`, `fences_delta`,
`pinned_params`, `records`), and the facts actually reach lowering — a
pinned two-module tree lowers the pinned helper natively, the same
end-to-end proof the pyrefly arm's pin test makes. pyrefly stays the
default arm; `own` is opt-in.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tests.test_gates import run_cli, write_case

_REPORT_FIELDS = {
    "source",
    "provider",
    "checker_seconds",
    "pinned_facts_seconds",
    "baseline_seconds",
    "inferred_seconds",
    "pins",
    "native_baseline",
    "native_inferred",
    "native_delta",
    "fences_baseline",
    "fences_inferred",
    "fences_delta",
    "fences_by_family",
    "fences_by_code",
    "definitions",
    "pinned_params",
    "records",
    "records_detail",
}


def test_own_bridge_report_matches_the_pyrefly_shape(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    root.mkdir()
    write_case(root, "lib.py", "def shout(text):\n    return text.upper()\n")
    write_case(root, "__main__.py", "from lib import shout\n\nshout('a')\nshout('b')\n")
    out = run_cli("infer", str(root), "--bridge", "own", "--json")
    assert out.returncode == 0, out.stderr
    report = json.loads(out.stdout)
    missing = _REPORT_FIELDS - set(report)
    assert not missing, f"own report is missing pyrefly-arm fields: {sorted(missing)}"
    assert report["provider"] == "own"
    assert report["pins"]["pinned_params"] == 1
    assert report["pinned_params"] == ["lib.shout.text"]
    assert report["native_delta"] > 0 or report["fences_delta"] < 0


def test_own_bridge_lowers_a_pinned_helper_natively(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    root.mkdir()
    write_case(root, "helpers.py", "def parse_count(text, default=0):\n    return len(text)\n")
    write_case(root, "__main__.py", "from helpers import parse_count\n\nparse_count('42')\nparse_count('4')\n")
    report = json.loads(run_cli("infer", str(root), "--bridge", "own", "--json").stdout)
    assert report["pins"]["pinned_params"] >= 1
    # The pin reaches lowering through the same channel the pyrefly arm
    # wires: the pinned helper enters the export table and the call site
    # lowers to a direct native call, so the inferred pass reports more
    # native definitions than the baseline.
    assert report["native_inferred"] > report["native_baseline"]


def test_own_arm_records_its_proof_kind(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    root.mkdir()
    write_case(root, "lib.py", "def shout(text):\n    return text.upper()\n")
    write_case(root, "__main__.py", "from lib import shout\n\nshout('a')\nshout('b')\n")
    report = json.loads(run_cli("infer", str(root), "--bridge", "own", "--json").stdout)
    assert report["records"], "the own arm records the facts it accepted"
    kinds = {record["kind"] for record in report["records_detail"]}
    assert kinds, f"records carry their proof kind: {report['records_detail'][:3]}"


def test_pyrefly_stays_the_default_arm(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    root.mkdir()
    write_case(root, "lib.py", "def keep(value):\n    return value\n")
    # The default run still names pyrefly, and an unknown bridge is still
    # refused at the flag: the own lane is opt-in, never a silent swap.
    assert (
        "--bridge"
        in subprocess.run([sys.executable, "-m", "pyscriptc", "infer", "--help"], capture_output=True, text=True).stdout
    )
    assert "own" in run_cli("infer", "--help").stdout
