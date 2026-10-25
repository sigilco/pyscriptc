"""Time the real sibling scripts under CPython and under the island floor binary.

The scripts live outside this repository (they are the sibling projects the
weekly dogfood runs against), so the harness takes a corpus root and keeps
per-case inputs under `benchmarks/real/`. Every case compiles once with
`--emit bin --fallback=cpython`, reads the executed plane map from the
artifact's `.report.json`, then times `python3 script` against the compiled
binary on the same argument list, interleaving the two so drift hits both.

    uv run python benchmarks/real/run.py --out benchmarks/real/results.json

Ten or more samples after two warmups, median plus p10/p90, the 1 minute load
average of every sample, and compile time and binary size recorded separately.
Nothing is parallel: the machine is shared.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORPUS = Path.home() / "Documents" / "prjcts" / "_own"
HERE = Path(__file__).resolve().parent
PLANES = ("native", "island")


class Skipped(Exception):
    """A case cannot run in this environment (missing input or dependency)."""


def expand(value: str, ctx: dict[str, str]) -> str:
    if value.startswith("{env:"):
        name = value[5:-1]
        resolved = os.environ.get(name, "")
        if not resolved:
            raise Skipped(f"{name} is not set")
        return resolved
    return value.format(**ctx)


def redact(text: str, corpus: Path, scratch: Path) -> str:
    """Strip the machine layout from recorded paths and argument lists."""
    for prefix, token in (
        (str(scratch), "<scratch>"),
        (str(corpus), "<corpus>"),
        (str(HERE), "<inputs>"),
        (str(REPO_ROOT), "<repo>"),
        (str(Path.home()), "~"),
    ):
        text = text.replace(prefix, token)
    return text


def quantile(values: list[float], q: float) -> float:
    if len(values) == 1:
        return values[0]
    cuts = statistics.quantiles(values, n=100, method="inclusive")
    return cuts[min(int(q * 100), 99) - 1] if q < 1 else values[-1]


def summarize(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    return {
        "median": statistics.median(ordered),
        "p10": quantile(ordered, 0.1),
        "p90": quantile(ordered, 0.9),
        "min": ordered[0],
        "max": ordered[-1],
    }


def timed(
    argv: list[str], cwd: Path, env: dict[str, str], timeout: int
) -> tuple[float, subprocess.CompletedProcess[bytes]]:
    start = time.perf_counter()
    proc = subprocess.run(argv, cwd=str(cwd), env=env, capture_output=True, timeout=timeout, check=False)
    return (time.perf_counter() - start) * 1000.0, proc


def plane_summary(report_path: Path) -> dict[str, object]:
    data = json.loads(report_path.read_text(encoding="utf-8"))
    plane = data.get("planeMap") or {}
    functions = plane.get("functions") or {}
    counts = {kind: sum(1 for value in functions.values() if value == kind) for kind in PLANES}
    total = len(functions)
    return {
        "moduleTopLevel": plane.get("moduleTopLevel"),
        "topLevelFences": plane.get("topLevelFences", 0),
        "functions": functions,
        "nativeFunctions": counts["native"],
        "islandFunctions": counts["island"],
        "functionCount": total,
        "nativeShare": (counts["native"] / total) if total else 0.0,
        "islandShare": (counts["island"] / total) if total else 0.0,
        "requiresLibpython": data.get("requiresLibpython"),
    }


def run_case(case: dict[str, object], ctx: dict[str, str], options: argparse.Namespace) -> dict[str, object]:
    name = str(case["name"])
    if case.get("exclude"):
        raise Skipped(str(case["exclude"]))
    script = Path(expand(str(case["script"]), ctx))
    if not script.is_file():
        raise Skipped(f"script not found: {script}")
    cwd = Path(expand(str(case.get("cwd") or script.parent), ctx))
    scratch = HERE / ".scratch" / name
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True, exist_ok=True)
    inner = {"inputs": str(HERE), "scratch": str(scratch), "corpus": str(Path(ctx["corpus"]))}
    argv = [expand(str(item), inner) for item in case.get("argv", [])]
    # A case gets the ambient environment minus PYTHONPATH, so an inherited
    # path cannot silently change which modules the run imports; a case that
    # needs its own path (or HOME) sets it below.
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    for key, value in (case.get("env") or {}).items():
        env[str(key)] = expand(str(value), inner)

    artifact = HERE / ".artifacts" / name
    artifact.parent.mkdir(parents=True, exist_ok=True)
    compile_cmd = [
        *options.pyscriptc,
        "compile",
        "--fallback=cpython",
        "--emit",
        "bin",
        "-o",
        str(artifact),
        str(script),
    ]
    start = time.perf_counter()
    compiled = subprocess.run(
        compile_cmd, cwd=str(cwd), env=env, capture_output=True, timeout=options.timeout * 3, check=False
    )
    compile_ms = (time.perf_counter() - start) * 1000.0
    if compiled.returncode != 0 or not artifact.is_file():
        raise Skipped(f"compile failed with exit {compiled.returncode}")
    if not os.access(artifact, os.X_OK):
        artifact.chmod(0o755)

    corpus = Path(ctx["corpus"])

    def shown(value: str) -> str:
        return redact(value, corpus, scratch)

    result: dict[str, object] = {
        "name": name,
        "kind": case.get("kind", "workload"),
        "script": shown(str(script)),
        "cwd": shown(str(cwd)),
        "argv": [shown(item) for item in argv],
        "note": case.get("note"),
        "compile": {"ms": compile_ms, "exit": compiled.returncode, "command": shown(" ".join(compile_cmd))},
        "binary": {"path": shown(str(artifact)), "bytes": artifact.stat().st_size},
    }
    report_path = artifact.with_suffix(".report.json")
    if report_path.is_file():
        plane = plane_summary(report_path)
        result["plane"] = plane
        result["binary"]["requiresLibpython"] = plane["requiresLibpython"]

    python_cmd = [options.python, str(script), *argv]
    binary_cmd = [str(artifact), *argv]
    for _ in range(options.warmups):
        timed(python_cmd, cwd, env, options.timeout)
        timed(binary_cmd, cwd, env, options.timeout)

    py_samples: list[float] = []
    bin_samples: list[float] = []
    loads: list[float] = []
    output_match = True
    py_exit = bin_exit = None
    py_out = bin_out = b""
    for index in range(options.samples):
        loads.append(os.getloadavg()[0])
        py_ms, py_proc = timed(python_cmd, cwd, env, options.timeout)
        bin_ms, bin_proc = timed(binary_cmd, cwd, env, options.timeout)
        py_samples.append(py_ms)
        bin_samples.append(bin_ms)
        if index == 0:
            py_exit, bin_exit = py_proc.returncode, bin_proc.returncode
            py_out, bin_out = py_proc.stdout, bin_proc.stdout
            output_match = py_exit == bin_exit and py_out == bin_out

    stats = {"python": summarize(py_samples), "binary": summarize(bin_samples)}
    stats["ratio"] = stats["binary"]["median"] / stats["python"]["median"]
    result.update(
        {
            "status": "ok" if output_match else "mismatch",
            "exitCodes": {"python": py_exit, "binary": bin_exit},
            "outputMatch": output_match,
            "pythonStdoutBytes": len(py_out),
            "binaryStdoutBytes": len(bin_out),
            "samples": {
                "python": [round(value, 3) for value in py_samples],
                "binary": [round(value, 3) for value in bin_samples],
                "loadavg": loads,
            },
            "stats": stats,
        }
    )
    return result


def git_commit() -> str:
    proc = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False
    )
    return proc.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases", default=str(HERE / "cases.json"))
    parser.add_argument("--out", default=str(HERE / "results.json"))
    parser.add_argument("--corpus", default=os.environ.get("PYSC_REAL_CORPUS") or str(DEFAULT_CORPUS))
    parser.add_argument("--python", default="python3")
    parser.add_argument("--samples", type=int, default=12)
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--only", action="append", default=[])
    parser.add_argument("--fail-on-mismatch", action="store_true")
    parser.add_argument(
        "--pyscriptc",
        nargs="+",
        default=["uv", "run", "--project", str(REPO_ROOT), "pyscriptc"],
        help="compiler command prefix",
    )
    options = parser.parse_args()
    if options.samples < 10:
        parser.error("--samples must be at least 10")

    manifest = json.loads(Path(options.cases).read_text(encoding="utf-8"))
    ctx = {"corpus": options.corpus, "inputs": str(HERE)}
    results: dict[str, object] = {
        "schemaVersion": 1,
        "recipe": "uv run python benchmarks/real/run.py",
        "commit": git_commit(),
        "machine": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "cpuCount": os.cpu_count(),
            "loadavgStart": os.getloadavg(),
        },
        "python": subprocess.run(
            [options.python, "--version"], capture_output=True, text=True, check=False
        ).stdout.strip(),
        "corpus": options.corpus,
        "samples": options.samples,
        "warmups": options.warmups,
        "cases": [],
    }
    failures = 0
    for case in manifest["cases"]:
        if options.only and str(case["name"]) not in options.only:
            continue
        try:
            entry = run_case(case, ctx, options)
        except Skipped as error:
            entry = {
                "name": case["name"],
                "kind": case.get("kind", "workload"),
                "status": "skipped",
                "skipReason": str(error),
            }
        results["cases"].append(entry)
        status = entry["status"]
        if status == "ok":
            stats = entry["stats"]
            plane = entry.get("plane") or {}
            print(
                f"{entry['name']:22} python {stats['python']['median']:8.2f} ms  "
                f"binary {stats['binary']['median']:8.2f} ms  ratio {stats['ratio']:5.2f}  "
                f"island {100 * plane.get('islandShare', 0):5.1f}%  {entry['kind']}",
                file=sys.stderr,
            )
        else:
            print(f"{entry['name']:22} {status}: {entry.get('skipReason', 'stdout or exit mismatch')}", file=sys.stderr)
        if status != "ok":
            failures += 1

    out = Path(options.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out}", file=sys.stderr)
    return 1 if (failures and options.fail_on_mismatch) else 0


if __name__ == "__main__":
    raise SystemExit(main())
