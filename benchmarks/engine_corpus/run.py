#!/usr/bin/env python3
"""The engine conformance corpus runner (issue #670).

Compiles every corpus program into an island binary and byte-compares
stdout, stderr and the exit code with CPython running the same entry. The
`--engine <descriptor>` flag selects an island engine for every compile, so
the same corpus runs on every shipped engine: the built-in CPython today,
the PyPy pack through its descriptor when #668 lands one. A descriptor may
also declare `conformance = false` in `[engine]` to be skipped by the
runner (an engine that does not implement the island seam yet).

    uv run python -m benchmarks.engine_corpus.run [--engine DESCRIPTOR] [--only a,b] [--json OUT]

Exit code 1 when any program diverges. `--json` writes the per-program
report; the PyPy leg of the CI job activates automatically once a PyPy
descriptor ships (#668): the workflow iterates engine descriptors, so a new
descriptor directory entry is picked up without a workflow edit.
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
ROOT = HERE.parent.parent

#: Engines skipped by name without a descriptor file (`--engine cpython`).
SKIPPED_ENGINES = {"cpython"}

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


def compile_program(
    program: Path, entry: str, binary: Path, engine: str | None
) -> tuple[bool, str]:
    """Compile the entry into an island binary; False plus stderr on refusal."""
    args = [sys.executable, "-m", "pyscriptc", "compile", entry, "--island", "--emit", "bin", "-o", str(binary)]
    if engine is not None and engine not in SKIPPED_ENGINES:
        args += ["--island-engine", engine]
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
    """Run the relocated binary with the program dir pointed at the sources."""
    proc = subprocess.run(
        [str(binary)],
        cwd=program,
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        env={**os.environ, "PYSC_PROGRAM_DIR": str(program)},
    )
    return proc.stdout, proc.stderr, proc.returncode


def engine_supported(engine: str | None) -> bool:
    """A descriptor may opt out with `conformance = false` in `[engine]`."""
    if engine is None or engine in SKIPPED_ENGINES:
        return True
    from pyscriptc.island_engine import load_file

    path = Path(engine)
    if not path.is_file():
        return True  # a name on PYSC_ISLAND_ENGINES: resolved at compile time
    descriptor = load_file(path)
    declared = descriptor.extra.get("conformance")
    if declared is False:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--engine", help="island engine descriptor (or `cpython` for the built-in)")
    parser.add_argument("--only", help="comma-separated program names")
    parser.add_argument("--json", dest="json_path", help="write the report to this path")
    args = parser.parse_args(argv)

    selected = program_dirs()
    if args.only:
        names = {n.strip() for n in args.only.split(",")}
        selected = [d for d in selected if d.name in names]

    if args.engine is not None and not engine_supported(args.engine):
        print(f"engine {args.engine}: conformance = false, skipped")
        if args.json_path:
            Path(args.json_path).write_text(
                json.dumps({"engine": args.engine, "skipped": True, "results": []}, indent=1) + "\n",
                encoding="utf-8",
            )
        return 0

    label = args.engine or "cpython"
    failed = False
    results: list[ProgramResult] = []
    with tempfile.TemporaryDirectory(prefix="engine-corpus-") as tmp:
        work = Path(tmp)
        for program in selected:
            entry = f"{program.name}_entry.py"
            binary = work / f"{program.name}.bin"
            ref_out, ref_err, ref_rc = reference_run(program, entry)
            compile_ok, compile_stderr = compile_program(program, entry, binary, args.engine)
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
            results.append(row)
            state = "ok" if row.ok else "DIFF"
            print(f"{program.name:12s} [{label}] {state}")
            if not row.ok:
                failed = True
                if not compile_ok:
                    for line in compile_stderr.splitlines()[:6]:
                        print(f"  compile: {line}")
                else:
                    print(f"  binary rc={row.exit_code} stdout={row.stdout!r} stderr={row.stderr!r}")
                    print(f"  cpython rc={ref_rc} stdout={ref_out!r} stderr={ref_err!r}")

    if args.json_path:
        report = {
            "engine": label,
            "programs": [row.as_dict() for row in results],
            "passed": sum(1 for row in results if row.ok),
            "total": len(results),
        }
        Path(args.json_path).write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")

    passed = sum(1 for row in results if row.ok)
    print(f"{passed}/{len(results)} programs match CPython on engine {label}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
