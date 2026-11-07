"""Receipt-3: MIR emit-lane flip receipt driver (handoff 10, epic #817).

The driver runs `benchmarks.compat2.score` twice (PYSC_MIR=0/1) with
`--no-cache` — the cache key does not include the lane — and feeds the two
result files through `scripts.compat_no_flip`. These tests never run the
real scorer; `run_lane` is stubbed with fixture score JSON.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from scripts.compat_mir_flip import (
    LANES,
    ROOT,
    SCRIPT,
    build_parser,
    check_passthrough,
    lane_env,
    main,
    result_path,
    run_lane,
    score_command,
    split_passthrough,
)


def _score(rows: list[tuple[str, str]]) -> dict:
    return {
        "oracle": "3.14.9",
        "rows": [
            {"id": probe_id, "outcome": outcome, "area": "language", "category": "language"}
            for probe_id, outcome in rows
        ],
        "counts": {},
    }


def test_lane_env_pins_mir_and_hashseed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYSC_MIR", "1")
    direct = lane_env(False)
    mir = lane_env(True)
    assert direct["PYSC_MIR"] == "0"
    assert mir["PYSC_MIR"] == "1"
    assert direct["PYTHONHASHSEED"] == "0"
    assert mir["PYTHONHASHSEED"] == "0"
    assert str(ROOT / "src") in direct["PYTHONPATH"].split(os.pathsep)


def test_score_command_always_bypasses_cache() -> None:
    cmd = score_command("benchmarks/compat2/probes", Path("/tmp/out.json"), ["--shard", "0/4"], corpus=True)
    assert "--no-cache" in cmd
    assert cmd[cmd.index("--out") + 1] == "/tmp/out.json"
    assert "--corpus" in cmd
    assert cmd[-2:] == ["--shard", "0/4"]
    assert cmd[1:3] == ["-m", "benchmarks.compat2.score"]


def test_score_command_without_corpus() -> None:
    cmd = score_command("probes/x.jsonl", Path("o.json"), [], corpus=False)
    assert "--corpus" not in cmd
    assert "--no-cache" in cmd


def test_result_path_follows_score_shard_output(tmp_path: Path) -> None:
    out = tmp_path / "direct.json"
    assert result_path(out, []) == out
    assert result_path(out, ["--shard", "0/1"]) == out
    assert result_path(out, ["--shard", "0/4"]) == tmp_path / "direct.shard0of4.json"
    assert result_path(out, ["--shard=2/4"]) == tmp_path / "direct.shard2of4.json"
    assert result_path(out, ["--shard", "1/4", "--shard", "3/8"]) == tmp_path / "direct.shard3of8.json"


def test_run_lane_reads_the_shard_file_score_writes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    out = tmp_path / "direct.json"
    expected = result_path(out, ["--shard", "0/4"])

    def fake_run(cmd, **kwargs):
        expected.write_text(json.dumps(_score([("p1", "pass")])) + "\n", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("scripts.compat_mir_flip.subprocess.run", fake_run)
    result = run_lane("direct", False, "probes", out, ["--shard", "0/4"], False)
    assert result == expected


def test_check_passthrough_blocks_holdout_and_managed_flags() -> None:
    for bad in ("--holdout", "--headroom", "--out", "--out=x.json", "--corpus"):
        with pytest.raises(ValueError, match="not allowed"):
            check_passthrough([bad])
    check_passthrough(["--shard", "0/4", "--tier", "0", "--timeout", "30"])


def test_split_passthrough_at_double_dash() -> None:
    tool, extra = split_passthrough(["--no-corpus", "--", "--shard", "0/4", "--workers", "8"])
    assert tool == ["--no-corpus"]
    assert extra == ["--shard", "0/4", "--workers", "8"]
    assert split_passthrough(["--json"]) == (["--json"], [])


def test_parser_defaults_and_probes_positional() -> None:
    parser = build_parser()
    args = parser.parse_args([])
    assert args.probes == "benchmarks/compat2/probes"
    assert args.no_corpus is False
    args = parser.parse_args(["probes/x.jsonl", "--no-corpus"])
    assert args.probes == "probes/x.jsonl"
    assert args.no_corpus is True


def _stub_lanes(monkeypatch: pytest.MonkeyPatch, direct_rows, mir_rows, calls: list) -> None:
    def fake_run_lane(name, mir, probes, out, extra, corpus):
        calls.append((name, mir, probes, str(out), tuple(extra), corpus))
        result = result_path(out, list(extra))
        payload = _score(direct_rows if name == "direct" else mir_rows)
        result.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        return result

    monkeypatch.setattr("scripts.compat_mir_flip.run_lane", fake_run_lane)


def test_main_runs_both_lanes_and_reports_flips(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list = []
    _stub_lanes(
        monkeypatch,
        [("p1", "pass"), ("p2", "fence")],
        [("p1", "fence"), ("p2", "pass")],
        calls,
    )
    code = main(["--no-corpus", "--", "--shard", "0/4"])
    assert code == 0
    assert [call[0] for call in calls] == ["direct", "mir"]
    assert calls[0][1] is False and calls[1][1] is True
    assert all(call[5] is False for call in calls)  # --no-corpus reached run_lane
    assert all(call[4] == ("--shard", "0/4") for call in calls)
    out = capsys.readouterr().out
    assert "outcome flips in either direction" in out
    assert "A to B: 2" in out


def test_main_json_and_out_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub_lanes(monkeypatch, [("x", "pass")], [("x", "pass")], [])
    json_out = tmp_path / "report.json"
    md_out = tmp_path / "report.md"
    code = main(["--json", "--json-out", str(json_out), "--md-out", str(md_out)])
    assert code == 0
    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert payload["flips_either_direction"] == 0
    assert "Compat outcome flips" in md_out.read_text(encoding="utf-8")
    assert "flips_either_direction" in capsys.readouterr().out


def test_main_zero_exit_despite_flips(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_lanes(monkeypatch, [("x", "pass")], [("x", "crash")], [])
    assert main([]) == 0


def test_main_nonzero_on_scorer_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(name, mir, probes, out, extra, corpus):
        raise RuntimeError("mir lane scorer failed (exit 1): boom")

    monkeypatch.setattr("scripts.compat_mir_flip.run_lane", boom)
    assert main([]) == 2


def test_main_rejects_holdout_passthrough(capsys: pytest.CaptureFixture[str]) -> None:
    # after `--` it reaches check_passthrough; before `--` argparse rejects it
    assert main(["--", "--holdout"]) == 2
    assert "not allowed" in capsys.readouterr().err
    assert main(["--holdout"]) == 2


def test_cli_help() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "PYSC_MIR" in proc.stdout


def test_lanes_cover_both_sides() -> None:
    assert LANES == (("direct", False), ("mir", True))
