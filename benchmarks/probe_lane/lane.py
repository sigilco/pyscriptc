#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""The differential probe lane: byte-compare the compiler against CPython (#523).

Every silent divergence the 2026-10-24 audit found was found by a human reading
output. None was found by a probe, the scorer, the census or the soundness
ratchet. This lane is the producer the divergence ledger consumes: it compiles a
corpus of small programs with the current compiler, runs each one next to the
pinned CPython 3.14 oracle, and byte-compares stdout, stderr and the exit code.
Any difference is a candidate divergence.

The lane has two inputs.

1. The semantic probes under `probes/`: one construct each, the programs the
   AGENTS.md differential probe rule requires for a new lowering and the ledger
   rows require for a numbered divergence.
2. The visible corpus under `benchmarks/compat2/corpus/`: the script programs
   that produce output, plus the pinned package trees that only have to compile.

The comparison is on untrimmed bytes. The compat2 scorer compares
whitespace-stripped stdout (`benchmarks/compat2/score.py:_judge`), so it cannot
see a trailing space, a missing newline, a NUL truncation or an escape-sequence
difference at all. This lane can, and that is the point.

For `probes/divergences/` the difference is already known: the ledger row in
`docs/reference/divergence-ledger.json` records the bytes both sides produced
when the row was written. The lane therefore compares the observed bytes
against the recorded row, so a divergence that moved or was fixed is a finding
in its own right, and it does not report a known divergence as a new one.

The lane is report-only. It exits 0 on every run: it never blocks a pull
request, it never opens a `nightly-failure` issue, and it never writes to the
frozen compat2 gate files. It also never reads the scorer holdout.

    uv run python -m benchmarks.probe_lane.lane
    uv run python -m benchmarks.probe_lane.lane --json-out out.json --summary "$GITHUB_STEP_SUMMARY"
    uv run python -m benchmarks.probe_lane.lane --lane probes --only json_bytes
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PROBES = ROOT / "probes"
CORPUS = ROOT / "benchmarks" / "compat2" / "corpus"
LEDGER = ROOT / "docs" / "reference" / "divergence-ledger.json"

DEFAULT_TIMEOUT = 120.0
RUN_TIMEOUT = 60.0

# Verdict vocabulary. MATCH is the only clean outcome; DIFF is a candidate
# divergence; the others say why the lane could not compare the two sides.
MATCH = "MATCH"
KNOWN = "KNOWN"
DIFF = "DIFF"
FENCED = "FENCED"
ERROR = "ERROR"
SKIP = "SKIP"

# The lane's own inputs, by name.
LANE_PROBES = "probes"
LANE_CORPUS = "corpus"

PS_CODE = re.compile(r"PS\d+")


@dataclass(frozen=True)
class Observation:
    """What one side of a comparison produced."""

    stdout: bytes
    stderr: bytes
    returncode: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "stdout": self.stdout.decode("utf-8", "replace"),
            "stderr": self.stderr.decode("utf-8", "replace"),
            "exit": self.returncode,
        }


@dataclass(frozen=True)
class LaneRow:
    """One program the lane compared, and what it saw.

    `verdict` is MATCH, DIFF, FENCED, ERROR or SKIP. `first_diff` names the
    first byte where the two sides disagree, or says that they matched. For a
    ledger probe, `expected` holds the recorded bytes and `differs_from_ledger`
    says whether the observation still equals the recorded row.
    """

    name: str
    lane: str
    source: str
    verdict: str
    first_diff: str = ""
    compile_status: str = ""
    fence: str = ""
    expected: dict[str, Any] | None = None
    differs_from_ledger: bool | None = None
    native: Observation | None = None
    cpython: Observation | None = None

    def as_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "name": self.name,
            "lane": self.lane,
            "source": self.source,
            "verdict": self.verdict,
            "compile_status": self.compile_status,
            "first_diff": self.first_diff,
        }
        if self.fence:
            row["fence"] = self.fence
        if self.expected is not None:
            row["expected"] = self.expected
            row["differs_from_ledger"] = self.differs_from_ledger
        if self.native is not None:
            row["native"] = self.native.as_dict()
        if self.cpython is not None:
            row["cpython"] = self.cpython.as_dict()
        return row


