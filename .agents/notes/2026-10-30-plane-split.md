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

## What the yt-dlp measurement shows

The split is measured on the yt-dlp tree (`2026.10.26`, 1042 units). The
executed native fraction stays 0%: the split does not engage for yt-dlp, and
the three reasons are ordered by how much work each one is.

1. The module top levels fence on unlowered stdlib imports. Every yt-dlp
   module imports stdlib modules outside the compiler's closed import table
   (`binascii`, `codecs`, `collections.abc`, `email.header`, `html.parser`,
   `locale`, `mimetypes`, `netrc`, `secrets`, `shlex`, ...). A module-level
   fence routes the whole top level island-side (Decision A), and the #620
   cascade then pulls the module's definitions onto the island with it. The
   measured per-module plan over all 1042 units is 1031 modules with an
   island top level and 11 with a native one, and 472 planned definitions of
   which 3 are native. This is the wall that matters: it is a stdlib-coverage
   wall, not a plane-split wall, and the split cannot lift it.
2. The program is package-shaped. `yt_dlp` has 16 package units (the
   `__init__.py` files), and the per-module seed cannot reproduce the
   package semantics. The experiment (relaxing the rule) shows the island
   seed re-executing the package `__init__` (its prints double) and an
   island function reading `__file__` finding no value: the per-module seed
   has no finder bootstrap that serves the program's own modules. The
   whole-program bootstrap has one, which is why the whole-program island
   works.
3. The program has three circular imports (`yt_dlp.utils.traversal`,
   `yt_dlp.networking.common`, `yt_dlp.extractor.common`). The island lane
   runs CPython, which resolves a cycle; the native lane's init order
   cannot, and `_program_plan` refuses the cycle before the per-module plans
   are computed at all.

So the split's own design goal is met on the shape it covers (plain-sibling
programs), and the yt-dlp number does not move because the program never
reaches the split. The next wall for a native fraction on yt-dlp is the
stdlib import table, not the boundary seam.
