# M-3 (A10): `try` / handlers as a block-argument CFG

Date: 2026-11-06. Epic: #817. Milestone: v0.9.0. Docs only; no code in this note.

How exception edges become MIR control flow without turning MIR into a semantic source of truth. Fences, exception codes, `finally`, and generator suspension already have a lowering on the direct path; this note maps that lowering onto the block-argument CFG the leaf already uses for `if` / `while`.

Read first: `src/pyscriptc/mir.py`, `mirlower.py`, `miremit.py`, `mirverify.py`, `.agents/plans/decisions/2026-11-02-mir-backend-only-contract.md`, `emit/llvm/_stmts.py` (`emit_try`), `runtime/ps_rt.c` (`ps_try_call`), and the generator/`with` decision `.agents/notes/decisions-and-platform.md` (generator `with` semantics). Generators stay on the HIR path until re-verified (backend-only contract, R5 checklist). This slice does not re-decide suspend-in-`with`.

## What exists today

MIR control flow is a CFG of basic blocks with typed block arguments (no phi nodes). Terminators: `MIR_JMP`, `MIR_BR`, `MIR_RET`, `MIR_RAISE`. `MIR_RAISE` is already a terminator: `call void @ps_raise_code(i32 code) noreturn; unreachable`. Its operand is a constant-pool word, the `EXCEPTIONS` table int spelled at the lowerer (`EXC_VALUE_ERROR = 51` for the leaf's `range` zero-step). There is no handler: a raise ends the function as far as MIR is concerned, and the runtime longjmps through `ps_try_call` if a `try` outlined the caller on the *direct* path.

The direct `try` is not LLVM `invoke`/`landingpad`. `ps_rt.c`: "A try runs its body as a callback: setjmp stays in `ps_try_call`, whose frame is still live if the body raises." `emit_try`:

1. Outline the body as a private function, pack live env pointers, call `ps_try_call(outline, env, flow_ptr)`.
2. Return 0 with `*flow` set (0 fell through, 1 return, 2 suspend); nonzero is the exception *code* (the `EXCEPTIONS` int).
3. Handler chain: `ps_exc_code()`, `_exc_match` per handler, bind `except E as e` from `ps_exc_current()`, miss falls through to `ps_reraise`.
4. `else` runs only on fallthrough (not raise, return, or break/continue flags).
5. `finally` is E2 desugar: body+handlers+else become an inner try inside one outline; the callsite runs `finalbody` on every outcome except suspension (flow 2 skips finally, matching issue #164 / the 2026-10-24 `with` decision).

Fences: `emit_llvm_mir` refuses a module with any fence. A `try` whose body is in-slice still cannot enter the MIR lane if a sibling function fenced. Out-of-slice `try` (unhandled construct inside the body) skips the whole function, same as today's leaf.

`mirverify` MVS03 already requires a terminator per block, existing successors, and `MIR_BR` block-argument arity/types. `MIR_JMP` to a block that has arguments is currently a violation ("jumps … without binding its N block arguments"): **only `MIR_BR` carries bindings**. That is the first CFG hole this slice has to close. A handler join and a `finally` merge are unconditional jumps that must pass the exception code or a fallthrough flag.

## Proposed mapping

Do not introduce LLVM landing pads in MIR. Byte identity with the direct path means the MIR emitter must still outline + `ps_try_call`. The CFG in MIR is the *body of the outline* plus the *handler chain at the callsite*, not a replacement for setjmp.

### Layer 1 — raise inside an in-slice function with no `try`

Already done: `MIR_RAISE` + `ps_raise_code`. No CFG change. Stays the leaf behaviour.

### Layer 2 — `try`/`except` without `finally`/`else` (first landing)

Lowering produces two MIR functions (or one function plus an outlined sibling, same as the direct path's private outline):

- **Outline function.** Its body is the try body, lowered with the leaf rules. Live names become env pointer parameters (`MIR_P` once M-2 exists; until then, a packed `ptr` passed as a word-sized pointer param — see decision 1). Terminators: `MIR_RET` of a flow code (`w` 0), or `MIR_RAISE`. No exception edges in this CFG: raise still goes to the runtime.
- **Callsite blocks.** One block calls `ps_try_call` (helper-backed copy producing `MIR_W` code). `MIR_BR` on `code != 0` to `handler` vs `cont`.
- **Handler chain.** A sequence of blocks. Each handler is a `MIR_BR` on `_exc_match(code, types)` (helper producing `MIR_B`) to `take` vs `miss`. `take` binds `excslot` (store of `ps_exc_current` into a name slot) and lowers the handler body, then `MIR_JMP` to `merge`. The last miss is `MIR_RAISE` of the current exception (`ps_reraise` helper, not a fresh code).
- **Merge.** Block arguments carry values the arms produced that are live out (the same problem `if` already solved with `br_args_*`). Because merge is reached by `MIR_JMP` from each `take` and from `cont`, **`MIR_JMP` must grow block-argument bindings** (decision 2).

Exception codes stay ints from `EXCEPTIONS` / `RESERVED_EXCEPTIONS`. Matching is a runtime helper, not a MIR-level subtype lattice (HIR already resolved the handler types). MIR does not interpret the code beyond passing it.

### Layer 3 — `else` and `finally`

Desugar in the lowerer the way E2 already did, *before* MIR sees the node: `finally` becomes "inner try outlined; callsite runs `finalbody` on raise and on normal, skips on flow 2". MIR then only implements Layer 2 plus:

- a flow word (`0/1/2`) loaded from the flow slot the outline wrote
- `MIR_BR` chain: leave → propagate return; susp → `_emit_propagated_suspend` equivalent; else → `orelse` then merge

Do not add `MIR_FINALLY` or `MIR_LANDING`. Those would become a second semantic language. The backend-only contract forbids it.

### Layer 4 — generators

Out of this slice. A `try` whose body yields is skipped (`supported()` false) until the owner re-verifies generators through MIR. The outline's flow 2 path is what the direct emitter uses; copying it without the coro transform would be a silent wrong lowering.

## Fences and exception codes

- A fenced module never enters the MIR lane. Unchanged.
- A raise of a type not in `EXCEPTIONS` / `RESERVED_EXCEPTIONS` is already a lowering fence on the HIR path. MIR only sees the integer. If a new code appears, it is a constant-pool `w`, not a schema change.
- `MIR_RAISE` of a non-const (reraise of `ps_exc_current`) needs an operand that is a value id of some exception *handle*, not a code. Today `emit_raise` only takes a const-ref code. Extend `MIR_RAISE` so `arg1` is either a `w` const (code) or a `MIR_P` current-exc pointer, with `arg2` a tag (0 = code, 1 = pointer), **or** add `MIR_RERAISE` with no operands. Recommended: `MIR_RERAISE` (arity 0), emitter spells `ps_reraise`. Keeps `MIR_RAISE` the leaf's const-code form. Verifier: `MIR_RERAISE` is a terminator; MVS03 lists it next to `MIR_RAISE`.

Codes stay out of the digest as *numbers in the pool*, not as names. Changing `EXCEPTIONS["ValueError"]` would move hashes; that table is not this slice.

## CFG holes in `mirverify` today

1. `MIR_JMP` cannot bind block arguments. Must add `jmp_args: list[int]` on `MirBlock` (or reuse `br_args_true` for the single successor). Serializer: a `jmpbind [v…]` line parallel to `brbind`. Only functions that jump to an args-block change their digest; existing leaf `if` uses `MIR_BR` bindings already.
2. `TERMINATORS` / verifier terminator lists must include `MIR_RERAISE` if added. Forgetting it is MVS03 "falls through" on a valid block. Mirror the local-int-literal spell in `mirlower` / `mirverify` (closed import table).
3. Outline functions are extra `MirFunction`s in the module. `module_serialize` walks `functions` in list order; insert outlines immediately after the owner function so the order is stable. Name them the way the direct path names outlines (the existing symbol scheme), or A/B identity on the LLVM `define` name fails even if the body matches.

## Digest, goldens, frozen files

- New terminator and `jmpbind` are additive for functions that do not use them. Existing leaf fixtures should keep MIR text if `jmpbind` is omitted when empty (like `brbind` today: only printed `if br_true or br_false`).
- LLVM goldens: byte-identical, no re-bless. The outline symbol, `ps_try_call` argument order, and handler label names (`try.handler`, `try.h0`, …) must match `_stmts.emit_try` and `fresh()`.
- Do not edit `IR_HASHES.json`, `TIR_HASHES.json`, probes, or `canonical.py`. Exception edges are backend CFG, not HIR.

## One-hour tasks

1. **`MIR_JMP` block-argument bindings.** `jmp_args` on the block, serializer `jmpbind` when nonempty, verifier arity/types, builder `emit_jmp(target, args)`. Extend a leaf `if` that already joins through `MIR_BR` is not enough: add a unit function that `jmp`s into a one-arg block. No `try` yet. Existing fixtures unchanged.
2. **`MIR_RERAISE`.** Op id, arity 0, terminator lists in `mir.py` / `miremit` / `mirverify` (local int literals). Verifier negative: missing terminator if a block ends in a non-terminator after a partial handler. Emitter: `ps_reraise` + `unreachable`.
3. **`try`/`except` Layer 2, int-only body.** `supported()` admits `try` whose body and handlers are leaf statements, no `else`/`finally`/yield. Outline + `ps_try_call` + one `except ValueError`. Fixture. LLVM A/B.
4. **`except E as e` and a second handler.** Bind `excslot`. Miss chain. Reraise on the last miss.
5. **`else` and `finally` via E2 desugar in the MIR lowerer** (do not emit new ops). One fixture with `finally` that prints; one with `else`. Confirm flow 1 (return inside try) still A/Bs. Yield inside try stays out of slice.
6. **Receipt.** Fixtures, whether `jmpbind` moved any leaf digest (should not, if omitted when empty), IR/TIR untouched, census pins.

Stop at the first hour that does not finish.

## Open decisions for the owner

1. **Outline env as `MIR_P` or as a word.** Until M-2, the packed env is a `ptr` the leaf cannot tag. Options: wait for `MIR_P`; treat the env as an untyped helper operand (style A from M-2); or skip `try` until M-2 merges. Recommended: helper-backed `ps_try_call` with the env pointer in `arg2` and the outline symbol in `fn.helpers`, so Layer 2 does not wait on M-2. The digest omits the outline *name* (helper) but includes the call op and the env value id.
2. **`MIR_RERAISE` vs overload `MIR_RAISE`.** Recommended: new op, arity 0.
3. **`jmp_args` vs using `MIR_BR` true-edge only.** Forcing every join through a dummy `br true` would preserve today's verifier but pollute A/B (extra `br i1 true`). Recommended: real `jmp` bindings.
4. **Generators in this slice.** Recommended: no. Owner re-verification, per the backend-only contract.
5. **Default lane.** No flip.

Blocked on: nothing for Layer 2 if decision 1 accepts helper-backed `ps_try_call`. Blocked on M-2 only if the owner requires `MIR_P` before any pointer operand. Implementation of Layer 3 is blocked on Layer 2 merging so outline naming is stable.
