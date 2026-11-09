"""Replay the divergence ledger (#522).

docs/reference/divergences.md claims that anything not listed is identical
to CPython. The ledger (docs/reference/divergence-ledger.json, regenerated
by scripts/gen_divergence_ledger.py) gives that claim teeth: every numbered
divergence and every 2026-10-24 audit finding carries a probe and recorded
bytes, and this test re-runs each probe under the pinned CPython and the
current compiler. A row that stops matching is a reviewed change — the
divergence was fixed, or it moved.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from scripts.gen_divergence_ledger import PROBES, run_sides
from tests.test_gates import ROOT

LEDGER = ROOT / "docs" / "reference" / "divergence-ledger.json"
DIVERGENCES = ROOT / "docs" / "reference" / "divergences.md"

ROWS = json.loads(LEDGER.read_text())["rows"]
PROBED = [r for r in ROWS if r["probe"]]


def numbered_divergences() -> list[int]:
    """The numbered entries in divergences.md's "Numbered divergences" section."""
    text = DIVERGENCES.read_text()
    section = text.split("## Numbered divergences", 1)[1]
    section = section.split("\n## ", 1)[0]
    return [int(m.group(1)) for m in re.finditer(r"^(\d+)\. ", section, re.M)]


@pytest.mark.parametrize("row", PROBED, ids=lambda r: r["id"])
def test_row_bytes(row: dict, tmp_path: Path) -> None:
    got = run_sides(PROBES / Path(row["probe"]).name, tmp_path, row.get("oracle_args"))
    for side in ("cpython", "pyscriptc"):
        assert got[side] == row[side], (
            f"{row['id']} moved on the {side} side:\n{got[side]}\nrecorded:\n{row[side]}\n"
            "if the change is intended, regenerate with scripts/gen_divergence_ledger.py"
        )


def test_every_numbered_divergence_has_a_row() -> None:
    listed = {r["id"] for r in ROWS if r["listed"]}
    assert listed == {f"D{n}" for n in numbered_divergences()}


def test_audit_findings_have_rows() -> None:
    audit = {r["id"] for r in ROWS if not r["listed"]}
    assert audit == {f"AUDIT-D{n}" for n in range(1, 13)}


def test_unprobed_rows_carry_a_reason() -> None:
    for row in ROWS:
        if row["probe"] is None:
            assert row["note"], f"{row['id']} is unprobed with no recorded reason"
            assert row["cpython"] is None and row["pyscriptc"] is None


def test_probes_exist_and_match_the_row() -> None:
    for row in PROBED:
        path = ROOT / row["probe"]
        assert path.parent == PROBES and path.is_file(), row["id"]
