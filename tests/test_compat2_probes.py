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
from benchmarks.compat2.version_surface import AXIS

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
    assert manifest["cpython_pin"] == "3.12"
    assert manifest["cpython"].startswith("3.12.")
    counts = Counter((r["area"], r["category"]) for r in data)
    for area, cats in manifest["areas"].items():
        for cat, n in cats.items():
            assert counts[(area, cat)] == n
    assert sum(1 for r in data if r["kind"] == "mention") == sum(manifest["mention_reasons"].values())


def test_every_builtin_name_and_stdlib_module_has_a_probe() -> None:
    denominators = json.loads((PROBES.parents[1] / "compat" / "denominators.json").read_text(encoding="utf-8"))
    manifest = json.loads((PROBES / "MANIFEST.json").read_text(encoding="utf-8"))
    absent = manifest["absent_in_oracle"]
    ids = {r["id"] for r in rows()}
    for module in denominators["stdlib_modules"]:
        assert (f"stdlib.{module}" in ids) != (module in absent["modules"])
    for name in denominators["builtins"]:
        assert (f"builtins.{name}" in ids) != (name in absent["builtins"])


@pytest.mark.parametrize(("subdir", "tag"), [("", "v3.12.15"), ("py314", "v3.14.8")])
def test_vendored_cpython_tests_match_the_pin(subdir: str, tag: str) -> None:
    folder = PROBES / "cpython_tests" / subdir
    pin = json.loads((folder / "PIN.json").read_text(encoding="utf-8"))
    assert pin["tag"] == tag
    for name, digest in pin["files"].items():
        assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == digest


def test_language_probes_cover_every_applicable_ast_node() -> None:
    manifest = json.loads((PROBES / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["language"]["nodes_uncovered"] == []


@pytest.mark.parametrize("pid", ["builtins.len", "builtins.str.upper", "stdlib.math.sqrt"])
def test_known_probes_are_value_probes(pid: str) -> None:
    by_id = {r["id"]: r for r in rows()}
    assert by_id[pid]["kind"] == "value"


def test_v1_equivalent_probes_are_continuity_only() -> None:
    lines = (PROBES.parent / "continuity" / "v1equiv.jsonl").read_text(encoding="utf-8").splitlines()
    data = [json.loads(line) for line in lines]
    assert data
    assert all(r["id"].endswith(".v1") and r["weight"] == 0.0 and r["kind"] == "value" for r in data)
    manifest = json.loads((PROBES / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["v1_equivalent"]["rows"] == len(data)


def test_loader_reads_the_probe_directory_without_continuity_rows() -> None:
    from benchmarks.compat2.loader import load_probes

    loaded = list(load_probes(PROBES))
    assert len(loaded) == len(rows())
    assert not any(p.id.endswith(".v1") for p in loaded)


def test_every_row_carries_a_sorted_subset_of_the_version_axis() -> None:
    data = rows()
    for row in data:
        assert row["versions"] == sorted(set(row["versions"]) & set(AXIS))
    manifest = json.loads((PROBES / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["versions"]["axis"] == list(AXIS)
    for area, counts in manifest["versions"]["by_area"].items():
        for version, n in counts.items():
            assert n == sum(1 for r in data if r["area"] == area and version in r["versions"])
    skipped = (
        set(manifest["versions"]["unverified_on_oracle"])
        | set(manifest["versions"]["seed_dependent"])
        | set(manifest["versions"]["process_dependent"])
    )
    for row in data:
        assert ("3.12" in row["versions"]) != (row["id"] in skipped)


def test_language_profile_tags_nodes_that_are_not_in_the_oracle() -> None:
    info = json.loads((PROBES / "MANIFEST.json").read_text(encoding="utf-8"))["language"]
    assert info["newer_nodes"]["TemplateStr"] == "3.14"
    assert info["min_version"]["TypeAlias"] == "3.12"
    assert "lang.TemplateStr" in info["absent_in_oracle"]
    assert not any(r["id"].startswith("lang.TemplateStr") for r in rows())


def test_typeshed_versions_file_filters_modules_per_target() -> None:
    from benchmarks.compat2.probes import typeshed

    assert typeshed.available("annotationlib", (3, 14))
    assert not typeshed.available("annotationlib", (3, 12))
    assert not typeshed.available("distutils", (3, 12))
    assert typeshed.available("distutils", (3, 11))
    assert not typeshed.available("importlib.resources.abc", (3, 10))  # a submodule entry gates the import
    assert typeshed.available("os.path", (3, 12))
    assert "annotationlib" not in typeshed.versioned_modules((3, 12))


def test_secondary_oracle_set_stays_out_of_the_probe_directory() -> None:
    from benchmarks.compat2.probes import cpython_adapter

    assert cpython_adapter.pinned_dir("3.12") == PROBES / "cpython_tests"
    assert cpython_adapter.pinned_dir("3.14") == PROBES / "cpython_tests" / "py314"
    secondary = PROBES.parent / "oracle-3.14" / "MANIFEST.json"
    assert json.loads(secondary.read_text(encoding="utf-8"))["cpython_pin"] == "3.14"
    assert not list(PROBES.rglob("oracle-3.14*"))
