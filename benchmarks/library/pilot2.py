#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Pilot 2 runner (issue #417): iniconfig from PyPI, fence profile and CPython diff.

The program is `iniconfig_pilot.py`, a PEP 723 script naming `iniconfig` and
nothing else; the committed `pylock.iniconfig_pilot.toml` pins the wheel. The
runner takes the same path a user does, with no vendoring and no path edits:

1. `deps.run_deps` resolves the lock and materialises the wheel source root;
   the package must land on the `pure` lane.
2. `pyscriptc compile --emit ir --debug-dir` records the reachable fence
   profile: what the import graph of the program actually reaches, after
   pruning (the number that decides whether the pilot compiles).
3. When the profile is empty, `pyscriptc compile --emit bin` builds the binary,
   and both it and CPython (the wheel source root on `PYTHONPATH`) run the
   script; stdout and the exit code must agree byte for byte.

  uv run python -m benchmarks.library.pilot2
  uv run python -m benchmarks.library.pilot2 --json pilot2.json

Exit 0 only when the binary matches CPython. While fences remain the runner
prints the profile and exits 1; the profile is the measurement the pilot
records, so it is reported in full rather than asserted.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pyscriptc import deps

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "iniconfig_pilot.py"
PACKAGE = "iniconfig"

RUN_TIMEOUT = 60.0  # seconds per process run, both sides
COMPILE_TIMEOUT = 600.0  # one compile, cold toolchain cache budget


def _spawn(
    command: list[str], *, env: dict[str, str] | None = None, timeout: float = RUN_TIMEOUT
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603
            command, capture_output=True, text=True, env=env, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(command, returncode=124, stdout="", stderr=f"timed out after {timeout:.0f}s")


def fence_profile(ir_path: Path, source_root: Path) -> dict[str, Any]:
    """The reachable fence profile from a compile's dumped `ir.json`.

    Counts by code, and by file relative to the wheel source root, so the
    program's own cascade (`ini is not a local`) is told apart from the
    package's root refusals.
    """
    fences = json.loads(ir_path.read_text(encoding="utf-8"))["fences"]
    by_file: collections.Counter[str] = collections.Counter()
    rows: list[dict[str, Any]] = []
    for fence in fences:
        span = fence.get("span") or {}
        path = Path(span.get("file", "?"))
        try:
            where = str(path.resolve().relative_to(source_root.resolve()))
        except ValueError:
            where = path.name
        by_file[where] += 1
        rows.append(
            {
                "code": fence["code"],
                "where": f"{where}:{span.get('line', '?')}",
                "function": fence.get("function"),
                "message": fence["message"],
            }
        )
    return {
        "total": len(fences),
        "byCode": dict(sorted(collections.Counter(f["code"] for f in fences).items())),
        "byFile": dict(sorted(by_file.items())),
        "fences": rows,
    }


def _compile(emit: str, out: Path, debug_dir: Path) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-m", "pyscriptc", "compile", str(SCRIPT), "--skip-check", "--emit", emit]
    command += ["-o", str(out), "--debug-dir", str(debug_dir)]
    return _spawn(command, env=dict(os.environ), timeout=COMPILE_TIMEOUT)


def main() -> int:
    parser = argparse.ArgumentParser(description="pilot 2: iniconfig compiled from PyPI and diffed against CPython")
    parser.add_argument("--json", metavar="PATH", help="write the JSON summary to this path")
    args = parser.parse_args()

    report = deps.run_deps(SCRIPT, cache_dir=os.environ.get("PYSC_CACHE_DIR"))
    package = next((p for p in report["packages"] if p["name"] == PACKAGE), None)
    if package is None:
        print(f"FAIL: the lock does not hold {PACKAGE}: {report['packages']}", file=sys.stderr)
        return 1
    if package["lane"] != "pure":
        print(f"FAIL: {PACKAGE} resolved to lane {package['lane']}: {package['reason']}", file=sys.stderr)
        return 1
    source_root = Path(package["source_root"])
    summary: dict[str, Any] = {
        "generated": datetime.now(UTC).isoformat(),
        "script": "benchmarks/library/iniconfig_pilot.py",
        "lockProvenance": report["lockProvenance"],
        PACKAGE: {k: package[k] for k in ("version", "wheel", "sha256", "lane", "reason")},
        "sourceRoot": str(source_root),
    }

    with tempfile.TemporaryDirectory(prefix="ps-pilot2-") as tmp:
        workdir = Path(tmp)
        ir_dir = workdir / "ir"
        _compile("ir", workdir / "pilot.ir.json", ir_dir)
        ir = ir_dir / "ir.json"
        if not ir.is_file():
            print(f"FAIL: the IR compile wrote no {ir}", file=sys.stderr)
            return _finish(summary, args.json, failure=True)
        profile = fence_profile(ir, source_root)
        summary["fences"] = profile
        print(f"reachable fences: {profile['total']} {profile['byCode']}")
        for row in profile["fences"]:
            print(f"  {row['code']} {row['where']} {row['function']}: {row['message']}")
        if profile["total"]:
            summary["output"] = {"match": False, "note": "fences remain, the binary is not built"}
            return _finish(summary, args.json, failure=True)

        binary = workdir / "iniconfig-pilot"
        done = _compile("bin", binary, workdir / "bin")
        if done.returncode != 0 or not binary.is_file():
            print(f"FAIL: compile failed:\n{done.stdout}{done.stderr}", file=sys.stderr)
            return _finish(summary, args.json, failure=True)
        reference = _spawn([sys.executable, str(SCRIPT)], env={**os.environ, "PYTHONPATH": str(source_root)})
        probe = _spawn([str(binary)], env=dict(os.environ))
        matched = reference.returncode == probe.returncode == 0 and reference.stdout == probe.stdout
        summary["output"] = {"match": matched, "returncode": probe.returncode, "lines": probe.stdout.splitlines()}
        if not matched:
            print(
                f"FAIL: output mismatch\ncompiled: {probe.stdout!r} rc={probe.returncode}\n"
                f"cpython:  {reference.stdout!r} rc={reference.returncode}\n{probe.stderr[-400:]}",
                file=sys.stderr,
            )
            return _finish(summary, args.json, failure=True)
        print(probe.stdout, end="")
    return _finish(summary, args.json, failure=False)


def _finish(summary: dict[str, Any], json_path: str | None, *, failure: bool) -> int:
    summary["ok"] = not failure
    if json_path:
        Path(json_path).write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return 1 if failure else 0


if __name__ == "__main__":
    sys.exit(main())
