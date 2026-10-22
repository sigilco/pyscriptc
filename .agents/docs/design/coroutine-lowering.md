# Coroutine lowering decision

Status: decided 2026-10-21 (issue #161). Scope: how generators, `async def` coroutines and async generators are lowered. Implementation lives in `src/pyscriptc/coro.py`; the event loop (later phase) lives in `src/pyscriptc/runtime/ps_loop.c`.

## Decision

Resumable functions are lowered as stackless state machines by a typed-IR pass (`coro.py`) that runs after `lower/` and before `llvm.py`. The pass rewrites each resumable function into a synthesized frame class (fields: params, locals, `state`, `value`, `pending` exception, `susp` flag) plus a resume method written entirely in existing IR kinds. Re-entry uses guards, because the IR is structured and has no goto: statements before the one holding the resume target are wrapped in `if self.state == 0`, a `while` that holds a target becomes `while self.state != 0 or cond`, and an `if` that holds a target tests `self.state in {targets} or (self.state == 0 and cond)`. Consumption reuses the existing `_for_dunder` path (a class with `__next__`, StopIteration code 8).

`llvm.coro.*` and stackful fibers are rejected.

## Why the IR guard transform

- The output is existing IR. No new IR kinds, no change to `llvm.py`, the validator, `canonical.py` or `IR_HASHES.json`, which are frozen gates. The P0 exit criterion is a byte-identical `irscore` A/B run on all existing probes.
- Try re-entry is free. Re-entering a structured `try` re-runs `ps_try_call` and re-establishes the setjmp frame, which is what a suspended frame needs. A `yield` inside `try` returns through the existing flow code 1.
- `gen.throw` and cancellation are just the `pending` field checked right after the resume label.
- A suspension never crosses a live setjmp frame, so `ps_top` stays consistent.

Costs accepted: one int compare per guarded construct on the hot path; all locals live in the heap frame (no mem2reg) until a copy-in/copy-out optimization lands; `finally` must be guarded with `if not self.susp` so a suspension does not run it.

An emitter-level `switch i32 %state` dispatch in `llvm.py` would be cheaper, but every `ps.try.N` outline is a separate function (each try holding a suspend needs its own nested dispatch), it needs a new IR kind under the frozen canonicity rules, and it grows a file with a single writer track. It stays a deferred option if the generator-versus-while-loop perf receipt misses 2x.

## Why not `llvm.coro.*`

CoroSplit must see the suspend points inside the presplit function. A `yield` inside `try` lives in the outlined `ps.try.N` function called through C `ps_try_call`, so it cannot be a suspend point of its parent, and suspending across a live `setjmp` frame is undefined anyway. Using the intrinsics would first require replacing setjmp exceptions with `invoke`/`landingpad` zero-cost EH. It would also tie correctness to the pass pipeline of whichever LLVM `clang` or `zig cc` ships, and make the frame layout opaque to the C event loop. Revisit only after a zero-cost EH migration, as a performance project.

## Why not stackful fibers

The runtime has no GC, which removes the usual conservative-scanning objection, but the other costs remain:

- Every generator object needs its own stack. A 64 KiB minimum with a guard page is about 100x a typical frame class, and real packages create many short generators.
- `ucontext` is deprecated on macOS and saves the signal mask with a syscall per switch. Custom asm is needed per target (x86_64 SysV, aarch64, Windows x64 and arm64).
- Growable stacks need split-stack support that LLVM no longer maintains.
- `ps_top` and `ps_current` would have to be swapped per fiber.
- Decisive: CPython 3.12+ checks C stack bounds, so running `ps_island_*` calls on a small foreign stack risks false or real stack overflows.

Fibers remain a possible later tool for blocking island calls, not for user generators.

## Event loop shape (later phase)

Runtime-internal awaitables (timers, fd readiness, thread futures) are hand-written C futures with the same header as compiled frames; user code never lowers to C source. `ps_loop.c` is one loop per thread with a ready deque, a binary-heap timer queue and a poller behind a small interface (kqueue, epoll, `poll()` fallback). `async def f() -> T` lowers to a frame class with `resume(self) -> i32` (0 pending, 1 done, exceptions propagate through setjmp as today) and a `result: T` field.

## Exceptions and codes

`CancelledError` is thrown into a frame through `pending` at the await point, so `try/finally` and `except CancelledError` work. `ps_current` is saved into the frame on suspend inside an `except` body and restored on resume. An abandoned generator never runs its `finally` (no destructors); `close()` is explicit. The codes for `GeneratorExit`, `StopAsyncIteration`, `CancelledError` and `TimeoutError` are reserved in the runtime without shifting `PS_USER_BASE` (issue #162), because shifting would renumber every user exception and change the IR hashes.

## Related

- [`ir-canonicity.md`](../spec/ir-canonicity.md): the frozen IR rules this decision avoids touching.
- [`e2-exception-model.md`](../spec/e2-exception-model.md): the setjmp exception model the guard transform relies on.
