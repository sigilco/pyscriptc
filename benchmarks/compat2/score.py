"""Scorer v2: run probes, type every outcome, cache by (probe, compiler) hash.

    python -m benchmarks.compat2.score PROBES --out results.json [--workers N]
        [--shard I/N] [--coverage-map map.json] [--no-cache] [--timeout S]

Outcomes are the typed set in outcomes.py. The headroom check runs first and the
scorer refuses to start while any row is capped. v1 (benchmarks/compat) is frozen
and only its CPython oracle idea is reused here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmarks.compat2 import headroom
from benchmarks.compat2.loader import load_probes
from benchmarks.compat2.outcomes import WEIGHTS, Probe, Row

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
COMPILER_DIR = ROOT / "src" / "pyscriptc"
CACHE_DIR = HERE / ".cache"
COMPILER_SUFFIXES = {".py", ".c", ".h"}
PS_CODE = re.compile(r"PS\d+")


@dataclass(frozen=True)
class Result:
    row: Row
    compile_s: float = 0.0


Evaluator = Callable[[Probe, float], Result]


def compiler_hash(modules: Iterable[str] | None = None) -> str:
    """Hash the compiler sources. `modules` limits it to the files a probe touches."""

    digest = hashlib.sha256()
    if modules is None:
        files = sorted(p for p in COMPILER_DIR.rglob("*") if p.suffix in COMPILER_SUFFIXES)
    else:
        files = sorted(COMPILER_DIR / m for m in modules if (COMPILER_DIR / m).is_file())
    for file in files:
        digest.update(str(file.relative_to(COMPILER_DIR)).encode())
        digest.update(file.read_bytes())
    return digest.hexdigest()


def probe_hash(probe: Probe) -> str:
    return hashlib.sha256(json.dumps(probe.to_json(), sort_keys=True).encode()).hexdigest()


def cache_key(probe: Probe, coverage: dict[str, list[str]], full_hash: str) -> str:
    modules = coverage.get(probe.id)
    chash = full_hash if modules is None else compiler_hash(modules)
    return hashlib.sha256((probe_hash(probe) + chash).encode()).hexdigest()


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
    oracle = _cpython(probe, timeout)
    if oracle[0] == "timeout":
        return Result(row("excluded", "oracle timed out"))
    if probe.kind == "raises":
        if oracle[0] != "raise" or oracle[1] != probe.expect_exc:
            return Result(row("excluded", f"oracle did not raise {probe.expect_exc}: {oracle[1]}"))
    elif oracle[0] != "ok":
        return Result(row("excluded", f"oracle raised {oracle[1]}"))

    with tempfile.TemporaryDirectory(prefix="compat2-") as tmp:
        src = Path(tmp) / "probe.py"
        binary = Path(tmp) / "probe"
        src.write_text(probe.source, encoding="utf-8")
        started = time.monotonic()
        try:
            build = subprocess.run(
                [
                    sys.executable, "-m", "pyscriptc", "compile", str(src),
                    "--emit", "bin", "--skip-check", "--call", probe.call,
                    "--args", repr(list(probe.args)), "-o", str(binary),
                ],
                cwd=ROOT, capture_output=True, text=True, timeout=timeout,
            )  # fmt: skip
        except subprocess.TimeoutExpired:
            return Result(row("timeout", "compile timed out"), time.monotonic() - started)
        spent = time.monotonic() - started
        if build.returncode == 3:
            match = PS_CODE.search(build.stderr)
            return Result(row("fence", _tail(build.stderr), match.group(0) if match else ""), spent)
        if build.returncode != 0 or not binary.exists():
            return Result(row("build-error", _tail(build.stderr or build.stdout)), spent)
        try:
            ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return Result(row("timeout", "binary timed out"), spent)
    return Result(_judge(probe, row, oracle, ran), spent)


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
    "    print('raise', type(exc).__name__)\n"
    "else:\n"
    "    print('ok', value)\n"
)


def _cpython(probe: Probe, timeout: float) -> tuple[str, str]:
    code = _ORACLE.format(call=probe.call, args=list(probe.args))
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-c", code], input=probe.source, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return "timeout", ""
    if proc.returncode != 0:
        return "raise", "SyntaxError" if "SyntaxError" in proc.stderr else "Error"
    kind, _, value = proc.stdout.rstrip("\n").partition(" ")
    return kind, value


def _tail(text: str) -> str:
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    return lines[-1].strip() if lines else ""


def score(
    probes: list[Probe],
    *,
    workers: int = 4,
    timeout: float = 60.0,
    coverage: dict[str, list[str]] | None = None,
    cache_dir: Path | None = CACHE_DIR,
    evaluator: Evaluator = evaluate,
) -> dict[str, Any]:
    coverage = coverage or {}
    full = compiler_hash()
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)

    def one(probe: Probe) -> tuple[Result, bool]:
        path = cache_dir / (cache_key(probe, coverage, full) + ".json") if cache_dir else None
        if path is not None and path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return Result(Row(**data["row"]), data["compile_s"]), True
        result = evaluator(probe, timeout)
        if path is not None:
            path.write_text(json.dumps({"row": result.row.to_json(), "compile_s": result.compile_s}), encoding="utf-8")
        return result, False

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        done = list(pool.map(one, probes))
    rows = [r.row for r, _ in done]
    return {
        "compiler_hash": full,
        "wall_s": round(time.monotonic() - started, 3),
        "compile_s": round(sum(r.compile_s for r, _ in done), 3),
        "cache_hits": sum(1 for _, hit in done if hit),
        "weights": {p.id: WEIGHTS[p.category] * p.weight for p in probes},
        "rows": [row.to_json() for row in rows],
        "counts": counts(rows),
    }


def counts(rows: Iterable[Row]) -> dict[str, int]:
    tally: dict[str, int] = {}
    for row in rows:
        tally[row.outcome] = tally.get(row.outcome, 0) + 1
    return tally


def weighted_pass(results: dict[str, Any]) -> float:
    weights = results["weights"]
    return sum(weights.get(r["id"], 1.0) for r in results["rows"] if r["outcome"] == "pass")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score probes against CPython (scorer v2).")
    parser.add_argument("probes", help="probe JSONL file or directory")
    parser.add_argument("--out", required=True, help="write results JSON here")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--shard", default="0/1", help="I/N: run probes whose index mod N equals I")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--coverage-map", help="JSON {probe id: [compiler files]} for narrower cache keys")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--headroom", action="store_true", help="only run the headroom check")
    args = parser.parse_args(argv)

    probes = list(load_probes(args.probes))
    if args.headroom:
        return headroom.main([args.probes])
    bad = headroom.capped(probes)
    if bad:
        for row in bad:
            print(f"capped {row.id}: {row.evidence}", file=sys.stderr)
        print(f"refusing to start: {len(bad)} capped rows (see --headroom)", file=sys.stderr)
        return 2
    index, _, total = args.shard.partition("/")
    probes = [p for i, p in enumerate(probes) if i % int(total or 1) == int(index)]
    coverage = json.loads(Path(args.coverage_map).read_text()) if args.coverage_map else {}
    results = score(
        probes,
        workers=args.workers,
        timeout=args.timeout,
        coverage=coverage,
        cache_dir=None if args.no_cache else CACHE_DIR,
    )
    Path(args.out).write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(f"scored {len(probes)} probes in {results['wall_s']}s  {results['counts']}")
    print(f"weighted pass {weighted_pass(results):.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
