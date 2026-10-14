"""Scorer v2: run probes, type every outcome, cache by (probe, compiler) hash.

    python -m benchmarks.compat2.score PROBES --out results.json [--workers N]
        [--shard I/N] [--tier 0|1] [--coverage-map map.json] [--no-cache] [--timeout S]

Outcomes are the typed set in outcomes.py. The headroom check runs first and the
scorer refuses to start while any row is capped. v1 (benchmarks/compat) is frozen
and only its CPython oracle idea is reused here.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import resource
import subprocess
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmarks.compat2 import bundle, cache, coverage_map, headroom
from benchmarks.compat2.cache import CACHE_DIR, COMPILER_DIR, ROOT, FileHashes, cache_key, compiler_hash, probe_hash  # noqa: F401
from benchmarks.compat2.corpus import load as corpus
from benchmarks.compat2.loader import load_probes
from benchmarks.compat2.outcomes import WEIGHTS, Probe, Row

PS_CODE = re.compile(r"PS\d+")


@dataclass(frozen=True)
class Result:
    row: Row
    compile_s: float = 0.0


Evaluator = Callable[[Probe, float], Result]

# Opened only by --holdout. The default run never reads the holdout corpus.
USE_HOLDOUT = False


def tree_for(probe: Probe) -> Path | None:
    """The pinned package tree a top100 probe belongs to, or None."""

    if probe.area != "top100":
        return None
    project = probe.id.split(".")[1]
    for holdout in (False, True) if USE_HOLDOUT else (False,):
        pin = corpus.package_pins(holdout).get(project)
        if pin:
            return corpus.package_root(pin, holdout)
    return None


def evaluate(probe: Probe, timeout: float) -> Result:
    """Compile and run one probe natively, compare with CPython."""

    def row(outcome: str, evidence: str = "", code: str = "") -> Row:
        return Row(
            id=probe.id,
            area=probe.area,
            category=probe.category,
            outcome=outcome,  # type: ignore[arg-type]
            code=code,
            evidence=evidence[:300],
        )

    if probe.kind == "mention":
        return Result(row("excluded", "mention probe is not scorable"))
    tree = tree_for(probe)
    env = _tree_env(tree)
    if probe.call == "":
        return _program(probe, row, tree, env, timeout)
    oracle = _cpython(probe, timeout, env)
    if oracle[0] == "timeout":
        return Result(row("excluded", "oracle timed out"))
    if probe.kind == "raises":
        if oracle[0] != "raise" or oracle[1].split(":")[0] != probe.expect_exc:
            return Result(row("excluded", f"oracle did not raise {probe.expect_exc}: {oracle[1]}"))
    elif oracle[0] != "ok":
        return Result(row("excluded", f"oracle raised {oracle[1]}"))

    with tempfile.TemporaryDirectory(prefix="compat2-") as tmp:
        src = Path(tmp) / "probe.py"
        binary = Path(tmp) / "probe"
        src.write_text(probe.source, encoding="utf-8")
        built = _build(
            [str(src), "--emit", "bin", "--call", probe.call, "--args", repr(list(probe.args)), "-o", str(binary)],
            env,
            timeout,
            row,
        )
        if isinstance(built, Result):
            return built
        build, spent = built
        if build.returncode != 0 or not binary.exists():
            return Result(row("build-error", _tail(build.stderr or build.stdout)), spent)
        try:
            ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return Result(row("timeout", "binary timed out"), spent)
    return Result(_judge(probe, row, oracle, ran), spent)


def _tree_env(tree: Path | None) -> dict[str, str] | None:
    """Every pinned package's parent goes on the import path, so one package can import another."""

    if tree is None:
        return None
    env = os.environ.copy()
    roots = [str(tree.parent), *map(str, corpus.import_roots(USE_HOLDOUT))]
    if env.get("PYTHONPATH"):
        roots.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(roots))
    return env


