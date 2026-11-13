# Threads plan: the P2b exit for #172 on real pthreads

Date: 2026-10-23. Status: written for issue #172; `queue.Queue` has since landed (`src/pyscriptc/restlower/_queue.py`, runtime queues in `ps_coll.c`) and the TSan lane is in CI. Verify current state against the code. Source of truth for the wider async and threads design: `.agents/docs/design/async-lowering-plan.md` (the P3 async lane) plus the milestone's staging table row P2b.

This document records what the runtime already does, what is still missing for the P2b exit criteria, the staged PRs that get there, the thread-safety story the runtime has to keep, and the WASM/WASI position. It is a design record, not a promise of scope: every stage below is separately shippable and separately revertable.

## Where the work stands today

The issue text is stale in one direction and the code is ahead of it in another.

Already landed (PRs #190 and #194, milestone `v0.5.0`):

- `threading.Lock`, `threading.RLock`, `threading.get_ident`, `threading.current_thread().name`, `threading.TIMEOUT_MAX`: native, backed by real `pthread_mutex_t` records in `src/pyscriptc/runtime/ps_thread.c`.
- `threading.Thread` with `target`, `args`, `name`, `daemon`, plus `start`, `join`, `is_alive`, `daemon`, `name`.
- `threading.Event`, `threading.Condition`, `threading.Semaphore`.

So the "first stage that can land safely" that this issue asks for is already in the tree. What is genuinely left for the P2b exit criteria of #172 is:

1. `queue.Queue` on pthreads (the queue module is not lowered at all today).
2. A ThreadSafe Audit of the runtime globals that a second thread can reach.
3. The island bridge: `ps_island_*` entry points called from a non-main thread.
4. The receipts: TSan clean on the threading suite, 8x10^6 locked increments exact, island call from 4 threads passes, boxed ledger tax 0 with drop_flag false.
5. The WASM/WASI position: no pthreads on the 0.2 lane, and the refusal has to be a clear diagnostic rather than a silent miscompile.

There is one correctness defect found while reading the code, described next, that has to be fixed before any of the receipts mean anything.

## The defect: an uncaught exception in a thread target kills the process

`src/pyscriptc/runtime/ps_thread.c:198-211` has the trampoline:

```c
static void *ps_thread_trampoline(void *arg) {
    PsThreadRec *rec = (PsThreadRec *)arg;
    void *pair = rec->pair;
    void **slots = (void **)pair;
    ps_call_fn impl = (ps_call_fn)slots[0];
    void *env = slots[1];
    impl(env);
    /* Publish completion: unlock the latch the joiner waits on. */
    pthread_mutex_unlock(&rec->latch);
    ...
}
```

The comment above it claims the target runs "inside the runtime's try machinery", but `impl(env)` is called directly. The emitted pair is a plain closure shim (`@pa.partial.0` in the emitted `.ll`), and `ps_try_call` is only emitted for `try` statements (`src/pyscriptc/emit/llvm/_stmts.py:103,143`). Nothing wraps the thread target.

The consequence: the exception state in the runtime is `_Thread_local` (`src/pyscriptc/runtime/ps_rt.c:22-23`, `ps_top` and `ps_current`). A fresh pthread starts with `ps_top == NULL`, so an exception raised in the target reaches `ps_deliver` (`src/pyscriptc/runtime/ps_rt.c:293-304`), which finds no handler, prints the traceback and calls `exit(1)`. The whole process dies, including the main thread, and the join latch is never released.

Reproduced on `origin/master` (commit `bd9a28c`) with:

```python
import threading


def boom() -> None:
    raise ValueError("boom in thread")


def checklist() -> int:
    t: threading.Thread = threading.Thread(target=boom, args=())
    t.start()
    t.join()
    return 0
```

The compiled binary prints `ValueError: boom in thread` and exits 1. CPython prints the thread's traceback, keeps the main thread running, and exits 0. Any program that relies on a worker thread dying alone is wrong today, and the P2b "island call from 4 threads passes" receipt would be measuring a process that can be killed by a worker.

This is the first thing to fix: the thread target needs its own root frame.

## The thread-safety story

### The memory model

The runtime has no refcounts, no GC and no GIL (`src/pyscriptc/runtime/ps_rt.c`, `ps_alloc` is `calloc` with no free). That removes the usual reason a GIL exists, and it is why the design doc chose SC-DRF (sequentially consistent for data-race-free programs) rather than trying to be CPython-compatible.

The contract to state plainly in the user docs:

- Synchronization through `Lock`, `RLock`, `Condition`, `Event`, `Semaphore`, `queue.Queue` and `Thread.join` is a full acquire/release barrier pair. A write made under a lock is visible to the next acquirer.
- Racy access to the same container (a `list`, `dict` or `str` builder) from two threads where at least one writes is undefined behavior and may crash. This differs from CPython, where `list.append` is atomic under the GIL. Documented as a numbered divergence.
- `Thread.join` is a barrier: every write the target made is visible to the joiner.

The barrier claim has to be true of the primitives that exist today, and today one of them is not:

- `Lock` and `RLock`: real `pthread_mutex_t`, so acquire/release is a full barrier pair. Correct.
- `Event`: `set` takes the mutex and broadcasts; `wait` polls the flag under the mutex. The poll loop reads the flag under the mutex, so the flag itself is race free. The barrier for *other* data written before `set` holds, because `set` releases the mutex and `wait` acquires it after observing the flag. There is a subtlety: `ps_event_wait` (`ps_thread.c:560-587`) polls with a 1 ms sleep and re-reads the flag under the mutex, so it does synchronize. Correct.
- `Semaphore`: `acquire` and `release` take the mutex. Correct.
- `Condition`: `wait` uses `pthread_cond_wait` (which releases the mutex), `notify` uses `pthread_cond_broadcast`. The timed path (`ps_thread.c:363-390`) does not use `pthread_cond_timedwait` (macOS does not ship it) and instead polls with `nanosleep`, which means a timed `wait` does not consume the condition and the predicate re-check loop in user code runs again. That matches CPython's semantics only loosely: CPython's timed `wait` returns False at the deadline. Here the timed wait returns False at the deadline too, so the user-visible result matches, but the wakeup is late by up to 1 ms and a `notify` that arrives during a timed wait is missed (the flag is re-read under the mutex, so the predicate is re-checked and the user's loop exits, but the wait call itself returns False). Document it, or fix it with a `pthread_cond_timedwait` shim. That is a stage decision, not a blocker.
- `Thread.join`: the latch mutex is locked by `start` and unlocked by the trampoline, so `join` acquiring it is a full barrier pair. Correct, except that the trampoline never unlocks it when the target raises (the defect above).

### The exception state

`ps_top` and `ps_current` are `_Thread_local`, so the exception machinery is already per thread. That is the right shape, and it is why the design doc's "GIL-less model" works at all. What is missing is the root frame per thread: a thread that raises with no handler must not reach `exit(1)`.

The fix has to give every thread a root frame whose handler prints the traceback, marks the thread as failed, and releases the join latch. Two options:

- Wrap the target in a `ps_try_call` at the runtime level: the trampoline calls `ps_try_call(target_entry, pair, &flow)`. `ps_try_call` is already exported (`ps_rt.c:510-530`) and already used by the emitter for `try`. This is the smallest change and keeps the exception unwinding inside one mechanism.
- Wrap at the emission level: the `ps.thread` closure shim calls `ps_try_call`. That keeps the runtime dumb but pushes the rule into the emitter, where every other thread-target shape would have to remember it.

The runtime-level wrap is the better one: the trampoline is the only place a foreign thread enters the runtime, so the root frame belongs there. `ps_deliver` then finds a handler (the trampoline's own), the trampoline reports the traceback and marks the record as failed.

CPython's `threading.excepthook` prints `Exception in thread Thread-1:` followed by the traceback, and the process keeps running. The compiled program should print the same shape to stderr and keep the exit code of the main thread.

### The runtime globals a second thread can reach

The design doc lists the unsynchronized globals. Checked against the tree on `origin/master`:

| Global | File | Reachable from a thread | Fix |
|---|---|---|---|
| `ps_top`, `ps_current` | `ps_rt.c:22-23` | yes | already `_Thread_local` |
| `mt`, `mt_idx`, `mt_seeded` | `ps_rand.c:15-17` | yes, `random` calls from any thread | per-instance mutex, or `_Thread_local` state seeded per thread (CPython's `random` is shared, so a mutex on the module instance is the faithful choice) |
| `ps_temp_cached`, `ps_temp_ready` | `ps_osmore.c:75-76` | yes, `tempfile.gettempdir` | `pthread_once` for the lazy fill |
| `ps_globals[64]`, `ps_global_n` | `ps_osmore.c:512-513` | yes, every module-level global read or write | one mutex around the slot table |
| `ps_argv_seq` | `ps_argv.c` | no, written once before `main` | leave |
| `ps_mix_lib`, `ps_mix_crc*` | `ps_mix.c` | yes, `nativeprobe` from a thread | `pthread_once` for the dlopen |
| island state | `ps_island.c` | yes | see the island stage |

`ps_globals` is the one that matters most for the P2b receipt: the locked-increment test in the design doc has the counter in a module-level global, and the global slot table is shared. A mutex around `ps_global_slot` plus the read and write paths gives the counter test a correct story. Note that the *slot* mutex only protects the table lookup, not the value: the value read and the value write are two separate calls, so a lock outside the counter increment still needs the user's `Lock`. That is exactly the SC-DRF contract and it is what the receipt measures.

The `random` state is the one that needs a decision, because `_Thread_local` would change behavior (CPython's module-level `random` is a shared instance whose sequence is shared across threads) and a mutex would serialize. The faithful choice is a mutex on the module-level instance; `_Thread_local` per-thread seeding is what `random.Random` instances get in CPython, not the module.

### The island bridge

Every `ps_island_*` entry point runs embedded CPython. CPython holds its own GIL, and the embedded interpreter is initialized on the first call (`ps_island.c:36-44`, `Py_InitializeEx(0)`).

Two problems when a second native thread calls in:

1. Initialization is not guarded. `ps_island_ensure` checks `Py_IsInitialized()` and then calls `Py_InitializeEx(0)`. Two threads racing here can both initialize, and the second one aborts the process inside CPython. Needs `pthread_once`.
2. The GIL is not released. `Py_InitializeEx` leaves the calling thread holding the GIL. Every other thread that calls into the island then blocks forever on the GIL, because the thread that initialized never released it. The fix is `PyEval_SaveThread()` after initialization, and `PyGILState_Ensure()` / `PyGILState_Release()` around each island call.

`ps_island.c` is boxed code (it embeds CPython), so every change there carries the `boxed:begin` / `boxed:end` markers and counts against the boxed ledger. The ledger rules are in `AGENTS.md`: code share may rise a little, churn and tax must stay flat, and the drop flag must stay false.

## The WASM/WASI position

The wasm target is in flight (PRs #249 and #313, not on `master`). The wasm design says the runtime calls `pthread_create` unconditionally and that "there is no wasm gating anywhere in `src/pyscriptc/runtime/`, so no compiler-side thread gate is needed: the sysroot decides whether threads exist".

That is true for the 0.2 lane and it is not good enough for us:

- zig's bundled wasi libc `pthread_create` returns `EAGAIN` unconditionally (the plan's own evidence). A program that spawns a thread on the 0.2 lane gets a runtime error at `start()`, with a message about the thread, not about the target.
- The 0.3 lane (wasi-sdk coop-threads) works, and the plan proves it: 4 threads created, joined, mutex-protected counter exact. So the feature is not impossible, it is a target-capability question.

The position this plan takes:

- The 0.2 lane has no threads. A `threading.Thread.start()` on that target must fail with a diagnostic that names the target and says what to use, not with a bare `EAGAIN` from inside libc.
- The 0.3 lane may have threads, once the wasi-sdk toolchain is a CI/reference toolchain. That is #312's lane, not this issue's.
- The fence belongs in the compiler, not only in the runtime, because the diagnostic has to be actionable.

Implementation shape, to be landed on top of the wasm branch rather than in conflict with it:

- The emitter already knows the target (`link_binary` takes `target`, `src/pyscriptc/emit/llvm/_driver.py:73-108`). The wasm branch has a refusal block for `ps_sub_`, `ps_mix_` and `ps_island_` symbols in the emitted text, with a message that names the target.
- The same block is the natural home for a `ps_thread_start` refusal: when the target is a wasm32 triple and the emitted text references the thread-start symbol, refuse with a message that names the target and points at the 0.3 lane.
- A lowerer-level fence (PS2001 on `threading.Thread`) would be wrong, because it would also fence the 0.3 lane. The refusal belongs at link time, where the target is known.

The refusal must not be a blocker for this issue. The threading work lands on the native lane first; the wasm refusal lands as its own small PR after the wasm branch merges, or as a stacked PR that the wasm author rebases.

## The staged PRs

Each stage is separately shippable. Each carries its own tests and its own receipts. The order is chosen so that the correctness fix lands before any receipt is measured.

### Stage A: the thread root frame (correctness, no new surface)

- `ps_thread.c`: the trampoline calls the target through `ps_try_call`, so a thread always has a root frame. The frame handler prints the CPython-shaped `Exception in thread <name>:` traceback to stderr, marks the record as failed, releases the join latch, and lets the process keep running.
- `Thread.join` on a failed thread returns, and the failure is observable (CPython re-raises nothing on `join`; the exception is reported by the excepthook). The compiled program matches: the traceback on stderr, the main thread's exit code untouched.
- Tests: a CPython-oracle case in `tests/test_threading_thread.py` that runs the same source under CPython and under the compiled binary and compares stderr and exit code.
- Receipt: the oracle case passes. The 8x10^6 locked increment count is unaffected.
- Why first: every later receipt measures a program whose workers can die. Measuring that with a broken worker is meaningless.

### Stage B: the runtime global audit (the shared state a second thread can reach)

- `ps_osmore.c`: one mutex around the `ps_globals` slot table and around the read and write paths of a slot value, so a module-level global is a coherent object rather than a racy field.
- `ps_osmore.c`: `pthread_once` for the temp-dir cache.
- `ps_mix.c`: `pthread_once` for the dlopen.
- `ps_rand.c`: a mutex on the module-level `random` instance state.
- Tests: a stress case where N threads update a module-level counter under a `Lock`, and the count is exact.
- Receipt: the count is exact for 8 threads and 10^6 increments each, and the run is clean under TSan (Stage D).
- Note on the boxed ledger: none of these files are boxed, so code share moves by zero and churn and tax stay flat.

### Stage C: `queue.Queue` on pthreads

- New runtime file `src/pyscriptc/runtime/ps_queue.c`: a bounded FIFO with a mutex and two condition variables (not empty, not full), an `unfinished` counter for `join`/`task_done`, and a `maxsize` of 0 meaning unbounded.
- New lowerer module `src/pyscriptc/restlower/_queue.py`: `queue.Queue`, `queue.LifoQueue`, `queue.SimpleQueue` constructors and the method surface `put`, `put_nowait`, `get`, `get_nowait`, `qsize`, `empty`, `full`, `task_done`, `join`, plus the `queue.Empty` and `queue.Full` exceptions.
- The module goes into `src/pyscriptc/stdlib_queue.py` `QUEUE` and `src/pyscriptc/stdmore.py` `REST_MODULES`, and the `tests/test_gates.py` `QUEUE` literal moves in lockstep.
- The element type comes from the annotation, the way `collections.deque` takes its element type, so `q: queue.Queue[int]`.
- Exceptions: `queue.Empty` and `queue.Full` need reserved codes. The exceptions epic reserved codes for `GeneratorExit`, `StopAsyncIteration`, `CancelledError` and `TimeoutError` without shifting `PS_USER_BASE`; `queue.Empty` and `queue.Full` are `Exception` subclasses and need the same treatment. If the reserved-code space is full, the queue surface can raise the existing `IndexError` and `ValueError` and document the divergence instead. That is a decision, and it is recorded here so it is made before the code is written.
- Receipt: the census fence count for `stdlib:queue` drops, and the `compat.json` and `compat-top100.md` tables are regenerated from the actual output.

### Stage D: the receipts (TSan, the stress count, the island call)

- A `--sanitize=thread` path. The compiler already has `--sanitize` (`src/pyscriptc/cli.py:90-93`) and the ASan+UBSan CI job is the template (`.github/workflows/ci.yml:237-278`). TSan needs its own CI job, because TSan and ASan cannot share a process and the TSan runtime is clang-only.
- The stress case: 8 threads, 10^6 locked increments each, the count is exactly 8,000,000.
- The island case: 4 threads each call an island function (for example `socket.gethostname()` or `logging.info`), the program completes and the output matches CPython.
- The boxed ledger at the boundary: `uv run python benchmarks/compat2/boxed_cost.py --window-commits 10`, with the numbers recorded in `AGENTS.md`.

### Stage E: the island bridge (boxed)

- `ps_island.c`: `pthread_once` around `Py_InitializeEx`, `PyEval_SaveThread()` after it, and `PyGILState_Ensure` / `PyGILState_Release` around each entry point.
- Every change in that file carries the `boxed:begin` / `boxed:end` markers.
- Receipt: the island call from 4 threads passes, and the boxed ledger shows tax 0 with drop_flag false.

### Stage F: the WASM refusal (stacked on the wasm branch)

- The link-time refusal for `ps_thread_start` on a wasm32 target, with a diagnostic that names the target.
- The divergence entry in `docs/reference/divergences.md`.

## What is out of scope

- `multiprocessing`: out of scope through v0.9.0 (needs process isolation and shared memory).
- `concurrent.futures.ThreadPoolExecutor`: a separate issue, and it depends on Stage A and Stage C landing first.
- `threading.local`: a `_Thread_local` keyed slot map. Separate issue.
- `threading.Timer`: a separate issue.
- `asyncio` `Lock` / `Event` / `Queue`: the async lane, not this one.
- A GIL, or any attempt to make container mutation atomic. The design doc chose SC-DRF and the divergence is documented.

## The receipts, restated

The P2b exit criteria of #172, and where each one lands:

| Criterion | Stage | Where the number goes |
|---|---|---|
| TSan clean on the threading suite | D | the CI job output |
| 8x10^6 locked increments exact | B, D | the stress test output |
| Island call from 4 threads passes | E | the island test output |
| Boxed ledger tax 0, drop_flag false | D, E | `AGENTS.md`, after `boxed_cost.py` |

## Decisions this plan needs from the owner

1. The queue exception codes. `queue.Empty` and `queue.Full` need reserved codes in the exception table, or the queue surface raises `IndexError` / `ValueError` and documents the divergence. The plan prefers the reserved codes, because the oracle tests compare the exception type.
2. The `random` module-level state under threads: one mutex on the shared instance (faithful to CPython) or per-thread state (faster, different). The plan prefers the mutex.
3. The wasm refusal timing: a stacked PR on the wasm branch, or a wait for #312 to merge. The plan prefers the stacked PR, because the refusal is small and the diagnostic is better in the tree than in a document.
