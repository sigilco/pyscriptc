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
MIR_NEGD = 11

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
MIR_MAP_PUT = 40
MIR_TRYPACK = 41
MIR_TRYCALL = 42
MIR_FLOWLD = 43
MIR_TRYICMP = 44
MIR_TRYNE = 45
MIR_EXCCODE = 46
MIR_EXCISA = 47
MIR_RETLOD = 48
MIR_EXCCUR = 49
MIR_RERAISE = 50
MIR_RAISEEXC = 51
MIR_UNREACH = 52
MIR_INITCALL = 53
# The dict lane: `ps_map_get`/`ps_map_has`/`ps_map_len` behind the dict
# subscript, `in` and `len` forms. `MIR_MAP_GET` reads `arg3` as the
# result tag, the `MIR_SEQGET` convention.
MIR_MAP_GET = 54
MIR_MAP_HAS = 55
MIR_MAP_LEN = 56

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

# The exception-name to code table the try handler match reads, spelled
# as int literals per the self-host spell (`pyscriptc.builtins` is not in
# the closed import table). It mirrors `EXCEPTIONS` then
# `RESERVED_EXCEPTIONS`: `_exc_match` consults the first table, then the
# second, and skips a name neither carries. A drift is a byte-identity
# failure, not a silent wrong match.
_MIR_EXC = {
    "BaseException": 1,
    "BaseExceptionGroup": 2,
    "ExceptionGroup": 3,
    "GeneratorExit": 4,
    "KeyboardInterrupt": 5,
    "SystemExit": 6,
    "Exception": 7,
    "ArithmeticError": 8,
    "FloatingPointError": 9,
    "OverflowError": 10,
    "ZeroDivisionError": 11,
    "AssertionError": 12,
    "AttributeError": 13,
    "BufferError": 14,
    "EOFError": 15,
    "ImportError": 16,
    "ModuleNotFoundError": 17,
    "LookupError": 18,
    "IndexError": 19,
    "KeyError": 20,
    "MemoryError": 21,
    "NameError": 22,
    "UnboundLocalError": 23,
    "OSError": 24,
    "BlockingIOError": 25,
    "ChildProcessError": 26,
    "ConnectionError": 27,
    "BrokenPipeError": 28,
    "ConnectionAbortedError": 29,
    "ConnectionRefusedError": 30,
    "ConnectionResetError": 31,
    "FileExistsError": 32,
    "FileNotFoundError": 33,
    "InterruptedError": 34,
    "IsADirectoryError": 35,
    "NotADirectoryError": 36,
    "PermissionError": 37,
    "ProcessLookupError": 38,
    "TimeoutError": 39,
    "ReferenceError": 40,
    "RuntimeError": 41,
    "NotImplementedError": 42,
    "RecursionError": 43,
    "StopAsyncIteration": 44,
    "StopIteration": 45,
    "SyntaxError": 46,
    "IndentationError": 47,
    "TabError": 48,
    "SystemError": 49,
    "TypeError": 50,
    "ValueError": 51,
    "UnicodeError": 52,
    "UnicodeDecodeError": 53,
    "UnicodeEncodeError": 54,
    "UnicodeTranslateError": 55,
    "Warning": 56,
    "BytesWarning": 57,
    "DeprecationWarning": 58,
    "EncodingWarning": 59,
    "FutureWarning": 60,
    "ImportWarning": 61,
    "PendingDeprecationWarning": 62,
    "ResourceWarning": 63,
    "RuntimeWarning": 64,
    "SyntaxWarning": 65,
    "UnicodeWarning": 66,
    "UserWarning": 67,
    "EnvironmentError": 24,
    "IOError": 24,
    # `RESERVED_EXCEPTIONS` names `EXCEPTIONS` does not carry; the three it
    # shares (`GeneratorExit`, `StopAsyncIteration`, `TimeoutError`) keep
    # the `EXCEPTIONS` codes `_exc_match` reads first.
    "CancelledError": 2147483634,
    "Empty": 2147483636,
    "Full": 2147483637,
}
# `builtins.USER_EXC_BASE`: a handler `codes` entry at or above it is a
# user class and lowers to an `icmp eq` the slice does not carry.
_MIR_USER_EXC_BASE = 256


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

# The str compare predicate suffixes: `ps_str_cmp` returns an i32
# ordering and the direct path's `icmp` reads it against 0.
_STR_CMPS = {
    "==": "eq",
    "!=": "ne",
    "<": "slt",
    "<=": "sle",
    ">": "sgt",
    ">=": "sge",
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

_EXPR_KINDS = frozenset(
    {
        "const",
        "name",
        "binary",
        "compare",
        "unary",
        "call",
        "cast",
        "list",
        "dict",
        "subscript",
        "attr",
        "contains",
    }
)
_STMT_KINDS = frozenset({"assign", "expr", "return", "pass", "if", "while", "raise", "setitem", "setattr", "try"})


def supported_type(typ: Dict[str, Any]) -> bool:
    """The slice test for one HIR type record.

    Scalars ride their existing tags (`str` rides `MIR_S`); `list[int]`
    and `list[float]` ride the shared `MIR_P` pointer tag. The element
    type stays in HIR: MIR sees only the pointer shape, so nested or
    non-scalar element lists stay out.

    A `class` value rides `MIR_P` too: every class kind qualifies at the
    type level, and the constructor call, the field ops and the
    call/return walks gate which class shapes the slice carries — the
    `tuple_N_*` unpacking classes and user classes whose constructor
    arguments and fields stay in the slice.
    """
    kind: str = typ["kind"]
    if kind in ("int", "float", "bool", "str"):
        return True
    if kind == "class":
        return "name" in typ
    if kind == "dict":
        key: Dict[str, Any] = typ["key"]
        value: Dict[str, Any] = typ["value"]
        kkind: str = key["kind"]
        return kkind in ("int", "str") and supported_type(value)
    if kind != "list":
        return False
    return _supported_seq_elem(typ["elem"])


def _supported_seq_elem(elem: Dict[str, Any]) -> bool:
    """The element types a sequence value may carry: int, float and str
    at the leaves, and lists of those recursively (the ABI is one pointer
    tag either way; the element semantics stay in HIR)."""
    ekind: str = elem["kind"]
    if ekind in ("int", "float", "str", "bool"):
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
    if kind == "dict":
        if not supported_type(expr["type"]):
            return False
        pairs: list[Dict[str, Any]] = expr["pairs"]
        for pair in pairs:
            if not supported_expr(pair["key"]) or not supported_expr(pair["value"]):
                return False
        return True
    if kind == "subscript":
        value: Dict[str, Any] = expr["value"]
        index: Dict[str, Any] = expr["index"]
        vkind: str = _type_kind(value["type"])
        if vkind == "str":
            # `s[i]`: the direct path's `ps_str_get` buffer-out call; the
            # index must be an int and the result a one-character str.
            itype: Dict[str, Any] = index["type"]
            ikind: str = itype["kind"]
            if ikind != "int":
                return False
            rtype: Dict[str, Any] = expr["type"]
            rkind: str = rtype["kind"]
            if rkind != "str":
                return False
            return supported_expr(value) and supported_expr(index)
        if vkind == "dict":
            # A dict load: the key kind must match the dict's key type
            # and the loaded value must stay inside the slice's types.
            vtype: Dict[str, Any] = value["type"]
            if not supported_type(vtype):
                return False
            if _type_kind(index["type"]) != _type_kind(vtype["key"]):
                return False
            if not supported_type(expr["type"]):
                return False
            return supported_expr(value) and supported_expr(index)
        if not _is_supported_list(value["type"]):
            return False
        itype = index["type"]
        ikind = itype["kind"]
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
    if kind == "contains":
        return _supported_contains(expr)
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
    the slice), and every argument is a scalar or str in the slice.
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
        if akind not in ("int", "float", "bool", "str"):
            return False
        if not supported_expr(arg):
            return False
    return True


