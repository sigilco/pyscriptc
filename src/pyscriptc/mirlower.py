"""HIR-to-MIR lowering for the R4 leaf slice.

The leaf slice covers pure scalar arithmetic: int/float/bool constants,
binary arithmetic and comparison on scalars, unary minus and `not`, names,
assignments, `return`, one-level `if` and `while` loops whose condition is
a scalar compare or bool and whose body is leaf statements plus `break`
and `continue` (no `while ... else`). Short-circuit `and`/`or`, the
ternary `a if c else b` and augmented assignment on scalar locals are
part of the slice: the IR desugars all three into temp assigns plus
one-level `if` statements (A6/A7), so the leaf handles them with the
existing block machinery and no new MIR op. It is the straight-line,
one-level-`if` and plain-`while` shape the byte-identity A/B runs
against; everything outside the slice stays on the direct path unchanged.

Three functions:

- `supported(fn)`: the slice test. A function is in the slice when every
  statement and expression is one the leaf handles; the module walker skips
  a function the slice does not cover, and the MIR lane falls back to the
  direct emitter for it. This keeps the strangler incremental: the slice
  grows, the fallback shrinks.

`while` loops admit `break` and `continue` in the body: the direct path
desugars both into flag assignments plus guards the slice already
lowers, so the lowerer only has to keep the block ids and label numbers
of nested control flow aligned with that path.
- `lower_function(fn)`: one IR function to a `MirFunction` built through
  `MirBuilder`, so construction invariants hold by construction.
- `lower_module(module)`: every in-slice function, as a `MirModule`. A
  module whose functions are all out of slice returns a module with an
  empty function list, which the emitter refuses.

Name-to-value mapping: every local (including parameters, which the IR
mirrors into `locals`) gets one value slot in `slots`, allocated in
`locals` order before the body walk, so slot ids are input-order stable.
Expression values are the operand ints of `mir.py` (a value id, or a
negative constant-pool reference).

The MIR lane emits `w` for ints and bools, `d` for floats; comparisons
produce `w` 0/1 values, the bool the branch op reads.

Self-host spell: the payload annotations use the `Dict[str, Any]` container
form with the read site annotating the element type, the op ids from
`mir.py` are spelled here as int literals (a drift is a mirverify or A/B
byte-identity failure, not a silent wrong lowering), and the two helpers
the module used to import (`fits_i64`, `_ir_const_value`) are local,
spelled over the same values. Four fences remain, one root: the
`pyscriptc.mir` import (the MirBuilder/MirFunction/MirModule annotations
and the MirBuilder reads) is not in the closed import table yet; decision
2026-11-02 admits it in R4 and drops all four at once.

"""

from __future__ import annotations

from typing import Any, Dict

from pyscriptc.mir import MirBuilder, MirFunction, MirModule

# The type tags and op ids, spelled as int literals per the self-host
# spell: `pyscriptc.mir` is not in the closed import table yet (decision
# 2026-11-02 admits it in R4), so the values mirror `mir.py` here. A drift
# is a mirverify or A/B byte-identity failure, not a silent wrong lowering.
MIR_W = 0
MIR_D = 1
MIR_B = 2
# The str value tag (`mir.py`'s MIR_S): a `%ps.str` aggregate at run time.
# 3 is MIR_VOID, so the str tag is 4; 5 is the shared heap-pointer tag
# (`mir.py`'s MIR_P) list values ride.
MIR_S = 4
MIR_P = 5

MIR_ADDW = 4
MIR_SUBW = 5
MIR_MULW = 6
MIR_ADDD = 7
MIR_SUBD = 8
MIR_MULD = 9
MIR_NEGW = 10

MIR_CLTW = 12
MIR_CLEW = 13
MIR_CGTW = 14
MIR_CGEW = 15
MIR_CEQW = 16
MIR_CNEW = 17
MIR_CLTD = 18
MIR_CLED = 19
MIR_CGTD = 20
MIR_CGED = 21
MIR_CEQD = 22
MIR_CNED = 23

MIR_COPY = 25
MIR_NOTB = 26
MIR_RAISE = 27
MIR_CALL = 28
MIR_EXTWD = 24
MIR_ANDW = 29
MIR_NEWSEQ = 30
MIR_SEQGET = 31
MIR_SEQSET = 32
MIR_SEQPUSH = 33
MIR_SEQLEN = 34
MIR_ORW = 35
MIR_XORW = 36
MIR_NEWOBJ = 37
MIR_SETATTR = 38
MIR_GETATTR = 39

# The void result tag (`mir.py`'s MIR_VOID): a function returning None has
# no return value. Spelled as the int literal per the self-host spell, so a
# drift is a mirverify or A/B byte-identity failure.
MIR_VOID = 3

I64_MIN = -9223372036854775807 - 1
I64_MAX = 9223372036854775807

# The one exception the leaf slice raises: `range`'s zero-step check. The
# code mirrors `builtins.EXCEPTIONS["ValueError"]`, spelled as an int
# literal per the self-host spell (`pyscriptc.builtins` is not in the
# closed import table); a drift is a byte-identity failure, not a silent
# wrong code.
EXC_VALUE_ERROR = 51


class MirLowerError(Exception):
    """A payload the leaf slice refuses. The class is local (the errors
    module is not importable under the closed import table) and the name is
    distinct from `errors.IRError`, which the census lowerer counts as a
    duplicate class definition; callers catch the base exception, so the
    shape is what matters."""

    pass


def _fits_i64(value: int) -> bool:
    return I64_MIN <= value <= I64_MAX


# The binary op to the MIR op, per operand kind. `//` and `%` on ints and
# `/`/`%`/`//`/`**` on floats go through runtime helpers; they are part of
# the slice through `HELPER_OPS` below.
_W_BINOPS = {
    "+": MIR_ADDW,
    "-": MIR_SUBW,
    "*": MIR_MULW,
    "&": MIR_ANDW,
    "|": MIR_ORW,
    "^": MIR_XORW,
}
_D_BINOPS = {
    "+": MIR_ADDD,
    "-": MIR_SUBD,
    "*": MIR_MULD,
}
_W_CMPOPS = {
    "<": MIR_CLTW,
    "<=": MIR_CLEW,
    ">": MIR_CGTW,
    ">=": MIR_CGEW,
    "==": MIR_CEQW,
    "!=": MIR_CNEW,
}
_D_CMPOPS = {
    "<": MIR_CLTD,
    "<=": MIR_CLED,
    ">": MIR_CGTD,
    ">=": MIR_CGED,
    "==": MIR_CEQD,
    "!=": MIR_CNED,
}

# The ps-helper-backed binary ops the slice lowers, per operand kind.
_W_HELPER_OPS = {
    "//": "floordiv",
    "%": "imod",
    "<<": "ishl",
    ">>": "ishr",
}
_D_HELPER_OPS = {
    "/": "fdiv",
    "%": "fmod",
    "//": "ffloordiv",
    "**": "fpow",
}

_EXPR_KINDS = frozenset({"const", "name", "binary", "compare", "unary", "call", "cast", "list", "subscript", "attr"})
_STMT_KINDS = frozenset({"assign", "expr", "return", "pass", "if", "while", "raise", "setitem", "setattr"})


def supported_type(typ: Dict[str, Any]) -> bool:
    """The slice test for one HIR type record.

    Scalars ride their existing tags (`str` rides `MIR_S`); `list[int]`
    and `list[float]` ride the shared `MIR_P` pointer tag. The element
    type stays in HIR: MIR sees only the pointer shape, so nested or
    non-scalar element lists stay out.

    A `class` value rides `MIR_P` too, but only the `tuple_N_*` classes
    the IR synthesizes for unpacking: they carry no methods, their
    fields are written by `setattr` after a bare constructor call and
    read back by `attr`. User classes (and the `Tree`/`opt_Tree` shapes
    the corpus's recursive walkers need) stay outside the slice.
    """
    kind: str = typ["kind"]
    if kind in ("int", "float", "bool", "str"):
        return True
    if kind == "class":
        if "name" not in typ:
            return False
        cname: str = typ["name"]
        return cname.startswith("tuple_")
    if kind != "list":
        return False
    return _supported_seq_elem(typ["elem"])


