"""The emitter package: the IR-reading seam and the LLVM emitter split.

`llvm.py` used to be one file with the whole emitter in it. It is now a
package under `pyscriptc.emit`: a shared IR-reading seam plus the LLVM
emitter split by concern. These tests pin the shape of that split and the
behavior that must not move:

- the public import path (`pyscriptc.llvm`) still resolves,
- the seam is importable without the LLVM emitter (a second backend reads the
  IR through it),
- the emitted text is byte-identical to the pre-split emitter,
- every emitter area lives in its own module.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "pyscriptc"
EXAMPLES = ROOT / "examples"


def test_llvm_module_is_a_shim_that_reexports_the_emitter() -> None:
    import pyscriptc.llvm as llvm

    for name in ("emit_llvm", "compile_unit", "link_binary", "standalone_entry", "Emitter"):
        assert hasattr(llvm, name), name


def test_the_emitter_lives_in_the_emit_package() -> None:
    from pyscriptc.emit.llvm import Emitter

    assert Emitter.__module__ == "pyscriptc.emit.llvm._driver"
    source = (SRC / "llvm.py").read_text(encoding="utf-8")
    assert len(source.splitlines()) < 60, "llvm.py is a shim, not the emitter"


def test_the_seam_imports_without_the_llvm_emitter() -> None:
    """A second backend consumes the seam without pulling in LLVM code."""

    probe = (
        "import sys\n"
        "from pyscriptc.emit import _ir\n"
        "assert 'pyscriptc.emit.llvm' not in sys.modules, "
        "sorted(m for m in sys.modules if m.startswith('pyscriptc.emit'))\n"
        "assert _ir._llvm_type({'kind': 'int'}) == 'i64'\n"
        "print('ok')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=ROOT,
        env={"PYTHONPATH": str(SRC), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "ok" in proc.stdout


def test_the_seam_knows_the_ir_type_mapping() -> None:
    from pyscriptc.emit import _ir

    assert _ir._llvm_type({"kind": "int"}) == "i64"
    assert _ir._llvm_type({"kind": "bool"}) == "i1"
    assert _ir._llvm_type({"kind": "float"}) == "double"
    assert _ir._llvm_type({"kind": "str"}) == "%ps.str"
    assert _ir._llvm_type({"kind": "none"}) == "void"
    assert _ir._llvm_type({"kind": "list", "elem": {"kind": "int"}}) == "ptr"


def test_the_seam_reads_the_kind_tag() -> None:
    from pyscriptc.emit import _ir

    assert _ir._kind({"kind": "int"}) == "int"
    assert _ir._kind({"kind": "str"}) == "str"


def test_the_seam_reads_the_expression_kind_tag() -> None:
    from pyscriptc.emit import _ir

    assert _ir._expr_kind({"kind": "const"}) == "const"
    assert _ir._expr_kind({"kind": "closure"}) == "closure"


def test_the_seam_reads_an_absent_kind_tag() -> None:
    from pyscriptc.emit import _ir

    assert _ir._kind_or_none({"kind": "int"}) == "int"
    assert _ir._kind_or_none({}) is None
    assert _ir._kind_or_none(None) is None


def test_the_seam_reads_the_node_children() -> None:
    """The node-shaped readers `_exprs` uses: child expressions, lists,
    pairs, flags and payloads."""
    from pyscriptc.emit import _ir

    span = {"file": "a.py", "line": 1}
    typ = {"kind": "int"}
    left = {"kind": "const", "type": typ, "value": "1", "span": span}
    right = {"kind": "name", "id": "x", "type": typ, "span": span}
    binary = {"kind": "binary", "op": "+", "left": left, "right": right, "type": typ, "span": span}
    assert _ir._node_op(binary) == "+"
    assert _ir._node_left(binary) == left
    assert _ir._node_right(binary) == right
    assert _ir._node_id(right) == "x"

    subscript = {"kind": "subscript", "value": right, "index": left, "type": typ, "span": span}
    assert _ir._node_value_node(subscript) == right
    assert _ir._node_index(subscript) == left

    contains = {"kind": "contains", "hay": left, "needle": right, "negated": True, "type": typ, "span": span}
    assert _ir._node_hay(contains) == left
    assert _ir._node_needle(contains) == right
    assert _ir._node_negated(contains) is True
    assert _ir._node_negated({"kind": "contains", "hay": left, "needle": right, "type": typ, "span": span}) is False

    unary = {"kind": "unary", "op": "-", "operand": left, "type": typ, "span": span}
    assert _ir._node_operand(unary) == left

    list_node = {"kind": "list", "elems": [left, right], "type": typ, "span": span}
    assert _ir._node_elems(list_node) == [left, right]
    assert _ir._node_elems({"kind": "list", "type": typ, "span": span}) == []

    pair = {"key": left, "value": right}
    assert _ir._pair_key(pair) == left
    assert _ir._pair_value(pair) == right
    dict_node = {"kind": "dict", "pairs": [pair], "type": typ, "span": span}
    assert _ir._node_pairs(dict_node) == [pair]
    assert _ir._node_pairs({"kind": "dict", "type": typ, "span": span}) == []

    value_callee = {"kind": "value", "target": right}
    assert _ir._node_target_node(value_callee) == right
    call = {"kind": "call", "callee": value_callee, "args": [left], "type": typ, "span": span}
    assert _ir._node_callee(call) == value_callee
    assert _ir._node_args(call) == [left]
    assert _ir._node_args({"kind": "call", "callee": value_callee, "type": typ, "span": span}) == []

    closure = {"kind": "closure", "name": "inner", "env": [], "type": typ, "span": span}
    assert _ir._node_name(closure) == "inner"
    assert _ir._node_env(closure) == []
    assert _ir._node_env({"kind": "closure", "name": "inner", "type": typ, "span": span}) == []

    attr = {"kind": "attr", "name": "field", "type": typ, "value": right, "span": span}
    assert _ir._node_name(attr) == "field"

    item_init = {"init": left, "type": typ}
    item_name = {"name": "y", "type": typ}
    assert _ir._env_item_init(item_init) == left
    assert _ir._env_item_init(item_name) is None
    assert _ir._env_item_name(item_name) == "y"


def test_the_seam_reads_the_abi_and_call_records() -> None:
    """The record readers `_abi` and `_calls` use: the runtime symbol, the
    thread and island plans, the shim specs, the callee and arg payloads."""
    from pyscriptc.emit import _ir

    typ = {"kind": "int"}
    left = {"kind": "name", "id": "x", "type": typ}
    right = {"kind": "name", "id": "y", "type": typ}

    assert _ir._node_symbol({"kind": "expr", "symbol": "ps_seq_new"}) == "ps_seq_new"
    assert _ir._node_fn_name({"kind": "expr", "fn": "add"}) == "add"

    closure = {"kind": "closure", "name": "worker", "type": typ}
    thread = {"closure": closure, "size": 16}
    assert _ir._node_thread({"kind": "expr", "thread": thread}) == thread
    assert _ir._thread_closure(thread) == closure
    assert _ir._thread_size(thread) == 16

    island = {"name": "render", "want": typ}
    assert _ir._node_island({"kind": "expr", "island": island}) == island
    assert _ir._island_want(island) == typ
    assert _ir._island_text(island) == "render"
    assert _ir._island_key({"key": "q"}) == "q"
    assert _ir._node_island_fn({"kind": "expr", "island_fn": island}) == island

    spec = {"bound": [typ], "params": [], "return": typ}
    assert _ir._spec_bound(spec) == [typ]
    assert _ir._spec_params(spec) == []
    assert _ir._spec_return(spec) == typ

    assert _ir._arg_type({"name": "x", "type": typ}) == typ
    assert _ir._elem_of({"kind": "list", "elem": typ}) == typ
    assert _ir._key_of({"kind": "dict", "key": left, "value": right}) == left
    assert _ir._value_of({"kind": "dict", "key": left, "value": right}) == right
    cls = {"name": "tuple_2", "fields": [{"name": "_0", "type": typ}]}
    assert _ir._class_fields(cls) == cls["fields"]

    assert _ir._opt_record({"want": typ}, "want") == typ
    assert _ir._opt_record({}, "want") is None
    marked = _ir._mark_fmt(typ)
    assert marked["fmt"] == 1
    assert {k: v for k, v in marked.items() if k != "fmt"} == typ

    assert _ir._callee_kind({"kind": "module"}) == "module"
    assert _ir._callee_name({"kind": "builtin", "name": "len"}) == "len"
    assert _ir._callee_module({"kind": "module", "module": "math"}) == "math"
    assert _ir._callee_receiver({"kind": "method", "receiver": "str"}) == "str"
    assert _ir._callee_tuple({"kind": "method", "tuple": "tuple_2_int_str"}) == "tuple_2_int_str"


def test_the_abi_and_calls_emitters_read_no_raw_node_key() -> None:
    """`_abi` and `_calls` walk the IR through the `_ir` seam: no raw
    string-literal dict key read on an IR node stays in either module.
    The exception-table lookups and the dict-literal write are not IR
    reads, so they stay out of the key set."""

    ir_keys = {
        "kind",
        "type",
        "span",
        "value",
        "object",
        "callee",
        "args",
        "env",
        "stub",
        "thread_stub",
        "thread",
        "operand",
        "left",
        "right",
        "hay",
        "needle",
        "elems",
        "pairs",
        "index",
        "cond",
        "expr",
        "target",
        "fn",
        "symbol",
        "island",
        "island_fn",
        "closure",
        "size",
        "want",
        "name",
        "key",
        "elem",
        "bound",
        "params",
        "return",
        "receiver",
        "tuple",
        "module",
        "fields",
        "id",
        "op",
        "island_npos",
        "island_nkw",
        "island_starred",
        "island_kwargs",
        "daemon",
        "islandSource",
        "json_flags",
        "format_kwnames",
        "format_style",
        "fmt",
        "returnType",
    }
    for name in ("_abi.py", "_calls.py"):
        source = (SRC / "emit" / "llvm" / name).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Subscript):
                continue
            if not isinstance(node.slice, ast.Constant) or not isinstance(node.slice.value, str):
                continue
            assert isinstance(node.ctx, ast.Load), f"{name}:{node.lineno}: raw key write"
            key = node.slice.value
            assert key not in ir_keys, f"{name}:{node.lineno}: raw read of IR key {key!r}; use the _ir seam"


def test_the_seam_reads_the_function_record() -> None:
    """The record readers `_funcs` uses: name, params, span, return type, env, boxed."""
    from pyscriptc.emit import _ir

    fn = {
        "name": "main",
        "params": [{"name": "x", "type": {"kind": "int"}}],
        "returnType": {"kind": "none"},
        "locals": [],
        "body": [],
        "span": {"file": "a.py", "line": 1},
        "env": [{"name": "y", "type": {"kind": "str"}}],
        "boxed": ["y"],
    }
    assert _ir._opt_str(fn, "name") == "main"
    assert _ir._return_type(fn) == {"kind": "none"}
    assert _ir._span_of(fn) == {"file": "a.py", "line": 1}
    assert _ir._items(fn, "params") == [{"name": "x", "type": {"kind": "int"}}]
    assert _ir._items(fn, "locals") == []
    assert _ir._items(fn, "env") == [{"name": "y", "type": {"kind": "str"}}]
    assert _ir._name_list(fn, "boxed") == ["y"]
    assert _ir._has(fn, "env") is True
    assert _ir._has(fn, "stub") is False
    assert _ir._opt_str({"name": "x"}, "name") == "x"
    assert _ir._opt_str({}, "name") == ""
    assert _ir._opt_span(fn) == {"file": "a.py", "line": 1}
    assert _ir._opt_span({"name": "x"}) is None


def test_the_seam_reads_the_type_record_and_markers() -> None:
    from pyscriptc.emit import _ir

    typ = {"kind": "class", "name": "Handle", "island": True, "handle": True}
    assert _ir._type_of({"type": typ}) == typ
    assert _ir._kind(_ir._type_of({"type": typ})) == "class"
    assert _ir._flag_of(typ, "island") is True
    assert _ir._flag_of(typ, "handle") is True
    assert _ir._flag_of({"kind": "int"}, "island") is False


def test_the_seam_reads_the_optional_and_map_fields() -> None:
    from pyscriptc.emit import _ir

    param = {"name": "x", "type": {"kind": "int"}, "default": {"kind": "const", "value": "1"}}
    assert _ir._opt_default(param) == {"kind": "const", "value": "1"}
    assert _ir._opt_default({"name": "y"}) is None
    assert _ir._opt_int({"exc_parent": 7}, "exc_parent", 0) == 7
    assert _ir._opt_int({}, "exc_parent", 0) == 0
    assert _ir._opt_int({"exc_code": 3}, "exc_parent", 9) == 9
    module = {"sourceFile": "m.py", "islandGlobals": {"g": "int:1"}}
    assert _ir._str_map(module, "islandGlobals") == {"g": "int:1"}
    assert _ir._str_map({"sourceFile": "m.py"}, "islandGlobals") == {}
    assert _ir._entry_span(module) == {"file": "m.py", "line": 1}


def test_the_seam_reads_the_const_payload() -> None:
    from pyscriptc.emit import _ir

    assert _ir._const_text({"kind": "const", "value": "1"}) == "1"
    assert _ir._const_bool({"kind": "const", "value": True}) is True


def test_the_seam_builds_the_none_type() -> None:
    from pyscriptc.emit import _ir

    assert _ir._none_type() == {"kind": "none"}


def test_the_seam_reads_the_node_kind_tags() -> None:
    from pyscriptc.emit import _ir

    assert _ir._node_kind({"kind": "assign"}) == "assign"
    assert _ir._node_type_kind({"kind": "const", "type": {"kind": "int"}}) == "int"
    assert _ir._maybe_node_kind({"kind": "raise"}) == "raise"
    assert _ir._maybe_node_kind({"target": "x"}) is None
    assert _ir._maybe_node_type_kind({"kind": "name", "type": {"kind": "str"}}) == "str"
    assert _ir._maybe_node_type_kind({"kind": "pass"}) is None


def test_the_seam_knows_the_symbol_naming() -> None:
    from pyscriptc.emit import _ir

    assert _ir._fn_symbol("main") == "ps.user.main"
    assert _ir._fn_symbol("other") == "other"
    assert _ir._safe("a.b.c") == "a_b_c"


def test_the_seam_knows_the_typed_accessors() -> None:
    """The typed IR reads an emitter needs: kind tags, class and field names."""

    from pyscriptc.emit import _ir

    assert _ir._kind({"kind": "int"}) == "int"
    assert _ir._kind({"kind": "class", "name": "tuple_2_int_str"}) == "class"
    assert _ir._type_name({"kind": "class", "name": "tuple_2_int_str"}) == "tuple_2_int_str"
    field = {"name": "_0", "type": {"kind": "str"}}
    assert _ir._field_name(field) == "_0"
    assert _ir._field_type(field) == {"kind": "str"}
    assert _ir._kind(_ir._field_type(field)) == "str"
    assert _ir._has({"sep": "x"}, "sep") is True
    assert _ir._has({"sep": "x"}, "end") is False
    assert _ir._opt_str_or_none({"file": "stderr"}, "file") == "stderr"
    assert _ir._opt_str_or_none({}, "file") is None
    sep = {"kind": "const", "type": {"kind": "str"}, "value": ","}
    end = {"kind": "const", "type": {"kind": "str"}, "value": ""}
    print_call = {"kind": "call", "sep": sep, "end": end}
    assert _ir._opt_node_sep(print_call) == sep
    assert _ir._opt_node_end(print_call) == end
    assert _ir._opt_node_sep({"kind": "call"}) is None
    assert _ir._opt_node_end({"kind": "call"}) is None
    cls = {"name": "tuple_2_int_str", "fields": [field]}
    assert _ir._items(cls, "fields") == [field]


def test_the_seam_knows_the_container_accessors() -> None:
    """The typed IR reads a stdlib emitter needs: element kinds and optionality."""

    from pyscriptc.emit import _ir

    assert _ir._elem_kind({"kind": "list", "elem": {"kind": "int"}}) == "int"
    assert _ir._elem_kind({"kind": "dict", "elem": {"kind": "str"}}) == "str"
    assert _ir._is_optional({"kind": "int", "optional": True}) is True
    assert _ir._is_optional({"kind": "int"}) is False
    assert _ir._is_optional({"kind": "int", "optional": False}) is False


def test_the_seam_reads_stdlib_type_children() -> None:
    """The type-record readers `_stdlib` uses: nested elem/value types and
    the argument list a copy call carries."""
    from pyscriptc.emit import _ir

    list_t = {"kind": "list", "elem": {"kind": "int"}}
    dict_t = {"kind": "dict", "key": {"kind": "str"}, "value": {"kind": "float"}}
    opt_t = {"kind": "int", "optional": True, "elem": {"kind": "int"}}
    assert _ir._elem_type(list_t) == {"kind": "int"}
    assert _ir._elem_type(opt_t) == {"kind": "int"}
    assert _ir._map_value_type(dict_t) == {"kind": "float"}
    arg = {"kind": "name", "id": "xs", "type": list_t}
    call = {"kind": "call", "args": [arg], "type": list_t}
    assert _ir._node_args(call) == [arg]
    assert _ir._type_of(arg) == list_t


def test_every_emitter_area_lives_in_its_own_module() -> None:
    """Each area of the emission owns one file, so a change has one home."""

    area = SRC / "emit" / "llvm"
    modules = sorted(path.name for path in area.glob("_*.py") if path.name != "__init__.py")
    assert modules == [
        "_abi.py",
        "_base.py",
        "_calls.py",
        "_core.py",
        "_driver.py",
        "_exprs.py",
        "_funcs.py",
        "_print.py",
        "_runtime.py",
        "_stdlib.py",
        "_stmts.py",
    ]
    # Every area module holds exactly one class, the mixin it contributes.
    # `_runtime.py` is the exception: it is the declaration table, not an area.
    # `_base.py` holds the typed state and method stubs every mixin inherits.
    for name in modules:
        tree = ast.parse((area / name).read_text(encoding="utf-8"))
        classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
        if name == "_runtime.py":
            assert not classes, name
            continue
        if name == "_base.py":
            assert [c.name for c in classes] == ["EmitterBase"], name
            continue
        assert len(classes) == 1, name
        assert classes[0].name.endswith("Mixin") or name == "_driver.py", name


def test_the_emitter_is_composed_of_the_area_mixins() -> None:
    from pyscriptc.emit.llvm import Emitter

    names = [base.__name__ for base in Emitter.__mro__]
    assert "CoreMixin" in names
    assert "StmtsMixin" in names
    assert "ExprsMixin" in names
    assert "CallsMixin" in names
    assert "AbiMixin" in names
    assert "PrintMixin" in names
    assert "StdlibMixin" in names
    assert "FuncsMixin" in names


def test_the_emitter_has_no_duplicated_method() -> None:
    """A method defined in two areas would silently shadow one of them."""

    from pyscriptc.emit.llvm import Emitter

    seen: dict[str, str] = {}
    for base in Emitter.__mro__:
        if base.__name__ == "EmitterBase":
            # The typed stub base: its stubs sit behind every real method.
            continue
        for value in vars(base).values():
            if not callable(value) and not isinstance(value, (staticmethod, classmethod)):
                continue
            func = value.__func__ if isinstance(value, (staticmethod, classmethod)) else value
            if func.__name__ in ("__init__",):
                continue
            key = func.__name__
            assert key not in seen, f"{key} is defined in {seen.get(key)} and {base.__name__}"
            seen[key] = base.__name__


def test_the_emitted_text_matches_the_golden_corpus() -> None:
    """The golden fixtures are the byte-exact expectation for the emitter."""

    from pyscriptc.emitter import emit_ir_text

    backend = ROOT / "tests" / "fixtures" / "backend"
    manifest = json.loads((backend / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["fixtures"]
    for entry in manifest["fixtures"]:
        ir = json.loads((backend / entry["ir"]).read_text(encoding="utf-8"))
        # The CPython stamp is incidental: it marks the interpreter the IR was
        # lowered on and never reaches the emitted text.
        from pyscriptc import ir as ir_mod

        ir["cpython"] = ir_mod.cpython_version()
        text = emit_ir_text(ir, call=entry["call"])
        golden = (backend / entry["ll"]).read_text(encoding="utf-8")
        assert text == golden, entry["name"]


def test_the_runtime_table_stays_with_the_emitter() -> None:
    """The `ps_*` declarations and the checked-arithmetic helpers live together."""

    from pyscriptc.emit.llvm import _runtime

    assert "declare i32 @ps_try_call(ptr, ptr, ptr)" in _runtime._runtime_declarations({"exc"})
    assert "declare ptr @ps_alloc(i64)" in _runtime._runtime_declarations({"alloc"})
    helpers = _runtime._helpers({"iadd"})
    assert len(helpers) == 1
    assert "define internal i64 @ps.iadd(i64 %a, i64 %b)" in helpers[0]
    # The exception codes are filled in from the builtin table.
    assert "@OVF@" not in helpers[0]


def test_the_runtime_directory_is_next_to_the_package() -> None:
    """The C runtime stays at `pyscriptc/runtime/`, whatever the emitter layout."""

    from pyscriptc.emit.llvm import runtime_dir

    runtime = runtime_dir()
    assert runtime.name == "runtime"
    assert runtime.parent == SRC
    assert (runtime / "ps_rt.h").exists()
    assert list(runtime.glob("*.c"))