def _supported_call(expr: Dict[str, Any]) -> bool:
    """The slice test for one call expression.

    A `print` call is the void builtin form (see `_supported_print`);
    `len`, `str` and `int` are the admitted builtin forms, and the
    `builtins` module's `int`/`bin`/`hex`/`oct` are the admitted abi
    forms (see `_supported_builtin_call`/`_supported_module_call`).
    Everything else names a plain same-module user function: the callee
    record's kind is `function` (the IR's spelling for a same-module name
    resolved at lowering), the name is defined by a function of this
    module (checked against `supported_fn`'s own param/return rules by
    the caller through `_callee_in_slice`), and every argument is in the
    slice. Class constructor calls admit the same way (the
    `MIR_INITCALL` form). Everything else (other builtins, other module
    members, methods, value callees) stays outside.
    """
    callee: Dict[str, Any] = expr["callee"]
    ckind: str = callee["kind"]
    cname: str = callee["name"]
    # The str builtins admit first; a `len` of a list falls through to
    # the list arm below, and the `builtins` module's bin/hex/oct admit
    # before `list.append`'s own arm (`builtins.int` rides the rt-call
    # shape in the module arm).
    if ckind == "builtin" and _supported_builtin_call(cname, expr):
        return True
    if ckind == "module":
        bmodule: str = callee["module"]
        if bmodule == "builtins" and _supported_module_call(expr):
            return True
    args: list[Dict[str, Any]] = expr["args"]
    if ckind == "function":
        for arg in args:
            atyp: Dict[str, Any] = arg["type"]
            akind: str = atyp["kind"]
            if (
                akind not in ("int", "float", "bool", "str")
                and not _is_supported_list(atyp)
                and not _is_supported_class(atyp)
                and akind != "dict"
            ):
                return False
            if not supported_expr(arg):
                return False
        return True
    if ckind == "class":
        # A `tuple_N_*` constructor keeps the bare-alloc form: the IR
        # emits it with no arguments (fields are written by `setattr`
        # after the call). Every other class call is the user-class
        # constructor (`MIR_INITCALL`): each argument must be in the
        # slice and the result a class value.
        cname: str = callee["name"]
        rtype: Dict[str, Any] = expr["type"]
        if not _is_supported_class(rtype):
            return False
        if cname.startswith("tuple_"):
            return len(args) == 0
        for arg in args:
            if not supported_expr(arg):
                return False
        return True
    if ckind == "method":
        return _supported_method_call(callee, expr)
    if ckind == "builtin":
        cname: str = callee["name"]
        if cname == "print":
            return _supported_print(expr)
        if cname == "len" and len(args) == 1:
            arg: Dict[str, Any] = args[0]
            atype: Dict[str, Any] = arg["type"]
            ak: str = _type_kind(atype)
            if _is_supported_list(atype) or (ak == "dict" and supported_type(atype)):
                return supported_expr(arg)
            return False
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
        if cmodule == "builtins" and mname == "dict.keys" and len(args) == 1:
            arg: Dict[str, Any] = args[0]
            atype: Dict[str, Any] = arg["type"]
            if (
                _type_kind(atype) == "dict"
                and supported_type(atype)
                and _is_supported_list(expr["type"])
                and "symbol" in expr
                and _expr_symbol(expr) == "ps_dict_keys"
                and supported_expr(arg)
            ):
                return True
        if cmodule == "builtins" and mname == "format":
            return _supported_format(expr)
        if cmodule == "builtins" and mname in _CONTAINER_REPR_CALLS:
            return _supported_container_repr(expr)
        if _rt_call_shape(cmodule, mname, expr):
            return _rt_args_order_ok(args)
    return False


# The `builtins` callee names a lowered `print(container)` argument
# carries: `seq_repr` for a list or set, `map_repr` for a dict. Each is an
# abi call whose `ps_*_repr` symbol the IR record spells.
_CONTAINER_REPR_CALLS = frozenset({"seq_repr", "map_repr"})


def _supported_container_repr(expr: Dict[str, Any]) -> bool:
    """The slice test for a `print` container repr call.

    The admitted shape is the lowering's own: the container value first
    (a supported list or dict), then the int const kind tags the runtime
    repr reads (a PsSeq cell is only a byte size, so the element kind
    rides the call), and a str result. Anything else stays outside.
    """
    if "symbol" not in expr:
        return False
    symbol: str = _expr_symbol(expr)
    args: list[Dict[str, Any]] = expr["args"]
    if _type_kind(expr["type"]) != "str":
        return False
    if symbol == "ps_seq_repr":
        if len(args) != 3:
            return False
        first: Dict[str, Any] = args[0]
        if _type_kind(first["type"]) != "list":
            return False
    elif symbol == "ps_map_repr":
        if len(args) != 2:
            return False
        first = args[0]
        if _type_kind(first["type"]) != "dict":
            return False
    else:
        return False
    for tag_arg in args[1:]:
        if tag_arg["kind"] != "const" or _type_kind(tag_arg["type"]) != "int":
            return False
    return supported_expr(first)


def _supported_format(expr: Dict[str, Any]) -> bool:
    """The slice test for `builtins.format` (`ps_str_format`).

    The admitted shape is the positional percent/dot form: a str format
    constant first, then int/float arguments only (the `%ps.arg` marshal
    covers those two kinds), no keyword names and a str result. The
    style rides the call marker since MIR carries no expr payload.
    """
    if "symbol" not in expr or _expr_symbol(expr) != "ps_str_format":
        return False
    if "format_kwnames" in expr:
        nkw: int = expr["format_kwnames"]
        if nkw != 0:
            return False
    style: str = "percent"
    if "format_style" in expr:
        style = _opt_str(expr, "format_style")
    if style != "percent" and style != "dot":
        return False
    args: list[Dict[str, Any]] = expr["args"]
    if len(args) < 2:
        return False
    fmt_arg: Dict[str, Any] = args[0]
    if _type_kind(fmt_arg["type"]) != "str":
        return False
    if not supported_expr(fmt_arg):
        return False
    for arg in args[1:]:
        akind: str = _type_kind(arg["type"])
        if akind != "int" and akind != "float":
            return False
        if not supported_expr(arg):
            return False
    return _type_kind(expr["type"]) == "str"


def _supported_method_call(callee: Dict[str, Any], expr: Dict[str, Any]) -> bool:
    """The slice test for one method call.

    `str.join` admits the separator (a str, carried as `args[0]`), one
    `list[str]` argument and a str result. `str.split`/`str.rsplit`
    admit the receiver (a str, `args[0]`), an optional str separator
    and an optional int maxsplit, with a `list[str]` result. Every
    other method stays outside.
    """
    if "receiver" not in callee or callee["receiver"] != "str":
        return False
    name: str = callee["name"]
    args: list[Dict[str, Any]] = expr["args"]
    if name == "split" or name == "rsplit":
        return _supported_str_split(expr)
    if name != "join":
        return False
    if len(args) != 2:
        return False
    sep: Dict[str, Any] = args[0]
    seq: Dict[str, Any] = args[1]
    if _type_kind(sep["type"]) != "str":
        return False
    seq_type: Dict[str, Any] = seq["type"]
    if _type_kind(seq_type) != "list" or _type_kind(seq_type["elem"]) != "str":
        return False
    if _type_kind(expr["type"]) != "str":
        return False
    return supported_expr(sep) and supported_expr(seq)


def _supported_str_split(expr: Dict[str, Any]) -> bool:
    """The slice test for `str.split`/`str.rsplit`.

    The receiver is `args[0]` (a str); `args[1]` is the optional str
    separator and `args[2]` the optional int maxsplit, the arities the
    direct `ps_str_split_m` arm renders. The result is `list[str]`.
    """
    args: list[Dict[str, Any]] = expr["args"]
    if len(args) < 1 or len(args) > 3:
        return False
    recv: Dict[str, Any] = args[0]
    if _type_kind(recv["type"]) != "str" or not supported_expr(recv):
        return False
    rtype: Dict[str, Any] = expr["type"]
    if _type_kind(rtype) != "list" or _type_kind(rtype["elem"]) != "str":
        return False
    if len(args) >= 2:
        sep: Dict[str, Any] = args[1]
        if _type_kind(sep["type"]) != "str" or not supported_expr(sep):
            return False
    if len(args) == 3:
        maxsplit: Dict[str, Any] = args[2]
        if _type_kind(maxsplit["type"]) != "int" or not supported_expr(maxsplit):
            return False
    return True


def _opt_str(expr: Dict[str, Any], key: str) -> str:
    """An optional str payload on an expr record; `""` when absent."""
    if key not in expr:
        return ""
    val: str = expr[key]
    return val