def _supported_seq_elem(elem: Dict[str, Any]) -> bool:
    """The element types a sequence value may carry: int and float at the
    leaves, and lists of those recursively (the ABI is one pointer tag
    either way; the element semantics stay in HIR)."""
    ekind: str = elem["kind"]
    if ekind in ("int", "float"):
        return True
    if ekind == "list":
        return supported_type(elem)
    return False


def _is_supported_list(typ: Dict[str, Any]) -> bool:
    tkind: str = typ["kind"]
    return tkind == "list" and supported_type(typ)


def _is_supported_class(typ: Dict[str, Any]) -> bool:
    tkind: str = typ["kind"]
    return tkind == "class" and supported_type(typ)


def _supported_field_type(typ: Dict[str, Any]) -> bool:
    """The field types an `attr`/`setattr` pair may carry.

    Scalars and nested tuple classes load and store as plain LLVM
    values; `str`/`bytes` fields need the `%ps.buf` narrow/widen the
    slice does not emit here, `none` fields are placeholder bytes, and
    the pointer containers stay out until their own slice lands.
    """
    fkind: str = typ["kind"]
    if fkind in ("int", "float", "bool"):
        return True
    if fkind == "class":
        return supported_type(typ)
    return False


def supported_expr(expr: Dict[str, Any]) -> bool:
    """The slice test for one expression node, recursive over its children."""
    kind: str = expr["kind"]
    if kind not in _EXPR_KINDS:
        return False
    if kind == "call":
        return _supported_call(expr)
    if kind == "const":
        typ: Dict[str, Any] = expr["type"]
        tkind: str = typ["kind"]
        if tkind not in ("int", "float", "bool", "str"):
            return False
        if tkind == "int":
            raw: str = expr["value"]
            if not _fits_i64(int(raw)):
                return False
        return True
    if kind == "name":
        return supported_type(expr["type"])
    if kind == "list":
        if not _is_supported_list(expr["type"]):
            return False
        elems: list[Dict[str, Any]] = expr["elems"]
        for elem in elems:
            if not supported_expr(elem):
                return False
        return True
    if kind == "subscript":
        value: Dict[str, Any] = expr["value"]
        index: Dict[str, Any] = expr["index"]
        if not _is_supported_list(value["type"]):
            return False
        itype: Dict[str, Any] = index["type"]
        ikind: str = itype["kind"]
        if ikind != "int":
            return False
        return supported_expr(value) and supported_expr(index)
    if kind == "attr":
        # A field read off a tuple-class value: the object must be an
        # in-slice class and the field's type one the attr ops carry.
        value = expr["value"]
        vtyp: Dict[str, Any] = value["type"]
        if not _is_supported_class(vtyp):
            return False
        if not _supported_field_type(expr["type"]):
            return False
        return supported_expr(value)
    if kind == "unary":
        typ = expr["type"]
        tkind = typ["kind"]
        if tkind not in ("int", "float", "bool"):
            return False
        op: str = expr["op"]
        if op == "not":
            return tkind == "bool" and supported_expr(expr["operand"])
        if op == "~":
            return tkind == "int" and supported_expr(expr["operand"])
        if op != "-":
            return False
        return tkind in ("int", "float") and supported_expr(expr["operand"])
    if kind == "binary":
        return _supported_binary(expr)
    if kind == "compare":
        return _supported_compare(expr)
    if kind == "cast":
        return _supported_cast(expr)
    return False


def _supported_cast(expr: Dict[str, Any]) -> bool:
    """The slice test for one cast expression.

    The IR admits one cast today, `int_to_float` (`ir.py` validates the
    op), which the emitter renders as `sitofp i64 ... to double`: the
    operand must be an in-slice int and the result a float. Anything
    else stays outside.
    """
    op: str = expr["op"]
    if op != "int_to_float":
        return False
    typ: Dict[str, Any] = expr["type"]
    tkind: str = typ["kind"]
    if tkind != "float":
        return False
    value: Dict[str, Any] = expr["value"]
    vtyp: Dict[str, Any] = value["type"]
    vkind: str = vtyp["kind"]
    if vkind != "int":
        return False
    return supported_expr(value)


def _supported_print(expr: Dict[str, Any]) -> bool:
    """The slice test for one `print` call.

    The rest is a value the walk has to cover; `print` is the void
    statement-call form the slice lowers itself, so the callee check is
    the builtin's name, the kwargs are out (the sep/end walk is not in
    the slice), and every argument is a scalar in the slice.
    """
    etype: Dict[str, Any] = expr["type"]
    ekind: str = etype["kind"]
    if ekind != "none":
        return False
    if "sep" in expr or "end" in expr:
        return False
    if "file" in expr:
        stream: str = expr["file"]
        if stream != "stdout":
            # `file=sys.stderr` rides the expr as the stream tag; the
            # slice emits stdout only, so anything but the default
            # stream stays out.
            return False
    args: list[Dict[str, Any]] = expr["args"]
    for arg in args:
        atyp: Dict[str, Any] = arg["type"]
        akind: str = atyp["kind"]
        if akind not in ("int", "float", "bool"):
            return False
        if not supported_expr(arg):
            return False
    return True


def _supported_call(expr: Dict[str, Any]) -> bool:
    """The slice test for one call expression.

    A `print` call is the void builtin form (see `_supported_print`).
    Everything else names a plain same-module user function: the callee
    record's kind is `function` (the IR's spelling for a same-module name
    resolved at lowering), the name is defined by a function of this
    module (checked against `supported_fn`'s own param/return rules by
    the caller through `_callee_in_slice`), and every argument is in the
    slice. Everything else (builtins, module members, methods, class
    constructors, value callees) stays outside.
    """
    callee: Dict[str, Any] = expr["callee"]
    ckind: str = callee["kind"]
    args: list[Dict[str, Any]] = expr["args"]
    if ckind == "function":
        for arg in args:
            atyp: Dict[str, Any] = arg["type"]
            akind: str = atyp["kind"]
            if akind not in ("int", "float", "bool") and not _is_supported_list(atyp) and not _is_supported_class(atyp):
                return False
            if not supported_expr(arg):
                return False
        return True
    if ckind == "class":
        # A `tuple_N_*` constructor: the IR emits it with no arguments
        # (fields are written by `setattr` after the bare alloc) and a
        # class-typed result.
        cname: str = callee["name"]
        if not cname.startswith("tuple_") or args:
            return False
        rtype: Dict[str, Any] = expr["type"]
        return _is_supported_class(rtype)
    if ckind == "builtin":
        cname: str = callee["name"]
        if cname == "print":
            return _supported_print(expr)
        if cname == "len" and len(args) == 1:
            arg: Dict[str, Any] = args[0]
            return _is_supported_list(arg["type"]) and supported_expr(arg)
        return False
    if ckind == "module":
        cmodule: str = callee["module"]
        mname: str = callee["name"]
        if cmodule == "builtins" and mname == "list.append":
            symbol: str = expr["symbol"]
            if symbol == "ps_list_push" and len(args) == 2:
                seq_arg: Dict[str, Any] = args[0]
                elem_arg: Dict[str, Any] = args[1]
                return (
                    _is_supported_list(seq_arg["type"])
                    and supported_type(elem_arg["type"])
                    and supported_expr(seq_arg)
                    and supported_expr(elem_arg)
                )
        if _rt_call_shape(cmodule, mname, expr):
            return _rt_args_order_ok(args)
    return False


def _type_kind(typ: Dict[str, Any]) -> str:
    """The `kind` string of an IR type record; the record binds to a
    concrete container first so the element read stays typed."""
    kind: str = typ["kind"]
    return kind


