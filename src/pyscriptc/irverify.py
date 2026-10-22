"""Structural verification of the frozen IR.

`ir.py` checks the schema: every node has the right kind, the right keys, and
a span. The schema says nothing about whether the module is *sound*. This
module checks the structural invariants the LLVM emitter relies on, so a drift
between lowering and the emitter is reported as a named diagnostic instead of
as an undefined symbol or a wrong-output binary.

Three families of checks:

- Types: the operand types agree, a comparison yields `bool`, a condition is
  `bool`, and a returned value matches the function's return type.
- Defined-before-use: a `name` resolves to a parameter, local, or env slot of
  its function, and a `call` callee names a function or class method that
  exists.
- Terminators: a `return` or `raise` ends its body, and a function with a
  non-`none` return type ends every path in a `return` or a `raise`.

The verifier reads the IR and never mutates it, so it is safe to run on a
module that is about to be emitted. Failures raise `IRVerifyError`, whose
`code` is a stable `PS` code.

"""

from __future__ import annotations

from typing import Any

from pyscriptc.errors import SpikeError
from pyscriptc.ir import TYPE_KINDS

# The verifier's own codes. `PS0001` is the generic invalid-IR code and stays
# for schema failures; the verifier gets its own block so a violation names
# the family it belongs to.
CODE_TYPE = "PS0101"
CODE_SCOPE = "PS0102"
CODE_TERMINATOR = "PS0103"


class IRVerifyError(SpikeError):
    """A structural violation in the IR. `code` is stable."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message)


def _span_of(node: Any) -> str:
    """`file:line:col` for a node that carries a span, else the empty string."""
    if not isinstance(node, dict):
        return ""
    span = node.get("span")
    if not isinstance(span, dict):
        return ""
    file = span.get("file", "?")
    line = span.get("line", 0)
    col = span.get("col", 0)
    return f"{file}:{line}:{col}"


def _where(where: str, node: Any) -> str:
    """The diagnostic prefix: the path plus the node's position when it has one."""
    span = _span_of(node)
    return f"{where} at {span}" if span else where


def _type_kind(node: Any) -> str | None:
    """The type kind of an expression node, or `None` when it has no usable type."""
    if not isinstance(node, dict):
        return None
    typ = node.get("type")
    if not isinstance(typ, dict):
        return None
    kind = typ.get("kind")
    return kind if isinstance(kind, str) else None


def _declared_kind(bound: Any) -> str:
    """The type kind of a `{name, type}` binding, or `unknown` when absent."""
    typ = bound.get("type") if isinstance(bound, dict) else None
    kind = typ.get("kind") if isinstance(typ, dict) else None
    return kind if isinstance(kind, str) else "unknown"


class _Scope:
    """The names a function can read: its parameters, locals and env slots."""

    def __init__(self, fn: dict[str, Any]) -> None:
        self.names: dict[str, str] = {}
        for param in fn.get("params", []) or []:
            self.names[param["name"]] = _declared_kind(param)
        for local in fn.get("locals", []) or []:
            self.names[local["name"]] = _declared_kind(local)
        for item in fn.get("env", []) or []:
            self.names[item["name"]] = _declared_kind(item)

    def has(self, name: str) -> bool:
        return name in self.names


