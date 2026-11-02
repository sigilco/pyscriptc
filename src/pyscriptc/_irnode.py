"""The typed HIR node (roadmap R3 slice 1).

Concrete, non-inheriting dataclasses: a `kind` tag, a `flags: int` bitmask
instead of bool fields, typed child/payload fields, and a per-kind tag+str
payload for the heterogeneous const (`value` is str/int-text/bool/none
across kinds, which the subset cannot express as one field type; see the
irtype note, probe 2).

Two rules this module follows that the irtype probes established:

- A dataclass default must be a constant, so `X | None = None` fences
  (PS2002). Every child/payload reference is declared FIRST without a
  default; the defaulted tail is str/int scalars and the flag word only.
  Construction goes through the per-kind factories at the bottom, which
  pass the unused references as None, so call sites never see the prefix.
- A dict value is strictly type-checked (`dict value is X, annotation
  says Y`), so no lowered annotation describes the heterogeneous
  serializer dict, and `as_dict()` cannot exist here. The dict
  reconstruction lives in `tirserde.py` (milestone two), which already
  speaks `Any`; this module stays pure data and compiles 0 fences.

The shape is the dict IR the emitter reads today, node for node: the
serializer reproduces the exact dict, key for key, so the canonical
payload never sees the dataclasses and the frozen gates stay blind to
the migration (contract: .agents/docs/spec/ir-canonicity.md).

This module is the one compiler-owned module the R3 strangler migrates
through (recorded decision 2026-11-02): sibling modules annotate these
classes across module boundaries without PS1106/PS1104 once the slice
lands.
"""

from __future__ import annotations

from dataclasses import dataclass

# flags bits, one per bool the dict IR carries. A node whose bit is set
# emits the key with its true spelling in the serializer.
F_NEGATED = 1
F_SET = 2
F_IDENTITY = 4
F_RERAISE = 8
F_BARE = 16
F_FROZEN = 32

# type-dict flags.
F_TOPT = 1
F_TSET = 2

# const payload tags. The dict IR stores the JSON value directly (True,
# None, a decimal str for int, a repr str for float); the typed node
# carries the tag plus the str text and the serializer restores the exact
# JSON value.
P_STR = 0
P_INT = 1
P_FLOAT = 2
P_BOOL = 3
P_NONE = 4
P_BYTES = 5

# The serializer-level sentinel is -1, passed by the factories at call
# sites (a default must be a constant, and a unary minus is not one).


@dataclass
class Span:
    """The `span` record: file plus the 1-based source range."""

    file: str
    line: int
    col: int
    endLine: int
    endCol: int


@dataclass
class T:
    """A type dict: the `kind` tag plus the shape the kind carries.

    Leaf kinds (`str`, `int`, `float`, `bool`, `bytes`, `none`, `buf`,
    `exc`, `typevar`) carry nothing; `class` carries `name` and, for the
    `X | None` spelling, `label`/`elem`/the optional bit; `list` carries
    `elem` and the set bit; `dict` carries `key`/`value`; `func` carries
    `params`/`return`. Build through the `t_*` helpers.
    """

    kind: str
    elem: T | None
    key: T | None
    value: T | None
    params: list[T] | None
    ret: T | None
    name: str = ""
    label: str = ""
    flags: int = 0


@dataclass
class Pair:
    """One dict-literal entry: `key` and `value` expressions."""

    key: IrNode
    value: IrNode


@dataclass
class EnvItem:
    """One closure env entry: a local to pack (`name`) or a value to box
    (`init`); `type` is the entry's type either way."""

    init: IrNode | None
    type: T
    name: str = ""


@dataclass
class Stub:
    """The `functools.partial`/thread shim spec: the bound types, the
    remaining params, the return."""

    bound: list[T]
    params: list[T]
    ret: T


@dataclass
class ThreadMeta:
    """The `thread` record a Thread/to_thread call rides.

    Thread construction carries the worker pair (`daemon`, `name`,
    `closure`); to_thread carries the result type and slot size
    (`result`, `size`). The serializer emits only the present keys.
    """

    closure: IrNode | None
    result: T | None
    size: int = 0
    name: str = ""
    daemon: int = 0


