from __future__ import annotations

from pathlib import Path

from benchmarks.compat2 import coverage_map as cm
from benchmarks.compat2.outcomes import Probe

OK = "def probe() -> int:\n    return 1\n"
FENCED = "import csv\n\ndef probe() -> int:\n    r: csv.nonexist = 1\n    return 1\n"


def make(pid: str, area: str = "builtins", category: str = "function", source: str = OK) -> Probe:
    return Probe(id=pid, area=area, category=category, kind="value", source=source)  # type: ignore[arg-type]


def corpus() -> list[Probe]:
    probes = [make(f"b{i}") for i in range(300)]
    probes += [make(f"s{i}", "surface", "class") for i in range(300)]
    probes += [make(f"c{i}", "surface", "constant") for i in range(5)]
    probes += [make("lang", "language", "language")]
    return probes


def test_stratified_is_deterministic_and_covers_every_stratum() -> None:
    probes = corpus()
    first = cm.stratified(probes, 50)
    assert [p.id for p in first] == [p.id for p in cm.stratified(list(reversed(probes)), 50)]
    assert len(first) == 50
    assert {(p.area, p.category) for p in first} == {(p.area, p.category) for p in probes}


def test_select_by_intersection_and_cap() -> None:
    probes = corpus()
    data = {"probes": {p.id: {"touches": ["lower.py"] if p.area == "builtins" else ["stdmore.py"]} for p in probes}}
    chosen, _ = cm.select(probes, data, {"stdmore.py"}, cap=100)
    assert len(chosen) == 100 and all(p.area != "builtins" for p in chosen)
    chosen, _ = cm.select(probes, data, {"lower.py"})
    assert len(chosen) == 300
    assert cm.select(probes, data, set())[0] == []


def test_unmapped_probes_are_always_selected() -> None:
    probes = [make("known"), make("new")]
    data = {"probes": {"known": {"touches": ["ir.py"]}}}
    assert [p.id for p in cm.select(probes, data, {"stdmore.py"})[0]] == ["new"]


def test_shared_core_takes_the_fixed_sample() -> None:
    probes = corpus()
    chosen, why = cm.select(probes, {"probes": {}}, {"llvm.py"})
    assert len(chosen) == cm.SHARED_SAMPLE and "shared core" in why
    assert chosen == cm.select(probes, None, {"runtime/ps_rt.c"})[0]


def test_build_records_modules_and_reuses_entries(tmp_path: Path) -> None:
    probes = [make("ok"), make("fenced", source=FENCED)]
    path = tmp_path / "map.json"
    data = cm.build(probes, path)
    ok, fenced = data["probes"]["ok"], data["probes"]["fenced"]
    assert "lower/__init__.py" in ok["touches"] and "emit/llvm/_driver.py" in ok["touches"]
    assert "emit/llvm/_driver.py" not in fenced["touches"]  # a fenced probe never reaches the emitter
    assert not any(f.startswith("runtime/") for f in fenced["key"])  # and never reaches clang
    assert "runtime/ps_rt.c" in ok["key"] and "cli.py" in fenced["key"]
    assert cm.load(path) == data
    assert cm.key_map(data)["ok"] == ok["key"]
    assert cm.build(probes, path) == data  # unchanged probes are not traced again


def test_key_map_drops_entries_for_changed_probes() -> None:
    from benchmarks.compat2.cache import probe_hash

    a, b = make("a"), make("b")
    data = {"probes": {"a": {"hash": probe_hash(a), "key": ["ir.py"]}, "b": {"hash": "old", "key": ["ir.py"]}}}
    assert cm.key_map(data, [a, b]) == {"a": ["ir.py"]}


def test_tier0_cli_runs_the_selection(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    import json

    from benchmarks.compat2.loader import dump_probes
    from benchmarks.compat2.score import main

    dump_probes(tmp_path / "p.jsonl", [make("a"), make("b")])
    seen: list[str] = []
    monkeypatch.setattr(cm, "tier0", lambda probes, base: (seen.extend(p.id for p in probes) or probes[:1], "stub"))
    out = tmp_path / "r.json"
    assert main([str(tmp_path / "p.jsonl"), "--out", str(out), "--tier", "0", "--no-cache"]) == 0
    assert seen == ["a", "b"]
    assert len(json.loads(out.read_text())["rows"]) == 1
