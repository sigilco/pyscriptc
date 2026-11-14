#!/usr/bin/env python3
"""The island probe: every pinned package, built on the island tier and diffed.

The fence census (`pyscriptc coverage`) answers what blocks a package from
lowering natively. It does not answer whether the package runs. This probe
does: for each pinned package it compiles one consumer program with
`--fallback=cpython --embed-python=host`, runs the emitted binary, runs the
same program under CPython, and requires the two to agree on stdout and exit
code.

The consumer programs live in `island_consumers.json` next to this file. One
per package, each exercising one entry point and printing a portable result.

  uv run python -m benchmarks.compat.top100.island_probe
  uv run python -m benchmarks.compat.top100.island_probe --json island.json

The probe is opt-in and slow: it compiles 18 programs. It needs a CPython
3.14 interpreter with the host `libpython` visible, which the runner locates
through `sysconfig` and passes on as `LIBRARY_PATH` and `LD_LIBRARY_PATH`.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CONSUMERS = HERE / "island_consumers.json"
SRC = HERE / "src"

RUN_TIMEOUT = 120.0  # seconds per program run, both sides
COMPILE_TIMEOUT = 900.0  # one compile, cold toolchain cache budget


def _lib_dir() -> str:
    """The directory holding the host `libpython`, from the running interpreter.

    A virtual environment inherits `sysconfig` from the base install, so the
    reported `LIBDIR` can name a directory that does not exist. The directory
    is only returned when it really holds the shared library the linker needs.
    """
    candidates = []
    libdir = sysconfig.get_config_var("LIBDIR") or ""
    if libdir:
        candidates.append(Path(libdir))
    prefix = Path(sys.base_prefix) / "lib"
    candidates.append(prefix)
    name = sysconfig.get_config_var("INSTSONAME") or ""
    for candidate in candidates:
        if name and (candidate / name).exists():
            return str(candidate)
    return ""


def _spawn(command: list[str], *, env: dict[str, str], timeout: float) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603
            command, capture_output=True, text=True, env=env, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(command, returncode=124, stdout="", stderr="timed out")


def _environment() -> dict[str, str]:
    """The environment the compiles and the runs need."""
    env = dict(os.environ)
    libdir = _lib_dir()
    if libdir:
        env["LIBRARY_PATH"] = os.pathsep.join(filter(None, [libdir, env.get("LIBRARY_PATH", "")]))
        env["LD_LIBRARY_PATH"] = os.pathsep.join(filter(None, [libdir, env.get("LD_LIBRARY_PATH", "")]))
    # The pinned trees are the importable packages, so the consumer programs
    # find them on the path.
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(SRC), env.get("PYTHONPATH", "")]))
    # The run cache would hide a change in the compiler under a stale binary.
    env["PYSC_NO_RUN_CACHE"] = "1"
    return env


def _compile(source: Path, target: Path, env: dict[str, str]) -> tuple[bool, str]:
    """Compile one consumer program with the island fallback. Returns built and the tail of the output."""
    command = [
        sys.executable,
        "-m",
        "pyscriptc",
        "compile",
        str(source),
        "--fallback=cpython",
        "--embed-python=host",
        # The probe measures an island binary on the build machine, where
        # the host libpython link is the point: the audit's opt-out keeps
        # the measurement running (issue #1247).
        "--allow-host-libpython",
        "--emit",
        "bin",
        "-o",
        str(target),
        "--skip-check",
    ]
    proc = subprocess.run(  # noqa: S603
        command, capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=COMPILE_TIMEOUT, check=False
    )
    built = proc.returncode == 0 and target.exists()
    tail = " | ".join((proc.stderr or proc.stdout).strip().splitlines()[-3:])
    return built, tail


def _probe(name: str, program: str, env: dict[str, str], workdir: Path) -> dict[str, Any]:
    """Build and run one consumer program, then diff it against CPython."""
    source = workdir / f"consumer_{name.replace('-', '_')}.py"
    source.write_text(program, encoding="utf-8")
    target = workdir / f"consumer_{name.replace('-', '_')}"
    built, tail = _compile(source, target, env)
    row: dict[str, Any] = {"name": name, "built": built, "runs": False}
    if not built:
        row["detail"] = tail[:400]
        return row
    binary = subprocess.run(  # noqa: S603
        [str(target)], capture_output=True, text=True, env=env, cwd=str(workdir), timeout=RUN_TIMEOUT, check=False
    )
    cpython = subprocess.run(  # noqa: S603
        [sys.executable, str(source)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(workdir),
        timeout=RUN_TIMEOUT,
        check=False,
    )
    row["binaryExit"] = binary.returncode
    row["cpythonExit"] = cpython.returncode
    row["runs"] = binary.returncode == cpython.returncode == 0 and binary.stdout.strip() == cpython.stdout.strip()
    if not row["runs"]:
        row["detail"] = (
            f"exit {binary.returncode}/{cpython.returncode}; "
            f"stdout {binary.stdout.strip()[:80]!r} vs {cpython.stdout.strip()[:80]!r}; "
            f"stderr {binary.stderr.strip()[-160:]!r} vs {cpython.stderr.strip()[-160:]!r}"
        )[:400]
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description="the island probe: every pinned package, built and diffed")
    parser.add_argument("--json", metavar="PATH", help="write the JSON summary to this path")
    parser.add_argument("--package", action="append", default=[], help="probe only this package (repeatable)")
    args = parser.parse_args()

    document = json.loads(CONSUMERS.read_text(encoding="utf-8"))
    consumers = document["consumers"]
    names = [name for name in sorted(consumers) if not args.package or name in args.package]
    env = _environment()
    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as scratch:
        workdir = Path(scratch)
        # The one consumer that reads a file needs it beside the program.
        (workdir / "fixed.ini").write_text("[a]\nx=1\n", encoding="utf-8")
        for name in names:
            row = _probe(name, consumers[name], env, workdir)
            rows.append(row)
            state = "runs" if row["runs"] else ("built" if row["built"] else "blocked")
            print(f"{name:20} {state:8} {row.get('detail', '')[:120]}", flush=True)

    summary = {
        "schemaVersion": 1,
        "packages": len(rows),
        "built": sum(1 for row in rows if row["built"]),
        "runs": sum(1 for row in rows if row["runs"]),
        "results": rows,
    }
    if args.json:
        Path(args.json).write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"{summary['runs']} of {summary['packages']} packages build and run on the island tier")
    return 0 if summary["runs"] == summary["packages"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
