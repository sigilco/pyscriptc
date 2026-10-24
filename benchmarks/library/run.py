#!/usr/bin/env python3
"""The library receipt runner: certifi through the deps lane, compiled against CPython.

The receipt is issue #409: one PEP 723 script (`certifi_receipt.py`) that is
not vendored. The resolver pulls a real certifi wheel from PyPI into the
content-addressed deps cache, the compiler lowers it from the unpacked wheel
source, and the wheel's data file (`cacert.pem`) is staged beside the emitted
binary where `importlib.resources` finds it. The runner then runs the same
script under CPython, with the unpacked wheel's source root on `PYTHONPATH`,
and requires the two stdout runs to agree line for line.

  uv run python -m benchmarks.library.run
  uv run python -m benchmarks.library.run --json library.json

Opt-in: no CI job on the default path and no read of
`benchmarks.baseline.run --check` state. It touches the network only when the
deps cache is cold (one wheel, verified against the committed lock's
sha256); the compile needs zig or clang.

The summary counts fences from the emitted IR next to the binary: the receipt
claims zero fences, so the runner reports the number instead of asserting it,
and exits 1 on any of (lane, compile, match) failures.
"""

from __future__ import annotations

import argparse
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
SCRIPT = HERE / "certifi_receipt.py"

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


def _fence_summary(binary: Path) -> dict[str, Any]:
    """Fence census from the IR written beside the binary (`<o>.ir.json`)."""
    ir = Path(f"{binary}.ir.json")
    if not ir.is_file():
        return {"total": None, "note": f"{ir.name} not found next to the binary"}
    fences = json.loads(ir.read_text(encoding="utf-8"))["fences"]
    return {"total": len(fences), "ids": sorted({f["id"] for f in fences})}


def main() -> int:
    parser = argparse.ArgumentParser(description="the library receipt: certifi compiled and diffed against CPython")
    parser.add_argument("--json", metavar="PATH", help="write the JSON summary to this path")
    args = parser.parse_args()

    report = deps.run_deps(SCRIPT, cache_dir=os.environ.get("PYSC_CACHE_DIR"))
    certifi = next((p for p in report["packages"] if p["name"] == "certifi"), None)
    if certifi is None:
        print(f"FAIL: the lock does not hold certifi: {report['packages']}", file=sys.stderr)
        return 1
    if certifi["lane"] != "pure":
        print(f"FAIL: certifi resolved to lane {certifi['lane']}: {certifi['reason']}", file=sys.stderr)
        return 1
    source_root = Path(certifi["source_root"])

    summary: dict[str, Any] = {
        "generated": datetime.now(UTC).isoformat(),
        "script": "benchmarks/library/certifi_receipt.py",
        "lockProvenance": report["lockProvenance"],
        "certifi": {
            "version": certifi["version"],
            "wheel": certifi["wheel"],
            "sha256": certifi["sha256"],
            "lane": certifi["lane"],
            "reason": certifi["reason"],
        },
        "sourceRoot": str(source_root),
        "fences": {"total": None, "note": "compile never ran"},
    }

    with tempfile.TemporaryDirectory(prefix="ps-library-receipt-") as tmp:
        workdir = Path(tmp)
        binary = workdir / "certifi-receipt"
        done = _spawn(
            [
                sys.executable,
                "-m",
                "pyscriptc",
                "compile",
                str(SCRIPT),
                "--skip-check",
                "--run",
                "--emit",
                "bin",
                "-o",
                str(binary),
            ],
            env=dict(os.environ),
            timeout=COMPILE_TIMEOUT,
        )
        summary["compile"] = {"rc": done.returncode}
        if done.returncode != 0 or not binary.is_file():
            summary["compile"]["detail"] = f"compile failed:\n{done.stdout}{done.stderr}"
            print(summary["compile"]["detail"], file=sys.stderr)
            return _finish(summary, args.json, failure=True)
        summary["fences"] = _fence_summary(binary)
        print(f"fences: {summary['fences']['total']}")

        # CPython side: the same script, certifi imported from the unpacked
        # wheel's source root (the deps lane's own copy, not site-packages).
        reference = _spawn([sys.executable, str(SCRIPT)], env={**os.environ, "PYTHONPATH": str(source_root)})
        probe = _spawn([str(binary)], env=dict(os.environ))
        matched = reference.returncode == probe.returncode == 0 and reference.stdout == probe.stdout
        summary["output"] = {
            "match": matched,
            "returncode": probe.returncode,
            "lines": probe.stdout.strip().splitlines(),
            "reference": reference.stdout.strip(),
        }
        if not matched:
            print(
                f"FAIL: output mismatch\ncompiled: {probe.stdout!r} rc={probe.returncode}\n"
                f"cpython:  {reference.stdout!r} rc={reference.returncode}\n{probe.stderr[-400:]}",
                file=sys.stderr,
            )
            return _finish(summary, args.json, failure=True)
        for line in summary["output"]["lines"]:
            print(line)

    return _finish(summary, args.json, failure=False)


def _finish(summary: dict[str, Any], json_path: str | None, *, failure: bool) -> int:
    summary["ok"] = not failure
    if json_path:
        Path(json_path).write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return 1 if failure else 0


if __name__ == "__main__":
    sys.exit(main())
