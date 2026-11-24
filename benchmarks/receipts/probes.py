# SPDX-License-Identifier: Apache-2.0
"""The probe set the receipt harness measures.

A receipt row is one probe: a small, self-contained Python program that
prints something, plus the bytes the pinned CPython 3.14 oracle prints
for it. The receipt compares the target's output against those bytes, so
the row is a measured run-and-match and not a claim.

The probes are toolchain-independent by construction: integer, string and
list arithmetic, no island fallback, no `dlopen`/mix, no threads, no
files, no locale, no clock and no randomness. A program whose output
depends on the host is not a receipt, so nothing here reads the clock,
the environment or the filesystem.

The set is small on purpose. Every probe is compiled once per target and
run once per target, so the cost is the compile, not the probe count.

Design: .agents/docs/benchmarks/target-receipts.md
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROBE_DIR = Path(__file__).resolve().parent / "probes"
# The expected-output file, one per probe, next to the program.
EXPECTED_SUFFIX = ".expected"


@dataclass(frozen=True)
class Probe:
    """One probe program and the bytes the oracle prints for it."""

    id: str
    path: Path
    expected: bytes

    @property
    def name(self) -> str:
        """The probe id, without the directory."""

        return self.id


def _expected_path(program: Path) -> Path:
    return program.with_suffix(EXPECTED_SUFFIX)


def load_probes(directory: Path | None = None) -> list[Probe]:
    """Every probe in the directory, sorted by id.

    A probe without its expected-output file is not a probe: the harness
    refuses the directory rather than measuring a program it cannot
    compare.
    """

    source = directory or PROBE_DIR
    probes: list[Probe] = []
    for program in sorted(source.glob("*.py")):
        expected = _expected_path(program)
        if not expected.exists():
            raise FileNotFoundError(f"probe {program.name} has no expected-output file {expected.name}")
        probes.append(
            Probe(
                id=program.stem,
                path=program,
                expected=expected.read_bytes(),
            )
        )
    return probes


def record_expectations(programs: dict[str, bytes], directory: Path | None = None) -> list[Path]:
    """Write the expected-output files from a measured run.

    The harness uses this to record a probe's expected bytes once, from
    the oracle, so the committed files are reproducible. The nightly never
    calls it: it reads the committed files.
    """

    source = directory or PROBE_DIR
    written: list[Path] = []
    for name, payload in sorted(programs.items()):
        target = source / f"{name}{EXPECTED_SUFFIX}"
        target.write_bytes(payload)
        written.append(target)
    return written


def load_manifest(path: Path) -> dict[str, object]:
    """The probe manifest, when the directory carries one."""

    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