@dataclass
class Handler:
    """One except handler: the matched exception names, their codes, the
    body, and the optional bind/slot/bare spellings."""

    exc: list[str]
    codes: list[int]
    body: list[IrNode]
    excslot: str = ""
    bind: str = ""
    flags: int = 0


@dataclass
class IrNode:
    """One typed HIR node: the `kind` tag plus the fields the kind carries.

    The no-default prefix is every child/payload reference (typed `Span`,
    `T`, `IrNode`, and the small records); the defaulted tail is the str /
    int scalars and the flag word. Construct through the per-kind
    factories, which pass the unused references as None.
    """

    kind: str
    span: Span | None
    type: T | None
    value: IrNode | None
    object: IrNode | None
    callee: IrNode | None
    args: list[IrNode] | None
    env: list[EnvItem] | None
    stub: Stub | None
    thread_stub: Stub | None
    thread: ThreadMeta | None
    operand: IrNode | None
    left: IrNode | None
    right: IrNode | None
    hay: IrNode | None
    needle: IrNode | None
    elems: list[IrNode] | None
    pairs: list[Pair] | None
    index: IrNode | None
    cond: IrNode | None
    then: list[IrNode] | None
    else_: list[IrNode] | None
    body: list[IrNode] | None
    orelse: list[IrNode] | None
    finalbody: list[IrNode] | None
    handlers: list[Handler] | None
    expr: IrNode | None
    message: IrNode | None
    cause: IrNode | None
    target_node: IrNode | None
    id: str = ""
    name: str = ""
    op: str = ""
    target: str = ""
    exc: str = ""
    code: int = 0
    excslot: str = ""
    symbol: str = ""
    format_style: str = ""
    format_kwnames: int = 0
    receiver: str = ""
    tuple_: str = ""
    module: str = ""
    label: str = ""
    brk: str = ""
    cont: str = ""
    ptag: int = P_STR
    svalue: str = ""
    flags: int = 0


# --- type helpers -------------------------------------------------------


def t_leaf(kind: str) -> T:
    """A leaf type dict: `str`, `int`, `float`, `bool`, `bytes`, `none`,
    `buf`, `exc`, `typevar`."""
    return T(kind=kind, elem=None, key=None, value=None, params=None, ret=None)


def t_class(name: str, label: str = "", elem: T | None = None, optional: bool = False) -> T:
    """A `class` type dict; `optional` spells the `X | None` bit."""
    flags = F_TOPT if optional else 0
    return T(kind="class", elem=elem, key=None, value=None, params=None, ret=None, name=name, label=label, flags=flags)


def t_list(elem: T, set_: bool = False) -> T:
    """A `list` type dict; `set_` spells the set-backed list bit."""
    flags = F_TSET if set_ else 0
    return T(kind="list", elem=elem, key=None, value=None, params=None, ret=None, flags=flags)


def t_dict(key: T, value: T) -> T:
    """A `dict` type dict."""
    return T(kind="dict", elem=None, key=key, value=value, params=None, ret=None)


def t_func(params: list[T], ret: T) -> T:
    """A `func` type dict: the param types plus the return."""
    return T(kind="func", elem=None, key=None, value=None, params=params, ret=ret)


# --- node factories -----------------------------------------------------
# Each factory fills the no-default reference fields the kind does not
# carry with None, so call sites name only what the kind carries.


def _nil() -> list[IrNode]:
    return []


def assign(target: str, value: IrNode, span: Span) -> IrNode:
    return IrNode(
        kind="assign",
        span=span,
        type=None,
        value=value,
        object=None,
        callee=None,
        args=None,
        env=None,
        stub=None,
        thread_stub=None,
        thread=None,
        operand=None,
        left=None,
        right=None,
        hay=None,
        needle=None,
        elems=None,
        pairs=None,
        index=None,
        cond=None,
        then=None,
        else_=None,
        body=None,
        orelse=None,
        finalbody=None,
        handlers=None,
        expr=None,
        message=None,
        cause=None,
        target_node=None,
        target=target,
    )


def attr(name: str, typ: T, value: IrNode, span: Span) -> IrNode:
    return IrNode(
        kind="attr",
        span=span,
        type=typ,
        value=value,
        object=None,
        callee=None,
        args=None,
        env=None,
        stub=None,
        thread_stub=None,
        thread=None,
        operand=None,
        left=None,
        right=None,
        hay=None,
        needle=None,
        elems=None,
        pairs=None,
        index=None,
        cond=None,
        then=None,
        else_=None,
        body=None,
        orelse=None,
        finalbody=None,
        handlers=None,
        expr=None,
        message=None,
        cause=None,
        target_node=None,
        name=name,
    )


