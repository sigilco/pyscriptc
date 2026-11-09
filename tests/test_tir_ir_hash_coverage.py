"""TIR_HASHES.json must cover every probe that IR_HASHES.json pins.

Read-only check on the two frozen baselines under benchmarks/compat2/probes.
`tirscore --verify-bytes` already proves the TIR stream is byte-identical to
the canonical dict payload on the same probe universe, so today the two
`probes` maps hold the same ids with the same sha256 digests. This test pins
the coverage half of that contract — every probe id hashed into
IR_HASHES.json must also have a TIR_HASHES.json entry and vice versa — so a
partial regen of one baseline cannot silently drop the other's coverage while
the nightly gate still passes. It never regenerates or edits either file.

Digest equality is deliberately not asserted: the typed-node serializer
planned under the #830 retirement prerequisites may legitimately re-key
TIR_HASHES.json through the recorded A/B regen ritual while IR_HASHES.json
stays frozen until its own deletion step.
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


def test_tir_hashes_cover_every_ir_hash_probe() -> None:
    ir_probes = _baseline("IR_HASHES.json")["probes"]
    tir_probes = _baseline("TIR_HASHES.json")["probes"]
    assert set(tir_probes) == set(ir_probes)
    assert all(HEX64.match(digest) for digest in tir_probes.values())


def test_both_hash_baselines_cover_every_committed_probe() -> None:
    ids = _committed_probe_ids()
    for name in ("IR_HASHES.json", "TIR_HASHES.json"):
        assert set(_baseline(name)["probes"]) == ids
