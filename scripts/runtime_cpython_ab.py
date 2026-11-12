#!/usr/bin/env python3
"""CPython-baselined runtime check for the MIR lane (epic #817).

The correctness baseline for the MIR lane is CPython behavior, not byte or
shape parity with the direct emitter (owner direction 2026-11-06, see
`.agents/plans/2026-11-06-v090-user-centered-flip.md`). This tool compiles
each program once through the MIR lane (`PYSC_MIR=1`), runs the native
binary, and runs the same script under the repo's pinned CPython 3.14
oracle (`benchmarks.compat2.score.pinned_oracle`: `PYSC_ORACLE`, else
`PYSC_ORACLE_DIR`, else `uv python find 3.14`). It then compares native
stdout, the exit code and a coarse stderr class — never stderr bytes,
because CPython prints a traceback where the runtime prints one line.

The default program set is every `tests/fixtures/mir_leaf/*.py` fixture
plus the visible compat2 corpus scripts under
`benchmarks/compat2/corpus/scripts/*/main.py` that have at least one
in-slice function (`mirlower.supported_fn`). Programs with no in-slice
function, or whose module carries fences (which fail the compile on both
lanes, so there is nothing to compare), report SKIP; compile or lower
failures report ERROR. Extra paths can be passed on the command line.

Verdicts: PASS when stdout, exit code and stderr class all match the
oracle; FAIL on any behavioral divergence; ERROR when the check itself
could not run (lower/compile/tool failure). The exit status is nonzero on
any FAIL; ERROR rows are reported but do not fail the run.

Usage:
    uv run python scripts/runtime_cpython_ab.py
    uv run python scripts/runtime_cpython_ab.py path/to/prog.py --json
    uv run python scripts/runtime_cpython_ab.py --json-out out.json --summary "$GITHUB_STEP_SUMMARY"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

if __package__ in (None, ""):
    # invoked as `python scripts/runtime_cpython_ab.py`: put the repo root
    # on sys.path so `scripts.runtime_ab` and `benchmarks.compat2` resolve
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.runtime_ab import (
    Observation,
    _compile_bin,
    _run_bin,
    corpus_script_paths,
    has_c_toolchain,
    leaf_fixture_paths,
)

from pyscriptc.errors import IRError
from pyscriptc.lower import lower_file
from pyscriptc.mirlower import supported_fn

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"
ERROR = "ERROR"

DEFAULT_TIMEOUT = 120.0
RUN_TIMEOUT = 60.0

_EXC_HEAD = re.compile(r"^([A-Za-z_][\w.]*)")


@dataclass(frozen=True)
class BaselineVerdict:
    """One program's MIR-vs-CPython result."""

    path: Path
    verdict: str
    in_slice: int
    functions: int
    note: str
    cpython: Observation | None = None
    mir: Observation | None = None
    cpython_stderr_class: str = "-"
    mir_stderr_class: str = "-"

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
            "cpython_stderr_class": self.cpython_stderr_class,
            "mir_stderr_class": self.mir_stderr_class,
        }
        if self.cpython is not None:
            row["cpython"] = _obs_dict(self.cpython)
        if self.mir is not None:
            row["mir"] = _obs_dict(self.mir)
        return row


def _obs_dict(obs: Observation) -> dict[str, object]:
    return {
        "stdout": obs.stdout.decode("utf-8", "replace"),
        "stderr": obs.stderr.decode("utf-8", "replace"),
        "returncode": obs.returncode,
    }


def stderr_class(stderr: bytes) -> str:
    """Coarse stderr bucket: `none`, `exc:<Name>` or `other`.

    CPython reports an uncaught exception as a traceback whose last line is
    `ExcName: message`; the runtime's last-resort handler prints the same
    `ExcName[: message]` shape as its only line, so the exception name is
    the comparable class. Anything else non-empty is `other`.
    """
    text = stderr.decode("utf-8", "replace")
    if not text.strip():
        return "none"
    last = next(line.strip() for line in reversed(text.splitlines()) if line.strip())
    match = _EXC_HEAD.match(last)
    head = match.group(1) if match else ""
    if "Traceback (most recent call last)" in text:
        return f"exc:{head or 'unknown'}"
    if head[:1].isupper():
        return f"exc:{head}"
    return "other"


def resolve_oracle() -> str | None:
    """The pinned CPython 3.14 oracle executable, or None when unavailable."""
    try:
        from benchmarks.compat2.score import pinned_oracle
    except ImportError:
        return None
    try:
        return pinned_oracle()
    except SystemExit:
        return None


def module_stats(path: Path) -> tuple[int, int, bool]:
    """(in-slice functions, total functions, module has fences) after `lower_file`."""
    module, _fences = lower_file(str(path))
    functions = module["functions"]
    in_slice = sum(1 for fn in functions if supported_fn(fn))
    return in_slice, len(functions), bool(module.get("fences"))