def _rt_call_shape(cmodule: str, mname: str, expr: Dict[str, Any]) -> bool:
    """The slice test for a runtime-ABI module call.

    These are the `module` calls the direct emitter renders through
    `_emit_abi`/`_emit_math` (a `ps_*` symbol, no same-module callee):
    `math.sqrt`, and the `builtins` `sum`/`repeat`/`int` forms whose IR
    carries a `symbol`. The argument and result types must stay inside
    the scalar/list slice; everything else stays outside.
    """
    args: list[Dict[str, Any]] = expr["args"]
    if cmodule == "math" and mname == "sqrt":
        # `math.sqrt` is not an `_abi_call`, so the IR carries no symbol;
        # the emitter's `_emit_math` spells `ps_math_sqrt` for it.
        if "symbol" in expr:
            return False
        if len(args) != 1 or _type_kind(expr["type"]) != "float":
            return False
        arg: Dict[str, Any] = args[0]
        return _type_kind(arg["type"]) == "float" and supported_expr(arg)
    if "symbol" not in expr:
        return False
    symbol: str = expr["symbol"]
    if cmodule == "builtins" and mname == "sum":
        if symbol != "ps_sum_i64" and symbol != "ps_sum_f64":
            return False
        if len(args) != 1 or _type_kind(expr["type"]) not in ("int", "float"):
            return False
        sum_arg: Dict[str, Any] = args[0]
        return _is_supported_list(sum_arg["type"]) and supported_expr(sum_arg)
    if cmodule == "builtins" and mname == "repeat":
        if symbol != "ps_seq_repeat" or len(args) != 2:
            return False
        if not _is_supported_list(expr["type"]):
            return False
        seq_arg: Dict[str, Any] = args[0]
        count_arg: Dict[str, Any] = args[1]
        return (
            _is_supported_list(seq_arg["type"])
            and _type_kind(count_arg["type"]) == "int"
            and supported_expr(seq_arg)
            and supported_expr(count_arg)
        )
    if cmodule == "builtins" and mname == "int" and _type_kind(expr["type"]) == "int":
        if symbol == "ps_int_from_bytes" and len(args) == 1:
            text_arg: Dict[str, Any] = args[0]
            return _type_kind(text_arg["type"]) == "str" and supported_expr(text_arg)
        if symbol == "ps_int_from_base" and len(args) == 2:
            first: Dict[str, Any] = args[0]
            second: Dict[str, Any] = args[1]
            return (
                _type_kind(first["type"]) == "str"
                and _type_kind(second["type"]) == "int"
                and supported_expr(first)
                and supported_expr(second)
            )
    return False


def _rt_args_order_ok(args: list[Dict[str, Any]]) -> bool:
    """A top-level str const argument materializes lazily at the call
    instruction; the direct path materializes it at the argument's own
    position. The two orders agree only while every argument after the
    first str const is itself a const (which emits no instructions)."""
    items: list[Dict[str, Any]] = args
    seen: bool = False
    for arg in items:
        akind: str = arg["kind"]
        atype: Dict[str, Any] = arg["type"]
        if seen and akind != "const":
            return False
        if akind == "const" and _type_kind(atype) == "str":
            seen = True
    return True


def _rt_call_symbol(cmodule: str, mname: str, expr: Dict[str, Any]) -> str:
    """The `ps_*` symbol an admitted runtime module call lowers to.

    The caller ran `supported_expr` first, so this only picks the
    spelling. `math.sqrt` carries no `symbol` key in the IR (it is not
    an `_abi_call`), so its symbol is written here; a drift is an A/B
    byte-identity failure, not a silent wrong call.
    """
    if cmodule == "math" and mname == "sqrt":
        return "ps_math_sqrt"
    if "symbol" not in expr:
        return ""
    symbol: str = expr["symbol"]
    if cmodule == "builtins" and mname == "sum":
        return symbol
    if cmodule == "builtins" and mname == "repeat":
        return symbol
    if cmodule == "builtins" and mname == "int":
        return symbol
    return ""


def _supported_binary(expr: Dict[str, Any]) -> bool:
    typ: Dict[str, Any] = expr["type"]
    tkind: str = typ["kind"]
    op: str = expr["op"]
    if tkind == "int":
        if op not in _W_BINOPS and op not in _W_HELPER_OPS:
            return False
    elif tkind == "float":
        if op not in _D_BINOPS and op not in _D_HELPER_OPS:
            return False
    elif tkind == "bool" and op in ("&", "|", "^"):
        pass
    else:
        return False
    left: Dict[str, Any] = expr["left"]
    ltyp: Dict[str, Any] = left["type"]
    lkind: str = ltyp["kind"]
    if lkind not in ("int", "float", "bool"):
        return False
    right: Dict[str, Any] = expr["right"]
    rtyp: Dict[str, Any] = right["type"]
    rkind: str = rtyp["kind"]
    if lkind != rkind:
        return False
    return supported_expr(left) and supported_expr(right)


def _supported_compare(expr: Dict[str, Any]) -> bool:
    op: str = expr["op"]
    left: Dict[str, Any] = expr["left"]
    ltyp: Dict[str, Any] = left["type"]
    lkind: str = ltyp["kind"]
    right: Dict[str, Any] = expr["right"]
    rtyp: Dict[str, Any] = right["type"]
    rkind: str = rtyp["kind"]
    if lkind != rkind:
        return False
    if lkind == "str":
        # The str slice compares through the `ps_str_cmp` helper copy;
        # ordering operators stay out until a helper row exists for them.
        if op != "==" and op != "!=":
            return False
        return supported_expr(left) and supported_expr(right)
    if op not in _W_CMPOPS:
        return False
    if lkind not in ("int", "float", "bool"):
        return False
    return supported_expr(left) and supported_expr(right)


