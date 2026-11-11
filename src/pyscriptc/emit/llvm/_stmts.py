"""Statement emission: the statement dispatch and the control-flow shapes
(assignments, `if`, `while`, `try`, `raise`, `setitem`, `setattr`).
"""

from __future__ import annotations

from typing import Any

from pyscriptc._irnode import Handler, IrNode
from pyscriptc.builtins import EXCEPTIONS, RESERVED_EXCEPTIONS, USER_EXC_BASE
from pyscriptc.emit._ir import (
    _field_type,
    _items,
    _llvm_field_type,
    _opt_excslot,
    _opt_node_code,
    _opt_str_or_none,
    _span_of,
    _type_of,
)
from pyscriptc.emit.llvm._base import EmitterBase
from pyscriptc.errors import IRError
from pyscriptc.tirserde import dict_to_node, dict_to_type, node_to_dict


def dict_to_dict(value: Any) -> dict[str, Any]:
    """The dict IR one typed value spells, or the mapping itself when it is
    already a plain dict (`node_to_dict` narrowed to the record case).

    This is the transitional boundary of the mixin's MIR migration
    (#829): statement-level reads go through the typed `_irnode` record,
    while the child paths that still speak dict IR (the sibling mixins'
    `emit_expr`, the try outline and handler records, `_used_names`) take
    the record's dict spelling back.
    """
    if isinstance(value, dict):
        return value
    if hasattr(value, "codes"):
        # A Handler is a kindless record: `node_to_dict` refuses it at the
        # top level (a list of handlers is the only shape it travels in).
        from pyscriptc.tirserde import _handler_dict

        out_handler: dict[str, Any] = _handler_dict(value)
        return out_handler
    out: Any = node_to_dict(value)
    return out


def _child_view(node: Any) -> tuple[str, str | None]:
    """The `(kind, type kind)` pair of a typed child, or of the plain dict
    the escape hatch kept whole (a call with payload keys the typed record
    has no slot for — print's `sep`/`end`, the island call's slots)."""
    if isinstance(node, dict):
        return _dict_child_view(node)
    t: Any = node.type
    tk: str | None = t.kind if t is not None else None
    k: Any = node.kind
    return (k if isinstance(k, str) else ""), tk


_DICT_VIEW_KEYS = ("type", "kind")


def _dict_child_view(node: dict[str, Any]) -> tuple[str, str | None]:
    """The `(kind, type kind)` pair of the plain dict the escape hatch kept
    whole (a call with payload keys the typed record has no slot for —
    print's `sep`/`end`, the island call's slots)."""
    t: Any = node[_DICT_VIEW_KEYS[0]]
    tk: str | None = t[_DICT_VIEW_KEYS[1]] if isinstance(t, dict) else None
    k: Any = node[_DICT_VIEW_KEYS[1]]
    return (k if isinstance(k, str) else ""), tk


def _is_exit_status(node: dict[str, Any] | None) -> bool:
    """Whether `node` is an int-valued expression (a bool is an int in CPython).

    Only `raise SystemExit(<int>)` keeps its argument typed; every other
    exception message is the lowered `str()` of its argument.
    """
    if node is None:
        return False
    n: Any = dict_to_node(node)
    t: Any = n.type
    if t is None:
        return False
    return t.kind in {"int", "bool"}


