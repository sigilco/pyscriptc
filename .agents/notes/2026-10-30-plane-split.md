# Plane split: per-module island granularity

## Current rule

Under `--fallback=cpython` (`--island`), `_lower_units` in
`src/pyscriptc/package.py` runs the per-definition planner per unit, but
then throws the plans away: when any unit carries a fence (or
`_boxed.unit_needs_island` says its own plan put anything on the island),
the whole program becomes `_program_island` (Refs #641). The merged IR
module gets `functions: []` and one `islandSource` seed that execs every
unit's source inside the embedded CPython, so a single fenced unit costs
every clean definition in the tree. On yt-dlp (2309 native-capable
definitions) the binary reports 0 native definitions and 0% executed
native code, because of this one decision.

Inside one module the granularity is already the definition: the planner
(`src/pyscriptc/boxed.py`, `plane_map`) routes each fenced definition to
the island floor, the lowered list drops it, the module's Stage-2 seed
(`islandSource`) carries only the segments the island definitions need,
and a native caller reaches it through `ps_island_fn_invoke`
(`INVOKE_SYMBOL`). That seam is the mechanism to reuse; there is no
second one to invent.

## Proposed rule

The island's granularity becomes the module, then the definition inside
it:

- A module with no fences lowers native, unchanged. Its functions enter
  the merged module as they do today.
- A module with fences keeps its own plan: its fenced definitions route
  to the island floor, its clean definitions lower native, and its
  `islandSource` is its Stage-2 seed. `_program_island` stops firing for
  this shape; it stays for shapes the split cannot lower (the entry-only
  cycle seed of #678, a package compile that hits the import refusal).
- The merged module carries `islandModules: {module_name: seed_source}`
  (new IR key) instead of one program-wide `islandSource` when more than
  one module plans island code; a single island module keeps the old
  `islandSource` key so existing consumers stay working.

## Boundary calling convention

Crossing calls reuse the boxed boundary, namespaced by the defining
module:

- Runtime (`src/pyscriptc/runtime/ps_island.c`): the island holds one
  namespace per program module instead of one per binary. New boxed
  symbols `ps_island_mod_seed(mod, source)` (exec the module's Stage-2
  seed into its own namespace, a real module object registered in
  `sys.modules` so `__name__` and pickle behave), and
  `ps_island_mod_fn_invoke[_value](mod, name, args...)` (call a
  definition of that module's namespace). The single-namespace symbols
  keep their ABI for one-module compiles; the per-module pair is the
  same `ps_island_fn_invoke` seam with a module key in front.
- Lowerer (`src/pyscriptc/lower/`): a call site whose export-table entry
  carries `island: true` (the defining module planned it) lowers to the
  per-module invoke instead of refusing with PS2002. The marshalling,
  the `want` unboxing and the handle ownership are the existing
  `_island_fn_call` machinery; only the callee key grows the module.
- A native module importing an island module's definition still imports
  the module handle through `ps_island_import` semantics only when it
  has to; the minimal slice calls the definition directly by
  `(module, name)` and never binds the module object.

## Soundness risks

- Module globals shared across planes: a native module reading a global
  slot that an island module owns reads a stale or missing slot. The
  minimal slice does not export island-owned globals (the export table
  already skips `slot.get("island")` entries), and a consumer of one
  refuses as today. One-directional: island-side rewrites of native
  slots stay invisible, as the #613 seam documents.
- Import-time init order: the per-module seeds run in the merged
  module-init order (import order), before the entry body, so an island
  module's top-level assignments land before a native module's init
  reads them only when the import order says so. The seeds must run in
  the same order CPython would import the modules; the existing
  `_module_init_order` gives that order and the emitter emits the seed
  calls in it.
- Exceptions across the boundary: an island call that raises maps to the
  island error path (`ps_island_fail`) as today; a native caller's
  `except` clauses see the island's exception class only through the
  existing exc-code mapping. The minimal slice tests only the
  exception-propagates case, not `except SomeIslandClass`.
- Class identity: an island module's class crossing to a native caller
  is an opaque handle (issue #628), not the native class record. Two
  modules must never both define the same class name natively; the
  merged class-name collision fence (PS2002) stays.
- Mutable container arguments across the boundary are copy-in (the #674
  demotion does not cross modules in the minimal slice): a native caller
  passing a list to an island callee does not see in-place mutation. The
  demotion rule stays intra-module; crossing callers accept the copy-in
  seam like island-lane imports do.

## Out of scope

- Per-definition split across modules: a native module calling one of
  two functions of a fenced module where only one is island-planned is
  the same seam, but the tests cover the whole-module case only.
- Island classes, island globals and island top-level script statements
  crossing a module boundary (a fenced module whose top level is itself
  island keeps working only when nothing native reads its globals).
- The island calling back into native code across the boundary (the
  island-to-compiled seam stays the `--emit binding` surface).
- yt-dlp reaching 100%: the fenced modules route whole; lifting their
  fences is the other walls' work.
