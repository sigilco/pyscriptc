#!/usr/bin/env python3
"""The engine conformance corpus runner (issue #670).

Compiles every corpus program into an island binary and byte-compares
stdout, stderr and the exit code with CPython running the same entry. The
runner iterates engine descriptors: every `*.toml` under `engines/` is one
engine of the matrix, so the PyPy leg activates when #668 ships a PyPy
descriptor — no workflow edit, no runner edit.

    uv run python -m benchmarks.engine_corpus.run [--engine DESCRIPTOR] [--only a,b] [--json OUT]

`--engine` runs one engine instead of the whole matrix. A descriptor opts
out of the matrix with a top-level `conformance = false` key (an engine
that does not implement the island seam yet): the runner reports it as
skipped and does not fail.

Exit code 1 when any program diverges on any engine. `--json` writes the
per-program, per-engine report.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROGRAMS = HERE / "programs"
ENGINES = HERE / "engines"

#: Timeout for one compile or one run, in seconds.
TIMEOUT = 300


@dataclass
class ProgramResult:
    name: str
    engine: str
    compile_ok: bool
    compile_stderr: str = ""
    run_ok: bool = False
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    cpython_stdout: str = ""
    cpython_stderr: str = ""
    cpython_exit_code: int = -1

    @property
    def ok(self) -> bool:
        return (
            self.compile_ok
            and self.run_ok
            and self.stdout == self.cpython_stdout
            and self.stderr == self.cpython_stderr
            and self.exit_code == self.cpython_exit_code
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "program": self.name,
            "engine": self.engine,
            "ok": self.ok,
            "compile_ok": self.compile_ok,
            "compile_stderr": self.compile_stderr,
            "run_ok": self.run_ok,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "cpython_stdout": self.cpython_stdout,
            "cpython_stderr": self.cpython_stderr,
            "cpython_exit_code": self.cpython_exit_code,
        }


def program_dirs() -> list[Path]:
    """Every corpus program directory, in name order."""
    return sorted(d for d in PROGRAMS.iterdir() if d.is_dir() and (d / "pyscriptc.toml").is_file())


def engine_descriptors() -> list[Path]:
    """Every engine descriptor of the matrix, in name order."""
    if not ENGINES.is_dir():
        return []
    return sorted(ENGINES.glob("*.toml"))


def engine_skipped(descriptor: Path) -> bool:
    """A descriptor opts out with a top-level `conformance = false` key."""
    from pyscriptc.island_engine import load_file

    return load_file(descriptor).extra.get("conformance") is False


def reference_run(program: Path, entry: str) -> tuple[str, str, int]:
    """CPython runs the entry from its own directory."""
    proc = subprocess.run(
        [sys.executable, entry],
        cwd=program,
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
    )
    return proc.stdout, proc.stderr, proc.returncode


def compile_program(program: Path, entry: str, binary: Path, engine: Path | None) -> tuple[bool, str]:
    """Compile the entry into an island binary; False plus stderr on refusal."""
    args = [
        sys.executable,
        "-m",
        "pyscriptc",
        "compile",
        entry,
        "--island",
        # The matrix measures an island binary on the build machine, where
        # the host libpython link is the point: the audit's opt-out keeps
        # the measurement running (issue #1247).
        "--allow-host-libpython",
        "--emit",
        "bin",
        "-o",
        str(binary),
    ]
    if engine is not None:
        args += ["--island-engine", str(engine)]
    proc = subprocess.run(
        args,
        cwd=program,
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        env={**os.environ, "PYSC_PROGRAM_DIR": str(program)},
    )
    return proc.returncode == 0, proc.stderr


def run_binary(binary: Path, program: Path) -> tuple[str, str, int]:
    """Run the binary with the program dir pointed at the sources."""
    proc = subprocess.run(
        [str(binary)],
        cwd=program,
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        env={**os.environ, "PYSC_PROGRAM_DIR": str(program)},
    )
    return proc.stdout, proc.stderr, proc.returncode


def run_engine(
    engine: Path | None,
    label: str,
    selected: list[Path],
    work: Path,
) -> list[ProgramResult]:
    """Run every selected program on one engine; the per-program rows."""
    rows: list[ProgramResult] = []
    for program in selected:
        entry = f"{program.name}_entry.py"
        binary = work / f"{label}-{program.name}.bin"
        ref_out, ref_err, ref_rc = reference_run(program, entry)
        compile_ok, compile_stderr = compile_program(program, entry, binary, engine)
        row = ProgramResult(
            name=program.name,
            engine=label,
            compile_ok=compile_ok,
            compile_stderr=compile_stderr,
            cpython_stdout=ref_out,
            cpython_stderr=ref_err,
            cpython_exit_code=ref_rc,
        )
        if compile_ok:
            out, err, rc = run_binary(binary, program)
            row.run_ok = True
            row.stdout = out
            row.stderr = err
            row.exit_code = rc
        rows.append(row)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--engine", help="run one engine descriptor instead of the whole matrix")
    parser.add_argument("--only", help="comma-separated program names")
    parser.add_argument("--json", dest="json_path", help="write the report to this path")
    args = parser.parse_args(argv)

    selected = program_dirs()
    if args.only:
        names = {n.strip() for n in args.only.split(",")}
        selected = [d for d in selected if d.name in names]

    if args.engine is not None:
        engines = [Path(args.engine)]
    else:
        engines = engine_descriptors()
    if not engines:
        print(f"no engine descriptors under {ENGINES}", file=sys.stderr)
        return 2

    failed = False
    results: list[ProgramResult] = []
    skipped: list[str] = []
    with tempfile.TemporaryDirectory(prefix="engine-corpus-") as tmp:
        work = Path(tmp)
        for descriptor in engines:
            label = descriptor.stem
            if descriptor.is_file() and engine_skipped(descriptor):
                print(f"{label}: conformance = false, skipped")
                skipped.append(label)
                continue
            rows = run_engine(descriptor, label, selected, work)
            results += rows
            for row in rows:
                state = "ok" if row.ok else "DIFF"
                print(f"{label}/{row.name:12s} {state}")
                if not row.ok:
                    failed = True
                    if not row.compile_ok:
                        for line in row.compile_stderr.splitlines()[:6]:
                            print(f"  compile: {line}")
                    else:
                        print(f"  binary rc={row.exit_code} stdout={row.stdout!r} stderr={row.stderr!r}")
                        print(
                            f"  cpython rc={row.cpython_exit_code} "
                            f"stdout={row.cpython_stdout!r} stderr={row.cpython_stderr!r}"
                        )

    if args.json_path:
        report = {
            "engines": sorted({row.engine for row in results}),
            "skipped": skipped,
            "programs": [row.as_dict() for row in results],
            "passed": sum(1 for row in results if row.ok),
            "total": len(results),
        }
        Path(args.json_path).write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")

    passed = sum(1 for row in results if row.ok)
    print(f"{passed}/{len(results)} program runs match CPython ({len(skipped)} engine(s) skipped)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
