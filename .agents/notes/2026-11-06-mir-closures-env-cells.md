# M-4 (A11): closures and env cells in MIR

Date: 2026-11-06. Epic: #817. Milestone: v0.9.0. Docs only; no code in this note.

How nested defs, lambdas, `nonlocal`, and callable values become MIR without making MIR a semantic source of truth. Closure identity, capture sets, and `stub` / `thread_stub` specs stay HIR; MIR only packs the `{implementation, env}` cell the backend already emits.

Read first: `src/pyscriptc/mir.py`, `mirlower.py`, `miremit.py`, `mirverify.py`, `.agents/plans/decisions/2026-11-02-mir-backend-only-contract.md`, `lower/_closures.py`, `emit/llvm/_exprs.py` (`emit_closure`, `emit_value_call`), and M-1/M-2 notes for `MIR_S` / `MIR_P`. Generators and the island thread shim stay out of this slice (backend-only contract: generator semantics are HIR; `thread_stub` is a lowering of `threading`, not a MIR primitive).

Handoff baseline: closures are not in the 3-of-22 leaf slice. They are not required for the "95 percent after str and list" step. This note exists so the next MIR hour after containers has a written breakdown instead of inventing env cells in the instruction stream.

## What exists today

HIR / dict IR (`lower/_closures.py`): a callable value is a `closure` node: `name`, `env` (list of `EnvItem`), `type`, optional `stub` (partial) or `thread_stub`. An env item either names a local (the storage pointer is packed) or carries an `init` expression (value goes into a fresh heap cell). Nested defs swap a large `_FN_STATE` (locals, captures, nonlocals, boxed names, island handles, …) for the duration of the nested body. `nonlocal` writes hit the parent's slot through that capture.

Direct emit (`emit_closure`):

- Implementation: env-first functions use the function symbol; a plain function gets a lazy `name.clo` wrapper that drops the env; a `stub` gets a synthesized `functools.partial` shim (`_partial_symbol`).
- Env: `null` if empty; else `ps_alloc(8 * n)` and `store ptr cell` at each index. A named capture stores the *alloca pointer* of the parent slot (so writes are shared). An `init` capture stores `_heap_cell(type, emit_expr(init))`.
- Pair: `ps_alloc(16)`, store `@{impl}` at 0, env pointer at 1. Result is `ptr`.
- Call: `emit_value_call` loads `{impl, env}` and calls env-first.

The leaf MIR has no `ptr` tag today (M-2 proposes `MIR_P`), no alloc op, and no indirect call. `MIR_COPY` of a function name does not exist; calls that the leaf will grow (handoff: "after calls and short-circuit") are direct, known-symbol calls, not closure calls.

`fn.helpers` already carries a runtime helper name on a `MIR_COPY`. An indirect call cannot be a copy: it has a callee value plus N arguments.

## Proposed shape

Closures are `MIR_P` values (the 16-byte pair is a heap pointer, like list/dict). Env cells are also `MIR_P` (pointer to a slot). Do not add `MIR_CLO` / `MIR_ENV` tags: LLVM is `ptr` either way, and M-2's one-pointer-tag recommendation applies.

### Ops

Stay inside four ints plus the `arg3` M-2 may have added.

- **`MIR_ALLOC`.** `arg1` = size in bytes (`w` const), `dst` = `MIR_P`. Emitter: `ps_alloc`. Used for the pair (16) and the env (`8 * n`).
- **`MIR_STOREP`.** `arg1` = base `MIR_P`, `arg2` = index `w`, `arg3` = stored `MIR_P` (or a scalar cell pointer). `dst = -1`. Same three-operand problem as `SEQSET`; reuse `arg3`.
- **`MIR_LOADP`.** `arg1` = base, `arg2` = index, `dst` = `MIR_P`.
- **`MIR_CALLV`.** Indirect env-first call. `arg1` = pair, `arg2` = nargs as `w` const. Arguments cannot all fit in the instruction: they live in a parallel `call_args: dict[int, list[int]]` keyed by instruction index, walked in index order by `serialize()` as `callargs i [v…]`. That dict is insertion-ordered; the serializer already allows one dict in the shape (`helpers`). Digest covers the value ids. Do not put arg lists in `helpers` (omitted from the hash).
- **Direct calls** (when that slice lands) stay a different op or a helper copy with a known symbol; this note does not specify them beyond: a known-symbol call is not `MIR_CALLV`.

A `closure` node lowers to: alloc env (or `null` as a const pointer — see decision 1), store each cell, alloc pair, store impl and env, `dst` of the last store's pair pointer is the closure value. The impl symbol is *not* a MIR value; it is a helper/name on the store that writes slot 0 (`storep impl`), the same "target spelling" carve-out as `ps_seq_new`. Unlike helper names, the *symbol string* affects LLVM (`store ptr @foo`). If it is omitted from the digest, two closures that differ only in which function they wrap would hash equal. **The impl symbol must be in the canonical text.** Put it in the constant pool as a new `MIR_SYM` const kind (`const N y <nbytes> <hex>` of the symbol spelling, distinct from `MIR_S` user strings) or as a dedicated `impl {index} {symbol}` line on the function. Recommended: a function-level `impls` list parallel to `consts`, serialized as `impl {i} {name}` in insertion order, operand encoding a negative ref into that list *or* a nonnegative index stored in `arg3` of the impl store. Negative refs into a second pool collide with const-refs. **Recommended: `arg3` of the impl `STOREP` holds a nonnegative impl-table index, not a const-ref.** Verifier: that index exists.

