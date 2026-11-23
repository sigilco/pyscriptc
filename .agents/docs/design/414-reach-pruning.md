# Reach-based pruning of unreached dependency modules

Issue #414. Feasibility spike, measured on master `69b54156` (2026-11-23). This note records the algorithm, the contract change, the measured fence split, and a recommended decision. It changes no code.

## Summary of the finding

The contract change #414 asks for is already in the tree. Issue #424 landed it on 2026-10-24 as PR #535 (`feat(deps): prune the unreached dependency fences by reach`), and `src/pyscriptc/package.py` carries the whole mechanism today:

- `_prune_unreached` (`src/pyscriptc/package.py:157`) splits the dependency units into **kept**, **catalogued** and **pruned**.
- `_reachable_modules` (`src/pyscriptc/package.py:777`) is the module-granularity reach walk from the entry over the import graph.
- `_program_plan` (`src/pyscriptc/package.py:924`) is the walk the issue names, and it runs on the kept units only.
- `_pruned_note` (`src/pyscriptc/package.py:847`) is the `pruned` note the issue asks for.

So #414 is not a greenfield design. What is left is three things, and this note measures them: the shape of the contract as it exists, the census numbers under it, and the two soundness holes the current implementation has (Risk 1 and Risk 4b).

The headline measurement: under the current reach rule, **the pruned fraction of the six candidates is 0 percent for five of them and 96 percent for shellingham**. rich prunes 15 percent. The pruning rule is real but it buys very little on the packages the issue cites.

## The algorithm as it exists

### The reach walk

`_reachable_modules(units, seeds)` builds a name-level import graph over the unit table and walks it from the seeds.

The seeds come from `_program_seeds` (`src/pyscriptc/package.py:758`):

- a **program** seeds from its entry module (`__main__`, or the named entry in project mode).
- a **library package** (no entry) seeds from **every module in the tree**, because any of them can be the module a consumer imports.

The edges come from `_program_imports` (`src/pyscriptc/package.py:1240`), which resolves absolute and relative imports against the unit table, and `_import_time_nodes` (`src/pyscriptc/package.py:1315`), which keeps only the imports that run at import time. A function-local import does not count, because it runs at call time, after the importing module's body finished.

The walk also adds the **parent edge** a submodule import implies: `import pkg.sub` runs `pkg`'s body first, so `pkg` is reached even when the program never names it.

### How a reached versus unreached module is decided

`_prune_unreached` (`src/pyscriptc/package.py:157`) puts every dependency unit into one of three buckets:

| bucket | condition | effect |
| --- | --- | --- |
| **kept** | the entry's import graph reaches it | lowers into the program, its fences refuse the compile |
| **catalogued** | the program's tree references it but the entry never reaches it | stays in the export table, does not lower, its fences do not refuse |
| **pruned** | nothing in the program imports it | dropped entirely, its fences stop being root fences |

A **reached** module is one the walk visits from the seeds. An **unreached** module is one it does not. The three buckets differ only in what happens to the module's code, not in whether its fences refuse: only a kept module's fences are root fences.

The soundness argument is CPython's import-time side effect rule: a module body runs once, at the import that reaches it, and never otherwise. A dependency module the entry never reaches never runs, so a fence inside it cannot change what the compiled program does.

The program's own units are **always** kept. The whole-tree rule stays for code the user wrote, which is exactly what #414 asks for.

### How cycle refusal interacts with pruning

Cycles are found by `_program_plan` on the **kept** units, so the walk and the plan agree on the graph. Three cases, all measured:

1. A cycle among **reached** modules still refuses: `PS2002: circular import: dep.a -> dep.b -> dep.a`. Pruning does not weaken the cycle rule for code that runs.
2. A cycle among **unreached** modules is invisible: both members land in the pruned bucket, and neither fence nor cycle is reported. The modules never run, so the cycle cannot happen.
3. A cycle that spans the boundary, where one member is reached and the other is not, cannot arise from the walk itself: if `a` is reached and `a` imports `b`, the walk reaches `b`. The boundary is defined by the reach set, so it is closed under the import edges by construction.

