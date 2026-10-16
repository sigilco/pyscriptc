from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from benchmarks.compat2 import cache
from benchmarks.compat2 import score as score_module
from benchmarks.compat2.loader import dump_probes
from benchmarks.compat2.outcomes import Probe, Row
from benchmarks.compat2.score import Result, counts_by_version, merge_results, resolve_oracle, score

OK = "def probe() -> int:\n    return 1\n"


def probe(pid: str, versions: tuple[str, ...] = ()) -> Probe:
    return Probe(id=pid, area="builtins", category="function", kind="value", source=OK, versions=versions)


def fake_oracle_dir(tmp_path: Path) -> Path:
    exe = tmp_path / "cpython-3.12.999-linux-x86_64-gnu" / "bin" / "python3"
    exe.parent.mkdir(parents=True)
    exe.symlink_to(sys.executable)
    return tmp_path


def test_resolve_oracle_defaults_to_running_interpreter() -> None:
    assert resolve_oracle(None) is None


def test_resolve_oracle_finds_pinned_interpreter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYSC_ORACLE_DIR", str(fake_oracle_dir(tmp_path)))
    executable, version = resolve_oracle("3.12")
    assert "cpython-3.12.999" in executable
    assert version == (
        f"{cache.SCORER_VERSION}/cpython-{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}"
    )


def test_resolve_oracle_errors_without_interpreter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYSC_ORACLE_DIR", str(tmp_path))
    with pytest.raises(SystemExit, match="no CPython 3.12 interpreter"):
        resolve_oracle("3.12")


def test_cache_key_follows_passed_oracle() -> None:
    hashes = cache.FileHashes()
    default = cache.cache_key(probe("a"), {}, hashes)
    assert cache.cache_key(probe("a"), {}, hashes, "2/cpython-3.12.15") != default
    assert cache.cache_key(probe("a"), {}, hashes, cache.oracle_version()) == default


def test_score_threads_oracle_to_evaluator_and_keys(tmp_path: Path) -> None:
    seen: list[str | None] = []

    def run(p: Probe, timeout: float, oracle: str | None = None) -> Result:
        seen.append(oracle)
        return Result(Row(id=p.id, area=p.area, category=p.category, outcome="pass"), 1.0)

    oracle = ("/pinned/bin/python3", "2/cpython-3.12.15")
    first = score([probe("a")], cache_dir=tmp_path, evaluator=run, oracle=oracle)
    second = score([probe("a")], cache_dir=tmp_path, evaluator=run, oracle=oracle)
    assert seen == ["/pinned/bin/python3"]
    assert first["oracle"] == "2/cpython-3.12.15" and first["cache_hits"] == 0 and second["cache_hits"] == 1

    # the same probe under a different oracle version misses the cache
    score([probe("a")], cache_dir=tmp_path, evaluator=run, oracle=("/other/bin/python3", "2/cpython-3.13.9"))
    assert seen == ["/pinned/bin/python3", "/other/bin/python3"]


def test_counts_by_version_columns() -> None:
    probes = [probe("a", ("3.12", "3.13")), probe("b", ("3.12",)), probe("c")]
    rows = [
        Row(id=p.id, area=p.area, category=p.category, outcome=outcome)
        for p, outcome in zip(probes, ["pass", "fence", "pass"], strict=True)
    ]
    assert counts_by_version(probes, rows) == {
        "3.12": {"probes": 2, "counts": {"pass": 1, "fence": 1}},
        "3.13": {"probes": 1, "counts": {"pass": 1}},
    }


def test_score_results_carry_version_columns() -> None:
    probes = [probe("a", ("3.12",)), probe("b", ("3.12", "3.13"))]

    def run(p: Probe, timeout: float) -> Result:
        return Result(Row(id=p.id, area=p.area, category=p.category, outcome="pass"), 1.0)

    results = score(probes, cache_dir=None, evaluator=run)
    assert results["versions"] == {
        "3.12": {"probes": 2, "counts": {"pass": 2}},
        "3.13": {"probes": 1, "counts": {"pass": 1}},
    }
    assert results["counts"] == {"pass": 2}
    assert results["oracle"] == cache.oracle_version()


def _part(pid: str, outcome: str, versions: dict[str, Any], oracle: str = "2/cpython-3.12.15") -> dict[str, Any]:
    return {
        "compiler_hash": "h",
        "oracle": oracle,
        "wall_s": 1,
        "compile_s": 1,
        "cache_hits": 0,
        "weights": {},
        "rows": [Row(id=pid, area="builtins", category="function", outcome=outcome).to_json()],
        "counts": {outcome: 1},
        "versions": versions,
    }


def test_merge_results_adds_version_columns() -> None:
    merged = merge_results(
        [
            _part("a", "pass", {"3.12": {"probes": 1, "counts": {"pass": 1}}}),
            _part("b", "fence", {"3.12": {"probes": 1, "counts": {"fence": 1}}}),
        ]
    )
    assert merged["versions"] == {"3.12": {"probes": 2, "counts": {"pass": 1, "fence": 1}}}
    assert merged["oracle"] == "2/cpython-3.12.15"


def test_merge_results_rejects_mixed_oracles() -> None:
    with pytest.raises(ValueError, match="different oracles"):
        merge_results([_part("a", "pass", {}), _part("b", "pass", {}, oracle="2/cpython-3.13.9")])


def test_cli_threads_target_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str] | None] = []

    def fake_score(probes: list[Probe], **kw: Any) -> dict[str, Any]:
        seen.append(kw.get("oracle"))
        return {
            "compiler_hash": "h",
            "oracle": "2/cpython-3.15.0",
            "wall_s": 0,
            "compile_s": 0,
            "cache_hits": 0,
            "weights": {},
            "rows": [],
            "counts": {},
            "versions": {},
        }

    monkeypatch.setenv("PYSC_ORACLE_DIR", str(fake_oracle_dir(tmp_path)))
    monkeypatch.setattr(score_module, "score", fake_score)
    dump_probes(tmp_path / "p.jsonl", [probe("builtins.abs")])
    assert (
        score_module.main([str(tmp_path / "p.jsonl"), "--out", str(tmp_path / "r.json"), "--target-version", "3.12"])
        == 0
    )
    assert seen[0] is not None and "cpython-3.12.999" in seen[0][0]
    assert seen[0][1].startswith(f"{cache.SCORER_VERSION}/cpython-")


def test_cli_without_target_uses_running_interpreter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str] | None] = []

    def fake_score(probes: list[Probe], **kw: Any) -> dict[str, Any]:
        seen.append(kw.get("oracle"))
        return {
            "compiler_hash": "h",
            "oracle": cache.oracle_version(),
            "wall_s": 0,
            "compile_s": 0,
            "cache_hits": 0,
            "weights": {},
            "rows": [],
            "counts": {},
            "versions": {},
        }

    monkeypatch.setattr(score_module, "score", fake_score)
    dump_probes(tmp_path / "p.jsonl", [probe("builtins.abs")])
    assert score_module.main([str(tmp_path / "p.jsonl"), "--out", str(tmp_path / "r.json")]) == 0
    assert seen == [None]
