# Project bundler (`src/pyscriptc/bundle.py`, issue #832)

An opt-in, AST-level bundler for a multi-module project tree. It is not
wired into the compile path or the CLI; the only entry point is
`bundle_project(entry: Path) -> Bundle`.

## What it does

Given the entry file of a project tree (every `.py` file under the
entry's directory), it:

- Discovers all modules and builds the intra-project import graph
  (absolute `import x` and `from x.y import z` forms; package
  `__init__.py` files map to the package name).
- Orders the modules topologically, dependencies first, following only
  imports reachable from the entry. The entry module comes last.
- Renames every top-level name (functions, classes, assigned module
  constants) to a deterministic `module__name` form (`a.b` becomes
  `a__b`), so same-named symbols in different modules cannot collide.
- Rewrites each module's top-level AST: project imports are dropped and
  their use sites are rewritten to the renamed target symbols, both for
  plain names (`from helper import greet` -> `greet(...)`) and dotted
  attribute chains (`import helper` -> `helper.greet(...)`).
  Non-project (stdlib, third-party) imports are kept verbatim.
- Concatenates the modules in dependency order into one source string
  and records a line map: every bundled line maps back to
  `(original file path, original line)` for diagnostics
  (`Bundle.original_location(line)`).

## Refusals (`BundleError` with a clear message)

- Import cycles in the project graph (the cycle is named).
- `import *` from any module.
- Relative imports (`from . import x`, `from .x import y`).
- A project module used as a bare value (`helper` alone, not followed by
  an attribute) -- modules become prefixes, not objects.
- One local import name bound to two different project symbols
  (`from a import config` + `from b import config`); use an explicit
  `as` alias, which works.

## Known limitations

- Only module-level imports are considered; function-local imports of
  project modules are neither tracked nor rewritten.
- Import statements inside `try:`/`if:` blocks are not visited.
- `if TYPE_CHECKING:`-only names referenced at runtime would resolve
  incorrectly; keep runtime imports at module level.
- A dotted path more than one segment below a project module
  (`helper.sub.thing`) is not resolved.
- The line map has statement granularity: a multi-line statement maps
  each of its bundled lines to the corresponding original line,
  clamped to the statement's end.

## Tests

`tests/test_bundle.py` covers dependency order, prefix renaming across
colliding names, aliased disambiguation, cycle refusal, the line map
round-trip, unreachable-module pruning, and the refusal diagnostics.