def first_byte_diff(left: bytes, right: bytes, label: str = "stdout") -> str:
    """Locate the first byte where two byte strings disagree.

    The note is the first difference, not a summary: the ledger and the job
    summary both want the offset, the two byte values and the side each came
    from. A length difference with no differing byte is reported as such.
    """

    for index, (a, b) in enumerate(zip(left, right, strict=False)):
        if a != b:
            return f"{label} first differs at byte {index}: native 0x{a:02x} cpython 0x{b:02x}"
    if len(left) == len(right):
        return ""
    shorter, longer = (left, right) if len(left) < len(right) else (right, left)
    tail = longer[len(shorter) :][:24]
    side = "cpython" if len(left) < len(right) else "native"
    return f"{label} length {len(left)} != {len(right)}: extra {side} bytes {tail!r}"


def channel_diffs(native: Observation, cpython: Observation) -> list[str]:
    """Every channel where the two sides disagree, most specific channel first."""

    diffs: list[str] = []
    if native.stdout != cpython.stdout:
        diffs.append(first_byte_diff(native.stdout, cpython.stdout, "stdout"))
    if native.stderr != cpython.stderr:
        diffs.append(first_byte_diff(native.stderr, cpython.stderr, "stderr"))
    if native.returncode != cpython.returncode:
        diffs.append(f"exit {cpython.returncode}!={native.returncode}")
    return diffs


@dataclass(frozen=True)
class Program:
    """One program the lane compiles and compares."""

    name: str
    lane: str
    path: Path
    kind: str = "program"  # "program" runs on both sides, "package" only compiles
    ledger_id: str | None = None
    # Extra arguments the CPython side takes, the way the ledger recorded it.
    # `-O` on the assert probe, for example: CPython skips the assert and the
    # recorded bytes say so, so the lane has to run the oracle the same way.
    oracle_args: tuple[str, ...] = ()


def semantic_probes() -> list[Program]:
    """Every probe source under `probes/`, one construct each.

    The two directories are the lane's semantic input: `differential/` holds the
    per-PR probe the AGENTS.md differential probe rule requires for a new
    lowering, `divergences/` holds the probe each ledger row pins. Both are
    plain Python programs, so both compile and run the same way.
    """

    programs: list[Program] = []
    for subdir in ("differential", "divergences"):
        directory = PROBES / subdir
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.py")):
            programs.append(
                Program(
                    name=f"{subdir}/{path.stem}",
                    lane=LANE_PROBES,
                    path=path,
                    kind="program",
                )
            )
    return programs


def corpus_programs() -> list[Program]:
    """The visible corpus: the script programs and the pinned package trees.

    The scripts under `corpus/scripts/<name>/main.py` are whole programs that
    print, so both sides run and the bytes are compared. The pinned packages
    under `corpus/src/` are import trees, not programs: they only have to
    compile, and their "output" on both sides is empty. The holdout under
    `corpus/holdout/` is never read: the lane has no code path that reaches it.
    """

    programs: list[Program] = []
    scripts = CORPUS / "scripts"
    if scripts.is_dir():
        for path in sorted(scripts.glob("*/main.py")):
            programs.append(
                Program(
                    name=f"scripts/{path.parent.name}",
                    lane=LANE_CORPUS,
                    path=path,
                    kind="program",
                )
            )
    src = CORPUS / "src"
    if src.is_dir():
        for path in sorted(src.iterdir()):
            if path.is_dir():
                programs.append(
                    Program(
                        name=f"src/{path.name}",
                        lane=LANE_CORPUS,
                        path=path,
                        kind="package",
                    )
                )
            elif path.suffix == ".py":
                programs.append(
                    Program(
                        name=f"src/{path.stem}",
                        lane=LANE_CORPUS,
                        path=path,
                        kind="package",
                    )
                )
    return programs


def load_ledger() -> dict[str, dict[str, Any]]:
    """The recorded divergence rows, keyed by the ledger id.

    The ledger is the record of what both sides produced when a row was
    written. The lane reads it to tell a known divergence from a moved one. It
    is a doc file, not a frozen compat2 gate, and the lane only reads it.
    """

    if not LEDGER.is_file():
        return {}
    rows = json.loads(LEDGER.read_text(encoding="utf-8")).get("rows", [])
    return {row["id"]: row for row in rows if row.get("id")}