def _build(args: list[str], env: dict[str, str] | None, timeout: float, row: Callable[..., Row]) -> Any:
    """Run the compiler. Returns (proc, seconds), or a final Result for fence and timeout."""

    started = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pyscriptc", "compile", *args, "--skip-check"],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout,
        )  # fmt: skip
    except subprocess.TimeoutExpired:
        return Result(row("timeout", "compile timed out"), time.monotonic() - started)
    spent = time.monotonic() - started
    if proc.returncode == 3:
        match = PS_CODE.search(proc.stderr)
        return Result(row("fence", _tail(proc.stderr), match.group(0) if match else ""), spent)
    return proc, spent


def _program(
    probe: Probe, row: Callable[..., Row], tree: Path | None, env: dict[str, str] | None, timeout: float
) -> Result:
    """Whole program: `args[0]` is the expected stdout. A package program expects "" and only must compile."""

    expected = str(probe.args[0]) if probe.args else ""
    with tempfile.TemporaryDirectory(prefix="compat2-") as tmp:
        target = tree if tree is not None and probe.category == "program" else Path(tmp) / "main.py"
        if target != tree:
            target.write_text(probe.source, encoding="utf-8")
        binary = Path(tmp) / "prog"
        built = _build([str(target), "--emit", "ir", "-o", str(Path(tmp) / "prog.ir.json")], env, timeout, row)
        if isinstance(built, Result):
            return built
        proc, spent = built
        if proc.returncode != 0:
            return Result(row("build-error", _tail(proc.stderr or proc.stdout)), spent)
        if tree is not None:
            return Result(row("pass", "tree compiles with no fences"), spent)
        built = _build([str(target), "--run", "-o", str(binary)], env, timeout, row)
        if isinstance(built, Result):
            return built
        proc, more = built
    spent += more
    if proc.returncode < 0 or proc.returncode >= 128:
        return Result(row("crash", f"signal exit {proc.returncode}: {_tail(proc.stderr)}"), spent)
    if proc.returncode != 0:
        return Result(row("build-error", _tail(proc.stderr or proc.stdout)), spent)
    if proc.stdout == expected:
        return Result(row("pass"), spent)
    return Result(row("wrong-output", f"native {proc.stdout[:80]!r} expected {expected[:80]!r}"), spent)


def _judge(probe: Probe, row: Callable[..., Row], oracle: tuple[str, str], ran: Any) -> Row:
    if ran.returncode < 0 or ran.returncode >= 128:
        return row("crash", f"signal exit {ran.returncode}: {_tail(ran.stderr)}")
    if probe.kind == "raises":
        if ran.returncode != 0 and probe.expect_exc in ran.stderr:
            return row("pass")
        return row("wrong-output", f"expected {probe.expect_exc}, exit {ran.returncode}: {_tail(ran.stderr)}")
    if ran.returncode != 0:
        return row("crash", f"exit {ran.returncode}: {_tail(ran.stderr)}")
    if ran.stdout.strip() == oracle[1]:
        return row("pass")
    return row("wrong-output", f"native {ran.stdout.strip()!r} CPython {oracle[1]!r}")


_ORACLE = (
    "import sys\n"
    "ns = {{}}\n"
    "exec(compile(sys.stdin.read(), '<probe>', 'exec'), ns)\n"
    "try:\n"
    "    value = ns[{call!r}](*{args!r})\n"
    "except Exception as exc:\n"
    "    print('raise', type(exc).__name__ + ': ' + (str(exc).splitlines() or [''])[0])\n"
    "else:\n"
    "    print('ok', value)\n"
)


def _cpython(probe: Probe, timeout: float, env: dict[str, str] | None = None) -> tuple[str, str]:
    code = _ORACLE.format(call=probe.call, args=list(probe.args))
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code], input=probe.source, capture_output=True, text=True, timeout=timeout, env=env
        )
    except subprocess.TimeoutExpired:
        return "timeout", ""
    if proc.returncode != 0:
        return "raise", _tail(proc.stderr)[:120] or "Error"
    kind, _, value = proc.stdout.rstrip("\n").partition(" ")
    return kind, value


def _tail(text: str) -> str:
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    return lines[-1].strip() if lines else ""