def default_programs() -> list[Path]:
    """Every leaf fixture, plus the visible corpus scripts with an in-slice function.

    Corpus scripts are filtered on `in_slice > 0` here (unlike
    `runtime_ab.default_programs`, which lists every script for its
    report-only nightly); `compare_program` still reports SKIP for fences
    or a lost slice race.
    """
    chosen = list(leaf_fixture_paths())
    for path in corpus_script_paths():
        try:
            in_slice, _total, _fenced = module_stats(path)
        except (IRError, OSError):
            continue
        if in_slice > 0:
            chosen.append(path)
    return sorted(chosen, key=lambda p: str(p))


def _run_oracle(source: Path, oracle: str, run_dir: Path, timeout: float) -> Observation:
    """Run `source` under the pinned CPython oracle in `run_dir`."""
    ran = subprocess.run(
        [oracle, str(source)],
        cwd=run_dir,
        capture_output=True,
        timeout=timeout,
        env={
            **os.environ,
            "PYTHONHASHSEED": "0",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        },
    )
    return Observation(ran.stdout, ran.stderr, ran.returncode)


def _channel_note(cpython: Observation, mir: Observation) -> str:
    mismatches: list[str] = []
    if cpython.stdout != mir.stdout:
        mismatches.append("stdout")
    if cpython.returncode != mir.returncode:
        mismatches.append(f"exit {cpython.returncode}!={mir.returncode}")
    cc, mc = stderr_class(cpython.stderr), stderr_class(mir.stderr)
    if cc != mc:
        mismatches.append(f"stderr class {cc}!={mc}")
    if not mismatches:
        return "match"
    return ", ".join(mismatches)


def compare_program(
    path: Path,
    oracle: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    run_timeout: float = RUN_TIMEOUT,
) -> BaselineVerdict:
    """Compile `path` on the MIR lane, run it and the CPython oracle, compare."""
    oracle = oracle if oracle is not None else resolve_oracle()
    if oracle is None:
        return BaselineVerdict(path, ERROR, 0, 0, "no pinned CPython 3.14 oracle (PYSC_ORACLE/PYSC_ORACLE_DIR/uv)")
    try:
        in_slice, functions, fenced = module_stats(path)
    except (IRError, OSError) as exc:
        return BaselineVerdict(path, ERROR, 0, 0, f"lower: {exc}")
    if in_slice == 0:
        return BaselineVerdict(path, SKIP, in_slice, functions, "no function where supported_fn holds")
    if fenced:
        return BaselineVerdict(path, SKIP, in_slice, functions, "module has fences; compile fails on both lanes")

    with tempfile.TemporaryDirectory(prefix="pyscriptc-cpython-ab-") as tmp:
        root = Path(tmp)
        build = root / "mir-build"
        cpython_run = root / "cpython-run"
        mir_run = root / "mir-run"
        for directory in (build, cpython_run, mir_run):
            directory.mkdir()
        mir_bin = build / path.stem
        try:
            mir_cc = _compile_bin(path, mir_bin, mir=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return BaselineVerdict(path, ERROR, in_slice, functions, "mir compile timed out")
        if mir_cc.returncode != 0:
            err = mir_cc.stderr.decode("utf-8", "replace").strip().splitlines()
            tail = err[-1] if err else f"exit {mir_cc.returncode}"
            return BaselineVerdict(path, ERROR, in_slice, functions, f"mir compile: {tail}")
        if not mir_bin.is_file():
            return BaselineVerdict(path, ERROR, in_slice, functions, "compile produced no binary")
        try:
            cpython_obs = _run_oracle(path, oracle, cpython_run, run_timeout)
            mir_obs = _run_bin(mir_bin, mir_run, run_timeout)
        except subprocess.TimeoutExpired:
            return BaselineVerdict(path, ERROR, in_slice, functions, "run timed out")
        note = _channel_note(cpython_obs, mir_obs)
        verdict = PASS if note == "match" else FAIL
        return BaselineVerdict(
            path,
            verdict,
            in_slice,
            functions,
            note,
            cpython_obs,
            mir_obs,
            stderr_class(cpython_obs.stderr),
            stderr_class(mir_obs.stderr),
        )


def compare_programs(
    programs: list[Path] | None = None,
    oracle: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    run_timeout: float = RUN_TIMEOUT,
) -> list[BaselineVerdict]:
    paths = programs if programs is not None else default_programs()
    return [compare_program(path, oracle=oracle, timeout=timeout, run_timeout=run_timeout) for path in paths]


def _display_names(rows: list[BaselineVerdict]) -> list[str]:
    """Per-row program labels; disambiguate same-named files with the parent dir."""
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


def _counts(rows: list[BaselineVerdict]) -> str:
    passed = sum(1 for row in rows if row.verdict == PASS)
    failed = sum(1 for row in rows if row.verdict == FAIL)
    errors = sum(1 for row in rows if row.verdict == ERROR)
    skipped = sum(1 for row in rows if row.verdict == SKIP)
    return f"{len(rows)} programs: {passed} {PASS}, {failed} {FAIL}, {errors} {ERROR}, {skipped} {SKIP}"


def render_table(rows: list[BaselineVerdict]) -> str:
    """A per-program verdict table (stdout of the tool)."""
    headers = ("program", "slice", "stdout", "stderr class", "exit", "verdict", "note")
    display = _display_names(rows)
    body: list[tuple[str, str, str, str, str, str, str]] = []
    for row, label in zip(rows, display, strict=True):
        stdout = stderr = exit_cell = "-"
        if row.cpython is not None and row.mir is not None:
            stdout = "match" if row.cpython.stdout == row.mir.stdout else "DIFF"
            stderr = f"{row.cpython_stderr_class}={row.mir_stderr_class}"
            if row.cpython_stderr_class == row.mir_stderr_class:
                stderr = row.cpython_stderr_class
            if row.cpython.returncode == row.mir.returncode:
                exit_cell = str(row.cpython.returncode)
            else:
                exit_cell = f"{row.cpython.returncode}!={row.mir.returncode}"
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

    def fmt(cells: tuple[str, ...]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells))

    lines = [fmt(headers), "  ".join("-" * w for w in widths)]
    lines.extend(fmt(cells) for cells in body)
    lines.append("")
    lines.append(_counts(rows))
    return "\n".join(lines) + "\n"