The one asymmetry is the **island tier**. Under `--fallback=cpython`, a cycle is not a refusal: the members ride the island floor and CPython resolves the cycle (`src/pyscriptc/package.py:1004`). The island group is computed on the source import graph before the catalog pass (`src/pyscriptc/package.py:285`), so it sees the same graph the walk does.

## The contract, before and after

### Before the contract change (the state #414 describes)

Every module in the tree, program or dependency, is all-or-nothing. A fence anywhere refuses the whole compile. The issue's own example: a program tree of clean modules compiles, runs and prints the right output; adding one unreached module with an unannotated parameter produces no binary (exit 3).

### After the contract change (the state on master)

| root | before | after |
| --- | --- | --- |
| **program root** | all-or-nothing: any fence in any module of the user's tree refuses | **unchanged**: all-or-nothing |
| **dependency root** | all-or-nothing: any fence in any module of the dependency refuses | only the **reached** modules' fences are root fences |

The unreached module's fences become a **`pruned` note** naming the module and its path, not a root fence. The note text is `_pruned_note` (`src/pyscriptc/package.py:847`):

> note: pruned shellingham.nt (.../shellingham/nt.py): the program's import graph does not reach it,
> so its fences do not block the compile

### What changes in `pyscriptc coverage` output

Nothing today, and that is the gap #414 names.

`coverage` runs `lower_directory` on the target tree and reads the fences the lowering produced (`src/pyscriptc/coverage.py:443`). When the target is a **dependency tree**, the lowering has no program entry and no separate dependency roots, so `_prune_unreached` has nothing to split and every fence in the tree stays a root fence. The `rootFences` count in the summary (`src/pyscriptc/coverage.py:855`) is therefore a whole-tree number, and the census (`benchmarks/compat2/census.py`) reads it that way.

The `pruned` note is not surfaced in the coverage report either. `_island_notes` (`src/pyscriptc/coverage.py:662`) only parses notes matching `note: PS2002 (...)`, so a `note: pruned ...` note is dropped: it appears in the IR `_notes` list, not in the coverage JSON.

So under the contract as it exists:

- `pyscriptc coverage <dep-tree>` reports every fence as a root fence.
- `pyscriptc compile` of a program that imports the dependency prunes the unreached modules.
- the two disagree, and the census measures the unpruned number.

### What the census reflects under the new contract

The census runs `build_report` over each pinned tree (`benchmarks/compat2/census.py:33`) and reads `report["summary"]["fences"]` and the per-fence `derived` flag (`benchmarks/compat2/census.py:37`), so its per-package fence count and its root-family rows come from the whole-tree report. Under a reach-based contract the census has to answer a different question: **which fences would refuse a real consumer?**

The shape of the answer is in the measurement below. The census does not need a new concept, it needs a seed: the consumer program the island probe already has (`benchmarks/compat/top100/island_consumers.json`) is exactly the reach seed the contract needs. The census would run `lower_directory` with the consumer as the program root and the pinned tree as the dependency root, and read the root fences of the merged IR. That number is smaller than the whole-tree number, and it is the number that decides whether a real program compiles.

## The measurement

### Method

Three independent measurements, all on master `69b54156`:

1. **Whole-tree fence count.** `pyscriptc coverage` over the pinned tree, via `build_report`. `fences` is every fence, `rootFences` is the non-derived subset, which is the census metric.
2. **Reach split.** The reach walk of `package.py:_reachable_modules` replayed over the tree's unit table, seeded from a small consumer program's import statements. The script mirrors the compiler's own walk (`_program_imports`, `_import_time_nodes`, the parent edge), so it is the compiler's graph, not a second graph.
3. **End-to-end check.** `lower_directory` with the tree as a dependency root and a consumer as the program root, to confirm the bucket split the walk predicts is the bucket split the compiler takes, and that the unreached fences become notes.