def _expr_symbol(expr: Dict[str, Any]) -> str:
    """The `symbol` payload of a call expr; `""` when the key is absent."""
    if "symbol" not in expr:
        return ""
    symbol: str = expr["symbol"]
    return symbol


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
    if cmodule == "builtins" and mname == "global_get":
        # A module-global read: the key is a str const and the result
        # rides the pointer (`ps_global_get_ptr`) or i64
        # (`ps_global_get_i64`) ABI shapes the `rt:` call already spells.
        # The str/path forms return through a buffer out-param, outside
        # the slice.
        if symbol != "ps_global_get_ptr" and symbol != "ps_global_get_i64":
            return False
        if len(args) != 1:
            return False
        rkind: str = _type_kind(expr["type"])
        if symbol == "ps_global_get_i64":
            if rkind != "int":
                return False
        elif rkind in ("int", "float", "bool", "str", "none"):
            return False
        if not supported_type(expr["type"]):
            return False
        key_arg: Dict[str, Any] = args[0]
        return _type_kind(key_arg["type"]) == "str" and supported_expr(key_arg)
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
    if cmodule == "builtins" and mname == "dict.keys":
        return symbol
    if cmodule == "builtins" and mname == "global_get":
        return symbol
    return ""


# The admitted builtin calls, as a name set plus "<name>:<arg-kind>"
# pair set: `len` reads a str's code-point count, `str` renders a scalar
# (a str argument is the identity), `int` converts a float or a str.
_BUILTIN_CALLS = frozenset({"len", "str", "int"})
_BUILTIN_CALL_ARGS = frozenset({"len:str", "str:int", "str:float", "str:bool", "str:str", "int:float", "int:str"})

# The admitted `builtins` module calls the `_rt_call_shape` table does
# not cover: each takes one argument and rides the direct emitter's
# `_emit_abi` walk — the `bin`/`hex`/`oct`/`chr` forms return a str
# through the `_buf_out` ABI and `ord` returns an i64 — so the admission
# keys on the callee name plus the argument kind, the result kind is
# checked against `_MODULE_CALL_RESULTS`, and the call carries its
# `ps_*` symbol. `builtins.int` itself rides the `_rt_call_shape` path
# above.
_MODULE_CALLS = frozenset({"bin", "hex", "oct", "chr", "ord"})
_MODULE_CALL_ARGS = frozenset({"bin:int", "hex:int", "oct:int", "chr:int", "ord:str"})
_MODULE_CALL_RESULTS = {"bin": "str", "hex": "str", "oct": "str", "chr": "str", "ord": "int"}


def _supported_builtin_call(name: str, expr: Dict[str, Any]) -> bool:
    """The slice test for one admitted builtin call (`len`/`str`/`int`)."""
    args: list[Dict[str, Any]] = expr["args"]
    if len(args) != 1:
        return False
    arg: Dict[str, Any] = args[0]
    atyp: Dict[str, Any] = arg["type"]
    akind: str = atyp["kind"]
    if f"{name}:{akind}" not in _BUILTIN_CALL_ARGS:
        return False
    return supported_expr(arg)


def _supported_module_call(expr: Dict[str, Any]) -> bool:
    """The slice test for one admitted `builtins` module call.

    The abi path needs the runtime symbol the IR resolved onto the call
    record; without it there is no spelling to emit.
    """
    callee: Dict[str, Any] = expr["callee"]
    name: str = callee["name"]
    if "symbol" not in expr:
        return False
    args: list[Dict[str, Any]] = expr["args"]
    if len(args) != 1:
        return False
    arg: Dict[str, Any] = args[0]
    atyp: Dict[str, Any] = arg["type"]
    akind: str = atyp["kind"]
    if f"{name}:{akind}" not in _MODULE_CALL_ARGS:
        return False
    expected: str = _MODULE_CALL_RESULTS[name]
    rkind: str = _type_kind(expr["type"])
    if rkind != expected:
        return False
    return supported_expr(arg)


def _supported_binary(expr: Dict[str, Any]) -> bool:
    typ: Dict[str, Any] = expr["type"]
    tkind: str = typ["kind"]
    op: str = expr["op"]
    if tkind == "str":
        if op != "+":
            return False
    elif tkind == "int":
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
    if lkind not in ("int", "float", "bool", "str"):
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
        # the six ordering/equality predicates all ride the same call.
        if op not in ("==", "!=", "<", "<=", ">", ">="):
            return False
        return supported_expr(left) and supported_expr(right)
    if op not in _W_CMPOPS:
        return False
    if lkind not in ("int", "float", "bool"):
        return False
    return supported_expr(left) and supported_expr(right)


def _supported_contains(expr: Dict[str, Any]) -> bool:
    """The slice test for one `in`/`not in` containment expression.

    Only the str hay/needle pair lowers: the direct path's
    `ps_str_find` sequence is the one the MIR helper copy renders.
    """
    typ: Dict[str, Any] = expr["type"]
    tkind: str = typ["kind"]
    if tkind != "bool":
        return False
    hay: Dict[str, Any] = expr["hay"]
    needle: Dict[str, Any] = expr["needle"]
    haytype: Dict[str, Any] = hay["type"]
    haykind: str = haytype["kind"]
    ntype: Dict[str, Any] = needle["type"]
    nkind: str = ntype["kind"]
    if haykind == "dict":
        # A dict membership test: the needle kind must match the dict's
        # key type and the dict type must stay inside the slice.
        if not supported_type(haytype):
            return False
        if nkind != _type_kind(haytype["key"]):
            return False
        return supported_expr(hay) and supported_expr(needle)
    if haykind != "str" or nkind != "str":
        return False
    return supported_expr(hay) and supported_expr(needle)


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
        ckind: str = _type_kind(container["type"])
        if ckind == "dict":
            # A dict store: the key kind must match the dict's key type
            # and the stored value must stay inside the slice's types.
            ctype: Dict[str, Any] = container["type"]
            if not supported_type(ctype):
                return False
            if _type_kind(index_node["type"]) != _type_kind(ctype["key"]):
                return False
            if not supported_type(item_value["type"]):
                return False
            return supported_expr(container) and supported_expr(index_node) and supported_expr(item_value)
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
    if kind == "try":
        # The try body is outlined by the direct path's `ps.try.N`
        # function, which the splice leaves untouched, so the body itself
        # is not slice-checked here. What the callsite emits is: the env
        # pack, the `ps_try_call`, the flow/exception probes, the handler
        # match and bodies (plain try), or the `fin.*` dispatch plus
        # `finalbody` at three outcome sites (E2 finally desugar). Those
        # are the parts this arm checks.
        finalbody: list[Dict[str, Any]] = _mir_items(stmt, "finalbody")
        if finalbody:
            # E2: body+handlers+orelse+flags ride the inner try inside
            # the outline; only `finalbody` emits at the callsite, once
            # per outcome, so only it must be in the slice.
            for s in finalbody:
                if not supported_stmt(s):
                    return False
            return True
        orelse_body: list[Dict[str, Any]] = _mir_items(stmt, "orelse")
        if orelse_body:
            return False
        for flag in ("brk", "cont"):
            if flag in stmt and stmt[flag] is not None:
                return False
        handlers: list[Dict[str, Any]] = _mir_items(stmt, "handlers")
        for handler in handlers:
            if not _supported_try_handler(handler):
                return False
            for s in _mir_items(handler, "body"):
                if not supported_stmt(s):
                    return False
        return True
    return True


def _supported_try_handler(handler: Dict[str, Any]) -> bool:
    """The handler-match shape the slice lowers: one builtin-name `ps_exc_isa`.

    Mirrors `_exc_match`'s accounting for the admitted subset: a bare
    handler folds to `true`, an `excslot` binds `ps_exc_current` (a slot
    type the slice does not carry), each resolvable name contributes one
    `ps_exc_isa` check, each `codes` entry at or above `USER_EXC_BASE`
    contributes an `icmp eq` the slice does not carry, and several checks
    fold through `or`. The slice admits exactly one name check and no
    user-code checks.
    """
    if "bare" in handler and handler["bare"]:
        return False
    if "excslot" in handler and handler["excslot"] is not None:
        return False
    checks: int = 0
    names: list[str] = _mir_items(handler, "exc")
    for name in names:
        if name in _MIR_EXC:
            checks = checks + 1
    codes: list[int] = _mir_items(handler, "codes")
    for code in codes:
        if code >= _MIR_USER_EXC_BASE:
            return False
    return checks == 1


def _mir_items(node: Dict[str, Any], key: str) -> list[Dict[str, Any]]:
    """A list field of an IR record, or [] when absent (the `_items`
    spelling, kept local per the closed import table)."""
    if key not in node:
        return []
    items: list[Dict[str, Any]] = node[key]
    return items


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


