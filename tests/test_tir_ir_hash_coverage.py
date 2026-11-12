"""TIR_HASHES.json must cover every committed probe.

Read-only check on the one canonicity gate under benchmarks/compat2/probes.
The dict-IR gate and its baseline are retired (#830): `TIR_HASHES.json` is
the single canonicity gate, so the coverage contract is now one-sided — every
probe id in the committed probe files must have a TIR_HASHES.json entry, and
every entry must be a sha256 hex digest. A partial regen cannot silently drop
probe coverage while the nightly gate still passes. It never regenerates or
edits the file.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

PROBES = Path(__file__).resolve().parents[1] / "benchmarks" / "compat2" / "probes"
PROBE_FILES = ("builtins", "surface", "modules", "language")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _baseline(name: str) -> dict:
    return json.loads((PROBES / name).read_text(encoding="utf-8"))


def _committed_probe_ids() -> set[str]:
    ids: set[str] = set()
    for stem in PROBE_FILES:
        for line in (PROBES / f"{stem}.jsonl").read_text(encoding="utf-8").splitlines():
            ids.add(json.loads(line)["id"])
    return ids


def test_the_tir_hashes_carry_a_sha256_digest_per_probe() -> None:
    tir_probes = _baseline("TIR_HASHES.json")["probes"]
    assert tir_probes
    assert all(HEX64.match(digest) for digest in tir_probes.values())


def test_the_hash_baseline_covers_every_committed_probe() -> None:
    ids = _committed_probe_ids()
    assert set(_baseline("TIR_HASHES.json")["probes"]) == ids
