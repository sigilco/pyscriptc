# Reach-based pruning of unreached dependency modules: feasibility and decision (#414)

Issue #414 (milestone v0.11.0). Design and measurement spike, measured on master `69b54156` (2026-11-23).
It changes no code.

## Summary

The behavior the issue asks for already exists.
Issue #424 (v0.7.0, commit `487637fe`, divergence 34, `tests/test_reach_pruning.py`, 11 tests passing today) landed reach pruning in `package.py:lower_directory`: the program's own modules are all-or-nothing, a dependency module the entry's import graph reaches refuses the compile with its fence, an unreached dependency module is pruned and reported as a `note: pruned <module>` line.
The issue text was written without that rule in view, and its central claim ("`rich` has 791 root fences and `wcwidth` 302; both are mostly in modules the importer never reaches") does not survive measurement: under today's rule 4 of the 6 candidates prune nothing, and the six together prune 9.5% of their root fences.

What is still open is narrower than the issue:

1. A false refusal: a function-local import is not a reach edge, so a reached function that imports an unreached sibling at call time loses that sibling to pruning and the compile fails with a misleading fence (reproduced below).
2. `pyscriptc coverage` knows nothing about reach: it lowers the tree without dependency roots and with every module as a seed, so it cannot report a `pruned` count.
3. Two import-time edges that never run on import (`if TYPE_CHECKING:` bodies and `if __name__ == "__main__":` bodies) count as reach edges and as cycle edges, which inflates both the reached set and the cycle refusals (rich: 45 modules sit in a phantom cycle set, 0 after the exclusion).

The recommendation (section 7) is to close #414 as superseded by #424, ratify #424 as the root-fence contract, and split the three residuals into small tickets.
No contract change and no census regeneration is needed, so the frozen compat2 gates stay untouched.

## 1. The algorithm as shipped

Everything lives in `src/pyscriptc/package.py`.

`lower_directory` discovers the program units and the dependency units (`_discover_dep_roots`), then calls `_prune_unreached(units, dep_units, program, entry)`, which returns three buckets for the dependency units:

- kept: reached from the seeds. They lower, and their fences refuse the compile (with the distribution that ships them, `_mark_dep_fences`).
- catalogued: referenced by the program's tree but not reached from the entry. They contribute exports to the export table (so an import in an unreached program module still binds) but lower no code and do not refuse.
- pruned: referenced by nothing. Dropped entirely, reported as a note.

