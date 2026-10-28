# E2 exception model design

How exceptions become values in the subset: their C representation, the unwinding strategy, the lowering rules for `try`/`except`/`else`/`finally`, and the milestone ladder for E2. Spec only: nothing here is implemented. The E2 exit is 70 builtin and 172 stdlib exception rows, valid `raises` probes, PS1205.

One correction to the brief up front, because it changes the design: the brief says the runtime has no unwinding. It does. `ps_try_call` (`src/pyscriptc/runtime/ps_rt.c:82`) installs a `setjmp` handler on a `PsJmp` stack (`ps_rt.c:17-22`), `ps_raise` `longjmp`s into it (`ps_rt.c:61`), and `emit_try` (`src/pyscriptc/llvm.py:304`) already outlines every try body into a `ps.try.N` callback invoked through `ps_try_call`. E2 extends that machinery; it does not introduce unwinding.

## Current state, with evidence

- Seven exception codes exist. `EXCEPTIONS` in `src/pyscriptc/builtins.py:12-20` maps six builtin names plus `Exception` (the wildcard) to `i32` codes 1 to 7; the matching `PS_*` constants are `src/pyscriptc/runtime/ps_rt.h:13-19`. 
- `raise` lowers only `raise Name` and `raise Name("str")` (`src/pyscriptc/lower/_stmt.py:570-597`). `raise ... from` fences PS2002 (`_stmt.py:572`), bare `raise` fences PS2002 (`_stmt.py:574`). The emitted IR is `{"kind": "raise", "exc": <name>, "code"?: <user code>, "message": <expr|null>}` (`_stmt.py:590,595`), validated at `src/pyscriptc/ir.py:213-218`.
- `try` lowers only `try`/`except Name:`/`except (N1, N2):`. `try-else` and `finally` fence PS2002 (`_stmt.py:600-603`), `except ... as` and bare `except` fence PS1205 (`_stmt.py:606-609`). The IR is `{"kind": "try", "body": [...], "handlers": [{"exc": [names], "codes": [ints], "body": [...]}]}` (`_stmt.py:615-616`).
- Matching is code equality. `_exc_match` (`src/pyscriptc/llvm.py:488-509`) emits `icmp eq i32` per listed code, OR-ed; `Exception` in the name list is a wildcard (`code != 0`, `llvm.py:490-493`). There is no hierarchy: `except Exception` today means "anything raised", not "Exception and subclasses", which happens to agree only because a subclass relation barely exists.
- Handler code lists come from `_exception_codes` (`src/pyscriptc/lower/_classes.py:708-719`): the named builtin's code, plus the `exc_code` of every known user class that transitively inherits the name — subclasses are precomputed into the flat `codes` list at lowering time.
- User exception classes already exist in a limited form. A class whose base resolves to a lowered exception gets `info["exception"] = True` and a fresh `exc_code` from a counter starting at 8 (`_classes.py:211-226`, `src/pyscriptc/lower/__init__.py:99`). `raise` accepts them (`_stmt.py:591-595`) and emits `code` in place of a table lookup (`llvm.py:452`). `raise` does not call `__init__`, and an inherited field never assigned reads as `0` (the historical v1 roadmap behavior, since moved on; verify against `src/pyscriptc/lower/_classes.py` before relying on it).
- The runtime error channel is two thread-locals: `ps_exc` (the `i32` code) and `ps_msg` (a 256-byte message buffer) (`ps_rt.c:22-24`). `ps_raise_code`, `ps_raise_cstr`, `ps_raise_msg`, and `ps_reraise` fill them and `longjmp` (`ps_rt.c:64-115`). Uncaught, `ps_raise` prints `Name: message` or `Name` to stderr and `exit(1)` (`ps_rt.c:53-59`), with names resolved by the six-case `ps_exc_name` switch (`ps_rt.c:29-46`).
- `contextlib.suppress(X)` already desugars to a `try` IR node whose handler body is empty (`_stmt.py:633-640`) and emits a handler dict without a `codes` key, which falls back to the builtin table in `_exc_match` (`llvm.py:494-496`).

