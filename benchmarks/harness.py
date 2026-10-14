"""Compile and measure helpers shared by the benchmark runners.

The statistics match the rule in scriptc's runtime runner: samples are
interleaved, the reported ratio is median(native) / median(CPython), and a
faster or slower verdict requires the whole bootstrap 95% interval on one
side of 1. Peak resident set size is ``VmHWM`` sampled while the child lives.
"""

from __future__ import annotations

import random
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def median(values: list[float]) -> float:
    ordered = sorted(values)
    count = len(ordered)
    if count == 0:
        raise ValueError("median of an empty sample")
    mid = count // 2
    if count % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def bootstrap_ratio(candidate: list[float], baseline: list[float], resamples: int = 2000) -> tuple[float, float]:
    rng = random.Random(0x5EED)
    ratios: list[float] = []
    candidate_n = len(candidate)
    baseline_n = len(baseline)
    for _ in range(resamples):
        candidate_median = median([candidate[rng.randrange(candidate_n)] for _ in range(candidate_n)])
        baseline_median = median([baseline[rng.randrange(baseline_n)] for _ in range(baseline_n)])
        ratios.append(candidate_median / max(baseline_median, 1e-9))
    ratios.sort()
    return ratios[int(resamples * 0.025)], ratios[int(resamples * 0.975)]


def verdict(low: float, high: float) -> str:
    if high < 1.0:
        return "faster"
    if low > 1.0:
        return "slower"
    return "inconclusive"


def versus_cpython(ratio: float) -> str:
    """Speedup against CPython. `ratio` stays native/CPython; the text is the reciprocal."""

    if ratio <= 0:
        return "inf x CPython"
    return f"{1.0 / ratio:.1f}x CPython"


def geomean(values: list[float]) -> float:
    if not values:
        raise ValueError("geomean of an empty sample")
    product = 1.0
    for value in values:
        product *= value
    return product ** (1.0 / len(values))


def literal_args(args: list[Any]) -> str:
    return repr(list(args))


def compile_call(
    source: Path,
    call: str,
    args: list[Any],
    output: Path,
    *,
    skip_check: bool = False,
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        "-m",
        "pyscriptc",
        "compile",
        str(source),
        "--emit",
        "bin",
        "--call",
        call,
        "--args",
        literal_args(args),
        "-o",
        str(output),
    ]
    if skip_check:
        command.append("--skip-check")
    return subprocess.run(command, capture_output=True, text=True)


ORACLE = """
import ast
import importlib.util
import sys

source, name, literal = sys.argv[1], sys.argv[2], sys.argv[3]
spec = importlib.util.spec_from_file_location("workload", source)
if spec is None or spec.loader is None:
    raise SystemExit("cannot load workload")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
result = getattr(module, name)(*ast.literal_eval(literal))
if isinstance(result, float):
    print("%.17g" % result)
else:
    print(result)
"""


def oracle_command(source: Path, call: str, args: list[Any]) -> list[str]:
    return [sys.executable, "-c", ORACLE, str(source), call, literal_args(args)]


def cpython_stdout(source: Path, call: str, args: list[Any]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(oracle_command(source, call, args), capture_output=True, text=True)


def time_command(command: list[str]) -> tuple[float, subprocess.CompletedProcess[str]]:
    sample = measure_command(command)
    completed = subprocess.CompletedProcess(
        command,
        sample.returncode,
        stdout=sample.stdout,
        stderr=sample.stderr,
    )
    return sample.elapsed_ms, completed


@dataclass(frozen=True)
class Sample:
    elapsed_ms: float
    rss_kb: int
    returncode: int
    stdout: str
    stderr: str


def measure_command(command: list[str]) -> Sample:
    """Run a command and return wall time plus peak resident set size.

    Peak RSS is the highest ``VmHWM`` seen in ``/proc/<pid>/status`` while the
    process is alive. ``ru_maxrss`` is not used: in this cgroup it reports the
    group high-water mark, so a tiny binary and CPython look the same size.
    """

    sampler = _rss_sampler()
    with tempfile.TemporaryDirectory(prefix="pyscriptc-sample-") as tmp:
        peak_path = Path(tmp) / "peak"
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            completed = subprocess.run(
                [str(sampler), str(peak_path), "--", *command],
                stdout=stdout,
                stderr=stderr,
                check=False,
            )
            stdout.seek(0)
            stderr.seek(0)
            rss_kb, elapsed_ms = _read_peak(peak_path)
            return Sample(
                elapsed_ms=elapsed_ms,
                rss_kb=rss_kb,
                returncode=completed.returncode,
                stdout=stdout.read().decode(),
                stderr=stderr.read().decode(),
            )


def _rss_sampler() -> Path:
    source = Path(__file__).resolve().parent / "baseline" / "rss_sample.c"
    binary = Path(tempfile.gettempdir()) / "pyscriptc-rss-sample"
    if binary.exists() and binary.stat().st_mtime >= source.stat().st_mtime:
        return binary
    compiled = subprocess.run(
        ["clang", "-O2", "-o", str(binary), str(source)],
        capture_output=True,
        text=True,
        check=False,
    )
    if compiled.returncode != 0:
        detail = (compiled.stderr or compiled.stdout).strip()
        raise RuntimeError(f"clang failed to build the RSS sampler: {detail}")
    binary.chmod(0o755)
    return binary


def _read_peak(path: Path) -> tuple[int, float]:
    try:
        rss_text, elapsed_text = path.read_text(encoding="utf-8").split()
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"RSS sampler wrote no peak sample at {path}") from exc
    return int(rss_text), float(elapsed_text)
