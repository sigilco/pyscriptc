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
    MIR_GETATTR,
    MIR_INITCALL,
    MIR_JMP,
    MIR_MAP_GET,
    MIR_MAP_HAS,
    MIR_MAP_LEN,
    MIR_MAP_PUT,
    MIR_NEWOBJ,
    MIR_P,
    MIR_S,
    MIR_SETATTR,
    MIR_VOID,
    MIR_W,
    MirBlock,
    MirBuilder,
    MirError,
    MirFunction,
    MirModule,
    module_hash,
    module_serialize,
    serialize,
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
        "leafstr.py",
        "leafstrcmp.py",
        "leafstrcall.py",
        "leafstrconst.py",
        "leafstrargs.py",
        "leafstrbuiltins.py",
        "leafstrops.py",
        "leafand.py",
        "leafcast.py",
        "leaflist.py",
        "leaflistfloat.py",
        "leaflistfor.py",
        "leafprint.py",
        "leafprintsep.py",
        "leafrec.py",
        "leafbit.py",
        "leaftuple.py",
        "leafdict.py",
        "leafstrlist.py",
        "leafstrindex.py",
        "leafuserclass.py",
        "leafmap.py",
        "leafzip.py",
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


def test_sequence_arg3_is_in_digest() -> None:
    for stem, element_tag in (("leaflist", "a3=0"), ("leaflistfloat", "a3=1")):
        module, _ = lower_file(str(_FIXTURES / f"{stem}.py"))
        text = module_serialize(lower_module(module))
        instr_lines = [line for line in text.splitlines() if line.startswith("instr ")]
        assert instr_lines
        assert all(" a3=" in line for line in instr_lines)
        assert element_tag in text


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


class TestStrConstPool:
    """The `MIR_S` constant pool entry: interning, the `const N s` serialize
    line, and builder admission for params, results and copies."""

    def _fn(self) -> MirFunction:
        b = MirBuilder("f")
        b.set_result(MIR_S)
        b.start_block()
        return b.fn

    def test_const_s_interns_once_and_serializes_hex(self) -> None:
        b = MirBuilder("f")
        b.set_result(MIR_VOID)
        b.start_block()
        first = b.const_s("a\nb")
        second = b.const_s("a\nb")
        assert first == second
        b.const_s("")
        b.emit_ret_void()
        text = serialize(b.finish())
        assert "const 0 s 3 610a62" in text
        assert "const 1 s 0\n" in text

    def test_str_param_result_and_copy_verify(self) -> None:
        b = MirBuilder("f")
        b.set_result(MIR_S)
        p = b.add_param(MIR_S)
        b.start_block()
        v = b.emit_copy(p, MIR_S)
        b.emit_ret(v)
        assert verify_function(b.finish()) == []

    def test_builder_refuses_other_value_tags(self) -> None:
        b = MirBuilder("f")
        with pytest.raises(MirError):
            b.add_param(9)
        b2 = MirBuilder("g")
        with pytest.raises(MirError):
            b2.set_result(9)

    def test_hash_stable_with_str_consts(self) -> None:
        b = MirBuilder("f")
        b.set_result(MIR_S)
        b.start_block()
        c = b.const_s("héllo")
        b.emit_ret(c)
        module = MirModule("x.py", [b.finish()], entry="f")
        assert module_hash(module) == module_hash(module)


class TestStrVerifierViolations:
    def test_ret_of_str_const_against_word_result(self) -> None:
        b = MirBuilder("f")
        b.set_result(MIR_W)
        b.start_block()
        c = b.const_s("x")
        b.emit_ret(c)
        errs = verify_function(b.finish())
        assert any(e.code == CODE_TYPE for e in errs)

    def test_br_on_str_value(self) -> None:
        b = MirBuilder("f")
        b.set_result(MIR_W)
        p = b.add_param(MIR_S)
        b.start_block()
        b.emit_jmp(1)
        b.start_block()
        b.emit_ret(p)
        b.start_block()
        b.emit_ret(p)
        fn = b.finish()
        i = next(i for i, op in enumerate(fn.ops) if op == MIR_JMP)
        fn.ops[i] = MIR_BR
        fn.arg1[i] = p
        fn.arg2[i] = 2
        fn.blocks[0].succ1 = 1
        fn.blocks[0].succ2 = 2
        errs = verify_function(fn)
        assert any(e.code == CODE_TYPE for e in errs)

    def test_str_copy_into_word_result_rejected(self) -> None:
        b = MirBuilder("f")
        b.set_result(MIR_W)
        b.start_block()
        c = b.const_s("x")
        v = b.emit_copy(c, MIR_S)
        b.emit_ret(v)
        errs = verify_function(b.finish())
        assert any(e.code == CODE_TYPE for e in errs)


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
        # `main` prints its result: the none-typed `print` builtin is in
        # the slice, so the entry function lowers too.
        assert mir.skipped == []
        assert "main" in by_name
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
            "def shout(a: int) -> int:\n    return min(1, 2)\n\n\ndef main() -> None:\n    pass\n",
            encoding="utf-8",
        )
        module, _ = lower_file(str(src))
        mir = lower_module(module)
        assert "shout" in mir.skipped


