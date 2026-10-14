"""Checks on the committed probe set. Regeneration itself needs the Linux container and is not run here."""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest
from benchmarks.compat2.outcomes import CATEGORIES, WEIGHTS, Probe

PROBES = Path(__file__).resolve().parents[1] / "benchmarks" / "compat2" / "probes"
FILES = ("builtins", "surface", "modules", "language")
FIELDS = {f.name for f in dataclasses.fields(Probe)}


def rows() -> list[dict]:
    out = []
    for name in FILES:
        out += [json.loads(line) for line in (PROBES / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()]
    return out


def test_rows_match_the_probe_contract() -> None:
    for row in rows():
        assert set(row) == FIELDS
        probe = Probe(**{**row, "args": tuple(row["args"])})
        assert probe.category in CATEGORIES
        assert probe.kind in {"value", "raises", "mention"}
        assert isinstance(row["args"], list)
        assert (probe.kind == "raises") == bool(probe.expect_exc)
        assert probe.weight == WEIGHTS[probe.category]
        ast.parse(probe.source)


def test_ids_are_unique_and_files_sorted() -> None:
    seen: set[str] = set()
    for name in FILES:
        ids = [json.loads(line)["id"] for line in (PROBES / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()]
        assert ids == sorted(ids)
        assert not seen & set(ids)
        seen |= set(ids)


def test_manifest_counts_match_the_files() -> None:
    manifest = json.loads((PROBES / "MANIFEST.json").read_text(encoding="utf-8"))
    data = rows()
    assert manifest["total"] == len(data)
    assert manifest["cpython_pin"] == "3.14"
    counts = Counter((r["area"], r["category"]) for r in data)
    for area, cats in manifest["areas"].items():
        for cat, n in cats.items():
            assert counts[(area, cat)] == n
    assert sum(1 for r in data if r["kind"] == "mention") == sum(manifest["mention_reasons"].values())


def test_every_builtin_name_and_stdlib_module_has_a_probe() -> None:
    denominators = json.loads((PROBES.parents[1] / "compat" / "denominators.json").read_text(encoding="utf-8"))
    ids = {r["id"] for r in rows()}
    for module in denominators["stdlib_modules"]:
        assert f"stdlib.{module}" in ids
    for name in denominators["builtins"]:
        assert f"builtins.{name}" in ids


def test_vendored_cpython_tests_match_the_pin() -> None:
    pin = json.loads((PROBES / "cpython_tests" / "PIN.json").read_text(encoding="utf-8"))
    assert pin["tag"] == "v3.14.8"
    for name, digest in pin["files"].items():
        assert hashlib.sha256((PROBES / "cpython_tests" / name).read_bytes()).hexdigest() == digest


def test_language_probes_cover_every_applicable_ast_node() -> None:
    manifest = json.loads((PROBES / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["language"]["nodes_uncovered"] == []


@pytest.mark.parametrize("pid", ["builtins.len", "builtins.str.upper", "stdlib.math.sqrt"])
def test_known_probes_are_value_probes(pid: str) -> None:
    by_id = {r["id"]: r for r in rows()}
    assert by_id[pid]["kind"] == "value"


def test_v1_equivalent_probes_are_continuity_only() -> None:
    lines = (PROBES / "v1equiv.jsonl").read_text(encoding="utf-8").splitlines()
    data = [json.loads(line) for line in lines]
    assert data
    assert all(r["id"].endswith(".v1") and r["weight"] == 0.0 and r["kind"] == "value" for r in data)
    manifest = json.loads((PROBES / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["v1_equivalent"]["rows"] == len(data)
