from __future__ import annotations

import json
from pathlib import Path

import pytest
from benchmarks.compat2 import score as scorer
from benchmarks.compat2.loader import dump_probes
from benchmarks.compat2.outcomes import Probe

OK = "def probe() -> int:\n    return 1\n"


def part(ids: list[str], compiler: str = "h", index: int = 0, total: int = 2) -> dict:
    rows = [{"id": i, "area": "builtins", "category": "function", "outcome": "pass"} for i in ids]
    return {
        "compiler_hash": compiler,
        "wall_s": 2.0 + index,
        "compile_s": 1.0,
        "cache_hits": 1,
        "weights": {i: 1.0 for i in ids},
        "rows": rows,
        "counts": {"pass": len(ids)},
        "shard": {"index": index, "total": total},
    }


def test_merge_sums_and_orders() -> None:
    merged = scorer.merge_results([part(["b", "d"], index=1), part(["a", "c"])])
    assert [r["id"] for r in merged["rows"]] == ["a", "b", "c", "d"]
    assert merged["counts"] == {"pass": 4} and merged["wall_s"] == 3.0 and merged["cache_hits"] == 2
    assert merged["compile_s"] == 2.0 and set(merged["weights"]) == {"a", "b", "c", "d"}


def test_merge_rejects_mixed_compilers_and_duplicates() -> None:
    with pytest.raises(ValueError, match="different compilers"):
        scorer.merge_results([part(["a"]), part(["b"], compiler="x")])
    with pytest.raises(ValueError, match="two shards"):
        scorer.merge_results([part(["a"]), part(["a"], index=1)])


def test_shards_merge_by_themselves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    probes = [Probe(id=f"p{i}", area="builtins", category="function", kind="value", source=OK) for i in range(5)]
    dump_probes(tmp_path / "p.jsonl", probes)
    out = tmp_path / "r.json"
    base = [str(tmp_path / "p.jsonl"), "--out", str(out), "--no-cache"]

    def passes(p: Probe, timeout: float, oracle: str | None = None) -> scorer.Result:
        return scorer.Result(scorer.Row(id=p.id, area=p.area, category=p.category, outcome="pass"))

    real = scorer.score
    monkeypatch.setattr(scorer, "score", lambda probes, **kw: real(probes, **{**kw, "evaluator": passes}))
    assert scorer.main([*base, "--shard", "0/2"]) == 0
    assert not out.exists()
    assert scorer.main([*base, "--shard", "1/2"]) == 0
    merged = json.loads(out.read_text())
    assert [r["id"] for r in merged["rows"]] == [f"p{i}" for i in range(5)]
    assert merged["counts"] == {"pass": 5}