def binary(op: str, left: IrNode, right: IrNode, typ: T, span: Span) -> IrNode:
    return IrNode(
        kind="binary",
        span=span,
        type=typ,
        value=None,
        object=None,
        callee=None,
        args=None,
        env=None,
        stub=None,
        thread_stub=None,
        thread=None,
        operand=None,
        left=left,
        right=right,
        hay=None,
        needle=None,
        elems=None,
        pairs=None,
        index=None,
        cond=None,
        then=None,
        else_=None,
        body=None,
        orelse=None,
        finalbody=None,
        handlers=None,
        expr=None,
        message=None,
        cause=None,
        target_node=None,
        op=op,
    )


def builtin(name: str) -> IrNode:
    return IrNode(
        kind="builtin",
        span=None,
        type=None,
        value=None,
        object=None,
        callee=None,
        args=None,
        env=None,
        stub=None,
        thread_stub=None,
        thread=None,
        operand=None,
        left=None,
        right=None,
        hay=None,
        needle=None,
        elems=None,
        pairs=None,
        index=None,
        cond=None,
        then=None,
        else_=None,
        body=None,
        orelse=None,
        finalbody=None,
        handlers=None,
        expr=None,
        message=None,
        cause=None,
        target_node=None,
        name=name,
    )


def call(
    callee: IrNode,
    args: list[IrNode],
    typ: T,
    span: Span,
    symbol: str,
    format_style: str,
    format_kwnames: int,
    thread: ThreadMeta | None = None,
    identity: bool = False,
) -> IrNode:
    flags = F_IDENTITY if identity else 0
    return IrNode(
        kind="call",
        span=span,
        type=typ,
        value=None,
        object=None,
        callee=callee,
        args=args,
        env=None,
        stub=None,
        thread_stub=None,
        thread=thread,
        operand=None,
        left=None,
        right=None,
        hay=None,
        needle=None,
        elems=None,
        pairs=None,
        index=None,
        cond=None,
        then=None,
        else_=None,
        body=None,
        orelse=None,
        finalbody=None,
        handlers=None,
        expr=None,
        message=None,
        cause=None,
        target_node=None,
        symbol=symbol,
        format_style=format_style,
        format_kwnames=format_kwnames,
        flags=flags,
    )


def cast(op: str, value: IrNode, typ: T, span: Span) -> IrNode:
    return IrNode(
        kind="cast",
        span=span,
        type=typ,
        value=value,
        object=None,
        callee=None,
        args=None,
        env=None,
        stub=None,
        thread_stub=None,
        thread=None,
        operand=None,
        left=None,
        right=None,
        hay=None,
        needle=None,
        elems=None,
        pairs=None,
        index=None,
        cond=None,
        then=None,
        else_=None,
        body=None,
        orelse=None,
        finalbody=None,
        handlers=None,
        expr=None,
        message=None,
        cause=None,
        target_node=None,
        op=op,
    )


def class_callee(name: str) -> IrNode:
    """The `class` callee: `Name(...)` on a lowered class."""
    return IrNode(
        kind="class",
        span=None,
        type=None,
        value=None,
        object=None,
        callee=None,
        args=None,
        env=None,
        stub=None,
        thread_stub=None,
        thread=None,
        operand=None,
        left=None,
        right=None,
        hay=None,
        needle=None,
        elems=None,
        pairs=None,
        index=None,
        cond=None,
        then=None,
        else_=None,
        body=None,
        orelse=None,
        finalbody=None,
        handlers=None,
        expr=None,
        message=None,
        cause=None,
        target_node=None,
        name=name,
    )


def closure(
    name: str,
    env: list[EnvItem],
    typ: T,
    span: Span,
    stub: Stub | None = None,
    thread_stub: Stub | None = None,
) -> IrNode:
    return IrNode(
        kind="closure",
        span=span,
        type=typ,
        value=None,
        object=None,
        callee=None,
        args=None,
        env=env,
        stub=stub,
        thread_stub=thread_stub,
        thread=None,
        operand=None,
        left=None,
        right=None,
        hay=None,
        needle=None,
        elems=None,
        pairs=None,
        index=None,
        cond=None,
        then=None,
        else_=None,
        body=None,
        orelse=None,
        finalbody=None,
        handlers=None,
        expr=None,
        message=None,
        cause=None,
        target_node=None,
        name=name,
    )