def _cached(cache_dir: Path, key: str) -> Result | None:
    data = cache.load(cache_dir, key)
    try:
        return Result(Row(**data["row"]), data["compile_s"]) if data else None
    except KeyError, TypeError:
        return None


def score(
    probes: list[Probe],
    *,
    workers: int = 4,
    timeout: float = 60.0,
    coverage: dict[str, list[str]] | None = None,
    cache_dir: Path | None = CACHE_DIR,
    evaluator: Evaluator = evaluate,
    bundles: bool | None = None,
) -> dict[str, Any]:
    """Score probes. Cache hits are free; misses are compiled in bundles unless `bundles` is False.

    Bundling defaults on for the real evaluator and off for any stand-in.
    """

    coverage = coverage or {}
    hashes = FileHashes()
    full = hashes.whole()
    use_bundles = (evaluator is evaluate) if bundles is None else bundles
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)

    keys = {p.id: cache_key(p, coverage, hashes) if cache_dir else "" for p in probes}
    done: dict[str, tuple[Result, bool]] = {}
    misses: list[Probe] = []
    for probe in probes:
        hit = _cached(cache_dir, keys[probe.id]) if cache_dir else None
        if hit is None:
            misses.append(probe)
        else:
            done[probe.id] = (hit, True)

    def run(unit: list[Probe]) -> list[tuple[str, Result]]:
        results = bundle.evaluate_group(unit, timeout, evaluator)
        for probe, result in zip(unit, results, strict=True):
            if cache_dir:
                cache.store(cache_dir, keys[probe.id], {"row": result.row.to_json(), "compile_s": result.compile_s})
        return [(p.id, r) for p, r in zip(unit, results, strict=True)]

    units = bundle.plan(misses) if use_bundles else [[p] for p in misses]
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for finished in pool.map(run, units):
            for pid, result in finished:
                done[pid] = (result, False)
    rows = [done[p.id][0].row for p in probes]
    return {
        "compiler_hash": full,
        "wall_s": round(time.monotonic() - started, 3),
        "compile_s": round(sum(done[p.id][0].compile_s for p in probes), 3),
        "cache_hits": sum(1 for p in probes if done[p.id][1]),
        "weights": {p.id: effective_weight(p) for p in probes},
        "rows": [row.to_json() for row in rows],
        "counts": counts(rows),
    }


def effective_weight(probe: Probe) -> float:
    """The corpus loader already sets weight to the category weight; the default 1.0 means unset."""

    return WEIGHTS[probe.category] if probe.weight == 1.0 else probe.weight


def counts(rows: Iterable[Row]) -> dict[str, int]:
    tally: dict[str, int] = {}
    for row in rows:
        tally[row.outcome] = tally.get(row.outcome, 0) + 1
    return tally


def weighted_pass(results: dict[str, Any]) -> float:
    weights = results["weights"]
    return sum(weights.get(r["id"], 1.0) for r in results["rows"] if r["outcome"] == "pass")


def shard_path(out: Path, index: int, total: int) -> Path:
    return out.with_name(f"{out.stem}.shard{index}of{total}{out.suffix}")