def supported_module_fn(
    fn: Dict[str, Any], callees: Dict[str, Dict[str, Any]], module_fns: Dict[str, Dict[str, Any]]
) -> bool:
    """The module-level slice test: `supported_fn` plus the callee checks.

    A call the body walk admits names a same-module function; here the
    callee must itself be a plain module-level user function in the
    slice, with an arity matching the call — or a name `module_fns`
    does not define at all (a fenced sibling the IR still references,
    which has no callee record to check). The checks are pairwise
    (call site against the callee's record), so a call to a function
    that would lower clean but is out of the slice by its own shape
    (a float param, an env) keeps the caller out too.
    """
    if not supported_fn(fn):
        return False
    body: list[Dict[str, Any]] = fn["body"]
    return _calls_in_slice(body, callees, module_fns)


def _calls_in_slice(
    stmts: list[Dict[str, Any]], callees: Dict[str, Dict[str, Any]], module_fns: Dict[str, Dict[str, Any]]
) -> bool:
    """Every call nested in `stmts` names an in-slice callee with a
    matching arity. `callees` is the admitted set, which may contain the
    caller itself: the module-level fixpoint admits recursion cycles
    (f calls f, or f and g calling each other), and a self-call reads the
    caller's own record for the arity check. `module_fns` is every
    function the module defines: a call naming a name outside it is the
    non-module callee the slice admits."""
    for stmt in stmts:
        if not _calls_in_slice_stmt(stmt, callees, module_fns):
            return False
    return True


def _calls_in_slice_stmt(
    stmt: Dict[str, Any], callees: Dict[str, Dict[str, Any]], module_fns: Dict[str, Dict[str, Any]]
) -> bool:
    kind: str = stmt["kind"]
    if kind == "assign":
        value: Dict[str, Any] = stmt["value"]
        return _calls_in_slice_expr(value, callees, module_fns)
    if kind == "expr":
        expr: Dict[str, Any] = stmt["expr"]
        return _calls_in_slice_expr(expr, callees, module_fns)
    if kind == "return":
        rvalue: Dict[str, Any] = stmt["value"]
        return _calls_in_slice_expr(rvalue, callees, module_fns)
    if kind == "setitem":
        set_container: Dict[str, Any] = stmt["container"]
        set_index: Dict[str, Any] = stmt["index"]
        set_value: Dict[str, Any] = stmt["value"]
        if not _calls_in_slice_expr(set_container, callees, module_fns):
            return False
        if not _calls_in_slice_expr(set_index, callees, module_fns):
            return False
        return _calls_in_slice_expr(set_value, callees, module_fns)
    if kind == "setattr":
        set_object: Dict[str, Any] = stmt["object"]
        set_value = stmt["value"]
        if not _calls_in_slice_expr(set_object, callees, module_fns):
            return False
        return _calls_in_slice_expr(set_value, callees, module_fns)
    if kind == "if":
        if not _calls_in_slice_expr(stmt["cond"], callees, module_fns):
            return False
        then_body: list[Dict[str, Any]] = stmt["then"]
        for s in then_body:
            if not _calls_in_slice_stmt(s, callees, module_fns):
                return False
        else_body: list[Dict[str, Any]] = stmt["else"]
        for s in else_body:
            if not _calls_in_slice_stmt(s, callees, module_fns):
                return False
        return True
    if kind == "while":
        if not _calls_in_slice_expr(stmt["cond"], callees, module_fns):
            return False
        body: list[Dict[str, Any]] = stmt["body"]
        for s in body:
            if not _calls_in_slice_stmt(s, callees, module_fns):
                return False
        return True
    if kind == "try":
        # Only the callsite parts can carry MIR calls: the try body rides
        # the direct-emitted outline. A finally desugar puts everything
        # but `finalbody` inside the outline; a plain try puts the
        # handler bodies (and `orelse`, fenced above) at the callsite.
        finalbody: list[Dict[str, Any]] = _mir_items(stmt, "finalbody")
        if finalbody:
            return _calls_in_slice(finalbody, callees, module_fns)
        for handler in _mir_items(stmt, "handlers"):
            if not _calls_in_slice(_mir_items(handler, "body"), callees, module_fns):
                return False
        return _calls_in_slice(_mir_items(stmt, "orelse"), callees, module_fns)
    return True


def _calls_in_slice_expr(
    expr: Dict[str, Any], callees: Dict[str, Dict[str, Any]], module_fns: Dict[str, Dict[str, Any]]
) -> bool:
    kind: str = expr["kind"]
    if kind == "call":
        callee: Dict[str, Any] = expr["callee"]
        args: list[Dict[str, Any]] = expr["args"]
        for arg in args:
            if not _calls_in_slice_expr(arg, callees, module_fns):
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
                if not _calls_in_slice_expr(arg, callees, module_fns):
                    return False
            return True
        target: Dict[str, Any] | None = callees.get(name)
        if target is None:
            # A call to a name no function of this module defines (a
            # fenced sibling the IR still references): there is no
            # callee record to check, so the call rides the lane. A
            # name the module does define but the slice rejected keeps
            # the caller out through the fixpoint.
            return name not in module_fns
        params: list[Dict[str, Any]] = target["params"]
        if len(args) != len(params):
            return False
        return True
    if kind == "binary":
        if not _calls_in_slice_expr(expr["left"], callees, module_fns):
            return False
        return _calls_in_slice_expr(expr["right"], callees, module_fns)
    if kind == "compare":
        if not _calls_in_slice_expr(expr["left"], callees, module_fns):
            return False
        return _calls_in_slice_expr(expr["right"], callees, module_fns)
    if kind == "unary":
        return _calls_in_slice_expr(expr["operand"], callees, module_fns)
    if kind == "cast":
        return _calls_in_slice_expr(expr["value"], callees, module_fns)
    if kind == "list":
        elems: list[Dict[str, Any]] = expr["elems"]
        for elem in elems:
            if not _calls_in_slice_expr(elem, callees, module_fns):
                return False
        return True
    if kind == "subscript":
        if not _calls_in_slice_expr(expr["value"], callees, module_fns):
            return False
        return _calls_in_slice_expr(expr["index"], callees, module_fns)
    if kind == "attr":
        return _calls_in_slice_expr(expr["value"], callees, module_fns)
    if kind == "contains":
        if not _calls_in_slice_expr(expr["hay"], callees, module_fns):
            return False
        return _calls_in_slice_expr(expr["needle"], callees, module_fns)
    return True


def _is_str_const(expr: Dict[str, Any]) -> bool:
    """A str const node: the map cell stashes it as a `%ps.buf` prefix."""
    ekind: str = expr["kind"]
    if ekind != "const":
        return False
    etyp: Dict[str, Any] = expr["type"]
    return _type_kind(etyp) == "str"


def _elem_size(typ: Dict[str, Any]) -> int:
    kind: str = typ["kind"]
    if kind in ("int", "float"):
        return 8
    if kind in ("str", "bytes"):
        return 16
    if kind == "bool":
        return 1
    return 8


def _param_tag(kind: str) -> int:
    if kind == "float":
        return MIR_D
    if kind == "bool":
        return MIR_B
    if kind == "str":
        return MIR_S
    if kind == "list" or kind == "dict" or kind == "class":
        return MIR_P
    return MIR_W


def _fn_result_tag(fn: Dict[str, Any]) -> int:
    rt: Dict[str, Any] = fn["returnType"]
    kind: str = rt["kind"]
    if kind == "none":
        return MIR_VOID
    return _param_tag(kind)


def _mir_used_names(body: list[Dict[str, Any]]) -> Dict[str, int]:
    """Every local name an IR fragment reads or writes, as a set-dict.

    A line-for-line port of the direct emitter's `_used_names`: the try
    env pack must name the same slots in the same sorted order, so the
    walk keeps the same shapes (`name` expressions add `id`, assignments
    add `target`, `for`/`with` add `target`, handler records add their
    `excslot`) and the same skip list. Anything unrecognized contributes
    nothing, matching the direct walk's missed-slot behavior.
    """
    names: Dict[str, int] = {}
    stack: list[Any] = list(body)
    while stack:
        node: Any = stack.pop()
        if isinstance(node, list):
            stack.extend(node)
            continue
        if not isinstance(node, dict):
            continue
        kind: str | None = None
        if "kind" in node:
            kind = node["kind"]
        if kind == "name":
            names[node["id"]] = 1
            continue
        if kind == "assign" or kind == "annassign" or kind == "augassign":
            names[node["target"]] = 1
        elif kind == "for" or kind == "with":
            target_name: str = ""
            if "target" in node:
                target_name = node["target"]
            names[target_name] = 1
        elif kind is None and "excslot" in node:
            # An except handler: its excslot stores the bound record at
            # the callsite, so the slot must be packed even when no
            # statement names it.
            slot: str | None = None
            if node["excslot"] is not None:
                slot = node["excslot"]
            if slot is not None:
                names[slot] = 1
        for key in node:
            value: Any = node[key]
            if (key == "target" or key == "id" or key == "excslot") and isinstance(value, str):
                continue
            if isinstance(value, dict) or isinstance(value, list):
                stack.append(value)
    if "" in names:
        del names[""]
    return names


