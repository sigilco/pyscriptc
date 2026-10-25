# Island floor for imported modules: direct fix versus bundling (2026-10-25)

Refs #641. Branch `work/bundleisl` from master `30ca38df`, CPython 3.14.9 host, arm64 macOS. Target program: `harness` (12 modules), copied to `/tmp/bundleisl`, original untouched.

## Where the plan stops at the module boundary

`lower_directory` -> `_lower_units` (`src/pyscriptc/package.py`) builds one `Lowerer` per unit. Each unit runs its own boxed plan (`_boxed_resolve`, the `_boxed_top_island` probe, `plane_map`) and returns its own `fallback`, `islandSource` and `islandGlobals`. The merge loop only copies `functions`, `classes` and `fences`, so the per-unit plan is dropped: the merged module has no `islandSource`, and the CLI's `module.get("fallback")` is empty.

Two consequences. A unit whose fences were planned for the island loses its body from the lowered list, but the cross-module call in another unit still resolves through the export table to the native name (`mp.helper.count`), and the emitter fails with `KeyError: 'mp.helper.count'` (reproduced on the two-module repro). A unit whose fences survive (module-level `Path(os.environ...)`, `class X(TypedDict)`) refuses the compile with exit 3, because the program has no island seed to absorb them.

There is also a deeper reason a per-unit fix is not small: the runtime keeps exactly one island namespace per binary (`ps_island_ns`, seeded once). A per-module plan needs one namespace per module with qualified names, a cross-module call trampoline in both directions (native unit calls island function of another unit and the reverse), module objects as island values for `import helper` / `helper.count`, and per-module top-level order (CPython runs a module body at first import). That is the Stage 2 seam multiplied by the module graph. I did not attempt it.

## Option A: program-wide island (implemented)

The coarse unit is the program. Under `--fallback=cpython`, when a program's plan has fences, or any unit's own plan puts anything on the island, `_lower_units` returns a module whose only content is an island top level: `fallback.moduleTopLevel = island`, no native functions, and an `islandSource` that is a bootstrap plus the entry module. The bootstrap embeds every unit's source in a dict, installs a `sys.meta_path` finder that serves them with their real path as origin (`spec_from_loader`, `has_location`), puts the program directory on `sys.path` like CPython does, sets `__file__` and `__package__` for the entry and `exec(compile(src, path, "exec"), globals())`s it. No C runtime change: it reuses the existing `ps_island_top_seed` path (#613).

