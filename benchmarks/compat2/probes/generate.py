"""Generate the v2 probe set: `uv run python -m benchmarks.compat2.probes.generate [--oracle 3.12]`.

The oracle is CPython 3.12 by default. Every worker (stdlib surface, language profile, version axis) runs on
the oracle interpreter found under `PYSC_ORACLE_DIR` (uv installs, see `docker/Dockerfile`), while this
orchestrator runs on whatever interpreter started it.

Writes builtins.jsonl, surface.jsonl, modules.jsonl, language.jsonl and MANIFEST.json. The 3.12 set goes next to this
file. Any other oracle (the 3.14 secondary set) goes to `../oracle-<version>/`, outside `probes/` so a directory load
never counts it twice. Every row carries `versions`: the subset of 3.10 to 3.14 where the probe agrees with the oracle.
Output is sorted by probe id and workers run with a fixed hash seed, so the same interpreters and typeshed reproduce
it byte for byte.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import version
from pathlib import Path

from benchmarks.compat2.outcomes import WEIGHTS, Probe

from . import typeshed

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CONTINUITY = HERE.parent / "continuity"  # outside probes/ so a directory load never counts these rows
DENOM = json.loads((ROOT / "benchmarks" / "compat" / "denominators.json").read_text(encoding="utf-8"))
PRIMARY_ORACLE = "3.12"
AXIS = ("3.10", "3.11", "3.12", "3.13", "3.14")
WORKERS = 3
MODULE_TIMEOUT = 1800
HASH_SEEDS = (
    "0",
    "1",
)  # the first seed is the reference. A probe whose result differs under the second is seed dependent


def interpreter(oracle: str) -> str:
    """Path of the CPython `oracle` (a minor version like 3.12): uv installs under PYSC_ORACLE_DIR, else this one."""
    found = sorted(Path(os.environ.get("PYSC_ORACLE_DIR", "/opt/oracles")).glob(f"cpython-{oracle}.*/bin/python3"))
    if found:
        return str(found[-1])
    if f"{sys.version_info[0]}.{sys.version_info[1]}" == oracle:
        return sys.executable
    raise SystemExit(f"no CPython {oracle} interpreter: set PYSC_ORACLE_DIR (uv python install --install-dir)")


def worker_env(oracle: str, seed: str = HASH_SEEDS[0]) -> dict[str, str]:
    return {
        **os.environ,
        "PYSC_ORACLE": oracle,
        "PYSC_TYPESHED": str(typeshed.stdlib_dir()),
        "PYTHONPATH": str(ROOT),
        "PYTHONHASHSEED": seed,
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def run_module(oracle: str, args: list[str], seed: str = HASH_SEEDS[0]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [interpreter(oracle), "-m", *args],
        cwd=ROOT,
        env=worker_env(oracle, seed),
        capture_output=True,
        text=True,
        timeout=MODULE_TIMEOUT,
    )


def run_worker(oracle: str, module: str) -> list[dict[str, object]]:
    proc = run_module(oracle, ["benchmarks.compat2.probes.surface", module])
    rows = [json.loads(line) for line in proc.stdout.splitlines() if line.startswith("{")]
    if proc.returncode != 0:
        print(f"worker {module} exit {proc.returncode}: {proc.stderr.strip()[-300:]}", file=sys.stderr)
    return rows


def split_excluded(rows: list[dict[str, object]]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    kept = [r for r in rows if "probe" in r]
    return kept, [r["excluded"] for r in rows if "excluded" in r]  # type: ignore[misc]


def surface_modules(oracle: tuple[int, int]) -> list[str]:
    """Top-level stdlib modules plus the submodules the frozen v1 surface already counts (importlib.resources).

    Modules that `stdlib/VERSIONS` does not list for the oracle (for example `annotationlib` before 3.14) are dropped.
    """
    top = [m for m in DENOM["stdlib_modules"] if m != "builtins"]
    extra = {q.rpartition(".")[0] for q in DENOM["stdlib_surface"]} - set(top)
    return [m for m in [*top, *sorted(extra)] if typeshed.available(m, oracle)]


def module_rows(oracle: tuple[int, int]) -> list[dict[str, object]]:
    rows = []
    for name in DENOM["stdlib_modules"]:
        if not typeshed.available(name, oracle):
            continue
        source = f"import {name}\n\ndef probe() -> int:\n    return 1\n"
        probe = Probe(f"stdlib.{name}", "modules", "module", "value", source, weight=WEIGHTS["module"])
        rows.append(
            {
                "probe": probe.to_json(),
                "meta": {"id": probe.id, "area": "modules", "category": "module", "kind": "value", "fold": "import"},
            }
        )
    return rows


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    ordered = sorted(rows, key=lambda r: r["probe"]["id"])  # type: ignore[index]
    seen: set[str] = set()
    lines = []
    for row in ordered:
        pid = row["probe"]["id"]  # type: ignore[index]
        if pid in seen:
            raise SystemExit(f"duplicate probe id {pid}")
        seen.add(pid)
        lines.append(json.dumps(row["probe"], sort_keys=True))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def normalize_unstable(rows: list[dict[str, object]], axis_info: dict[str, object]) -> None:
    """Rows the axis caught flapping must not ship a value probe.

    Emission decisions are static (VOLATILE_CALLS, stubs), but a call that slips through
    can still reach the file with whichever probe shape its agreement check happened to
    emit that run. Rewriting every flagged value row to the canonical mention row makes
    the file a function of the manifest flags, which come from multi-sample repeats and
    are stable where a single forked check is not.
    """
    flagged = set(axis_info["process_dependent"]) | set(axis_info["seed_dependent"])  # type: ignore[arg-type]
    for row in rows:
        probe = row["probe"]  # type: ignore[index]
        pid = str(probe["id"])
        if pid not in flagged or probe["kind"] != "value" or not pid.startswith(("stdlib.", "builtins.")):
            continue
        qual = pid.split(".", 1)[1]
        header = str(probe["source"]).split("def probe", 1)[0].rstrip("\n")
        probe["kind"] = "mention"
        probe["args"] = []
        probe["expect_exc"] = ""
        probe["source"] = (header + "\n\n" if header else "") + f"def probe() -> int:\n    {qual}\n    return 1\n"
        meta = row["meta"]  # type: ignore[index]
        meta["kind"] = "mention"
        meta["fold"] = "mention"
        meta["reason"] = "unstable across axis runs"


def compute_versions(oracle: str, rows: list[dict[str, object]]) -> tuple[dict[str, list[str]], dict[str, object]]:
    """For each probe id, the axis versions whose result equals the oracle's (hash seed independent).

    Also returns the ast node names per interpreter, for the language `min_version` tags.
    """
    with tempfile.TemporaryDirectory(prefix="ps-axis-") as tmp:
        path = Path(tmp) / "probes.jsonl"
        ordered = sorted(rows, key=lambda r: r["id"])  # type: ignore[index, return-value]
        path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in ordered), encoding="utf-8")
        jobs = [(v, HASH_SEEDS[0]) for v in AXIS] + [(oracle, HASH_SEEDS[1])]

        def run(job: tuple[str, str]) -> dict[str, tuple[str, object]]:
            proc = run_module(job[0], ["benchmarks.compat2.probes.versions", str(path)], job[1])
            if proc.returncode != 0:
                raise SystemExit(f"version worker {job} failed: {proc.stderr.strip()[-300:]}")
            out = {}
            for line in proc.stdout.splitlines():
                pid, status, value = json.loads(line)
                out[pid] = (status, value)
            return out

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = dict(zip(jobs, pool.map(run, jobs), strict=True))
        # Second and third oracle workers at the same seed decide whether the recorded value survives
        # leaving the process that generated the row. getpriority, clock_gettime, getpgid and friends
        # return values that vary per process; wall-clock probes like timeit.timeit return name-stable
        # but value-unstable ints that slip past the seed check. Two independent repeats must agree
        # with the row's own value, else the probe is process dependent and its versions stay empty.
        repeats = [
            run_module(oracle, ["benchmarks.compat2.probes.versions", str(path)], HASH_SEEDS[0]) for _ in range(2)
        ]
        for rep in repeats:
            if rep.returncode != 0:
                raise SystemExit(f"version worker repeat failed: {rep.stderr.strip()[-300:]}")
    reference = results[(oracle, HASH_SEEDS[0])]
    reseeded = results[(oracle, HASH_SEEDS[1])]
    stable: dict[str, bool] = {pid: True for pid in (row["id"] for row in ordered)}  # type: ignore[index]
    for rep in repeats:
        for line in rep.stdout.splitlines():
            pid, status, value = json.loads(line)
            if (status, value) != reference.get(pid):
                stable[pid] = False
    out: dict[str, list[str]] = {}
    unverified: list[str] = []
    seed_dependent: list[str] = []
    process_dependent: list[str] = []
    for row in ordered:
        pid, ref = row["id"], reference.get(row["id"])  # type: ignore[index]
        valid = ref is not None and (
            (ref[0] == "exc" and ref[1] == row["expect_exc"])  # type: ignore[index]
            if row["kind"] == "raises"  # type: ignore[index]
            else ref[0] == "ok" and isinstance(ref[1], int)  # type: ignore[index]
        )
        if not valid:
            unverified.append(pid)  # type: ignore[arg-type]
            out[pid] = []  # type: ignore[index]
        elif not stable.get(pid, True):
            process_dependent.append(pid)  # type: ignore[arg-type]
            out[pid] = []  # type: ignore[index]
        elif reseeded.get(pid) != ref:  # type: ignore[arg-type]
            seed_dependent.append(pid)  # type: ignore[arg-type]
            out[pid] = []  # type: ignore[index]
        else:
            out[pid] = [v for v in AXIS if results[(v, HASH_SEEDS[0])].get(pid) == ref]  # type: ignore[index, arg-type]
    nodes: dict[str, list[str]] = {}
    for v in AXIS:
        proc = run_module(v, ["benchmarks.compat2.probes.versions", "--ast"])
        nodes[v] = json.loads(proc.stdout)
    return out, {
        "unverified_on_oracle": sorted(unverified),
        "seed_dependent": sorted(seed_dependent),
        "process_dependent": sorted(process_dependent),
        "ast_nodes": nodes,
    }


def min_versions(concrete: list[str], nodes: dict[str, list[str]]) -> dict[str, str]:
    """First axis version that has each oracle ast node, listed only when it is later than the first axis version."""
    out = {}
    for name in concrete:
        first = next(v for v in AXIS if name in nodes[v])
        if first != AXIS[0]:
            out[name] = first
    return out


def newer_nodes(oracle: str, nodes: dict[str, list[str]]) -> dict[str, str]:
    """Ast nodes that exist on a later axis version but not in the oracle, with their first version."""
    later = {n for v in AXIS[AXIS.index(oracle) + 1 :] for n in nodes[v]} - set(nodes[oracle])
    return {n: next(v for v in AXIS if n in nodes[v]) for n in sorted(later)}


def main(argv: list[str]) -> int:
    from . import v1equiv

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--oracle", default=PRIMARY_ORACLE, choices=AXIS)
    parser.add_argument("--out", type=Path, default=None, help="output directory (default depends on the oracle)")
    opts = parser.parse_args(argv)
    oracle = opts.oracle
    primary = oracle == PRIMARY_ORACLE
    out_dir: Path = opts.out or (HERE if primary else HERE.parent / f"oracle-{oracle}")
    oracle_pair = typeshed.parse_version(oracle)

    if sys.platform != "linux":
        raise SystemExit("probe generation executes stdlib calls, run it in the Linux dev container")

    builtins_rows = run_worker(oracle, "builtins")
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        surface_parts = list(pool.map(lambda m: run_worker(oracle, m), surface_modules(oracle_pair)))
    surface, excluded = split_excluded([row for part in surface_parts for row in part])
    modules = module_rows(oracle_pair)
    proc = run_module(oracle, ["benchmarks.compat2.probes.language"])
    if proc.returncode != 0:
        raise SystemExit(f"language worker failed: {proc.stderr.strip()[-500:]}")
    produced = json.loads(proc.stdout)
    lang, lang_info = produced["rows"], produced["info"]
    groups = {"builtins": builtins_rows, "surface": surface, "modules": modules, "language": lang}
    v1_rows: list[dict[str, object]] = []
    v1_info: dict[str, object] = {}
    if primary:
        v1_rows, v1_info = v1equiv.generate()
    every = [*groups.values(), v1_rows]
    versions, axis_info = compute_versions(oracle, [row["probe"] for rows in every for row in rows])  # type: ignore[misc]
    for rows in every:
        for row in rows:
            row["probe"]["versions"] = versions[row["probe"]["id"]]  # type: ignore[index]
    lang_info["min_version"] = min_versions(produced["ast_nodes"], axis_info["ast_nodes"])  # type: ignore[arg-type]
    lang_info["newer_nodes"] = newer_nodes(oracle, axis_info["ast_nodes"])  # type: ignore[arg-type]
    for rows in every:
        normalize_unstable(rows, axis_info)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in groups.items():
        write_jsonl(out_dir / f"{name}.jsonl", rows)
    if primary:
        CONTINUITY.mkdir(exist_ok=True)
        write_jsonl(CONTINUITY / "v1equiv.jsonl", v1_rows)
        if opts.out is not None:
            # Duplicate under an explicit --out so a/b runs can byte-compare it; the
            # default out dir IS the corpus, where a stray *.jsonl would be re-loaded.
            write_jsonl(out_dir / "v1equiv.jsonl", v1_rows)
    metas = [row["meta"] for rows in groups.values() for row in rows]
    ids = {m["id"] for m in metas}
    interpreters = {
        v: subprocess.run(
            [interpreter(v), "-c", "import platform;print(platform.python_version())"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        for v in AXIS
    }
    by_version = {
        area: {v: sum(1 for r in rows if v in r["probe"]["versions"]) for v in AXIS}  # type: ignore[index]
        for area, rows in groups.items()
    }
    manifest = {
        "cpython": interpreters[oracle],
        "cpython_pin": oracle,
        "interpreters": interpreters,
        "mypy": version("mypy"),
        "total": len(metas),
        "versions": {
            "axis": list(AXIS),
            "by_area": by_version,
            "unverified_on_oracle": axis_info["unverified_on_oracle"],
            "seed_dependent": axis_info["seed_dependent"],
            "process_dependent": axis_info["process_dependent"],
        },
        "absent_in_oracle": {
            "builtins": sorted(n for n in DENOM["builtins"] if f"builtins.{n}" not in ids),
            "modules": sorted(n for n in DENOM["stdlib_modules"] if f"stdlib.{n}" not in ids),
        },
        "language": lang_info,
        "excluded_methods": sorted(excluded, key=lambda e: str(e["module"])),
        "areas": {
            area: {
                cat: n
                for (a, cat), n in sorted(Counter((m["area"], m["category"]) for m in metas).items())
                if a == area
            }  # type: ignore[index]
            for area in groups
        },
        "kinds": {
            f"{a}/{c}/{k}": n
            for (a, c, k), n in sorted(Counter((m["area"], m["category"], m["kind"]) for m in metas).items())  # type: ignore[index]
        },
        "folds": {f: n for f, n in sorted(Counter(str(m["fold"]) for m in metas).items())},  # type: ignore[index]
        "mention_reasons": {
            r: n for r, n in sorted(Counter(str(m.get("reason", "")) for m in metas if m["kind"] == "mention").items())
        },  # type: ignore[attr-defined,index]
    }
    if primary:
        manifest["v1_equivalent"] = v1_info
    (out_dir / "MANIFEST.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: manifest[k] for k in ("total", "areas")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