def _mir_node_cost(node: Any) -> int:
    """Outline count one IR node contributes, recursing lists and dicts."""
    if isinstance(node, list):
        return _mir_list_cost(node)
    if not isinstance(node, dict):
        return 0
    if "kind" in node:
        return _mir_stmt_cost(node)
    total: int = 0
    for key in node:
        total = total + _mir_node_cost(node[key])
    return total


def _mir_list_cost(items: list[Dict[str, Any]]) -> int:
    total: int = 0
    for item in items:
        total = total + _mir_node_cost(item)
    return total


def _mir_stmt_cost(stmt: Dict[str, Any]) -> int:
    """Outline count one statement contributes when the direct emitter
    walks it, in any function context (callsite or inside an outline)."""
    if stmt["kind"] == "try":
        return _mir_try_cost(stmt)
    total: int = 0
    for key in stmt:
        total = total + _mir_node_cost(stmt[key])
    return total


def _mir_try_cost(stmt: Dict[str, Any]) -> int:
    """The `ps.try.N` outlines one try statement consumes.

    A plain try outlines its body once; the handler and `orelse`
    callsites can hide further trys, all counted by `_mir_inner_cost`. A
    finally desugar wraps body+handlers+orelse in an inner try inside one
    outline (two outlines plus the inner's own nested cost) and emits
    `finalbody` at three outcome sites, so a try inside `finalbody`
    outlines three times. Dead code after a terminator is counted anyway:
    the approximation only ever shifts later names in shapes the slice
    does not admit.
    """
    cost: int = 1
    finalbody: list[Dict[str, Any]] = _mir_items(stmt, "finalbody")
    inner: int = _mir_inner_cost(stmt)
    if finalbody:
        cost = cost + 1 + inner + 3 * _mir_list_cost(finalbody)
    else:
        cost = cost + inner
    return cost


