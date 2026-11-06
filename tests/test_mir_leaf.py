"""Unit tests for the MIR leaf slice: emitter byte-identity and the verifier.

The emitter tests pin that lowering a leaf-slice module through MIR and
emitting from it produces the same LLVM bytes as the direct path. The
verifier tests pin the stable `MVS` codes on hand-built broken functions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pyscriptc.emit.llvm import emit_llvm
from pyscriptc.lower import lower_file
from pyscriptc.mir import (
    MIR_B,
    MIR_BR,
    MIR_CALL,
    MIR_CLTW,
    MIR_D,
    MIR_JMP,
    MIR_VOID,
    MIR_W,
    MirBlock,
    MirBuilder,
    MirError,
    module_hash,
)
from pyscriptc.miremit import emit_llvm_mir
from pyscriptc.mirlower import lower_module
from pyscriptc.mirverify import (
    CODE_CONTROL,
    CODE_TYPE,
    CODE_USEDEF,
    verify_function,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "mir_leaf"
LEAF_SAMPLES = [
    str(_FIXTURES / name)
    for name in (
        "leafint.py",
        "leafbool.py",
        "mirleaf.py",
        "mirmix.py",
        "leafboolret.py",
        "leafmulret.py",
        "leafneg.py",
        "leafboolconst.py",
        "leafhelperret.py",
        "leaforder.py",
        "leafwhile.py",
        "leafbreak.py",
        "leafforrange.py",
        "leafelif.py",
        "leafnone.py",
        "leafcall.py",
        "leafshortcircuit.py",
        "leafifexp.py",
        "leafaug.py",
    )
]


@pytest.mark.parametrize("path", LEAF_SAMPLES)
class TestByteIdentity:
    def test_direct_vs_mir_bytes(self, path: str) -> None:
        module, _ = lower_file(path)
        direct = emit_llvm(module)
        mir = emit_llvm_mir(module)
        assert mir == direct

    def test_mir_hash_stable(self, path: str) -> None:
        module, _ = lower_file(path)
        assert module_hash(lower_module(module)) == module_hash(lower_module(module))

    def test_mir_lowered_verifies(self, path: str) -> None:
        module, _ = lower_file(path)
        mmod = lower_module(module)
        for fn in mmod.functions:
            assert verify_function(fn) == []


class TestVerifierViolations:
    @staticmethod
    def _good(name: str = "f") -> MirBuilder:
        # Entry compares its param, then branches; the targets must exist
        # before `emit_br` names them, so the builder gets a placeholder
        # `jmp` that the test patches into a `br` after `finish`.
        b = MirBuilder(name)
        b.set_result(MIR_W)
        p = b.add_param(MIR_W)
        b.start_block()
        c = b.const_w(1)
        s = b.emit_val(MIR_CLTW, p, c, MIR_B)
        two = b.const_w(2)
        b.emit_jmp(1)
        b.start_block()
        b.emit_ret(two)
        b.start_block()
        b.emit_ret(c)
        fn = b.finish()
        i = next(i for i, op in enumerate(fn.ops) if op == MIR_JMP)
        fn.ops[i] = MIR_BR
        fn.arg1[i] = s
        fn.arg2[i] = 2
        fn.blocks[0].succ1 = 1
        fn.blocks[0].succ2 = 2
        return b

    def test_valid_function_has_no_violations(self) -> None:
        fn = self._good().finish()
        assert verify_function(fn) == []

    def test_use_before_def(self) -> None:
        b = MirBuilder("f")
        b.set_result(MIR_W)
        b.start_block()
        ghost = 99  # never defined
        b.emit_ret(ghost)
        errs = verify_function(b.finish())
        assert errs and all(e.code == CODE_USEDEF for e in errs)

    def test_br_condition_must_be_bool(self) -> None:
        b = MirBuilder("f")
        b.set_result(MIR_W)
        b.start_block()
        word = b.const_w(7)
        b.emit_jmp(1)
        b.start_block()
        b.emit_ret(word)
        b.start_block()
        b.emit_ret(word)
        fn = b.finish()
        i = next(i for i, op in enumerate(fn.ops) if op == MIR_JMP)
        fn.ops[i] = MIR_BR
        fn.arg1[i] = word
        fn.arg2[i] = 2
        fn.blocks[0].succ1 = 1
        fn.blocks[0].succ2 = 2
        errs = verify_function(fn)
        assert any(e.code == CODE_TYPE for e in errs)

    def test_missing_terminator(self) -> None:
        b = MirBuilder("f")
        b.set_result(MIR_W)
        b.start_block()
        b.const_w(1)
        # Hand-build a second block directly: the builder refuses to open a
        # block without a terminator, so bypass it to forge the violation.
        fn = b.fn
        fn.blocks.append(MirBlock(bid=1, args=[]))
        fn.blocks[0].succ1 = 1
        errs = verify_function(fn)
        assert any(e.code == CODE_CONTROL for e in errs)

    def test_jmp_to_missing_block(self) -> None:
        b = MirBuilder("f")
        b.set_result(MIR_W)
        b.start_block()
        b.emit_jmp(5)
        errs = verify_function(b.finish())
        assert any(e.code == CODE_CONTROL for e in errs)

    def test_dangling_open_block_refused_by_builder(self) -> None:
        b = MirBuilder("f")
        b.set_result(MIR_W)
        b.start_block()
        with pytest.raises(MirError):
            b.finish()

    def test_copy_type_rides_arg2(self) -> None:
        b = MirBuilder("f")
        b.set_result(MIR_B)
        b.start_block()
        c = b.const_b(1)
        v = b.emit_copy(c, MIR_B)
        b.emit_ret(v)
        assert verify_function(b.finish()) == []

    def test_mismatched_ret_type(self) -> None:
        b = MirBuilder("f")
        b.set_result(MIR_W)
        b.start_block()
        c = b.const_b(1)
        b.emit_ret(c)
        errs = verify_function(b.finish())
        assert any(e.code == CODE_TYPE for e in errs)

    def test_void_ret_verifies(self) -> None:
        b = MirBuilder("f")
        b.set_result(MIR_VOID)
        b.start_block()
        b.emit_ret_void()
        assert verify_function(b.finish()) == []

    def test_void_result_rejects_value_ret(self) -> None:
        b = MirBuilder("f")
        b.set_result(MIR_VOID)
        b.start_block()
        c = b.const_w(1)
        b.emit_ret(c)
        errs = verify_function(b.finish())
        assert any(e.code == CODE_TYPE for e in errs)

    def test_value_result_rejects_void_ret(self) -> None:
        b = MirBuilder("f")
        b.set_result(MIR_W)
        b.start_block()
        b.emit_ret_void()
        errs = verify_function(b.finish())
        assert any(e.code == CODE_TYPE for e in errs)

    def test_call_verifies_and_types_its_result(self) -> None:
        b = MirBuilder("f")
        b.set_result(MIR_W)
        p = b.add_param(MIR_W)
        b.start_block()
        arg = b.const_w(2)
        r = b.emit_call(p, [arg], MIR_W)
        b.emit_ret(r)
        assert verify_function(b.finish()) == []

    def test_void_call_verifies(self) -> None:
        b = MirBuilder("f")
        b.set_result(MIR_VOID)
        p = b.add_param(MIR_W)
        b.start_block()
        arg = b.const_w(2)
        b.emit_call(p, [arg], MIR_VOID)
        b.emit_ret_void()
        assert verify_function(b.finish()) == []

    def test_void_call_with_dst_refused_by_builder(self) -> None:
        b = MirBuilder("f")
        b.set_result(MIR_VOID)
        b.start_block()
        with pytest.raises(MirError):
            b.emit_call(0, [], "void")  # type: ignore[arg-type]

    def test_call_bad_result_tag(self) -> None:
        b = MirBuilder("f")
        b.set_result(MIR_VOID)
        b.start_block()
        b.emit(MIR_CALL, -1, 0, 9)
        b.emit_ret_void()
        fn = b.finish()
        fn.call_args[0] = []
        errs = verify_function(fn)
        assert any(e.code == CODE_TYPE for e in errs)

    def test_value_call_without_dst(self) -> None:
        b = MirBuilder("f")
        b.set_result(MIR_VOID)
        b.start_block()
        b.emit(MIR_CALL, -1, 0, MIR_W)
        b.emit_ret_void()
        fn = b.finish()
        fn.call_args[0] = []
        errs = verify_function(fn)
        assert any(e.code == CODE_USEDEF for e in errs)

    def test_void_call_writing_dst(self) -> None:
        b = MirBuilder("f")
        b.set_result(MIR_VOID)
        b.start_block()
        b.emit(MIR_CALL, 0, 0, MIR_VOID)
        b.emit_ret_void()
        fn = b.finish()
        fn.call_args[0] = []
        errs = verify_function(fn)
        assert any(e.code == CODE_USEDEF for e in errs)

    def test_call_without_call_args_entry(self) -> None:
        b = MirBuilder("f")
        b.set_result(MIR_VOID)
        b.start_block()
        b.emit(MIR_CALL, -1, 0, MIR_VOID)
        b.emit_ret_void()
        errs = verify_function(b.finish())
        assert any(e.code == CODE_USEDEF for e in errs)

    def test_call_arg_use_before_def(self) -> None:
        b = MirBuilder("f")
        b.set_result(MIR_VOID)
        b.start_block()
        b.emit(MIR_CALL, -1, 0, MIR_VOID)
        b.emit_ret_void()
        fn = b.finish()
        fn.call_args[0] = [99]
        errs = verify_function(fn)
        assert any(e.code == CODE_USEDEF for e in errs)

    def test_call_result_feeds_ret(self) -> None:
        b = MirBuilder("f")
        b.set_result(MIR_D)
        p = b.add_param(MIR_D)
        b.start_block()
        r = b.emit_call(p, [], MIR_D)
        b.emit_ret(r)
        assert verify_function(b.finish()) == []


class TestCallSlice:
    """The call slice (A3): a call to a plain module-level user function
    with scalar args and result, and the void call, lower to `MIR_CALL`
    and emit byte-identical to the direct emitter."""

    def test_leafcall_fixture_byte_identical(self) -> None:
        path = str(_FIXTURES / "leafcall.py")
        module, _ = lower_file(path)
        assert emit_llvm_mir(module) == emit_llvm(module)

    def test_call_functions_lower_to_mir_call(self) -> None:
        path = str(_FIXTURES / "leafcall.py")
        module, _ = lower_file(path)
        mir = lower_module(module)
        by_name = {fn.name: fn for fn in mir.functions}
        assert "main" in mir.skipped
        add_fn = by_name["add"]
        assert add_fn.call_args == {}
        total_fn = by_name["total"]
        assert sorted(total_fn.callees.values()) == ["add", "note", "scale"]
        assert MIR_CALL in total_fn.ops
        for fn in mir.functions:
            assert verify_function(fn) == []

    def test_builtin_and_method_calls_stay_out(self, tmp_path: Path) -> None:
        src = tmp_path / "outcalls.py"
        src.write_text(
            "def shout(a: int) -> int:\n    return len(str(a))\n\n\ndef main() -> None:\n    pass\n",
            encoding="utf-8",
        )
        module, _ = lower_file(str(src))
        mir = lower_module(module)
        assert "shout" in mir.skipped


class TestShortCircuitSlice:
    """The short-circuit slice (A6): `and`/`or` and the ternary fold
    into the existing shape (the IR desugars both into temp assigns plus
    one-level ifs), so no new MIR op is needed; the fixtures pin both
    operand orders, scalar int/float/bool operands, and byte identity."""

    @staticmethod
    def _lower(name: str) -> Any:
        module, _ = lower_file(str(_FIXTURES / name))
        return module, lower_module(module)

    def test_shortcircuit_functions_in_slice(self) -> None:
        module, mir = self._lower("leafshortcircuit.py")
        assert "main" in mir.skipped
        names = {fn.name for fn in mir.functions}
        assert "land_int" in names
        assert "land_calls" in names

    def test_ifexp_functions_in_slice(self) -> None:
        module, mir = self._lower("leafifexp.py")
        assert "main" in mir.skipped
        names = {fn.name for fn in mir.functions}
        assert "ifexp_int" in names
        assert "ifexp_calls" in names

    def test_aug_functions_in_slice(self) -> None:
        module, mir = self._lower("leafaug.py")
        assert "main" in mir.skipped
        names = {fn.name for fn in mir.functions}
        assert "aug_in_loop" in names

    def test_augassign_folds_to_plain_assign(self) -> None:
        # `x += e` desugars in the IR to `x = x + e`, so the slice test
        # sees only the `assign` kind the body walk already lowers. The
        # fixture pins byte identity through LEAF_SAMPLES; this pins
        # that no AugAssign-shaped node reaches the walk.
        module, _ = self._lower("leafaug.py")
        by_name = {fn["name"]: fn for fn in module["functions"]}
        body = by_name["aug_in_loop"]["body"]
        kinds = [s["kind"] for s in body]
        assert "augassign" not in kinds
        assert "assign" in kinds