def ledger_row_for(probe_path: Path) -> tuple[str, dict[str, Any]] | None:
    """The ledger row that pins `probe_path`, if there is one."""

    for row_id, row in load_ledger().items():
        probe = row.get("probe")
        if probe and Path(probe).name == probe_path.name and probe_path.parent.name == "divergences":
            return row_id, row
    return None


def compile_env() -> dict[str, str]:
    """The environment the compiler runs in.

    `PYTHONPATH` carries `src` so the lane works from a source checkout, and
    `PYTHONHASHSEED=0` so a set or dict iteration order cannot make a run
    differ from the next one of the same program.
    """

    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    src = str(ROOT / "src")
    env["PYTHONPATH"] = src if not existing else src + os.pathsep + existing
    env["PYTHONHASHSEED"] = "0"
    return env


def run_env() -> dict[str, str]:
    """The environment both sides run in.

    The two sides get the same encoding settings, so a byte difference is a
    difference in behavior and not in how the interpreter was told to write.
    """

    env = os.environ.copy()
    env["PYTHONHASHSEED"] = "0"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def fence_code(stderr: bytes) -> str:
    """The PS code when a compile ended in a fence, else an empty string."""

    match = PS_CODE.search(stderr.decode("utf-8", "replace"))
    return match.group(0) if match else ""


