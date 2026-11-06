#!/usr/bin/env python3
"""Runtime A/B harness: direct emitter vs opt-in MIR lane (epic #817, task B9).

This is the emit-flip release gate. It does not change either lane. For each
program it compiles once through the default (direct) emitter and once with
`PYSC_MIR` set, runs the two binaries in isolated temp directories, and diffs
stdout, stderr and the return code.

The default corpus is the MIR leaf fixtures under `tests/fixtures/mir_leaf/`
that have at least one in-slice function (`mirlower.supported_fn`), plus every
visible compat2 corpus script under `benchmarks/compat2/corpus/scripts/*/main.py`
(the scripts split only; the holdout is never touched). Corpus scripts without
an in-slice function report SKIP; ones that fail to lower or compile report
ERROR with the compiler's note. Extra paths can be passed on the command line.

Usage:
    uv run python scripts/runtime_ab.py
    uv run python scripts/runtime_ab.py path/to/prog.py --json
    uv run python scripts/runtime_ab.py --json-out out.json --summary "$GITHUB_STEP_SUMMARY"
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pyscriptc.errors import IRError
from pyscriptc.lower import lower_file
from pyscriptc.miremit import MIR_ENV
from pyscriptc.mirlower import supported_fn
from pyscriptc.zigshim import resolve_zig

ROOT = Path(__file__).resolve().parents[1]
LEAF_DIR = ROOT / "tests" / "fixtures" / "mir_leaf"
CORPUS_SCRIPTS = ROOT / "benchmarks" / "compat2" / "corpus" / "scripts"
DEFAULT_TIMEOUT = 120.0
RUN_TIMEOUT = 60.0

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"
ERROR = "ERROR"


@dataclass(frozen=True)
class Observation:
    """What one compiled binary produced."""

    stdout: bytes
    stderr: bytes
    returncode: int


@dataclass(frozen=True)
class ProgramVerdict:
    """One program's A/B result."""

    path: Path
    verdict: str
    in_slice: int
    functions: int
    note: str
    direct: Observation | None = None
    mir: Observation | None = None

    @property
    def name(self) -> str:
        return self.path.name

    def as_dict(self) -> dict[str, object]:
        row: dict[str, object] = {
            "program": str(self.path),
            "name": self.name,
            "verdict": self.verdict,
            "in_slice": self.in_slice,
            "functions": self.functions,
            "note": self.note,
        }
        if self.direct is not None:
            row["direct"] = _obs_dict(self.direct)
        if self.mir is not None:
            row["mir"] = _obs_dict(self.mir)
        return row


def _obs_dict(obs: Observation) -> dict[str, object]:
    return {
        "stdout": obs.stdout.decode("utf-8", "replace"),
        "stderr": obs.stderr.decode("utf-8", "replace"),
        "returncode": obs.returncode,
    }


def has_c_toolchain() -> bool:
    """Whether compile-to-binary can find zig, clang, or a host cc."""
    if shutil.which("zig") is not None or shutil.which("clang") is not None:
        return True
    if shutil.which("cc") is not None or shutil.which("gcc") is not None:
        return True
    return resolve_zig() is not None


def leaf_fixture_paths() -> list[Path]:
    """The committed MIR leaf samples, sorted by name."""
    return sorted(path for path in LEAF_DIR.glob("*.py") if path.is_file())


def corpus_script_paths() -> list[Path]:
    """The visible compat2 script corpus `main.py` files, sorted.

    Same enumeration as `scripts/mir_coverage.py:script_mains`; duplicated
    here so this tool also runs standalone (`python scripts/runtime_ab.py`)
    where the `scripts` package is not importable. Never walks any other
    corpus split.
    """
    return sorted(path for path in CORPUS_SCRIPTS.glob("*/main.py") if path.is_file())


def slice_counts(path: Path) -> tuple[int, int]:
    """In-slice functions over total functions after `lower_file`."""
    module, _fences = lower_file(str(path))
    functions = module["functions"]
    in_slice = sum(1 for fn in functions if supported_fn(fn))
    return in_slice, len(functions)


def default_programs() -> list[Path]:
    """Leaf fixtures with an in-slice function, plus every visible corpus script.

    Corpus scripts are included unconditionally: the nightly A/B report should
    show each script's verdict (PASS/FAIL/SKIP/ERROR), not silently drop the
    ones the slice cannot reach yet.
    """
    chosen: list[Path] = []
    for path in leaf_fixture_paths():
        in_slice, _total = slice_counts(path)
        if in_slice > 0:
            chosen.append(path)
    chosen.extend(corpus_script_paths())
    return sorted(chosen, key=lambda p: str(p))


def lane_env(mir: bool) -> dict[str, str]:
    """Compile environment for one lane. Direct sets `PYSC_MIR=0` so a parent opt-in cannot leak."""
    env = os.environ.copy()
    src = str(ROOT / "src")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src if not existing else src + os.pathsep + existing
    env["PYTHONHASHSEED"] = "0"
    env[MIR_ENV] = "1" if mir else "0"
    return env


