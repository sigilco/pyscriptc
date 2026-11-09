#!/usr/bin/env python3
"""Unstripped-byte corpus comparison lane beside the stripped scorer (#527).

The compat2 scorer judges the corpus on whitespace-stripped stdout
(`benchmarks/compat2/score.py:_judge` compares `ran.stdout.strip()` to the
oracle value). That comparison cannot see a trailing space, a missing
newline, a NUL truncation or an escape-sequence difference. This lane is
the strict sibling `docs/reference/divergences.md` calls planned work:
every runnable probe of the visible corpus is compiled with the default
(direct) emitter, the native binary and the pinned CPython 3.14 oracle run
the same source, and stdout is compared as untrimmed bytes.

The lane covers every corpus probe that produces output:

- `scripts.<name>` (corpus `scripts/*/main.py`): the whole program runs on
  both sides; stdout bytes, exit code and the coarse stderr class are
  compared. stderr stays a class comparison, never bytes, because CPython
  prints a traceback where the runtime prints one line.
- `top100.<project>.<qual>` (corpus closed functions): the wrapper probe
  is compiled and run like the scorer's `--call/--args` evaluation; the
  oracle answer is the same `_ORACLE` harness output minus its `ok `
  prefix, compared byte for byte.
- `top100.<project>` package programs are compile-only in the scorer: they
  produce no output on either side, so the lane reports them as SKIP.

The report is the input to the divergence ledger and it is report-only:
the lane exits 0 with the rows on disk so the nightly can publish the
backlog without gating merges. The scorer, its weights and every frozen
compat2 file stay untouched.

Usage:
    uv run python scripts/strict_corpus_ab.py
    uv run python scripts/strict_corpus_ab.py --json-out out.json --summary "$GITHUB_STEP_SUMMARY"
    uv run python scripts/strict_corpus_ab.py --only scripts.word_freq --workers 4
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

if __package__ in (None, ""):
    # invoked as `python scripts/strict_corpus_ab.py`: put the repo root
    # on sys.path so `scripts.*` and `benchmarks.compat2` resolve
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.compat2.corpus import load as corpus
from benchmarks.compat2.outcomes import Probe
from benchmarks.compat2.score import _ORACLE, PS_CODE, _tree_env, tree_for
from scripts.runtime_ab import Observation, _run_bin, has_c_toolchain
from scripts.runtime_cpython_ab import _run_oracle, resolve_oracle, stderr_class

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TIMEOUT = 120.0
RUN_TIMEOUT = 60.0

PASS = "PASS"
DIFF = "DIFF"
SKIP = "SKIP"
ERROR = "ERROR"


@dataclass(frozen=True)
class StrictRow:
    """One corpus probe's strict-bytes result."""

    probe_id: str
    verdict: str
    note: str
    native: Observation | None = None
    cpython: Observation | None = None
    native_stderr_class: str = "-"
    cpython_stderr_class: str = "-"

    @property
    def name(self) -> str:
        return self.probe_id

    def as_dict(self) -> dict[str, object]:
        row: dict[str, object] = {
            "id": self.probe_id,
            "verdict": self.verdict,
            "note": self.note,
            "native_stderr_class": self.native_stderr_class,
            "cpython_stderr_class": self.cpython_stderr_class,
        }
        if self.native is not None:
            row["native"] = _obs_dict(self.native)
        if self.cpython is not None:
            row["cpython"] = _obs_dict(self.cpython)
        return row


def _obs_dict(obs: Observation) -> dict[str, object]:
    return {
        "stdout": obs.stdout.decode("utf-8", "replace"),
        "stderr": obs.stderr.decode("utf-8", "replace"),
        "returncode": obs.returncode,
    }


def default_probes() -> list[Probe]:
    """Every visible corpus probe the lane can run or report.

    Script rows are keyed by probe id (`scripts.<name>`) so the report
    lines up with the scorer's row ids; the script's `main.py` path is the
    compile target. Package program probes are included and always report
    SKIP, which keeps the row count equal to the visible corpus.
    """
    return [*corpus.script_probes(), *corpus.package_probes()]