`.clo` wrapper synthesis remains an emitter concern: if the HIR function has no `env`, the direct path sets `impl = fn_sym + ".clo"` and records `clo_wrappers`. `miremit` must do the same, from HIR, not from a MIR flag. MIR only sees the final impl index.

`stub` / partial shims: out of the first landing. `supported()` false if `"stub" in expr` or `"thread_stub" in expr`.

### Env cells vs SSA

A named capture is the *address* of a parent slot, not the current SSA value. The leaf emitter already demotes multi-use SSA values to alloca; captures force alloca even for single-use names that are captured. The MIR lowerer must:

1. Know the capture set before walking the parent body (HIR already has it).
2. Allocate a slot (`alloca` in emit, a `MIR_P` cell in MIR) for each captured name at function entry.
3. Every ordinary read/write of that name in the parent goes through `LOADP`/`STOREP` of the cell, not through a pure SSA `MIR_COPY`. That is a lowering change for the *parent*, not only the nested function. A/B identity depends on matching the direct path's slot decisions.

An `init` capture is a heap cell holding a value, not the parent alloca. Lower `init`, `_heap_cell`, store into env[i].

`nonlocal` in the nested function is `LOADP`/`STOREP` of `env[i]` (the cell the parent packed). The nested function's first parameter is the env pointer (`MIR_P`). That ABI is already env-first on the direct path.

### `supported()`

A function is in the closure slice when:

- every nested def/lambda in it is itself in the leaf+str+list slice (or this slice, recursively)
- no `stub` / `thread_stub`
- no yield (generators excluded)
- captures are names or `init` expressions the slice can lower

A module that mixes an in-slice nested function with an out-of-slice parent skips the parent (nested rides along as a skipped sibling, emitted direct). Do not half-lower a closure.

Fences: unchanged. Island handle bookkeeping in `_FN_STATE` is boxed (`boxed:begin` in `_closures.py`); a nested def that uses the island is out of this slice.

## Digest and frozen gates

- `callargs` lines and `impl` table lines are additive (omit when empty). Existing leaf fixtures keep their bytes.
- `MIR_ALLOC`/`STOREP`/`LOADP`/`CALLV` are new op ids; local int-literal tables in `mirlower`/`mirverify`/`miremit` must be updated together or A/B and MVS drift.
- LLVM goldens byte-identical, no re-bless. Wrapper symbols (`.clo`) and `ps_alloc` sizes (16, `8*n`) are the identity surface.
- Do not edit frozen IR/TIR probe files. If a change would move those hashes, stop.
- Census pins: only if the lowerer/emitter gain fences; repin with `scripts/repin_census.py`, never by hand.

## One-hour tasks

1. **Impl table + `MIR_ALLOC`/`STOREP`/`LOADP`.** No closures yet. Builder APIs, serializer lines, verifier (store to non-`P` is MVS01). A synthetic unit test that allocs 16 and stores two pointers; round-trip hash stable. Depends on M-2's `MIR_P` and `arg3`.
2. **Empty-env callable.** Lower `def f(x: int) -> int: ...` used as a value (`g = f` / passing `f`) to a pair with `env = null` and a `.clo` wrapper. One fixture. LLVM A/B vs `emit_closure`.
3. **Named captures.** Nested def that reads a parent `int`. Parent slot forced to alloca. Nested param 0 is env. Fixture.
4. **`nonlocal` write.** Nested def assigns the captured name. Shared cell. Fixture.
5. **`MIR_CALLV` + `callargs`.** Call the closure. nargs and arg value ids in the digest. Fixture combining 2 and a call.
6. **`init` captures and receipt.** Lambda with a default-like `init` cell. Record skipped: partial stubs, thread stubs, generators, island nested defs. IR/TIR untouched.

Stop at the first hour that does not finish. Tasks 2–5 each need M-2; task 1 can prototype on a hand-built `MirFunction` without the lowerer.

## Open decisions for the owner

1. **`null` env.** A `w` 0 const used as pointer vs a dedicated `const_null` producing a `MIR_P` const. Recommended: `const_p_null()` interned once, serialized as `const N p 0`. Do not reuse `const_w(0)` (verifier would type the env as `w`).
2. **Impl table vs `MIR_SYM` in the value pool.** Recommended: separate `impls` list so user string constants (`MIR_S`) cannot be confused with linker symbols.
3. **Force-alloca of captured names.** Recommended: yes, even when the nested function only reads once, because the direct path packs the slot pointer. Matching A/B beats a clever SSA capture.
4. **Partial/`thread_stub` in this slice.** Recommended: no. Separate hours; `thread_stub` touches boxed island code.
5. **Default lane.** No flip.

Blocked on: M-2 (`MIR_P`, `arg3`) for any lowering task. Not blocked on M-3 (`try`). Not blocked on M-1 unless a capture is `str` (task 3 can use `int`). If M-2 rejects `arg3`, `STOREP` has the same hole as `SEQSET` and this slice waits on that owner decision.