The seeds are the repo's own consumer programs where they exist (`benchmarks/compat/top100/island_consumers.json`), and a one-line `import <pkg>` otherwise.

The trees are the pinned ones: `benchmarks/compat/top100/src/{colorama,iniconfig}` and the materialised wheel source roots under `~/.cache/pyscriptc/deps/src/<sha256>/` for shellingham 1.5.4, tomli 2.5.0 and wcwidth 0.9.5. rich 15.0.0 was fetched from PyPI (sha256 `33bd4ef74232fb73fe9279a257718407f169c09b78a87ad3d296f548e27de0bb`) because it is not in the top100 pin set.

### The table

| package | version | modules | reached | unreached | fences today | root fences today | fences in reached modules | fences pruned | pruned fraction |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| shellingham | 1.5.4 | 7 | 2 | 5 | 45 | 40 | 2 | 43 | 95.6 percent |
| rich | 15.0.0 | 79 | 65 | 14 | 848 | 623 | 722 | 126 | 14.9 percent |
| colorama | 0.4.6 | 6 | 6 | 0 | 82 | 72 | 82 | 0 | 0 percent |
| iniconfig | 2.3.1 | 4 | 3 | 1 | 23 | 22 | 23 | 0 | 0 percent |
| tomli | 2.5.0 | 4 | 4 | 0 | 51 | 43 | 51 | 0 | 0 percent |
| wcwidth | 0.9.5 | 52 | 30 | 22 | 483 | 384 | 483 | 0 | 0 percent |

The "fences in reached modules" column counts every fence whose file maps to a reached module. "Root fences today" is the non-derived subset, the census metric. The pruned root fences are 38 for shellingham, 74 for rich and 0 for the rest.

### Reading the table

**The pruning rule is real, and on the packages the issue cites it barely fires.**

- **shellingham** is the one package the rule rescues: 43 of 45 fences (38 of 40 root fences) sit in `shellingham.nt` (30), `shellingham.posix` (6), `shellingham.posix.proc` (6) and `shellingham.posix.ps` (2), none of which a consumer reaches. That is the Windows and POSIX backend split, and the reach rule correctly says a Linux consumer never runs them.
- **rich** prunes 126 fences across 14 modules: `layout` (25), `traceback` (17), `_inspect` (16), `prompt` (13), `progress_bar` (12), `tree` (12), `diagnose` (8), `bar` (7), `logging` (5), `_windows_renderer` (4), `scope` (3), `filesize` (2), `json` (2). These are the optional subsystems a `Console` consumer does not touch. The 722 fences that stay are in the modules the console itself pulls in, so rich still does not compile.
- **colorama, iniconfig, tomli and wcwidth** prune **nothing**, or almost nothing. Their modules are few and the package `__init__` reaches almost all of them. iniconfig's one unreached module (`iniconfig._version`) carries no fence.

**The issue's cited numbers are stale.** The issue says rich 791, wcwidth 302, colorama 147, shellingham 42, tomli 63, iniconfig 43. Today's whole-tree root fences are rich 623, wcwidth 384, colorama 72, shellingham 40, tomli 43, iniconfig 22. The typing slices of #412 (landed 2026-11-12) took colorama 147 to 72 and tomli 63 to 43, and wcwidth went up because today's resolution is 0.9.5, a much larger rewrite than the version measured for the issue. The numbers cited in #414 should not be used as the baseline.

**rich is not in the pinned corpus.** It is not in `benchmarks/compat/top100/pins.json` nor in `benchmarks/compat2/corpus/pins.json`, so the issue's rich figure came from an ad hoc tree. The measurement above fetched rich 15.0.0 from PyPI for this note.

### A caveat on the seed