def _first_diff(native: bytes, cpython: bytes) -> str:
    """A short note locating the first byte where two outputs disagree."""
    for i, (a, b) in enumerate(zip(native, cpython, strict=False)):
        if a != b:
            return f"stdout first differs at byte {i}: native 0x{a:02x} cpython 0x{b:02x}"
    shorter, longer = (native, cpython) if len(native) < len(cpython) else (cpython, native)
    tail = longer[len(shorter) :][:24]
    side = "cpython" if len(native) < len(cpython) else "native"
    return f"stdout length {len(native)} != {len(cpython)}: extra {side} bytes {tail!r}"


def _compile(
    args: list[str], env: dict[str, str] | None, timeout: float, cwd: Path = ROOT
) -> subprocess.CompletedProcess[bytes]:
    """Run the compiler like the scorer's `_build` does (`--skip-check`, repo cwd)."""
    return subprocess.run(
        [sys.executable, "-m", "pyscriptc", "compile", *args, "--skip-check"],
        cwd=cwd,
        env=env,
        capture_output=True,
        timeout=timeout,
    )


def _fenced(stderr: bytes) -> str | None:
    """The PS code when a compile ended in a fence (exit 3), else None."""
    match = PS_CODE.search(stderr.decode("utf-8", "replace"))
    return match.group(0) if match else None


def _verdict(native: Observation, oracle: Observation) -> tuple[str, str]:
    """PASS, or DIFF with the mismatching channels named."""
    mismatches = [_first_diff(native.stdout, oracle.stdout)] if native.stdout != oracle.stdout else []
    if native.returncode != oracle.returncode:
        mismatches.append(f"exit {oracle.returncode}!={native.returncode}")
    nc, oc = stderr_class(native.stderr), stderr_class(oracle.stderr)
    if nc != oc:
        mismatches.append(f"stderr class {oc}!={nc}")
    return (PASS, "match") if not mismatches else (DIFF, "; ".join(mismatches))


def compare_script(probe: Probe, oracle: str, timeout: float, run_timeout: float) -> StrictRow:
    """Compile `scripts/<name>/main.py` directly, run it and the oracle, byte-compare."""
    main = ROOT / "benchmarks" / "compat2" / "corpus" / "scripts" / str(probe.id).split(".", 1)[1] / "main.py"
    if not main.is_file():
        return StrictRow(probe.id, ERROR, f"missing {main.relative_to(ROOT)}")
    with tempfile.TemporaryDirectory(prefix="pyscriptc-strict-ab-") as tmp:
        root = Path(tmp)
        native_run, oracle_run = root / "native-run", root / "oracle-run"
        native_run.mkdir(), oracle_run.mkdir()
        binary = root / main.stem
        try:
            built = _compile([str(main), "--emit", "bin", "-o", str(binary)], None, timeout)
        except subprocess.TimeoutExpired:
            return StrictRow(probe.id, ERROR, "compile timed out")
        if built.returncode != 0:
            fence = _fenced(built.stderr) if built.returncode == 3 else None
            err = built.stderr.decode("utf-8", "replace").strip().splitlines()
            tail = err[-1] if err else f"exit {built.returncode}"
            verdict, note = (SKIP, f"fenced: {fence}") if fence else (ERROR, f"compile: {tail}")
            return StrictRow(probe.id, verdict, note)
        if not binary.is_file():
            return StrictRow(probe.id, ERROR, "compile produced no binary")
        try:
            cpython_obs = _run_oracle(main, oracle, oracle_run, run_timeout)
            native_obs = _run_bin(binary, native_run, run_timeout)
        except subprocess.TimeoutExpired:
            return StrictRow(probe.id, ERROR, "run timed out")
    verdict, note = _verdict(native_obs, cpython_obs)
    return StrictRow(
        probe.id,
        verdict,
        note,
        native_obs,
        cpython_obs,
        stderr_class(native_obs.stderr),
        stderr_class(cpython_obs.stderr),
    )