What it keeps, because CPython itself runs the modules: module `__name__` (`mp.util`), `__file__`, relative imports, package `__init__` bodies, import order, circular imports (the plan's circular-import fence is bypassed on purpose), function-level imports, `importlib`/`pkgutil`/package data next to the real files, and `python -m pkg` semantics (`__package__`, `sys.path[0]`). Tracebacks keep original file names and line numbers because each module is compiled from its own source with its own path.

What it costs: all-or-nothing at program level. One fenced module moves every module to the island, so a program that was partly native (fully typed siblings) becomes fully island. A fully native program is untouched: the promotion only fires when some unit needs the island (test `test_clean_program_stays_native`). The island needs the original sources only at compile time (they are embedded), but `__file__` points at the compile-time path.

Measured on this branch:

| Check | Result |
|---|---|
| Two-module repro (`mp.helper.count`) | compiles, prints 3 (was `KeyError`) |
| `harness` as a package, `compile --program --fallback=cpython` | compiles, exit 0 (was exit 3 on module-level fences, per the assessment note), 695 KB binary |
| `--help`, `ls --json`, `bogus`, `launch` usage errors | stdout, stderr and exit code identical to `python -m harness` |
| `status nosuch`, `wait nosuch`, `steer nosuch hi` | exit 0 and no stderr instead of exit 1 and the message: this is #642, `SystemExit` with a `str` payload, unchanged by this work |
| `tests/test_island_program.py` (3 differential tests: plain program dir, package with relative imports, a cycle and import order, clean program stays native) | pass |
| `test_fallback_definitions`, `test_planmap_report`, `test_island_pilot`, `test_package`, `test_program`, `test_reach_pruning`, `test_emitter_package` | pass, except `test_program.py::test_a_side_effect_import_runs_the_body_once`, which fails identically on master (stash check) |

Not covered: #643 / #650 (`KeyError: '_ps_imod_1'`) is a lowerer crash inside a unit, before the promotion point. `harness` (which has the `ThreadPoolExecutor` `with`) compiled without hitting it in program mode, so I could not reproduce it on this path; a unit that crashes the lowerer is still a crash. Catching it and promoting the program would be a one-line follow-up but hides a real bug, so I left it. #642 is also separate. The per-module native/island split (native speed for clean modules) is not done.

## Option B: bundle into one module (prototype, not shipped)

Prototype: `/tmp/bundleisl_proto/bundle.py` (about 80 lines of `ast`): topological order over top-level relative imports, mangle every top-level name to `module__name`, rewrite `from .x import y` and `x.attr` to mangled names, respecting function-local shadowing. It is throwaway and not in the repo.

On `harness` it works: it produced a single file that runs under CPython (`--help`, `status`, `ls --json` match the package), and with the same island tier it compiled to a 679 KB binary with identical output and exactly the same #642 divergence as Option A. It needed two fixes found by running it (class-body annotation targets such as TypedDict field names must not be mangled, and keyword arguments must not be mangled), which shows the class of silent bugs a rename pass invites.

What bundling breaks, shown on a 3-file package (`/tmp/bd`: `__init__` that prints, `cli` and `util` importing each other, `__name__` and `__file__` read in `util`). CPython prints `init mp / cli mp.cli / util mp.util / HI6 util.py`. The bundle:

| Area | Bundle result |
|---|---|
| Package `__init__` body | dropped by the prototype; a real bundler must inline it in first-import order |
| `__name__` | `__main__` instead of `mp.util` (`len(cli.__name__)` changes the output), module attribute reads like `cli.__name__` need synthetic module objects |
| `__file__`, package data, `Path(__file__).parent` | points at the bundle, not the module |
| Import order and side effects | toposort ran `util` before `cli`, so the prints reorder; cycles have no valid order (a lazy init protocol is needed, which is a module system) |
| Circular imports | a cycle needs per-module init functions and deferred bindings, the same machinery the native lowering already refuses |
| Relative and function-level imports | rewritable only when the target is a known unit; `from . import x` needs a module object; lazy function-level imports lose their laziness |
| `importlib`, `pkgutil`, `sys.modules`, `-m` semantics, tracebacks | all observably different (one file name, one namespace) |
| Name collisions, `global`, `del`, `__all__`, star imports, class-scope names, keyword names, string annotations | each needs scope-aware renaming; two bugs surfaced in 80 lines |

Bundling makes the single-file pipeline work, and it can then use native lowering inside the bundle (a clean bundle keeps native functions), which Option A does not. That is its only advantage.

## Recommendation

Ship Option A. It is 150 lines, no runtime or lowerer change, and keeps CPython's module semantics because it uses CPython's module system. Option B is the same island result for harness with a large semantic surface that must be maintained (a mini linker) and is wrong on `__name__`, `__file__`, order and cycles.

Kill criterion for A: drop the coarse promotion in favour of the real per-module planner (or revisit bundling) if, on the next two multi-module programs, a program that previously had at least a third of its definitions native ends up fully island and the compat or timing receipt regresses because of that, or if the program-wide island hits a case CPython's own import machinery handles differently from the embedded finder (zip or namespace-package layouts we embed wrongly). The reverse kill for B: not pursued unless native-inside-the-bundle is measured to matter and the bundle passes the `__name__`, `__file__`, order and cycle cases above.

Boxed ledger: new code is `boxed.py` (about 100 lines) plus marked regions in `package.py` (about 45 lines); no unmarked boxed branches added.