def compare(op: str, left: IrNode, right: IrNode, typ: T, span: Span) -> IrNode:
    return IrNode(
        kind="compare",
        span=span,
        type=typ,
        value=None,
        object=None,
        callee=None,
        args=None,
        env=None,
        stub=None,
        thread_stub=None,
        thread=None,
        operand=None,
        left=left,
        right=right,
        hay=None,
        needle=None,
        elems=None,
        pairs=None,
        index=None,
        cond=None,
        then=None,
        else_=None,
        body=None,
        orelse=None,
        finalbody=None,
        handlers=None,
        expr=None,
        message=None,
        cause=None,
        target_node=None,
        op=op,
    )


def const_str(svalue: str, typ: T, span: Span) -> IrNode:
    return IrNode(
        kind="const",
        span=span,
        type=typ,
        value=None,
        object=None,
        callee=None,
        args=None,
        env=None,
        stub=None,
        thread_stub=None,
        thread=None,
        operand=None,
        left=None,
        right=None,
        hay=None,
        needle=None,
        elems=None,
        pairs=None,
        index=None,
        cond=None,
        then=None,
        else_=None,
        body=None,
        orelse=None,
        finalbody=None,
        handlers=None,
        expr=None,
        message=None,
        cause=None,
        target_node=None,
        ptag=P_STR,
        svalue=svalue,
    )


def const_node(ptag: int, svalue: str, typ: T, span: Span) -> IrNode:
    """The const node with an explicit payload tag (int/float/bool/none)."""
    return IrNode(
        kind="const",
        span=span,
        type=typ,
        value=None,
        object=None,
        callee=None,
        args=None,
        env=None,
        stub=None,
        thread_stub=None,
        thread=None,
        operand=None,
        left=None,
        right=None,
        hay=None,
        needle=None,
        elems=None,
        pairs=None,
        index=None,
        cond=None,
        then=None,
        else_=None,
        body=None,
        orelse=None,
        finalbody=None,
        handlers=None,
        expr=None,
        message=None,
        cause=None,
        target_node=None,
        ptag=ptag,
        svalue=svalue,
    )


def contains(hay: IrNode, needle: IrNode, typ: T, span: Span, negated: bool = False) -> IrNode:
    flags = F_NEGATED if negated else 0
    return IrNode(
        kind="contains",
        span=span,
        type=typ,
        value=None,
        object=None,
        callee=None,
        args=None,
        env=None,
        stub=None,
        thread_stub=None,
        thread=None,
        operand=None,
        left=None,
        right=None,
        hay=hay,
        needle=needle,
        elems=None,
        pairs=None,
        index=None,
        cond=None,
        then=None,
        else_=None,
        body=None,
        orelse=None,
        finalbody=None,
        handlers=None,
        expr=None,
        message=None,
        cause=None,
        target_node=None,
        flags=flags,
    )


def dict_lit(pairs: list[Pair], typ: T, span: Span) -> IrNode:
    return IrNode(
        kind="dict",
        span=span,
        type=typ,
        value=None,
        object=None,
        callee=None,
        args=None,
        env=None,
        stub=None,
        thread_stub=None,
        thread=None,
        operand=None,
        left=None,
        right=None,
        hay=None,
        needle=None,
        elems=None,
        pairs=pairs,
        index=None,
        cond=None,
        then=None,
        else_=None,
        body=None,
        orelse=None,
        finalbody=None,
        handlers=None,
        expr=None,
        message=None,
        cause=None,
        target_node=None,
    )


def dict_type(key: T, value: T) -> IrNode:
    """The type-only `dict` node: `{"kind": "dict", "key": ..., "value": ...}`."""
    return IrNode(
        kind="dict",
        span=None,
        type=None,
        value=None,
        object=None,
        callee=None,
        args=None,
        env=None,
        stub=None,
        thread_stub=None,
        thread=None,
        operand=None,
        left=None,
        right=None,
        hay=None,
        needle=None,
        elems=None,
        pairs=None,
        index=None,
        cond=None,
        then=None,
        else_=None,
        body=None,
        orelse=None,
        finalbody=None,
        handlers=None,
        expr=None,
        message=None,
        cause=None,
        target_node=None,
    )


