# Library blocks go away, and the class-base wall gets an ordered plan

Status: design note, 2026-11-18, session rsE. Research note, not a decision. The decision rows proposed at the end are what turn it into one.

Inputs: `.agents/docs/design/loose-typing-and-default-inference.md` (#1283), `.agents/docs/design/island-promotion-and-annotation-surface.md` (rsB), `docs/guide/python-subset.md` (the user-facing typing guide; there is no `docs/guide/typing.md` at HEAD), `.agents/decisions.md`, the nine-tree report and `compat_matrix.csv` (2026-11-18, `/tmp/pyscriptc_native_compat_report.md`, `/tmp/compat_matrix.csv`), and the fence sources `src/pyscriptc/stdlib_queue.py`, `src/pyscriptc/imports.py`, `src/pyscriptc/lower/_stdlib_bind.py`, `src/pyscriptc/lower/_classes_more.py`, `src/pyscriptc/lower/_stdlib.py`, `src/pyscriptc/config.py`, `src/pyscriptc/cli/_main.py`.

The owner direction this note implements (2026-11-17): typing is a QUALITY lane, not an ADMISSION lane. Inference is on by default; `--infer` is dropped and no `--no-infer` is added. A codebase fails to lower only for language-wise walls (PSxxxx), never because "<library> can't compile". This note answers the two questions that direction leaves open: (1) what is a library block today and what replaces each one, and (2) in what order do the class-base refusals fall.

## Question 1: the inventory of library-level blocks

Today a codebase can fail to lower for reasons that are about a library, not about the language. Five mechanisms do this. Each is classified below as a language wall (keep, it is a PSxxxx fence the subset genuinely cannot express) or a library wall (remove, route instead).

### 1.1 The closed stdlib import table

What it is: `QUEUE` in `src/pyscriptc/stdlib_queue.py`, about fifty modules, and `module_status()` returning None for anything not listed. The import binder (`src/pyscriptc/lower/_stdlib_bind.py`, `_import_fence_message`) fences `X is not in the closed import table` with PS2002 (`coverage.py:159` classifies it; `assess.py:62` and `boxed.py:100` special-case the message). A listed-but-not-lowered module imports cleanly and fences PS2101 on use (`lower/_stdlib.py:1059`).

Classification: mixed, mostly library wall. The refusal of `weakref` and `traceback` (asyncpg's two missing stdlib modules, report chapter 5) is a library block: those are plain-Python-shaped modules the subset simply has not grown yet. The refusal of an arbitrary stdlib module is also not a language wall: nothing in the language forbids it, the table is an implementation schedule. The genuinely language-wise part is narrower: a module whose surface needs runtime reflection (inspect-style introspection), true metaclasses, or C-extension internals cannot lower, and that refusal must stay and keep its PSxxxx code (PS2001/PS2002/PS2101 wording that names the construct, not the module).

What replaces it: island import routing. The policy lane `[tool.pyscriptc.imports]` (`config.py` lanes `compile`, `replace`, `extern`, `island`, `refuse`; `lane_for`) already routes any named import to the island, and `--island` (alias of `--fallback=cpython`, `cli/_main.py:273`) already routes "every import outside the closed table". The change is policy, not machinery: unknown imports lower as island-boxed values by default instead of refusing, per rsB's P4/N2 recommendation (`--fallback-island-imports` shape). The closed table survives as the list of modules that lower natively, and stops being an admission gate. PS2101 stays as a quality signal (this stdlib name is not lowered, the use sites ride the island).

### 1.2 The closed-table third-party refusals

What it is: every third-party import PS2002s in strict mode regardless of being pure Python (report chapter 4: "refuses every third-party import with PS2002"). pydantic cannot lower because pydantic_core is native-backed; FastAPI hits starlette.responses refusals; the coverage classifier even has a dedicated entry for "is not in the closed import table" on a locked distribution (`coverage.py:660`).

Classification: library wall, the purest one. Whether `starlette.responses` lowers is a fact about starlette, not about Python.

What replaces it: the same N2 routing. A pure-Python third-party module routes to island by default and remains a promotion candidate (the imports ladder `native` / `native: fences` / `island` in `imports.py` already models this). Native-backed cores (pydantic_core, asyncpg's Cython protocol) route to island or pack; that is a routing fact, never a build failure.

### 1.3 Per-library denylists and reflection fences

What it is: per-import policy entries (`refuse` lane in `config.py`) and reflection-tier routing (`reflection.py`, PS2001's "routes the definition to embedded CPython" wording in `diagnostics.py:196`). The `packs track dropped` note in `stdlib_queue.py` (2026-10-25 decision) already shrank curated packs to narrow exceptions (jwt, orjson, ndarray, numpy).

Classification: the explicit `refuse` lane is a library wall when used as an admission gate; it stays only as a user-chosen deny (the user asked for it, it is not the compiler refusing). Reflection fences are language walls and stay: reading runtime metadata cannot lower natively, and PS2001 already routes the definition rather than failing the build.

What replaces the admission use: nothing needs to; N2 default routing makes `refuse` opt-in-only, and the rsB `@island` decorator (A4) gives per-definition opt-out with a note instead of a fence.

### 1.4 `--fallback` gating

What it is: `--fallback=cpython` / `--island` are opt-in flags (`cli/_main.py:268-282`); without them, unknown imports and uncompilable functions fence. `config.fallback_definitions()` and `wildcard_island()` (`config.py:245,350`) plus `imports: {"*": "island"}` are the config spellings of the same gate.

Classification: library wall as an admission gate (the build fails unless the user found the flag). The tier itself is not a wall, it is the island and it is proven (pg0 SDK receipt, exit 0, about 42 ms startup; every tested package runs end to end in island mode).

What replaces it: the flag becomes default-on. The end state, consistent with the 2026-11-17 direction and rsB's P4 verdict: `--fallback=cpython` semantics become the default; the flags remain as documented spellings (no `--no-fallback` is added, mirroring the `--infer` decision). A compile fails only when the language-wise fences inside lowered code stand.

### 1.5 PS2101 (queued-stdlib use fences)

Classification: language wall at the use site (the construct cannot lower yet) but library-shaped at the import site. Keep the code; under N2 the module import stops failing the build, so PS2101 degrades from build blocker to per-use fence with an island route for the function that uses it.

### The end state

A codebase fails to lower only for language-wise fences: a construct the typed subset cannot express (PS1001-family where the pins cannot prove, PS1103/1104/1105/1106 annotation forms, PS1203 headers, PS2001 reflection, PS2002 non-import construct forms, PS2003/2005/2006 and the rest) inside a definition that has no island route. Every library-shaped refusal (closed-table import, third-party import, native-backed core, unlowered stdlib module) becomes a routing decision recorded in the plane map, never an exit non-zero. The success criterion is measurable in the matrix vocabulary: the "is not in the closed import table" and "refused by the imports policy" fence families go to zero as fences (they may remain as assess notes), and `compile` on all nine census trees returns a binary, with native_share_pct the only number left to argue about.

Definition-granularity matters: the island is per-definition (divergence 41), not per-package (2026-10-24 package tiering decision, "one tier per package, never approximate" governs the tier label, not the routing). A module that imports an island module lowers natively except where it touches island values; the N1 ABI (below) is what closes that boundary.

## Question 2: class-base refusals, decomposed and ranked

### 2.1 What fences today

`_resolve_base` (`lower/_classes_more.py:453-493`) accepts: enum bases (`_enum_base_name`), the builtin EXCEPTIONS table (`builtins.py`), lowered classes in `self.classes`, and the http.server handler base (issue #1270). It refuses: island-floor classes (the `island_floor_classes` check, issue #628, boxed-marked), synthetic stdlib subclasses ("subclassing a stdlib type is not lowered"), enum-as-base, and everything else with "this class base is not lowered". PS1203 fences the header forms (metaclasses, abc headers, 254 in psycopg).

### 2.2 The census breakdown

The matrix gives PS2002 totals per tree and the top message gives the class-base share; the report names the base kinds (declarative bases, Table, BaseModel, protocol classes, island boundaries). The per-base-kind split below is an estimate from the report and the packages' known structure; the first implementation step should make it a number (issue below).

| Base kind | Example | Trees hit (est. share of class-base fences) | Lowering it needs |
| --- | --- | --- | --- |
| Library base defined in an island-routed module | SQLAlchemy `DeclarativeBase`/`Base`, Piccolo `Table`, starlette responses | all five ORM trees, the bulk of the 2,071+ events | nothing in the lowerer: N2 makes the base island-defined, then N1 demotion routes the subclass island |
| Third-party core base (native-backed or C) | pydantic `BaseModel` (pydantic_core), asyncpg internals | pydantic, asyncpg | same N1 demotion; pack only if the base must lower natively |
| `Protocol` / structural | typing extensions in FastAPI/starlette internals | small | stays a language wall for native lowering (no runtime structural check); demote per class to island |
| `Generic[T]` / typing base | `Generic`, `ABC` | every tree, small per tree | `Generic` can lower natively as an erased annotation (the checker already drops Protocol/ABC annotations per `python-subset.md` divergence 14); the runtime `__class_getitem__` shape is the fence |
| stdlib synthetic base | `http.server` (already special-cased), `threading.Thread`, `Exception` subclasses beyond the table | small | extend the accepted table per N5-style completion; stdlib bases can lower natively once their class record exists |
| `Enum` variants | `IntEnum`, `StrEnum`, enum-as-base | small, every tree | enum-as-int is already N3; demote exotic enum bases per class until then |
| `NamedTuple` / `TypedDict` | typing constructs as bases | rare | annotation-only constructs; reject natively, demote to island |
| Metaclass headers | abc.ABCMeta (psycopg's 254 PS1203) | psycopg mostly | true metaclasses are a language wall for native lowering; demote per class |

The dominant kind by mass is the first row, and it is pure routing: once N2 islands the base module and N1 provides per-class demotion, the fence disappears without any new lowering. The natively-lowerable rows (stdlib bases, `Generic` erasure, enum-as-int via N3) are small but cheap and they raise native share rather than island share, so they belong early despite the smaller counts.

### 2.3 The ranked plan

Rank 1: N2 import routing (do now, small-medium). Closes every closed-table refusal and turns row-one bases into island-defined values. Receipt: the pg0 policy-lane probe (exit 0) and the full-island receipts for all five ORM stacks. Follows rsB's first do-now PR.

Rank 2: N1 per-class island demotion (do now as the strategic track, large). The `_resolve_base` island-floor refusal (`issue #628` fence) becomes a demotion: a class whose base is not lowered is defined on the island and its module's dependents demote at class granularity, exactly what `assess` demotion hints already compute. The missing half is the boxed-class ABI in `runtime/` so native code can hold and call island classes. Receipt target: sqlalchemy's 1,383 and the five-tree class-base mass move from fence to island routing. rsB's P6 first step applies: emit per-class demotion plans in `assess` output before the ABI work.

Rank 3: N3 small-gaps batch (do now, small). Enum-as-int, bool fields, tuple arity, resumable try/with. Removes the enum-base and enum-member refusals that block `IntEnum`-style bases and is the highest cheap native-share win.

Rank 4: stdlib base completion (gate on evidence, medium each). Extend the accepted stdlib bases (`threading.Thread`, remaining `EXCEPTIONS`, container bases) where the class record can lower natively. Do the ones the demotion census shows demoting most often; skip the rest. `weakref` and `traceback` (asyncpg) fit here as N5 items.

Rank 5: typing-base handling (do now with the checker work, small). `Protocol`/`ABC`/`Generic` bases: accept as erased annotations where the checker already drops them (divergence 14), demote per class when runtime structure is needed. `NamedTuple`/`TypedDict` as bases: demote. This rides the default-inference work (`#1283` Option B as amended 2026-11-17: default-on, `--infer` dropped, no opt-out flag).

Rank 6: N8 decorator-aware demotion (gate on evidence, small-medium on top of N1). Not class-base mass directly, but the same demotion machinery closes the PS2006/PS3099 framework-registration fences; do it once N1's demotion exists and the webapp trees show it as the residual.

Metaclass headers (PS1203, psycopg's 254) are the permanent language wall of this family: native lowering of arbitrary metaclasses is the Codon trade and stays rejected. They demote per class and the fence becomes an island note.

### Effort and receipts summary

| Rank | Change | Effort | Fence mass addressed | Receipt |
| --- | --- | --- | --- | --- |
| 1 | N2 island import routing default | Small-medium | All closed-table PS2002s | pg0 probe; matrix "closed import table" family to zero |
| 2 | N1 boxed-class ABI + per-class demotion | Large | about 2,071+ class-base events | assess demotion plans; sqlalchemy 1,383 to island |
| 3 | N3 small-gaps batch | Small | Enum/bool/tuple refusals in all nine trees | pg0 first-fence removal; enum rows to zero |
| 4 | stdlib base completion | Medium each | stdlib base refusals | demotion census per base kind |
| 5 | typing-base erasure | Small | Protocol/ABC/Generic/NamedTuple rows | divergence 14 wording updated in the same PR |
| 6 | N8 decorator demotion | Small-medium | PS2006/PS3099 residuals | webapp receipt |

## Recommendation

Do now: (1) N2 as the first PR, defaulting unknown-import routing to the island and demoting the closed-table refusal families to assess notes, exactly rsB's P4 first step; (2) the N1 demotion-plan output in `assess` (rsB P6 first step) so the boxed-class ABI has a stable contract; (3) N3. (4) The per-base-kind census instrument: extend `coverage` to record the resolved base expression for each PS2002 class-base fence, so ranks 2, 4 and 5 are ordered by number and rank 4 is gated on that evidence.

Gate on evidence: rank 4 stdlib bases, rank 6 N8, and any second decorator beyond rsB's three (its existing gate).

Reject: keeping the closed table as an admission gate in any spelling (it contradicts the 2026-11-17 direction); a `--no-fallback` or `--no-infer` flag (none exists in the references; none is added); native lowering of arbitrary metaclasses; per-package hand-lowering to dodge library walls.

## Issues to file

1. feat: default island routing for imports outside the closed table (N2), closed-table refusals become assess notes; milestone v0.11.0.
2. feat: per-class demotion plans in assess output (N1 step 1), stable contract before the boxed-class ABI; milestone v0.11.0.
3. feat: boxed-class ABI in the native runtime (N1 step 2), island-defined bases callable and holdable from native code; milestone Next.
4. feat: coverage records the resolved base expression per class-base fence, per-base-kind census; milestone v0.11.0.
5. feat: N3 small-gaps batch (enum-as-int, bool fields, tuple arity, resumable try/with); milestone v0.11.0.
6. docs: rewrite the python-subset import-table and libraries paragraphs to the routing end state ("failing to lower means a language fence, never a library refusal"); ride with issue 1.
7. chore: `--fallback`/`--island` docs update to default-on semantics with no opt-out flag; ride with issue 1.

## A proposed decisions.md row (not appended)

| Date | Decision | Why (one line) | Rejected alternatives | Detail |
| --- | --- | --- | --- | --- |
| 2026-11-18 | Library blocks are removed as admission gates: unknown imports route to the island by default (N2), class-base refusals demote per class (N1), and a build fails only for language-wise PSxxxx fences; no `--no-infer`/`--no-fallback` opt-outs exist | The 2026-11-17 direction makes typing a quality lane, the matrix shows closed-table and class-base refusals are library facts the island already handles (pg0 probe, all five ORM stacks run in island mode), and keeping them as build failures contradicts "never <library> can't compile" | Keeping the closed table as a gate; a no-fallback flag; native metaclass lowering; per-package hand-lowering | This note |

## Related

- `.agents/docs/design/loose-typing-and-default-inference.md`: the inference default this note assumes, as amended by the 2026-11-17 direction.
- `.agents/docs/design/island-promotion-and-annotation-surface.md`: the rsB P4/N2, P6/N1, A1/A4 recommendations this note follows.
- `docs/guide/python-subset.md`: the paragraphs issues 6 rewrites.
- `src/pyscriptc/stdlib_queue.py`, `src/pyscriptc/lower/_stdlib_bind.py`, `src/pyscriptc/lower/_classes_more.py`: the fences this note routes.
- The nine-tree report chapters 4 to 6 and `compat_matrix.csv`: the census every rank above reads.
