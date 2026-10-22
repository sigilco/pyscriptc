# Backend contract: one IR, two emitters, one runtime ABI

The specification the emitters share. The LLVM emitter (`src/pyscriptc/emit/llvm/`, 3195 lines across its modules) is the consumer in the tree today; the C backend is the second consumer this document is written for. Every claim cites the file that makes it true. No behavior lives here: this page describes what the code already does, so a backend author can check their emitter against the same rules the LLVM emitter was checked against.

## The IR is the single source of truth

Both emitters are consumers of the canonical JSON IR (`src/pyscriptc/ir.py`), never producers and never validators of semantics. The facts:

- `IR_VERSION = 1` and `SUBSET = "gates-v1"` (`src/pyscriptc/ir.py:15`, `src/pyscriptc/ir.py:16`). `validate_module` rejects anything else (`src/pyscriptc/ir.py:341`, `src/pyscriptc/ir.py:343`).
- The IR carries a `cpython` stamp. `assert_same_cpython` refuses to emit a module stamped by a different interpreter than the one running the compiler (`src/pyscriptc/ir.py:453`).
- An `int` literal is a decimal string so JSON cannot round it; JavaScript-shaped kinds from scriptc (`f64`, `nullT` and the rest of `FORBIDDEN_KINDS`) are rejected (`src/pyscriptc/ir.py:37`, `src/pyscriptc/ir.py:100`).
- Types come from annotations on the AST. mypy is a preflight whose tree is thrown away; the IR is what a backend sees, and it already encodes every type decision.

The shared reading seam is `src/pyscriptc/emit/_ir.py`: the IR type to target-type mapping, the symbol naming rules (`fn_symbol`, `safe`), and the literal rules (`ir_const_value`, `prepare_arg`). Nothing in it imports LLVM-specific machinery, so a C backend consumes this module without pulling in the LLVM emitter (module docstring, `src/pyscriptc/emit/_ir.py:1`). A backend with a different word size or literal syntax supplies its own spelling; the IR walk around it does not change. The consequence, and the rule that prevents semantic drift: **a construct a backend cannot express is a bug in that backend.** It is never a fence (fences are born at lowering, before IR exists), and it is never a new IR kind invented to make a backend's life easier. New IR kinds are lowering decisions that both backends then must implement. If a backend cannot implement a kind, the backend is incomplete, and the golden corpus will say which kind.

The golden corpus makes per-kind coverage checkable: `tests/fixtures/backend/llvm/` holds one IR input plus byte-exact output per node kind, `tests/fixtures/backend/manifest.json` maps each fixture to the IR node kind it covers, and `tests/test_emitter_goldens.py` lists the kinds coverage dropped. The fixture layout is backend-neutral on purpose: a second backend adds its own expectations beside the same inputs (`ARCHITECTURE.md`, "The emitter golden corpus"). A new backend starts by rendering every existing fixture input and diffing its semantics, not its bytes, against what LLVM produces.

The in-memory seam for that work is `src/pyscriptc/emitter.py`: `emit_ir_text` validates one IR module the way a compile does and renders it to target text with no file, no subprocess and no toolchain.

## The runtime ABI is the second contract

The emitted text calls into C. That boundary is the real ABI, and both emitters compile against the same one.

### Struct layout

Fixed in `src/pyscriptc/runtime/ps_rt.h`. The header comment states the invariants (`src/pyscriptc/runtime/ps_rt.h:3`):

- `%ps.str` is `{ i64, ptr }`: a length and a pointer. Both emitters must build strings in this shape.
- `PsBuf` must stay 16 bytes (`src/pyscriptc/runtime/ps_rt.h:129`). It is passed and returned by value across the boundary.
- `PsSeq` keeps `len`, `elem`, `data`, then the appended `cap` (`src/pyscriptc/runtime/ps_rt.h:134`). Field order is frozen.

The compat rule for the structs is append-only: new fields go at the tail (`PsSeq.cap` is the precedent), new functions are additive, existing signatures are frozen. Any change to a `ps_*` signature or a `%ps.*` struct layout requires a `PS_ABI` epoch bump or an explicit no-bump note. The full policy, including the epoch model and what is deliberately not promised (no cross-version `.ll` compat), is `.agents/docs/ffi/abi-strategy.md`.

### The `ps_*` function surface

The runtime exposes the `ps_*` functions declared in `ps_rt.h` (108 declarations in that header). The emitted LLVM text declares only what it calls: the shared prelude is built in `src/pyscriptc/emit/llvm/_runtime.py` (`declare` lines from `src/pyscriptc/emit/llvm/_runtime.py:39` onward). A backend emits calls with C-ABI signatures against the same declarations; runtime `.c` implementations are shared verbatim.

The island subset (`ps_island_*`, linked only when the text contains that symbol, `src/pyscriptc/emit/llvm/_driver.py:131`) crosses into embedded CPython. `ps_island_check_output` is the `subprocess.check_output` path (`src/pyscriptc/stdmore.py:1464`, implemented at `src/pyscriptc/runtime/ps_island.c:72`).