def write_results(path: Path, results: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def merge_results(parts: list[dict[str, Any]]) -> dict[str, Any]:
    """One result from shard results of the same compiler. Raises ValueError on a mismatch."""

    if len({p["compiler_hash"] for p in parts}) != 1:
        raise ValueError("shards were scored with different compilers")
    rows = [row for part in parts for row in part["rows"]]
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("a probe appears in two shards")
    weights = {k: v for part in parts for k, v in part["weights"].items()}
    rows.sort(key=lambda row: row["id"])
    return {
        "compiler_hash": parts[0]["compiler_hash"],
        "wall_s": round(max(p["wall_s"] for p in parts), 3),  # shards run side by side
        "compile_s": round(sum(p["compile_s"] for p in parts), 3),
        "cache_hits": sum(p["cache_hits"] for p in parts),
        "weights": weights,
        "rows": rows,
        "counts": dict(Counter(row["outcome"] for row in rows)),
    }


def merge_if_complete(out: Path, total: int) -> bool:
    """Merge the shard files next to `out` into `out` once all `total` are present and agree."""

    files = [shard_path(out, i, total) for i in range(total)]
    if not all(f.exists() for f in files):
        return False
    try:
        parts = [json.loads(f.read_text(encoding="utf-8")) for f in files]
        if any(p.get("shard") != {"index": i, "total": total} for i, p in enumerate(parts)):
            return False
        write_results(out, merge_results(parts))
    except ValueError, KeyError:
        return False  # stale shard files from another run: leave them, merge when the new ones land
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score probes against CPython (scorer v2).")
    parser.add_argument("probes", nargs="?", help="probe JSONL file or directory")
    parser.add_argument("--corpus", action="store_true", help="also score the real program corpus")
    parser.add_argument(
        "--holdout", action="store_true", help="score the held out corpus instead (epic boundaries only)"
    )
    parser.add_argument("--out", help="write results JSON here")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--shard", default="0/1", help="I/N: run probes whose index mod N equals I")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument(
        "--coverage-map", help="coverage_map.py output for narrower cache keys (default: the built map, if any)"
    )
    parser.add_argument("--tier", type=int, choices=(0, 1), default=1, help="0: probes the git diff can move; 1: all")
    parser.add_argument("--base", default="HEAD", help="git ref the Tier 0 diff is taken against")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--headroom", action="store_true", help="only run the headroom check")
    args = parser.parse_args(argv)

    _, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (hard if hard != resource.RLIM_INFINITY else 65536, hard))
    global USE_HOLDOUT
    USE_HOLDOUT = args.holdout
    if args.holdout and not corpus.holdout_open():
        print(f"the holdout is closed: it opens only for the evaluator ({corpus.EVALUATOR_ENV}=1)", file=sys.stderr)
        return 2
    probes = list(load_probes(args.probes)) if args.probes else []
    if args.corpus or args.holdout:
        probes += corpus.load(args.holdout)
    if not probes:
        parser.error("give a probes path, --corpus or --holdout")
    if not args.out and not args.headroom:
        parser.error("--out is required")
    if args.headroom:
        for row in headroom.capped(probes):
            print(f"capped {row.id}: {row.evidence}", file=sys.stderr)
        return 1 if headroom.capped(probes) else 0
    bad = headroom.capped(probes)
    if bad:
        for row in bad:
            print(f"capped {row.id}: {row.evidence}", file=sys.stderr)
        print(f"refusing to start: {len(bad)} capped rows (see --headroom)", file=sys.stderr)
        return 2
    if args.tier == 0:
        probes, why = coverage_map.tier0(probes, args.base)
        print(f"tier 0: {len(probes)} probes ({why})", file=sys.stderr)
    index, _, total = args.shard.partition("/")
    shard = (int(index), int(total or 1))
    probes = [p for i, p in enumerate(probes) if i % shard[1] == shard[0]]
    mapped = coverage_map.load(Path(args.coverage_map) if args.coverage_map else coverage_map.MAP_PATH)
    coverage = coverage_map.key_map(mapped, probes) if mapped else {}
    results = score(
        probes,
        workers=args.workers,
        timeout=args.timeout,
        coverage=coverage,
        cache_dir=None if args.no_cache or args.holdout else CACHE_DIR,
    )
    out = Path(args.out)
    if shard[1] > 1:
        results["shard"] = {"index": shard[0], "total": shard[1]}
        out = shard_path(out, *shard)
    write_results(out, results)
    if shard[1] > 1:
        merged = merge_if_complete(Path(args.out), shard[1])
        print(
            f"shard {shard[0]}/{shard[1]}: {out.name}" + (f", merged all {shard[1]} into {args.out}" if merged else "")
        )
    if args.holdout:
        # aggregate numbers only: per probe ids and results must never reach a generator's context
        print(f"holdout: scored {len(probes)} probes  {results['counts']}")
    else:
        print(f"scored {len(probes)} probes in {results['wall_s']}s  {results['counts']}")
    print(f"weighted pass {weighted_pass(results):.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
