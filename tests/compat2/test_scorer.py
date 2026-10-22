from __future__ import annotations

import json
from pathlib import Path

import pytest
from benchmarks.compat2 import headroom, ledger, verdict
from benchmarks.compat2.loader import dump_probes, load_probes
from benchmarks.compat2.outcomes import Probe, Row, probe_weight
from benchmarks.compat2.score import Result, evaluate, score, weighted_pass

OK = "def probe() -> int:\n    return 1\n"


def probe(pid: str, kind: str = "value", category: str = "function", **kw: object) -> Probe:
    return Probe(id=pid, area="builtins", category=category, kind=kind, source=OK, **kw)  # type: ignore[arg-type]


def fake(outcomes: dict[str, str]):
    def run(p: Probe, timeout: float) -> Result:
        return Result(Row(id=p.id, area=p.area, category=p.category, outcome=outcomes[p.id]), 1.0)  # type: ignore[arg-type]

    return run


def results(rows: dict[str, str], compile_s: float = 10.0, cats: dict[str, str] | None = None) -> dict:
    cats = cats or {}
    probes = [probe(i, category=cats.get(i, "function")) for i in rows]
    return score(probes, cache_dir=None, evaluator=fake(rows)) | {"compile_s": compile_s}


def test_loader_roundtrip(tmp_path: Path) -> None:
    probes = [probe("builtins.abs", args=(1, "a")), probe("builtins.len", kind="raises", expect_exc="TypeError")]
    dump_probes(tmp_path / "a.jsonl", probes)
    assert list(load_probes(tmp_path)) == probes
    assert list(load_probes(tmp_path / "a.jsonl")) == probes


def test_loader_reports_bad_line(tmp_path: Path) -> None:
    (tmp_path / "x.jsonl").write_text('{"nope": 1}\n')
    with pytest.raises(ValueError, match="x.jsonl:1"):
        list(load_probes(tmp_path))


def test_headroom_flags_mention() -> None:
    bad = headroom.capped([probe("a"), probe("b", kind="mention"), probe("c", kind="raises")])
    assert [r.id for r in bad] == ["c"]
    row = headroom.classify(probe("b", kind="mention"))
    assert row.outcome == "excluded" and row.reachable and row.evidence == "mention: weight 0 (backlog)"


def test_cli_refuses_capped(tmp_path: Path) -> None:
    from benchmarks.compat2.score import main

    dump_probes(tmp_path / "p.jsonl", [probe("m", kind="raises")])
    assert main([str(tmp_path / "p.jsonl"), "--out", str(tmp_path / "r.json")]) == 2


def test_cache_skips_second_run(tmp_path: Path) -> None:
    calls = []

    def run(p: Probe, timeout: float) -> Result:
        calls.append(p.id)
        return fake({"a": "pass"})(p, timeout)

    first = score([probe("a")], cache_dir=tmp_path, evaluator=run)
    second = score([probe("a")], cache_dir=tmp_path, evaluator=run)
    assert calls == ["a"] and first["cache_hits"] == 0 and second["cache_hits"] == 1


def test_weighted_pass_uses_category_weight() -> None:
    r = results({"a": "pass", "b": "pass", "c": "fence"}, cats={"b": "constant", "c": "program"})
    assert weighted_pass(r) == pytest.approx(1.1)


def test_soundness_gate_fails() -> None:
    base = results({"a": "fence", "b": "pass"})
    cand = results({"a": "wrong-output", "b": "pass"})
    out = verdict.verdict(base, cand, skip_perf=True)
    assert out["verdict"] == "FAIL"
    assert out["findings"][0]["severity"] == "P0" and out["findings"][0]["area"] == "soundness:a"


def test_pass_ratchet_fails_even_with_gain() -> None:
    base = results({"a": "pass", "b": "fence"})
    cand = results({"a": "fence", "b": "pass"})
    out = verdict.verdict(base, cand, skip_perf=True)
    assert out["verdict"] == "FAIL" and "pass-ratchet:a" in {f["area"] for f in out["findings"]}


def test_keep_on_primary_gain() -> None:
    base = results({"a": "fence"})
    cand = results({"a": "pass"})
    assert verdict.verdict(base, cand, skip_perf=True)["verdict"] == "PASS"


def test_census_progress_keeps_flat_primary() -> None:
    base = results({"a": "fence"})
    before = {"fences": [{"id": "x", "blocks": 3}]}
    after = {"fences": [{"id": "x", "blocks": 1}]}
    out = verdict.verdict(base, base, census_before=before, census_after=after, skip_perf=True)
    assert out["verdict"] == "PASS" and out["census_delta"] == 2


def test_flat_drops() -> None:
    base = results({"a": "fence"})
    assert verdict.verdict(base, base, skip_perf=True)["findings"][0]["area"] == "progress"


def test_compile_budget() -> None:
    base = results({"a": "fence"}, compile_s=10)
    cand = results({"a": "pass"}, compile_s=20)
    assert verdict.verdict(base, cand, skip_perf=True)["findings"][0]["area"] == "compile-time"