def _mir_inner_cost(stmt: Dict[str, Any]) -> int:
    """The nested-try cost inside a try's body, handler bodies and
    `orelse`: everything but the try's own outline and `finalbody`."""
    total: int = _mir_list_cost(_mir_items(stmt, "body"))
    handlers: list[Dict[str, Any]] = _mir_items(stmt, "handlers")
    for handler in handlers:
        total = total + _mir_list_cost(_mir_items(handler, "body"))
    total = total + _mir_list_cost(_mir_items(stmt, "orelse"))
    return total


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
    # Every function name the module defines, admitted or not. A call
    # naming a name outside this table is the admitted non-module
    # callee (a fenced sibling); `None` keeps the strict spelling for
    # `lower_function` callers that pass no module context.
    module_fns: Dict[str, Dict[str, Any]] | None
    # The direct emitter's label counter: `fresh()` hands out one number
    # per label, in statement pre-order. The lowerer reserves the same
    # numbers so the emitted labels land on the direct path's.
    next_label: int
    # The instruction index of each block's terminator, keyed by block
    # id. A forward jump's operand is rewritten once the block it names
    # opens, and the terminator is the block's last instruction.
    block_terminator: Dict[int, int]
    # The direct emitter's `ps.try.N` counter for this function: the
    # module walk seeds it with the outlines every earlier function
    # consumed, and each `emit_try` advances it by the try's whole
    # outline cost (`_mir_try_cost`), so a callsite try names the same
    # `ps.try.N` the direct path's `_emit_outline` minted.
    next_outline: int

    def __init__(
        self,
        fn: Dict[str, Any],
        callees: Dict[str, Dict[str, Any]],
        outline_base: int = 0,
        module_fns: Dict[str, Dict[str, Any]] | None = None,
    ) -> None:
        self.fn = fn
        self.builder = MirBuilder(fn["name"])
        self.callees = callees
        self.module_fns = module_fns
        self.slots = {}
        self.local_types = {}
        self.slot_vids = {}
        self.next_label = 0
        self.block_terminator = {}
        self.next_outline = outline_base

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

    def patch_br_true(self, bid: int, target: int) -> None:
        """Point block `bid`'s `MIR_BR` true edge at `target`.

        The try callsite's `leave`/`susp` probes branch to blocks whose
        ids are only final once the outcome arms lower, the same forward
        edge `patch_br_false` covers on the other side.
        """
        self.builder.patch_succ1(bid, target)

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
            # A body that falls off the end returns None when the
            # function's return type is none; a non-None function that
            # can fall through is refused upstream (PS1004), so the only
            # non-void fall-off the lane sees is a try merge, which the
            # direct path renders `unreachable`.
            rt_kind: str = self.fn["returnType"]["kind"]
            if rt_kind == "none":
                builder.emit_ret_void()
            else:
                builder.emit_unreach()
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
            # the same order and parks the stored value in `arg3`. A dict
            # store rides `MIR_MAP_PUT` the same way (`ps_map_put`).
            container = self.emit_expr(stmt["container"])
            index = self.emit_expr(stmt["index"])
            item = self.emit_expr(stmt["value"])
            if _type_kind(stmt["container"]["type"]) == "dict":
                self.builder.emit(MIR_MAP_PUT, -1, container, index, item)
                return
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
        if kind == "try":
            self.emit_try(stmt)
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

    def emit_try(self, stmt: Dict[str, Any]) -> None:
        """A `try` statement: the callsite half of the direct path's
        `emit_try`. The body (and, for a finally desugar, the inner
        try's handlers) rides the direct-emitted `ps.try.N` outline the
        splice leaves in place; this walk emits only what the caller
        emits: the env pack, the `ps_try_call`, the flow and exception
        probes, and the handler or `fin.*` dispatch.

        Label reservation mirrors `_stmts.emit_try`'s `fresh()` order so
        the emitted `L<n>.<hint>` labels land on the direct path's, and
        `next_outline` names the same `ps.try.N` `_emit_outline` minted.
        """
        finalbody: list[Dict[str, Any]] = _mir_items(stmt, "finalbody")
        if finalbody:
            self._emit_try_finally(stmt, finalbody)
            return
        self._emit_try_plain(stmt)

    def _outline_name(self, stmt: Dict[str, Any]) -> str:
        """The `ps.try.N` symbol this try's outline carries, and the
        counter advance its whole emission costs."""
        name: str = "ps.try." + str(self.next_outline)
        self.next_outline = self.next_outline + _mir_try_cost(stmt)
        return name

    def _try_env_names(self, body: list[Dict[str, Any]]) -> list[str]:
        """The env slot names, in the direct path's order: the sorted
        intersection of the body's used names with the stored slots."""
        used: Dict[str, int] = _mir_used_names(body)
        touched: list[str] = []
        for name in sorted(used):
            if name in self.slot_vids:
                touched.append(name)
        return touched

    def _try_pack(self, names: list[str]) -> int:
        vid: int = self.builder.emit_val(MIR_TRYPACK, -1, -1, MIR_P)
        self.builder.fn.helpers[len(self.builder.fn.ops) - 1] = "trypack " + " ".join(names)
        return vid

    def _try_call(self, env: int, outline: str) -> int:
        vid: int = self.builder.emit_val(MIR_TRYCALL, env, -1, MIR_W)
        self.builder.fn.helpers[len(self.builder.fn.ops) - 1] = "trycall " + outline
        return vid

    def _emit_handler_match(self, ecode: int, handler: Dict[str, Any]) -> int:
        """One `ps_exc_isa` check plus the `!= 0` flag, the admitted
        `_exc_match` shape (`_supported_try_handler` guaranteed it)."""
        builder: MirBuilder = self.builder
        names: list[str] = _mir_items(handler, "exc")
        wanted: int = 0
        for name in names:
            if name in _MIR_EXC:
                wanted = _MIR_EXC[name]
        call: int = builder.emit_val(MIR_EXCISA, ecode, builder.const_w(wanted), MIR_W)
        return builder.emit_val(MIR_TRYNE, call, -1, MIR_B)

    def _emit_try_ret(self) -> None:
        """The propagated return/suspend tail: both spell the same two
        lines at the top level (`_emit_propagated_return` and
        `_emit_propagated_suspend` coincide outside an outline)."""
        builder: MirBuilder = self.builder
        rt_kind: str = self.fn["returnType"]["kind"]
        if rt_kind == "none":
            builder.emit_ret_void()
            return
        tag: int = _fn_result_tag(self.fn)
        value: int = builder.emit_val(MIR_RETLOD, -1, tag, tag)
        builder.emit_ret(value)

    def _emit_try_plain(self, stmt: Dict[str, Any]) -> None:
        """`try`/`except` without `finally`: the `try.handler`/`try.cont`
        dispatch `_stmts.emit_try` emits for the plain shape."""
        builder: MirBuilder = self.builder
        outline: str = self._outline_name(stmt)
        body: list[Dict[str, Any]] = _mir_items(stmt, "body")
        env: int = self._try_pack(self._try_env_names(body))
        code: int = self._try_call(env, outline)
        raised: int = builder.emit_val(MIR_TRYNE, code, -1, MIR_B)
        hand_lid: int = self.reserve_label()
        cont_lid: int = self.reserve_label()
        hand_id: int = len(builder.fn.blocks)
        header_bid: int = builder.current_bid()
        builder.emit_br(raised, hand_id, -1, [], [])
        self._record_terminator()
        builder.start_block(hint="try.handler", label_id=hand_lid)
        merge_lid: int = self.reserve_label()
        ret_lid: int = self.reserve_label()
        ecode: int = builder.emit_val(MIR_EXCCODE, -1, -1, MIR_W)
        handlers: list[Dict[str, Any]] = _mir_items(stmt, "handlers")
        jmp_bids: list[int] = []
        for index in range(len(handlers)):
            handler: Dict[str, Any] = handlers[index]
            take_lid: int = self.reserve_label()
            miss_lid: int = self.reserve_label()
            cond: int = self._emit_handler_match(ecode, handler)
            take_id: int = len(builder.fn.blocks)
            br_bid: int = builder.current_bid()
            builder.emit_br(cond, take_id, -1, [], [])
            self._record_terminator()
            builder.start_block(hint="try.h" + str(index), label_id=take_lid)
            self.emit_body(_mir_items(handler, "body"))
            if not builder.terminated:
                builder.emit_jmp(-1)
                self._record_terminator()
                jmp_bids.append(builder.current_bid())
            miss_id: int = len(builder.fn.blocks)
            self.patch_br_false(br_bid, miss_id)
            builder.start_block(hint="try.miss", label_id=miss_lid)
        # Every handler missed: the direct path reraises the in-flight
        # exception and ends the block.
        builder.emit_reraise()
        cont_id: int = len(builder.fn.blocks)
        self.patch_br_false(header_bid, cont_id)
        # The merge and the propagated-return block sit five blocks past
        # the continuation in the fixed outcome layout (cont, notret,
        # susp, fell, ret, merge), none of which opens a nested block.
        merge_id: int = cont_id + 5
        for jmp_bid in jmp_bids:
            self.patch_jmp(jmp_bid, merge_id)
        builder.start_block(hint="try.cont", label_id=cont_lid)
        flow: int = builder.emit_val(MIR_FLOWLD, -1, -1, MIR_W)
        leave: int = builder.emit_val(MIR_TRYICMP, flow, builder.const_w(1), MIR_B)
        susp: int = builder.emit_val(MIR_TRYICMP, flow, builder.const_w(2), MIR_B)
        susp_lid: int = self.reserve_label()
        notret_lid: int = self.reserve_label()
        fell_lid: int = self.reserve_label()
        notret_id: int = cont_id + 1
        susp_id: int = cont_id + 2
        fell_id: int = cont_id + 3
        ret_id: int = cont_id + 4
        builder.emit_br(leave, ret_id, notret_id, [], [])
        self._record_terminator()
        builder.start_block(hint="try.notret", label_id=notret_lid)
        builder.emit_br(susp, susp_id, fell_id, [], [])
        self._record_terminator()
        builder.start_block(hint="try.susp", label_id=susp_lid)
        self._emit_try_ret()
        builder.start_block(hint="try.fell", label_id=fell_lid)
        builder.emit_jmp(merge_id)
        self._record_terminator()
        builder.start_block(hint="try.ret", label_id=ret_lid)
        self._emit_try_ret()
        builder.start_block(hint="try.merge", label_id=merge_lid)

    def _emit_try_finally(self, stmt: Dict[str, Any], finalbody: list[Dict[str, Any]]) -> None:
        """`try`/`finally` (the E2 desugar): body+handlers+orelse ride an
        inner try inside one outline; the callsite runs `finalbody` at
        the raise, return and continue outcome sites."""
        builder: MirBuilder = self.builder
        outline: str = self._outline_name(stmt)
        inner: Dict[str, Any] = {
            "kind": "try",
            "body": _mir_items(stmt, "body"),
            "handlers": _mir_items(stmt, "handlers"),
            "span": stmt["span"] if "span" in stmt else {},
        }
        orelse: list[Dict[str, Any]] = _mir_items(stmt, "orelse")
        if orelse:
            inner["orelse"] = orelse
        for flag in ("brk", "cont"):
            if flag in stmt and stmt[flag] is not None:
                inner[flag] = stmt[flag]
        env: int = self._try_pack(self._try_env_names([inner]))
        code: int = self._try_call(env, outline)
        raised: int = builder.emit_val(MIR_TRYNE, code, -1, MIR_B)
        raise_lid: int = self.reserve_label()
        norm_lid: int = self.reserve_label()
        merge_lid: int = self.reserve_label()
        ret_lid: int = self.reserve_label()
        raise_id: int = len(builder.fn.blocks)
        header_bid: int = builder.current_bid()
        builder.emit_br(raised, raise_id, -1, [], [])
        self._record_terminator()
        builder.start_block(hint="fin.raise", label_id=raise_lid)
        saved: int = builder.emit_val(MIR_EXCCUR, -1, -1, MIR_P)
        self.emit_body(finalbody)
        if not builder.terminated:
            builder.emit_raiseexc(saved)
        norm_id: int = len(builder.fn.blocks)
        self.patch_br_false(header_bid, norm_id)
        builder.start_block(hint="fin.norm", label_id=norm_lid)
        flow: int = builder.emit_val(MIR_FLOWLD, -1, -1, MIR_W)
        leave: int = builder.emit_val(MIR_TRYICMP, flow, builder.const_w(1), MIR_B)
        susp: int = builder.emit_val(MIR_TRYICMP, flow, builder.const_w(2), MIR_B)
        cont_lid: int = self.reserve_label()
        susp_lid: int = self.reserve_label()
        notret_lid: int = self.reserve_label()
        norm_br_bid: int = builder.current_bid()
        builder.emit_br(leave, -1, -1, [], [])
        self._record_terminator()
        notret_bid: int = builder.start_block(hint="fin.notret", label_id=notret_lid)
        builder.emit_br(susp, -1, -1, [], [])
        self._record_terminator()
        ret_bid: int = builder.start_block(hint="fin.ret", label_id=ret_lid)
        self.emit_body(finalbody)
        if not builder.terminated:
            self._emit_try_ret()
        susp_bid: int = builder.start_block(hint="fin.susp", label_id=susp_lid)
        self._emit_try_ret()
        cont_bid: int = builder.start_block(hint="fin.cont", label_id=cont_lid)
        self.emit_body(finalbody)
        cont_jmp_bid: int = -1
        if not builder.terminated:
            builder.emit_jmp(-1)
            self._record_terminator()
            cont_jmp_bid = builder.current_bid()
        merge_bid: int = len(builder.fn.blocks)
        # The forward edges land once every outcome block's id is final:
        # `leave` picks the propagated return, `susp` picks the propagated
        # suspend, and the continue arm jumps the merge.
        self.patch_br_true(norm_br_bid, ret_bid)
        self.patch_br_false(norm_br_bid, notret_bid)
        self.patch_br_true(notret_bid, susp_bid)
        self.patch_br_false(notret_bid, cont_bid)
        if cont_jmp_bid >= 0:
            self.patch_jmp(cont_jmp_bid, merge_bid)
        builder.start_block(hint="fin.merge", label_id=merge_lid)

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
        if kind == "contains":
            return self.emit_contains(expr)
        if kind == "unary":
            return self.emit_unary(expr)
        if kind == "call":
            return self.emit_call_expr(expr)
        if kind == "cast":
            return self.emit_cast(expr)
        if kind == "list":
            return self.emit_list(expr)
        if kind == "dict":
            return self.emit_dict(expr)
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
            class_name: str = callee["name"]
            if class_name.startswith("tuple_") and len(args) == 0:
                return self.emit_newobj(expr)
            return self.emit_initcall(expr)
        if ckind == "builtin":
            cname: str = callee["name"]
            if cname == "len" and len(args) == 1:
                arg0: Dict[str, Any] = args[0]
                arg0_type: Dict[str, Any] = arg0["type"]
                if _is_supported_list(arg0_type):
                    seq = self.emit_expr(arg0)
                    return self.builder.emit_val(MIR_SEQLEN, seq, -1, MIR_W)
                if _type_kind(arg0_type) == "dict":
                    # `len(d)` reads `ps_map_len`, the direct builtin arm.
                    mapping = self.emit_expr(arg0)
                    return self.builder.emit_val(MIR_MAP_LEN, mapping, -1, MIR_W)
        if ckind == "method":
            return self.emit_method_call(expr)
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
            if cmodule == "builtins" and mname == "format":
                return self.emit_format_call(expr)
            if cmodule == "builtins" and mname in _CONTAINER_REPR_CALLS:
                return self.emit_container_repr(expr)
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
        if ckind == "builtin" and name in _BUILTIN_CALLS:
            return self.emit_builtin_call(expr)
        if ckind == "module" and cmodule == "builtins" and name in _MODULE_CALLS:
            return self.emit_module_call(expr)
        target: Dict[str, Any] | None = self.callees.get(name)
        if target is None:
            module_fns = self.module_fns
            if module_fns is None:
                raise MirLowerError(f"the MIR leaf slice has no lowered callee {name}")
            if name in module_fns:
                raise MirLowerError(f"the MIR leaf slice has no lowered callee {name}")
        # A `target` of None here is the admitted non-module callee: a
        # call to a name no function of this module defines (a fenced
        # sibling the IR still references). The module-level walk
        # admitted it because there is no callee record to check; the
        # MIR records the call and a fenced module never reaches
        # emission, so the result tag comes from the expression's own
        # type instead of the callee record.
        callee_vid: int = self.builder.add_callee(name)
        values: list[int] = []
        for arg in args:
            values.append(self.emit_expr(arg))
        typ: Dict[str, Any] = expr["type"]
        tkind: str = typ["kind"]
        if tkind == "none":
            self.builder.emit_call(callee_vid, values, MIR_VOID)
            return -1
        tag: int = _param_tag(tkind)
        if target is not None:
            tag = _fn_result_tag(target)
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

    def emit_initcall(self, expr: Dict[str, Any]) -> int:
        """A user-class constructor call: `MIR_INITCALL`.

        The class name interns as a `MIR_S` constant the emitter reads
        without materializing (the `MIR_NEWOBJ` convention); the
        constructor arguments evaluate left to right and ride
        `call_args`, the `MIR_CALL` channel. The emitter allocates the
        `%cls.<name>` struct and runs `cls.<name>.__init__` when the
        class defines one, the direct `emit_constructor`'s shape.
        """
        callee: Dict[str, Any] = expr["callee"]
        name: str = callee["name"]
        args: list[Dict[str, Any]] = expr["args"]
        values: list[int] = []
        for arg in args:
            values.append(self.emit_expr(arg))
        vid: int = self.builder.emit_val(MIR_INITCALL, self.builder.const_s(name), -1, MIR_P)
        self.builder.fn.call_args[len(self.builder.fn.ops) - 1] = values
        return vid

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

    def emit_dict(self, expr: Dict[str, Any]) -> int:
        """A dict literal: evaluate pairs, `ps_map_new`, then `MIR_MAP_PUT`s."""
        pairs: list[Dict[str, Any]] = expr["pairs"]
        keys: list[int] = []
        vals: list[int] = []
        for pair in pairs:
            key_node: Dict[str, Any] = pair["key"]
            val_node: Dict[str, Any] = pair["value"]
            key = self.emit_expr(key_node)
            val = self.emit_expr(val_node)
            if _is_str_const(key_node):
                key = self.builder.emit_copy(key, MIR_S)
            if _is_str_const(val_node):
                val = self.builder.emit_copy(val, MIR_S)
            keys.append(key)
            vals.append(val)
        typ: Dict[str, Any] = expr["type"]
        key_t: Dict[str, Any] = typ["key"]
        val_t: Dict[str, Any] = typ["value"]
        key_kind: int = 1 if _type_kind(key_t) == "int" else 2
        reg: int = self._map_new(key_kind, _elem_size(key_t), _elem_size(val_t), len(keys))
        for index in range(len(keys)):
            self.builder.emit(MIR_MAP_PUT, -1, reg, keys[index], vals[index])
        return reg

    def _map_new(self, key_kind: int, key_size: int, val_size: int, count: int) -> int:
        callee = self.builder.add_callee("rt:ps_map_new")
        args = [
            self.builder.const_w(key_kind),
            self.builder.const_w(key_size),
            self.builder.const_w(val_size),
            self.builder.const_w(count),
        ]
        return self.builder.emit_call(callee, args, MIR_P)

    def emit_subscript(self, expr: Dict[str, Any]) -> int:
        """A subscript load: container then index.

        A list load is `MIR_SEQGET` with the element tag in `arg3` (the
        direct path's `ps_seq_get` + cell reload); a dict load is
        `MIR_MAP_GET` the same way (`ps_map_get`). A str load is the
        direct path's `ps_str_get` buffer-out call, carried as a marked
        `MIR_CALL` (`strget`) since its argument order — buffer parts of
        the string, then the index — is the abi walk's, not the seq
        op's.
        """
        value_node: Dict[str, Any] = expr["value"]
        vkind: str = _type_kind(value_node["type"])
        if vkind == "str":
            seq = self.emit_expr(value_node)
            index = self.emit_expr(expr["index"])
            callee_vid: int = self.builder.add_callee("strget")
            dst: int = self.builder.emit_call(callee_vid, [seq, index], MIR_S)
            self.builder.fn.helpers[len(self.builder.fn.ops) - 1] = "strget"
            return dst
        value = self.emit_expr(value_node)
        index = self.emit_expr(expr["index"])
        result_type: Dict[str, Any] = expr["type"]
        rkind: str = result_type["kind"]
        tag: int = _param_tag(rkind)
        if _type_kind(expr["value"]["type"]) == "dict":
            return self.builder.emit_val3(MIR_MAP_GET, value, index, tag, tag)
        return self.builder.emit_val3(MIR_SEQGET, value, index, tag, tag)

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

    def emit_builtin_call(self, expr: Dict[str, Any]) -> int:
        """An admitted builtin call (`len`/`str`/`int`): a marked `MIR_CALL`.

        The callee-table name is the builtin's, the same out-of-band marker
        convention `print` uses: a user function named `len` shares the
        table, so the emitter reads `helpers` to pick the builtin arm.
        `str` of a str argument is the identity — the direct path returns
        the value unchanged — so the lowerer binds the argument itself
        and emits nothing.
        """
        callee: Dict[str, Any] = expr["callee"]
        name: str = callee["name"]
        args: list[Dict[str, Any]] = expr["args"]
        arg: Dict[str, Any] = args[0]
        atyp: Dict[str, Any] = arg["type"]
        akind: str = atyp["kind"]
        value: int = self.emit_expr(arg)
        if name == "str" and akind == "str":
            return value
        etype: Dict[str, Any] = expr["type"]
        tkind: str = etype["kind"]
        tag: int = MIR_S if tkind == "str" else MIR_W
        callee_vid: int = self.builder.add_callee(name)
        dst: int = self.builder.emit_call(callee_vid, [value], tag)
        self.builder.fn.helpers[len(self.builder.fn.ops) - 1] = f"builtin:{name}"
        return dst

    def emit_format_call(self, expr: Dict[str, Any]) -> int:
        """An admitted `builtins.format` call: a marked `MIR_CALL`.

        The arguments evaluate left to right (the format string first);
        the marker carries the `ps_str_format` style code since the MIR
        call record has no expr payload for `format_style`.
        """
        style: str = "percent"
        if "format_style" in expr:
            style = _opt_str(expr, "format_style")
        style_code: int = 1 if style == "dot" else 0
        args: list[Dict[str, Any]] = expr["args"]
        values: list[int] = []
        for arg in args:
            values.append(self.emit_expr(arg))
        callee_vid: int = self.builder.add_callee("format")
        dst: int = self.builder.emit_call(callee_vid, values, MIR_S)
        self.builder.fn.helpers[len(self.builder.fn.ops) - 1] = f"format:{style_code}"
        return dst

    def emit_method_call(self, expr: Dict[str, Any]) -> int:
        """An admitted method call (`str.join`, `str.split`, `str.rsplit`):
        a marked `MIR_CALL`.

        The arguments evaluate left to right; the callee name is the
        receiver-qualified spelling (`str.join`, `str.split`) and the
        `method:` helper marker tells the emitter apart from a user
        function of the same spelling, the same out-of-band convention
        `builtin:`/`abi ` use. `join` returns a str (`MIR_S`); the split
        family returns `list[str]` (`MIR_P`).
        """
        callee: Dict[str, Any] = expr["callee"]
        name: str = callee["name"]
        args: list[Dict[str, Any]] = expr["args"]
        values: list[int] = []
        for arg in args:
            values.append(self.emit_expr(arg))
        if name == "join":
            callee_name: str = "str.join"
            tag: int = MIR_S
        else:
            callee_name = "str." + name
            tag = MIR_P
        callee_vid: int = self.builder.add_callee(callee_name)
        dst: int = self.builder.emit_call(callee_vid, values, tag)
        self.builder.fn.helpers[len(self.builder.fn.ops) - 1] = "method:" + name
        return dst

    def emit_container_repr(self, expr: Dict[str, Any]) -> int:
        """A `print` container repr call: a marked `MIR_CALL`.

        The container and the kind-tag constants evaluate left to right;
        the `abi ` marker carries the `ps_*_repr` symbol, the same
        convention `emit_module_call` uses for the `bin`/`hex`/`oct`
        forms, and the result is the str the `_buf_out` ABI produces.
        """
        callee: Dict[str, Any] = expr["callee"]
        name: str = callee["name"]
        symbol: str = expr["symbol"]
        args: list[Dict[str, Any]] = expr["args"]
        values: list[int] = []
        for arg in args:
            values.append(self.emit_expr(arg))
        callee_vid: int = self.builder.add_callee(name)
        dst: int = self.builder.emit_call(callee_vid, values, MIR_S)
        self.builder.fn.helpers[len(self.builder.fn.ops) - 1] = f"abi {symbol}"
        return dst

    def emit_module_call(self, expr: Dict[str, Any]) -> int:
        """An admitted `builtins` module call: a marked `MIR_CALL`.

        These calls carry the runtime symbol on the IR record and lower
        through the direct emitter's generic `_emit_abi` walk, so the
        marker carries the symbol (`abi <symbol>`) and the result tag is
        the call's return kind (`MIR_S` for the str-returning forms,
        `MIR_W` for `int`).
        """
        callee: Dict[str, Any] = expr["callee"]
        name: str = callee["name"]
        symbol: str = expr["symbol"]
        args: list[Dict[str, Any]] = expr["args"]
        arg: Dict[str, Any] = args[0]
        value: int = self.emit_expr(arg)
        etype: Dict[str, Any] = expr["type"]
        tkind: str = etype["kind"]
        tag: int = MIR_S if tkind == "str" else MIR_W
        callee_vid: int = self.builder.add_callee(name)
        dst: int = self.builder.emit_call(callee_vid, [value], tag)
        self.builder.fn.helpers[len(self.builder.fn.ops) - 1] = f"abi {symbol}"
        return dst

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
        if tkind == "str":
            # `+` is the only admitted str binary: the direct path's
            # `helper_call("strconcat", ...)` spelling rides the same
            # helper-copy convention.
            return self.emit_helper("strconcat", MIR_S, left, right)
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
            # compares it to 0 with `icmp`. The helper copy's name
            # carries the predicate to the emitter, which renders the
            # extractvalue/call/icmp sequence the direct path emits.
            helper = "str_cmp_" + _STR_CMPS[okey]
            return self.emit_helper(helper, MIR_B, left, right)
        table: Dict[str, int] = _D_CMPOPS if kind == "float" else _W_CMPOPS
        mir_op: int = table[okey]
        return self.builder.emit_val(mir_op, left, right, MIR_B)

    def emit_contains(self, expr: Dict[str, Any]) -> int:
        """A str `in`/`not in`: a marked helper `MIR_COPY`.

        The direct path evaluates the needle before the haystack, then
        emits the `_buf_parts` extracts, the `ps_str_find` call and the
        `icmp sge`; `not in` adds the `xor i1` tail. The helper copy's
        operands carry (hay, needle) and its name carries the negation,
        the same out-of-band convention the compare helpers use.
        """
        needle: int = self.emit_expr(expr["needle"])
        hay: int = self.emit_expr(expr["hay"])
        negated: bool = expr["negated"]
        if _type_kind(expr["hay"]["type"]) == "dict":
            # A dict membership test: `MIR_MAP_HAS` reads (hay, needle)
            # and produces the bool flag; `not in` folds through
            # `MIR_NOTB`, the direct path's `xor i1` tail.
            return self._emit_map_contains(hay, needle, negated)
        helper: str = "str_ncontains" if negated else "str_contains"
        return self.emit_helper(helper, MIR_B, hay, needle)

    def _emit_map_contains(self, hay: int, needle: int, negated: bool) -> int:
        """A dict `in`/`not in`: `MIR_MAP_HAS` plus the `not` fold.

        The direct path emits `ps_map_has` then `icmp ne` for `in`, and
        an `xor i1` for `not in`; the MIR keeps the same shape — the op
        produces the un-negated flag and a `MIR_NOTB` folds the
        negation, rendering the same `xor i1 flag, true` line."""
        flag: int = self.builder.emit_val(MIR_MAP_HAS, hay, needle, MIR_B)
        if not negated:
            return flag
        return self.builder.emit_val(MIR_NOTB, flag, -1, MIR_B)

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
        if op == "-" and tkind == "float":
            return self.builder.emit_val(MIR_NEGD, operand, -1, MIR_D)
        raise MirLowerError(f"the MIR leaf slice cannot lower {op} on {tkind}")


