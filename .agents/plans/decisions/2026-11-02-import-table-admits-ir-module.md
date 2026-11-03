# Draft recorded decision: the import table admits a named closed list of compiler-owned IR modules

Status: Approved 2026-11-03

## Decision (proposed)

The import table admits a named closed list of compiler-owned IR modules, each entry carrying a one-line reason recorded in the table itself. The initial list is the one below; admission of a new entry is a recorded decision that names the module and its reason, never a category or a pattern.

- `pyscriptc/_irnode.py` (new, R3 slice one): concrete, non-inheriting dataclasses forming the typed HIR; importable across `src/pyscriptc` module boundaries without PS1106, under a PS2002 import-table wording change scoped to exactly this module. Reason: the single seam every typed form of the strangler migrates through.
- `pyscriptc/tirserde.py` (exists on `work/tirgate`, 153 lines): the streaming canonical serializer. Reason: it must read IR node structures from every producer to hash them; it writes only digests, never IR values, back into compiler code.
- `pyscriptc/canonical.py` (167 lines): the dict-IR canonicalizer. Reason: the gate and the serializer both consume its canonical form; it imports nothing but stdlib today and the entry makes that boundary explicit instead of incidental.
- `pyscriptc/ir.py` (473 lines): the IR validators and callee/type node constructors. Reason: the R3 validators for the typed nodes land beside the dict validators; both sides of the strangler need to share one node vocabulary during migration.
- `pyscriptc/mir.py`, `pyscriptc/mirlower.py`, `pyscriptc/miremit.py`, `pyscriptc/mirverify.py` (planned, R4): the MIR representation and its lower/emit/verify halves. Reason: the MIR backend-only contract (sibling draft) needs the same cross-module dataclass import; admitting them on day one avoids reopening this decision mid-R4.

The list is closed: a module outside it that imports IR dataclasses still fences with PS1106/PS2002 exactly as today.

## Fence evidence (measured on master, 2026-11-03)

Measured with `uv run python -m benchmarks.compat2.census_self --json` (parse-only, `src/pyscriptc`, 94 modules, 42,336 lines, 2,698 fences total). Two families are import-boundary fences: `unlowered-type-name` (PS1106, "the annotation X names a type that is not lowered", which today fires on cross-module class annotations) with 375 fences across 30 modules, and the PS2002 import-table family ("X is not in the closed import table") with 65 fences across 58 modules. Together 440 fences, about 16% of the self-host census.

PS1106 (375) by compiler module: `lower/_classes` 54, `lower/_calls` 53, `lower/__init__` 49, `lower/_stmt` 46, `lower/_expr` 38, `lower/_stdlib` 20, `reduce` 15, `sigregistry` 13, `stubs` 13, `coro` 10, `package` 10, `lower/_core` 8, `typefacts` 7, `factsbridge` 6, `lower/_closures` 6, `telemetry` 6, `emitlib` 3, and 14 modules with 1-2 each (`check`, `cli`, `config`, `coverage`, `deps`, `diagnostics`, `dwarf`, `imports`, `mobile`, `lower/_argparse`, `lower/_shared`, `lower/_stdlib_shared`, `lower/_subprocess`). Most messages name `ast.*` types, which is the lowering-internal annotation shape; these clear as the lowering modules migrate to the typed nodes, not by admission itself.

PS2002 import-table (65) by compiler module: 44 modules with exactly 1 fence each (every `lower/_<stdlib>` module plus `check`, `cli`, `coro`, `coverage`, `deps`, `doctor`, `log`, `package`, `reduce`, `rtcache`, `stubs`, `telemetry`, `lower/_driver`, `lower/_folds`, `lower/_io`, `lower/_system`), and multi-fence modules `factsbridge` 3 (ast, importlib.metadata, mypy.nodes), `imports` 3 (ast, sysconfig, zipfile), `typefacts` 2 (ast, shlex), `check` 2, `lower/__init__` 2. Every one of these fences is a stdlib or third-party import refusal, not a compiler-module import: admission of the IR list does not clear them, and they are listed here so the amendment does not silently claim them.

## Why

Every typed form the rearchitecture depends on fails today on one rule: cross-module concrete dataclass import is the single change the irtype note identified as load-bearing, and the current import table plus PS1106 make it a fence. The reassessment quantifies the reach: 638 of the 732 PS1104 fences in `src/pyscriptc` are IR-dict annotations, and 1,925 IR-dict annotation sites across 84 files are the conversion denominator. None of it is reachable while the IR modules themselves cannot be imported.

The scope is deliberately narrow but named. A closed list keeps the closed table closed for user code, keeps the tiering policy's fence-at-the-boundary promise intact, and gives the strangler migration (R3/R4) named seams to migrate through instead of a rule change to reason about at every slice. The one-line reason per entry is the review artifact: an entry whose reason no longer holds is a removal candidate, and a new entry has to state its reason to be admitted.

## Alternatives rejected

- The AST-level bundler as the workaround (collapse `src/pyscriptc` into one module before compiling): removes the fence family (65 fences, 2.4% per the 2026-11-02 self-host receipt, `.agents/notes/2026-11-02-selfhost-bundle-receipt.md`) but not the analysis fences, adds name-collision and cycle-refusal machinery, and leaves every typed form blocked anyway. Retained as the R7 bundler, gated on R6, for a different purpose.
- Admitting all of `src/pyscriptc` into the import table at once: the table stops meaning anything and the fence-at-the-boundary promise for user code has no seam to review against.
- Admitting exactly one module (`_irnode.py` only, the 2026-11-02 wording): forces either a second recorded decision mid-R4 for the MIR half, or an over-broad `_irnode.py` that also carries MIR nodes, serializer internals and validators. The named list prices the whole migration's admissions up front, each with its reason.
- Keeping the dict IR and typing it in place (the prior plan's conservatism): closed by the reassessment under the owner mandate; the dict shape is what the fences are made of.

## What lands with it

- The import table gains one entry per listed module, not a category; each entry text carries the module path and the one-line reason verbatim.
- The PS2002 wording change names the admitted module paths and their reasons; user-level PS2002 behavior is unchanged.
- Modules planned but not yet written (`_irnode.py`, the four `mir*.py`) are admitted when they land, on the same recorded list; if one does not land, its entry lapses with the roadmap item.
- The recorded gate change, if any probe moves, goes through the new TIR gate per the serializer decision, not through the frozen `IR_HASHES.json`.

## What reopens the decision

Any compiler module needing admission that is not on the list above reopens it as a new entry with its own reason, and a user-facing consequence of the PS2002 wording reopens it too.

Where: the roadmap decision checklist row "Import table admits the compiler IR modules" blocks R3. The spec text for the modules' shapes is roadmap R3/R4 and the irtype note (`.agents/notes/2026-10-30-ir-typing-options.md` on `work/irtype`).