def excmake(exc: str, span: Span, code: int, message: IrNode | None = None) -> IrNode:
    """The `raise E from cause` cause record."""
    return IrNode(
        kind="excmake",
        span=span,
        type=None,
        value=None,
        object=None,
        callee=None,
        args=None,
        env=None,
        stub=None,
        thread_stub=None,
        thread=None,
        operand=None,
        left=None,
        right=None,
        hay=None,
        needle=None,
        elems=None,
        pairs=None,
        index=None,
        cond=None,
        then=None,
        else_=None,
        body=None,
        orelse=None,
        finalbody=None,
        handlers=None,
        expr=None,
        message=message,
        cause=None,
        target_node=None,
        exc=exc,
        code=code,
    )


def expr_stmt(expr: IrNode, span: Span) -> IrNode:
    return IrNode(
        kind="expr",
        span=span,
        type=None,
        value=None,
        object=None,
        callee=None,
        args=None,
        env=None,
        stub=None,
        thread_stub=None,
        thread=None,
        operand=None,
        left=None,
        right=None,
        hay=None,
        needle=None,
        elems=None,
        pairs=None,
        index=None,
        cond=None,
        then=None,
        else_=None,
        body=None,
        orelse=None,
        finalbody=None,
        handlers=None,
        expr=expr,
        message=None,
        cause=None,
        target_node=None,
    )


def function_callee(name: str) -> IrNode:
    """The `function` callee: a same-module function by name."""
    return IrNode(
        kind="function",
        span=None,
        type=None,
        value=None,
        object=None,
        callee=None,
        args=None,
        env=None,
        stub=None,
        thread_stub=None,
        thread=None,
        operand=None,
        left=None,
        right=None,
        hay=None,
        needle=None,
        elems=None,
        pairs=None,
        index=None,
        cond=None,
        then=None,
        else_=None,
        body=None,
        orelse=None,
        finalbody=None,
        handlers=None,
        expr=None,
        message=None,
        cause=None,
        target_node=None,
        name=name,
    )


def if_stmt(cond: IrNode, then: list[IrNode], else_: list[IrNode], span: Span) -> IrNode:
    return IrNode(
        kind="if",
        span=span,
        type=None,
        value=None,
        object=None,
        callee=None,
        args=None,
        env=None,
        stub=None,
        thread_stub=None,
        thread=None,
        operand=None,
        left=None,
        right=None,
        hay=None,
        needle=None,
        elems=None,
        pairs=None,
        index=None,
        cond=cond,
        then=then,
        else_=else_,
        body=None,
        orelse=None,
        finalbody=None,
        handlers=None,
        expr=None,
        message=None,
        cause=None,
        target_node=None,
    )


def list_lit(elems: list[IrNode], typ: T, span: Span, frozen: bool = False) -> IrNode:
    flags = F_FROZEN if frozen else 0
    return IrNode(
        kind="list",
        span=span,
        type=typ,
        value=None,
        object=None,
        callee=None,
        args=None,
        env=None,
        stub=None,
        thread_stub=None,
        thread=None,
        operand=None,
        left=None,
        right=None,
        hay=None,
        needle=None,
        elems=elems,
        pairs=None,
        index=None,
        cond=None,
        then=None,
        else_=None,
        body=None,
        orelse=None,
        finalbody=None,
        handlers=None,
        expr=None,
        message=None,
        cause=None,
        target_node=None,
        flags=flags,
    )


def list_type(elem: T) -> IrNode:
    """The type-only `list` node: `{"kind": "list", "elem": ...}`."""
    return IrNode(
        kind="list",
        span=None,
        type=None,
        value=None,
        object=None,
        callee=None,
        args=None,
        env=None,
        stub=None,
        thread_stub=None,
        thread=None,
        operand=None,
        left=None,
        right=None,
        hay=None,
        needle=None,
        elems=None,
        pairs=None,
        index=None,
        cond=None,
        then=None,
        else_=None,
        body=None,
        orelse=None,
        finalbody=None,
        handlers=None,
        expr=None,
        message=None,
        cause=None,
        target_node=None,
    )


