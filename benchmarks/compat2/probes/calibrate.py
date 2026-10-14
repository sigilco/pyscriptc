"""Calibrate every frozen probe against the current compiler (Linux only).

    uv run python -m benchmarks.compat2.probes.calibrate --shard 0/8 --workers 2 --out shard-0.json
    uv run python -m benchmarks.compat2.probes.calibrate --merge shard-0.json shard-1.json --out CALIBRATION.json

The probe set is the four JSONL files in this directory plus ``continuity/v1equiv.jsonl``. Rows are sorted by id
and split with ``--shard I/N`` (index mod N). Each shard writes per-id outcomes and a cache jsonl so a killed
shard resumes. ``--merge`` unions those outcomes in id order and rebuilds the aggregates.

``--oracle`` names the CPython the comparison should use. The A1 interpreter switch
(``generate.interpreter`` / ``PYSC_ORACLE_DIR``) is not on this branch, so the only accepted value is the
running interpreter. Any other version exits non-zero and names that missing switch. The report records the
version that actually ran.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import threading
from collections import Counter
from collections.abc import Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from . import engine

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
FILES = ("builtins", "surface", "modules", "language")
TIMEOUT = 120
MISSING_SWITCH = (
    "the A1 interpreter switch is not on this branch (benchmarks.compat2.probes.generate.interpreter / PYSC_ORACLE_DIR)"
)


def load_all() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for name in FILES:
        path = HERE / f"{name}.jsonl"
        rows += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    continuity = HERE.parent / "continuity" / "v1equiv.jsonl"
    rows += [json.loads(line) for line in continuity.read_text(encoding="utf-8").splitlines() if line]
    return rows


def v1_lowered_ids() -> set[str]:
    """Probe ids for rows the frozen v1 scorer had lowered in its newest iteration."""
    newest = sorted((ROOT / "benchmarks" / "compat" / "iterations").glob("*.json"))[-1]
    data = json.loads(newest.read_text(encoding="utf-8"))
    ids = {f"builtins.{r['id']}" for r in data["builtins"] if r["status"] == "lowered"}
    ids |= {f"stdlib.{r['id']}" for r in data["stdlib_surface"] if r["status"] == "lowered"}
    ids |= {f"stdlib.{r['id']}" for r in data["stdlib_modules"] if r["status"] == "lowered"}
    return ids


def cell_of(row: dict[str, object]) -> str:
    return f"{row['area']}/{row['category']}/{row['kind']}"


def parse_shard(spec: str) -> tuple[int, int]:
    index, sep, total = spec.partition("/")
    if sep != "/" or not index.isdecimal() or not total.isdecimal():
        raise SystemExit(f"--shard must be I/N, got {spec!r}")
    shard, count = int(index), int(total)
    if count < 1 or shard < 0 or shard >= count:
        raise SystemExit(f"--shard {spec} is out of range")
    return shard, count


def assign_shard(rows: Sequence[dict[str, object]], shard: tuple[int, int] | None) -> list[dict[str, object]]:
    """Stable full-set order is by id. Shard I/N keeps indexes where index mod N equals I."""
    ordered = sorted(rows, key=lambda row: str(row["id"]))
    if shard is None:
        return ordered
    index, count = shard
    return [row for position, row in enumerate(ordered) if position % count == index]


def running_oracle() -> dict[str, str | None]:
    version = f"{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}"
    return {"version": version, "executable": sys.executable, "requested": None}


def resolve_oracle(requested: str | None) -> dict[str, str | None]:
    """Accept the running interpreter. Any other version names the missing A1 switch and fails."""
    current = running_oracle()
    minor = f"{sys.version_info[0]}.{sys.version_info[1]}"
    if requested is None or requested in {minor, current["version"]}:
        return {**current, "requested": requested}
    raise SystemExit(
        f"--oracle {requested}: {MISSING_SWITCH}. "
        f"This calibration only runs the current interpreter {current['version']} ({current['executable']}). "
        f"Refusing to report a {requested} oracle it did not run."
    )


def wrapped(row: dict[str, object]) -> str:
    call = ", ".join(repr(a) for a in row["args"])  # type: ignore[attr-defined]
    return f"{row['source']}\n\ndef entry() -> int:\n    return {row['call']}({call})\n"


def oracle(row: dict[str, object]) -> str:
    def job() -> object:
        namespace: dict[str, object] = {}
        exec(compile(str(row["source"]), "<probe>", "exec"), namespace)  # noqa: S102
        return namespace[str(row["call"])](*row["args"])  # type: ignore[operator,misc]

    status, value = engine.run_forked(job)
    return str(value) if status == "ok" else f"!{status}:{value}"


def run_one(row: dict[str, object]) -> str:
    if row["kind"] == "mention":
        return "mention"
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "probe.py"
        src.write_text(wrapped(row), encoding="utf-8")
        cmd = [sys.executable, "-m", "pyscriptc", "compile", str(src), "--run", "--skip-check", "--call", "entry"]
        try:
            proc = subprocess.run(
                [*cmd, "-o", str(Path(tmp) / "bin")], cwd=ROOT, capture_output=True, text=True, timeout=TIMEOUT
            )
        except subprocess.TimeoutExpired:
            return "timeout"
    if proc.returncode == 3:
        return "fence"
    if proc.returncode != 0:
        if row["kind"] != "raises":
            return "build-error"
        return "pass" if str(row["expect_exc"]) in proc.stderr else "raised-other"
    if row["kind"] == "raises":
        return "compiled"
    return "pass" if proc.stdout.strip() == oracle(row) else "wrong-output"


def outcome_row(row: dict[str, object], outcome: str) -> dict[str, str]:
    return {"id": str(row["id"]), "outcome": outcome, "cell": cell_of(row)}


def merge_outcomes(groups: Iterable[Sequence[dict[str, str]]]) -> list[dict[str, str]]:
    """Union per-id rows. Order does not matter. Identical duplicates collapse; conflicts fail."""
    merged: dict[str, dict[str, str]] = {}
    for group in groups:
        for item in group:
            current = {"id": item["id"], "outcome": item["outcome"], "cell": item["cell"]}
            previous = merged.get(current["id"])
            if previous is not None and previous != current:
                raise SystemExit(f"shard conflict on {current['id']}: {previous} vs {current}")
            merged[current["id"]] = current
    return [merged[key] for key in sorted(merged)]


def merge_oracle(metas: Sequence[dict[str, str | None]]) -> dict[str, str | None]:
    versions = {str(meta["version"]) for meta in metas}
    executables = {str(meta["executable"]) for meta in metas}
    if len(versions) != 1 or len(executables) != 1:
        raise SystemExit(f"shard oracle mismatch: versions {sorted(versions)} executables {sorted(executables)}")
    requested = {meta.get("requested") for meta in metas}
    requested.discard(None)
    if len(requested) > 1:
        raise SystemExit(f"shard --oracle mismatch: {sorted(str(item) for item in requested)}")
    return {
        "version": versions.pop(),
        "executable": executables.pop(),
        "requested": None if not requested else requested.pop(),
    }


def report_from_outcomes(
    outcomes: Sequence[dict[str, str]],
    lowered: set[str],
    oracle_meta: dict[str, str | None],
    shards: Sequence[str] = (),
) -> dict[str, object]:
    ordered = merge_outcomes([outcomes])
    by_id = {item["id"]: item["outcome"] for item in ordered}
    cells: dict[str, Counter[str]] = {}
    for item in ordered:
        cells.setdefault(item["cell"], Counter())[item["outcome"]] += 1
    present = [probe_id for probe_id in sorted(lowered) if probe_id in by_id]
    v1 = Counter(by_id[probe_id] for probe_id in present)
    v1_equiv = Counter(outcome for probe_id, outcome in by_id.items() if probe_id.endswith(".v1"))
    totals = Counter(item["outcome"] for item in ordered)
    report: dict[str, object] = {
        "oracle": oracle_meta,
        "probes": len(ordered),
        "totals": dict(sorted(totals.items())),
        "outcomes": ordered,
        "cells": {key: dict(sorted(counts.items())) for key, counts in sorted(cells.items())},
        "v1_lowered": {
            "ids": len(lowered),
            "v2_probe_outcomes": dict(sorted(v1.items())),
            "v2_probe_missing": sorted(probe_id for probe_id in lowered if probe_id not in by_id),
            "v1_equivalent_probe_outcomes": dict(sorted(v1_equiv.items())),
        },
        "v1_lowered_v2_probe_not_passing": sorted(
            probe_id for probe_id in lowered if by_id.get(probe_id, "absent") != "pass"
        ),
        "v1_equivalent_not_passing": sorted(
            probe_id for probe_id, outcome in by_id.items() if probe_id.endswith(".v1") and outcome != "pass"
        ),
    }
    if shards:
        report["shards"] = list(shards)
    return report


def merge_shard_reports(
    paths: Sequence[Path],
    *,
    expect_ids: set[str] | None,
) -> dict[str, object]:
    loaded = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    outcomes = merge_outcomes(report["outcomes"] for report in loaded)
    if expect_ids is not None:
        got = {item["id"] for item in outcomes}
        missing = sorted(expect_ids - got)
        extra = sorted(got - expect_ids)
        if missing or extra:
            sample = ", ".join(missing[:8])
            raise SystemExit(f"merge incomplete: missing {len(missing)} ({sample}) extra {len(extra)}")
    shards = tuple(sorted({str(report["shard"]) for report in loaded if report.get("shard")}))
    oracle_meta = merge_oracle([report["oracle"] for report in loaded])
    return report_from_outcomes(outcomes, v1_lowered_ids(), oracle_meta, shards)


def dump_report(report: dict[str, object]) -> str:
    return json.dumps(report, indent=2, sort_keys=True) + "\n"


def _load_cache(path: Path, oracle_version: str) -> dict[str, dict[str, str]]:
    done: dict[str, dict[str, str]] = {}
    if not path.is_file():
        return done
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get("oracle") != oracle_version or "outcome" not in item or "cell" not in item:
            continue
        done[str(item["id"])] = {"id": str(item["id"]), "outcome": str(item["outcome"]), "cell": str(item["cell"])}
    return done


def _calibrate_rows(
    rows: Sequence[dict[str, object]], *, workers: int, cache_path: Path, oracle_version: str
) -> list[dict[str, str]]:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    done = _load_cache(cache_path, oracle_version)
    finished: list[dict[str, str]] = []
    pending: list[dict[str, object]] = []
    for row in rows:
        probe_id = str(row["id"])
        cached = done.get(probe_id)
        if cached is not None and cached["cell"] == cell_of(row):
            finished.append(cached)
        else:
            pending.append(row)
    print(f"cached {len(finished)} pending {len(pending)}", file=sys.stderr, flush=True)
    if not pending:
        return finished
    # Import the module by name so the worker function pickles under forkserver.
    # Running this file as __main__ would otherwise point the pool at __main__.run_one.
    import benchmarks.compat2.probes.calibrate as calibrate_mod

    lock = threading.Lock()
    completed = 0
    total = len(pending)

    def store(row: dict[str, object], outcome: str) -> dict[str, str]:
        nonlocal completed
        result = outcome_row(row, outcome)
        line = json.dumps({**result, "oracle": oracle_version})
        with lock:
            with cache_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            completed += 1
            if completed % 50 == 0 or completed == total:
                print(f"  {completed}/{total} {result['id']} {result['outcome']}", file=sys.stderr, flush=True)
        return result

    with ProcessPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(calibrate_mod.run_one, row): row for row in pending}
        for future in as_completed(futures):
            row = futures[future]
            finished.append(store(row, future.result()))
    return finished


def _summary(report: dict[str, object]) -> str:
    oracle_meta = report["oracle"]
    assert isinstance(oracle_meta, dict)
    totals = report["totals"]
    assert isinstance(totals, dict)
    parts = " ".join(f"{key}={value}" for key, value in totals.items())
    shard = report.get("shard", "")
    shard_text = f" shard {shard}" if shard else ""
    return f"oracle {oracle_meta['version']} probes {report['probes']}{shard_text} {parts}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Calibrate every compat2 probe, in resumable shards.")
    parser.add_argument("--shard", default="", help="I/N: probes whose sorted index mod N equals I")
    parser.add_argument("--merge", nargs="*", default=None, help="shard JSON files (or one directory) to merge")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--out", default="")
    parser.add_argument("--cache", default="", help="jsonl of finished ids, so an interrupted shard can resume")
    parser.add_argument(
        "--oracle",
        default=None,
        help="CPython version for the comparison. Only the running interpreter works until the A1 switch lands.",
    )
    opts = parser.parse_args(argv)
    oracle_meta = resolve_oracle(opts.oracle)
    if opts.merge is not None and opts.shard:
        raise SystemExit("pass either --shard or --merge, not both")
    if opts.merge is not None:
        paths: list[Path] = []
        for item in opts.merge:
            candidate = Path(item)
            if candidate.is_dir():
                paths.extend(sorted(candidate.glob("*.json")))
            else:
                paths.append(candidate)
        if not paths:
            raise SystemExit("--merge needs shard JSON files")
        expect = {str(row["id"]) for row in load_all()}
        report = merge_shard_reports(paths, expect_ids=expect)
        dest = Path(opts.out) if opts.out else HERE / "CALIBRATION.json"
        dest.write_text(dump_report(report), encoding="utf-8")
        print(_summary(report))
        print(f"wrote {dest}")
        return 0
    if sys.platform != "linux":
        raise SystemExit("the compiler only runs on Linux, use the dev container")
    shard = parse_shard(opts.shard) if opts.shard else None
    rows = assign_shard(load_all(), shard)
    if shard is None:
        dest = Path(opts.out) if opts.out else HERE / "CALIBRATION.json"
        shard_label = ""
    else:
        index, count = shard
        default_out = Path(tempfile.gettempdir()) / "pyscriptc-a2-cal" / f"shard-{index}-of-{count}.json"
        dest = Path(opts.out) if opts.out else default_out
        shard_label = f"{index}/{count}"
    cache_path = Path(opts.cache) if opts.cache else dest.with_suffix(dest.suffix + ".cache.jsonl")
    print(
        f"oracle {oracle_meta['version']} ({oracle_meta['executable']}) shard {shard_label or 'all'} "
        f"rows {len(rows)} workers {max(1, opts.workers)}",
        file=sys.stderr,
        flush=True,
    )
    outcomes = _calibrate_rows(
        rows, workers=opts.workers, cache_path=cache_path, oracle_version=str(oracle_meta["version"])
    )
    report = report_from_outcomes(outcomes, v1_lowered_ids(), oracle_meta, (shard_label,) if shard_label else ())
    if shard_label:
        report["shard"] = shard_label
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(dump_report(report), encoding="utf-8")
    print(_summary(report))
    print(f"wrote {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
