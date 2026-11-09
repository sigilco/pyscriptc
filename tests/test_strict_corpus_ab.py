"""Strict corpus A/B lane: unstripped stdout bytes vs the pinned oracle (#527).

`scripts/strict_corpus_ab.py` is the report-only sibling of the whitespace-
stripped scorer comparison: every runnable probe of the visible corpus is
compiled on the direct lane, run natively and under the pinned CPython 3.14
oracle, and compared byte for byte. Unit rows monkeypatch compile and run;
the live test needs a C toolchain and the oracle and skips without them.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from benchmarks.compat2.outcomes import Probe
from scripts import strict_corpus_ab as ab
from scripts.runtime_ab import Observation, has_c_toolchain

ROOT = Path(__file__).resolve().parents[1]


def _probe(probe_id: str, **kw: object) -> Probe:
    fields: dict[str, object] = {
        "id": probe_id,
        "area": "scripts",
        "category": "program",
        "kind": "value",
        "source": "",
        "call": "",
        "args": (),
    }
    fields.update(kw)
    return Probe(**fields)  # type: ignore[arg-type]


def _built(returncode: int = 0, stderr: bytes = b"") -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess([], returncode, b"", stderr)


def _fake_compile(args: list[str], *a: object, **k: object) -> subprocess.CompletedProcess[bytes]:
    Path(args[args.index("-o") + 1]).write_bytes(b"\x7fELF")
    return _built()


def test_first_diff_reports_first_byte_and_length() -> None:
    assert ab._first_diff(b"abc", b"abd") == "stdout first differs at byte 2: native 0x63 cpython 0x64"
    note = ab._first_diff(b"ab", b"abcd")
    assert note.startswith("stdout length 2 != 4")
    assert "extra cpython bytes" in note
    note = ab._first_diff(b"abcd", b"ab")
    assert "extra native bytes" in note


def test_verdict_is_byte_strict() -> None:
    same = Observation(b"out\n", b"", 0)
    assert ab._verdict(same, Observation(b"out\n", b"", 0)) == (ab.PASS, "match")
    # Whitespace-stripped comparisons call this equal; the strict lane must not.
    verdict, note = ab._verdict(Observation(b"out \n", b"", 0), same)
    assert verdict == ab.DIFF
    assert "byte" in note
    verdict, note = ab._verdict(Observation(b"out\n", b"", 1), same)
    assert verdict == ab.DIFF
    assert "exit 0!=1" in note
    verdict, note = ab._verdict(Observation(b"out\n", b"ValueError: x\n", 1), Observation(b"out\n", b"", 1))
    assert verdict == ab.DIFF
    assert "stderr class" in note


def test_default_probes_cover_the_visible_corpus() -> None:
    probes = ab.default_probes()
    ids = {p.id for p in probes}
    assert "scripts.caesar_cipher" in ids
    assert "top100.click" in ids
    assert "top100.click.click.formatting.wrap_text" in ids
    # Same denominator as the scorer's --corpus selection: 20 + 39 + 47.
    assert len(probes) == 106


def test_package_program_reports_compile_only() -> None:
    probe = _probe("top100.click", area="top100", category="program")
    row = ab.compare_probe(probe, "unused-oracle", 1.0, 1.0)
    assert row.verdict == ab.SKIP
    assert "compile-only" in row.note


def _script_dir(tmp_path: Path) -> Path:
    d = tmp_path / "benchmarks" / "compat2" / "corpus" / "scripts" / "toy"
    d.mkdir(parents=True)
    (d / "main.py").write_text("print('hi')\n", encoding="utf-8")
    return d


def test_script_pass_when_bytes_match(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _script_dir(tmp_path)
    probe = _probe("scripts.toy")
    monkeypatch.setattr(ab, "ROOT", tmp_path)
    monkeypatch.setattr(ab, "_compile", _fake_compile)
    obs = Observation(b"hi\n", b"", 0)
    monkeypatch.setattr(ab, "_run_oracle", lambda *a, **k: obs)
    monkeypatch.setattr(ab, "_run_bin", lambda *a, **k: obs)

    row = ab.compare_script(probe, "oracle", 1.0, 1.0)
    assert row.verdict == ab.PASS
    assert row.native == obs and row.cpython == obs


def test_script_diff_when_bytes_differ(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _script_dir(tmp_path)
    probe = _probe("scripts.toy")
    monkeypatch.setattr(ab, "ROOT", tmp_path)
    monkeypatch.setattr(ab, "_compile", _fake_compile)
    monkeypatch.setattr(ab, "_run_oracle", lambda *a, **k: Observation(b"hi\n", b"", 0))
    # A trailing space the stripped comparison would hide.
    monkeypatch.setattr(ab, "_run_bin", lambda *a, **k: Observation(b"hi \n", b"", 0))

    row = ab.compare_script(probe, "oracle", 1.0, 1.0)
    assert row.verdict == ab.DIFF
    assert "stdout first differs" in row.note


def test_script_fence_reports_skip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _script_dir(tmp_path)
    probe = _probe("scripts.toy")
    monkeypatch.setattr(ab, "ROOT", tmp_path)
    monkeypatch.setattr(
        ab,
        "_compile",
        lambda *a, **k: _built(3, b"main.py:1:1: PS2003: print is lowered for str values\n"),
    )
    row = ab.compare_script(probe, "oracle", 1.0, 1.0)
    assert row.verdict == ab.SKIP
    assert "PS2003" in row.note


def test_function_strips_only_the_ok_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    probe = _probe("top100.pkg.pkg.mod.fn", area="top100", category="function", call="probe")
    monkeypatch.setattr(ab, "_oracle_raw", lambda *a, **k: Observation(b"ok 42\n", b"", 0))
    monkeypatch.setattr(ab, "_compile", _fake_compile)
    monkeypatch.setattr(ab, "_run_binary", lambda *a, **k: Observation(b"42\n", b"", 0))
    monkeypatch.setattr(ab, "tree_for", lambda p: None)
    monkeypatch.setattr(ab, "_tree_env", lambda t: None)

    row = ab.compare_function(probe, "oracle", 1.0, 1.0)
    assert row.verdict == ab.PASS

    monkeypatch.setattr(ab, "_run_binary", lambda *a, **k: Observation(b"42 \n", b"", 0))
    row = ab.compare_function(probe, "oracle", 1.0, 1.0)
    assert row.verdict == ab.DIFF


def test_function_skips_when_oracle_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    probe = _probe("top100.pkg.pkg.mod.fn", area="top100", category="function", call="probe")
    monkeypatch.setattr(ab, "_oracle_raw", lambda *a, **k: Observation(b"raise ValueError: x\n", b"", 0))
    monkeypatch.setattr(ab, "tree_for", lambda p: None)
    monkeypatch.setattr(ab, "_tree_env", lambda t: None)
    row = ab.compare_function(probe, "oracle", 1.0, 1.0)
    assert row.verdict == ab.SKIP
    assert "ok" in row.note


def test_main_writes_report_and_never_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ab, "has_c_toolchain", lambda: True)
    probe = _probe("scripts.toy")
    rows = [
        ab.StrictRow("scripts.toy", ab.DIFF, "stdout first differs at byte 0: native 0x61 cpython 0x62"),
        ab.StrictRow("top100.click", ab.SKIP, "compile-only"),
    ]
    monkeypatch.setattr(ab, "default_probes", lambda: [probe])
    monkeypatch.setattr(ab, "compare_probes", lambda *a, **k: rows)
    json_out = tmp_path / "strict.json"
    summary = tmp_path / "summary.md"
    code = ab.main(["--json-out", str(json_out), "--summary", str(summary)])
    assert code == 0
    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert payload["lane"] == "strict-corpus-ab"
    assert payload["rows"][0]["verdict"] == ab.DIFF
    assert "Strict corpus A/B" in summary.read_text(encoding="utf-8")


def test_main_errors_without_toolchain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ab, "has_c_toolchain", lambda: False)
    assert ab.main([]) == 2


def test_nightly_wires_strict_lane_as_report_only() -> None:
    workflow = (ROOT / ".github" / "workflows" / "nightly.yml").read_text(encoding="utf-8")
    assert "scripts/strict_corpus_ab.py" in workflow
    assert "--json-out nightly/strict-corpus-ab.json" in workflow
    assert '--summary "$GITHUB_STEP_SUMMARY"' in workflow
    assert "strict-corpus-ab:" in workflow
    assert "continue-on-error: true" in workflow
    # Report only: the failure-issue job does not wait on this lane.
    report = workflow.split("report-failure:")[-1]
    assert "needs: [visible-score, boxed-ledger, embed-acceptance]" in report


@pytest.mark.skipif(not has_c_toolchain(), reason="no C toolchain")
@pytest.mark.skipif(ab.resolve_oracle() is None, reason="no pinned CPython 3.14 oracle")
def test_live_script_roundtrip() -> None:
    oracle = ab.resolve_oracle()
    probe = next(p for p in ab.default_probes() if p.id == "scripts.caesar_cipher")
    row = ab.compare_script(probe, oracle, 120.0, 60.0)
    assert row.verdict == ab.PASS, row.note