class Verifier:
    """Walks one module and collects the violations it finds."""

    def __init__(self, module: dict[str, Any]) -> None:
        self.module = module
        self.functions: set[str] = {fn["name"] for fn in module.get("functions", [])}
        self.methods: set[str] = set()
        self.classes: set[str] = set()
        for cls in module.get("classes", []) or []:
            self.classes.add(cls["name"])
            for method in cls.get("methods", []) or []:
                self.methods.add(f"cls.{cls['name']}.{method['name']}")
        self.errors: list[IRVerifyError] = []

    def fail(self, code: str, where: str, message: str) -> None:
        self.errors.append(IRVerifyError(code, f"{where}: {message}"))

    # --- module ---------------------------------------------------------

    def run(self) -> list[IRVerifyError]:
        for fn in self.module.get("functions", []) or []:
            self.check_function(fn, fn["name"])
        for cls in self.module.get("classes", []) or []:
            for method in cls.get("methods", []) or []:
                self.check_function(method, f"{cls['name']}.{method['name']}")
        return self.errors

    # --- functions ------------------------------------------------------

    def check_function(self, fn: dict[str, Any], label: str) -> None:
        scope = _Scope(fn)
        return_type = _declared_kind({"type": fn.get("returnType")})
        body = fn.get("body", []) or []
        self.check_body(body, scope, label, return_type, top=True)
        self.check_terminated(body, label, return_type)

    def check_body(
        self,
        body: list[dict[str, Any]],
        scope: _Scope,
        label: str,
        return_type: str,
        top: bool = False,
    ) -> None:
        for index, stmt in enumerate(body):
            self.check_stmt(stmt, scope, label, return_type)
            if stmt.get("kind") in ("return", "raise") and index + 1 < len(body):
                # A statement after a terminator is unreachable. The emitter
                # drops it, so it is a drift between the IR and the emitted
                # text, not a wrong-answer bug.
                self.fail(
                    CODE_TERMINATOR,
                    _where(f"{label}.body[{index}]", stmt),
                    f"statement follows a {stmt['kind']} that ends the body",
                )

    def check_terminated(self, body: list[dict[str, Any]], label: str, return_type: str) -> None:
        """A function with a value return type must end every path."""
        if return_type in ("none", "unknown"):
            return
        if not _always_terminates(body):
            self.fail(
                CODE_TERMINATOR,
                label,
                f"function returns {return_type} but a path can reach the end without a return",
            )

    # --- statements -----------------------------------------------------

    def check_stmt(self, stmt: dict[str, Any], scope: _Scope, label: str, return_type: str) -> None:
        kind = stmt.get("kind")
        where = _where(label, stmt)
        if kind == "assign":
            self.check_expr(stmt.get("value"), scope, label)
            target_type = scope.names.get(stmt.get("target", ""))
            value_type = _type_kind(stmt.get("value"))
            if (
                target_type is not None
                and value_type is not None
                and target_type != value_type
                and "unknown" not in (target_type, value_type)
            ):
                self.fail(
                    CODE_TYPE,
                    where,
                    f"assigns a {value_type} to {stmt.get('target')}, which is a {target_type}",
                )
        elif kind == "if":
            self.check_condition(stmt.get("cond"), scope, label)
            self.check_body(stmt.get("then", []) or [], scope, label, return_type)
            self.check_body(stmt.get("else", []) or [], scope, label, return_type)
        elif kind == "while":
            self.check_condition(stmt.get("cond"), scope, label)
            self.check_body(stmt.get("body", []) or [], scope, label, return_type)
        elif kind == "return":
            self.check_expr(stmt.get("value"), scope, label)
            value_type = _type_kind(stmt.get("value"))
            if (
                value_type is not None
                and return_type != "none"
                and value_type != return_type
                and "unknown" not in (value_type, return_type)
            ):
                self.fail(
                    CODE_TYPE,
                    where,
                    f"returns a {value_type} but the function returns {return_type}",
                )
        elif kind == "expr":
            self.check_expr(stmt.get("expr"), scope, label)
        elif kind == "setitem":
            self.check_expr(stmt.get("container"), scope, label)
            self.check_expr(stmt.get("index"), scope, label)
            self.check_expr(stmt.get("value"), scope, label)
        elif kind == "setattr":
            self.check_expr(stmt.get("object"), scope, label)
            self.check_expr(stmt.get("value"), scope, label)
        elif kind == "raise":
            if isinstance(stmt.get("message"), dict):
                self.check_expr(stmt["message"], scope, label)
            cause = stmt.get("cause")
            if isinstance(cause, dict):
                if cause.get("kind") == "name":
                    self.check_expr(cause, scope, label)
                elif cause.get("kind") == "excmake" and isinstance(cause.get("message"), dict):
                    self.check_expr(cause["message"], scope, label)
        elif kind == "try":
            self.check_body(stmt.get("body", []) or [], scope, label, return_type)
            for index, handler in enumerate(stmt.get("handlers", []) or []):
                self.check_body(handler.get("body", []) or [], scope, label, return_type)
                excslot = handler.get("excslot")
                if isinstance(excslot, str) and not scope.has(excslot):
                    self.fail(
                        CODE_SCOPE,
                        _where(f"{label}.handlers[{index}]", stmt),
                        f"handler binds {excslot}, which the function does not declare",
                    )
            for key in ("orelse", "finalbody"):
                self.check_body(stmt.get(key, []) or [], scope, label, return_type)

    def check_condition(self, cond: Any, scope: _Scope, label: str) -> None:
        self.check_expr(cond, scope, label)
        kind = _type_kind(cond)
        if kind is not None and kind not in ("bool", "unknown"):
            self.fail(
                CODE_TYPE,
                _where(label, cond),
                f"condition is a {kind}, not a bool",
            )

    # --- expressions ----------------------------------------------------

    def check_expr(self, expr: Any, scope: _Scope, label: str) -> None:
        if not isinstance(expr, dict):
            return
        kind = expr.get("kind")
        where = _where(label, expr)
        if kind == "name":
            name = expr.get("id")
            if isinstance(name, str) and not scope.has(name):
                self.fail(CODE_SCOPE, where, f"{name} is used but not defined in this function")
            return
        if kind == "binary":
            left = expr.get("left")
            right = expr.get("right")
            self.check_expr(left, scope, label)
            self.check_expr(right, scope, label)
            left_type = _type_kind(left)
            right_type = _type_kind(right)
            if (
                left_type is not None
                and right_type is not None
                and left_type != right_type
                and "unknown" not in (left_type, right_type)
            ):
                self.fail(
                    CODE_TYPE,
                    where,
                    f"binary {expr.get('op')} mixes a {left_type} and a {right_type}",
                )
        elif kind == "compare":
            left = expr.get("left")
            right = expr.get("right")
            self.check_expr(left, scope, label)
            self.check_expr(right, scope, label)
            left_type = _type_kind(left)
            right_type = _type_kind(right)
            if (
                # `is` and `is not` are identity checks: they compare any two
                # operands. The lowering rewrites `x is not None` into
                # `x != None`, so a `none` operand is legal on any operator.
                expr.get("op") not in ("is", "is not")
                and left_type != "none"
                and right_type != "none"
                and left_type is not None
                and right_type is not None
                and left_type != right_type
                and "unknown" not in (left_type, right_type)
            ):
                self.fail(
                    CODE_TYPE,
                    where,
                    f"compare {expr.get('op')} mixes a {left_type} and a {right_type}",
                )
            result = _type_kind(expr)
            if result is not None and result not in ("bool", "unknown"):
                self.fail(CODE_TYPE, where, f"compare yields a {result}, not a bool")
        elif kind == "call":
            self.check_callee(expr.get("callee"), scope, label)
            for arg in expr.get("args", []) or []:
                self.check_expr(arg, scope, label)
        elif kind == "list":
            for elem in expr.get("elems", []) or []:
                self.check_expr(elem, scope, label)
        elif kind == "dict":
            for pair in expr.get("pairs", []) or []:
                if isinstance(pair, dict):
                    self.check_expr(pair.get("key"), scope, label)
                    self.check_expr(pair.get("value"), scope, label)
        elif kind in ("subscript", "attr", "cast"):
            self.check_expr(expr.get("value"), scope, label)
            if kind == "subscript":
                self.check_expr(expr.get("index"), scope, label)
        elif kind == "unary":
            self.check_expr(expr.get("operand"), scope, label)
        elif kind == "contains":
            self.check_expr(expr.get("hay"), scope, label)
            self.check_expr(expr.get("needle"), scope, label)
        elif kind == "closure":
            name = expr.get("name")
            if isinstance(name, str) and name not in self.functions and name not in self.classes:
                self.fail(CODE_SCOPE, where, f"closure names {name}, which is not a function or class")
            for item in expr.get("env", []) or []:
                if isinstance(item, dict) and isinstance(item.get("init"), dict):
                    self.check_expr(item["init"], scope, label)

    def check_callee(self, callee: Any, scope: _Scope, label: str) -> None:
        if not isinstance(callee, dict):
            return
        kind = callee.get("kind")
        where = _where(label, callee)
        if kind == "function":
            name = callee.get("name")
            if isinstance(name, str) and name not in self.functions and name not in self.methods:
                self.fail(CODE_SCOPE, where, f"call names {name}, which is not a function of this module")
        elif kind == "class":
            name = callee.get("name")
            if isinstance(name, str) and name not in self.classes:
                self.fail(CODE_SCOPE, where, f"call names the class {name}, which this module does not define")
        elif kind == "value":
            self.check_expr(callee.get("target"), scope, label)


