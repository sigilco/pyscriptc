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


def test_the_seam_reads_an_absent_kind_tag() -> None:
    from pyscriptc.emit import _ir

    assert _ir._kind_or_none({"kind": "int"}) == "int"
    assert _ir._kind_or_none({}) is None
    assert _ir._kind_or_none(None) is None


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


def test_the_seam_knows_the_container_accessors() -> None:
    """The typed IR reads a stdlib emitter needs: element kinds and optionality."""

    from pyscriptc.emit import _ir

    assert _ir._elem_kind({"kind": "list", "elem": {"kind": "int"}}) == "int"
    assert _ir._elem_kind({"kind": "dict", "elem": {"kind": "str"}}) == "str"
    assert _ir._is_optional({"kind": "int", "optional": True}) is True
    assert _ir._is_optional({"kind": "int"}) is False
    assert _ir._is_optional({"kind": "int", "optional": False}) is False


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