def lower_function(
    fn: Dict[str, Any],
    callees: Dict[str, Dict[str, Any]] | None = None,
    outline_base: int = 0,
    module_fns: Dict[str, Dict[str, Any]] | None = None,
) -> MirFunction:
    """Lower one in-slice IR function to MIR. `supported_fn` must hold.

    `callees` is the admitted callee table (name -> IR function record);
    a call lowers to a `MIR_CALL` only when its name is in it, so a
    function with no calls needs no table. `module_fns` is the module's
    full function-name table when the caller has one: a call naming a
    name outside it is the admitted non-module callee. `outline_base`
    seeds the `ps.try.N` counter with the outlines earlier module
    functions consumed.
    """
    table: Dict[str, Dict[str, Any]] = {}
    if callees is not None:
        table = callees
    return _FnLowerer(fn, table, outline_base, module_fns).run()


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
    module_fns: Dict[str, Dict[str, Any]] = {}
    for fn in fns:
        fname: str = fn["name"]
        if fname not in module_fns:
            module_fns[fname] = fn
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
            if _calls_in_slice(fn["body"], admitted, module_fns):
                kept[fname] = fn
            else:
                settling = True
        admitted = kept
    functions: list[MirFunction] = []
    skipped: list[str] = []
    # The `ps.try.N` counter runs across the whole module in emission
    # order: every function's body contributes its outline cost before
    # the next function's first try names its symbol.
    outline_base: int = 0
    for fn in fns:
        fname2: str = fn["name"]
        fn_admitted: Dict[str, Any] | None = admitted.get(fname2)
        if fn_admitted is not None and supported_module_fn(fn, admitted, module_fns):
            functions.append(_FnLowerer(fn, admitted, outline_base, module_fns).run())
        else:
            skipped.append(fname2)
        outline_base = outline_base + _mir_list_cost(_mir_items(fn, "body"))
    source: str = module["sourceFile"]
    entry: str = module["entry"]
    mir_module: MirModule = MirModule(source_file=source, functions=functions, entry=entry)
    mir_module.skipped = skipped
    return mir_module