def _always_terminates(body: list[dict[str, Any]]) -> bool:
    """True when every path through `body` ends in a `return` or a `raise`."""
    for stmt in body:
        kind = stmt.get("kind")
        if kind in ("return", "raise"):
            return True
        if kind == "if":
            then = stmt.get("then", []) or []
            otherwise = stmt.get("else", []) or []
            if then and otherwise and _always_terminates(then) and _always_terminates(otherwise):
                return True
        elif kind == "while":
            # A `while True` without a break never falls out. The IR has no
            # flag for it, so treat a constant-true condition as terminating.
            cond = stmt.get("cond")
            if isinstance(cond, dict) and cond.get("kind") == "const" and cond.get("value") == "True":
                return True
        elif kind == "try":
            handlers = stmt.get("handlers", []) or []
            if handlers and all(_always_terminates(handler.get("body", []) or []) for handler in handlers):
                if _always_terminates(stmt.get("body", []) or []):
                    return True
    return False


def verify_module(module: dict[str, Any]) -> list[IRVerifyError]:
    """Verify a lowered module and return every violation it found."""
    return Verifier(module).run()


def verify_or_raise(module: dict[str, Any]) -> None:
    """Verify a lowered module and raise on the first violation."""
    errors = verify_module(module)
    if errors:
        raise errors[0]


def type_kinds() -> set[str]:
    """The type kinds the verifier knows. Exposed for the tests."""
    return set(TYPE_KINDS)