## 1. Runtime representation

**Exception value.** Replace the bare `ps_exc`/`ps_msg` pair with a heap-allocated exception record, and keep the `longjmp` payload an `i32` that encodes "exception pending" rather than the class code:

```c
typedef struct PsExc {
    int32_t code;      /* class code, table or user */
    int32_t flags;
    PsBuf name;        /* leaf class name, e.g. "ValueError" */
    PsBuf message;     /* str(e); empty when raised without a message */
    struct PsExc *cause;   /* raise ... from, NULL otherwise */
    PsSeq *args;       /* boxed args, see below */
} PsExc;
```

`ps_top` becomes a stack of `PsJmp` exactly as today (`ps_rt.c:17-22`); `longjmp` returns a fixed nonzero sentinel (`PS_RAISED = -1`) and the active `PsExc *` lives in a thread-local `ps_current`. This decouples the unwinding wire format (one sentinel) from the class identity (a field on the record), which is what allows the code space to grow past a `switch`. User-class payloads are an open question deferred to E3: the record reserves `PsSeq *args` now so `args` works for builtins immediately; binding a user exception to its class instance is specified in section 3 but its field layout lands with E3b.

**Code allocation.** Codes stay `i32`. The table below assigns the 70 builtins the dense range 1 to 71 (`Exception` keeps its wildcard role via the hierarchy, not via a magic code — see the note after the table). User exception classes allocate from a runtime-visible counter starting at **256** (`PS_USER_BASE`), leaving 72 to 255 as headroom for future builtins and internal sentinels without renumbering user classes across releases. Today's user counter starts at 8 (`lower/__init__.py:99`); E2 bumps the floor to 256 and records the decision in `docs/abi-strategy.md`, since exception codes cross the emitted-`.ll`/runtime boundary and therefore the ABI that document owns.

