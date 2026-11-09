"""Statement emission: the statement dispatch and the control-flow shapes
(assignments, `if`, `while`, `try`, `raise`, `setitem`, `setattr`).
"""

from __future__ import annotations

from typing import Any

from pyscriptc.builtins import EXCEPTIONS, RESERVED_EXCEPTIONS, USER_EXC_BASE
from pyscriptc.emit._ir import (
    _field_type,
    _flag_of,
    _handler_codes,
    _handler_exc,
    _has,
    _items,
    _llvm_field_type,
    _maybe_node_kind,
    _maybe_node_type_kind,
    _node_body,
    _node_cond,
    _node_container,
    _node_else,
    _node_exc,
    _node_expr,
    _node_handlers,
    _node_id,
    _node_index,
    _node_kind,
    _node_name,
    _node_object,
    _node_target,
    _node_then,
    _node_type_kind,
    _node_value_node,
    _opt_excslot,
    _opt_node_cause,
    _opt_node_code,
    _opt_node_message,
    _opt_str,
    _opt_str_or_none,
    _span_of,
    _type_of,
)
from pyscriptc.emit.llvm._base import EmitterBase
from pyscriptc.errors import IRError
from pyscriptc.tirserde import dict_to_type


def _is_exit_status(node: dict[str, Any] | None) -> bool:
    """Whether `node` is an int-valued expression (a bool is an int in CPython).

    Only `raise SystemExit(<int>)` keeps its argument typed; every other
    exception message is the lowered `str()` of its argument.
    """
    if node is None:
        return False
    kind = _maybe_node_type_kind(node)
    return kind in {"int", "bool"}