def render_summary(rows: list[BaselineVerdict]) -> str:
    """Markdown per-program verdict table for a job summary."""
    lines = [
        "### Runtime CPython baseline (MIR vs CPython 3.14)",
        "",
        "Gating check: each row is one program's native stdout, exit code and stderr class "
        "against the pinned CPython oracle. Byte or shape parity with the direct emitter "
        "is not required.",
        "",
        "| program | slice | stdout | stderr class | exit | verdict | note |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        stdout = stderr = exit_cell = "-"
        if row.cpython is not None and row.mir is not None:
            stdout = "match" if row.cpython.stdout == row.mir.stdout else "DIFF"
            stderr = row.cpython_stderr_class
            exit_cell = str(row.cpython.returncode)
            if row.cpython.returncode != row.mir.returncode:
                exit_cell = f"{row.cpython.returncode}!={row.mir.returncode}"
            if row.cpython_stderr_class != row.mir_stderr_class:
                stderr = f"{row.cpython_stderr_class}!={row.mir_stderr_class}"
        note = row.note.replace("|", "\\|")
        label = row.name
        lines.append(
            f"| {label} | {row.in_slice}/{row.functions} | {stdout} | {stderr} | {exit_cell} | {row.verdict} | {note} |"
        )
    lines.append("")
    lines.append(_counts(rows))
    lines.append("")
    return "\n".join(lines)


def _exit_code(rows: list[BaselineVerdict]) -> int:
    if any(row.verdict == FAIL for row in rows):
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="CPython-baselined runtime check: compile each program on the MIR lane, "
        "run the binary and the pinned CPython 3.14 oracle, compare stdout/exit/stderr class."
    )
    parser.add_argument(
        "programs",
        nargs="*",
        type=Path,
        help="programs to check (default: tests/fixtures/mir_leaf/*.py plus the visible "
        "corpus scripts with an in-slice function)",
    )
    parser.add_argument("--oracle", help="CPython executable (default: the pinned 3.14 oracle)")
    parser.add_argument("--json", action="store_true", help="print machine-readable rows instead of the table")
    parser.add_argument("--json-out", type=Path, help="write machine-readable rows to this path")
    parser.add_argument(
        "--summary",
        type=Path,
        help="append a markdown per-program verdict table to this file (GITHUB_STEP_SUMMARY)",
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="compile timeout in seconds")
    parser.add_argument("--run-timeout", type=float, default=RUN_TIMEOUT, help="run timeout in seconds")
    args = parser.parse_args(argv)
    if not has_c_toolchain():
        print(
            "no C toolchain found on PATH (neither zig nor clang), and the CPython-baselined check needs a C toolchain",
            file=sys.stderr,
        )
        return 2
    oracle = args.oracle or resolve_oracle()
    if oracle is None:
        print(
            "no pinned CPython 3.14 oracle: set PYSC_ORACLE=/path/to/python3.14, "
            "set PYSC_ORACLE_DIR, or run `uv python install 3.14`",
            file=sys.stderr,
        )
        return 2
    programs = [path.resolve() for path in args.programs] if args.programs else None
    rows = compare_programs(programs, oracle=oracle, timeout=args.timeout, run_timeout=args.run_timeout)
    payload = {
        "oracle": oracle,
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