def _compile_bin(source: Path, output: Path, mir: bool, timeout: float) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(source),
            "--emit",
            "bin",
            "-o",
            str(output),
        ],
        cwd=ROOT,
        env=lane_env(mir),
        capture_output=True,
        timeout=timeout,
    )


def _run_bin(binary: Path, run_dir: Path, timeout: float) -> Observation:
    staged = run_dir / binary.name
    shutil.copy2(binary, staged)
    staged.chmod(staged.stat().st_mode | 0o111)
    ran = subprocess.run(
        [str(staged)],
        cwd=run_dir,
        capture_output=True,
        timeout=timeout,
        env={**os.environ, MIR_ENV: "0"},
    )
    return Observation(ran.stdout, ran.stderr, ran.returncode)


def _channel_note(direct: Observation, mir: Observation) -> str:
    mismatches: list[str] = []
    if direct.stdout != mir.stdout:
        mismatches.append("stdout")
    if direct.stderr != mir.stderr:
        mismatches.append("stderr")
    if direct.returncode != mir.returncode:
        mismatches.append(f"exit {direct.returncode}!={mir.returncode}")
    if not mismatches:
        return "match"
    return ", ".join(mismatches)


def compare_program(path: Path, timeout: float = DEFAULT_TIMEOUT, run_timeout: float = RUN_TIMEOUT) -> ProgramVerdict:
    """Compile `path` on both lanes, run both binaries, return the verdict."""
    try:
        in_slice, functions = slice_counts(path)
    except (IRError, OSError) as exc:
        return ProgramVerdict(path, ERROR, 0, 0, f"lower: {exc}")
    if in_slice == 0:
        return ProgramVerdict(path, SKIP, in_slice, functions, "no function where supported_fn holds")

    with tempfile.TemporaryDirectory(prefix="pyscriptc-runtime-ab-") as tmp:
        root = Path(tmp)
        direct_build = root / "direct-build"
        mir_build = root / "mir-build"
        direct_run = root / "direct-run"
        mir_run = root / "mir-run"
        for directory in (direct_build, mir_build, direct_run, mir_run):
            directory.mkdir()
        stem = path.stem
        direct_bin = direct_build / stem
        mir_bin = mir_build / stem
        try:
            direct_cc = _compile_bin(path, direct_bin, mir=False, timeout=timeout)
            mir_cc = _compile_bin(path, mir_bin, mir=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return ProgramVerdict(path, ERROR, in_slice, functions, "compile timed out")
        if direct_cc.returncode != 0:
            err = direct_cc.stderr.decode("utf-8", "replace").strip().splitlines()
            tail = err[-1] if err else f"exit {direct_cc.returncode}"
            return ProgramVerdict(path, ERROR, in_slice, functions, f"direct compile: {tail}")
        if mir_cc.returncode != 0:
            err = mir_cc.stderr.decode("utf-8", "replace").strip().splitlines()
            tail = err[-1] if err else f"exit {mir_cc.returncode}"
            return ProgramVerdict(path, ERROR, in_slice, functions, f"mir compile: {tail}")
        if not direct_bin.is_file() or not mir_bin.is_file():
            return ProgramVerdict(path, ERROR, in_slice, functions, "compile produced no binary")
        try:
            direct_obs = _run_bin(direct_bin, direct_run, run_timeout)
            mir_obs = _run_bin(mir_bin, mir_run, run_timeout)
        except subprocess.TimeoutExpired:
            return ProgramVerdict(path, ERROR, in_slice, functions, "run timed out")
        note = _channel_note(direct_obs, mir_obs)
        verdict = PASS if note == "match" else FAIL
        return ProgramVerdict(path, verdict, in_slice, functions, note, direct_obs, mir_obs)


def compare_programs(
    programs: list[Path] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    run_timeout: float = RUN_TIMEOUT,
) -> list[ProgramVerdict]:
    paths = programs if programs is not None else default_programs()
    return [compare_program(path, timeout=timeout, run_timeout=run_timeout) for path in paths]


def _display_names(rows: list[ProgramVerdict]) -> list[str]:
    """Per-row program labels; disambiguate same-named files (e.g. corpus `main.py`s) with the parent dir."""
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.name] = counts.get(row.name, 0) + 1
    names: list[str] = []
    for row in rows:
        if counts[row.name] > 1:
            names.append(f"{row.path.parent.name}/{row.name}")
        else:
            names.append(row.name)
    return names


