# Draft recorded decision: the import table admits the compiler-owned IR module

Status: DRAFT for owner sign-off, 2026-11-02. On approval this is the decision row that unblocks roadmap R3 (typed HIR strangler); until then no import-table code changes. Input: `.agents/plans/2026-11-02-ir-rearchitecture-roadmap.md` (owner-approved framing) and `.agents/notes/2026-11-02-ir-rearchitecture-reassessment.md`.

## Decision (proposed)

The import table admits one additional compiler-owned module, `pyscriptc/_irnode.py`: concrete, non-inheriting dataclasses forming the typed HIR, importable across `src/pyscriptc` module boundaries without PS1106, under a PS2002 import-table wording change scoped to exactly this module.

## Why

Every typed form the rearchitecture depends on fails today on one rule: cross-module concrete dataclass import is the single change the irtype note identified as load-bearing, and the current import table plus PS1106 make it a fence. The reassessment quantifies the reach: 638 of the 732 PS1104 fences in `src/pyscriptc` are IR-dict annotations, and 1,925 IR-dict annotation sites across 84 files are the conversion denominator. None of it is reachable while the IR module itself cannot be imported.

The scope is deliberately narrow. Admitting one compiler-owned module keeps the closed table closed for user code, keeps the tiering policy's fence-at-the-boundary promise intact, and gives the strangler migration (R3) one seam to migrate through instead of a rule change to reason about at every slice.

## Alternatives rejected

- The AST-level bundler as the workaround (collapse `src/pyscriptc` into one module before compiling): removes the fence family (65 fences, 2.4% per the 2026-11-02 self-host receipt, `.agents/notes/2026-11-02-selfhost-bundle-receipt.md`) but not the analysis fences, adds name-collision and cycle-refusal machinery, and leaves every typed form blocked anyway. Retained as the R7 bundler, gated on R6, for a different purpose.
- Admitting all of `src/pyscriptc` into the import table at once: the table stops meaning anything and the fence-at-the-boundary promise for user code has no seam to review against.
- Keeping the dict IR and typing it in place (the prior plan's conservatism): closed by the reassessment under the owner mandate; the dict shape is what the fences are made of.

## What lands with it

- `pyscriptc/_irnode.py`, written in R3 slice one, is the only admitted module; the import table gains one entry, not a category.
- The PS2002 wording change names the module path and the reason; user-level PS2002 behavior is unchanged.
- The recorded gate change, if any probe moves, goes through the new TIR gate per the serializer decision, not through the frozen `IR_HASHES.json`.

## What reopens the decision

A second compiler module needing the same admission, or a user-facing consequence of the PS2002 wording, each reopens it as a new entry.

Where: the roadmap decision checklist row "Import table admits the compiler IR module" blocks R3. The spec text for the module's shape is roadmap R3 and the irtype note (`.agents/notes/2026-10-30-ir-typing-options.md` on `work/irtype`).
