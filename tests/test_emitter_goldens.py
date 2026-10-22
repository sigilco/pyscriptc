"""Emitter golden corpus: one fixture per IR node kind, asserted byte for byte.

Each fixture is a pair under `tests/fixtures/backend/llvm/`:
`<name>.ir.json` is the IR module the emitter sees, `<name>.ll` is the
byte-exact LLVM text it must produce. `tests/fixtures/backend/manifest.json`
records which IR node kind each fixture covers; the coverage test fails with
the missing kinds listed if the corpus ever stops covering the emitter's
dispatch surface.

Emission goes through `pyscriptc.emitter.emit_ir_text`: in memory, no file,
no subprocess, no `cc`, so the whole suite runs in well under a second.

After an intentional emitter change, re-bless the corpus with:

    uv run pytest tests/test_emitter_goldens.py --update-goldens
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pyscriptc import ir as ir_mod  # noqa: E402
from pyscriptc.emitter import emit_ir_text  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "tests" / "fixtures" / "backend"
MANIFEST = json.loads((BACKEND / "manifest.json").read_text(encoding="utf-8"))


def _fixture_dirs() -> dict[str, Path]:
    """Fixture name -> directory, per backend subdirectory of the corpus."""

    return {path.name: path for path in sorted(BACKEND.iterdir()) if path.is_dir()}


@pytest.fixture()
def update_goldens(request: pytest.FixtureRequest) -> bool:
    requested = request.config.getoption("--update-goldens")
    if requested:
        yield True
        return
    if os.getenv("PYSC_UPDATE_GOLDENS") == "1":
        yield True
        return
    yield False


def test_golden_corpus(update_goldens: bool) -> None:
    """Every fixture re-emits byte-identical LLVM text (or re-blesses it)."""

    changed: list[str] = []
    for entry in MANIFEST["fixtures"]:
        name = entry["name"]
        ir = json.loads((BACKEND / entry["ir"]).read_text(encoding="utf-8"))
        # The CPython stamp is incidental (see .agents/docs/spec/ir-canonicity.md): it marks
        # the interpreter the IR was lowered on and never reaches the emitted
        # text. Re-stamp it so a fixture written on one 3.14 patch still emits
        # on another.
        ir["cpython"] = ir_mod.cpython_version()
        golden = BACKEND / entry["ll"]
        text = emit_ir_text(ir, call=entry["call"])
        if update_goldens:
            golden.write_text(text, encoding="utf-8")
            continue
        if golden.read_text(encoding="utf-8") != text:
            changed.append(name)
    assert not changed, (
        "emitter output drifted for: " + ", ".join(changed) + "; if the change is intentional re-bless with "
        "`uv run pytest tests/test_emitter_goldens.py --update-goldens` and review the diff"
    )


def test_golden_coverage() -> None:
    """The corpus covers every IR node kind the emitter dispatches on."""

    covered = {kind for entry in MANIFEST["fixtures"] for kind in entry["covers"]}
    missing = sorted((ir_mod.EXPR_KINDS | ir_mod.STMT_KINDS) - covered)
    assert not missing, f"IR node kinds with no golden fixture: {missing}"


def test_manifest_matches_files() -> None:
    """Every manifest entry points at a fixture pair on disk, and vice versa."""

    on_disk = sorted(path.name.removesuffix(".ir.json") for path in BACKEND.rglob("*.ir.json"))
    in_manifest = sorted(entry["name"] for entry in MANIFEST["fixtures"])
    assert on_disk == in_manifest
    for entry in MANIFEST["fixtures"]:
        assert (BACKEND / entry["ir"]).is_file(), entry["ir"]
        assert (BACKEND / entry["ll"]).is_file(), entry["ll"]


def test_generator_is_current() -> None:
    """The committed manifest is what `scripts/gen_emitter_goldens.py` builds."""

    from scripts.gen_emitter_goldens import CALLS, COVERAGE, FIXTURE_BUILDERS, STRUCTURAL

    expected = [
        {
            "name": name,
            "ir": f"llvm/{name}.ir.json",
            "ll": f"llvm/{name}.ll",
            "covers": COVERAGE[name],
            "structural": STRUCTURAL.get(name),
            "call": CALLS.get(name, (None, []))[0],
        }
        for name in FIXTURE_BUILDERS
    ]
    assert MANIFEST["fixtures"] == expected