def method_callee(receiver: str, name: str, tuple_: str = "") -> IrNode:
    """The `method` callee: a receiver-kind attribute call."""
    return IrNode(
        kind="method",
        span=None,
        type=None,
        value=None,
        object=None,
        callee=None,
        args=None,
        env=None,
        stub=None,
        thread_stub=None,
        thread=None,
        operand=None,
        left=None,
        right=None,
        hay=None,
        needle=None,
        elems=None,
        pairs=None,
        index=None,
        cond=None,
        then=None,
        else_=None,
        body=None,
        orelse=None,
        finalbody=None,
        handlers=None,
        expr=None,
        message=None,
        cause=None,
        target_node=None,
        name=name,
        receiver=receiver,
        tuple_=tuple_,
    )


def module_callee(module: str, name: str) -> IrNode:
    """The `module` callee: a lowered-stdlib attribute call."""
    return IrNode(
        kind="module",
        span=None,
        type=None,
        value=None,
        object=None,
        callee=None,
        args=None,
        env=None,
        stub=None,
        thread_stub=None,
        thread=None,
        operand=None,
        left=None,
        right=None,
        hay=None,
        needle=None,
        elems=None,
        pairs=None,
        index=None,
        cond=None,
        then=None,
        else_=None,
        body=None,
        orelse=None,
        finalbody=None,
        handlers=None,
        expr=None,
        message=None,
        cause=None,
        target_node=None,
        name=name,
        module=module,
    )


def name_expr(id_: str, typ: T, span: Span) -> IrNode:
    return IrNode(
        kind="name",
        span=span,
        type=typ,
        value=None,
        object=None,
        callee=None,
        args=None,
        env=None,
        stub=None,
        thread_stub=None,
        thread=None,
        operand=None,
        left=None,
        right=None,
        hay=None,
        needle=None,
        elems=None,
        pairs=None,
        index=None,
        cond=None,
        then=None,
        else_=None,
        body=None,
        orelse=None,
        finalbody=None,
        handlers=None,
        expr=None,
        message=None,
        cause=None,
        target_node=None,
        id=id_,
    )


def raise_stmt(
    exc: str,
    span: Span,
    code: int,
    message: IrNode | None = None,
    reraise: bool = False,
    excslot: str = "",
    cause: IrNode | None = None,
) -> IrNode:
    flags = F_RERAISE if reraise else 0
    return IrNode(
        kind="raise",
        span=span,
        type=None,
        value=None,
        object=None,
        callee=None,
        args=None,
        env=None,
        stub=None,
        thread_stub=None,
        thread=None,
        operand=None,
        left=None,
        right=None,
        hay=None,
        needle=None,
        elems=None,
        pairs=None,
        index=None,
        cond=None,
        then=None,
        else_=None,
        body=None,
        orelse=None,
        finalbody=None,
        handlers=None,
        expr=None,
        message=message,
        cause=cause,
        target_node=None,
        exc=exc,
        code=code,
        excslot=excslot,
        flags=flags,
    )


def return_stmt(value: IrNode | None, span: Span) -> IrNode:
    return IrNode(
        kind="return",
        span=span,
        type=None,
        value=value,
        object=None,
        callee=None,
        args=None,
        env=None,
        stub=None,
        thread_stub=None,
        thread=None,
        operand=None,
        left=None,
        right=None,
        hay=None,
        needle=None,
        elems=None,
        pairs=None,
        index=None,
        cond=None,
        then=None,
        else_=None,
        body=None,
        orelse=None,
        finalbody=None,
        handlers=None,
        expr=None,
        message=None,
        cause=None,
        target_node=None,
    )


def setattr_stmt(object_: IrNode, name: str, value: IrNode, span: Span) -> IrNode:
    return IrNode(
        kind="setattr",
        span=span,
        type=None,
        value=value,
        object=object_,
        callee=None,
        args=None,
        env=None,
        stub=None,
        thread_stub=None,
        thread=None,
        operand=None,
        left=None,
        right=None,
        hay=None,
        needle=None,
        elems=None,
        pairs=None,
        index=None,
        cond=None,
        then=None,
        else_=None,
        body=None,
        orelse=None,
        finalbody=None,
        handlers=None,
        expr=None,
        message=None,
        cause=None,
        target_node=None,
        name=name,
    )