class StmtsMixin(EmitterBase):
    def emit_stmt(self, stmt: dict[str, Any]) -> None:
        node: Any = dict_to_node(stmt)
        kind: str = node.kind
        if kind == "assign":
            value_node: Any = node.value
            vkind: str
            vtyp_kind: str | None
            vkind, vtyp_kind = _child_view(value_node)
            if vtyp_kind == "none":
                if vkind != "const":
                    self.emit_expr(dict_to_dict(value_node))
                return
            self.store(node.target, self.emit_expr(dict_to_dict(value_node)))
        elif kind == "expr":
            expr_node: Any = node.expr
            ekind: str
            etyp_kind: str | None
            ekind, etyp_kind = _child_view(expr_node)
            if ekind == "const" and etyp_kind == "none":
                return
            self.emit_expr(dict_to_dict(expr_node))
        elif kind == "return":
            self._emit_return(stmt)
        elif kind == "pass":
            return
        elif kind == "setitem":
            self.emit_setitem(stmt)
        elif kind == "setattr":
            self.emit_setattr(stmt)
        elif kind == "raise":
            self.emit_raise(stmt)
            self.terminated = True
        elif kind == "try":
            self.emit_try(stmt)
        elif kind == "if":
            self.emit_if(stmt)
        elif kind == "while":
            self.emit_while(stmt)
        else:
            raise IRError(f"cannot emit statement {kind}")

    def emit_if(self, stmt: dict[str, Any]) -> None:
        node: Any = dict_to_node(stmt)
        cond = self.emit_expr(dict_to_dict(node.cond))
        then_l = self.fresh("then")
        else_l = self.fresh("else")
        merge_l = self.fresh("merge")
        self.line(f"br i1 {cond}, label %{then_l}, label %{else_l}")
        self.terminated = True
        self.block(then_l)
        # The arms read from the original dict, not the typed record: a
        # suspension return inside an arm carries the dict-only `suspend`
        # marker coro.py stamps after the node is built, and the record
        # round trip drops it (see `emit/_ir._items`).
        self.emit_body(_items(stmt, "then"))
        then_dead = self.terminated
        if not then_dead:
            self.line(f"br label %{merge_l}")
            self.terminated = True
        self.block(else_l)
        self.emit_body(_items(stmt, "else"))
        else_dead = self.terminated
        if not else_dead:
            self.line(f"br label %{merge_l}")
            self.terminated = True
        if then_dead and else_dead:
            self.terminated = True
            return
        self.block(merge_l)

    def emit_while(self, stmt: dict[str, Any]) -> None:
        node: Any = dict_to_node(stmt)
        cond_l = self.fresh("while.cond")
        body_l = self.fresh("while.body")
        end_l = self.fresh("while.end")
        self.line(f"br label %{cond_l}")
        self.terminated = True
        self.block(cond_l)
        cond = self.emit_expr(dict_to_dict(node.cond))
        self.line(f"br i1 {cond}, label %{body_l}, label %{end_l}")
        self.terminated = True
        self.block(body_l)
        self.emit_body(_items(stmt, "body"))
        if not self.terminated:
            self.line(f"br label %{cond_l}")
            self.terminated = True
        self.block(end_l)

    def emit_try(self, stmt: dict[str, Any]) -> None:
        self.needed.add("exc")
        # The child lists read from the original dict, not the typed record:
        # the `return` statements inside a try body carry the dict-only
        # `suspend` marker coro.py stamps after the node is built (the
        # `_restamp` owner decision), and the record round trip drops it, so
        # a suspension would emit as a return and the finally would run
        # before the resume (see `emit/_ir._items`).
        finalbody: list[dict[str, Any]] = _items(stmt, "finalbody")
        if finalbody:
            # E2 desugar: body+handlers+else become an inner try inside one
            # outline; the callsite runs `finalbody` on every outcome.
            inner: dict[str, Any] = {
                "kind": "try",
                "body": _items(stmt, "body"),
                "handlers": _items(stmt, "handlers"),
                "span": _span_of(stmt),
            }
            orelse: list[dict[str, Any]] = _items(stmt, "orelse")
            if orelse:
                inner["orelse"] = orelse
                for flag in ["brk", "cont"]:
                    flag_name: str | None = _opt_str_or_none(stmt, flag)
                    if flag_name is not None:
                        inner[flag] = flag_name
            outline, env_order = self._emit_outline([inner])
            env = self._pack_env(env_order)
            if self.flow_ptr is None:
                raise IRError("try has no flow slot")
            code = self._call_ret("i32", "ps_try_call", [f"ptr @{outline}", f"ptr {env}", f"ptr {self.flow_ptr}"])
            raised = self.tmp()
            self.line(f"{raised} = icmp ne i32 {code}, 0")
            raise_l = self.fresh("fin.raise")
            norm_l = self.fresh("fin.norm")
            merge_l = self.fresh("fin.merge")
            ret_l = self.fresh("fin.ret")
            self.line(f"br i1 {raised}, label %{raise_l}, label %{norm_l}")
            self.terminated = True
            self.block(raise_l)
            self._abi_decl("ps_exc_current", "ptr", [])
            self._abi_decl("ps_raise_exc", "void", ["ptr"])
            saved = self._call_ret("ptr", "ps_exc_current", [])
            self.emit_body(finalbody)
            if not self.terminated:
                self.line(f"call void @ps_raise_exc(ptr {saved}) noreturn")
                self.line("unreachable")
                self.terminated = True
            self.block(norm_l)
            flow = self._load("i32", self.flow_ptr)
            leave = self.tmp()
            self.line(f"{leave} = icmp eq i32 {flow}, 1")
            susp = self.tmp()
            self.line(f"{susp} = icmp eq i32 {flow}, 2")
            cont_l = self.fresh("fin.cont")
            susp_l = self.fresh("fin.susp")
            notret_l = self.fresh("fin.notret")
            # leave -> finally then return; susp -> the body suspended (a yield
            # inside the try), so the finally must NOT run and the suspension
            # propagates; otherwise -> finally then continue.
            self.line(f"br i1 {leave}, label %{ret_l}, label %{notret_l}")
            self.terminated = True
            self.block(notret_l)
            self.line(f"br i1 {susp}, label %{susp_l}, label %{cont_l}")
            self.terminated = True
            self.block(ret_l)
            self.emit_body(finalbody)
            if not self.terminated:
                self._emit_propagated_return()
            self.block(susp_l)
            # The suspension itself carries the value (the yield's element or
            # the resume status): the outline stored it in the return slot.
            self._emit_propagated_suspend()
            self.block(cont_l)
            self.emit_body(finalbody)
            if not self.terminated:
                self.line(f"br label %{merge_l}")
                self.terminated = True
            self.block(merge_l)
            return
        outline, env_order = self._emit_outline(_items(stmt, "body"))
        env = self._pack_env(env_order)
        if self.flow_ptr is None:
            raise IRError("try has no flow slot")
        code = self._call_ret("i32", "ps_try_call", [f"ptr @{outline}", f"ptr {env}", f"ptr {self.flow_ptr}"])
        raised = self.tmp()
        self.line(f"{raised} = icmp ne i32 {code}, 0")
        hand_l = self.fresh("try.handler")
        cont_l = self.fresh("try.cont")
        self.line(f"br i1 {raised}, label %{hand_l}, label %{cont_l}")
        self.terminated = True
        self.block(hand_l)
        merge_l = self.fresh("try.merge")
        ret_l = self.fresh("try.ret")
        ecode = self._call_ret("i32", "ps_exc_code", [])
        for index, handler in enumerate(_items(stmt, "handlers")):
            take_l = self.fresh(f"try.h{index}")
            miss_l = self.fresh("try.miss")
            cond = self._exc_match(ecode, dict_to_dict(handler))
            self.line(f"br i1 {cond}, label %{take_l}, label %{miss_l}")
            self.terminated = True
            self.block(take_l)
            slot = _opt_excslot(handler)
            if slot is not None:
                if slot not in self.slots:
                    # A slot the outline never packed: the compile must
                    # answer with a diagnostic, not a KeyError traceback
                    # (#530). The used-name walk packs every handler
                    # excslot, so this is a last resort.
                    raise IRError(f"except handler slot {slot} is not packed")
                # `except E as e` binding (or a bare-raise temp slot).
                self._abi_decl("ps_exc_current", "ptr", [])
                current = self._call_ret("ptr", "ps_exc_current", [])
                self.line(f"store ptr {current}, ptr {self.slots[slot][0]}")
            self.emit_body(_items(handler, "body"))
            if not self.terminated:
                self.line(f"br label %{merge_l}")
                self.terminated = True
            self.block(miss_l)
        self.line("call void @ps_reraise() noreturn")
        self.line("unreachable")
        self.terminated = True
        self.block(cont_l)
        flow = self._load("i32", self.flow_ptr)
        leave = self.tmp()
        self.line(f"{leave} = icmp eq i32 {flow}, 1")
        susp = self.tmp()
        self.line(f"{susp} = icmp eq i32 {flow}, 2")
        susp_l = self.fresh("try.susp")
        notret_l = self.fresh("try.notret")
        fell_l = self.fresh("try.fell")
        # leave -> the body returned: propagate the return.
        # susp  -> the body suspended (a yield inside the try): no else, and
        #          the suspension propagates.
        # else  -> the body fell through.
        self.line(f"br i1 {leave}, label %{ret_l}, label %{notret_l}")
        self.terminated = True
        self.block(notret_l)
        self.line(f"br i1 {susp}, label %{susp_l}, label %{fell_l}")
        self.terminated = True
        self.block(susp_l)
        # The suspension carries the value (the yield's element or the resume
        # status): the outline stored it in the return slot.
        self._emit_propagated_suspend()
        orelse_body: list[dict[str, Any]] = _items(stmt, "orelse")
        if orelse_body:
            else_l = self.fresh("try.else")
            self.block(fell_l)
            # else runs iff the body fell through: no raise, return, or jump.
            jumped: str = "false"
            for flag in ["brk", "cont"]:
                fname = _opt_str_or_none(stmt, flag)
                if fname is not None:
                    reg = self._load("i1", self.slots[fname][0])
                    if jumped == "false":
                        jumped = reg
                    else:
                        nxt = self.tmp()
                        self.line(f"{nxt} = or i1 {jumped}, {reg}")
                        jumped = nxt
            self.line(f"br i1 {jumped}, label %{merge_l}, label %{else_l}")
            self.terminated = True
            self.block(else_l)
            self.emit_body(orelse_body)
            if not self.terminated:
                self.line(f"br label %{merge_l}")
                self.terminated = True
        else:
            self.block(fell_l)
            self.line(f"br label %{merge_l}")
            self.terminated = True
        self.block(ret_l)
        self._emit_propagated_return()
        self.block(merge_l)

    def _emit_outline(self, body: list[dict[str, Any]]) -> tuple[str, list[str]]:
        """Emit the try body as its own function.

        Returns the function name and the slot layout of its env array. Only
        the slots the body reads or writes travel in the env array: every
        packed slot keeps its caller storage address-taken, which blocks
        register promotion for the whole enclosing function, so a hot loop
        that wraps a try per iteration would otherwise push every local of
        the loop body through memory. The callsite packs the env in exactly
        this order, so the two agree.
        """
        name = f"ps.try.{self.outline_n}"
        self.outline_n += 1
        saved_out = self.out
        saved_slots = self.slots
        saved_flow = self.flow_ptr
        saved_ret = self.ret_ptr
        saved_term = self.terminated
        saved_lid = self.lid
        saved_tid = self.tid
        saved_depth = self.outline_depth
        saved_order = self.slot_order
        saved_types = self.slot_types
        # The outline is its own function: its allocas hoist into its own
        # entry block, never into the caller's.
        saved_entry = self.in_entry
        saved_hoist = self.hoisted_allocas
        saved_at = self.hoist_at
        # A `None`-typed local has no storage (it is the singleton), so it
        # never travels in the env array.
        stored = {name for name, (ptr, _) in self.slots.items() if ptr}
        touched = sorted(_used_names(body) & stored)
        self.slot_order = touched
        self.slot_types = {name: self.slots[name][1] for name in touched}
        self.in_entry = True
        self.hoisted_allocas = []
        self.hoist_at = None
        self.out = []
        self.out.append(f"define internal i32 @{name}(ptr %env) {{")
        self.tid = 0
        self.lid = 0
        self.terminated = False
        self.outline_depth = saved_depth + 1
        self.block("entry")
        self._bind_env("%env")
        self.flow_ptr = self._alloca("i32")
        self.emit_body(body)
        if not self.terminated:
            self.line("ret i32 0")
        self._flush_allocas()
        self.out.append("}")
        self.out.append("")
        self.in_entry = saved_entry
        self.hoisted_allocas = saved_hoist
        self.hoist_at = saved_at
        self.outline_lines.extend(self.out)
        self.out = saved_out
        self.slots = saved_slots
        self.flow_ptr = saved_flow
        self.ret_ptr = saved_ret
        self.terminated = saved_term
        self.lid = saved_lid
        self.tid = saved_tid
        self.outline_depth = saved_depth
        self.slot_order = saved_order
        self.slot_types = saved_types
        return name, touched

    def _env_pointers(self, order: list[str] | None = None) -> list[str]:
        order = self.slot_order if order is None else order
        ptrs = [self.slots[name][0] for name in order]
        if self.ret_ptr is not None:
            ptrs.append(self.ret_ptr)
        return ptrs

    def _pack_ptrs(self, ptrs: list[str]) -> str:
        if not ptrs:
            return "null"
        env = self._alloca(f"[{len(ptrs)} x ptr]")
        for index, ptr in enumerate(ptrs):
            dest = env if index == 0 else self.tmp()
            if index != 0:
                self.line(f"{dest} = getelementptr ptr, ptr {env}, i64 {index}")
            self.line(f"store ptr {ptr}, ptr {dest}")
        return env

    def _pack_env(self, order: list[str] | None = None) -> str:
        return self._pack_ptrs(self._env_pointers(order))

    def _bind_env(self, env: str) -> None:
        self.slots = {}
        for index, name in enumerate(self.slot_order):
            loaded = self._load_env_ptr(env, index)
            self.slots[name] = (loaded, self.slot_types[name])
        if self.ret_llvm is not None:
            self.ret_ptr = self._load_env_ptr(env, len(self.slot_order))

    def _load_env_ptr(self, env: str, index: int) -> str:
        if index == 0:
            return self._load("ptr", env)
        gep = self.tmp()
        self.line(f"{gep} = getelementptr ptr, ptr {env}, i64 {index}")
        return self._load("ptr", gep)

    def emit_raise(self, stmt: dict[str, Any]) -> None:
        self.needed.add("exc")
        node: Any = dict_to_node(stmt)
        stmt_code: int | None = _opt_node_code(stmt)
        code = stmt_code if stmt_code is not None else EXCEPTIONS[node.exc]
        raise_message: dict[str, Any] | None = None
        if node.message is not None:
            raise_message = dict_to_dict(node.message)
        if node.flags & 8:  # F_RERAISE
            slot = _opt_excslot(stmt)
            if slot is not None:
                value = self.load(slot)
                self._abi_decl("ps_raise_exc", "void", ["ptr"])
                self.line(f"call void @ps_raise_exc(ptr {value}) noreturn")
            else:
                self.line("call void @ps_reraise() noreturn")
        elif node.cause is not None:
            # `raise E(args) from cause`: build the record, link the cause
            # (an excmake or bound `except ... as e` local), then raise.
            self._abi_decl("ps_exc_make", "ptr", ["i32", "i64", "ptr"])
            self._abi_decl("ps_exc_set_cause", "void", ["ptr", "ptr"])
            self._abi_decl("ps_raise_exc", "void", ["ptr"])
            made = self._call_ret("ptr", "ps_exc_make", [f"i32 {code}", "i64 0", "ptr null"])
            if raise_message is not None:
                msg = self.emit_expr(raise_message)
                mlen, mptr = self._buf_parts(msg)
                made = self._call_ret("ptr", "ps_exc_make", [f"i32 {code}", f"i64 {mlen}", f"ptr {mptr}"])
            cause: Any = node.cause
            if cause.kind == "excmake":
                cause_code: int | None = _opt_node_code(dict_to_dict(cause))
                ccode = cause_code if cause_code is not None else EXCEPTIONS[cause.exc]
                cause_value = self._call_ret("ptr", "ps_exc_make", [f"i32 {ccode}", "i64 0", "ptr null"])
                if cause.message is not None:
                    cmsg = self.emit_expr(dict_to_dict(cause.message))
                    clen, cptr = self._buf_parts(cmsg)
                    cause_value = self._call_ret("ptr", "ps_exc_make", [f"i32 {ccode}", f"i64 {clen}", f"ptr {cptr}"])
            elif cause.kind == "name":
                cause_value = self.load(cause.id)
            else:
                raise IRError("raise cause must be a name or excmake")
            self._call_void("ps_exc_set_cause", [f"ptr {made}", f"ptr {cause_value}"])
            self.line(f"call void @ps_raise_exc(ptr {made}) noreturn")
        elif node.exc == "SystemExit" and _is_exit_status(raise_message):
            # `raise SystemExit(3)` keeps its value typed: ps_raise_system_exit
            # stores it as the process status for the uncaught handler (#603).
            if raise_message is None:
                raise IRError("SystemExit status is missing")
            value = self.emit_expr(raise_message)
            rtyp = dict_to_type(_type_of(raise_message)).kind
            if rtyp == "bool":
                wide = self.tmp()
                self.line(f"{wide} = zext i1 {value} to i64")
                value = wide
            self._abi_decl("ps_raise_system_exit", "void", ["i32", "i64"])
            self.line(f"call void @ps_raise_system_exit(i32 {code}, i64 {value}) noreturn")
        elif raise_message is None:
            self.line(f"call void @ps_raise_code(i32 {code}) noreturn")
        else:
            message = self.emit_expr(raise_message)
            length, ptr = self._buf_parts(message)
            self.line(f"call void @ps_raise_msg(i32 {code}, i64 {length}, ptr {ptr}) noreturn")
        self.line("unreachable")

    def emit_setitem(self, stmt: dict[str, Any]) -> None:
        node: Any = dict_to_node(stmt)
        container_node: dict[str, Any] = dict_to_dict(node.object)
        index_node: dict[str, Any] = dict_to_dict(node.index)
        value_node: dict[str, Any] = dict_to_dict(node.value)
        container = self.emit_expr(container_node)
        index = self.emit_expr(index_node)
        value = self.emit_expr(value_node)
        kind: str = dict_to_type(_type_of(container_node)).kind
        if kind == "list":
            self.needed.add("seq")
            slot = self._stash_cell(_type_of(value_node), value)
            self.line(f"call void @ps_seq_set(ptr {container}, i64 {index}, ptr {slot})")
            return
        if kind == "dict":
            self.needed.add("map")
            key = self._stash_cell(_type_of(index_node), index)
            # Issue #781: an Any container's slot is unresolved; the write
            # site's value is concrete, and the cell takes the value under
            # its own proven type.
            slot = self._stash_cell(_type_of(value_node), value)
            self.line(f"call void @ps_map_put(ptr {container}, ptr {key}, ptr {slot})")
            return
        raise IRError(f"cannot assign into {kind}")

    def emit_setattr(self, stmt: dict[str, Any]) -> None:
        node: Any = dict_to_node(stmt)
        object_node: dict[str, Any] = dict_to_dict(node.object)
        value_node: dict[str, Any] = dict_to_dict(node.value)
        obj = self.emit_expr(object_node)
        value = self.emit_expr(value_node)
        class_name: str = dict_to_type(_type_of(object_node)).name
        field = self._field(class_name, node.name)
        slot = self._field_ptr(class_name, obj, field)
        field_type: dict[str, Any] = _field_type(field)
        field_kind: str = dict_to_type(field_type).kind
        if field_kind == "none":
            # A None field holds the singleton: one placeholder byte, zero.
            self.line(f"store i8 0, ptr {slot}")
            return
        if field_kind in {"str", "bytes"}:
            length = self.tmp()
            ptr = self.tmp()
            self.line(f"{length} = extractvalue %ps.str {value}, 0")
            self.line(f"{ptr} = extractvalue %ps.str {value}, 1")
            len_ptr = self.tmp()
            data_ptr = self.tmp()
            self.line(f"{len_ptr} = getelementptr %ps.buf, ptr {slot}, i32 0, i32 0")
            self.line(f"store i64 {length}, ptr {len_ptr}")
            self.line(f"{data_ptr} = getelementptr %ps.buf, ptr {slot}, i32 0, i32 1")
            self.line(f"store ptr {ptr}, ptr {data_ptr}")
            return
        self.line(f"store {_llvm_field_type(field_type)} {value}, ptr {slot}")

    def _exc_match(self, code: str, handler: dict[str, Any] | list[str]) -> str:
        h: Any = dict_to_node(handler) if isinstance(handler, dict) else None
        if h is not None and h.flags & 16:  # F_BARE
            return "true"
        names = handler if isinstance(handler, list) else h.exc
        checks: list[str] = []
        for name in names:
            wanted = EXCEPTIONS.get(name)
            if wanted is None:
                wanted = RESERVED_EXCEPTIONS.get(name)
            if wanted is None:
                continue
            call = self._call_ret("i32", "ps_exc_isa", [f"i32 {code}", f"i32 {wanted}"])
            checks.append(self._flag(call))
        numbers = list(h.codes) if h is not None and h.codes is not None else []
        if not numbers:
            numbers = [EXCEPTIONS[name] for name in names if name in EXCEPTIONS]
        for number in numbers:
            if number < USER_EXC_BASE:
                continue
            # A user class: the runtime walks its registered parent chain, so
            # `except Exception` and `except <its builtin base>` both match.
            reg = self.tmp()
            self.line(f"{reg} = icmp eq i32 {code}, {number}")
            checks.append(reg)
        acc: str | None = None
        for reg in checks:
            if acc is None:
                acc = reg
                continue
            nxt = self.tmp()
            self.line(f"{nxt} = or i1 {acc}, {reg}")
            acc = nxt
        if acc is None:
            raise IRError("except handler has no exception name")
        return acc


