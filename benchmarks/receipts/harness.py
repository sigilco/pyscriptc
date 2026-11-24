# SPDX-License-Identifier: Apache-2.0
"""The receipt harness: compile, run, byte-compare, write a receipt.

One receipt per target. For each probe in the set the harness compiles
the program for the target, runs the binary through the runner that can
execute it, and compares the output byte for byte against the bytes the
pinned CPython 3.14 oracle produced for the same program.

The posture is the one the smoke script already has: a missing runner is
a skip with a reason, a wrong output is a failure. The harness never
invents a verdict.

The receipt document is the contract between the measurement and the
page. Its shape is documented in `.agents/docs/benchmarks/target-receipts.md`.

Design: .agents/docs/benchmarks/target-receipts.md
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from benchmarks.receipts.oracle import oracle_version, resolve_oracle
from benchmarks.receipts.probes import Probe, load_probes
from benchmarks.receipts.targets import TARGETS, Target, runner_for

ROOT = Path(__file__).resolve().parents[2]

# The schema version of a receipt document. A bump means the page and
# the schema validator change in the same PR.
SCHEMA = "1"

# How long a single compile or run may take, in seconds. An emulator
# (FEX on an arm64 container running an x86_64 zig, for example) makes a
# compile much slower than the toolchain itself is, so both are
# overridable without touching the code.
COMPILE_TIMEOUT = float(os.environ.get("PYSC_RECEIPT_COMPILE_TIMEOUT", "300"))
RUN_TIMEOUT = float(os.environ.get("PYSC_RECEIPT_RUN_TIMEOUT", "60"))


@dataclass
class ProbeRow:
    """One probe's verdict on one target."""

    id: str
    built: bool = False
    ran: bool = False
    match: bool = False
    # Why the row is what it is: the fence code, the runner that was
    # missing, the first byte that differed. Empty when the row matched.
    detail: str = ""
    # The bytes the target produced and the bytes the oracle produced,
    # both decoded for the reader. Only present when the row differs.
    expected: str = ""
    actual: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "built": self.built,
            "ran": self.ran,
            "match": self.match,
            "detail": self.detail,
        }


@dataclass
class TargetReceipt:
    """The receipt of one target."""

    target: str
    triple: str
    system: str
    arch: str
    # The runner the harness used, or "" when the row is compile-only.
    runner: str = ""
    # The method the receipt was measured with: "native", "qemu",
    # "wasmtime" or "compile-only".
    method: str = "compile-only"
    # Why the row could not run, when it could not.
    skip_reason: str = ""
    probes: list[ProbeRow] = field(default_factory=list)
    # The toolchain that built the row, for the receipt's provenance.
    toolchain: str = ""

    @property
    def built(self) -> int:
        """How many probes built."""

        return sum(1 for row in self.probes if row.built)

    @property
    def ran(self) -> int:
        """How many probes ran."""

        return sum(1 for row in self.probes if row.ran)

    @property
    def matched(self) -> int:
        """How many probes matched the oracle."""

        return sum(1 for row in self.probes if row.match)

    @property
    def total(self) -> int:
        """How many probes the target was measured with."""

        return len(self.probes)

    def to_json(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "triple": self.triple,
            "system": self.system,
            "arch": self.arch,
            "runner": self.runner,
            "method": self.method,
            "skip_reason": self.skip_reason,
            "toolchain": self.toolchain,
            "built": self.built,
            "ran": self.ran,
            "matched": self.matched,
            "total": self.total,
            "probes": [row.to_json() for row in self.probes],
        }


@dataclass
class ReceiptDocument:
    """The whole run: one receipt per target, plus the provenance."""

    generated: str
    commit: str
    # The oracle, as the interpreter's version. The path is the measuring
    # machine's directory layout and carries the account name with it, so
    # the document never publishes the path.
    oracle: str
    host: dict[str, str]
    targets: list[TargetReceipt] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "generated": self.generated,
            "commit": self.commit,
            "oracle": self.oracle,
            "host": self.host,
            "targets": [receipt.to_json() for receipt in self.targets],
        }


def git_commit() -> str:
    """The commit the measurement was taken on, or an empty string."""

    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip()


def compile_env() -> dict[str, str]:
    """The environment the compiler runs in.

    `PYTHONPATH` carries `src` so the harness works from a source
    checkout, and `PYTHONHASHSEED=0` so a set or dict iteration order
    cannot make a run differ from the next one of the same program.
    """

    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    src = str(ROOT / "src")
    env["PYTHONPATH"] = src if not existing else src + os.pathsep + existing
    env["PYTHONHASHSEED"] = "0"
    return env


