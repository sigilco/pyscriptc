"""irscore tests: synthetic probes only. The real corpus is the orchestrator's post-integration run."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from benchmarks.compat2 import irscore  # noqa: I001 - first-party after third-party is ruff's order

ROOT = Path(__file__).resolve().parents[1]


def probe_json(pid: str, ret: int = 1) -> str:
    source = f"def probe() -> int:\n    return {ret}\n"
    return (
        json.dumps(
            {
                "area": "builtins",
                "args": [],
                "call": "probe",
                "category": "function",
                "id": pid,
                "kind": "value",
                "source": source,
                "weight": 1.0,
            },
            sort_keys=True,
        )
        + "\n"
    )


def open_probe_json(pid: str = "lang.open") -> str:
    """A probe whose lowering pulls the synthetic TextIO/BytesIO classes (the set-order leak)."""

    import json as _json

    return (
        _json.dumps(
            {
                "area": "language",
                "args": [],
                "call": "probe",
                "category": "language",
                "id": pid,
                "kind": "value",
                "source": "def probe() -> int:\n    f = open('x.txt')\n    return 1\n",
                "weight": 1.0,
            },
            sort_keys=True,
        )
        + "\n"
    )


def write_probes(path: Path, lines: list[str]) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "p.jsonl").write_text("".join(lines), encoding="utf-8")
    return path


def base_probes(tmp_path: Path) -> Path:
    return write_probes(tmp_path / "probes", [probe_json("b.one", 1), probe_json("b.two", 2)])


def run_cli(probes: Path, *flags: str, seed: str | None = None) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": f"src{os.pathsep}{ROOT}"}
    if seed is not None:
        env["PYTHONHASHSEED"] = seed
    return subprocess.run(
        [sys.executable, "-m", "benchmarks.compat2.irscore", str(probes), *flags],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )


def test_generate_is_deterministic_across_runs(tmp_path: Path) -> None:
    probes = base_probes(tmp_path)
    one, two = tmp_path / "b1.json", tmp_path / "b2.json"
    assert run_cli(probes, "--out", str(one)).returncode == 0
    assert run_cli(probes, "--out", str(two)).returncode == 0
    assert one.read_bytes() == two.read_bytes()
    doc = json.loads(one.read_text(encoding="utf-8"))
    assert doc["version"] == 1 and doc["revision"] == 1 and doc["hash_algo"] == "sha256"
    assert doc["canonical_schema"] == "canonical-schema/2"
    assert set(doc["probes"]) == {"b.one", "b.two"}
    assert all(len(h) == 64 and h == h.lower() for h in doc["probes"].values())
    assert one.read_text(encoding="utf-8").endswith("\n")


def test_unchanged_probes_exit_zero(tmp_path: Path) -> None:
    probes = base_probes(tmp_path)
    baseline = tmp_path / "b.json"
    assert run_cli(probes, "--out", str(baseline)).returncode == 0
    done = run_cli(probes, "--baseline", str(baseline))
    assert done.returncode == 0, done.stdout + done.stderr
    assert "~0 changed (0 total)" in done.stdout


def test_add_remove_change_exits_one_with_sections(tmp_path: Path) -> None:
    probes = base_probes(tmp_path)
    baseline = tmp_path / "b.json"
    assert run_cli(probes, "--out", str(baseline)).returncode == 0
    moved = write_probes(
        tmp_path / "moved",
        [
            probe_json("a.new", 9),  # added
            probe_json("b.one", 111),  # changed
            # b.two is absent now: removed
        ],
    )
    done = run_cli(moved, "--baseline", str(baseline))
    assert done.returncode == 1
    assert "+1 added, -1 removed, ~1 changed (3 total)" in done.stdout
    added, removed, changed = (done.stdout.split(title, 1)[1] for title in ("ADDED", "REMOVED", "CHANGED"))
    assert "a.new" in added.split("REMOVED")[0]
    assert "b.two" in removed.split("CHANGED")[0]
    changed_line = next(line.strip() for line in changed.splitlines() if line.strip().startswith("b.one:"))
    old8, new8 = changed_line.split(": ")[1].split(" -> ")
    assert len(old8) == len(new8) == 8
    assert old8 != new8


def test_limit_generates_a_subset(tmp_path: Path) -> None:
    probes = write_probes(tmp_path / "probes", [probe_json(f"b.p{i:02d}") for i in range(5)])
    baseline = tmp_path / "b.json"
    assert run_cli(probes, "--out", str(baseline), "--limit", "3").returncode == 0
    doc = json.loads(baseline.read_text(encoding="utf-8"))
    assert set(doc["probes"]) == {"b.p00", "b.p01", "b.p02"}


def test_cap_at_20_with_more_line(tmp_path: Path) -> None:
    baseline = tmp_path / "b.json"
    old = write_probes(tmp_path / "old", [probe_json(f"o.p{i:02d}") for i in range(5)])
    assert run_cli(old, "--out", str(baseline)).returncode == 0
    new = write_probes(tmp_path / "new", [probe_json(f"n.p{i:02d}", 7) for i in range(25)])
    done = run_cli(new, "--baseline", str(baseline))
    assert done.returncode == 1
    assert "+25 added, -5 removed, ~0 changed (30 total)" in done.stdout
    added = done.stdout.split("ADDED", 1)[1].split("REMOVED", 1)[0]
    listed = [line for line in added.splitlines() if line.strip() and not line.strip().startswith("... and")]
    assert len(listed) == 20
    assert "... and 5 more" in done.stdout


def test_header_mismatch_exits_two(tmp_path: Path) -> None:
    probes = base_probes(tmp_path)
    baseline = tmp_path / "b.json"
    assert run_cli(probes, "--out", str(baseline)).returncode == 0
    doc = json.loads(baseline.read_text(encoding="utf-8"))
    doc["canonical_schema"] = "canonical-schema/0"
    baseline.write_text(json.dumps(doc), encoding="utf-8")
    done = run_cli(probes, "--baseline", str(baseline))
    assert done.returncode == 2
    assert "canonical_schema" in done.stderr and "regenerate" in done.stderr


def test_oracle_mismatch_exits_two(tmp_path: Path) -> None:
    probes = base_probes(tmp_path)
    baseline = tmp_path / "b.json"
    assert run_cli(probes, "--out", str(baseline)).returncode == 0
    doc = json.loads(baseline.read_text(encoding="utf-8"))
    doc["oracle"] = "999/cpython-0.0.0"
    baseline.write_text(json.dumps(doc), encoding="utf-8")
    done = run_cli(probes, "--baseline", str(baseline))
    assert done.returncode == 2
    assert "oracle" in done.stderr and "regenerate" in done.stderr


def test_seed_invariance_of_the_whole_file(tmp_path: Path) -> None:
    """Same probes, two subprocess runs with different PYTHONHASHSEED: identical bytes.

    The open() probe lowers the synthetic TextIO/BytesIO classes, emitted today in
    set iteration order; canonicalization must sort that away. Without the sort the
    two files would differ.
    """

    probes = write_probes(tmp_path / "probes", [probe_json("b.one"), open_probe_json()])
    outs = []
    for seed in ("0", "1"):
        out = tmp_path / f"b{seed}.json"
        done = run_cli(probes, "--out", str(out), seed=seed)
        assert done.returncode == 0, done.stderr
        outs.append(out.read_bytes())
    assert outs[0] == outs[1]


def test_main_module_entry_generates_and_compares(tmp_path: Path) -> None:
    """In-process main(): generate then compare, using a tmp cwd so lower_file paths stay tmp-relative."""

    probes = base_probes(tmp_path)
    baseline = tmp_path / "b.json"
    assert irscore.main([str(probes), "--out", str(baseline)]) == 0
    assert baseline.is_file()
    assert irscore.main([str(probes), "--baseline", str(baseline)]) == 0
    moved = write_probes(tmp_path / "moved", [probe_json("b.one", 42)])
    assert irscore.main([str(moved), "--baseline", str(baseline)]) == 1


def test_bad_baseline_file_exits_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A corrupt baseline exits 2 before any lowering work."""

    probes = base_probes(tmp_path)
    broken = tmp_path / "broken.json"
    broken.write_text('{"version": 1', encoding="utf-8")
    monkeypatch.setattr(irscore, "probe_hashes", lambda probes, **_: {"b.one": "0" * 64, "b.two": "1" * 64})
    assert irscore.main([str(probes), "--baseline", str(broken)]) == 2
    assert "not valid JSON" in capsys.readouterr().err