**Hierarchy.** Matching becomes "raised class is the listed class or a subclass", replacing the flat `codes` equality list. The runtime stores the parent chain as a static table `ps_exc_parent[72]` (`int32_t`, 0 for `BaseException`'s root); `ps_exc_isa(code, wanted)` walks it. For user classes the parent is known at lowering time (single inheritance, `_classes.py:218`), so lowering either emits the runtime call `ps_exc_isa` with a per-user parent registered at module init, or keeps the precomputed `codes` list — the design picks the former for builtins (one runtime helper, no list blowup) and keeps the `codes` list for user classes until E3 gives them runtime identity. `Exception` in a handler stops being the `code != 0` wildcard: it becomes an ordinary `isa` check against code 7, and `BaseException` becomes the true wildcard. This matters because the subset will grow `KeyboardInterrupt`/`SystemExit` (codes below), which a bare `except Exception` must not swallow.

Full 70-builtin table (PS code, parent). Codes are assigned in hierarchy order so the parent always has a smaller code than its children:

| Code | Class | Parent |
| --- | --- | --- |
| 1 | BaseException | (root, parent 0) |
| 2 | BaseExceptionGroup | BaseException |
| 3 | GeneratorExit | BaseException |
| 4 | KeyboardInterrupt | BaseException |
| 5 | SystemExit | BaseException |
| 6 | Exception | BaseException |
| 7 | ExceptionGroup | Exception |
| 8 | ArithmeticError | Exception |
| 9 | FloatingPointError | ArithmeticError |
| 10 | OverflowError | ArithmeticError |
| 11 | ZeroDivisionError | ArithmeticError |
| 12 | AssertionError | Exception |
| 13 | AttributeError | Exception |
| 14 | BufferError | Exception |
| 15 | EOFError | Exception |
| 16 | ImportError | Exception |
| 17 | ModuleNotFoundError | ImportError |
| 18 | LookupError | Exception |
| 19 | IndexError | LookupError |
| 20 | KeyError | LookupError |
| 21 | MemoryError | Exception |
| 22 | NameError | Exception |
| 23 | UnboundLocalError | NameError |
| 24 | OSError | Exception |
| 25 | BlockingIOError | OSError |
| 26 | ChildProcessError | OSError |
| 27 | ConnectionError | OSError |
| 28 | BrokenPipeError | ConnectionError |
| 29 | ConnectionAbortedError | ConnectionError |
| 30 | ConnectionRefusedError | ConnectionError |
| 31 | ConnectionResetError | ConnectionError |
| 32 | FileExistsError | OSError |
| 33 | FileNotFoundError | OSError |
| 34 | InterruptedError | OSError |
| 35 | IsADirectoryError | OSError |
| 36 | NotADirectoryError | OSError |
| 37 | PermissionError | OSError |
| 38 | ProcessLookupError | OSError |
| 39 | TimeoutError | OSError |
| 40 | ReferenceError | Exception |
| 41 | RuntimeError | Exception |
| 42 | NotImplementedError | RuntimeError |
| 43 | RecursionError | RuntimeError |
| 44 | StopAsyncIteration | Exception |
| 45 | StopIteration | Exception |
| 46 | SyntaxError | Exception |
| 47 | IndentationError | SyntaxError |
| 48 | TabError | IndentationError |
| 49 | SystemError | Exception |
| 50 | TypeError | Exception |
| 51 | ValueError | Exception |
| 52 | UnicodeError | ValueError |
| 53 | UnicodeDecodeError | UnicodeError |
| 54 | UnicodeEncodeError | UnicodeError |
| 55 | UnicodeTranslateError | UnicodeError |
| 56 | Warning | Exception |
| 57 | BytesWarning | Warning |
| 58 | DeprecationWarning | Warning |
| 59 | EncodingWarning | Warning |
| 60 | FutureWarning | Warning |
| 61 | ImportWarning | Warning |
| 62 | PendingDeprecationWarning | Warning |
| 63 | ResourceWarning | Warning |
| 64 | RuntimeWarning | Warning |
| 65 | SyntaxWarning | Warning |
| 66 | UnicodeWarning | Warning |
| 67 | UserWarning | Warning |
| 68 | EnvironmentError | alias of OSError (code 24) |
| 69 | IOError | alias of OSError (code 24) |
| 70 | WindowsError | alias of OSError (code 24); name emitted only on Windows targets, kept out of the subset |
| 71 | VMSError | reserved, never lowered (VMS-only legacy alias) |

That is 70 names total: 67 real classes (1 to 67), plus the three `OSError` aliases CPython keeps for compatibility. Codes 72 to 255 are reserved. Mapping to today's codes: `ValueError` moves 1 to 51, `IndexError` 2 to 19, `KeyError` 3 to 20, `OverflowError` 4 to 10, `ZeroDivisionError` 5 to 11, `RuntimeError` 6 to 41, `Exception` 7 to 6. The renumber is an ABI break and is absorbed inside E2 before any cross-binary `.ll` consumer depends on the current values; the alternative (append 8 to 71 after today's 1 to 7) is rejected because it splits siblings' codes across the table and forces every future builtin into user-code space.

**Runtime API.** `ps_raise_*` variants keep their signatures for every existing callsite in `ps_*.c` (the call set is large — over 30 sites use `ps_raise_cstr` alone, e.g. `ps_help.c:121-204`, `ps_dt.c:106-283`, `ps_b64.c:39-148`) but they now allocate a `PsExc` instead of writing `ps_exc`/`ps_msg`. New: `ps_exc_isa(i32 raised, i32 wanted) -> i32`, `ps_exc_name_for(i32 code) -> const char *` (table lookup replacing the `ps_exc_name` switch), `ps_exc_args(PsExc *) -> PsSeq *`, and `ps_exc_str(PsExc *) -> PsBuf`.