### Exception codes

Exception class codes cross the boundary as `i32` literals in the emitted text, so the enum is part of this ABI. The dense builtin range 1 to 67 plus the three `OSError` aliases, `USER_EXC_BASE = 256` for user classes, 72 to 255 as headroom, and the reserved range at the top are defined in `src/pyscriptc/builtins.py:93` through `src/pyscriptc/builtins.py:175`. A backend must emit the same numbers. The renumber decision is recorded in `.agents/docs/ffi/abi-strategy.md` ("Exception codes").

## Per-kind semantics both emitters must reproduce

These are the rules a second emitter is checked against. Each one is what the LLVM emitter does today; the C backend implements the same semantics, spelled in C.

- **SSA and temp discipline.** Every expression result lives in a fresh temp; the emitters never reuse or reorder observations. The IR walk in `emit/_ir.py` is shared so both backends name symbols identically (`fn_symbol`, `src/pyscriptc/emit/_ir.py:42`).
- **`for` is already desugared.** Lowering turns `for` into `while` so the iterator is consumed once and in one place (`src/pyscriptc/lower/__init__.py:5`, the pass at `src/pyscriptc/lower/__init__.py:213`). A backend sees only `while`; if it finds a `for` in IR, that is a lowering bug, not something to handle.
- **Env closure cells.** Closures lower to explicit environment cells in the IR; both emitters materialize them as allocated memory. There is no backend-local closure representation to invent.
- **Class layout.** A class is a struct of its fields in declaration order, one base only. The layout decision lives in the IR, not in either emitter.
- **try/except.** A try runs its body as a callback: `setjmp` stays in `ps_try_call`, whose contract is one setjmp per frame in the runtime (`src/pyscriptc/runtime/ps_rt.c:501`, the header comment at `src/pyscriptc/runtime/ps_rt.c:4`). The emitter declares `i32 @ps_try_call(ptr, ptr, ptr)` (`src/pyscriptc/emit/llvm/_runtime.py:65`). A backend calls the same runtime function; it does not build its own unwinding.
- **Raises.** `ps_raise`, `ps_raise_code`, `ps_raise_msg` and `ps_raise_zdi` are the raise variants, declared noreturn (`src/pyscriptc/emit/llvm/_runtime.py:66`, `src/pyscriptc/emit/llvm/_runtime.py:41`). `ps_raise_code` takes the exception enum value (`src/pyscriptc/emit/llvm/_abi.py:316`).
- **Integer overflow.** Overflow is a trap, per divergence 1. The LLVM side uses `llvm.sadd.with.overflow.i64` and its siblings and raises `OverflowError` on the overflow bit (`src/pyscriptc/emit/llvm/_runtime.py:43` and the raise sites at `:202`, `:236`). The C-side equivalent is the checked-arithmetic pattern over the same `ps_raise*` calls: compute with the overflow bit, raise the same exception code on overflow. A literal that does not fit `i64` is `PS3002` at emission time, before any code runs.
- **Division and modulo.** Division by zero and `INT64_MIN / -1` trap through `ps_raise_zdi` with the operation-specific `ZDI_*` codes (`src/pyscriptc/emit/llvm/_runtime.py:233` through `:382`). Floor division is Python floor division, not C truncation; a C backend must not inherit C's `%` and `/`.

## What is shared versus what is duplicated

Shared, verbatim, one copy in the tree:

- the IR and its validator (`src/pyscriptc/ir.py`), the canonical-form rules in `.agents/docs/spec/ir-canonicity.md`,
- lowering (`src/pyscriptc/lower/`, `src/pyscriptc/restlower/`), including the fences: every `PSxxxx` is decided before emission,
- the shared IR-reading seam (`src/pyscriptc/emit/_ir.py`),
- the runtime: every `.c` source in `src/pyscriptc/runtime/` and `ps_rt.h`,
- the golden IR inputs in `tests/fixtures/backend/`.

Duplicated, once per backend, on purpose:

- the emitter implementations themselves (`src/pyscriptc/emit/llvm/` today; `src/pyscriptc/emit/c/` when it exists),
- the target spelling of types and literals (the `llvm_type` / `llvm_double` layer of the seam),
- the link entry point: each backend owns how its text becomes a binary (`link_binary` in `src/pyscriptc/emit/llvm/_driver.py:92` is the LLVM one, including the island link-line decision at `:131`).

## Divergence policy

`docs/reference/divergences.md` stays the single divergence list. A behavior difference between two compiled binaries from the same IR is a bug in one of the emitters, never a new divergence and never an IR change. Divergences are properties of lowering and the runtime (the Latin-1 string model, `i64` integers, the closed `re` subset), which both backends inherit for free by consuming the same IR and linking the same runtime. If a backend renders the same IR with different observable behavior, fix the backend; if fixing requires an IR change, that is a lowering-level decision both backends take together.
