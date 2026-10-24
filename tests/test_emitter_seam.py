"""The emitter seam itself: one IR fragment in, LLVM text out.

The golden corpus (`tests/test_emitter_goldens.py`) pins what the emitter
produces for the committed fixtures. These tests pin the seam contract the
corpus and the future C backend build on: `make_module` builds a valid
module in memory, `emit_ir_text` runs the same checks a whole-program
compile runs, no file is written and no toolchain is needed, and the corpus
regenerates byte-identically (the generator iterates its builders in a fixed
order and the emitter normalizes every set it iterates, so a diff in a
golden is always a reviewed change, never hash-order noise).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pyscriptc import ir as ir_mod  # noqa: E402
from pyscriptc.emitter import emit_ir_text, make_module  # noqa: E402
from pyscriptc.errors import IRError, VersionError  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "tests" / "fixtures" / "backend"

SPAN = {"file": "<seam>", "line": 1, "col": 1, "endLine": 1, "endCol": 2}


def _int_const(value: str) -> dict:
    return {"kind": "const", "type": {"kind": "int"}, "span": dict(SPAN), "value": value}


def _assign(target: str, value: dict) -> dict:
    return {"kind": "assign", "span": dict(SPAN), "target": target, "value": value}


def _fn(name: str, body: list[dict], locals_: list[dict] | None = None) -> dict:
    return {
        "name": name,
        "params": [],
        "returnType": {"kind": "none"},
        "locals": locals_ or [],
        "body": body,
    }


def _local(name: str) -> dict:
    return {"name": name, "type": {"kind": "int"}}


# --- make_module ------------------------------------------------------------


def test_make_module_builds_a_valid_stamp_current_module() -> None:
    module = make_module([_fn("f", [_assign("x", _int_const("1"))], [_local("x")])])
    ir_mod.validate_module(module)
    assert module["cpython"] == ir_mod.cpython_version()
    assert module["entry"] == "f"
    assert module["fences"] == []


def test_make_module_rejects_a_bad_fragment() -> None:
    with pytest.raises(IRError):
        make_module([_fn("f", [{"kind": "nope", "span": dict(SPAN)}])])


# --- emit_ir_text -----------------------------------------------------------


def test_emit_ir_text_returns_text_in_memory() -> None:
    module = make_module([_fn("f", [_assign("x", _int_const("1"))], [_local("x")])])
    text = emit_ir_text(module)
    assert text.startswith("%ps.str = type { i64, ptr, i64, i32 }")
    assert "define void @f()" in text
    assert text.endswith("\n")


def test_emit_ir_text_needs_no_toolchain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The seam is pure: no cc, no zig, no subprocess, no file next to the test."""

    import os

    def no_subprocess(*a: object, **k: object) -> None:  # pragma: no cover - tripwire
        raise AssertionError("emit_ir_text must not spawn a subprocess")

    monkeypatch.setattr(subprocess, "run", no_subprocess)
    monkeypatch.setattr(subprocess, "Popen", no_subprocess)
    monkeypatch.setattr(os, "system", no_subprocess)
    module = make_module(
        [
            _fn(
                "f",
                [
                    {
                        "kind": "expr",
                        "span": dict(SPAN),
                        "expr": {
                            "kind": "call",
                            "type": {"kind": "none"},
                            "span": dict(SPAN),
                            "callee": {"kind": "builtin", "name": "print"},
                            "args": [_int_const("1")],
                        },
                    }
                ],
            )
        ]
    )
    text = emit_ir_text(module)
    assert "declare i32 @printf(ptr, ...)" in text
    assert list(tmp_path.iterdir()) == []


def test_emit_ir_text_stamps_are_checked() -> None:
    module = make_module([_fn("f", [_assign("x", _int_const("1"))], [_local("x")])])
    module["cpython"] = "1.2.3"
    with pytest.raises(VersionError):
        emit_ir_text(module)


def test_emit_ir_text_refuses_fences_and_empty_modules() -> None:
    module = make_module([_fn("f", [_assign("x", _int_const("1"))], [_local("x")])])
    module["fences"] = [{"code": "F001"}]
    with pytest.raises(IRError):
        emit_ir_text(module)
    empty = make_module([])
    with pytest.raises(IRError):
        emit_ir_text(empty)