**User exception classes.** A class inheriting a lowered exception keeps `info["exception"]` and `exc_code` (`_classes.py:223-226`) but draws codes from 256+. Its `ps_exc_parent` entry is written by a module-init call `ps_exc_register(code, parent_code, name)` emitted next to the class's vtable/registration hook when E3 lands. Until then, `_exception_codes` keeps precomputing the subclass list (`_classes.py:708-719`), which remains correct because every reachable user subclass is known at lowering time. `raise` keeps not calling `__init__` (`_stmt.py:594` comment): the exception value is code plus message; `args` for user classes is the one-element list `[message]` until E3 binds instances.

## 2. Control flow: unwinding strategy

**Decision: keep the existing SJLJ scheme — `setjmp`/`longjmp` through `ps_try_call` — and extend it, rather than landing pads or return-code propagation.**

Options considered:

- **LLVM landing pads (`landingpad`/`invoke` + personality function).** Rejected. They require a target personality routine and unwind tables (`__gxx_personality_v0`-family, `libunwind`/libgcc-eh), which is exactly the machinery `zig cc` cannot guarantee on every `--target` triple; pulling in a C++ unwinder also drags libc++/libunwind link dependencies into a runtime that is otherwise plain C (see the cross-compile constraints in `docs/abi-strategy.md`, whose emit path `packaging/zig/emit.sh` is the documented way saved `.ll` is compiled). Landing pads would also rewrite `emit_try` wholesale for zero behavioral gain in a subset with no destructors.
- **Return-code propagation through every call.** Rejected. Every emitted function and every `ps_*` runtime call would need an out-band error return or a global checked after each call site. That is an ABI-wide change touching all ~30+ `ps_raise_cstr` callsites plus every emitted call instruction, costs a branch on the hot path of every call, and offers no benefit over `longjmp`, which already skips intervening frames.
- **SJLJ (kept).** `ps_try_call` already solves the frame-lifetime problem by keeping the `jmp_buf` in a live heap frame (`ps_rt.c:80-102`). Costs are one `malloc`+`setjmp` per `try` entered (already paid today), portable to every `zig cc` target, and zero ABI surface — signatures of emitted functions do not change. The known `setjmp` pitfalls (non-volatile locals, `longjmp` into dead frames) are structurally avoided: the try body is outlined (`llvm.py:306`, `_emit_outline` `llvm.py:381`), so handler-relevant locals live behind the env pack, and the `PsJmp` is popped before `ps_try_call` returns in both paths.

**Extension for `finally`.** `finally` needs the code to re-enter unwinding deliberately: when a `try` with a `finalbody` catches the sentinel, the runtime must run the finalbody and then continue the unwind if the body raised, returned, or fell through. Design: the outlined callback's `flow` out-param (today 0 = fell through, 1 = returned, `ps_rt.c:80-81`) gains codes `2 = break`, `3 = continue`, `4 = raised-with-PsExc` so `finally` can replay the exact control flow after running. `ps_try_call` returns the caught code only when `flow == 4`; other flows keep their current return path. This preserves today's contract that `return` inside `try` stores into `ret_ptr` and propagates (`llvm.py:336-342`, `_emit_propagated_return` `llvm.py:369-379`).

## 3. Lowering rules

### IR changes

The `try` node grows three optional fields; `ir.py` validation (`ir.py:218-226`) extends to match:

```python
{
    "kind": "try",
    "body": [...],
    "handlers": [
        {
            "exc": [names],
            "codes": [ints],
            "bind": "e" | None,  # NEW: except-as name
            "body": [...],
        }
    ],
    "orelse": [...],  # NEW: try-else body
    "finalbody": [...],  # NEW: finally body
    "span": ...,
}
```

`"bind"` is `None` when absent so older IR stays valid; `orelse`/`finalbody` default to `[]`. `STMT_KINDS` (`ir.py:33`) is unchanged — kind stays `"try"`.