def _used_names(body: list[dict[str, Any]]) -> set[str]:
    """Every local name an IR fragment reads or writes.

    The walk is generic: it looks at the shapes the IR uses for a local
    (`name` in an expression, `target` in an assignment, `id` in a few
    statement kinds) and at the shapes that carry a local inside a larger
    node. Anything it does not recognize contributes nothing, so a new IR
    kind is a missed slot rather than a wrong answer; the caller intersects
    the result with the slots that exist.
    """
    names: set[str] = set()
    stack: list[Any] = [dict_to_node(item) for item in body]
    while stack:
        item: Any = stack.pop()
        if isinstance(item, list):
            stack.extend(item)
            continue
        if isinstance(item, dict):
            # A dict that the converter answers whole (the escape-hatch call
            # dicts: json_flags, print's sep/end) or refuses (a span or type
            # spelling): walk its children so the names they read still pack.
            try:
                converted: Any = dict_to_node(item)
            except TypeError:
                converted = item
            if isinstance(converted, dict):
                stack.extend(value for value in converted.values() if isinstance(value, (dict, list)))
                continue
            node: Any = converted
        else:
            node: Any = item
        if isinstance(item, Handler):
            # An except handler record has no `kind`: walk its body (the
            # names it reads must travel in the try outline's env array)
            # and keep its excslot: it stores the bound record before the
            # body emits beside the try's callsite, so the slot must be
            # packed even when no statement names it (#530).
            if item.excslot:
                names.add(item.excslot)
            stack.extend(item.body)
            continue
        if not hasattr(node, "kind"):
            continue
        kind: str = node.kind
        if kind == "name":
            names.add(node.id)
            continue
        if kind == "assign":
            names.add(node.target)
        elif kind in {"annassign", "augassign"}:
            names.add(node.target)
        elif kind == "for":
            names.add(node.target)
        elif kind == "with":
            names.add(node.target)
        elif kind == "excslot":
            names.add(node.id)
        elif node.handlers is not None:
            # An except handler: its excslot stores the bound record before
            # the body emits beside the try's callsite, so the slot must be
            # packed even when no statement names it (#530).
            for handler in node.handlers:
                if handler.excslot:
                    names.add(handler.excslot)
        for value in (
            node.span,
            node.type,
            node.value,
            node.object,
            node.callee,
            node.args,
            node.env,
            node.stub,
            node.thread_stub,
            node.thread,
            node.operand,
            node.left,
            node.right,
            node.hay,
            node.needle,
            node.elems,
            node.pairs,
            node.index,
            node.cond,
            node.then,
            node.else_,
            node.body,
            node.orelse,
            node.finalbody,
            node.handlers,
            node.expr,
            node.message,
            node.cause,
            node.target_node,
        ):
            if value is None:
                continue
            if isinstance(value, (IrNode, list)):
                stack.append(value)
    names.discard("")
    return names
