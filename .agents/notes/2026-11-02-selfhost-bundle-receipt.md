# Self-host bundle receipt: census and the amalgamation counterfactual

Date: 2026-11-02. Branch `work/tirgate`, worktree `~/.worktrees/pyscriptc-tirgate`. An input to the self-hosting reassessment (roadmap R2 close-out). No gate file was edited.

## Method

- Per-module census: `uv run python -m benchmarks.compat2.census_self --json /tmp/tirgate-census.json` on this checkout, parse-only, one directory compile of `src/pyscriptc`. Receipt: `benchmarks/compat2/fixpoint.json` (the fixed-point lane) and the table below. 42,488 lines, 95 modules, 1,632 definitions, 2,706 fences, 1,279 fenced definitions (78.4%).
- The bundler prototype: **not usable, and it does not exist.** `work/selfbin` is the self-compiled *bundle binary* lane (`--embed-python bundle`, `tests/test_selfbin.py`); it packages the tree for the runtime, it does not amalgamate the Python source. The AST-level bundler in the roadmap (R7) and in `/tmp/better-compiler.md` ("a 100-line AST-level bundler") has not been written on any branch checked here (searched every local and remote ref for amalgam/bundler source files). So the "native share after amalgamation" below is a counterfactual computed from the census family attribution, not a measurement of a run bundler. A real receipt needs the R7 bundler built first.

## Census, per module (top 25 by fences)

| module | fences | defs | fenced defs |
| --- | --- | --- | --- |
| cli | 330 | 38 | 38 |
| deps | 136 | 40 | 40 |
| lower/__init__ | 113 | 74 | 74 |
| typefacts | 109 | 48 | 48 |
| emit/__init__ | 106 | 6 | 6 |
| lower/_calls | 99 | 62 | 62 |
| lower/_classes | 93 | 71 | 71 |
| lower/_stmt | 83 | 59 | 59 |
| ffi_manifest | 81 | 33 | 33 |
| coro | 80 | 70 | 70 |
| lower/_driver | 79 | 9 | 9 |
| lower/_expr | 79 | 46 | 46 |
| factsbridge | 59 | 32 | 32 |
| package | 54 | 35 | 35 |
| config | 53 | 16 | 16 |
| stdmore | 53 | 10 | 10 |
| check | 51 | 12 | 12 |
| lower/_stdlib | 50 | 27 | 27 |
| imports | 45 | 23 | 23 |
| stubs | 43 | 23 | 23 |
| lower/_core | 40 | 37 | 37 |
| reduce | 36 | 28 | 28 |
| lower/_itertools | 34 | 16 | 16 |
| stdlib_queue | 33 | 8 | 8 |
| sigregistry | 32 | 18 | 18 |

94 of 95 modules carry at least one fence; only the package root is clean.

## Family totals over the whole tree

dynamic-annotation 650, name-resolution 493, export-cascade 429, unlowered-type-name 375, construct-other 149, unlowered-call 101, field-lookup 90, local-inference 86, import-table 65, keyword-args 41, union-types 39, decorators 33, type-mismatch 28, generic-alias 25, call-arity 19, module-assign 15, everything else under 10 each.

## What amalgamation would and would not change

Collapsing the tree into one module removes exactly one fence family: `import-table` (65 fences, 2.4%), the cross-module import fences the closed import table raises. It also removes the cross-module *dataclass* blocker the same family creates, which is the real point of R7: it is a blocker, not a fence count.

It does not touch the other 97.6%: dynamic-annotation, name-resolution and export-cascade are per-definition analysis fences that fire the same way inside one giant module; unlowered-type-name and construct-other are stdlib-surface fences. Name collisions would add work (the better-compiler hazard list: top-level symbol prefixing, cycle refusal, deterministic ordering), and a failed amalgamation is a new fence surface of its own.

## Native share estimate

Today: 95 modules, 2,706 fences, 1,279 of 1,632 definitions fenced (78.4%). After the counterfactual amalgamation: 2,641 fences, a 2.4% fence reduction; the definition fence rate is unchanged because amalgamation does not make any definition easier to type. The honest statement for the reassessment: the bundler changes the self-host *reach* (the tree becomes one compile unit that can clear the import table), not the native *share*. The native share moves only with the fence families the IR rearchitecture (dynamic-annotation, 650) and the stdlib lowering surfaces (name-resolution, unlowered-type-name: 868 together) address, which is exactly the reassessment's 20-to-25-percent ceiling argument applied again.

Where: the census JSON is `/tmp/tirgate-census.json` (session artifact; rerun with `uv run python -m benchmarks.compat2.census_self --json census.json`), the fixed-point receipt is `benchmarks/compat2/fixpoint.json`, and the roadmap input is `.agents/plans/2026-11-02-ir-rearchitecture-roadmap.md` R7.