A new stmt kind `"reraise"` is **not** added: bare `raise` inside a handler lowers to the existing `{"kind": "raise", "exc": "...", "reraise": true}` shape that `emit_raise` already emits (`llvm.py:453-454`), with the fence on bare `raise` (`_stmt.py:574`) relaxed only when statically inside a handler or `except`-bound scope; outside it stays PS2002.

### except-as binding

`except Name as e:` lowers `bind` to the target name (a plain `ast.Name` only; attribute/subscript targets fence PS2003). The bound value is the `PsExc`-backed exception object — until E3 gives exceptions instance layout, `e` has type `{"kind": "class", "name": <matched name>}` and supports exactly the surface below. CPython semantics: `e` is deleted when the handler exits, so lowering emits the binding as a fresh local marked deleted at handler end (an `assign` to a poison/`none` marker at the tail of the handler body, mirroring the `for` target rebinding discipline in `_bind_loop_target`, `_stmt.py:537-543`). Reading `e` after the handler is a NameError-class failure caught by the existing unbound-name check (`_stmt.py:337`).

### Surface on the exception object

- `str(e)` → `ps_exc_str`: the message, or the empty string.
- `e.args` → `ps_exc_args`: a `list` of one `str` element (the message) when raised with one, empty list otherwise. Multiple positional args (`raise ValueError(1, "x")`) stay fenced PS2003 (`_stmt.py:580-585` rule extends: non-str args fence).
- `repr(e)` / `str(e)` on a user class prints `Name: message` / `message` (CPython-compatible for the single-arg case).

`raise ... from cause` lowers `stmt.cause` to a `cause` field on the raise IR (`{"kind": "raise", ..., "cause": <exc expr|null>}`); `cause` must evaluate to an exception name/class or `None` (`raise X from None` suppresses the chain). The fence at `_stmt.py:572` relaxes accordingly; chains deeper than one level are stored but only the immediate cause prints (see report format).

### else and finally ordering

Semantics follow CPython exactly:

- `orelse` runs when `body` completes with no exception and no `return`/`break`/`continue` out. It does not run on any non-local exit.
- `finalbody` runs always: after `body` or `orelse` completes, after a handled exception's handler completes, and on the way out for an unhandled exception, a `return`, a `break`, or a `continue`. A `raise` or `return` inside `finalbody` replaces the in-flight outcome (CPython behavior); the design implements this by running `finalbody` inside the same outline frame and then replaying the saved `flow` code from section 2.
- `except` inside `finalbody` is allowed and nests normally (a `try` node inside `finalbody`).

### Uncaught-exception report

Keep the current contract and extend names: `ps_raise`'s uncaught path (`ps_rt.c:53-59`) prints `Name: message\n` (or `Name\n`) to stderr and exits 1. E2 changes the name resolution from the six-case `ps_exc_name` switch (`ps_rt.c:29-46`) to `ps_exc_name_for` on the full table, prints the CPython-style prefix line `Traceback (most recent call last):` suppressed — **decision: do not print a fake traceback**; the subset has no frames to list, so the report stays the two-line `Name[: message]` form plus, when `cause` is set, a preceding `The above exception was the direct cause of the following exception.` ordering note matching CPython's wording with the cause printed first. Exit code stays 1.

## 4. Compatibility with existing machinery