class TestPrintSlice:
    """The `print` builtin slice: a none-typed `print` call with scalar
    and str arguments lowers to a void `MIR_CALL` the emitter renders
    through the direct `_print_one` walk; kwargs and other kinds stay
    out."""

    def test_print_functions_in_slice(self) -> None:
        module, _ = lower_file(str(_FIXTURES / "leafprint.py"))
        mir = lower_module(module)
        assert mir.skipped == []
        by_name = {fn.name: fn for fn in mir.functions}
        main_fn = by_name["main"]
        assert MIR_CALL in main_fn.ops
        assert sorted(main_fn.callees.values()) == ["positive", "print", "twice"]
        for fn in mir.functions:
            assert verify_function(fn) == []

    def test_print_kwargs_and_non_scalars_stay_out(self) -> None:
        module, _ = lower_file(str(_FIXTURES / "leafprintsep.py"))
        mir = lower_module(module)
        assert sorted(mir.skipped) == ["p_bytes", "p_end", "p_sep", "p_stderr"]
        names = {fn.name for fn in mir.functions}
        assert "p_str" in names
        assert "p_stdout" in names
        assert "main" in names
        for fn in mir.functions:
            assert verify_function(fn) == []

    def test_print_call_emits_void_call(self) -> None:
        module, _ = lower_file(str(_FIXTURES / "leafprint.py"))
        mir = lower_module(module)
        by_name = {fn.name: fn for fn in mir.functions}
        main_fn = by_name["main"]
        # Every print call is a void `MIR_CALL` marked in `helpers`;
        # the callee table carries the builtin's name once.
        print_calls = [
            i for i, op in enumerate(main_fn.ops) if op == MIR_CALL and main_fn.callees[main_fn.arg1[i]] == "print"
        ]
        assert print_calls
        for i in print_calls:
            assert main_fn.helpers.get(i) == "print"
            assert main_fn.arg2[i] == MIR_VOID


class TestRecursionSlice:
    """The recursion slice: the module-level callee fixpoint is a greatest
    fixpoint, so a self-call and a mutual-recursion pair admit each other
    and lower to `MIR_CALL` byte-identical to the direct path (the
    `leafrec.py` fixture pins the bytes through `LEAF_SAMPLES`)."""

    def test_recursive_functions_lower_to_mir_call(self) -> None:
        path = str(_FIXTURES / "leafrec.py")
        module, _ = lower_file(path)
        mir = lower_module(module)
        by_name = {fn.name: fn for fn in mir.functions}
        # `main` calls the admitted `print` builtin, so the whole module
        # lowers: nothing is skipped.
        assert mir.skipped == []
        assert "main" in by_name
        assert "fib" in by_name
        assert "is_even" in by_name
        assert "is_odd" in by_name
        fib_fn = by_name["fib"]
        assert MIR_CALL in fib_fn.ops
        assert sorted(fib_fn.callees.values()) == ["fib"]
        even_fn = by_name["is_even"]
        assert sorted(even_fn.callees.values()) == ["is_odd"]
        for fn in mir.functions:
            assert verify_function(fn) == []

    def test_caller_of_out_of_slice_callee_stays_out(self, tmp_path: Path) -> None:
        # The greatest fixpoint must not admit a function whose callee is
        # out of the slice by its own shape: `bad` calls the `abs`
        # builtin, so it is not a candidate, and `wrap` stays out even
        # though `wrap`'s own shape is leaf-clean.
        src = tmp_path / "recbad.py"
        src.write_text(
            "def bad(n: int) -> int:\n    return abs(n)\n\n\n"
            "def wrap(n: int) -> int:\n    return bad(n)\n\n\n"
            "def main() -> None:\n    pass\n",
            encoding="utf-8",
        )
        module, _ = lower_file(str(src))
        mir = lower_module(module)
        names = {fn.name for fn in mir.functions}
        assert "bad" not in names
        assert "wrap" not in names


