# SPDX-License-Identifier: Apache-2.0
"""The default island routing for imports outside the closed table (issue #1308).

N2: the closed import table is not an admission gate any more. An import
outside it routes to the island lane by default, with no opt-out flag, and
the closed-table refusal becomes an advisory note instead of a build
failure. A language-wise `PSxxxx` fence inside a lowered definition still
stops the compile.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from pyscriptc import coverage
from pyscriptc.lower import lower_file
from pyscriptc.stdlib_queue import island_policy

ROOT = Path(__file__).resolve().parents[1]

UNKNOWN_IMPORT = "import sqlite3\n"
THIRD_PARTY_IMPORT = "import requests\n"


def _lower_with_default(path: Path) -> tuple[dict, str]:
    """Lower the way the CLI compile path does.

    The default routing is installed by the entry points that serve a
    user (the CLI compile path and the coverage pass), not by the module
    global: a bare `lower_file` (the TIR probe corpus, the reducer)
    keeps the closed-table fence, so the typed IR a frozen gate measures
    does not move under a routing change.
    """

    with island_policy([], default=True):
        return lower_file(str(path))


def _run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pyscriptc", *args],
        cwd=cwd or ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_an_unknown_import_lowers_without_a_fence(tmp_path: Path) -> None:
    """The import outside the closed table is a note, not a fence."""

    script = _write(tmp_path, "app.py", UNKNOWN_IMPORT)
    module, _ = _lower_with_default(script)
    assert module["fences"] == []
    notes = module.get("_notes") or []
    assert any("sqlite3 is not in the closed import table" in note for note in notes)


def test_a_third_party_import_lowers_without_a_fence(tmp_path: Path) -> None:
    script = _write(tmp_path, "app.py", THIRD_PARTY_IMPORT)
    module, _ = _lower_with_default(script)
    assert module["fences"] == []
    assert any("requests is not in the closed import table" in note for note in (module.get("_notes") or []))


def test_a_from_import_outside_the_table_lowers_without_a_fence(tmp_path: Path) -> None:
    script = _write(tmp_path, "app.py", "from sqlite3 import connect\n")
    module, _ = _lower_with_default(script)
    assert module["fences"] == []


def test_the_note_names_the_island_route(tmp_path: Path) -> None:
    script = _write(tmp_path, "app.py", UNKNOWN_IMPORT)
    module, _ = _lower_with_default(script)
    notes = module.get("_notes") or []
    assert any("island lane" in note and "requires libpython" in note for note in notes)


def test_a_bare_lower_file_keeps_the_closed_table_fence(tmp_path: Path) -> None:
    """A caller that never asks for the island lane keeps the fence.

    The TIR probe corpus and the reducer lower bare, so the typed IR a
    frozen gate measures does not move under a routing change.
    """

    script = _write(tmp_path, "app.py", UNKNOWN_IMPORT)
    module, _ = lower_file(str(script))
    assert len(module["fences"]) == 1
    assert module["fences"][0]["code"] == "PS2002"
    assert "closed import table" in module["fences"][0]["message"]


def test_the_refuse_lane_still_fences(tmp_path: Path) -> None:
    """A `refuse` policy is the user's own deny; it still stops the build."""

    (tmp_path / "pyproject.toml").write_text("[tool.pyscriptc.imports]\nrich = 'refuse'\n", encoding="utf-8")
    script = _write(tmp_path, "app.py", "import rich\n")
    module, _ = _lower_with_default(script)
    assert len(module["fences"]) == 1
    assert module["fences"][0]["code"] == "PS2002"
    assert "refused by" in module["fences"][0]["message"]


def test_a_named_compile_policy_keeps_the_closed_table_fence(tmp_path: Path) -> None:
    """An explicit lane wins over the default routing."""

    (tmp_path / "pyproject.toml").write_text("[tool.pyscriptc.imports]\nrich = 'compile'\n", encoding="utf-8")
    script = _write(tmp_path, "app.py", "import rich\n")
    module, _ = _lower_with_default(script)
    assert len(module["fences"]) == 1
    assert "closed import table" in module["fences"][0]["message"]


def test_a_language_wise_fence_still_fails_the_build(tmp_path: Path) -> None:
    """PSxxxx fences inside a lowered definition are not library facts."""

    script = _write(
        tmp_path,
        "app.py",
        "import sqlite3\n\n\ndef main() -> int:\n    f = lambda x: x\n    return 1\n",
    )
    got = _run_cli("compile", str(script), "--skip-check", "--emit", "ir", "-o", str(tmp_path / "app.ir"))
    assert got.returncode == 3, got.stderr
    assert "PS1001" in got.stderr


def test_the_compile_reports_the_note_on_stderr(tmp_path: Path) -> None:
    script = _write(tmp_path, "app.py", UNKNOWN_IMPORT)
    got = _run_cli("compile", str(script), "--skip-check", "--emit", "ir", "-o", str(tmp_path / "app.ir"))
    assert got.returncode == 0, got.stderr
    assert "sqlite3 is not in the closed import table" in got.stderr
    # The note carries the code it replaced, but it is a note: no fence line.
    assert "PS2002:" not in got.stderr


def test_the_coverage_report_carries_the_note(tmp_path: Path) -> None:
    script = _write(tmp_path, "app.py", UNKNOWN_IMPORT)
    report = coverage.build_report(script)
    assert not [e for e in report["fences"] if "sqlite3" in e["message"]]
    assert any(n["name"] == "sqlite3" for n in report["notes"])


def test_the_assess_report_names_the_routed_import(tmp_path: Path) -> None:
    script = _write(tmp_path, "app.py", UNKNOWN_IMPORT)
    got = _run_cli("assess", str(script), "--json")
    assert got.returncode == 0, got.stderr
    data = json.loads(got.stdout)
    assert any(n["name"] == "sqlite3" for n in data["notes"])
    assert "sqlite3" in [i["name"] for i in data["imports"]]
    island_import = next(i for i in data["imports"] if i["name"] == "sqlite3")
    assert island_import["routing"] == "island"


def test_the_imports_report_routes_the_import_to_the_island(tmp_path: Path) -> None:
    script = _write(tmp_path, "app.py", UNKNOWN_IMPORT)
    got = _run_cli("imports", str(script))
    assert got.returncode == 0, got.stderr
    assert "sqlite3" in got.stdout
    assert "island" in got.stdout


def test_no_opt_out_flag_exists(tmp_path: Path) -> None:
    """There is no flag that turns the default import routing off."""

    script = _write(tmp_path, "app.py", UNKNOWN_IMPORT)
    got = _run_cli("compile", str(script), "--skip-check", "--no-island-imports", "--emit", "ir")
    assert got.returncode != 0
    assert "unrecognized arguments" in got.stderr or "no such option" in got.stderr.lower()
