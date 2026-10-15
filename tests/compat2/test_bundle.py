from __future__ import annotations

import shutil

import pytest
from benchmarks.compat2 import bundle
from benchmarks.compat2.outcomes import Probe
from benchmarks.compat2.score import evaluate

OK = "def probe() -> int:\n    return 1\n"
CSV = "import csv\n\ndef probe() -> int:\n    r: csv.nonexist = 1\n    return 1\n"
ADD = "def probe(a: int, b: int) -> int:\n    return a + b\n"


def make(pid: str, source: str = OK, **kw: object) -> Probe:
    fields = {"area": "builtins", "category": "function", "kind": "value", **kw}
    return Probe(id=pid, source=source, **fields)  # type: ignore[arg-type]


def test_plan_groups_by_import_set_and_caps_the_size() -> None:
    probes = [make(f"a{i}") for i in range(120)] + [make(f"c{i}", CSV) for i in range(3)]
    units = bundle.plan(probes)
    assert sorted(len(u) for u in units) == [3, 20, 50, 50]
    assert all(len({bundle.parse(p).imports for p in u}) == 1 for u in units)  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "probe",
    [
        make("raises", kind="raises", expect_exc="X"),
        make("helper", "X = 1\n" + OK),
        make("returns-bool", "def probe() -> bool:\n    return True\n"),
        make("recursive", "def probe() -> int:\n    return probe()\n"),
        make("tuple-arg", ADD, args=((1, 2), 3)),
        make("program", OK, call=""),
        make("nan", ADD, args=(float("nan"), 1)),
    ],
)
def test_unbundleable_probes_run_solo(probe: Probe) -> None:
    assert bundle.parse(probe) is None
    assert bundle.plan([probe]) == [[probe]]


def test_source_has_one_function_per_probe_and_a_mask() -> None:
    probes = [make("a", ADD, args=(1, 2)), make("b", ADD, args=(3, -4))]
    text = bundle.source_of(probes, {"a": 3, "b": -1})
    assert "def probe_0(a: int, b: int) -> int:" in text and "def probe_1(" in text
    assert "if probe_0(1, 2) == 3:" in text and "mask = mask + 2" in text and "probe_1(3, -4) == -1" in text


@pytest.mark.skipif(shutil.which("clang") is None, reason="needs the native toolchain")
def test_bundle_matches_solo_outcomes() -> None:
    probes = [
        make("ok", ADD, args=(1, 2)),
        make("also-ok"),
        make("fenced", "def probe() -> int:\n    import csv\n    r: csv.nonexist = 1\n    return 1\n"),
    ]
    solo = {p.id: evaluate(p, 120.0).row for p in probes}
    together = {p.id: r.row for p, r in zip(probes, bundle.evaluate_group(probes, 120.0, evaluate), strict=True)}
    assert {k: (v.outcome, v.code) for k, v in together.items()} == {k: (v.outcome, v.code) for k, v in solo.items()}
    assert solo["ok"].outcome == "pass" and solo["fenced"].outcome == "fence"
