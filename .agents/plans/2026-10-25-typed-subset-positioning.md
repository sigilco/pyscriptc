# Typed-subset positioning: island is the floor, native is the ceiling

Date: 2026-10-25. Status: decision plan, board edits ride with it. Owner decision session (explore mode), grounded on issues #93, #157, #174, #428, #430, #457 and milestones 3-10.

Supersedes: the owner-priority line in #430 ("library authors ship natively compiled wheels", 2026-10-23) and the pack-format track it parents. Does not supersede the release-cadence or compat2 gate decisions.

## Decision 1: scope is the typed subset, not CPython parity

pyscriptc compiles strictly-typed Python (PEP 484 surface, statically certifiable by the facts seam: pyrefly/mypy/pyright). Byte-for-byte CPython parity of dynamic semantics is retired as a goal; compat2's outcome receipts stay as the credibility mechanism for the subset we do compile (they measure the subset, not dynamic Python). The guarded-speculation/bailout architecture (deopt, OSR, `PyLong` bailout on overflow, frames-in-LLVM) from the earlier island-parity iteration is permanently dropped: never designed, never built, and it is the complexity class of a PyPy team.

Soundness posture for "your app won't break": refuse-by-default is the structural guarantee (closed tables: imports, builtins, call forms; unknown constructs fence, never guess), the soundness ratchet (`benchmarks/compat2/verdict.py`: no new wrong-output/crash, P0) is the process guarantee, and two new receipts close the remaining adversarial gaps (tickets filed with this plan):

- Must-fence adversarial corpus: constructs that MUST refuse, per T0-T3; a construct that silently compiles is a P0 soundness failure of the refusal claim.
- `pyscriptc verify`: compile, run the user's own program under CPython and native with the same inputs, diff outputs, emit a receipt. "Won't break" becomes checkable on the user's app at build time, not just on our corpus.

## Decision 2: the compat ladder — island is the floor, native is the ceiling

Every PyPI package gets a defined run mode at every maturity stage:

- **Floor (always defined):** the import runs in the CPython island — embedded interpreter, in-process, value-only marshalling (scalar/str/bytes/list across the boundary), CPython speed for that part, requires libpython. Compat never answers "impossible"; it answers "island".
- **Ceiling (earned per package):** when the package fits the typed subset it compiles native, lane `compile`, receipts attached.

`requests.get(url)` from a typed binary is the flagship of the floor: typed user code native, untyped PyPI dep on the island, no pack, no port. The per-package answer is visible in `pyscriptc imports FILE`: `native` / `native: fences (PS20xx …)` / `island (requires libpython)` — the ladder made user-visible before either lane is finished.

Framing guard: island-first must not become island-instead-of-lowering. The boxed ledger (`boxed_cost.py`, drop-flag rules in AGENTS.md) stays the arbitration; the island tier may not grow features while the flag mechanism says the compiler is starving.

The scriptc parallel: vercel-labs/scriptc shipped dynamic-fallback first, then dropped it for JS because bundlers could absorb it. Python's ecosystem cannot (runtime introspection is idiomatic: fastapi, pydantic, click, pytest), so we retain the island architecture as a permanent tier, not a transition state.

## Decision 3: two unidirectional interop seams, never bidirectional

Compiled ↔ island contact exists only as two one-way seams:

1. **compiled → island:** in-process embedded CPython. User's typed code is native; an untyped import (or a T1 construct: `eval`/`exec`/`__import__`, per refused-features.md) marshals values across into the island and back. Subprocess-shaped semantics, in-process cost. GIL discipline at this seam is ours (`PyGILState_Ensure`/`SaveThread`, #174): the only GIL in the product lives here.
2. **island → compiled (i.e. CPython consuming compiled code):** `pyscriptc --emit binding`. A loader `.py` + shared library via `ctypes` (no `Python.h`, no per-CPython-version ABI matrix; the py3-none-<platform> honesty argument from #457 survives the pack track's death). Feels to the host like a Rust lib with a Python binding.

There is no compiled → island → compiled call chain: values cross a seam exactly once per direction, ownership transfers at the crossing, no object identity survives it (island handles are opaque; native classes never appear as `PyObject*`). This one-way rule is what keeps the marshalling vocabulary (F1b/F1c) and the boxed ledger honest, and it is the reason a tracing GC never enters the picture.

## Decision 4: GIL by plane, not by product

- Compiled native code: no GIL exists. `ps_thread.c` phase 1 (real pthread mutexes, Lock/RLock/get_ident) shipped in v0.6.0; Thread/Event/Condition are phase 2 of the async-threads design. Runtime data-plane thread-safety is our build-out, never a user requirement.
- Island seam: GIL required on every entry, #174 is the receipt (island call from 4 threads, boxed tax 0).
- Binding seam: GIL-compatible by construction via ctypes; a future free-threaded host (PEP 703/779, 3.14t) is detectable (`sysconfig.Py_GIL_DISABLED`) and only changes the island-embed choice, not the contract.

Ecosystem worry resolved: pure-Python code is GIL-safe by construction under CPython — the GIL is what makes it so. C extensions that are not thread-safe behave identically inside our island as inside any interpreter; the island inherits CPython semantics, adds none.

## Decision 5: packs are dropped; curated packages become the narrow exception

The pack format track (#430 epic, #377 spec, #378 conformance, #456 wheel builder, #458 pure-wheel pilot, #411 migration, #152 umbrella) is closed as a track. Rationale: it recreates the curated-fork burden (perryTS's path), it requires codebase changes per pack, and both jobs it served now have cheaper homes — PyPI consumption goes through compile-from-source (typed) or the island floor (untyped), and CPython-side consumption goes through `--emit binding`.

Narrow exception, kept: already-shipped curated dists (`pyscriptc-orjson`, `pyscriptc-jwt`, sqlite pilot #66) continue as standalone packages with the import-alias rule (`import pyscriptc_sqlite3 as sqlite3`); `import sqlite3` itself stays lowered natively (stdlib never packs). A new curated dist requires high ROI plus codebase-level compat, decided per case. `stdlib_queue.py` untouched.

#428 rescopes from "compile the requests family natively" to the per-package ladder: the requests family is the island-floor flagship first (see Decision 6); native graduation happens per package only where it fits the subset (urllib3's pool is typed-but-stateful: assess, don't assert). certifi/idna stay the pure-compile pilots (#409 receipt unchanged).

## Decision 6: sequencing (the agreed milestone change)

- **v0.7.0 "libraries are consumable"** keeps its pure-dep leg (#398 landed, #409 receipt, #405 re-exports) and adds the island leg: #174 (GIL-safe entry) → #93 child "requests.get island pilot" (persistent session, generic import/call/getattr trampoline, per-import policy wiring) → #157 (island as per-import policy, coverage label, reach export). Exit criteria updated in the milestone description.
- **#430's wheel ambition** is retired, not moved.
- v0.8.0+ keeps the compute two-plane (FFI extern lane, ORT) unchanged; `--emit binding` slots beside it as the inverse seam.

## Decision 7: introspection handling (from the 2026-10-25 session notes)

Typed-but-reflective packages (fastapi: `inspect.signature`, `__annotations__` reads, import-time schema building) are the hard middle of the ladder: 100% annotated, not statically compilable. Handling, in order of honesty:

1. **Tier discipline unchanged:** they are T1-shaped — never lowered natively; they run on the island floor where real CPython reflection works. No fake `__annotations__` tables are invented to let a partial lowering pretend to be fastapi. The "static schema descriptor" idea (compile-time route extraction, frozen OpenAPI tables) is recorded as a Future option for a *specific* framework, per-package, only when demand justifies a descriptor generator — it is pack-thinking reborn and must pass the same high-ROI bar.
2. **Diagnostics tell the truth:** when a fence traces to runtime reflection, the message names the culprit (`inspect.signature` / `__annotations__` access / metaclass synthesis), points at the island lane, and, where a typed idiom exists, names it (PEP 692 `Unpack[TypedDict]` instead of `**kwargs`, `match` instead of `getattr` dispatch). The audit surface (`--debug=stages` today, coverage/imports JSON) gains a capability summary: what fraction lowered native vs. routed island, per file and per import.
3. **Metadata retention is a binding-seam concern only:** `--emit binding` preserves enough function surface (names, signatures via the `.pyi` vocabulary) for the host to call it; it does not promise `inspect.signature` parity inside compiled code.

## Compat-effort rule (compiler boundary)

The compiler never grows a special-case shim for untyped code. A stdlib/PyPI form lands one of three ways: typed lowering (genuinely compilable, demand-driven via the top100 corpus and the receipts), a lane (island/extern/binding), or a refusal with a diagnostic that names the culprit and the nearest typed idiom. The "bundler" layer (`deps.py`, `imports.py`, `config.py`) routes; `lower/` stays bounded.

## Unidirectionality and PEP 779 note (from the owner, recorded)

- compiled → island is in-process; island → compiled is lib/extension/FFI; both exist but neither is bidirectional. A call chain that crosses a seam twice in one direction is a design bug.
- PEP 779 free-threaded island embed: preemptively detectable (`Py_GIL_DISABLED` / `sysconfig.Py_GIL_DISABLED`), cheap to flag in `pyscriptc doctor`/binary metadata; adopt only as an option, never a requirement.

## Board edits riding with this plan

- Close: #430, #456, #458, #411, #152 (decision ref: this file). Rewrite: #457 → `feat: --emit binding, ctypes loader over the compiled shared library`. Rescope: #428 (ladder framing).
- File: #93 child "requests.get island pilot" (Next, with #174 → #157 prerequisites); must-fence adversarial corpus (Next); `pyscriptc verify` differential receipt (Next); introspection diagnostics + capability summary (Next); static schema descriptors (Future, gated per Decision 7.1).
- Milestone 4 (v0.7.0) description: island leg added to the theme's exit criteria.
