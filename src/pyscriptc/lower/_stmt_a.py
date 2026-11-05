"""Statement lowering helpers and the narrow/unpack family.

Split verbatim from `lower._stmt` (E0 move for #852). `StmtAMixin` holds
the narrow/type-inference and unpack method family that moved out of
`StmtMixin`, and this module keeps the module-level helper definitions the
`_stmt` family shares (the dict-IR builders, the loop-flag plumbing, and
the jump detectors) at module level, so they stay reachable unqualified
from every module in the family. Every definition below is the code that
lived in `lower._stmt`, unchanged: the IR and diagnostics are
byte-identical.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING, Any  # noqa: F401  # moved-verbatim import

from pyscriptc import _irnode as _ir  # noqa: F401  # moved-verbatim import
from pyscriptc.ir import types_equal  # noqa: F401  # moved-verbatim import
from pyscriptc.lower._core import (
    Fence,  # noqa: F401  # moved-verbatim import
    _any_contains,  # noqa: F401  # moved-verbatim import
    _assign,  # noqa: F401  # moved-verbatim import
    _attr,  # noqa: F401  # moved-verbatim import
    _binary_int,  # noqa: F401  # moved-verbatim import
    _compare,  # noqa: F401  # moved-verbatim import
    _concat_str,  # noqa: F401  # moved-verbatim import
    _const_bool,  # noqa: F401  # moved-verbatim import
    _const_int,  # noqa: F401  # moved-verbatim import
    _const_none,  # noqa: F401  # moved-verbatim import
    _const_str,  # noqa: F401  # moved-verbatim import
    _has_typevar,  # noqa: F401  # moved-verbatim import
    _is_none_expr,  # noqa: F401  # moved-verbatim import
    _item,  # noqa: F401  # moved-verbatim import
    _len_call,  # noqa: F401  # moved-verbatim import
    _maybe_type_kind,  # noqa: F401  # moved-verbatim import
    _name,  # noqa: F401  # moved-verbatim import
    _node_kind,  # noqa: F401  # moved-verbatim import
    _node_type_kind,  # noqa: F401  # moved-verbatim import
    _raise_value,  # noqa: F401  # moved-verbatim import
    _show,  # noqa: F401  # moved-verbatim import
    _tuple_assign,  # noqa: F401  # moved-verbatim import
    _type_kind,  # noqa: F401  # moved-verbatim import
    _zero,  # noqa: F401  # moved-verbatim import
)
from pyscriptc.tirserde import dict_to_node, dict_to_type, node_to_dict  # noqa: F401  # moved-verbatim import

if TYPE_CHECKING:
    from pyscriptc.lower import Lowerer  # noqa: F401  # moved-verbatim import


def dict_to_list(items: list[dict[str, Any]]) -> list[Any]:
    """The typed nodes one dict-IR statement list spells."""
    return [dict_to_node(item) for item in items]


def _body_nodes(body: list[dict[str, Any]]) -> list[Any]:
    return dict_to_list(body)


def _pass_stmt(span: dict[str, Any]) -> dict[str, Any]:
    return node_to_dict(dict_to_node({"kind": "pass", "span": span}))


def _expr_wrap(expr: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return node_to_dict(_ir.expr_stmt(dict_to_node(expr), _mk_span_of(span)))


def _mk_span_of(span: dict[str, Any]) -> Any:
    from pyscriptc.tirserde import dict_to_span

    return dict_to_span(span)


def _if_stmt(
    cond: dict[str, Any], then: list[dict[str, Any]], span: dict[str, Any], else_: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    from pyscriptc.tirserde import dict_to_span

    return node_to_dict(
        _ir.if_stmt(
            dict_to_node(cond),
            [dict_to_node(item) for item in then],
            [dict_to_node(item) for item in (else_ or [])],
            dict_to_span(span),
        )
    )


def _while_loop(cond: dict[str, Any], body: list[dict[str, Any]], span: dict[str, Any]) -> dict[str, Any]:
    from pyscriptc.tirserde import dict_to_span

    return node_to_dict(_ir.while_stmt(dict_to_node(cond), [dict_to_node(item) for item in body], dict_to_span(span)))


def _unary_not(operand: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return node_to_dict(_ir.unary("not", dict_to_node(operand), dict_to_type({"kind": "bool"}), _mk_span_of(span)))


class StmtAMixin:
    def _plain_class_elem(self, elem: dict[str, Any]) -> bool:
        """A user class iterates like any pointer element; synthetic runtime classes do not."""
        if _type_kind(elem) != "class":
            return False
        info = self.classes.get(elem["name"], {})
        return bool(info.get("tuple")) or not info.get("synthetic")

    def _narrowable(self, name: str) -> bool:
        typ = self.locals.get(name)
        return typ is not None and bool(typ.get("optional")) and name not in self.fn_globals

    def _narrow_names(self, test: ast.expr) -> tuple[set[str], set[str]]:
        """The tagged optionals a test proves set when it is true and when it is false.

        `x is not None` and a bare `x` prove x set when true; `x is None`
        proves it when false; `not` swaps the two; `a and b` proves the union
        of both sides when true and what both sides prove when false; `a or b`
        is the dual. A walrus target inside the test is never narrowed.
        """
        none: set[str] = set()
        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
            when_true, when_false = self._narrow_names(test.operand)
            return when_false, when_true
        if isinstance(test, ast.Name):
            return ({test.id} if self._narrowable(test.id) else none), none
        if isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], (ast.Is, ast.IsNot)):
            left, right = test.left, test.comparators[0]
            probe = left if _is_none_expr(right) else right if _is_none_expr(left) else None
            if isinstance(probe, ast.Name) and self._narrowable(probe.id):
                if isinstance(test.ops[0], ast.IsNot):
                    return {probe.id}, none
                return none, {probe.id}
            return none, none
        if isinstance(test, ast.BoolOp):
            parts = [self._narrow_names(value) for value in test.values]
            trues = [part[0] for part in parts]
            falses = [part[1] for part in parts]
            if isinstance(test.op, ast.And):
                return set().union(*trues), set.intersection(*falses)
            return set.intersection(*trues), set().union(*falses)
        return none, none

    def _narrowed(self, names: set[str], scope: list[ast.stmt] | ast.expr, body: Any) -> Any:
        """Run `body()` with `names` narrowed, minus any name rebound in `scope`."""
        scopes = scope if isinstance(scope, list) else [scope]
        usable = {
            name
            for name in names
            if not any(
                isinstance(inner, ast.Name) and inner.id == name and isinstance(inner.ctx, (ast.Store, ast.Del))
                for item in scopes
                for inner in ast.walk(item)
            )
        }
        saved = dict(self.opt_unwrap)
        for name in usable:
            self.opt_unwrap[name] = self.locals[name]
        try:
            return body()
        finally:
            self.opt_unwrap = saved

    def _match(self, stmt: ast.Match, span: dict[str, Any]) -> list[dict[str, Any]]:
        """`match` is an if chain; a case's prelude (binds, guard eval) runs only when earlier cases miss."""
        subject_expr = self._expr(stmt.subject)
        temp = self._temp("subj")
        self.locals[temp] = subject_expr["type"]
        self.prelude.append(_assign(temp, subject_expr, span))
        for case in stmt.cases:
            pat = case.pattern
            if not (isinstance(pat, ast.MatchAs) and pat.pattern is None and pat.name) or pat.name in self.locals:
                continue
            self.locals[pat.name] = subject_expr["type"]  # fresh capture: zero-init like a `for` target
            if _node_type_kind(subject_expr) != "none":
                self.prelude.append(_assign(pat.name, _zero(subject_expr["type"], span), span))
        chain: list[dict[str, Any]] = []
        for case in reversed(stmt.cases):
            saved, self.prelude = self.prelude, []
            cond = self._match_case(case, temp, span)
            then = self._body(list(case.body))
            self.prelude, pre = saved, self.prelude
            chain = [*pre, _if_stmt(cond, then, span, chain)]
        return chain

    def _match_case(self, case: ast.match_case, subject: str, span: dict[str, Any]) -> dict[str, Any]:
        """A case's condition: alternatives or'd, guard and'd; a capture binds the subject."""
        pat = case.pattern
        values: list[ast.expr] = []
        always = False
        for part in pat.patterns if isinstance(pat, ast.MatchOr) else [pat]:
            if isinstance(part, ast.MatchAs) and part.pattern is None:
                if part.name is not None and isinstance(pat, ast.MatchOr):
                    self.fail(part, "PS2002", "a capture pattern is not lowered")
                if part.name is not None:
                    self._expect(part, self.locals[subject], self.locals[part.name], part.name)
                    self.prelude.append(_assign(part.name, _name(subject, self.locals[subject], span), span))
                always = True
                continue
            if isinstance(part, ast.MatchValue) and isinstance(part.value, ast.Constant):
                right, op = part.value, ast.Eq()
            elif isinstance(part, ast.MatchSingleton) and part.value is None:
                right, op = ast.Constant(value=None), ast.Is()
            else:
                self._refuse_pattern(part)
            left = ast.copy_location(ast.Name(id=subject, ctx=ast.Load()), part)
            compare = ast.copy_location(ast.Compare(left=left, ops=[op], comparators=[right]), part)
            values.append(compare)
        test: ast.expr | None = None
        if values and not always:
            test = values[0] if len(values) == 1 else ast.BoolOp(op=ast.Or(), values=values)
        if case.guard is not None:
            test = case.guard if test is None else ast.BoolOp(op=ast.And(), values=[test, case.guard])
        if test is not None:
            ast.copy_location(test, pat)
        return _const_bool(True, span) if test is None else self._bool_cond(test)

    def _refuse_pattern(self, pat: ast.pattern) -> None:
        if isinstance(pat, ast.MatchAs):
            if pat.name is None:
                self.fail(pat, "PS2002", "a wildcard pattern is not lowered")
            self.fail(pat, "PS2002", "a capture pattern is not lowered")
        if isinstance(pat, ast.MatchOr):
            self.fail(pat, "PS2002", "an or pattern is not lowered")
        if isinstance(pat, ast.MatchSequence) or isinstance(pat, ast.MatchStar):
            self.fail(pat, "PS2002", "a sequence pattern is not lowered")
        if isinstance(pat, ast.MatchClass):
            self.fail(pat, "PS2002", "a class pattern is not lowered")
        if isinstance(pat, ast.MatchMapping):
            self.fail(pat, "PS2002", "a mapping pattern is not lowered")
        if isinstance(pat, ast.MatchSingleton):
            self.fail(pat, "PS2002", "a singleton pattern is not lowered")
        if isinstance(pat, ast.MatchValue):
            self.fail(pat, "PS2002", "a match value that is not a literal is not lowered")
        self.fail(pat, "PS2002", f"{type(pat).__name__} is not lowered")

    def _delete(self, stmt: ast.Delete, span: dict[str, Any]) -> list[dict[str, Any]]:
        """`del` of one unread local is dropped.

        The name stays in the local table so the earlier assignment still
        type-checks, and nothing reads it. A later load stays fenced: this
        compiler cannot raise UnboundLocalError, and an empty `for` target
        already reads as 0. Other targets stay fenced.
        """
        if len(stmt.targets) != 1:
            self.fail(stmt, "PS2002", "del of more than one name is not lowered")
        target = stmt.targets[0]
        if isinstance(target, ast.Attribute):
            self.fail(stmt, "PS2002", "del of an attribute is not lowered")
        if isinstance(target, ast.Subscript):
            if isinstance(target.slice, ast.Slice):
                self.fail(stmt, "PS2002", "del of a slice is not lowered")
            # del d[k] lowers to ps_map_del for a declared dict local; every
            # other subscript target keeps the subscript fence.
            if isinstance(target.value, ast.Name):
                local = self.locals.get(target.value.id, {})
                kind = _maybe_type_kind(local)
                if kind == "dict":
                    container = self._expr(target.value)
                    index = self._expr(target.slice)
                    self._expect(stmt, index["type"], container["type"]["key"], "dict key")
                    call = self._abi_call(
                        "builtins",
                        "dict.delitem",
                        [container, index],
                        {"kind": "none"},
                        "ps_map_del",
                        span,
                    )
                    return [_expr_wrap(call, span)]
                if kind == "list" and not local.get("set"):
                    container = self._expr(target.value)
                    index = self._expr(target.slice)
                    if _node_type_kind(index) != "int":
                        self.fail(stmt, "PS1003", "a list index is int")
                    call = self._abi_call(
                        "builtins",
                        "list.delitem",
                        [container, index],
                        {"kind": "none"},
                        "ps_seq_del",
                        span,
                    )
                    return [_expr_wrap(call, span)]
            self.fail(stmt, "PS2002", "del of a subscript is not lowered")
        if isinstance(target, (ast.Tuple, ast.List)):
            self.fail(stmt, "PS2002", "del of a tuple of names is not lowered")
        if not isinstance(target, ast.Name):
            self.fail(stmt, "PS2002", "del of this target is not lowered")
        name = target.id
        if name in self.fn_globals:
            self.fail(stmt, "PS2002", "del of a global is not lowered")
        if name not in self.locals:
            self.fail(stmt, "PS2002", "del of an unbound name is not lowered")
        if self._name_read_after(stmt, name):
            self.fail(stmt, "PS2002", "del of a name that is read later is not lowered")
        return [_pass_stmt(span)]

    def _name_read_after(self, delete: ast.Delete, name: str) -> bool:
        state = {"seen": False, "hit": False}

        def visit(node: ast.AST) -> None:
            if state["hit"]:
                return
            if node is delete:
                state["seen"] = True
                return
            if state["seen"] and isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id == name:
                state["hit"] = True
                return
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                return
            for child in ast.iter_child_nodes(node):
                visit(child)

        for stmt in self.fn_body:
            visit(stmt)
        return (not state["seen"]) or state["hit"]

    def _infer_accumulated_type(self, stmt: ast.Assign) -> dict[str, Any] | None:
        """The container type an empty display accumulates in this body.

        An empty `[]` or `{}` says nothing about its element type: the uses
        do. This scans the rest of the function body for `name.append(v)`
        and `name[k] = v` shapes on the display's own name, lowers each
        contributing value under the peek, and returns the list or dict
        type when every use agrees on it. The scan is structural and order
        free: a key or value naming a local bound earlier in the same body
        still resolves, because that binding lowers before the mutation in
        a real run. Mixed element types, a use the peek cannot lower, or a
        mutation that is not a plain append or item write is a refusal
        (None): the name keeps its annotation fence. A display that is not
        empty has its own element type and never reaches here.
        """
        if not isinstance(stmt.value, (ast.List, ast.Dict, ast.Set)):
            return None
        if isinstance(stmt.value, ast.List) and stmt.value.elts:
            return None
        if isinstance(stmt.value, ast.Set) and stmt.value.elts:
            return None
        if isinstance(stmt.value, ast.Dict) and stmt.value.keys:
            return None
        if self.function is None or self.inf_peek:
            return None
        target = stmt.targets[0].id

        def _locals_upto(node: ast.AST) -> None:
            """Bind the names assigned before `node` in source order.

            The peek lowers a use of a local the body bound earlier (a key
            variable); without the binding the peek sees a name error and
            refuses. The types come from the same peek, so a value that
            does not lower leaves the name out and the mutation is refused
            later on its own read.
            """

            for item in self.fn_body:
                if item is node:
                    break
                for inner in ast.walk(item):
                    if isinstance(inner, ast.Assign) and isinstance(inner.targets[0], ast.Name):
                        name = inner.targets[0].id
                        if name == target or name in self.locals:
                            continue
                        if inner.value is stmt.value:
                            continue
                        typ = self._infer_type_of(inner.value)
                        if typ is not None and _type_kind(typ) != "none":
                            self.locals[name] = typ

        def _bind_for_targets() -> None:
            """Bind loop targets to their iterable's element type.

            A key or value inside a `for` loop reads the loop variable; a
            named iterable is already a local (the loop lowered before in a
            real run), so the element type is its `elem`. A `range(...)`
            iterable peeks directly: its element type is the int the loop
            binds. Any other iterable the subset cannot read leaves the
            target out. The whole body is scanned: the mutation node sits
            inside the loop, not under it.
            """

            for outer in self.fn_body:
                for loop in ast.walk(outer):
                    if not isinstance(loop, ast.For) or not isinstance(loop.target, ast.Name):
                        continue
                    name = loop.target.id
                    if name in self.locals:
                        continue
                    if isinstance(loop.iter, ast.Name):
                        iter_typ = self.locals.get(loop.iter.id)
                    else:
                        # A display or call iterable peeks directly: its
                        # element type is the fact, or the peek refuses and
                        # the target stays out.
                        iter_typ = self._infer_type_of(loop.iter)
                    if iter_typ is not None and _maybe_type_kind(iter_typ) == "list":
                        self.locals[name] = iter_typ["elem"]

        def _written_value(node: ast.expr) -> dict[str, Any] | None:
            """The written value's type, read through the accumulator.

            `dic[k] = dic.get(k, 0) + 1` is the count pattern: the read of
            the accumulator itself is what the peek cannot resolve (the
            type is being derived), so the read is replaced by the
            default's type and the arithmetic runs on that. An int default
            means int counts. Any other shape peeks with the partial
            accumulator type bound, and refuses when the read still does
            not resolve.
            """

            if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
                left = _written_value(node.left)
                right = self._infer_type_of(node.right)
                if left is None or right is None or _type_kind(right) != "int":
                    return None
                if _type_kind(left) == "int":
                    return left
                return None
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                recv = node.func.value
                if node.func.attr == "get" and isinstance(recv, ast.Name) and recv.id == target and len(node.args) == 2:
                    default = self._infer_type_of(node.args[1])
                    if default is None or _type_kind(default) == "none":
                        return None
                    return default
            saved = dict(self.locals)
            self.locals[target] = (
                {"kind": "dict", "key": {"kind": "str"}, "value": {"kind": "int"}}
                if value is None
                else {"kind": "dict", "key": {"kind": "str"}, "value": value}
            )
            try:
                return self._infer_type_of(node)
            finally:
                self.locals = saved

        saved_locals = dict(self.locals)
        elem: dict[str, Any] | None = None
        value: dict[str, Any] | None = None
        try:
            _bind_for_targets()
            for item in self.fn_body:
                for node in ast.walk(item):
                    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                        continue
                    if node.func.attr != "append" or not isinstance(node.func.value, ast.Name):
                        continue
                    if node.func.value.id != target or len(node.args) != 1:
                        continue
                    _locals_upto(node)
                    _bind_for_targets()
                    typ = self._infer_type_of(node.args[0])
                    if typ is None or _type_kind(typ) in {"none", "bool", "func"}:
                        return None
                    if elem is None:
                        elem = typ
                    elif not types_equal(elem, typ):
                        return None
                for node in ast.walk(item):
                    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                        continue
                    if node.func.attr != "setdefault" or not isinstance(node.func.value, ast.Name):
                        continue
                    if node.func.value.id != target or len(node.args) != 2:
                        continue
                    _locals_upto(node)
                    _bind_for_targets()
                    key = self._infer_type_of(node.args[0])
                    val = self._infer_type_of(node.args[1])
                    if key is None or val is None or _type_kind(key) not in {"int", "str"}:
                        return None
                    if value is None:
                        value = val
                    elif not types_equal(value, val):
                        return None
                for node in ast.walk(item):
                    if (
                        isinstance(node, ast.Assign)
                        and isinstance(node.targets[0], ast.Subscript)
                        and isinstance(node.targets[0].value, ast.Name)
                        and node.targets[0].value.id == target
                    ):
                        _locals_upto(node)
                        _bind_for_targets()
                        key = self._infer_type_of(node.targets[0].slice)
                        val = _written_value(node.value)
                        if key is None or val is None or _type_kind(key) not in {"int", "str"}:
                            return None
                        if value is None:
                            value = val
                        elif not types_equal(value, val):
                            return None
        finally:
            self.locals = saved_locals
        if elem is not None:
            return {"kind": "list", "elem": elem}
        if value is not None:
            return {"kind": "dict", "key": {"kind": "str"}, "value": value}
        return None

    def _aug_assign(self, stmt: ast.AugAssign, span: dict[str, Any]) -> list[dict[str, Any]]:
        if not isinstance(stmt.target, ast.Name):
            self.fail(stmt, "PS2002", "augmented assignment is only lowered for a name")
        target = stmt.target.id
        if (
            target in self.fn_globals
            or target in self.fn_nonlocals
            or (self.function == self.entry_fn_name and target in self.global_slots)
        ):
            assign = ast.Assign(
                targets=[stmt.target],
                value=ast.BinOp(left=ast.Name(id=target, ctx=ast.Load()), op=stmt.op, right=stmt.value),
            )
            ast.copy_location(assign, stmt)
            ast.copy_location(assign.value, stmt)
            ast.copy_location(assign.value.left, stmt)
            return self._stmt_core(assign)
        if target not in self.locals:
            self.fail(stmt, "PS1001", f"{target} needs an annotation before use")
        left = ast.Name(id=target, ctx=ast.Load())
        ast.copy_location(left, stmt)
        node = ast.BinOp(left=left, op=stmt.op, right=stmt.value)
        ast.copy_location(node, stmt)
        value = self._expr(node)
        self._expect(stmt, value["type"], self.locals[target], target)
        return [_assign(target, value, span)]

    def _spread_assign(self, stmt: ast.Assign, span: dict[str, Any]) -> list[dict[str, Any]]:
        """Chained (`a = b = c`) and destructuring (`a, b = pair`, `first, *rest = xs`) targets.

        The RHS lowers once; a non-name value pins to a temp when several
        targets share it or a destructuring needs repeated reads.
        """
        value = self._expr(stmt.value)
        stmts: list[dict[str, Any]] = []
        for target in stmt.targets:
            if isinstance(target, (ast.Tuple, ast.List)):
                value = self._pin(value, stmts, span)
                stmts.extend(self._unpack(stmt, target.elts, value, span))
            elif isinstance(target, ast.Name):
                if len(stmt.targets) > 1:
                    value = self._pin(value, stmts, span)
                stmts.append(self._store_target(stmt, target.id, value, span))
                self._note_frozen(target.id, stmt.value)
            else:
                self.fail(stmt, "PS2002", "only a single name can be assigned")
        return stmts

    def _pin(self, value: dict[str, Any], stmts: list[dict[str, Any]], span: dict[str, Any]) -> dict[str, Any]:
        if _node_kind(value) == "name":
            return value
        holder = self._temp("hold")
        self.locals[holder] = value["type"]
        stmts.append(_assign(holder, value, span))
        return _name(holder, value["type"], span)

    def _store_target(self, node: ast.AST, name: str, value: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
        """One name target: a global slot stores through `ps_global_set`; a local binds."""
        if name in self.fn_globals:
            slot = self.global_slots.get(name)
            if slot is None or not slot.get("active"):
                self.fail(node, "PS2002", f"global {name} is not a module slot")
            self._expect(node, value["type"], slot["type"], name)
            return self._global_set(name, value, span)
        self._bind_target(name, value["type"], node)
        return _assign(name, value, span)

    def _bind_target(self, name: str, typ: dict[str, Any], node: ast.AST) -> None:
        """An unpack or chained target binds a fresh name implicitly, like a for target."""
        if name in self.bindings or name in self.classes:
            self.fail(node, "PS2002", f"{name} is already bound and cannot be an unpack target")
        if name not in self.locals:
            self.locals[name] = typ
            return
        if not types_equal(self.locals[name], typ):
            self.fail(node, "PS1003", f"{name} is {_show(self.locals[name])}, and the unpack target is {_show(typ)}")

    def _unpack(
        self, node: ast.AST, elts: list[ast.expr], value: dict[str, Any], span: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """A tuple or list destructuring target.

        Element targets are names or a nested tuple/list; a single `*name`
        collects the middle. The value is a tuple (static field reads) or a
        list (runtime length checks).
        """
        star = -1
        for index, elt in enumerate(elts):
            inner = elt.value if isinstance(elt, ast.Starred) else elt
            if not isinstance(inner, (ast.Name, ast.Tuple, ast.List)):
                self.fail(elt, "PS2002", "unpack targets are names")
            if isinstance(elt, ast.Starred):
                if star >= 0:
                    self.fail(elt, "PS2002", "unpack keeps a single star")
                star = index
        typ = value["type"]
        if _type_kind(typ) == "class" and self.classes.get(typ["name"], {}).get("tuple"):
            return self._unpack_fixed(node, elts, star, value, span)
        if _type_kind(typ) == "list" and not typ.get("set"):
            return self._unpack_sized(elts, star, value, span)
        self.fail(node, "PS2002", "unpacking needs a tuple or list value")
        raise AssertionError("unreachable")

    def _unpack_fixed(
        self, node: ast.AST, elts: list[ast.expr], star: int, value: dict[str, Any], span: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """A tuple RHS reads fields; a wrong name count is a static fence (the checker rejects it first anyway)."""
        fields = self.classes[value["type"]["name"]]["fields"]
        if star < 0:
            if len(fields) != len(elts):
                self.fail(node, "PS2002", f"unpacking a {len(fields)}-tuple needs {len(fields)} names")
        elif len(fields) < len(elts) - 1:
            self.fail(node, "PS2002", f"starred unpack of a {len(fields)}-tuple needs {len(fields)} fields")
        tail = len(elts) - star - 1 if star >= 0 else 0
        span_fields = fields[star : len(fields) - tail] if star >= 0 else []
        elem: dict[str, Any] | None = None
        if star >= 0:
            if not span_fields:
                self.fail(elts[star], "PS2002", "starred unpack needs an element type")
            elem = span_fields[0]["type"]
            if any(not types_equal(field["type"], elem) for field in span_fields):
                self.fail(elts[star], "PS2002", "starred unpack needs uniform element types")
        stmts: list[dict[str, Any]] = []
        for index, elt in enumerate(elts):
            if index == star:
                member: dict[str, Any] = {
                    "kind": "list",
                    "elems": [_attr(value, field["name"], field["type"], span) for field in span_fields],
                    "type": {"kind": "list", "elem": elem},
                    "span": span,
                }
            else:
                f_index = index if star < 0 or index < star else len(fields) - (len(elts) - index)
                member = _attr(value, fields[f_index]["name"], fields[f_index]["type"], span)
            stmts.extend(self._unpack_name(elt, member, span))
        return stmts

    def _unpack_sized(
        self, elts: list[ast.expr], star: int, value: dict[str, Any], span: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """A list RHS length-checks at run time; a wrong count raises the CPython ValueError."""
        stmts: list[dict[str, Any]] = []
        if _node_kind(value) != "name":
            seq = self._temp("seq")
            self.locals[seq] = value["type"]
            stmts.append(_assign(seq, value, span))
            value = _name(seq, value["type"], span)
        length = _len_call(value, span)
        got = node_to_dict(
            _ir.call(
                _ir.builtin("str"), [dict_to_node(length)], dict_to_type({"kind": "str"}), _mk_span_of(span), "", "", -1
            )
        )
        need = len(elts) - 1 if star >= 0 else len(elts)
        checks: list[tuple[dict[str, Any], dict[str, Any]]] = []
        if star < 0:
            checks.append(
                (
                    _compare("<", length, _const_int(need, span), span),
                    _concat_str(
                        [
                            _const_str(f"not enough values to unpack (expected {need}, got ", span),
                            got,
                            _const_str(")", span),
                        ],
                        span,
                    ),
                )
            )
            checks.append(
                (
                    _compare(">", length, _const_int(need, span), span),
                    _const_str(f"too many values to unpack (expected {need})", span),
                )
            )
        else:
            checks.append(
                (
                    _compare("<", length, _const_int(need, span), span),
                    _concat_str(
                        [
                            _const_str(f"not enough values to unpack (expected at least {need}, got ", span),
                            got,
                            _const_str(")", span),
                        ],
                        span,
                    ),
                )
            )
        for cond, message in checks:
            stmts.append(_if_stmt(cond, [_raise_value(message, span)], span))
        elem = value["type"]["elem"]
        for index, elt in enumerate(elts):
            if index == star:
                tail = len(elts) - star - 1
                member = self._abi_call(
                    "builtins",
                    "slice",
                    [
                        value,
                        _const_int(star, span),
                        _const_int(-tail, span),
                        _const_int(1, span),
                        _const_int(1 if tail else 0, span),
                        _const_int(1, span),
                    ],
                    {"kind": "list", "elem": elem},
                    "ps_seq_slice",
                    span,
                )
            else:
                offset = index if star < 0 or index < star else index - len(elts)
                member = _item(value, _const_int(offset, span), elem, span)
            stmts.extend(self._unpack_name(elt, member, span))
        return stmts

    def _unpack_name(self, elt: ast.expr, member: dict[str, Any], span: dict[str, Any]) -> list[dict[str, Any]]:
        """One unpack element: a Name binds; a nested tuple/list recurses."""
        if isinstance(elt, ast.Starred):
            elt = elt.value
        if isinstance(elt, ast.Name):
            return [self._store_target(elt, elt.id, member, span)]
        assert isinstance(elt, (ast.Tuple, ast.List))
        return self._unpack(elt, elt.elts, member, span)


def _is_main_guard(stmt: ast.stmt) -> bool:
    if not isinstance(stmt, ast.If) or not isinstance(stmt.test, ast.Compare):
        return False
    test = stmt.test
    return (
        isinstance(test.left, ast.Name)
        and test.left.id == "__name__"
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Eq)
        and len(test.comparators) == 1
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value == "__main__"
    )


def _not_bool(name: str, span: dict[str, Any]) -> dict[str, Any]:
    return _unary_not(_name(name, {"kind": "bool"}, span), span)


def _always_returns(body: list[dict[str, Any]]) -> bool:
    for stmt in body:
        if _node_kind(stmt) == "return" or _node_kind(stmt) == "raise":
            return True
        if _node_kind(stmt) == "if" and _always_returns(stmt["then"]) and _always_returns(stmt["else"]):
            return True
        if (
            _node_kind(stmt) == "try"
            and _always_returns(stmt["body"])
            and all(_always_returns(handler["body"]) for handler in stmt["handlers"])
        ):
            return True
    return False
