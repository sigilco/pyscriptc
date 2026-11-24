"""The per-target receipt seam: the probe set, the targets and the builder.

The web page is node-tested in `apps/web/test/` (run by site.yml). What
stays under pytest here is the data layer:

| part | what the test proves |
| --- | --- |
| the probe set | every probe has an expected-output file and the harness loads them |
| the target matrix | the riscv64 rows are compile-only, the wasm row carries wasmtime |
| the receipt schema | `scripts/build_receipts_json.py` validates a real document |
| `data/compat/receipts.json` | the committed receipt validates |

Design doc: .agents/docs/benchmarks/target-receipts.md
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks.receipts.probes import load_probes  # noqa: E402
from benchmarks.receipts.targets import TARGETS, by_name, names, runner_for  # noqa: E402

RECEIPTS = ROOT / "data" / "compat" / "receipts.json"


def test_every_probe_has_an_expected_output() -> None:
    probes = load_probes()
    assert len(probes) >= 5, "the probe set is smaller than the plan's floor of five"
    for probe in probes:
        assert probe.expected, probe.id


def test_the_probe_set_is_toolchain_independent() -> None:
    # The set carries no import outside the standard library's pure surface:
    # no island fallback, no threads, no dlopen, no clock, no randomness.
    for probe in load_probes():
        source = probe.path.read_text(encoding="utf-8")
        for forbidden in ("ps_island", "fallback", "dlopen", "ctypes", "threading", "random", "time.time"):
            assert forbidden not in source, f"{probe.id} uses {forbidden}"


def test_riscv64_rows_are_compile_only_by_decision() -> None:
    for name in names():
        if "riscv64" in name:
            target = by_name(name)
            assert runner_for(target) == "", f"{name} has a runner"
            assert target.compile_only_reason, f"{name} carries no reason"


def test_the_runnable_rows_carry_a_runner() -> None:
    assert runner_for(by_name("wasm32-wasi")) == "wasmtime"
    # A foreign-architecture Linux row runs under a user-mode qemu when the
    # host can run one; the name is whatever the distro installs.
    qemu = runner_for(by_name("aarch64-linux-musl"))
    assert qemu.startswith("qemu-"), qemu


def test_the_matrix_matches_the_smoke_script_targets() -> None:
    # The matrix is the tier-A cross matrix: every target the smoke script
    # builds is a receipt row.
    smoke = (ROOT / "packaging" / "zig" / "smoke.sh").read_text(encoding="utf-8")
    for name in (
        "aarch64-gnu",
        "riscv64-gnu",
        "x86_64-musl",
        "aarch64-musl",
        "riscv64-musl",
        "aarch64-macos",
        "x86_64-macos",
    ):
        assert name in smoke, f"{name} is not a smoke-script target"


def test_the_committed_receipt_validates() -> None:
    from scripts.build_receipts_json import validate

    if not RECEIPTS.exists():
        pytest.skip(f"receipt data file not built: {RECEIPTS}")
    document = json.loads(RECEIPTS.read_text(encoding="utf-8"))
    assert validate(document) == []


def test_the_committed_receipt_publishes_no_local_path() -> None:
    # The document is published on the site: the oracle is a version
    # string, never the measuring machine's path.
    if not RECEIPTS.exists():
        pytest.skip(f"receipt data file not built: {RECEIPTS}")
    document = json.loads(RECEIPTS.read_text(encoding="utf-8"))
    oracle = document["oracle"]
    assert "/" not in oracle, oracle
    assert oracle.startswith("3.14"), oracle


def test_the_builder_refuses_a_malformed_document(tmp_path: Path) -> None:
    from scripts.build_receipts_json import validate

    assert validate({"schema": "wrong"}) != []
    assert validate({"schema": "1", "targets": []}) != []
    document = {
        "schema": "1",
        "generated": "2026-11-24",
        "commit": "abc",
        "oracle": "/usr/bin/python3.14",
        "host": {"zig": "0.16.0"},
        "targets": [
            {
                "target": "wasm32-wasi",
                "triple": "wasm32-wasi-musl",
                "system": "wasi",
                "arch": "wasm32",
                "runner": "wasmtime",
                "method": "wasmtime",
                "skip_reason": "",
                "toolchain": "0.16.0",
                "built": 5,
                "ran": 5,
                "matched": 5,
                "total": 5,
                "probes": [{"id": "int_arith", "built": True, "ran": True, "match": True, "detail": ""}],
            }
        ],
    }
    assert validate(document) != [], "one probe row but total 5 is inconsistent"


@pytest.mark.parametrize("system,expected", [("linux", "linux"), ("macos", "macos")])
def test_the_matrix_covers_the_three_systems(system: str, expected: str) -> None:
    assert any(target.system == expected for target in TARGETS), system