class TestClassSlice:
    """The tuple-class slice: `tuple_N_*` unpacking constructors, field
    writes and reads, class-valued params, args and returns all ride
    `MIR_P`; the class name and field key ride `MIR_S` constants the
    emitter reads as keys, never as runtime strings."""

    def test_tuple_functions_in_slice(self) -> None:
        module, _ = lower_file(str(_FIXTURES / "leaftuple.py"))
        mir = lower_module(module)
        assert mir.skipped == []
        names = {fn.name for fn in mir.functions}
        assert {"swap", "make", "read", "main"} <= names
        for fn in mir.functions:
            assert verify_function(fn) == []

    def test_class_ops_lower(self) -> None:
        module, _ = lower_file(str(_FIXTURES / "leaftuple.py"))
        mir = lower_module(module)
        by_name = {fn.name: fn for fn in mir.functions}
        swap_fn = by_name["swap"]
        assert MIR_NEWOBJ in swap_fn.ops
        assert MIR_SETATTR in swap_fn.ops
        assert MIR_GETATTR in swap_fn.ops
        # `make` returns the tuple class: its MIR result tag is MIR_P.
        make_fn = by_name["make"]
        assert make_fn.result == MIR_P
        # `read` takes the class as a MIR_P param.
        read_fn = by_name["read"]
        assert [p.typ for p in read_fn.params] == [MIR_P]
        # `main` passes the `make` result into `read` as a class arg:
        # the call's arg list carries a MIR_P operand.
        main_fn = by_name["main"]
        call_ops = [i for i, op in enumerate(main_fn.ops) if op == MIR_CALL]
        assert call_ops

    def test_user_class_constructor_slice(self) -> None:
        # A user class whose fields are class-level annotations and whose
        # constructor arguments stay in the slice rides `MIR_INITCALL`:
        # `Pair(n, n + 1)` lowers to the alloc-plus-`__init__` op, the
        # `-> Pair` return rides `MIR_P`, and `main`'s none-typed `print`
        # call carries the nested user calls.
        module, _ = lower_file(str(_FIXTURES / "leafuserclass.py"))
        assert module["fences"] == []
        mir = lower_module(module)
        assert mir.skipped == []
        by_name = {fn.name: fn for fn in mir.functions}
        assert {"make_pair", "sum_pair", "build", "main"} <= set(by_name)
        build_fn = by_name["build"]
        assert MIR_INITCALL in build_fn.ops
        assert build_fn.result == MIR_P
        make_fn = by_name["make_pair"]
        assert MIR_INITCALL in make_fn.ops
        for fn in mir.functions:
            assert verify_function(fn) == []

    def test_fenced_user_class_stays_out(self, tmp_path: Path) -> None:
        # The same class shape without the class-level field annotation
        # fences `__init__` and `main` upstream (PS2001), so nothing
        # reaches the slice predicates at all.
        src = tmp_path / "userclass.py"
        src.write_text(
            "class Node:\n"
            "    def __init__(self) -> None:\n"
            "        self.v = 1\n"
            "\n"
            "\n"
            "def main() -> None:\n"
            "    n = Node()\n"
            "    print(n.v)\n",
            encoding="utf-8",
        )
        module, _ = lower_file(str(src))
        mir = lower_module(module)
        assert not any(fn.name == "main" for fn in mir.functions)


class TestMapSlice:
    """The dict lane: `d[k]` reads, `d[k] = v` stores, `k in d`
    membership and `len(d)` lower to `MIR_MAP_GET`/`MIR_MAP_PUT`/
    `MIR_MAP_HAS`/`MIR_MAP_LEN`, and `str.split`/`rsplit` ride the
    marked method call; the fixture pins byte identity through
    `LEAF_SAMPLES`."""

    def test_map_functions_in_slice(self) -> None:
        module, _ = lower_file(str(_FIXTURES / "leafmap.py"))
        mir = lower_module(module)
        assert mir.skipped == []
        names = {fn.name for fn in mir.functions}
        assert {"tally", "lookup", "parts", "main"} <= names
        for fn in mir.functions:
            assert verify_function(fn) == []

    def test_map_ops_lower(self) -> None:
        module, _ = lower_file(str(_FIXTURES / "leafmap.py"))
        mir = lower_module(module)
        by_name = {fn.name: fn for fn in mir.functions}
        tally_fn = by_name["tally"]
        assert MIR_MAP_GET in tally_fn.ops
        assert MIR_MAP_PUT in tally_fn.ops
        assert MIR_MAP_HAS in tally_fn.ops
        assert MIR_MAP_LEN in tally_fn.ops
        lookup_fn = by_name["lookup"]
        assert MIR_MAP_HAS in lookup_fn.ops
        assert MIR_MAP_GET in lookup_fn.ops
        main_fn = by_name["main"]
        assert MIR_MAP_LEN in main_fn.ops


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
        names = {fn.name for fn in mir.functions}
        assert "main" in names
        assert "land_int" in names
        assert "land_calls" in names

    def test_ifexp_functions_in_slice(self) -> None:
        module, mir = self._lower("leafifexp.py")
        names = {fn.name for fn in mir.functions}
        assert "main" in names
        assert "ifexp_int" in names
        assert "ifexp_calls" in names

    def test_aug_functions_in_slice(self) -> None:
        module, mir = self._lower("leafaug.py")
        names = {fn.name for fn in mir.functions}
        assert "main" in names
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