def test_emit_ir_text_passes_call_through_to_main() -> None:
    module = make_module(
        [
            _fn("f", [_assign("x", _int_const("1"))], [_local("x")]),
            _fn("g", [_assign("y", _int_const("2"))], [_local("y")]),
        ]
    )
    text = emit_ir_text(module, call="g")
    assert "define void @f()" in text
    assert "define void @g()" in text
    assert "call void @g()" in text


# --- corpus determinism -----------------------------------------------------


def test_corpus_regenerates_byte_identically(tmp_path: Path) -> None:
    """A fresh generator run over the committed builders reproduces every file.

    The builders iterate in a fixed dict order and the emitter normalizes the
    sets it iterates, so re-blessing a golden can only ever be an intentional
    emitter diff, never hash-order noise. The fixtures are written to a
    scratch copy so the committed corpus is untouched.
    """

    from scripts.gen_emitter_goldens import COVERAGE, FIXTURE_BUILDERS, SURFACE, main

    # The coverage record the manifest commits is what the generator builds.
    assert sorted(COVERAGE) == sorted(FIXTURE_BUILDERS)
    assert sorted(SURFACE) == sorted(FIXTURE_BUILDERS)

    scratch = tmp_path / "backend"
    scratch.mkdir()
    main(fixtures_dir=scratch, manifest_path=tmp_path / "manifest.json")

    for entry in json.loads((BACKEND / "manifest.json").read_text(encoding="utf-8"))["fixtures"]:
        committed = json.loads((BACKEND / entry["ir"]).read_text(encoding="utf-8"))
        rebuilt = json.loads((scratch / Path(entry["ir"]).name).read_text(encoding="utf-8"))
        # The CPython stamp is incidental (.agents/docs/spec/ir-canonicity.md):
        # it marks the interpreter that wrote the fixture and never reaches the
        # emitted text, so a fixture written on one 3.14 patch regenerates on
        # another. Drop the stamps and compare the rest exactly, key order
        # included; a stamp mismatch is the golden test's re-stamp, not drift.
        committed.pop("cpython")
        rebuilt.pop("cpython")
        assert list(committed) == list(rebuilt), entry["name"]
        assert committed == rebuilt, entry["name"]


def test_corpus_is_hash_seed_stable(tmp_path: Path) -> None:
    """PYTHONHASHSEED must not move the committed goldens."""

    manifest = json.loads((BACKEND / "manifest.json").read_text(encoding="utf-8"))
    first = None
    for seed in ("0", "1", "12345"):
        out = subprocess.run(
            [
                sys.executable,
                "-X",
                f"utf8-{seed}",
                "-c",
                "import json,sys;"
                "sys.path.insert(0, 'src');sys.path.insert(0, '.');"
                "from pyscriptc import ir as m;"
                "from pyscriptc.emitter import emit_ir_text;"
                f"ir=json.load(open('{(BACKEND / manifest['fixtures'][0]['ir']).relative_to(ROOT)}'));"
                "ir['cpython']=m.cpython_version();"
                "sys.stdout.write(emit_ir_text(ir))",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        assert out.returncode == 0, out.stderr
        if first is None:
            first = out.stdout
        assert out.stdout == first
    golden = (BACKEND / manifest["fixtures"][0]["ll"]).read_text(encoding="utf-8")
    assert first == golden


def test_corpus_covers_every_node_kind() -> None:
    """The manifest records coverage per kind and the uncovered list is empty."""

    manifest = json.loads((BACKEND / "manifest.json").read_text(encoding="utf-8"))
    covered = {kind for entry in manifest["fixtures"] for kind in entry["covers"]}
    uncovered = sorted((ir_mod.EXPR_KINDS | ir_mod.STMT_KINDS) - covered)
    assert uncovered == []
    assert len(manifest["fixtures"]) >= len(ir_mod.EXPR_KINDS | ir_mod.STMT_KINDS)