The pruned fraction depends on the consumer. The table uses the repo's own consumer programs. A rich consumer that imports `rich.traceback` or `rich.progress_bar` reaches those modules and prunes nothing there. The numbers are therefore a **floor on the pruning**, not a fixed property of the package: a package's pruned fraction is a function of the program that imports it.

This matters for the contract. A census that reports "rich prunes 15 percent" is reporting a number about a specific consumer, not about rich. The census number has to name the consumer it used, the way the island probe names its consumer per package.

## The risk list

Each risk names what could silently change behavior and the guard that catches it.

### Risk 1: a module imported only inside a function body is pruned while CPython runs it

**This is a live hole in the current implementation.** `_import_time_nodes` (`src/pyscriptc/package.py:1315`) skips function and method bodies, so a dependency module imported inside a function is never reached by the walk and lands in the pruned bucket. But CPython runs that import when the function is called.

Measured: a program whose entry calls a function that does `import dep.late` produces `PS2002: dep.late is not a module in this package` and `PS2001: dep.late.value is not in the lowered subset of dep`. The module was pruned from the export table, so the import does not bind. The fence saves the compile here, but by accident: the module was dropped, not reached.

The guard that is missing: the walk must consider a function-local import as a reach edge when the module is a dependency unit, and lower it into the export table even when it is not a root fence. The alternative guard is to treat a function-local import of a pruned dependency module as a hard refusal with its own code, so the user sees "this module is imported at call time and the compiler cannot prune it" rather than a confusing "not a module in this package".

**Recommendation:** the refusal is the safer first cut. It is a smaller change, it is honest about the limit, and it cannot produce a wrong binary. Widening the walk to function-local imports is the follow-up.

### Risk 2: a dynamic import reaches a module the walk cannot see

`importlib.import_module` and `__import__` are invisible to a name-based walk. Three of the candidates use them:

| package | call site | what it imports |
| --- | --- | --- |
| shellingham | `shellingham/__init__.py:12` | `importlib.import_module(".{}".format(name), __name__)`, the platform backend |
| wcwidth | `wcwidth/table_grapheme_overrides/__init__.py:29` | `importlib.import_module(f'._known_{hash_key}', __package__)`, the terminal override tables |
| rich | `rich/_unicode_data/__init__.py:90`, `rich/pager.py:21` | the Unicode version tables, the pager module |

The guard that holds today: `import_module` does not lower. Measured: `importlib.import_module` fences with `PS2001: import_module returns a module`, so no lowered program can reach past it. The walk's assumption is sound under the current lowering.

The guard that has to be added: when a dynamic import **does** lower (the island tier, or a future `import_module` slice), the walk must treat the whole package as reached, or the pruning becomes unsound. A `__import__` slice that lowers would silently break the assumption. This belongs in the decision as a standing constraint on any future dynamic-import work.

The wcwidth case is the sharpest one, because its dynamic import is inside a **reached** module (`wcwidth.table_grapheme_overrides`) and targets modules the walk prunes: 22 of wcwidth's 52 modules are the `_known_*` tables, and they are pruned. If `import_module` ever lowers, those 22 modules must be reached.

### Risk 3: an unreached module with an import-time side effect

CPython runs a module body at the import that reaches it. A module the program never imports never runs, so its side effect never happens, and pruning it is sound. The risk is a module that is **reached but whose body the compiler does not run**: the compiled program would miss the side effect.

The guards: the kept units lower with their module `__init__` injected at the right owner (`_inject_inits`, `src/pyscriptc/package.py:337`), and the island tier keeps CPython running the bodies it cannot lower natively. A kept module's body always runs, natively or island-side.

### Risk 4: `__init__.py` chains and re-exports

A package's `__init__.py` is the module a consumer imports, and it is reached even when the consumer only names a submodule, because of the parent edge. But a re-export (`from .core import hello` in `__init__.py`) makes the consumer's `dep.hello` bind to `dep.core.hello`. If `dep.core` is pruned, the binding breaks.