def supported_stmt(stmt: Dict[str, Any]) -> bool:
    """The slice test for one statement, recursive into nested bodies."""
    kind: str = stmt["kind"]
    if kind not in _STMT_KINDS:
        return False
    if kind == "assign":
        # A None-typed value lowers to nothing (the None singleton has no
        # runtime value): a const or a name read assigns nothing. A
        # None-typed call is the void-call form: it lowers when its callee
        # is in the slice (the module-level callee test runs later).
        value: Dict[str, Any] = stmt["value"]
        vtyp: Dict[str, Any] = value["type"]
        vkind: str = vtyp["kind"]
        if vkind == "none":
            vnode: str = value["kind"]
            if vnode == "const" or vnode == "name":
                return True
            if vnode == "call":
                return _supported_call(value)
            return False
        return supported_expr(value)
    if kind == "expr":
        # The direct path skips a None const expression statement, so the
        # slice admits it and lowers to nothing; a None-typed call is the
        # void-call form; anything else is a value expression the walk has
        # to cover. A bare str const is the exception inside "anything
        # else": the direct path materializes it (a `string_value` global
        # plus the insertvalue sequence) even though the value is
        # discarded, and the MIR lane emits no instruction for a const,
        # so admitting it would drop those lines and shift every later
        # `@ps.str.N` index.
        expr: Dict[str, Any] = stmt["expr"]
        etyp: Dict[str, Any] = expr["type"]
        ekind: str = etyp["kind"]
        if ekind == "none":
            enode: str = expr["kind"]
            if enode == "const":
                return True
            if enode == "call":
                return _supported_call(expr)
            return False
        if ekind == "str":
            str_node: str = expr["kind"]
            if str_node == "const":
                return False
        return supported_expr(expr)
    if kind == "return":
        # A `None`-typed return lowers to a void `MIR_RET`; the body walk
        # skips the None const the IR carries (`return None` and bare
        # `return` lower to the same node). A None-typed call as the
        # returned value is a void call followed by the void ret.
        rvalue: Dict[str, Any] = stmt["value"]
        rtyp: Dict[str, Any] = rvalue["type"]
        rkind: str = rtyp["kind"]
        if rkind == "none":
            rnode: str = rvalue["kind"]
            if rnode == "const" or rnode == "name":
                return True
            if rnode == "call":
                return _supported_call(rvalue)
            return False
        return supported_expr(rvalue)
    if kind == "setitem":
        container: Dict[str, Any] = stmt["container"]
        index_node: Dict[str, Any] = stmt["index"]
        item_value: Dict[str, Any] = stmt["value"]
        if not _is_supported_list(container["type"]):
            return False
        itype: Dict[str, Any] = index_node["type"]
        ikind: str = itype["kind"]
        if ikind != "int" or not supported_type(item_value["type"]):
            return False
        return supported_expr(container) and supported_expr(index_node) and supported_expr(item_value)
    if kind == "setattr":
        # A field write on a tuple-class value: the object must be an
        # in-slice class and the stored value's type one the attr ops
        # carry (the field record's type matches it; the IR verified).
        object_node: Dict[str, Any] = stmt["object"]
        otyp: Dict[str, Any] = object_node["type"]
        if not _is_supported_class(otyp):
            return False
        value_node: Dict[str, Any] = stmt["value"]
        if not _supported_field_type(value_node["type"]):
            return False
        return supported_expr(object_node) and supported_expr(value_node)
    if kind == "if":
        if not supported_expr(stmt["cond"]):
            return False
        then_body: list[Dict[str, Any]] = stmt["then"]
        for s in then_body:
            if not supported_stmt(s):
                return False
        else_body: list[Dict[str, Any]] = stmt["else"]
        for s in else_body:
            if not supported_stmt(s):
                return False
        return True
    if kind == "while":
        # The condition is a scalar compare or bool: a `MIR_BR` reads an
        # i1, which a compare or a bool name/const already is. The body
        # is leaf statements; a `while ... else` stays out of the slice
        # (the desugared shape carries no `orelse` key on the while, so
        # an else on the source loop never reaches this walk).
        if not supported_expr(stmt["cond"]):
            return False
        body: list[Dict[str, Any]] = stmt["body"]
        for s in body:
            if not supported_stmt(s):
                return False
        return True
    if kind == "raise":
        # The one raise the slice covers: `range`'s zero-step check, a
        # `ValueError` with no message. It raises the exception code
        # (`ps_raise_code`) and ends its block, the way the direct path's
        # `emit_raise` does. The absence reads spell "not present" (`_nd_key`
        # drops a None message/cause and a non-raising reraise), spelled as
        # membership tests to keep `Any` out of the locals.
        exc: str = stmt["exc"]
        if exc != "ValueError":
            return False
        if "message" in stmt:
            message: Dict[str, Any] | None = stmt["message"]
            if message is not None:
                return False
        if "cause" in stmt:
            cause: Dict[str, Any] | None = stmt["cause"]
            if cause is not None:
                return False
        if "reraise" in stmt:
            reraise: bool = stmt["reraise"]
            if reraise:
                return False
        return True
    return True


def supported_fn(fn: Dict[str, Any]) -> bool:
    """The slice test for one function: scalar params, scalar or None return, in-slice body."""
    if "env" in fn:
        env: str = fn["env"]
        if env is not None:
            return False
    rt: Dict[str, Any] = fn["returnType"]
    rtk: str = rt["kind"]
    if rtk != "none" and not supported_type(rt):
        return False
    params: list[Dict[str, Any]] = fn["params"]
    for param in params:
        if not supported_type(param["type"]):
            return False
    locals_list: list[Dict[str, Any]] = fn["locals"]
    for local in locals_list:
        ltyp: Dict[str, Any] = local["type"]
        lk: str = ltyp["kind"]
        if lk != "none" and not supported_type(ltyp):
            return False
    body: list[Dict[str, Any]] = fn["body"]
    for stmt in body:
        if not supported_stmt(stmt):
            return False
    return True


def supported_module_fn(fn: Dict[str, Any], callees: Dict[str, Dict[str, Any]]) -> bool:
    """The module-level slice test: `supported_fn` plus the callee checks.

    A call the body walk admits names a same-module function; here the
    callee must itself be a plain module-level user function in the
    slice, with an arity matching the call. The checks are pairwise
    (call site against the callee's record), so a call to a function
    that would lower clean but is out of the slice by its own shape
    (a float param, an env) keeps the caller out too.
    """
    if not supported_fn(fn):
        return False
    body: list[Dict[str, Any]] = fn["body"]
    return _calls_in_slice(body, callees)


def _calls_in_slice(stmts: list[Dict[str, Any]], callees: Dict[str, Dict[str, Any]]) -> bool:
    """Every call nested in `stmts` names an in-slice callee with a
    matching arity. `callees` is the admitted set, which may contain the
    caller itself: the module-level fixpoint admits recursion cycles
    (f calls f, or f and g calling each other), and a self-call reads the
    caller's own record for the arity check."""
    for stmt in stmts:
        if not _calls_in_slice_stmt(stmt, callees):
            return False
    return True


def _calls_in_slice_stmt(stmt: Dict[str, Any], callees: Dict[str, Dict[str, Any]]) -> bool:
    kind: str = stmt["kind"]
    if kind == "assign":
        value: Dict[str, Any] = stmt["value"]
        return _calls_in_slice_expr(value, callees)
    if kind == "expr":
        expr: Dict[str, Any] = stmt["expr"]
        return _calls_in_slice_expr(expr, callees)
    if kind == "return":
        rvalue: Dict[str, Any] = stmt["value"]
        return _calls_in_slice_expr(rvalue, callees)
    if kind == "setitem":
        set_container: Dict[str, Any] = stmt["container"]
        set_index: Dict[str, Any] = stmt["index"]
        set_value: Dict[str, Any] = stmt["value"]
        if not _calls_in_slice_expr(set_container, callees):
            return False
        if not _calls_in_slice_expr(set_index, callees):
            return False
        return _calls_in_slice_expr(set_value, callees)
    if kind == "setattr":
        set_object: Dict[str, Any] = stmt["object"]
        set_value = stmt["value"]
        if not _calls_in_slice_expr(set_object, callees):
            return False
        return _calls_in_slice_expr(set_value, callees)
    if kind == "if":
        if not _calls_in_slice_expr(stmt["cond"], callees):
            return False
        then_body: list[Dict[str, Any]] = stmt["then"]
        for s in then_body:
            if not _calls_in_slice_stmt(s, callees):
                return False
        else_body: list[Dict[str, Any]] = stmt["else"]
        for s in else_body:
            if not _calls_in_slice_stmt(s, callees):
                return False
        return True
    if kind == "while":
        if not _calls_in_slice_expr(stmt["cond"], callees):
            return False
        body: list[Dict[str, Any]] = stmt["body"]
        for s in body:
            if not _calls_in_slice_stmt(s, callees):
                return False
        return True
    return True


def _calls_in_slice_expr(expr: Dict[str, Any], callees: Dict[str, Dict[str, Any]]) -> bool:
    kind: str = expr["kind"]
    if kind == "call":
        callee: Dict[str, Any] = expr["callee"]
        args: list[Dict[str, Any]] = expr["args"]
        for arg in args:
            if not _calls_in_slice_expr(arg, callees):
                return False
        ckind: str = callee["kind"]
        if ckind != "function":
            # `len(list)` and `list.append` are runtime-helper calls the
            # emitter renders directly; they have no same-module callee to
            # admit.
            return True
        name: str = callee["name"]
        ckind: str = callee["kind"]
        args: list[Dict[str, Any]] = expr["args"]
        if ckind == "builtin" and name == "print":
            # The void builtin form: no module-level callee to check, only
            # the arguments' own shape.
            for arg in args:
                if not _calls_in_slice_expr(arg, callees):
                    return False
            return True
        target: Dict[str, Any] | None = callees.get(name)
        if target is None:
            return False
        params: list[Dict[str, Any]] = target["params"]
        if len(args) != len(params):
            return False
        return True
    if kind == "binary":
        if not _calls_in_slice_expr(expr["left"], callees):
            return False
        return _calls_in_slice_expr(expr["right"], callees)
    if kind == "compare":
        if not _calls_in_slice_expr(expr["left"], callees):
            return False
        return _calls_in_slice_expr(expr["right"], callees)
    if kind == "unary":
        return _calls_in_slice_expr(expr["operand"], callees)
    if kind == "cast":
        return _calls_in_slice_expr(expr["value"], callees)
    if kind == "list":
        elems: list[Dict[str, Any]] = expr["elems"]
        for elem in elems:
            if not _calls_in_slice_expr(elem, callees):
                return False
        return True
    if kind == "subscript":
        if not _calls_in_slice_expr(expr["value"], callees):
            return False
        return _calls_in_slice_expr(expr["index"], callees)
    if kind == "attr":
        return _calls_in_slice_expr(expr["value"], callees)
    return True