def run_env() -> dict[str, str]:
    """The environment the target binary runs in."""

    env = os.environ.copy()
    env["PYTHONHASHSEED"] = "0"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def zig_version() -> str:
    """The zig version that built the rows, or an empty string."""

    try:
        out = subprocess.run(["zig", "version"], capture_output=True, text=True, check=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip()


def _fence_code(stderr: bytes) -> str:
    """The PS code when a compile ended in a fence, else an empty string."""

    import re

    match = re.search(r"PS\d{4}", stderr.decode("utf-8", "replace"))
    return match.group(0) if match else ""


def first_byte_diff(left: bytes, right: bytes) -> str:
    """Where two byte strings first differ, described in one line."""

    if left == right:
        return ""
    limit = min(len(left), len(right))
    for index in range(limit):
        if left[index] != right[index]:
            return f"byte {index}: expected {right[index]!r}, got {left[index]!r}"
    return f"length: expected {len(right)} bytes, got {len(left)}"


def compile_probe(probe: Probe, target: Target, workdir: Path) -> tuple[Path | None, ProbeRow]:
    """Compile one probe for one target.

    Returns the binary and the row. A compile that fails is a row with
    `built: false` and the reason; it is never a crash of the harness.
    """

    row = ProbeRow(id=probe.id)
    binary = workdir / f"{probe.id}"
    command = [
        sys.executable,
        "-m",
        "pyscriptc",
        "compile",
        str(probe.path),
        "--emit",
        "bin",
        "--skip-check",
        "--driver",
        "zig",
        "--target",
        target.triple,
        "-o",
        str(binary),
    ]
    try:
        proc = subprocess.run(
            command,
            cwd=ROOT,
            env=compile_env(),
            capture_output=True,
            timeout=COMPILE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        row.detail = "the compile timed out"
        return None, row
    except OSError as error:
        row.detail = f"the compiler could not run: {error}"
        return None, row
    if proc.returncode == 3:
        code = _fence_code(proc.stderr)
        row.detail = code or "the compiler fenced the program"
        return None, row
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        row.detail = tail[-1] if tail else "the compile failed"
        return None, row
    # The wasm target writes a `.wasm` beside the requested path.
    produced = binary
    if target.system == "wasi":
        produced = binary.with_suffix(".wasm")
    if not produced.exists() or produced.stat().st_size == 0:
        row.detail = "the compile produced no binary"
        return None, row
    row.built = True
    return produced, row


def run_probe(binary: Path, runner: str, workdir: Path) -> tuple[bytes, bytes, int, str]:
    """Run one binary and return its stdout, stderr, exit code and reason.

    A binary that could not be run at all is reported through the reason,
    with an empty stdout and a non-zero exit code.
    """

    command = [str(binary)] if runner == "native" else [runner, str(binary)]
    try:
        proc = subprocess.run(
            command,
            cwd=workdir,
            capture_output=True,
            timeout=RUN_TIMEOUT,
            env=run_env(),
        )
    except subprocess.TimeoutExpired:
        return b"", b"", 124, "the run timed out"
    except OSError as error:
        return b"", b"", 125, f"the runner could not run: {error}"
    return proc.stdout, proc.stderr, proc.returncode, ""


def measure_target(
    target: Target,
    probes: list[Probe],
    oracle_bytes: dict[str, bytes],
    runner: str,
) -> TargetReceipt:
    """Measure one target: build every probe, run what can run, compare.

    The expected bytes come from the oracle, so the comparison is the
    same program against the same reference on every target.
    """

    receipt = TargetReceipt(
        target=target.name,
        triple=target.triple,
        system=target.system,
        arch=target.arch,
        runner=runner,
        method=_method(runner),
        toolchain=zig_version(),
    )
    if not runner:
        receipt.skip_reason = target.compile_only_reason or "no runner for this target on this host"
    elif not runner_available(runner):
        # The runner exists for this target but is not installed on this
        # host. The row is a skip with a reason, the way the smoke script
        # treats a missing qemu: the build is still the measurement.
        receipt.method = "compile-only"
        receipt.skip_reason = f"{runner} is not installed on this host"
        runner = ""
    with tempfile.TemporaryDirectory(prefix="pyscriptc-receipt-") as tmp:
        workdir = Path(tmp)
        for probe in probes:
            binary, row = compile_probe(probe, target, workdir)
            if binary is None:
                receipt.probes.append(row)
                continue
            if not runner:
                # The row is compile-only on this host: the build is the
                # measurement, and the receipt says so.
                row.detail = receipt.skip_reason
                receipt.probes.append(row)
                continue
            stdout, stderr, code, reason = run_probe(binary, runner, workdir)
            if reason:
                row.detail = reason
                receipt.probes.append(row)
                continue
            if code != 0:
                row.detail = f"the program exited with {code}"
                receipt.actual = stderr.decode("utf-8", "replace")[:400]
                receipt.probes.append(row)
                continue
            row.ran = True
            expected = oracle_bytes.get(probe.id, probe.expected)
            if stdout == expected:
                row.match = True
            else:
                row.detail = first_byte_diff(stdout, expected)
                row.expected = expected.decode("utf-8", "replace")[:400]
                row.actual = stdout.decode("utf-8", "replace")[:400]
            receipt.probes.append(row)
    return receipt


def _method(runner: str) -> str:
    """The method a receipt was measured with, for the page's column."""

    if runner == "native":
        return "native"
    if runner.startswith("qemu-"):
        return "qemu"
    if runner == "wasmtime":
        return "wasmtime"
    return "compile-only"


def oracle_output(probe: Probe, oracle: str, workdir: Path) -> bytes:
    """The bytes the oracle prints for one probe.

    The oracle runs in its own directory, so a probe that leaves a file
    behind cannot change what the next side sees.
    """

    proc = subprocess.run(
        [oracle, str(probe.path)],
        cwd=workdir,
        capture_output=True,
        timeout=RUN_TIMEOUT,
        env=run_env(),
    )
    return proc.stdout


def measure_all(
    targets: list[Target] | None = None,
    probes: list[Probe] | None = None,
    oracle: str | None = None,
) -> ReceiptDocument:
    """Measure every target and return the receipt document.

    The oracle is resolved once: the same interpreter for every row, so
    the reference bytes are the same bytes everywhere.
    """

    chosen_targets = targets if targets is not None else list(TARGETS)
    chosen_probes = probes if probes is not None else load_probes()
    resolved = oracle or resolve_oracle()
    if resolved is None:
        raise RuntimeError(
            "no pinned CPython 3.14 oracle: set PYSC_ORACLE=/path/to/python3.14, "
            "or PYSC_ORACLE_DIR to a directory holding a cpython-3.14.* install"
        )
    with tempfile.TemporaryDirectory(prefix="pyscriptc-oracle-") as tmp:
        oracle_bytes = {probe.id: oracle_output(probe, resolved, Path(tmp)) for probe in chosen_probes}
    _version, oracle_full = oracle_version(resolved)
    document = ReceiptDocument(
        generated=datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d"),
        commit=git_commit(),
        oracle=oracle_full,
        host={
            "system": " ".join(platform_uname()),
            "zig": zig_version(),
        },
    )
    for target in chosen_targets:
        document.targets.append(measure_target(target, chosen_probes, oracle_bytes, runner_for(target)))
    return document


def platform_uname() -> tuple[str, ...]:
    """The host description, for the receipt's provenance."""

    import platform

    return (platform.system(), platform.machine())


def write_receipts(document: ReceiptDocument, outdir: Path) -> list[Path]:
    """Write one JSON file per target plus the combined document.

    The per-target files are what the plan asks for ("one JSON per
    target"). The combined file is what the page reads, so the page
    never has to stitch the parts together.
    """

    outdir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for receipt in document.targets:
        path = outdir / f"{receipt.target}.json"
        path.write_text(json.dumps(receipt.to_json(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append(path)
    combined = outdir / "receipts.json"
    combined.write_text(json.dumps(document.to_json(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    written.append(combined)
    return written


def summary_table(document: ReceiptDocument) -> str:
    """The receipt as a markdown table, for the job summary."""

    lines = [
        "| target | triple | method | built | ran | matched | total |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for receipt in document.targets:
        lines.append(
            f"| {receipt.target} | {receipt.triple} | {receipt.method} | "
            f"{receipt.built} | {receipt.ran} | {receipt.matched} | {receipt.total} |"
        )
    return "\n".join(lines)


def runner_available(runner: str) -> bool:
    """Whether a runner executable is on PATH.

    A missing runner is a skip with a reason, the way the smoke script
    treats a missing qemu.
    """

    if runner == "native":
        return True
    return shutil.which(runner) is not None