def test_oscillation_terminates(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    good = results({"a": "pass"})
    bad = results({"a": "fence"})
    verdict.verdict(good, bad, ledger=path, skip_perf=True)  # opens pass-ratchet:a
    verdict.verdict(good, good, ledger=path, skip_perf=True)  # closes it
    out = verdict.verdict(good, bad, ledger=path, skip_perf=True)  # reopens
    assert "oscillation:pass-ratchet:a" in {f["area"] for f in out["findings"]}
    assert len(json.loads(path.read_text())) == 3


def test_support_ledger(tmp_path: Path) -> None:
    rows = [
        Row(id="stdlib.csv.reader", area="surface", category="function", outcome="fence", code="PS1001"),
        Row(id="stdlib.csv.QUOTE_ALL", area="surface", category="constant", outcome="pass"),
        Row(id="builtins.map", area="builtins", category="function", outcome="pass"),
    ]
    ledger.write(rows, tmp_path)
    data = json.loads((tmp_path / "support-ledger.json").read_text())
    csv = data["modules"]["csv"]["function"]
    assert csv["percent"] == 0.0 and csv["backlog"][0]["id"] == "stdlib.csv.reader"
    assert data["modules"]["builtins"]["function"]["percent"] == 100.0
    assert "| csv | function | 0 | 1 | 0.0 | 1 |" in (tmp_path / "support-ledger.md").read_text()


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_injected_wrong_output_is_soundness_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    """The oracle is forced to disagree with the native result, as a miscompile would."""

    from benchmarks.compat2 import score as score_mod

    floordiv = Probe(
        id="inject.floordiv",
        area="language",
        category="language",
        kind="value",
        source="def probe() -> int:\n    return (-3) // 2\n",
    )
    honest = evaluate(floordiv, 120.0)
    assert honest.row.outcome == "pass", honest.row
    monkeypatch.setattr(score_mod, "_cpython", lambda p, t, env=None: ("ok", "-1"))  # truncating division
    broken = evaluate(floordiv, 120.0)
    assert broken.row.outcome == "wrong-output"
    base = score([floordiv], cache_dir=None, evaluator=fake({floordiv.id: "pass"}))
    cand = score([floordiv], cache_dir=None, evaluator=lambda p, t: broken)
    out = verdict.verdict(base, cand, skip_perf=True)
    assert out["verdict"] == "FAIL" and out["findings"][0]["area"] == "soundness:inject.floordiv"


def test_probe_weight_follows_the_primary_metric() -> None:
    for category in ("function", "class", "method", "exception", "module", "language"):
        assert probe_weight(category) == 1.0
    assert probe_weight("constant") == 0.1
    assert probe_weight("program", area="scripts") == 3.0
    assert probe_weight("function", area="top100") == 2.0
    assert probe_weight("program", area="top100") == 5.0
    for fold in ("handle", "recv-handle", "exec", "none", "shape"):
        assert probe_weight("function", fold=fold) == 0.25
        assert probe_weight("language", fold=fold) == 0.25
    assert probe_weight("function", fold="int") == 1.0
    assert probe_weight("function", fold="len") == 1.0
    assert probe_weight("function", fold="recv-len") == 1.0
    assert probe_weight("constant", fold="handle") == 0.1
    assert probe_weight("function", kind="raises") == 0.0
    assert probe_weight("exception", kind="raises", fold="raises") == 0.0
    assert probe_weight("method", kind="mention") == 0.0
    assert probe_weight("constant", kind="mention") == 0.0
    assert probe_weight("function", kind="raises", area="top100") == 0.0
    assert probe_weight("function", kind="mention", area="scripts") == 0.0


def test_effective_weight_does_not_double_count() -> None:
    from benchmarks.compat2.score import effective_weight

    assert effective_weight(probe("a", category="constant")) == 0.1
    prog = Probe(
        id="p",
        area="scripts",
        category="program",
        kind="value",
        source="",
        call="",
        weight=probe_weight("program", area="scripts"),
    )
    assert effective_weight(prog) == 3.0
    package = Probe(
        id="top100.pkg",
        area="top100",
        category="program",
        kind="value",
        source="",
        call="",
        weight=probe_weight("program", area="top100"),
    )
    assert effective_weight(package) == 5.0


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_program_probe_fences_or_matches() -> None:
    prog = Probe(
        id="scripts.tiny",
        area="scripts",
        category="program",
        kind="value",
        source="print('hi')\n",
        call="",
        args=("hi\n",),
        weight=probe_weight("program", area="scripts"),
    )
    assert evaluate(prog, 120.0).row.outcome in {"pass", "fence"}


def test_holdout_closed_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from benchmarks.compat2 import score as score_mod
    from benchmarks.compat2.corpus import load as corpus

    seen: list[bool] = []
    real = corpus.package_pins
    monkeypatch.setattr(corpus, "package_pins", lambda holdout=False: seen.append(holdout) or real(holdout))
    p = Probe(id="top100.nonexistent.f", area="top100", category="function", kind="value", source=OK)
    assert score_mod.tree_for(p) is None
    assert seen == [False]


def test_corpus_headroom_cli(capsys: pytest.CaptureFixture[str]) -> None:
    from benchmarks.compat2.score import main

    assert main(["--corpus", "--headroom"]) == 0


def test_corrupt_cache_entry_is_recomputed(tmp_path: Path) -> None:
    run = fake({"a": "pass"})
    score([probe("a")], cache_dir=tmp_path, evaluator=run)
    for entry in tmp_path.glob("*.json"):
        entry.write_text("")
    again = score([probe("a")], cache_dir=tmp_path, evaluator=run)
    assert again["cache_hits"] == 0 and again["counts"] == {"pass": 1}


def test_holdout_cli_is_closed_without_the_evaluator_flag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from benchmarks.compat2.score import main

    monkeypatch.delenv("PYSCRIPTC_EVALUATOR", raising=False)
    assert main(["--holdout", "--out", "/nonexistent/never-written.json"]) == 2
    assert "the holdout is closed" in capsys.readouterr().err