def _param_tag(kind: str) -> int:
    if kind == "float":
        return MIR_D
    if kind == "bool":
        return MIR_B
    if kind == "str":
        return MIR_S
    if kind == "list" or kind == "class":
        return MIR_P
    return MIR_W


def _fn_result_tag(fn: Dict[str, Any]) -> int:
    rt: Dict[str, Any] = fn["returnType"]
    kind: str = rt["kind"]
    if kind == "none":
        return MIR_VOID
    return _param_tag(kind)


class _FnLowerer:
    """One function's body walk. Slots and the builder live here.

    The field table is spelled as class-level bare annotations: the census
    lowerer collects fields from the `__init__` body, but its AnnAssign
    statement shape is not lowered, so a `self.x: T = ...` assignment fences
    every later `self.x` read. The bare-name form registers the field
    without a statement to lower.
    """

    fn: Dict[str, Any]
    builder: MirBuilder
    slots: Dict[str, int]
    local_types: Dict[str, str]
    slot_vids: Dict[str, int]
    # The admitted callee records, keyed by the callee name the IR's
    # `function` callee carries. A call lowers only when its name is here
    # (`supported_module_fn` guaranteed it for in-slice bodies).
    callees: Dict[str, Dict[str, Any]]
    # The direct emitter's label counter: `fresh()` hands out one number
    # per label, in statement pre-order. The lowerer reserves the same
    # numbers so the emitted labels land on the direct path's.
    next_label: int
    # The instruction index of each block's terminator, keyed by block
    # id. A forward jump's operand is rewritten once the block it names
    # opens, and the terminator is the block's last instruction.
    block_terminator: Dict[int, int]

    def __init__(self, fn: Dict[str, Any], callees: Dict[str, Dict[str, Any]]) -> None:
        self.fn = fn
        self.builder = MirBuilder(fn["name"])
        self.callees = callees
        self.slots = {}
        self.local_types = {}
        self.slot_vids = {}
        self.next_label = 0
        self.block_terminator = {}

    def patch_jmp(self, bid: int, target: int) -> None:
        """Point block `bid`'s forward `MIR_JMP` at `target`.

        The direct emitter allocates its labels lazily (`fresh` at the
        line that emits the jump), so a nested `if` shifts the block ids
        a precomputed forward jump would name. The lowerer emits the jump
        with a placeholder and rewrites the operand, and the block's
        successor edge, once the target's id is final.
        """
        fn = self.builder.fn
        i: int = self.block_terminator[bid]
        fn.arg1[i] = target
        self.builder.patch_succ1(bid, target)

    def patch_br_false(self, bid: int, target: int) -> None:
        """Point block `bid`'s `MIR_BR` false edge at `target`.

        The header of an `if` or `while` names its join or exit block
        before the arm lowers; a nested `if` inside the arm shifts the
        construction-order id, so the edge is rewritten once the target's
        id is final.
        """
        fn = self.builder.fn
        i: int = self.block_terminator[bid]
        fn.arg2[i] = target
        self.builder.patch_succ2(bid, target)

    def _record_terminator(self) -> None:
        """Note the current block's terminator index.

        The builder's `emit_jmp`/`emit_br` do not return the instruction
        index, so the index is the last one appended.
        """
        bid = self.builder.current_bid()
        if bid < 0:
            raise MirLowerError("no open block to terminate")
        self.block_terminator[bid] = len(self.builder.fn.ops) - 1

    def reserve_label(self) -> int:
        """Reserve the next direct-path label number.

        The direct emitter's `fresh()` increments its counter before it
        returns the label, so the first label is 1. The entry block has
        no label.
        """
        self.next_label = self.next_label + 1
        return self.next_label

    def run(self) -> MirFunction:
        builder: MirBuilder = self.builder
        builder.set_result(_fn_result_tag(self.fn))
        # Parameters first, in IR order: their value ids seed the space the
        # body reads. The IR mirrors each parameter into `locals`, so the
        # locals walk skips the names the params already took.
        params: list[Dict[str, Any]] = self.fn["params"]
        for param in params:
            ptyp: Dict[str, Any] = param["type"]
            pname: str = param["name"]
            pk: str = ptyp["kind"]
            self.slots[pname] = builder.add_param(_param_tag(pk))
        # Every name (parameters included) gets one slot value id, in
        # locals order: the id the emitter renders as an alloca, stores
        # write, and pre-store reads load from. Parameters keep their
        # parameter ids for the prologue store; the slot id is the memory.
        fn_locals: list[Dict[str, Any]] = self.fn["locals"]
        extra_locals: list[Dict[str, Any]] = []
        for loc in fn_locals:
            # A None-typed local is the singleton, no runtime value, so it
            # takes no slot: the direct path registers it with no alloca,
            # and the emitter's slot walk must not see one here.
            loc_type: Dict[str, Any] = loc["type"]
            loc_kind: str = loc_type["kind"]
            if loc_kind == "none":
                continue
            extra_locals.append(loc)
        for local in extra_locals:
            vid: int = builder.next_vid
            builder.next_vid = vid + 1
            lname: str = local["name"]
            self.slot_vids[lname] = vid
            ltyp: Dict[str, Any] = local["type"]
            self.local_types[lname] = ltyp["kind"]
        builder.start_block()
        # The prologue: every parameter stores into its slot value, the
        # direct path's `store i64 %a, ptr %tN` per parameter. Later reads
        # of the parameter's name read the slot, not the raw parameter.
        for param in params:
            pname: str = param["name"]
            self.store(pname, self.slots[pname])
        body: list[Dict[str, Any]] = self.fn["body"]
        self.emit_body(body)
        # A body that falls off the end returns None: the checker refuses a
        # non-None function that can (PS1004), so reaching here with an open
        # block means the function returns None and the direct path renders
        # `ret void` at the same point, at the end of the entry flow.
        if not builder.terminated:
            builder.emit_ret_void()
        return builder.finish()

    def kind_of(self, name: str) -> str:
        params: list[Dict[str, Any]] = self.fn["params"]
        for param in params:
            pname: str = param["name"]
            if pname == name:
                ptyp: Dict[str, Any] = param["type"]
                pk: str = ptyp["kind"]
                return pk
        return self.local_types[name]

    def emit_body(self, body: list[Dict[str, Any]]) -> None:
        # A statement after the block died (both arms of a nested `if`
        # returned) is unreachable: the direct path drops it in
        # `emit_body`, so the lane drops it here too.
        builder: MirBuilder = self.builder
        for stmt in body:
            if builder.terminated:
                return
            self.emit_stmt(stmt)

    def emit_stmt(self, stmt: Dict[str, Any]) -> None:
        kind: str = stmt["kind"]
        if kind == "pass":
            return
        if kind == "assign":
            target: str = stmt["target"]
            value_expr: Dict[str, Any] = stmt["value"]
            if self.is_none(value_expr):
                # A None-typed value binds nothing: the None singleton has
                # no runtime value, so there is no slot write. A void call
                # still emits (its effects are the call itself), the way
                # the direct path emits the call then skips the store.
                vnode: str = value_expr["kind"]
                if vnode == "call":
                    self.emit_call_expr(value_expr)
                return
            value = self.emit_expr(value_expr)
            self.store(target, value)
            return
        if kind == "setitem":
            # The direct path evaluates container, index, then value, then
            # stashes the value cell and calls `ps_seq_set`; the MIR keeps
            # the same order and parks the stored value in `arg3`.
            container = self.emit_expr(stmt["container"])
            index = self.emit_expr(stmt["index"])
            item = self.emit_expr(stmt["value"])
            self.builder.emit(MIR_SEQSET, -1, container, index, item)
            return
        if kind == "setattr":
            self.emit_setattr(stmt)
            return
        if kind == "expr":
            expr: Dict[str, Any] = stmt["expr"]
            if self.is_none(expr):
                # The direct path skips a None const expression statement;
                # a void call emits.
                enode: str = expr["kind"]
                if enode == "call":
                    self.emit_call_expr(expr)
                return
            self.emit_expr(expr)
            return
        if kind == "return":
            # A None-typed return lowers to the void `MIR_RET`: no value
            # rides it, the way the direct path renders `ret void` at the
            # same point in the flow. A void call as the returned value
            # emits first, then the void ret.
            rvalue: Dict[str, Any] = stmt["value"]
            if self.is_none(rvalue):
                rnode: str = rvalue["kind"]
                if rnode == "call":
                    self.emit_call_expr(rvalue)
                self.builder.emit_ret_void()
                return
            value = self.emit_expr(rvalue)
            self.builder.emit_ret(value)
            return
        if kind == "if":
            self.emit_if(stmt)
            return
        if kind == "while":
            self.emit_while(stmt)
            return
        if kind == "raise":
            # `range`'s zero-step check: a `ValueError` with no message,
            # which the direct path raises as `ps_raise_code`.
            code = self.builder.const_w(EXC_VALUE_ERROR)
            self.builder.emit_raise(code)
            return
        raise MirLowerError(f"the MIR leaf slice cannot lower statement {kind}")

    def emit_if(self, stmt: Dict[str, Any]) -> None:
        """One-level if without block arguments on the join.

        The join block takes the merge of the two arms' effects through
        ordinary stores to the shared slots; no value flows through a
        block argument in this slice, so the branch binds nothing.

        The three labels are reserved before the arms lower, matching
        the direct emitter's `fresh()` order (then, else, merge). The
        block ids do not: a nested `if` inside an arm opens blocks of
        its own, so the arms' forward jumps and the header's false edge
        are rewritten once the real ids are final.
        """
        builder = self.builder
        cond = self.emit_expr(stmt["cond"])
        then_lid = self.reserve_label()
        else_lid = self.reserve_label()
        merge_lid = self.reserve_label()
        then_id = len(builder.fn.blocks)
        builder.emit_br(cond, then_id, -1, [], [])
        self._record_terminator()
        builder.start_block(hint="then", label_id=then_lid)
        then_body: list[Dict[str, Any]] = stmt["then"]
        self.emit_body(then_body)
        then_dead = builder.terminated
        # The arm's jump to the join lives in the arm's last block: a
        # nested `if` inside the arm leaves its own join block open, and
        # the direct path continues the arm there.
        then_last: int = builder.current_bid()
        if not then_dead:
            builder.emit_jmp(-1)
            self._record_terminator()
        else_id = len(builder.fn.blocks)
        self.patch_br_false(then_id - 1, else_id)
        builder.start_block(hint="else", label_id=else_lid)
        else_body: list[Dict[str, Any]] = stmt["else"]
        self.emit_body(else_body)
        else_dead = builder.terminated
        else_last: int = builder.current_bid()
        if not else_dead:
            builder.emit_jmp(-1)
            self._record_terminator()
        if then_dead and else_dead:
            return
        merge_id = len(builder.fn.blocks)
        if not then_dead:
            self.patch_jmp(then_last, merge_id)
        if not else_dead:
            self.patch_jmp(else_last, merge_id)
        builder.start_block(hint="merge", label_id=merge_lid)
        orelse: list[Dict[str, Any]] = []
        if "orelse" in stmt:
            orelse = stmt["orelse"]
        self.emit_body(orelse)

    def emit_while(self, stmt: Dict[str, Any]) -> None:
        """A plain `while`: header block, body block, exit block.

        The label order mirrors `_stmts.emit_while`'s `fresh` sequence
        (`while.cond`, `while.body`, `while.end`), so the emitter's
        labels land on the direct path's. The body's effects merge
        through the shared slots the same way the `if` arms' do: no
        value flows through a block argument in this slice.

        The exit's block id is not final until the body lowers: a nested
        `if` inside the body opens blocks of its own, so the header's
        false edge is rewritten once the exit's id is final.
        """
        builder = self.builder
        cond_lid = self.reserve_label()
        body_lid = self.reserve_label()
        end_lid = self.reserve_label()
        cond_id = len(builder.fn.blocks)
        body_id = cond_id + 1
        builder.emit_jmp(cond_id)
        self._record_terminator()
        builder.start_block(hint="while.cond", label_id=cond_lid)
        cond = self.emit_expr(stmt["cond"])
        builder.emit_br(cond, body_id, -1, [], [])
        self._record_terminator()
        builder.start_block(hint="while.body", label_id=body_lid)
        body: list[Dict[str, Any]] = stmt["body"]
        self.emit_body(body)
        if not builder.terminated:
            builder.emit_jmp(cond_id)
        self.patch_br_false(cond_id, len(builder.fn.blocks))
        builder.start_block(hint="while.end", label_id=end_lid)

    def store(self, name: str, value: int) -> None:
        """A store to `name`: a `MIR_COPY` into the name's slot value.

        The slot value is a MIR value id (never the parameter's own id), so
        the emitter renders every definition of a name as one `store` and
        every read as one `load`, the same shape the direct walk produces.
        `arg2` of the copy carries the value's tag (`copy_result_type`
        reads it), so the verifier's type walk stays true.
        """
        vid = self.slot_vids.get(name)
        if vid is None:
            raise MirLowerError(f"the MIR leaf slice has no slot for {name}")
        tag = _param_tag(self.kind_of(name))
        self.builder.emit(MIR_COPY, vid, value, tag)

    def load_slot(self, name: str) -> int:
        """The operand for a read of `name`: an eager copy of the slot.

        Every read emits its `MIR_COPY` from the slot at the point the
        body walk reaches it, so the loads (which the emitter renders as
        one `load` per copy) land in the same source order the direct
        walk produces. A lazy design that hands back the slot vid and
        lets the emitter load at operand-render time evaluates a
        computed right operand before an unresolved left one, which
        reorders the loads against the direct path (issue #871).
        """
        vid = self.slot_vids.get(name)
        if vid is None:
            raise MirLowerError(f"the MIR leaf slice has no slot for {name}")
        tag = _param_tag(self.kind_of(name))
        return self.builder.emit_copy(vid, tag)

    def emit_expr(self, expr: Dict[str, Any]) -> int:
        kind: str = expr["kind"]
        if kind == "const":
            return self.emit_const(expr)
        if kind == "name":
            return self.load_slot(expr["id"])
        if kind == "binary":
            return self.emit_binary(expr)
        if kind == "compare":
            return self.emit_compare(expr)
        if kind == "unary":
            return self.emit_unary(expr)
        if kind == "call":
            return self.emit_call_expr(expr)
        if kind == "cast":
            return self.emit_cast(expr)
        if kind == "list":
            return self.emit_list(expr)
        if kind == "subscript":
            return self.emit_subscript(expr)
        if kind == "attr":
            return self.emit_attr(expr)
        raise MirLowerError(f"the MIR leaf slice cannot lower expression {kind}")

    def emit_call_expr(self, expr: Dict[str, Any]) -> int:
        """A call to an admitted same-module function: a `MIR_CALL`.

        The callee rides `arg1` as the callee entry value id (allocated
        through `MirBuilder.add_callee`, named in the function's callee
        table); arguments evaluate left to right, then the call; the
        result tag is the callee's return kind (`MIR_VOID` for a None
        return). A void call returns -1: the direct path binds nothing.
        """
        callee: Dict[str, Any] = expr["callee"]
        ckind: str = callee["kind"]
        args: list[Dict[str, Any]] = expr["args"]
        if ckind == "class":
            return self.emit_newobj(expr)
        if ckind == "builtin":
            cname: str = callee["name"]
            if cname == "len":
                seq = self.emit_expr(args[0])
                return self.builder.emit_val(MIR_SEQLEN, seq, -1, MIR_W)
        if ckind == "module":
            cmodule: str = callee["module"]
            mname: str = callee["name"]
            if cmodule == "builtins" and mname == "list.append":
                symbol: str = expr["symbol"]
                if symbol == "ps_list_push":
                    seq = self.emit_expr(args[0])
                    item = self.emit_expr(args[1])
                    self.builder.emit(MIR_SEQPUSH, -1, seq, item)
                    return -1
            rt_symbol: str = _rt_call_symbol(cmodule, mname, expr)
            if rt_symbol != "":
                # A runtime-ABI call: the `rt:` callee prefix marks the
                # name table entry so the emitter renders the `ps_*`
                # symbol directly instead of resolving a module function.
                rargs: list[int] = []
                for arg in args:
                    rargs.append(self.emit_expr(arg))
                rtype: Dict[str, Any] = expr["type"]
                rkind: str = rtype["kind"]
                rtag: int = MIR_VOID
                if rkind != "none":
                    rtag = _param_tag(rkind)
                rt_vid: int = self.builder.add_callee("rt:" + rt_symbol)
                return self.builder.emit_call(rt_vid, rargs, rtag)
        name: str = callee["name"]
        ckind: str = callee["kind"]
        if ckind == "builtin" and name == "print":
            return self.emit_print(expr)
        target: Dict[str, Any] | None = self.callees.get(name)
        if target is None:
            raise MirLowerError(f"the MIR leaf slice has no lowered callee {name}")
        callee_vid: int = self.builder.add_callee(name)
        values: list[int] = []
        for arg in args:
            values.append(self.emit_expr(arg))
        typ: Dict[str, Any] = expr["type"]
        tkind: str = typ["kind"]
        tag: int = _fn_result_tag(target)
        if tkind == "none":
            self.builder.emit_call(callee_vid, values, MIR_VOID)
            return -1
        return self.builder.emit_call(callee_vid, values, tag)

    def emit_list(self, expr: Dict[str, Any]) -> int:
        """A list literal: evaluate the elements, `MIR_NEWSEQ`, then one
        `MIR_SEQSET` per element, the direct path's order."""
        elems: list[Dict[str, Any]] = expr["elems"]
        values: list[int] = []
        for elem in elems:
            values.append(self.emit_expr(elem))
        list_type: Dict[str, Any] = expr["type"]
        elem_type: Dict[str, Any] = list_type["elem"]
        ekind: str = elem_type["kind"]
        # The stride mirrors the direct emitter's `_elem_size`: pointer
        # elements (nested lists) are 8 bytes like the scalars, str cells
        # are the 16-byte `%ps.buf` prefix, bool is one byte.
        elem_size: int = 8
        if ekind == "bool":
            elem_size = 1
        elif ekind == "str" or ekind == "bytes" or ekind == "any":
            elem_size = 16
        seq: int = self.builder.emit_val(
            MIR_NEWSEQ, self.builder.const_w(elem_size), self.builder.const_w(len(values)), MIR_P
        )
        for index in range(len(values)):
            self.builder.emit(MIR_SEQSET, -1, seq, self.builder.const_w(index), values[index])
        return seq

    def emit_newobj(self, expr: Dict[str, Any]) -> int:
        """A `tuple_N_*` constructor call: `MIR_NEWOBJ` with the class
        name interned as a `MIR_S` constant the emitter reads without
        materializing. The slice admits only the no-argument form (the
        IR writes fields through `setattr` after the bare alloc)."""
        callee: Dict[str, Any] = expr["callee"]
        name: str = callee["name"]
        return self.builder.emit_val(MIR_NEWOBJ, self.builder.const_s(name), -1, MIR_P)

    def emit_setattr(self, stmt: Dict[str, Any]) -> None:
        """A field write: object then value (the direct order), then
        `MIR_SETATTR` keyed by a `<class>.<field>` `MIR_S` constant."""
        obj = self.emit_expr(stmt["object"])
        value = self.emit_expr(stmt["value"])
        object_node: Dict[str, Any] = stmt["object"]
        otyp: Dict[str, Any] = object_node["type"]
        cls_name: str = otyp["name"]
        field: str = stmt["name"]
        key: int = self.builder.const_s(cls_name + "." + field)
        self.builder.emit(MIR_SETATTR, -1, obj, value, key)

    def emit_attr(self, expr: Dict[str, Any]) -> int:
        """A field read: `MIR_GETATTR` keyed the same way, the field's
        type tag riding `arg3` (the `MIR_SEQGET` convention)."""
        obj = self.emit_expr(expr["value"])
        value_node: Dict[str, Any] = expr["value"]
        vtyp: Dict[str, Any] = value_node["type"]
        cls_name: str = vtyp["name"]
        field: str = expr["name"]
        key: int = self.builder.const_s(cls_name + "." + field)
        rkind: str = _type_kind(expr["type"])
        tag: int = _param_tag(rkind)
        return self.builder.emit_val3(MIR_GETATTR, obj, key, tag, tag)

    def emit_subscript(self, expr: Dict[str, Any]) -> int:
        """A list load: container then index, `MIR_SEQGET` with the element
        tag in `arg3` (the direct path's `ps_seq_get` + cell reload)."""
        seq = self.emit_expr(expr["value"])
        index = self.emit_expr(expr["index"])
        result_type: Dict[str, Any] = expr["type"]
        rkind: str = result_type["kind"]
        tag: int = _param_tag(rkind)
        return self.builder.emit_val3(MIR_SEQGET, seq, index, tag, tag)

    def emit_print(self, expr: Dict[str, Any]) -> int:
        """A `print` call: the args lower, then one void `MIR_CALL`.

        The callee is the builtin's name (`print`) in the function's
        callee table: the same table a user callee rides, and the
        emitter resolves the name to the builtin's emission there. The
        arguments evaluate left to right (the direct path's order); the
        call produces nothing (`MIR_VOID`), so the lowerer binds no
        value. The direct path emits the argument expressions in
        `_emit_call`'s first loop, before the dispatch the `print` arm
        reads, so the order matches.
        """
        args: list[Dict[str, Any]] = expr["args"]
        values: list[int] = []
        for arg in args:
            values.append(self.emit_expr(arg))
        callee_vid: int = self.builder.add_callee("print")
        self.builder.emit_call(callee_vid, values, MIR_VOID)
        # The builtin marks its call out of band: a user function named
        # `print` shares the callee-table name, so the emitter reads the
        # marker to tell the builtin form from the user call.
        self.builder.fn.helpers[len(self.builder.fn.ops) - 1] = "print"
        return -1

    def is_none(self, expr: Dict[str, Any]) -> bool:
        """True when the expression's value is the None singleton.

        None is not a runtime value: the direct path emits no code for a
        None const and reads no slot for a None local, so the slice skips
        the same nodes.
        """
        typ: Dict[str, Any] = expr["type"]
        kind: str = typ["kind"]
        return kind == "none"

    def emit_const(self, expr: Dict[str, Any]) -> int:
        typ: Dict[str, Any] = expr["type"]
        tkind: str = typ["kind"]
        # The IR's const payload is the source spell: int and float consts
        # carry a str, bool consts a bool, so each arm reads the payload at
        # its own proven type and converts where the pool wants a number.
        if tkind == "int":
            raw: str = expr["value"]
            return self.builder.const_w(int(raw))
        if tkind == "float":
            raw: str = expr["value"]
            return self.builder.const_d(float(raw))
        if tkind == "bool":
            bval: bool = expr["value"]
            return self.builder.const_b(1 if bval else 0)
        if tkind == "str":
            sval: str = expr["value"]
            # A str constant materializes through a copy instruction, not
            # a bare pool reference: the copy sits in the instruction
            # stream at the point the direct emitter's `emit_expr` ran,
            # so the `%ps.str` insertvalue lines land in evaluation
            # order against the slot reads around them. A deferred
            # constant would materialize at its consumer, after every
            # operand's instructions, which reorders the lines whenever
            # a constant precedes a name read (`"a" == s`).
            return self.builder.emit_copy(self.builder.const_s(sval), MIR_S)
        raise MirLowerError(f"the MIR leaf slice cannot lower a {tkind} constant")

    def emit_binary(self, expr: Dict[str, Any]) -> int:
        left = self.emit_expr(expr["left"])
        right = self.emit_expr(expr["right"])
        typ: Dict[str, Any] = expr["type"]
        tkind: str = typ["kind"]
        op: str = expr["op"]
        if tkind == "int":
            mir_op = _W_BINOPS.get(op)
            if mir_op is not None:
                return self.builder.emit_val(mir_op, left, right, MIR_W)
            return self.emit_helper(_W_HELPER_OPS[op], MIR_W, left, right)
        if tkind == "float":
            mir_op = _D_BINOPS.get(op)
            if mir_op is not None:
                return self.builder.emit_val(mir_op, left, right, MIR_D)
            return self.emit_helper(_D_HELPER_OPS[op], MIR_D, left, right)
        raise MirLowerError(f"the MIR leaf slice cannot lower {op} on {tkind}")

    def emit_cast(self, expr: Dict[str, Any]) -> int:
        """An `int_to_float` cast: `MIR_EXTWD`, the i64-to-double widen.

        The emitter renders it as `sitofp i64 <operand> to double`, the
        direct path's spelling for the same node.
        """
        op: str = expr["op"]
        if op != "int_to_float":
            raise MirLowerError(f"the MIR leaf slice cannot lower cast {op}")
        operand = self.emit_expr(expr["value"])
        return self.builder.emit_val(MIR_EXTWD, operand, -1, MIR_D)

    def emit_helper(self, helper: str, tag: int, left: int, right: int) -> int:
        """A runtime-helper binary op, carried as a `MIR_COPY` with the

        helper recorded out of band. The MIR op vocabulary has no call op
        yet (calls are a later slice), so the copy instruction's index maps
        to the helper name on the function; the emitter renders it as
        `call <tag> @ps.<name>` and needs the operands only. The copy's
        result type rides `arg2` (the `emit_copy` convention), so the
        verifier's type walk stays true to the value's tag.
        """
        vid = self.builder.emit_val(MIR_COPY, left, right, tag)
        index = len(self.builder.fn.ops) - 1
        # `helpers` carries (helper, tag): the copy's arg1/arg2 hold the
        # operands, so the result tag lives beside the helper name.
        self.builder.fn.helpers[index] = f"{helper} {tag}"
        return vid

    def emit_compare(self, expr: Dict[str, Any]) -> int:
        left = self.emit_expr(expr["left"])
        right = self.emit_expr(expr["right"])
        lexpr: Dict[str, Any] = expr["left"]
        ltyp: Dict[str, Any] = lexpr["type"]
        kind: str = ltyp["kind"]
        okey: str = expr["op"]
        if kind == "str":
            # `ps_str_cmp` returns an i32 ordering; the direct path
            # compares it to 0 with `icmp eq`/`ne`. The helper copy's
            # name carries the predicate to the emitter, which renders
            # the extractvalue/call/icmp sequence the direct path emits.
            helper = "str_cmp_eq" if okey == "==" else "str_cmp_ne"
            return self.emit_helper(helper, MIR_B, left, right)
        table: Dict[str, int] = _D_CMPOPS if kind == "float" else _W_CMPOPS
        mir_op: int = table[okey]
        return self.builder.emit_val(mir_op, left, right, MIR_B)

    def emit_unary(self, expr: Dict[str, Any]) -> int:
        operand = self.emit_expr(expr["operand"])
        op: str = expr["op"]
        typ: Dict[str, Any] = expr["type"]
        tkind: str = typ["kind"]
        if op == "not":
            return self.builder.emit_val(MIR_NOTB, operand, -1, MIR_B)
        if op == "-" and tkind == "int":
            return self.builder.emit_val(MIR_NEGW, operand, -1, MIR_W)
        if op == "~" and tkind == "int":
            # `~x` is `x ^ -1`: the direct path spells it `xor i64 v, -1`,
            # which a `MIR_XORW` against the interned -1 const renders
            # byte for byte.
            minus_one = self.builder.const_w(-1)
            return self.builder.emit_val(MIR_XORW, operand, minus_one, MIR_W)
        raise MirLowerError(f"the MIR leaf slice cannot lower {op} on {tkind}")