def _oracle_raw(probe: Probe, env: dict[str, str] | None, executable: str, timeout: float) -> Observation:
    """The scorer's `_ORACLE` harness under the pinned CPython, with raw bytes kept."""
    code = _ORACLE.format(call=probe.call, args=list(probe.args))
    with tempfile.TemporaryDirectory(prefix="strict-oracle-") as tmp:
        ran = subprocess.run(
            [executable, "-c", code],
            input=probe.source.encode(),
            capture_output=True,
            timeout=timeout,
            env=env,
            cwd=tmp,
        )
    return Observation(ran.stdout, ran.stderr, ran.returncode)


def compare_function(probe: Probe, oracle: str, timeout: float, run_timeout: float) -> StrictRow:
    """Compile and run a top100 wrapper probe; byte-compare against the oracle's `ok` line."""
    tree = tree_for(probe)
    env = _tree_env(tree)
    try:
        oracle_obs = _oracle_raw(probe, env, oracle, timeout)
    except subprocess.TimeoutExpired:
        return StrictRow(probe.id, ERROR, "oracle timed out")
    if not oracle_obs.stdout.startswith(b"ok "):
        tail = oracle_obs.stdout.rstrip(b"\n")[:120]
        return StrictRow(probe.id, SKIP, f"oracle did not answer ok: {tail!r}")
    expected = Observation(oracle_obs.stdout[3:], oracle_obs.stderr, oracle_obs.returncode)
    with tempfile.TemporaryDirectory(prefix="pyscriptc-strict-ab-") as tmp:
        root = Path(tmp)
        src, binary = root / "probe.py", root / "probe"
        src.write_text(probe.source, encoding="utf-8")
        try:
            built = _compile(
                [str(src), "--emit", "bin", "--call", probe.call, "--args", repr(list(probe.args)), "-o", str(binary)],
                env,
                timeout,
            )
        except subprocess.TimeoutExpired:
            return StrictRow(probe.id, ERROR, "compile timed out")
        if built.returncode != 0:
            fence = _fenced(built.stderr) if built.returncode == 3 else None
            err = built.stderr.decode("utf-8", "replace").strip().splitlines()
            tail = err[-1] if err else f"exit {built.returncode}"
            verdict, note = (SKIP, f"fenced: {fence}") if fence else (ERROR, f"compile: {tail}")
            return StrictRow(probe.id, verdict, note)
        if not binary.is_file():
            return StrictRow(probe.id, ERROR, "compile produced no binary")
        try:
            native_obs = _run_binary(binary, root, run_timeout)
        except subprocess.TimeoutExpired:
            return StrictRow(probe.id, ERROR, "run timed out")
    verdict, note = _verdict(native_obs, expected)
    return StrictRow(
        probe.id,
        verdict,
        note,
        native_obs,
        expected,
        stderr_class(native_obs.stderr),
        stderr_class(expected.stderr),
    )


def _run_binary(binary: Path, run_root: Path, timeout: float) -> Observation:
    run_dir = run_root / "run"
    run_dir.mkdir(exist_ok=True)
    return _run_bin(binary, run_dir, timeout)


def compare_probe(probe: Probe, oracle: str, timeout: float, run_timeout: float) -> StrictRow:
    category = probe.category
    if category == "function":
        return compare_function(probe, oracle, timeout, run_timeout)
    if str(probe.id).startswith("top100."):
        return StrictRow(probe.id, SKIP, "package program is compile-only in the scorer; no output to compare")
    return compare_script(probe, oracle, timeout, run_timeout)


def compare_probes(
    probes: list[Probe] | None = None,
    oracle: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    run_timeout: float = RUN_TIMEOUT,
    workers: int = 4,
) -> list[StrictRow]:
    rows = probes if probes is not None else default_probes()
    oracle = oracle if oracle is not None else resolve_oracle()
    if oracle is None:
        return [StrictRow(getattr(p, "id", str(p)), ERROR, "no pinned CPython 3.14 oracle") for p in rows]
    if workers <= 1:
        return [compare_probe(p, oracle, timeout, run_timeout) for p in rows]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda p: compare_probe(p, oracle, timeout, run_timeout), rows))