- **Mixed `and/or` + slices.** Untouched. `and`/`or` lowering lives in `_expr.py` and slice helpers in `ps_rt.c:690-770`; E2 adds no shared fields and no new expression kinds, so their IR is byte-stable.
- **IR hash churn.** `IR_HASHES.json` regen is required: handler dicts gain `bind`, `try` nodes gain `orelse`/`finalbody`, and the `Exception`-as-`isa` change alters `_exc_match` input whenever a handler names `Exception` or a builtin subclass appears. Per `AGENTS.md`, `probes/IR_HASHES.json` is frozen under `gates-v2`; E2 needs a recorded decision to regen it, then regenerates under the A/B byte-identical discipline: `uv run python -m benchmarks.compat2.irscore` twice, `cmp` the outputs, commit only on identical bytes. Sorting `codes` in emitted handlers is already flagged as needed in `.agents/docs/spec/ir-canonicity.md:114` and is folded into this epic's regen.
- **Frozen gates E2 must touch.** `probes/*.jsonl` gets new exception probes only via the sanctioned probe-regen path (recorded decision + regeneration, same discipline as IR hashes). `outcomes.py`, `score.py` weights, `TRIAGE.md`, `CALIBRATION*`, `canonical.py`, `benchmarks/compat/` are not touched: `raises` probes weigh 1.0 until E2 lands, and flipping that weight is itself a frozen-file change requiring its own recorded decision — it is listed here as an E2-epic-end action, not a design dependency.
- **suppress/nullcontext.** `contextlib.suppress` keeps desugaring to the same `try` shape (`_stmt.py:633-640`); with `Exception`-as-`isa` it gains subclass-correct matching for free once its handler gets `codes`.

## 5. Milestones

Each milestone is an iteration under the loop's keep/drop rules (`.agents/docs/loop.md:230-260`): verdict `PASS` plus its family-fence drop, with `uv run pytest` and `uv run ruff check && uv run ruff format --check` green on every commit.

M1 shipped with #52. M2 to M5 shipped with #186 (branch `feat/exceptions-m2-m5`). The "Status" column records where each one landed.

| Milestone | Contents | Gate commands | Status |
| --- | --- | --- | --- |
| M1: one builtin end to end | `PsExc` record, `ps_exc_isa` for the 7 existing codes, `ValueError` raised and caught by name and by `Exception` parent. Existing corpus must not regress. | `uv run pytest`; `uv run python -m benchmarks.compat2.irscore` (A/B byte-identical on unchanged IR surface) | shipped (#52) |
| M2: hierarchy table | Full 70-name table, `ps_exc_parent`, `ps_exc_name_for`, `ps_exc_register`, code renumber 1 to 71, user floor 256, ABI note in `docs/abi-strategy.md`. | `uv run pytest`; irscore A/B regen committed (recorded decision); container-baseline `uv run python -m benchmarks.baseline.run --check` | shipped (ba701bf) |
| M3: except-as + raise-from | `bind` on handlers, `e.args`, `str(e)`, deleted-after-handler scoping, `raise ... from` cause storage and print. PS1205 family fences drop. | `uv run pytest`; verdict candidate vs epic-start baseline (`--compile-budget 1.25`); PS1205 family fence count strictly down | shipped (951d4b7, 7d88de0) |
| M4: else + finally | `orelse`/`finalbody` on `try`, flow codes 2 to 4, replay semantics, nested try in finalbody. PS2002 `try-else`/`finally` fences drop. | `uv run pytest`; verdict candidate; family fence count strictly down | shipped (080c259) |
| M5: uncaught format | `ps_exc_name_for` in the uncaught path, cause-chain wording, exit-1 contract, stderr golden tests. | `uv run pytest`; verdict boundary: `PASS`, `primary_delta` > 0, flip-list hit rate >= 50% (`.agents/docs/loop.md:239`); `boxed_cost.py` ledger recorded | shipped (26fb804) |

Epic-close extras (not milestones): the `raises`-probe weight flip in `score.py` (weight-1.0 decision above), and the boundary evaluator's holdout aggregate recorded in the epic file.

## Out of scope

`except*`/exception groups beyond the table entries (needs version gating, track V), `KeyboardInterrupt`/`SystemExit` actual delivery (no signals/threads in the subset yet — they exist in the table so `except Exception` semantics are right), `e.__traceback__`, `__context__` implicit chaining, `try*` notes, and `raise` of arbitrary non-exception values (stays fenced).