def lower_function(fn: Dict[str, Any], callees: Dict[str, Dict[str, Any]] | None = None) -> MirFunction:
    """Lower one in-slice IR function to MIR. `supported_fn` must hold.

    `callees` is the admitted callee table (name -> IR function record);
    a call lowers to a `MIR_CALL` only when its name is in it, so a
    function with no calls needs no table.
    """
    table: Dict[str, Dict[str, Any]] = {}
    if callees is not None:
        table = callees
    return _FnLowerer(fn, table).run()


def lower_module(module: Dict[str, Any]) -> MirModule:
    """Lower every in-slice function; out-of-slice functions are skipped.

    The skipped names come back on `module.skipped` so the emitter can
    refuse a module whose entry the slice cannot lower, instead of
    silently emitting a module without it.

    The callee table: a call in an in-slice body must name a plain
    module-level user function that itself lowers clean, with an arity
    matching the call, so the lowerer can pack the callee as a call and
    the emitter can resolve the symbol. A function a callee names but
    that is out of the slice itself drags its callers out (pairwise,
    transitively). Pass two is a greatest fixpoint: it starts from every
    plain-shape candidate and drops any whose callee set leaves the
    candidate pool, so recursion cycles (a self-call, or mutual
    recursion) stay admitted while a caller of a function that is out of
    the slice by its own shape is still dragged out.
    """
    fns: list[Dict[str, Any]] = module["functions"]
    # Pass one: plain-shape candidates by name. A later def shadows an
    # earlier one only if names repeat, which the lowerer refuses
    # upstream; the dict keeps the first.
    by_name: Dict[str, Dict[str, Any]] = {}
    for fn in fns:
        fname: str = fn["name"]
        if fname not in by_name and supported_fn(fn):
            by_name[fname] = fn
    # Pass two: greatest fixpoint of "every callee is a candidate". Each
    # round rebuilds the admitted set from the survivors of the previous
    # one (the self-host subset has no `del` or `dict()`/`list()` calls),
    # so a caller of a dropped callee drops in the next round.
    admitted: Dict[str, Dict[str, Any]] = by_name
    settling: bool = True
    while settling:
        settling = False
        kept: Dict[str, Dict[str, Any]] = {}
        for fname in admitted:
            fn = admitted[fname]
            if _calls_in_slice(fn["body"], admitted):
                kept[fname] = fn
            else:
                settling = True
        admitted = kept
    functions: list[MirFunction] = []
    skipped: list[str] = []
    for fn in fns:
        fname2: str = fn["name"]
        fn_admitted: Dict[str, Any] | None = admitted.get(fname2)
        if fn_admitted is not None and supported_module_fn(fn, admitted):
            functions.append(_FnLowerer(fn, admitted).run())
        else:
            skipped.append(fname2)
    source: str = module["sourceFile"]
    entry: str = module["entry"]
    mir_module: MirModule = MirModule(source_file=source, functions=functions, entry=entry)
    mir_module.skipped = skipped
    return mir_module