def render_table(rows: list[ProgramVerdict]) -> str:
    """A per-program verdict table (stdout of the tool)."""
    headers = ("program", "slice", "stdout", "stderr", "exit", "verdict", "note")
    display = _display_names(rows)
    body: list[tuple[str, str, str, str, str, str, str]] = []
    for row, label in zip(rows, display, strict=True):
        stdout = stderr = exit_cell = "-"
        if row.direct is not None and row.mir is not None:
            stdout = "match" if row.direct.stdout == row.mir.stdout else "DIFF"
            stderr = "match" if row.direct.stderr == row.mir.stderr else "DIFF"
            exit_cell = f"{row.direct.returncode}={row.mir.returncode}"
            if row.direct.returncode != row.mir.returncode:
                exit_cell = f"{row.direct.returncode}!={row.mir.returncode}"
        body.append(
            (
                label,
                f"{row.in_slice}/{row.functions}",
                stdout,
                stderr,
                exit_cell,
                row.verdict,
                row.note,
            )
        )
    widths = [len(h) for h in headers]
    for cells in body:
        for i, cell in enumerate(cells):
            widths[i] = max(widths[i], len(cell))

    def fmt(cells: tuple[str, ...] | tuple[str, str, str, str, str, str, str]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells))

    lines = [fmt(headers), "  ".join("-" * w for w in widths)]
    lines.extend(fmt(cells) for cells in body)
    passed = sum(1 for row in rows if row.verdict == PASS)
    failed = sum(1 for row in rows if row.verdict == FAIL)
    errors = sum(1 for row in rows if row.verdict == ERROR)
    skipped = sum(1 for row in rows if row.verdict == SKIP)
    lines.append("")
    lines.append(f"{len(rows)} programs: {passed} {PASS}, {failed} {FAIL}, {errors} {ERROR}, {skipped} {SKIP}")
    return "\n".join(lines) + "\n"


def render_summary(rows: list[ProgramVerdict]) -> str:
    """Markdown per-program verdict table for a job summary. Report only."""
    lines = [
        "### Runtime A/B (direct vs MIR)",
        "",
        "Report only: no threshold. Each row is one program's stdout, stderr and exit compared across lanes.",
        "",
        "| program | slice | stdout | stderr | exit | verdict | note |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        stdout = stderr = exit_cell = "-"
        if row.direct is not None and row.mir is not None:
            stdout = "match" if row.direct.stdout == row.mir.stdout else "DIFF"
            stderr = "match" if row.direct.stderr == row.mir.stderr else "DIFF"
            if row.direct.returncode == row.mir.returncode:
                exit_cell = str(row.direct.returncode)
            else:
                exit_cell = f"{row.direct.returncode}!={row.mir.returncode}"
        note = row.note.replace("|", "\\|")
        cell = f"{row.name} | {row.in_slice}/{row.functions} | {stdout} | {stderr}"
        lines.append(f"| {cell} | {exit_cell} | {row.verdict} | {note} |")
    passed = sum(1 for row in rows if row.verdict == PASS)
    failed = sum(1 for row in rows if row.verdict == FAIL)
    errors = sum(1 for row in rows if row.verdict == ERROR)
    skipped = sum(1 for row in rows if row.verdict == SKIP)
    lines.append("")
    lines.append(f"{len(rows)} programs: {passed} {PASS}, {failed} {FAIL}, {errors} {ERROR}, {skipped} {SKIP}")
    lines.append("")
    return "\n".join(lines)


def _exit_code(rows: list[ProgramVerdict]) -> int:
    if any(row.verdict in (FAIL, ERROR) for row in rows):
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Runtime A/B: compile each program on the direct emitter and the MIR lane, diff stdout/stderr/exit."
    )
    parser.add_argument(
        "programs",
        nargs="*",
        type=Path,
        help="programs to compare (default: tests/fixtures/mir_leaf/*.py with an in-slice function "
        "plus benchmarks/compat2/corpus/scripts/*/main.py)",
    )
    parser.add_argument("--json", action="store_true", help="print machine-readable rows instead of the table")
    parser.add_argument("--json-out", type=Path, help="write machine-readable rows to this path")
    parser.add_argument(
        "--summary",
        type=Path,
        help="append a markdown per-program verdict table to this file (GITHUB_STEP_SUMMARY)",
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="compile timeout in seconds")
    parser.add_argument("--run-timeout", type=float, default=RUN_TIMEOUT, help="binary run timeout in seconds")
    args = parser.parse_args(argv)
    if not has_c_toolchain():
        print(
            "no C toolchain found on PATH (neither zig nor clang), and the runtime A/B harness needs a C toolchain",
            file=sys.stderr,
        )
        return 2
    programs = [path.resolve() for path in args.programs] if args.programs else None
    rows = compare_programs(programs, timeout=args.timeout, run_timeout=args.run_timeout)
    payload = {
        "rows": [row.as_dict() for row in rows],
        "exit": _exit_code(rows),
    }
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if args.summary is not None:
        with args.summary.open("a", encoding="utf-8") as handle:
            handle.write(render_summary(rows))
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        sys.stdout.write(render_table(rows))
    return _exit_code(rows)


if __name__ == "__main__":
    raise SystemExit(main())
