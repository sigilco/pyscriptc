"""Unit tests for the MIR leaf slice: emitter byte-identity and the verifier.

The emitter tests pin that lowering a leaf-slice module through MIR and
emitting from it produces the same LLVM bytes as the direct path. The
verifier tests pin the stable `MVS` codes on hand-built broken functions.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pyscriptc.emit.llvm import emit_llvm
from pyscriptc.lower import lower_file
from pyscriptc.mir import (
    MIR_B,
    MIR_BR,
    MIR_CLTW,
    MIR_JMP,
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
