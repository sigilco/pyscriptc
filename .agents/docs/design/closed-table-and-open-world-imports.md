# The closed stdlib import table and the open-world import model

Status: design note, 2026-11-18, session rsF. Research note, not a decision. The decision row proposed at the end is what turns it into one.

Inputs: `.agents/docs/design/library-blocks-and-class-bases.md` (rsE), `.agents/docs/design/loose-typing-and-default-inference.md` (#1283), `.agents/docs/design/island-promotion-and-annotation-surface.md` (rsB), `docs/guide/python-subset.md`, `docs/guide/libraries.md`, `docs/reference/divergences.md` (divergences 41 and 49), `.agents/decisions.md`, and the fence sources `src/pyscriptc/stdlib_queue.py`, `src/pyscriptc/imports.py`, `src/pyscriptc/lower/_stdlib_bind.py`, `src/pyscriptc/lower/_stdlib.py`, `src/pyscriptc/sigregistry.py`, `src/pyscriptc/stubs.py`, `src/pyscriptc/assess.py`, `src/pyscriptc/coverage.py`, `src/pyscriptc/config.py`, `src/pyscriptc/cli/_main.py`, `scripts/gen_support_table.py`.

The owner direction this note serves (2026-11-17): typing is a QUALITY lane, not an ADMISSION lane. Inference is on by default; `--infer` is dropped and no `--no-infer` is added. A codebase fails to lower only for language-wise walls (PSxxxx), never because "<library> can't compile".

## Question

How does the closed stdlib import table work end to end, which sibling closed lists share its shape, why do we still keep it, does its admission role hurt more than its knowledge role helps, and what does the target open-world architecture (table as native-support catalogue, unknown imports always island, only PSxxxx language walls refuse) look like as a migration?

## Evidence

### The end-to-end mechanics of the closed table

Definition. `QUEUE` in `src/pyscriptc/stdlib_queue.py:29` is a 68-entry ordered tuple of dotted module names (math, re, json, asyncio, http.server and so on). `LOWERED` (`stdlib_queue.py:100`) is its frozenset view. The module docstring states the closed-world rule directly: every name in `QUEUE` has a lowered surface; importing a module that is not listed is refused; `typing` is intentionally absent; a name added to `QUEUE` but left out of `LOWERED` imports cleanly and fences PS2101 on use. `MODULE_ALIASES` (`stdlib_queue.py:108`) maps user spellings onto lowered surfaces (`os.path` to `posixpath`, `email.message` to `email`).

The lookup. `module_status(name)` (`stdlib_queue.py:272`) returns `lowered`, `queued`, or `None`. `None` means outside the table, and that is the closed-world refusal point. It also folds in the per-compile `--ffi` manifests (`_FFI_MANIFESTS`, `stdlib_queue.py:312`): a manifest module answers `lowered` without touching `QUEUE`, which is the one registration point the docstring calls out because the table is frozen.

What a miss does at the import site. `lower/_stdlib_bind.py` is the consumer. For `import x` (`_bind_import`, around line 85): if `module_status` is `None`, the order of exits is (1) the `imports` policy `refuse` lane fences PS2002 "import `x` is refused by the `imports` policy" naming the config site, (2) inside `boxed:begin` markers, `island_lane(name)` binds the name as an island `Binding("island", ...)`, (3) otherwise the closed-table PS2002 fence fires with `_import_fence_message`. The same ladder exists for `from x import y` (`_bind_from`, around line 176): island binds each alias as `Binding("island_member", ...)`, star imports stay refused, and the fallback is the PS2002 closed-table fence. So a miss today is a hard compile failure unless the user has opted into island routing per-import or globally.

The island routing knobs that sit next to the table. `island_lane`/`island_policy` (`stdlib_queue.py:366-382`, boxed-marked) hold the per-compile island set plus a default flag. `cli/_main.py:1991-1998` fills them: `island_names = config.island_imports()`, and `island_default = fallback or config.wildcard_island()` where `fallback` is `--fallback=cpython` (alias `--island`, `cli/_main.py:268-278`) or `[tool.pyscriptc] fallback`, and `wildcard_island()` (`config.py:350`) is `[tool.pyscriptc.imports] "*" = "island"`. `config.py:246` adds `[tool.pyscriptc.fallback]` per-definition routing. So the open world exists already, but only behind explicit opt-in flags; without them the closed table refuses.

What an in-table but not-yet-lowered entry does. `queued` status imports bind fine and fence PS2101 "queued stdlib module ... planned but not compiled yet" at the use site (`lower/_stdlib.py:1061`, `diagnostics.py:249`). PS2001 fires for a member outside a module's lowered subset (`lower/_stdlib.py:526`). The table's staged membership (`LOWERED` vs `QUEUE`) is exactly the tier metadata the knowledge role wants.

Sibling closed lists, same shape.

- `PACK_MODULES` (`stdlib_queue.py:246`): `{"jwt", "orjson", "pyscriptc_ndarray", "numpy"}`, the curated replacement packs in `src/pyscriptc/restlower/` (53 pack modules at HEAD, `restlower/_argparse.py` and friends). A pack import binds as a module alias outside the table entirely (`_stdlib_bind.py:74-84`, issue #496 normalization).
- `native_packages.json` (`src/pyscriptc/imports.py:94`, 40 packages): the known native-backed distributions, consumed by the `pyscriptc imports` classifier (`imports.py` docstring: lanes native, pack, extern, island, absent) and by `assess`. Closed but advisory: it feeds reports, not admission.
- The sigregistry/stubs side. `src/pyscriptc/stubs.py` sets `STDLIB_SUBSET` from `QUEUE` (line 54-60) and uses it as the default stub-discovery allowlist (`discover_roots(..., stdlib_allow: Iterable[str] | None = STDLIB_SUBSET)`, line 134-139); `TYPESHED_STDLIB` is the vendored stub tree. `src/pyscriptc/sigregistry.py` (header: "partial slice, not wired yet") is designed to answer signatures for `(module, qualname)` pairs, map them onto closed-table rows, and return a `Refusal` naming why a signature does not lower. Its stated integration point: "where `lower/_stdlib.py` would ask the registry before fencing PS2002 or PS2101". Hand-written closed-table rows stay authoritative; the registry fills and cross-checks.
- Erased modules (`lower/_stdlib_shared.py` `_ERASED_MODULES`: `typing`, `typing_extensions`, `collections.abc`, the shadow `pyscriptc._annotations` from #1321): a closed list with the opposite polarity, membership is free.

Other readers of the table.

- `assess` (`assess.py:412-425, 608-615`) reads `stdlib_queue.QUEUE` directly to decide whether a fence's import root is stdlib (suppressing routing hints) and to decide whether an untyped import needs a `[tool.pyscriptc.imports]` policy line in its report. If the admission miss becomes a routing, these two reads keep working unchanged because they only ask "is this name in the lowered set", which the catalogue still answers.
- `coverage.py:195` knows PS2101 and counts it as the partial-stdlib family.
- The compat site generator (`scripts/gen_support_table.py:4-5, 36, 201`) iterates `QUEUE` and `LOWERED` to build `data/compat/support.json`: the site's stdlib support table is literally the catalogue made user-visible. This is the knowledge role doing its job.
- `engine_gate.py`/`island_engine` and `stdlibzip` consume the island lane mechanism, not the table itself; a default-on island routing increases their traffic but does not change their contract (divergence 49's guarded code cache covers the startup cost).
- Test locks: `tests/test_imports.py`, `tests/test_gates.py` and others reference `QUEUE` and its order; the tuple docstring says "do not reorder casually: tests lock this sequence".

### Interaction with #1308 default island routing

#1308 (branch `work/imp1308`, commits `1367133f`..`2b1c3b54`, "install the default island routing at the serving entry points") is in flight, not merged to master at HEAD `57c57b27`. Its shape, from the commit subjects: default island routing carried through every compile path and installed at the serving entry points. That is rsE's rank-1 recommendation ("N2 island import routing default, small-medium, closes every closed-table PS2002") and rsB's P4 first step. Once it lands, the miss path in `_stdlib_bind.py` stops at the island bind and the PS2002 closed-table fence becomes dead code except under an explicit `refuse` policy entry. This note's architecture is what #1308 leaves behind: the table still exists as the membership oracle every consumer above reads, so the question "what replaces the table" is live regardless of #1308's merge state.

### Why we still keep it: separating the roles

Legitimate roles, all real, all worth keeping.

1. Native-lowering knowledge. `QUEUE`/`LOWERED` is the authoritative answer to "which stdlib modules have hand-written lowered surfaces" (`restlower/` packs, `lower/_stdlib.py` dispatch, `emit/llvm/_stdlib.py` runtime calls). The sigregistry design explicitly keeps these rows authoritative over typeshed-derived signatures.
2. Tier metadata. The `lowered` vs `queued` split is the staged-wave mechanism; PS2101 exists precisely so a queued module imports cleanly and fences only on use. `queue_index` and the test-locked order are the wave plan.
3. Divergence accounting and site content. `gen_support_table.py` publishes the catalogue; `coverage.py` counts the partial-stdlib fence family; the differential probe rule (#524) attaches to each new lowering entry.
4. Assessment routing. `assess` needs to distinguish "stdlib module with a partial surface" from "third-party import needing a policy line"; `QUEUE` is that oracle today.

The admission role, which is the one the owner direction removes. A miss (status `None`) fails the compile with PS2002 "module `x` is not in the closed import table" unless the user opted into island routing. That is a library wall in exactly the sense the 2026-11-17 direction forbids: the refusal is about which module the user imports, not about a language construct the compiler cannot express. `docs/guide/python-subset.md:119` documents the wall as user-visible behavior ("A module outside the closed import table (`platform`, `calendar`, `zipfile`, `tarfile`, `sqlite3`) stops with `PS2002` on the import"). Note the irony the doc's own example list shows: `platform` and `calendar` are in `QUEUE` at HEAD (lines 86-87) and the doc text is stale against the table, which is the maintenance cost of a closed list made visible in user docs.

### Do peers keep unknown imports out of the compiler?

One sentence each, with sources.

- Nuitka: compiles everything importable, unknown modules fall through to CPython's import machinery at run time; there is no compile-time import admission at all (https://nuitka.net/doc/user-manual.html).
- Cython: unknown imports compile to ordinary Python `import` statements executed at run time; the compiler never refuses (https://docs.cython.org/en/latest/src/userguide/compilation.html).
- mypyc: compiles what mypy accepts and leaves dynamic escape hatches open; a missing module is a run-time ImportError, not a compile refusal (https://mypyc.readthedocs.io/en/stable/introduction.html).
- Codon: closed-world by design, refuses unknown imports at compile time, and pays for it by leaving CPython semantics (https://docs.exaloop.io/language/overview/). This is the company pyscriptc keeps if the admission role stays.
- Numba: `@njit` code referencing an unsupported global falls back to object mode (historically) or raises, but module imports are resolved at run time by the host interpreter (https://numba.readthedocs.io/en/stable/user/faq.html).
- PyPy: a full interpreter, refuses nothing, unknown imports behave exactly as CPython.
- Pyodide: the whole stdlib is available and unknown packages are fetched and loaded at run time (https://pyodide.org/en/stable/usage/loading-packages.html); the runtime is an open world with a package catalogue, which is precisely the target shape here.
- TypeScript with unresolved modules: the compiler reports `error TS2307: Cannot find module` but by default still emits output (`noEmitOnError` off); with `@types` absent it treats the module as `any`. Refusal is a lint-level quality signal, not an emission wall (https://www.typescriptlang.org/docs/handbook/module-resolution.html).
- Rust crates: the analogue is a missing crate, which is a build-system error, not a language wall; but the relevant detail is that a present crate always compiles, and rustc never refuses a crate for being "not in the supported list" (https://doc.rust-lang.org/cargo/).

The pattern: every peer that preserves host semantics resolves unknown imports at run time or lowers them transparently. Only the closed-world language (Codon) makes unknown imports compile-refusing. TypeScript is the closest analogy to our checker-bridge architecture, and it demotes the unknown to `any` with a diagnostic, which is what island routing does here.

### The trade, honestly

What the admission role buys: a compile fails fast and loudly instead of producing a binary where an import silently runs at interpreter speed. That is worth something before island default routing exists. Once #1308 lands, the fence only fires when the user has explicitly chosen `refuse`, which is the policy lane doing its job, not the table.

What it costs: user-visible "library X cannot compile" walls (the matrix's PS2002 mass, the exact rows rsE attributes to the closed table); documentation drift (the python-subset example above); maintenance (every new lowering is a `QUEUE` edit plus order-locked test churn); and a conceptual coupling that leaks: `stubs.py` discovery, `assess` routing, the compat site and the lowerer all read one frozen tuple for four different reasons, so a wave change ripples through sites that only wanted membership.

What an open world risks: silent slowdowns. A typo'd import becomes an island binding that fails at run time with an ImportError instead of at compile time. Mitigation: the compile summary already reports lane assignments (`cli/_main.py:1062-1076` prints `islandImports`), `assess` prints the plane map, and the policy `refuse` lane stays for teams that want the hard wall back, per-module and by choice. A typo is also caught by the existing checker preflight in most trees. The risk is real but bounded, and it is the same trade every open-world peer already accepted.

## Options

### Option A: keep the closed table as admission, extend it module by module

What it is: the status quo plus #1308. The table stays an admission gate; unknown imports island by default; growth happens by adding `QUEUE` entries.

Peers: this is Codon's world model. Every host-parity peer left it.

Cost: permanent maintenance of a frozen ordered tuple and its test locks; every wave PR touches `stdlib_queue.py` and its locked tests; the user-facing wall only moves, never dies, for anything not yet queued.

Verdict: reject as the end state. #1308 already de-fangs the admission miss, so the remaining cost is pure overhead.

### Option B: open-world imports, the table becomes a native-support catalogue

What it is: unknown imports always route to the island lane (no flag needed, this is #1308's default made the only behavior); the closed table keeps its knowledge roles and loses its admission role entirely; compile failures are only PSxxxx language walls plus explicit `refuse` policy entries.

The catalogue shape. `stdlib_queue.py` keeps `QUEUE`, `LOWERED`, `module_status`, `queue_index`, `MATH_FUNCTIONS`, `WAVE_FUNCTIONS`, `MODULE_ALIASES`, `PACK_MODULES` and the ffi manifest registry, and gains nothing structurally: it is already the catalogue. What changes is who consults it for what:

- `lower/_stdlib_bind.py`: the final PS2002 closed-table fence in `_bind_import` and `_bind_from` becomes unreachable (the island bind answers every miss). Delete the dead branches and `_import_fence_message`'s closed-table wording once #1308's default routing is unconditional. The `refuse` policy lane check stays: it is user intent, not the table.
- `island_policy` default flips from opt-in (`--fallback=cpython`, `--island`, `wildcard_island`) to always-on; the flags survive as no-op compatibility aliases for one release or are removed outright, matching the `--infer` precedent from the owner direction.
- Tier metadata grows a public accessor if the report needs it (`module_status` already is one); `queued` entries keep PS2101 at the use site, which is a language-wall-shaped diagnostic about a not-yet-lowered construct, not a library wall about an import.

Migration steps, in order, each independently revertable.

1. Land #1308 (in flight): default island routing at every serving entry point. The closed-table PS2002 becomes reachable only under `refuse`.
2. Move the catalogue's consumers off admission reads: `stubs.py` `STDLIB_SUBSET` and `discover_roots` already read the set, not the wall; `assess` reads membership only. Audit that no path fences on `module_status(...) is None` other than the island-defaulted bind. This is a grep-sized PR plus a test.
3. Delete the dead fence branches and their tests; `_import_fence_message` keeps only the policy-refuse wording. Update `docs/guide/python-subset.md:119` (the closed-table paragraph and its stale example list) in the same PR per the documentation policy.
4. Retitle the thing: rename the docstring and the compat-site generator's language from "closed import table" to "native-support catalogue" (`stdlib_queue.py:1-7`, `scripts/gen_support_table.py:4-5`). No behavior change; the module name stays (renaming `stdlib_queue.py` is churn without a consumer).
5. The sigregistry integration (already designed, "not wired yet") fills catalogue gaps from typeshed; it must never reintroduce admission: a registry `Refusal` surfaces as a lower-quality row (island or PSxxxx at the use site), never as an import fence.

What must stay: `QUEUE`/`LOWERED` membership and order (waves), `module_status`, `MODULE_ALIASES`, `PACK_MODULES` and the restlower packs, `MATH_FUNCTIONS`/`WAVE_FUNCTIONS` rows, the ffi manifest registration point, `_ERASED_MODULES`, `native_packages.json`, the policy `refuse` lane, PS2101, PS2001, and the compat-site generator's consumption.

What goes away: the closed-table PS2002 fence message and its emission branches, the opt-in-only island default (flags become aliases then die), the `python-subset.md` closed-table paragraph, and eventually the phrase "closed import table" everywhere a user can read it.

Effect on the matrix: the closed-table PS2002 family rsE attributed to the table goes to zero by routing (already #1308's receipt); nothing else moves. The honest statement is rsB's: it is a compile-succeeds win, not a native-share win.

Risks, named.

- Silent island growth: every unknown import in a big tree becomes island work at run time. Watch the boxed ledger's code-share window after the flip; the drop-flag rules in AGENTS.md already govern this.
- Error-at-run-time instead of compile-time for genuinely absent modules. Mitigation: the summary lane report and `assess` name them; keep `refuse` for CI-strict users; consider an `assess` warning class for imports that resolved nowhere (advisory, no fence).
- The `imports` classifier (`imports.py`) and the lowerer can now disagree about a name the classifier calls `absent` while the lowerer islands it. Keep one membership oracle (`module_status`) and have the classifier consume it, so the report and the compile never split.
- Test churn on the order lock: deleting fence tests is mechanical but wide; do it in the same PR as the branch deletion so no window has a green suite guarding dead code.

Verdict: do now, staged as above, gated only on #1308's merge. It is the completion of the owner direction, not a new direction.

### Option C: drop the table entirely, derive the catalogue from stubs and packs

What it is: delete `QUEUE`; the catalogue becomes the union of `restlower/` pack discovery, the sigregistry's typeshed-derived surfaces, and `native_packages.json`.

Cost: the sigregistry is explicitly not wired ("partial slice, not wired yet", `sigregistry.py` header) and the wave ordering, the test locks and the compat-site generation all assume the tuple. Deriving membership from filesystem discovery makes the catalogue nondeterministic across checkouts and loses the `lowered`/`queued` tier split, which is load-bearing for PS2101.

Verdict: reject now, revisit if the sigregistry lands and proves itself as the authoritative signature source; even then keep the tuple as the wave plan. First step: none.

## Recommendation

Do now (each its own PR, milestone v0.11.0 unless the issue says otherwise):

1. Land #1308; it is the load-bearing step (in flight, not this note's PR).
2. The dead-branch deletion PR: remove the closed-table PS2002 emission in `lower/_stdlib_bind.py` once island default routing is unconditional, keep the `refuse` policy lane, update `docs/guide/python-subset.md` in the same PR.
3. The terminology PR: catalogue wording in `stdlib_queue.py`'s docstring and `scripts/gen_support_table.py`; behavior untouched.

Gate on evidence: the boxed-ledger reading after the flip (churn share must stay under the drop-flag thresholds with island traffic up); the `assess` absent-import advisory warning; the sigregistry-as-catalogue completion (Option C's gate).

Reject: keeping the table as an admission gate in any spelling (contradicts the 2026-11-17 direction and rsE already recorded this); deleting the table now (Option C, the consumers are not ready); a `--no-island` or `--no-fallback` flag to restore the closed world (same rule as `--no-infer`: none exists in the references, none is added).

## Issues to file

1. refactor(lower): delete the closed-table PS2002 emission behind unconditional island default routing; keep the `refuse` policy lane; update `docs/guide/python-subset.md`; milestone v0.11.0.
2. docs(design): retitle the closed import table as the native-support catalogue in `stdlib_queue.py`, `scripts/gen_support_table.py` and the compat site copy; milestone v0.11.0.
3. feat(assess): an advisory absent-import warning class for imports that resolved nowhere (report only, never a fence); gate on the post-flip ledger reading; milestone v0.11.0 or Later.