def setitem_stmt(container: IrNode, index: IrNode, value: IrNode, span: Span) -> IrNode:
    return IrNode(
        kind="setitem",
        span=span,
        type=None,
        value=value,
        object=container,
        callee=None,
        args=None,
        env=None,
        stub=None,
        thread_stub=None,
        thread=None,
        operand=None,
        left=None,
        right=None,
        hay=None,
        needle=None,
        elems=None,
        pairs=None,
        index=index,
        cond=None,
        then=None,
        else_=None,
        body=None,
        orelse=None,
        finalbody=None,
        handlers=None,
        expr=None,
        message=None,
        cause=None,
        target_node=None,
    )


def subscript(value: IrNode, index: IrNode, typ: T, span: Span) -> IrNode:
    return IrNode(
        kind="subscript",
        span=span,
        type=typ,
        value=value,
        object=None,
        callee=None,
        args=None,
        env=None,
        stub=None,
        thread_stub=None,
        thread=None,
        operand=None,
        left=None,
        right=None,
        hay=None,
        needle=None,
        elems=None,
        pairs=None,
        index=index,
        cond=None,
        then=None,
        else_=None,
        body=None,
        orelse=None,
        finalbody=None,
        handlers=None,
        expr=None,
        message=None,
        cause=None,
        target_node=None,
    )


def try_stmt(
    body: list[IrNode],
    handlers: list[Handler],
    span: Span,
    orelse: list[IrNode] | None = None,
    finalbody: list[IrNode] | None = None,
    brk: str = "",
    cont: str = "",
) -> IrNode:
    return IrNode(
        kind="try",
        span=span,
        type=None,
        value=None,
        object=None,
        callee=None,
        args=None,
        env=None,
        stub=None,
        thread_stub=None,
        thread=None,
        operand=None,
        left=None,
        right=None,
        hay=None,
        needle=None,
        elems=None,
        pairs=None,
        index=None,
        cond=None,
        then=None,
        else_=None,
        body=body,
        orelse=orelse,
        finalbody=finalbody,
        handlers=handlers,
        expr=None,
        message=None,
        cause=None,
        target_node=None,
        brk=brk,
        cont=cont,
    )


def unary(op: str, operand: IrNode, typ: T, span: Span) -> IrNode:
    return IrNode(
        kind="unary",
        span=span,
        type=typ,
        value=None,
        object=None,
        callee=None,
        args=None,
        env=None,
        stub=None,
        thread_stub=None,
        thread=None,
        operand=operand,
        left=None,
        right=None,
        hay=None,
        needle=None,
        elems=None,
        pairs=None,
        index=None,
        cond=None,
        then=None,
        else_=None,
        body=None,
        orelse=None,
        finalbody=None,
        handlers=None,
        expr=None,
        message=None,
        cause=None,
        target_node=None,
        op=op,
    )


def value_callee(target: IrNode) -> IrNode:
    """The `value` callee: a decorated/local callable expression."""
    return IrNode(
        kind="value",
        span=None,
        type=None,
        value=None,
        object=None,
        callee=None,
        args=None,
        env=None,
        stub=None,
        thread_stub=None,
        thread=None,
        operand=None,
        left=None,
        right=None,
        hay=None,
        needle=None,
        elems=None,
        pairs=None,
        index=None,
        cond=None,
        then=None,
        else_=None,
        body=None,
        orelse=None,
        finalbody=None,
        handlers=None,
        expr=None,
        message=None,
        cause=None,
        target_node=target,
    )


def while_stmt(cond: IrNode, body: list[IrNode], span: Span) -> IrNode:
    return IrNode(
        kind="while",
        span=span,
        type=None,
        value=None,
        object=None,
        callee=None,
        args=None,
        env=None,
        stub=None,
        thread_stub=None,
        thread=None,
        operand=None,
        left=None,
        right=None,
        hay=None,
        needle=None,
        elems=None,
        pairs=None,
        index=None,
        cond=cond,
        then=None,
        else_=None,
        body=body,
        orelse=None,
        finalbody=None,
        handlers=None,
        expr=None,
        message=None,
        cause=None,
        target_node=None,
    )