class StmtsMixin(EmitterBase):
    def emit_stmt(self, stmt: dict[str, Any]) -> None:
        kind = _node_kind(stmt)
        if kind == "assign":
            value_node: dict[str, Any] = _node_value_node(stmt)
            if _node_type_kind(value_node) == "none":
                if _node_kind(value_node) != "const":
                    self.emit_expr(value_node)
                return
            self.store(_node_target(stmt), self.emit_expr(value_node))
        elif kind == "expr":
            expr_node: dict[str, Any] = _node_expr(stmt)
            if _node_kind(expr_node) == "const" and _node_type_kind(expr_node) == "none":
                return
            self.emit_expr(expr_node)
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
        cond = self.emit_expr(_node_cond(stmt))
        then_l = self.fresh("then")
        else_l = self.fresh("else")
        merge_l = self.fresh("merge")
        self.line(f"br i1 {cond}, label %{then_l}, label %{else_l}")
        self.terminated = True
        self.block(then_l)
        self.emit_body(_node_then(stmt))
        then_dead = self.terminated
        if not then_dead:
            self.line(f"br label %{merge_l}")
            self.terminated = True
        self.block(else_l)
        self.emit_body(_node_else(stmt))
        else_dead = self.terminated
        if not else_dead:
            self.line(f"br label %{merge_l}")
            self.terminated = True
        if then_dead and else_dead:
            self.terminated = True
            return
        self.block(merge_l)

    def emit_while(self, stmt: dict[str, Any]) -> None:
        cond_l = self.fresh("while.cond")
        body_l = self.fresh("while.body")
        end_l = self.fresh("while.end")
        self.line(f"br label %{cond_l}")
        self.terminated = True
        self.block(cond_l)
        cond = self.emit_expr(_node_cond(stmt))
        self.line(f"br i1 {cond}, label %{body_l}, label %{end_l}")
        self.terminated = True
        self.block(body_l)
        self.emit_body(_node_body(stmt))
        if not self.terminated:
            self.line(f"br label %{cond_l}")
            self.terminated = True
        self.block(end_l)

    def emit_try(self, stmt: dict[str, Any]) -> None:
        self.needed.add("exc")
        finalbody: list[dict[str, Any]] = _items(stmt, "finalbody")
        if finalbody:
            # E2 desugar: body+handlers+else become an inner try inside one
            # outline; the callsite runs `finalbody` on every outcome.
            inner = {
                "kind": "try",
                "body": _node_body(stmt),
                "handlers": _node_handlers(stmt),
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
        outline, env_order = self._emit_outline(_node_body(stmt))
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
        for index, handler in enumerate(_node_handlers(stmt)):
            take_l = self.fresh(f"try.h{index}")
            miss_l = self.fresh("try.miss")
            cond = self._exc_match(ecode, handler)
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
            self.emit_body(_node_body(handler))
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
        stmt_code: int | None = _opt_node_code(stmt)
        code = stmt_code if stmt_code is not None else EXCEPTIONS[_node_exc(stmt)]
        raise_message: dict[str, Any] | None = _opt_node_message(stmt)
        if _flag_of(stmt, "reraise"):
            slot = _opt_excslot(stmt)
            if slot is not None:
                value = self.load(slot)
                self._abi_decl("ps_raise_exc", "void", ["ptr"])
                self.line(f"call void @ps_raise_exc(ptr {value}) noreturn")
            else:
                self.line("call void @ps_reraise() noreturn")
        elif _opt_node_cause(stmt) is not None:
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
            cause = _opt_node_cause(stmt)
            if cause is None:
                raise IRError("raise cause must be a name or excmake")
            if _node_kind(cause) == "excmake":
                cause_code: int | None = _opt_node_code(cause)
                ccode = cause_code if cause_code is not None else EXCEPTIONS[_node_exc(cause)]
                cause_value = self._call_ret("ptr", "ps_exc_make", [f"i32 {ccode}", "i64 0", "ptr null"])
                cause_message: dict[str, Any] | None = _opt_node_message(cause)
                if cause_message is not None:
                    cmsg = self.emit_expr(cause_message)
                    clen, cptr = self._buf_parts(cmsg)
                    cause_value = self._call_ret("ptr", "ps_exc_make", [f"i32 {ccode}", f"i64 {clen}", f"ptr {cptr}"])
            elif _node_kind(cause) == "name":
                cause_value = self.load(_node_id(cause))
            else:
                raise IRError("raise cause must be a name or excmake")
            self._call_void("ps_exc_set_cause", [f"ptr {made}", f"ptr {cause_value}"])
            self.line(f"call void @ps_raise_exc(ptr {made}) noreturn")
        elif _node_exc(stmt) == "SystemExit" and _is_exit_status(raise_message):
            # `raise SystemExit(3)` keeps its value typed: ps_raise_system_exit
            # stores it as the process status for the uncaught handler (#603).
            if raise_message is None:
                raise IRError("SystemExit status is missing")
            value = self.emit_expr(raise_message)
            if _node_type_kind(raise_message) == "bool":
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
        container_node: dict[str, Any] = _node_container(stmt)
        index_node: dict[str, Any] = _node_index(stmt)
        value_node: dict[str, Any] = _node_value_node(stmt)
        container = self.emit_expr(container_node)
        index = self.emit_expr(index_node)
        value = self.emit_expr(value_node)
        kind = _node_type_kind(container_node)
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
        object_node: dict[str, Any] = _node_object(stmt)
        value_node: dict[str, Any] = _node_value_node(stmt)
        obj = self.emit_expr(object_node)
        value = self.emit_expr(value_node)
        class_name: str = dict_to_type(_type_of(object_node)).name
        field = self._field(class_name, _node_name(stmt))
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
        if isinstance(handler, dict) and _flag_of(handler, "bare"):
            return "true"
        names = handler if isinstance(handler, list) else _handler_exc(handler)
        checks: list[str] = []
        for name in names:
            wanted = EXCEPTIONS.get(name)
            if wanted is None:
                wanted = RESERVED_EXCEPTIONS.get(name)
            if wanted is None:
                continue
            call = self._call_ret("i32", "ps_exc_isa", [f"i32 {code}", f"i32 {wanted}"])
            checks.append(self._flag(call))
        numbers = _handler_codes(handler) if isinstance(handler, dict) else []
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
    stack: list[Any] = list(body)
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            stack.extend(node)
            continue
        if not isinstance(node, dict):
            continue
        kind = _maybe_node_kind(node)
        if kind == "name":
            names.add(_node_id(node))
            continue
        if kind == "assign":
            names.add(_node_target(node))
        elif kind in {"annassign", "augassign"}:
            names.add(_node_target(node))
        elif kind == "for":
            names.add(_opt_str(node, "target"))
        elif kind == "with":
            names.add(_opt_str(node, "target"))
        elif kind == "excslot":
            names.add(node)
        elif kind is None and _has(node, "excslot"):
            # An except handler: its excslot stores the bound record before
            # the body emits beside the try's callsite, so the slot must be
            # packed even when no statement names it (#530).
            slot = _opt_excslot(node)
            if slot is not None:
                names.add(slot)
        for key, value in node.items():
            if key in {"target", "id", "excslot"} and isinstance(value, str):
                continue
            if isinstance(value, (dict, list)):
                stack.append(value)
    names.discard("")
    return names