The seeds come from `_program_seeds`: the entry module in program mode (`__main__` or `main`, or the named `entry`), every module for a library package (any module can be the consumer's import target).
`_reachable_modules` is a depth-first walk from the seeds over the edges `_program_imports` reads, plus the parent-package edge a dotted import implies (`import pkg.sub` runs `pkg` first, `parents_of`).
Only names in the unit table are nodes: stdlib, packs and refused imports are never nodes.

The edges are the import-time imports only (`_import_time_nodes`): top-level statements, plus those nested in top-level `if`, `try`, `with`, `match` and class bodies.
A function or method body contributes no edge.

Reach and the cycle walk are two different graphs over the same edge function, and the shipped code reuses `_program_imports` for both (the issue's "reuse the walk, do not build a second one").
`_program_plan` then runs only over the kept units (`_lower_units` receives `[*units, *kept]`), so:

- A cycle among pruned or catalogued modules is never seen and never refuses.
- A cycle among reached modules refuses with PS2002 exactly as for the program's own code (unless the island tier takes the members, `_boxed.definitions_tier()`).
- Init owners are assigned only over reached modules, so a pruned module's body never gets an init call, matching CPython, which would never run it.

### Reached versus unreached, precisely

A module is reached if some seed imports it at import time, directly, through a package body (`from . import x` reaches `pkg.x` and `pkg`), or through a chain of reached modules.
It is unreached otherwise.
Module granularity means a reached module keeps every definition and every fence in it, including definitions the program never calls; a definition-level reach is out of scope here and is the real next lever (section 6).

## 2. The contract, before and after

There is nothing to change in `compile`; the table records the contract that holds today and the two residual deltas this spike recommends.

| Surface | Program root (user code) | Dependency root, reached | Dependency root, unreached |
| --- | --- | --- | --- |
| `compile` today | every fence refuses (exit 3) | every fence refuses, names the distribution | no refusal; `note: pruned` (or `unreached` when catalogued) |
| `coverage` today | counted as root fences | not applicable: `coverage` never receives dependency roots | not applicable |
| `coverage` proposed | unchanged | counted as root fences | `pruned` entry per module with its fence count, not in `rootFences` |
| census today and proposed | whole-tree, unchanged | whole-tree, unchanged | whole-tree, unchanged |

The census (`benchmarks/compat2/census*.py`, `census_bridged.py`) calls `coverage.build_report` on each corpus package directory.
A package directory has no entry, so `_program_seeds` seeds every module and nothing is ever unreached.
That is the right behavior for a census, which asks "how much of this package can compile", not "how much of it does one consumer reach".
Pruning a census would need a consumer program per package, which the corpus does not have and the holdout rules forbid generators from reading.
So the census reflects the new contract by construction: it keeps counting every fence as a root fence, and the issue's "census snapshots are regenerated under the new contract, with the delta recorded" resolves to a zero delta.
Anything that wants reach-conditioned numbers (this note's table, the #415 receipts) reads them from a consumer-based report, never from the frozen census gates.

### `coverage` output, proposed

`pyscriptc coverage` gains an optional reach input (`--entry FILE`, or implicit when the target is a program directory with PEP 723 dependencies) and, only then:

- `summary.rootFences` counts program fences plus reached-dependency fences.
- `summary.prunedFences` and `summary.prunedModules` count the rest.
- A new `pruned` array of `{module, file, fences, codes}` rows carries the information the issue wants kept.
- The text report adds one line per pruned module (`pruned dep.broken: 3 fences, not reached`).
- Without the input the report is byte-identical to today (`schemaVersion` does not move; the new keys are additive and absent).

## 3. Measurement method

Throwaway scripts live under `/tmp/pysc/m414/` and are not committed.

- Trees: the materialised wheel source roots in `~/.cache/pyscriptc/deps/src/<sha256>/` for colorama 0.4.6, shellingham 1.5.4, iniconfig 2.3.1, tomli 2.5.0 and wcwidth 0.9.5 (the #415 resolutions), and rich 15.0.0 from its wheel. `benchmarks/compat/top100/pins.json` pins only colorama, shellingham and iniconfig; rich, wcwidth and tomli are not pinned there, so the issue's 791, 302 and 63 are not reproducible and the numbers below are for today's resolutions.
- Whole-tree numbers: `coverage.build_report(root/pkg)`, the same call the CLI and the census make. `rootFences` is the non-derived fence count, `fences` the total. Per-module attribution uses each fence's `file`; every fence file is a discovered unit (no orphans).
- Reach: `package._prune_unreached([("__main__", consumer, False)], dep_units, program=True, entry=None)` with a real consumer program per package (the #415 `*_real.py` and `*_consumer.py` files, and for rich three consumers: `rich.markup.escape`, `Console().print`, `Console` plus `Table` plus `Panel`). Each fence in a kept module counts as reached, each in a pruned or catalogued module as pruned.
- Cross-check: `pyscriptc compile --emit ir` on the real consumers, counting IR fences, to see how far the whole-tree attribution drifts from the fences a real compile raises.
- Variants: `package._import_time_nodes` monkeypatched to try the tightened and widened edge rules of section 5, with no change to the repo.

## 4. Measurement table

Whole-tree `coverage` today, then the reach prune with the shipped rule (`import-time edges`).
For rich the three consumers produce the same reached set; for the others the minimal and the realistic consumer agree.

| package | version | modules | root fences today | reached modules | root fences in reached modules | root fences pruned | pruned fraction |
| --- | --- | --- | --- | --- | --- | --- | --- |
| rich | 15.0.0 | 79 | 623 | 65 | 549 | 74 | 11.9% |
| wcwidth | 0.9.5 | 52 | 384 | 30 | 384 | 0 | 0.0% |
| colorama | 0.4.6 | 6 | 72 | 6 | 72 | 0 | 0.0% |
| shellingham | 1.5.4 | 7 | 40 | 2 | 2 | 38 | 95.0% |
| tomli | 2.5.0 | 4 | 43 | 4 | 43 | 0 | 0.0% |
| iniconfig | 2.3.1 | 4 | 22 | 3 | 22 | 0 | 0.0% |
| total | | 152 | 1184 | 110 | 1072 | 112 | 9.5% |

Reading it:

- shellingham is the only package the rule transforms: its `posix/` and `nt` backends hold 38 of its 40 fences and a consumer on one platform reaches `shellingham` and `shellingham._core` only.
- rich prunes 14 modules and 74 fences; 65 of its 79 modules are reached because its core modules import each other heavily at import time.
- wcwidth prunes 22 of 52 modules but they are data tables with zero fences; its 384 fences sit in the 30 reached modules.
- colorama, tomli and iniconfig reach every module with fences. Their fences are the library's own reachable code, so pruning cannot help; only fixing the fences can.
- The issue's premise holds for 2 of 6 candidates (shellingham fully, rich weakly) and fails for 4.

The whole-tree attribution undercounts what a real compile raises, because a reached module lowered in context raises cascade fences in the consumer (`X is not exported`, `Y is not a local`).
`compile --emit ir` on the real consumers raised: shellingham 5 (2 reached-module fences plus 3 cascades), tomli 81, iniconfig 29, wcwidth 524, rich `Console().print` 69 (3 of them circular-import refusals).
Pruning removes the unreached module's contribution to those counts as well (a compile of the rich consumer with its real transitive dependencies printed 347 `pruned` notes, almost all of them `pygments` lexers and `markdown_it` rules the consumer never reaches; their fence counts were not measured).
colorama's real consumer exits 2 in the checker (`int + str` on the untyped `Fore.RED`) before lowering, so its compile-side count is not available and only the coverage-side number is reported.

## 5. Variants of the reach rule

Same consumers, same trees, edge function swapped.
The first column is the shipped rule.
"No TC" drops `if TYPE_CHECKING:` bodies.
"No TC, no main" additionally drops `if __name__ == "__main__":` bodies.
"Sound" is "No TC, no main" plus every function-local import in a def (an over-approximation of what a reached function can import at call time).

| package | whole | shipped (pruned) | No TC (pruned) | No TC, no main (pruned) | Sound (pruned) |
| --- | --- | --- | --- | --- | --- |
| rich | 623 | 74 (11.9%) | 188 (30.2%) | 271 (43.5%) | 67 (10.8%) |
| shellingham | 40 | 38 | 38 | 38 | 38 |
| tomli | 43 | 0 | 3 (7.0%) | 3 (7.0%) | 3 (7.0%) |
| wcwidth, colorama, iniconfig | 478 | 0 | 0 | 0 | 0 |
| total | 1184 | 112 (9.5%) | 229 (19.3%) | 312 (26.4%) | 108 (9.1%) |

Cycle members among the rich modules (`_program_cycle_members`): shipped 45, No TC 28, No TC no main 0.
The `rich.cells -> rich._unicode_data -> rich.cells` refusal a rich consumer hits today comes entirely from an `if TYPE_CHECKING:` import, and the 28 that remain disappear once `__main__` guard bodies (for example `box.py`'s demo block) are dropped too.

Two lessons:

- Dropping TYPE_CHECKING and `__main__` guard bodies is sound for both graphs (neither runs at import) and removes phantom cycle refusals, but it is not a free pruning win, because the same rich modules are reachable through function-local imports.
- The sound reach rule (add function-local imports) prunes about what the shipped rule prunes (108 versus 112), while closing the false refusal below. The shipped rule's higher number is partly unsound.

### The false refusal

Reproduced with `lower_directory` on a program that imports `dep`, where `dep.core.hello()` does `from dep import lazy` inside the function body and `dep/lazy.py` is imported by nothing else:

```
fences ['dep.lazy is not exported by this package', 'lazy is not a local or parameter']
notes  ["note: pruned dep.lazy (...): the program's import graph does not reach it ..."]
```

`dep.lazy` is pruned (no import-time edge) and then the reached function that needs it is lowered against an export table that no longer has it.
The compile fails loudly with a message that blames the dependency and names no way out, so it is a wrong refusal, not a wrong binary.
No silent behavior change was found.

## 6. Risk list and guards

| Risk | What could silently change | Guard |
| --- | --- | --- |
| Module body with an import side effect, never reached | Nothing: CPython never runs it either. | Reach is over import-time edges; a body that nothing imports cannot run. Test: a pruned module with a printing body, compared with CPython (`test_a_pep723_script_with_an_unreached_dependency_fence_matches_cpython`). |
| `__init__.py` chains | A package body that imports submodules registers them; pruning a submodule the body names would change attribute access (`pkg.sub`). | `parents_of` adds the parent body for every dotted target, and the walk reads the body's own import-time imports, so anything the body names is reached. Test: `test_a_package_body_the_entry_imports_is_reached`. |
| Re-exports | `from .core import hello` makes `pkg.hello` a reached module's name; pruning `core` would break it. | `from . import x` and `from .x import y` are edges in `_program_imports`; `from pkg import name` where `pkg` re-exports resolves to the re-exporting module's own body (reached). Add a case where the consumer names a re-exported symbol only through `from pkg import hello`. |
| Function-local imports | A reached function imports an unreached module at call time; the module is pruned and the function refuses (section 5). | Residual 1: add a separate reach-only edge set that includes function-local imports, and keep `_import_time_nodes` for the cycle and init-owner graph (a local import cannot close an import-time cycle). A reached module's local imports are over-approximated at module granularity. |
| Dynamic imports (`importlib.import_module`, `__import__`) | CPython runs the target; the compiled binary does not. | Both are refused today (not in the lowered subset), so no lowered program reaches past them. Divergence 34 already says so. The call sites that matter are `shellingham/__init__.py` (the platform backend), `wcwidth/table_grapheme_overrides/__init__.py` (the `_known_*` terminal tables, 22 of wcwidth's 52 modules) and `rich/_unicode_data/__init__.py` (the Unicode version tables). If a future slice lowers `import_module`, the whole package must be reached, or the pruning rule is withdrawn for it. |
| Unreached program module imports a dependency | The dependency must still bind for the module's own lowering. | The catalogued bucket keeps the export table entry without lowering. Test: `test_an_import_in_an_unreached_program_module_still_binds`. |
| A catalogued module's fence refuses the compile after all | A catalogued module (referenced by the program's tree, not reached by the entry) does not lower, so its own fences are not root fences, but the catalog pass lowers it to learn its exports and `_promote_definition_fences` (`src/pyscriptc/package.py`) promotes a definition fence back into the compile's fence list when the program's tree names that definition. | Measured: an import in a dead program module that calls the catalogued module's function produces `PS1001` on the catalogued module's file, while an import that does not call it produces no fence. The catalogued bucket's promise ("its fences do not refuse") is not met in that case. The decision has to pick one reading (a catalogued module's fences are notes like a pruned module's, or they refuse when the tree names the definition) and the note text and the `_lower_units` docstring have to match it. |
| TYPE_CHECKING edge removal | A runtime read of `TYPE_CHECKING` that is `True` (never in CPython) would be dropped. | The exclusion matches only the `TYPE_CHECKING` / `typing.TYPE_CHECKING` spelling in an `if` test; `if not TYPE_CHECKING:` stays an edge, as does the else branch. |
| `__main__` guard edge removal | A dependency module run with `python -m` would execute the guard body. | Applied to modules that are not the seed. The seed program keeps its `__main__` guard (it is the entry). A library tree seeded by every module treats all of them as seeds, so no guard is dropped there. |
| Reach rule drifts from the program plan | A module the plan lowers but the reach rule pruned (or the reverse). | One edge function with a flag, never two parsers. A unit test asserts reached modules equal the plan's visited set for each fixture tree. |
| Whole-tree census regenerated by accident | Frozen gates move. | Coverage reach mode is off unless an entry is given; the census calls `build_report` with no entry, so its output is byte-identical. A test pins this. |

## 7. Recommendation and draft decision

Recommendation: do not implement #414 as written.
The root-fence contract it describes already holds (#424, divergence 34), the measured prune on the six candidates is 112 of 1184 root fences (9.5%, 95% for shellingham, 12% for rich, 0% for the other four), and the sound variant of the rule stays within 4 fences of that number.
Close #414 as superseded, ratify #424's contract in a decision, and file three small tickets: the local-import false refusal (S), the TYPE_CHECKING and `__main__` guard edge exclusion for the cycle graph and the reach graph (S), and the `coverage` reach mode with `pruned` rows (M).
The real lever for libraries like colorama, tomli, iniconfig and wcwidth is not module pruning but fewer fences in reached modules, or definition-level reach (only functions the entry can call contribute their local imports and fences), which is a larger design and a separate issue.

Draft decision (for the owner to record; not written to `.agents/decisions.md`):

```
Decision (2026-11-23, issue #414): the root-fence contract is the #424 reach rule.
Contract: the program's own modules are all-or-nothing; a dependency module refuses the
compile only if the entry's import graph reaches it; an unreached dependency module is
pruned and named in a note, never a root fence. #414 is closed as superseded; no new
contract change and no census regeneration (the census seeds every module of a package
directory, so it is whole-tree by construction and stays byte-identical).
What it costs: reach is module granularity and static. A reached module keeps all its
fences. Function-local imports need their own reach edge set (residual ticket) or a reached
function can lose a module it needs and refuse with a misleading fence.
What it buys, measured on 2026-11-23 for six real libraries with one consumer each: 112 of
1184 root fences pruned (9.5%): shellingham 38 of 40, rich 74 of 623, colorama, tomli,
iniconfig and wcwidth 0 of 521. Excluding TYPE_CHECKING and __main__ guard bodies is sound for
the cycle graph and removes rich's 45-module phantom cycle set, but as a reach rule it is
offset by function-local imports (sound variant: 108 pruned).
Guard rails: (1) reach edges and cycle/init-owner edges stay separate sets over one edge
function; (2) coverage reach mode is opt-in and additive, the schema version does not move;
(3) frozen compat2 files and TIR_HASHES are not touched; (4) a differential probe per
reached-module shape (package body, re-export, local import) before any rule change lands;
(5) re-measure with the same scripts when the candidate set changes.
```

## 8. Residual tickets (proposed, not filed)

| Ticket | Effort | Scope | Owned files |
| --- | --- | --- | --- |
| Reach edge set includes function-local imports | S | `_reachable_modules` only; `_import_time_nodes` stays for cycles and init owners; test for the false refusal above | `src/pyscriptc/package.py`, `tests/test_reach_pruning.py` |
| TYPE_CHECKING and `__main__` guard bodies are not import-time edges | S | `_import_time_nodes`; removes phantom cycle refusals (rich: 45 modules to 0) | `src/pyscriptc/package.py`, `tests/test_reach_pruning.py`, `docs/reference/divergences.md` (divergence 34) |
| `coverage` reach mode with `pruned` rows | M | `coverage.py`, `render_text`, JSON additive keys, `docs/reference/cli.md` | `src/pyscriptc/coverage.py`, `src/pyscriptc/cli.py`, `docs/reference/cli.md`, `tests/test_coverage*.py` |
| Definition-granularity reach | L, design first | Only functions the entry can call contribute fences and local imports; changes what a reached module's fences mean | new design note |