def _counts(rows: list[StrictRow]) -> str:
    tally = {v: sum(1 for r in rows if r.verdict == v) for v in (PASS, DIFF, ERROR, SKIP)}
    return f"{len(rows)} probes: {tally[PASS]} {PASS}, {tally[DIFF]} {DIFF}, "
    f"{tally[ERROR]} {ERROR}, {tally[SKIP]} {SKIP}"


def render_table(rows: list[StrictRow]) -> str:
    """A per-probe verdict table (stdout of the tool)."""
    headers = ("probe", "stdout", "stderr class", "exit", "verdict", "note")
    body: list[tuple[str, str, str, str, str, str]] = []
    for row in rows:
        stdout = stderr = exit_cell = "-"
        if row.native is not None and row.cpython is not None:
            stdout = "match" if row.native.stdout == row.cpython.stdout else "DIFF"
            stderr = row.native_stderr_class
            if row.native_stderr_class != row.cpython_stderr_class:
                stderr = f"{row.cpython_stderr_class}!={row.native_stderr_class}"
            if row.native.returncode == row.cpython.returncode:
                exit_cell = str(row.native.returncode)
            else:
                exit_cell = f"{row.cpython.returncode}!={row.native.returncode}"
        body.append((row.probe_id, stdout, stderr, exit_cell, row.verdict, row.note))
    widths = [len(h) for h in headers]
    for cells in body:
        for i, cell in enumerate(cells):
            widths[i] = max(widths[i], len(cell))
    lines = [
        "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)),
        "  ".join("-" * w for w in widths),
    ]
    lines.extend("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells)) for cells in body)
    lines.extend(("", _counts(rows)))
    return "\n".join(lines) + "\n"


def render_summary(rows: list[StrictRow]) -> str:
    """GitHub-flavoured markdown table for the job summary."""
    lines = [
        "### Strict corpus A/B (unstripped bytes, report only)",
        "",
        "| probe | stdout | stderr class | exit | verdict |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        stdout = stderr = exit_cell = "-"
        if row.native is not None and row.cpython is not None:
            stdout = "match" if row.native.stdout == row.cpython.stdout else "DIFF"
            stderr = row.native_stderr_class
            if row.native_stderr_class != row.cpython_stderr_class:
                stderr = f"{row.cpython_stderr_class}!={row.native_stderr_class}"
            exit_cell = (
                str(row.native.returncode)
                if row.native.returncode == row.cpython.returncode
                else f"{row.cpython.returncode}!={row.native.returncode}"
            )
        lines.append(f"| {row.probe_id} | {stdout} | {stderr} | {exit_cell} | {row.verdict} |")
    lines.extend(("", _counts(rows), ""))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", help="run only probe ids containing this substring")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="compile/oracle timeout seconds")
    parser.add_argument("--run-timeout", type=float, default=RUN_TIMEOUT, help="binary run timeout seconds")
    parser.add_argument("--json-out", help="write the JSON report here")
    parser.add_argument("--summary", help="append a markdown table to this file")
    args = parser.parse_args(argv)

    if not has_c_toolchain():
        print("no C toolchain (zig, clang, cc or gcc): strict A/B needs compile-to-binary", file=sys.stderr)
        return 2
    probes = default_probes()
    if args.only:
        probes = [p for p in probes if args.only in p.id]
    if not probes:
        print(f"no probes match {args.only!r}", file=sys.stderr)
        return 2
    started = time.monotonic()
    rows = compare_probes(probes, timeout=args.timeout, run_timeout=args.run_timeout, workers=args.workers)
    report = {
        "lane": "strict-corpus-ab",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "seconds": round(time.monotonic() - started, 3),
        "counts": _counts(rows),
        "rows": [row.as_dict() for row in rows],
    }
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as fh:
            fh.write(render_summary(rows))
    print(render_table(rows), end="")
    # Report only: a DIFF row is a finding for the ledger, never a failure.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