The guards: the parent edge keeps `__init__.py` reached, and the catalogued bucket keeps an unreached-but-referenced dependency module in the export table so the import still binds (`src/pyscriptc/package.py:254`). Measured: an import in an unreached program module still binds, and a pruned module's exports are still in the table.

The residual risk: a **reached** module that re-exports from an **unreached** module. The catalog pass handles this for the export table, but the re-exported module's **body** never runs, so a module-level side effect in the re-exported module is lost. The guard is that the catalogued bucket must cover a re-export chain, not just a direct import.

### Risk 4b: a catalogued module's fence refuses the compile after all

**This is a second live hole, and it is a smaller one.** A catalogued module (referenced by the program's tree but not reached by the entry) does not lower, so its own fences are not root fences. But the catalog pass lowers it anyway, to learn its exports, and `_promote_definition_fences` (`src/pyscriptc/package.py:2313`) promotes the catalogued module's definition fence back into the compile's fence list when the program's tree names that definition.

Measured, with `dead.py` in the program tree importing `dep.late` and `dep.late` carrying an unannotated parameter:

- `dead.py` imports `dep.late` without calling it: **no fence**, the catalogued module's fence stays out.
- `dead.py` imports `dep.late` and calls `dep.late.value()`: **`PS1001: parameter x needs an annotation` on `late.py`**, plus `PS2001: dep.late.value is not exported by this package` on `dead.py`.

So the fence in the catalogued module's file does reach the compile, through the promotion path, but only when the program's tree names the definition. The catalogued bucket's promise ("its fences do not refuse", `src/pyscriptc/package.py:254`) is not met in that case.

Whether this is a bug depends on the contract. Under a strict reading of #414 ("an unreached module's fences become a `pruned` note"), the catalogued module's fence should be a note too. Under the reading the current code implements (a catalogued module is one the program's tree references, so its fences are relevant), the promotion is correct and the documentation is what is wrong.

The guard: whichever reading the decision picks, the note text and the `_lower_units` docstring have to say it. The measurement shows the two behaviours are already distinguishable, so the decision only has to pick one and the code has to match.

### Risk 5: the census moves under the new contract

The census number is a whole-tree number today (`build_report` on the pinned tree). Under a reach-based contract it becomes a consumer-seeded number, and it changes.

The measured direction: the census root fences would **fall** by 38 for shellingham and 74 for rich, and stay put for the other four. The ranking of families would move with it, since the pruned fences are concentrated: shellingham's pruned fences are almost all `module-value` and `param-inference`, and rich's are concentrated in `name-resolution` and `construct-other`.

The guard: the regenerated census snapshots must record the seed each package's number used, and the delta must be in the PR. The frozen files (`benchmarks/compat2/coverage_map.py`, `score.py`, `outcomes.py`) stay untouched: the census is a separate tool.

### Risk 6: a library package with no entry seeds from every module

`_program_seeds` (`src/pyscriptc/package.py:758`) seeds a library from **all** its modules, because any of them can be the one a consumer imports. So a library package's reach is effectively the whole tree, and the pruning does not fire for it.

That is the right default (the consumer decides what it imports), but it means the "unreached dependency module" case only ever applies to a **program** root with dependency roots beside it. The issue's phrasing ("a dependency root keeps only reached modules") matches this: the pruning is about the dependency roots of a program, not about a library package's own tree.

## What the contract change costs and buys

**Buys:** shellingham's 43 fences stop being root fences for a Linux consumer, which is 38 of 40 root fences, or 95 percent of the package's root-fence load. rich's 126 fences stop being root fences for a `Console` consumer, 74 of 623 root fences, or 12 percent. For colorama, iniconfig, tomli and wcwidth it buys **nothing**.

**Costs:**

1. The `coverage` report and the `compile` path disagree: `coverage` on a dependency tree reports every fence as a root fence, while `compile` of a program that imports it prunes. Two numbers for the same package.
2. The pruned-fence count becomes a function of the consumer, so the census needs a named seed per package.
3. The soundness argument rests on "a module body runs only at the import that reaches it", which holds for import-time imports and does not hold for the function-local and dynamic import cases above.
4. The pruned fences disappear from the `coverage` JSON entirely, because `_island_notes` only parses `note: PS2002 (...)`. A user asking "what cannot this package compile" gets a smaller answer than the truth.

**Recommendation:** record the decision as **narrower than #414 asks, and already largely delivered**. The reach rule is the right rule and it is already the rule for the compile path. What #414 should record is the four gaps, not a new contract:

1. the contract statement itself (program root all-or-nothing, dependency root reached-only, the `pruned` note), so the rule is written down rather than implied by PR #535,
2. the `coverage` surface: the report must show the pruned fences and their count, so the coverage number and the compile number agree,
3. the two soundness guards: the function-local import and the dynamic import,
4. the catalogued bucket's fence behaviour, stated one way or the other.

The reach rule alone does not unblock the packages the issue names. rich still has 722 fences in reached modules, wcwidth 483, tomli 51, colorama 82, iniconfig 23. The blocker for those is the fences in the modules a consumer actually reaches, and reach pruning does not touch them. That is why the sibling spike #415 picks iniconfig as the strong-receipt target: iniconfig is the package whose **reached** fence count is small enough to close.

### Draft decision text

> **Decision: reach-based pruning of dependency fences, program root all-or-nothing.**
>
> A dependency root keeps only the modules the program's entry reaches over the import graph. An unreached dependency module's fences are not root fences; they are reported as a `pruned` note naming the module and its fence count. A program root keeps the whole-tree rule: an unreached module in the user's own code is a fence the user should see.
>
> The reach walk is `package.py:_reachable_modules`, seeded from the program entry (or from every module, for a library package with no entry). It runs at import time only: a function-local import and a dynamic import do not count as reach edges.
>
> Cost: the pruned fence count depends on the consumer program, so every census number names the consumer it used. The `coverage` report must show pruned fences and their count, so the coverage number and the compile number agree. The compat2 census snapshots are regenerated under the new contract with the delta recorded in the PR; the frozen gates are untouched.
>
> Buys: shellingham's 43 of 45 fences (38 of 40 root fences) stop being root fences for a Linux consumer, rich's 126 of 848 (74 of 623) for a `Console` consumer. colorama, iniconfig, tomli and wcwidth prune nothing.
>
> Guards: a function-local import of a pruned dependency module is a refusal with its own code, not a silently pruned binding. Any future lowering of `import_module` or `__import__` must treat the whole package as reached, or the pruning rule is withdrawn for that package. The catalogued bucket's fence behaviour is stated explicitly: either a catalogued module's fences are notes like a pruned module's, or they refuse when the program's tree names the definition, and the note text says which.

## Reproducing the measurement

The measurement scripts are throwaway and live outside the repo. The three steps:

1. `pyscriptc coverage --json <tree>` for the whole-tree fence and root-fence counts.
2. The reach walk, replaying `package.py:_reachable_modules` over the tree's unit table (`_discover_dep_package`) with the seeds taken from a consumer program's import statements.
3. `lower_directory(program_root, dep_roots=[tree])` for the end-to-end bucket split and the `pruned` notes.

The trees are the pinned ones under `benchmarks/compat/top100/src/` and `~/.cache/pyscriptc/deps/src/<sha256>/`; rich 15.0.0 was fetched from PyPI for this note.

## References

- Issue #414, this spike's brief.
- Issue #424, the parent: "reach-based pruning of unreached-module fences", closed 2026-10-24.
- PR #535, `feat(deps): prune the unreached dependency fences by reach (#424)`, the implementation that already carries the rule.
- Issue #415, the sibling spike: the strong pure-dependency receipt.
- `.agents/docs/design/415-pure-dep-receipt.md`, the measured fence profile of the same candidates.