def compile_program(
    program: Program,
    output: Path,
    timeout: float,
    skip_check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    """Compile one program to a native binary.

    A package tree compiles to a binary that imports nothing at run time, so
    the lane only needs the compile to succeed for it.

    `skip_check` decides whether the type checker runs first. The lane skips it
    by default, the way the compat2 scorer does, so a program that the checker
    refuses is measured as a fence. The divergence ledger generator keeps the
    checker on, so a ledger probe whose recorded side is a compile-stage
    refusal is reproduced with the checker on too; otherwise the lane would
    compare a run against a recorded compile and report a move that never
    happened.
    """

    args = [sys.executable, "-m", "pyscriptc", "compile", str(program.path)]
    if skip_check:
        args.append("--skip-check")
    args += ["--emit", "bin", "-o", str(output)]
    return subprocess.run(
        args,
        cwd=ROOT,
        env=compile_env(),
        capture_output=True,
        timeout=timeout,
    )


def run_native(binary: Path, workdir: Path, timeout: float) -> Observation:
    """Run the compiled binary in an isolated directory."""

    ran = subprocess.run(
        [str(binary)],
        cwd=workdir,
        capture_output=True,
        timeout=timeout,
        env=run_env(),
    )
    return Observation(ran.stdout, ran.stderr, ran.returncode)


def run_cpython(
    source: Path,
    oracle: str,
    workdir: Path,
    timeout: float,
    oracle_args: tuple[str, ...] = (),
) -> Observation:
    """Run the same source under the pinned CPython oracle."""

    ran = subprocess.run(
        [oracle, *oracle_args, str(source)],
        cwd=workdir,
        capture_output=True,
        timeout=timeout,
        env=run_env(),
    )
    return Observation(ran.stdout, ran.stderr, ran.returncode)


_ORACLE_PREFIX: str | None = None


def oracle_prefix(oracle: str) -> str:
    """The oracle interpreter's install root, for traceback normalization.

    A CPython traceback names stdlib files under the interpreter's prefix, so
    the recorded bytes carry a path that moves with every interpreter install.
    The ledger generator normalizes it away; the lane does the same so the two
    sides of the comparison are comparable.
    """

    global _ORACLE_PREFIX
    if _ORACLE_PREFIX is None:
        out = subprocess.run(
            [oracle, "-c", "import sys; print(sys.base_prefix)"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        _ORACLE_PREFIX = out.stdout.strip()
    return _ORACLE_PREFIX


def normalize(text: str, probe: Path, workdir: Path, oracle: str) -> str:
    """Make recorded bytes stable: the probe path, the scratch cwd and the
    oracle install root are volatile (a traceback names stdlib files)."""

    try:
        relative = str(probe.relative_to(ROOT))
    except ValueError:
        relative = str(probe)
    return text.replace(str(probe), relative).replace(str(workdir), "<cwd>").replace(oracle_prefix(oracle), "<oracle>")


def ledger_side(row: dict[str, Any], side: str) -> dict[str, Any] | None:
    """The recorded bytes of one side of a ledger row."""

    recorded = row.get(side)
    return recorded if isinstance(recorded, dict) else None


def oracle_missing_module(obs: Observation) -> str:
    """The module the oracle could not import, or an empty string.

    The oracle interpreter is a plain CPython 3.14: it carries the standard
    library and nothing else. A program that needs a third party package (the
    numpy probe needs numpy) runs on the native side, where the compiler
    carries its own pack, and fails on the oracle side with a
    `ModuleNotFoundError`. The two sides are then not running the same
    program, so the lane reports the program as skipped instead of as a
    difference.
    """

    if obs.returncode == 0:
        return ""
    text = obs.stderr.decode("utf-8", "replace")
    match = re.search(r"ModuleNotFoundError: No module named '([^']+)'", text)
    return match.group(1) if match else ""


def _missing_module_note(obs: Observation) -> str:
    """The note a skipped program carries."""

    module = oracle_missing_module(obs)
    return f"oracle has no {module}: the two sides do not run the same program"


def compile_note(stderr: bytes, returncode: int) -> str:
    """One line that says why a compile ended: the first PS diagnostic line.

    A fence prints its code, a caret line and a count of follow-on fences. The
    caret line says nothing on its own, so the note is the line that carries
    the diagnostic code, else the last non-blank line of stderr.
    """

    text = stderr.decode("utf-8", "replace").replace(str(ROOT) + os.sep, "")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        if PS_CODE.search(line):
            return line[:200]
    return lines[-1][:200] if lines else f"compile exit {returncode}"


def source_label(program: Program) -> str:
    """The repo-relative path of a program, for the report."""

    path = program.path
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def compare_program(
    program: Program,
    oracle: str,
    timeout: float,
    run_timeout: float,
    skip_check: bool = True,
) -> LaneRow:
    """Compile one program, run both sides, byte-compare the three channels.

    The verdict is MATCH when the two sides agree on stdout, stderr and the
    exit code. DIFF names the first differing byte. FENCED means the compiler
    refused the program, which is not a divergence: a construct that does not
    compile is outside the parity contract.
    """

    label = source_label(program)
    with tempfile.TemporaryDirectory(prefix="pyscriptc-probe-lane-") as tmp:
        root = Path(tmp)
        binary = root / "prog"
        try:
            built = compile_program(program, binary, timeout, skip_check=skip_check)
        except subprocess.TimeoutExpired:
            return LaneRow(
                name=program.name,
                lane=program.lane,
                source=label,
                verdict=ERROR,
                compile_status="compile timed out",
                first_diff="compile timed out",
            )
        if built.returncode != 0:
            fence = fence_code(built.stderr) if built.returncode == 3 else ""
            tail = compile_note(built.stderr, built.returncode)
            return LaneRow(
                name=program.name,
                lane=program.lane,
                source=label,
                verdict=FENCED if fence else ERROR,
                compile_status=f"compile exit {built.returncode}",
                fence=fence,
                first_diff=tail,
            )
        if not binary.is_file():
            return LaneRow(
                name=program.name,
                lane=program.lane,
                source=label,
                verdict=ERROR,
                compile_status="compile produced no binary",
                first_diff="compile produced no binary",
            )
        if program.kind == "package":
            # A pinned package tree is a library, not a program. Both sides
            # would print nothing, so the compile is the whole comparison.
            return LaneRow(
                name=program.name,
                lane=program.lane,
                source=label,
                verdict=MATCH,
                compile_status="compiled",
                first_diff="match",
            )
        native_dir, oracle_dir = root / "native", root / "oracle"
        native_dir.mkdir(), oracle_dir.mkdir()
        try:
            native_obs = run_native(binary, native_dir, run_timeout)
            cpython_obs = run_cpython(
                program.path,
                oracle,
                oracle_dir,
                run_timeout,
                oracle_args=program.oracle_args,
            )
        except subprocess.TimeoutExpired:
            return LaneRow(
                name=program.name,
                lane=program.lane,
                source=label,
                verdict=ERROR,
                compile_status="compiled",
                first_diff="run timed out",
            )
    if oracle_missing_module(cpython_obs):
        # The oracle interpreter does not carry what the native side has, so
        # the two sides are not running the same program and a difference
        # between them says nothing. The numpy probe is the case: the pinned
        # oracle has no numpy, the compiler carries the ndarray pack.
        return LaneRow(
            name=program.name,
            lane=program.lane,
            source=label,
            verdict=SKIP,
            compile_status="compiled",
            first_diff=_missing_module_note(cpython_obs),
            native=native_obs,
            cpython=cpython_obs,
        )
    diffs = channel_diffs(native_obs, cpython_obs)
    verdict = MATCH if not diffs else DIFF
    return LaneRow(
        name=program.name,
        lane=program.lane,
        source=label,
        verdict=verdict,
        compile_status="compiled",
        first_diff=diffs[0] if diffs else "match",
        native=native_obs,
        cpython=cpython_obs,
    )


def ledger_sides(
    program: Program,
    oracle: str,
    workdir: Path,
    timeout: float,
    run_timeout: float,
    compile_stage: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Both sides of a ledger row, observed the way the ledger observed them.

    The ledger generator runs the CPython side and the pyscriptc side in the
    same working directory, the CPython one first. That is not an accident:
    a probe that creates a file or a directory leaves it behind, so the second
    side sees what the first side left. The recorded bytes carry that, and a
    divergence row can be exactly that (row D47 is a `FileExistsError` the
    second side hit).

    The lane therefore observes a ledger probe the same way when it compares
    against the recorded row. The ordinary lane comparison runs each side in
    its own directory, where a difference is a difference in behavior and not
    in what the first side left on disk.
    """

    cpython_obs = run_cpython(
        program.path,
        oracle,
        workdir,
        run_timeout,
        oracle_args=program.oracle_args,
    )
    cpython = {
        "stage": "run",
        "exit": cpython_obs.returncode,
        "stdout": normalize(cpython_obs.stdout.decode("utf-8", "replace"), program.path, workdir, oracle),
        "stderr": normalize(cpython_obs.stderr.decode("utf-8", "replace"), program.path, workdir, oracle),
    }
    binary = workdir / (program.path.stem + ".bin")
    try:
        built = compile_program(program, binary, timeout, skip_check=not compile_stage)
    except subprocess.TimeoutExpired:
        return {"stage": "compile", "exit": -1, "stdout": "", "stderr": "compile timed out"}, cpython
    if built.returncode != 0:
        return (
            {
                "stage": "compile",
                "exit": built.returncode,
                "stdout": normalize(built.stdout.decode("utf-8", "replace"), program.path, workdir, oracle),
                "stderr": normalize(built.stderr.decode("utf-8", "replace"), program.path, workdir, oracle),
            },
            cpython,
        )
    ran = run_native(binary, workdir, run_timeout)
    native = {
        "stage": "run",
        "exit": ran.returncode,
        "stdout": normalize(ran.stdout.decode("utf-8", "replace"), program.path, workdir, oracle),
        "stderr": normalize(ran.stderr.decode("utf-8", "replace"), program.path, workdir, oracle),
    }
    return native, cpython


def compare_ledger_row(
    program: Program,
    row_id: str,
    row: dict[str, Any],
    oracle: str,
    timeout: float,
    run_timeout: float,
) -> LaneRow:
    """Compare a divergence probe against the bytes the ledger recorded.

    For this set the difference between the two sides is already known and
    documented, so comparing the two sides against each other says nothing
    new. What matters is whether the divergence still looks the way the ledger
    recorded it: the observed CPython bytes and the observed pyscriptc bytes
    are each compared with the recorded row, and a row that moved is a finding
    in its own right (the divergence was fixed, or it moved to another seam).

    The verdict is KNOWN when the observation still equals the recorded row:
    the two sides differ the way the ledger says they differ, which is a
    documented divergence and not a finding. A row that moved reports DIFF
    and names the channel that moved, because that is a finding: the
    divergence was fixed, or it moved to another seam.
    """

    recorded_cpython = ledger_side(row, "cpython")
    recorded_pyscriptc = ledger_side(row, "pyscriptc")
    # A ledger row recorded a compile-stage refusal on the pyscriptc side. The
    # lane reproduces that compile with the checker on, because the run it
    # would do instead is a different observation than the one on record.
    compile_stage = bool(recorded_pyscriptc) and recorded_pyscriptc.get("stage") == "compile"
    with tempfile.TemporaryDirectory(prefix="pyscriptc-probe-ledger-") as tmp:
        native, cpython = ledger_sides(program, oracle, Path(tmp), timeout, run_timeout, compile_stage)
    expected = {
        "id": row_id,
        "status": row.get("status", ""),
        "cpython": recorded_cpython,
        "pyscriptc": recorded_pyscriptc,
    }
    moved: list[str] = []
    if recorded_cpython is not None and cpython != recorded_cpython:
        moved.append(_side_diff("cpython", recorded_cpython, cpython))
    if recorded_pyscriptc is not None and native != recorded_pyscriptc:
        moved.append(_side_diff("pyscriptc", recorded_pyscriptc, native))
    differs = bool(moved)
    fence = ""
    if native.get("stage") == "compile":
        match = PS_CODE.search(str(native.get("stderr", "")))
        fence = match.group(0) if match else ""
    return LaneRow(
        name=program.name,
        lane=program.lane,
        source=source_label(program),
        verdict=DIFF if differs else KNOWN,
        compile_status="compiled" if native.get("stage") == "run" else "compile exit " + str(native.get("exit")),
        fence=fence,
        first_diff=moved[0] if moved else _side_summary(recorded_cpython, recorded_pyscriptc),
        expected=expected,
        differs_from_ledger=differs,
    )


def _side_summary(recorded_cpython: dict[str, Any] | None, recorded_pyscriptc: dict[str, Any] | None) -> str:
    """The note a stable ledger row carries: the two sides differ as recorded."""

    if not recorded_cpython or not recorded_pyscriptc:
        return "match"
    if all(recorded_cpython.get(k) == recorded_pyscriptc.get(k) for k in ("stdout", "stderr", "exit")):
        return "match"
    return "known divergence"


def _side_diff(side: str, recorded: dict[str, Any], observed: dict[str, Any]) -> str:
    """Name the channel of one side that moved away from the recorded bytes."""

    for channel in ("stdout", "stderr"):
        if recorded.get(channel) != observed.get(channel):
            left = str(recorded.get(channel, "")).encode("utf-8", "replace")
            right = str(observed.get(channel, "")).encode("utf-8", "replace")
            return f"{side} {first_byte_diff(left, right, channel)}"
    if recorded.get("exit") != observed.get("exit"):
        return f"{side} exit {recorded.get('exit')}!={observed.get('exit')}"
    return f"{side} moved"


def run_lane(
    programs: list[Program],
    oracle: str,
    timeout: float,
    run_timeout: float,
    ledger: dict[str, dict[str, Any]] | None = None,
) -> list[LaneRow]:
    """Run the lane over `programs` and return one row per program."""

    rows: list[LaneRow] = []
    for program in programs:
        row = None
        if program.path.parent.name == "divergences" and ledger is not None:
            found = ledger_row_for(program.path)
            if found is not None:
                row_id, recorded = found
                # The oracle may need arguments the ledger recorded (`-O` on the
                # assert probe). The CPython side has to run the same way the
                # recorded bytes were captured, or the comparison is against
                # a different program.
                program = replace(program, ledger_id=row_id, oracle_args=tuple(recorded.get("oracle_args", ())))
                row = compare_ledger_row(program, row_id, recorded, oracle, timeout, run_timeout)
        if row is None:
            row = compare_program(program, oracle, timeout, run_timeout)
        rows.append(row)
    return rows


def counts(rows: list[LaneRow]) -> dict[str, int]:
    """The verdict tally, in a fixed order."""

    tally = {verdict: 0 for verdict in (MATCH, KNOWN, DIFF, FENCED, ERROR, SKIP)}
    for row in rows:
        tally[row.verdict] = tally.get(row.verdict, 0) + 1
    return tally


def counts_line(rows: list[LaneRow]) -> str:
    """The one-line count summary the job summary and the report carry.

    KNOWN is a documented divergence that still looks the way the ledger
    recorded it. It is reported apart from DIFF, because a difference that is
    already written down is not a finding.
    """

    tally = counts(rows)
    compiled = sum(1 for row in rows if row.compile_status == "compiled")
    return (
        f"{len(rows)} programs: {compiled} compiled, {tally[MATCH]} match, {tally[KNOWN]} known divergence, "
        f"{tally[DIFF]} differ, {tally[FENCED]} fenced, {tally[ERROR]} error, {tally[SKIP]} skip"
    )


def render_summary(rows: list[LaneRow]) -> str:
    """The markdown job summary.

    The numbers come first, then every differing program with its first
    difference, so a reader of the nightly run sees the findings without
    downloading the artifact.
    """

    tally = counts(rows)
    compiled = sum(1 for row in rows if row.compile_status == "compiled")
    differing = [row for row in rows if row.verdict == DIFF]
    lines = [
        "### Differential probe lane (byte-exact against CPython 3.14)",
        "",
        "Report only: this lane never blocks a pull request. A difference is a candidate",
        "divergence for the ledger, not a failure.",
        "",
        f"- programs compared: {len(rows)}",
        f"- programs compiled: {compiled}",
        f"- matched: {tally[MATCH]}",
        f"- differed: {tally[DIFF]}",
        f"- known divergences (recorded in the ledger, unchanged): {tally[KNOWN]}",
        f"- fenced (not compiled, outside the parity contract): {tally[FENCED]}",
        f"- errors: {tally[ERROR]}",
        "",
    ]
    if differing:
        lines.extend(("#### Differing programs", "", "| program | lane | first difference |", "| --- | --- | --- |"))
        for row in differing:
            note = row.first_diff.replace("|", "\\|")
            lines.append(f"| `{row.name}` | {row.lane} | {note} |")
        lines.append("")
    else:
        lines.extend(("No program differed from the pinned CPython on stdout, stderr or the exit code.", ""))
    return "\n".join(lines)


def render_table(rows: list[LaneRow]) -> str:
    """The plain-text table the tool prints on stdout."""

    headers = ("program", "lane", "compile", "verdict", "first difference")
    body: list[tuple[str, ...]] = []
    for row in rows:
        note = (row.first_diff or "-").replace("\n", " ")
        body.append((row.name, row.lane, row.compile_status or "-", row.verdict, note[:100]))
    widths = [len(h) for h in headers]
    for cells in body:
        for index, cell in enumerate(cells):
            widths[index] = max(widths[index], len(cell))
    lines = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))]
    lines.append("  ".join("-" * w for w in widths))
    lines.extend("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells)) for cells in body)
    lines.extend(("", counts_line(rows)))
    return "\n".join(lines) + "\n"


ORACLE_PIN = (3, 14)


def oracle_version(executable: str) -> tuple[tuple[int, int], str]:
    """The (major, minor) and the full version string of an interpreter."""

    out = subprocess.run(
        [executable, "-c", "import platform;print(platform.python_version())"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    full = out.stdout.strip()
    major, minor = full.split(".")[:2]
    return (int(major), int(minor)), full


def resolve_oracle() -> str | None:
    """The pinned CPython 3.14 oracle executable, or None when unavailable.

    The lane never silently compares against the host interpreter: the oracle
    has to be CPython 3.14, the pin the repo holds everywhere else. The
    resolution order is the one the repo already uses (`PYSC_ORACLE`, else
    `PYSC_ORACLE_DIR`, else `uv python find 3.14`).

    The resolver lives here rather than being imported from
    `benchmarks/compat2/score.py` for two reasons. That file is a frozen
    compat2 gate, so the lane must not depend on it, and it carries 3.14-only
    syntax (unparenthesized except tuples) that a 3.11 or 3.12 host cannot
    import, while the lane has to run on every host the compiler supports.
    """

    candidates: list[str] = []
    explicit = os.environ.get("PYSC_ORACLE")
    if explicit:
        if not os.path.isfile(explicit) or not os.access(explicit, os.X_OK):
            print(
                f"PYSC_ORACLE={explicit!r} is not an executable path; the oracle must be CPython 3.14",
                file=sys.stderr,
            )
            return None
        candidates.append(explicit)
    oracle_dir = os.environ.get("PYSC_ORACLE_DIR")
    if oracle_dir:
        candidates += [str(p) for p in sorted(Path(oracle_dir).glob("cpython-3.14.*/bin/python3"))[-1:]]
    if not candidates:
        try:
            found = subprocess.run(["uv", "python", "find", "3.14"], capture_output=True, text=True, check=True)
            candidates.append(found.stdout.strip())
        except (OSError, subprocess.CalledProcessError):
            pass
    if not candidates and sys.version_info[:2] == ORACLE_PIN:
        candidates.append(sys.executable)
    for candidate in candidates:
        try:
            version, _full = oracle_version(candidate)
        except (OSError, subprocess.SubprocessError):
            continue
        if version == ORACLE_PIN:
            return candidate
    return None


def has_c_toolchain() -> bool:
    """Whether compile-to-binary can find a toolchain."""

    from pyscriptc.zigshim import resolve_zig

    if shutil.which("zig") is not None or shutil.which("clang") is not None:
        return True
    if shutil.which("cc") is not None or shutil.which("gcc") is not None:
        return True
    return resolve_zig() is not None


def select_programs(lane: str | None, only: str | None) -> list[Program]:
    """The programs the lane covers, filtered by lane name and by a substring."""

    programs = []
    if lane in (None, "all", LANE_PROBES):
        programs.extend(semantic_probes())
    if lane in (None, "all", LANE_CORPUS):
        programs.extend(corpus_programs())
    if only:
        programs = [p for p in programs if only in p.name or only in str(p.path)]
    return programs


def build_report(rows: list[LaneRow], oracle: str, seconds: float) -> dict[str, Any]:
    """The machine-readable report the nightly publishes as an artifact."""

    tally = counts(rows)
    return {
        "lane": "differential-probe",
        "issue": 523,
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "oracle": oracle,
        "seconds": round(seconds, 3),
        "summary": {
            "programs": len(rows),
            "compiled": sum(1 for row in rows if row.compile_status == "compiled"),
            "matched": tally[MATCH],
            "known_divergences": tally[KNOWN],
            "differed": tally[DIFF],
            "fenced": tally[FENCED],
            "errors": tally[ERROR],
            "skipped": tally[SKIP],
        },
        "counts": counts_line(rows),
        "rows": [row.as_dict() for row in rows],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lane", choices=["all", LANE_PROBES, LANE_CORPUS], default="all")
    parser.add_argument("--only", help="run only programs whose name or path contains this substring")
    parser.add_argument("--oracle", help="CPython executable (default: the pinned 3.14 oracle)")
    parser.add_argument("--json-out", type=Path, help="write the JSON report here")
    parser.add_argument("--summary", type=Path, help="append the markdown summary to this file")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="compile timeout in seconds")
    parser.add_argument("--run-timeout", type=float, default=RUN_TIMEOUT, help="run timeout in seconds")
    args = parser.parse_args(argv)

    if not has_c_toolchain():
        print(
            "no C toolchain on PATH (neither zig nor clang): the differential probe lane needs compile-to-binary",
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
    programs = select_programs(args.lane, args.only)
    if not programs:
        print(f"no programs match lane={args.lane!r} only={args.only!r}", file=sys.stderr)
        return 2
    started = time.monotonic()
    rows = run_lane(programs, oracle, args.timeout, args.run_timeout, ledger=load_ledger())
    report = build_report(rows, oracle, time.monotonic() - started)
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.summary is not None:
        with args.summary.open("a", encoding="utf-8") as handle:
            handle.write(render_summary(rows))
    print(render_table(rows), end="")
    # Report only: a difference is a finding for the ledger, never a failure.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
