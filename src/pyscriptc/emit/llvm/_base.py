"""Typed emitter state and method signatures shared by the emit mixins.

Every mixin inherits `EmitterBase`, so `self.out`, `self.tmp(...)` and the sibling
mixins' methods resolve in each mixin file. The bodies are stubs: `Emitter` lists the
mixins before this base, so the real methods win. State is assigned in
`CoreMixin.__init__`.
"""

from __future__ import annotations

from typing import Any

from pyscriptc.dwarf import DwarfEmitter


class EmitterBase:
    module: dict[str, Any]
    entry_symbol: str
    dwarf: DwarfEmitter | None
    functions: dict[str, dict[str, Any]]
    out: list[str]
    globals: list[str]
    needed: set[str]
    string_data: list[bytes]
    tid: int
    lid: int
    slots: dict[str, tuple[str, dict[str, Any]]]
    terminated: bool
    try_depth: int
    outline_depth: int
    outline_n: int
    outline_lines: list[str]
    slot_order: list[str]
    slot_types: dict[str, dict[str, Any]]
    ret_ptr: str | None
    ret_llvm: str | None
    flow_ptr: str | None
    in_entry: bool
    hoisted_allocas: list[str]
    hoist_at: int | None
    class_index: dict[str, dict[str, Any]]
    json_n: int
    copy_n: int
    atexit_n: int
    abi_decls: dict[str, tuple[str, list[str]]]
    clo_wrappers: set[str]
    partials: dict[str, dict[str, Any]]
    partial_keys: dict[str, str]
    partial_n: int
    threads: dict[str, dict[str, Any]]
    thread_keys: dict[str, str]
    thread_n: int
    span: dict[str, Any] | None
    library: bool
    binding: bool

    def _ensure_arg_type(self) -> None:
        raise NotImplementedError

    def _store_ps_arg(self, base: str, value: str, typ: dict[str, Any]) -> None:
        raise NotImplementedError

    def _island_code(self, typ: dict[str, Any]) -> int:
        raise NotImplementedError

    def _island_pad(self, want: dict[str, Any]) -> int:
        raise NotImplementedError

    def _store_island_arg(self, base: str, value: str, typ: dict[str, Any], starred: bool = False) -> None:
        raise NotImplementedError

    def _island_name(self, expr: dict[str, Any]) -> tuple[int, str]:
        raise NotImplementedError

    def _prepare_island_call_args(
        self, pos_values: list[str], pos_args: list[dict[str, Any]], kw_values: list[str], kw_args: list[dict[str, Any]]
    ) -> tuple[str, str]:
        raise NotImplementedError

    def _emit_island_call(self, expr: dict[str, Any], values: list[str]) -> str:
        raise NotImplementedError

    def _emit_island_fn_call(self, expr: dict[str, Any], values: list[str]) -> str:
        raise NotImplementedError

    def _emit_island_getitem(self, expr: dict[str, Any], values: list[str]) -> str:
        raise NotImplementedError

    def _emit_island_seed(self, source: str) -> None:
        raise NotImplementedError

    def _emit_island_def_handle(self, expr: dict[str, Any]) -> str:
        raise NotImplementedError

    def _emit_island_seed_global(self) -> None:
        raise NotImplementedError

    def _emit_island_getattr(self, expr: dict[str, Any], values: list[str]) -> str:
        raise NotImplementedError

    def _emit_island_unmarshal(self, expr: dict[str, Any], values: list[str]) -> str:
        raise NotImplementedError

    def _load_island_result(self, out: str, want: dict[str, Any]) -> str:
        raise NotImplementedError

    def _buf_out(self, symbol: str, args: list[str]) -> str:
        raise NotImplementedError

    def _abi_decl(self, symbol: str, ret: str, params: list[str]) -> None:
        raise NotImplementedError

    def _abi_lines(self) -> list[str]:
        raise NotImplementedError

    def _call_void(self, name: str, args: list[str]) -> None:
        raise NotImplementedError

    def _flag(self, raw: str) -> str:
        raise NotImplementedError

    def _alloc_class(self, name: str) -> str:
        raise NotImplementedError

    def _c_pieces(self, value: str, typ: dict[str, Any]) -> list[tuple[str, str]]:
        raise NotImplementedError

    def _emit_abi(self, expr: dict[str, Any], values: list[str]) -> str:
        raise NotImplementedError

    def _emit_thread_new(self, expr: dict[str, Any]) -> str:
        raise NotImplementedError

    def _emit_to_thread_new(self, expr: dict[str, Any], values: list[str]) -> str:
        raise NotImplementedError

    def _emit_to_thread_start(self, expr: dict[str, Any], values: list[str]) -> str:
        raise NotImplementedError

    def _emit_list_push(self, expr: dict[str, Any], values: list[str]) -> str:
        raise NotImplementedError

    def _emit_atexit(self, expr: dict[str, Any]) -> str:
        raise NotImplementedError

    def _emit_exit(self, value: str) -> str:
        raise NotImplementedError

    def _emit_exit_message(self, message: str) -> str:
        raise NotImplementedError

    def _emit_join(self, values: list[str]) -> str:
        raise NotImplementedError

    def _emit_deque_store(self, expr: dict[str, Any], values: list[str]) -> str:
        raise NotImplementedError

    def _emit_deque_load(self, expr: dict[str, Any], values: list[str]) -> str:
        raise NotImplementedError

    def _emit_class_cmp(self, expr: dict[str, Any], left: str, right: str) -> str:
        raise NotImplementedError

    def _emit_class_binary(self, expr: dict[str, Any], left: str, right: str) -> str:
        raise NotImplementedError

    def _emit_minmax(self, name: str, values: list[str], arg_types: list[dict[str, Any]]) -> str:
        raise NotImplementedError

    def _emit_reduce(self, expr: dict[str, Any]) -> str:
        raise NotImplementedError

    def _seq_at(self, seq: str, index: str, elem: dict[str, Any]) -> str:
        raise NotImplementedError

    def _emit_partial_stub(self, symbol: str, spec: dict[str, Any]) -> None:
        raise NotImplementedError

    def _partial_symbol(self, spec: dict[str, Any]) -> str:
        raise NotImplementedError

    def _thread_symbol(self, spec: dict[str, Any]) -> str:
        raise NotImplementedError

    def _emit_thread_stub(self, symbol: str, spec: dict[str, Any]) -> None:
        raise NotImplementedError

    def _heap_cell(self, typ: dict[str, Any], value: str) -> str:
        raise NotImplementedError

    def _emit_task_result(self, expr: dict[str, Any], values: list[str]) -> str:
        raise NotImplementedError

    def emit_call(self, expr: dict[str, Any]) -> str:
        raise NotImplementedError

    def emit_builtin(self, name: str, values: list[str], arg_types: list[dict[str, Any]], expr: dict[str, Any]) -> str:
        raise NotImplementedError

    def emit_module_call(self, expr: dict[str, Any], values: list[str]) -> str:
        raise NotImplementedError

    def emit_method(self, expr: dict[str, Any], values: list[str]) -> str:
        raise NotImplementedError

    def emit_constructor(self, name: str, values: list[str]) -> str:
        raise NotImplementedError

    def emit(
        self, call: str | None, args: list[Any], standalone: bool = False, library: bool = False, binding: bool = False
    ) -> str:
        raise NotImplementedError

    def _render(self) -> str:
        raise NotImplementedError

    def _island_metadata(self) -> list[str]:
        raise NotImplementedError

    def _field(self, class_name: str, name: str) -> dict[str, Any]:
        raise NotImplementedError

    def _field_ptr(self, class_name: str, obj: str, field: dict[str, Any]) -> str:
        raise NotImplementedError

    def _stash(self, typ: dict[str, Any], value: str) -> str:
        raise NotImplementedError

    def _stash_cell(self, typ: dict[str, Any], value: str) -> str:
        raise NotImplementedError

    def _widen_cell(self, typ: dict[str, Any], slot: str, proven: dict[str, Any] | None = None) -> str:
        raise NotImplementedError

    def _alloca(self, llvm_type: str) -> str:
        raise NotImplementedError

    def _flush_allocas(self) -> None:
        raise NotImplementedError

    def _entry_end(self) -> int:
        raise NotImplementedError

    def _load(self, llvm_type: str, slot: str) -> str:
        raise NotImplementedError

    def _call_ret(self, ret: str, name: str, args: list[str]) -> str:
        raise NotImplementedError

    def _buf_parts(self, value: str) -> tuple[str, str]:
        raise NotImplementedError

    def _buf_cell_parts(self, value: str) -> tuple[str, str]:
        raise NotImplementedError

    def _buf_cmp(self, left: str, right: str) -> str:
        raise NotImplementedError

    def _buf_cmp_cells(self, left: str, right: str) -> str:
        raise NotImplementedError

    def _emit_clo_wrapper(self, fn: dict[str, Any]) -> None:
        raise NotImplementedError

    def store(self, name: str, value: str) -> None:
        raise NotImplementedError

    def load(self, name: str) -> str:
        raise NotImplementedError

    def block(self, name: str) -> None:
        raise NotImplementedError

    def fresh(self, hint: str) -> str:
        raise NotImplementedError

    def tmp(self) -> str:
        raise NotImplementedError

    def line(self, text: str) -> None:
        raise NotImplementedError

    def _dbg_suffix(self, text: str) -> str:
        raise NotImplementedError

    def _reset_function(self) -> None:
        raise NotImplementedError

    def emit_expr(self, expr: dict[str, Any]) -> str:
        raise NotImplementedError

    def emit_const(self, expr: dict[str, Any]) -> str:
        raise NotImplementedError

    def emit_binary(self, expr: dict[str, Any], left: str, right: str) -> str:
        raise NotImplementedError

    def emit_compare(self, expr: dict[str, Any]) -> str:
        raise NotImplementedError

    def _emit_identity(self, op: str, kind: str, left: str, right: str) -> str:
        raise NotImplementedError

    def _emit_none_side(self, expr: dict[str, Any]) -> None:
        raise NotImplementedError

    def emit_list(self, expr: dict[str, Any]) -> str:
        raise NotImplementedError

    def emit_dict(self, expr: dict[str, Any]) -> str:
        raise NotImplementedError

    def emit_subscript(self, expr: dict[str, Any]) -> str:
        raise NotImplementedError

    def emit_attr(self, expr: dict[str, Any]) -> str:
        raise NotImplementedError

    def emit_contains(self, expr: dict[str, Any]) -> str:
        raise NotImplementedError

    def emit_closure(self, expr: dict[str, Any]) -> str:
        raise NotImplementedError

    def emit_value_call(self, expr: dict[str, Any], values: list[str]) -> str:
        raise NotImplementedError

    def call_function(self, fn: dict[str, Any], values: list[str]) -> str:
        raise NotImplementedError

    def emit_function(self, fn: dict[str, Any]) -> None:
        raise NotImplementedError

    def emit_main(self, name: str, args: list[Any], library: bool = False) -> None:
        raise NotImplementedError

    def emit_noop_library_main(self) -> None:
        raise NotImplementedError

    def emit_top_island(self) -> str:
        raise NotImplementedError

    def _emit_island_top_seed(self) -> None:
        raise NotImplementedError

    def _emit_island_top_globals(self) -> None:
        raise NotImplementedError

    def _emit_exc_registrations(self) -> None:
        raise NotImplementedError

    def _declare_params(self, fn: dict[str, Any]) -> None:
        raise NotImplementedError

    def emit_body(self, body: list[dict[str, Any]]) -> None:
        raise NotImplementedError

    def _emit_return(self, stmt: dict[str, Any]) -> None:
        raise NotImplementedError

    def _emit_propagated_return(self) -> None:
        raise NotImplementedError

    def _emit_propagated_suspend(self) -> None:
        raise NotImplementedError

    def helper_call(self, name: str, ret: str, args: list[str], arg_type: str = "i64") -> str:
        raise NotImplementedError

    def string_value(self, text: str, binary: bool = False) -> str:
        raise NotImplementedError

    def materialize_arg(self, typ: dict[str, Any], value: Any) -> str:
        raise NotImplementedError

    def print_value(self, typ: dict[str, Any], value: str) -> None:
        raise NotImplementedError

    def _emit_print(self, values: list[str], arg_types: list[dict[str, Any]], expr: dict[str, Any]) -> None:
        raise NotImplementedError

    def _print_fmt(self, name: str, data: bytes, args: list[str] | None = None, err: bool = False) -> None:
        raise NotImplementedError

    def _trunc_i32(self, arg: str) -> str:
        raise NotImplementedError

    def _print_one(self, typ: dict[str, Any], value: str, err: bool) -> None:
        raise NotImplementedError

    def _is_tuple(self, name: str) -> bool:
        raise NotImplementedError

    def _print_tuple(self, name: str, value: str, err: bool) -> None:
        raise NotImplementedError

    def format_global(self, name: str, data: bytes) -> str:
        raise NotImplementedError

    def _emit_math(self, name: str, values: list[str]) -> str:
        raise NotImplementedError

    def _emit_re(self, name: str, values: list[str]) -> str:
        raise NotImplementedError

    def _emit_pattern(self, values: list[str]) -> str:
        raise NotImplementedError

    def _emit_b64(self, name: str, values: list[str]) -> str:
        raise NotImplementedError

    def _emit_hash(self, name: str, values: list[str]) -> str:
        raise NotImplementedError

    def _emit_url(self, name: str, values: list[str]) -> str:
        raise NotImplementedError

    def _emit_urlsplit(self, value: str) -> str:
        raise NotImplementedError

    def _emit_struct(self, name: str, values: list[str], args: list[dict[str, Any]], typ: dict[str, Any]) -> str:
        raise NotImplementedError

    def _emit_struct_pack(self, values: list[str], args: list[dict[str, Any]]) -> str:
        raise NotImplementedError

    def _emit_json_loads(self, text: str, typ: dict[str, Any]) -> str:
        raise NotImplementedError

    def _emit_json_dumps(self, value: str, typ: dict[str, Any], flags: int = 0) -> str:
        raise NotImplementedError

    def _json_global(self, typ: dict[str, Any]) -> tuple[str, int]:
        raise NotImplementedError

    def _json_alloca(self, typ: dict[str, Any]) -> str:
        raise NotImplementedError

    def _json_read(self, typ: dict[str, Any], slot: str) -> str:
        raise NotImplementedError

    def _json_write(self, typ: dict[str, Any], value: str) -> str:
        raise NotImplementedError

    def _emit_copy_deep(self, expr: dict[str, Any], values: list[str]) -> str:
        raise NotImplementedError

    def _copy_global(self, typ: dict[str, Any]) -> tuple[str, int]:
        raise NotImplementedError

    def emit_stmt(self, stmt: dict[str, Any]) -> None:
        raise NotImplementedError

    def emit_if(self, stmt: dict[str, Any]) -> None:
        raise NotImplementedError

    def emit_while(self, stmt: dict[str, Any]) -> None:
        raise NotImplementedError

    def emit_try(self, stmt: dict[str, Any]) -> None:
        raise NotImplementedError

    def _emit_outline(self, body: list[dict[str, Any]]) -> tuple[str, list[str]]:
        raise NotImplementedError

    def _env_pointers(self, order: list[str] | None = None) -> list[str]:
        raise NotImplementedError

    def _pack_ptrs(self, ptrs: list[str]) -> str:
        raise NotImplementedError

    def _pack_env(self, order: list[str] | None = None) -> str:
        raise NotImplementedError

    def _bind_env(self, env: str) -> None:
        raise NotImplementedError

    def _load_env_ptr(self, env: str, index: int) -> str:
        raise NotImplementedError

    def emit_raise(self, stmt: dict[str, Any]) -> None:
        raise NotImplementedError

    def emit_setitem(self, stmt: dict[str, Any]) -> None:
        raise NotImplementedError

    def emit_setattr(self, stmt: dict[str, Any]) -> None:
        raise NotImplementedError

    def _exc_match(self, code: str, handler: dict[str, Any] | list[str]) -> str:
        raise NotImplementedError
