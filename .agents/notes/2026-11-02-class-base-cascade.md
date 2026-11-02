# The class-base cascade: measured options and the recommendation

Date: 2026-11-02. Branch `work/cascade` (base `work/stdlibtab`, `b9fad6bd`), worktree
`~/.worktrees/cascade/main`. Question: can a subclass of an island-floor base lower
natively for its clean methods, with the island base kept as a handle, instead of the
whole subclass demoting at `lower/_classes.py:665` (`island_floor_classes` refusal)
and `:681` ("this class base is not lowered")?

Target tree: `/tmp/ytdlp-classconst/yt_dlp` (1043 units). Harnesses: the stdlibtab
per-definition plane map (`/tmp/stdlibtab_defs.py`, results `/tmp/stdlibtab_defs_now.json`)
plus a new fence-relaxation probe (`/tmp/cascade_probe.py`, results
`/tmp/cascade_relaxed_fences.json`) that patches `ClassesMixin._resolve_base` to hand
back nominal class records for island-floor and unknown bases instead of fencing, so
lowering continues and every fence *behind* the cascade becomes visible. A CPython
semantics probe (`/tmp/cascade_semantics.py`) exercises the mixed-plane shapes against
the reference interpreter.

## 1. The cascade hides a second wall

The stdlibtab session counted 4268 of 4806 fenced definitions as "fenced only by the
class-base cascade". That is true of the fence *capture*, and false of the fence
*causality*: `_collect_class` fences on the base and stops, so no definition ever gets
the chance to report its own fences. With the base refusal relaxed, the per-def residual
over the same 4800 fenced definitions is:

| residual fence family (cascade removed) | definitions |
|---|---|
| PS1204 class-body assign only | 2159 |
| PS1001 annotation + PS1204 | 1817 |
| PS1001 annotation only | 493 |
| PS1201 decorators | 29 (+18 mixed) |
| clean | 36 |

The cascade was masking the real wall: **class-body assignments (PS1204)**. A raw
`ast.walk` over yt-dlp class bodies counts ~14.8k `Assign` statements, of which 14,212
are `ast.literal_eval`-clean constants (module tuples of `_VALID_URL` regexes, `_AGE`
limits, API key strings) and ~600 are not (f-string `JoinedStr`, `re.compile` calls,
`BinOp` concatenation, name references to earlier members).

## 2. Cumulative flip counts by option

Per-definition flips over the 4800 fenced defs (native today: 23 of 4836, 0.48%):

| intervention | defs lowered | native share |
|---|---|---|
| today | 23 | 0.48% |
| (1)/(2): mixed-plane subclass allowed, base still island | **+36 → 59** | 1.2% |
| + PS1204 const class-body table (the base lowers, its body consts land in a class-const table) | **+2159 → 2218** | 45.9% |
| + `--infer` pins (PS1001, the `work/ytnative` line) | **+2310 → 4528** | 93.7% |
| + PS1201 decorator admissions (classmethod/staticmethod/property) + PS1202 kwargs | **+272 → 4800** | 99.4% |

The option-1/2 number (36) is exact, not an estimate: it is the count of definitions
whose *every* fence is a cascade fence, i.e. the subclasses that are fully clean apart
from having an island base. Under option 1 or 2 that set is all that can flip, because
the island base itself still fences on its own PS1001/PS1204/PS1201 body.

The decisive fact: **once the base lowers, the cascade dissolves by itself.** Option 3
does not need any mixed-plane machinery, and it flips 160× the definitions.

## 3. The options, honestly

### (1) Native subclass with island base handle — S effort, ~36 defs, rejects

The island class machinery of #628 (`island_classes`, `island_floor_classes`,
`_island_floor_class_call`, the `ps_island_fn_invoke` seam) already carries class
*handles* across the boundary. What it cannot carry is a subclass that is half record,
half island object. The CPython probe shows the three shapes the compiler would have to
reproduce, each of which CPython gives for free because both planes are one dict:

- **Storage split.** The island base's `__init__` writes `self.name` into an island
  object; the native subclass writes `self.n` into a struct. If the seam copies at the
  boundary, island-side later writes (`self.name = renamed` in a base method) are stale
  in the native view and vice versa. There is no copy protocol that preserves Python's
  single-dict mutation coherence without making every attribute access a boundary call.
- **Virtual dispatch island → native.** `InfoExtractor.extract` (island) calls
  `self._real_extract` (native override of every one of the ~1200 extractor subclasses).
  A native receiver in island code means island code holds a struct it cannot see into;
  an island receiver means the native method is invoked through the seam with a
  marshalled `self` whose fields copy back — mutation during `_real_extract` (which
  writes `self._cache`-style fields constantly) would not cohere.
- **Identity.** yt-dlp's metaclass registers extractor classes in island-side dicts by
  class identity; a native subclass is a struct layout, not a class object, and never
  appears in the registry. `except` identity is the same: an island base raised
  island-side is not caught by a native `except Subclass` clause unless the exc-code
  mapping carries the subclass relation across planes (today it does not;
  `lower/_classes.py:1782` even fences `except` of an island-floor class outright).

A *conservative* slice (subclass with no overridden island method, no `super()` into the
island, constructed only natively) is sound, but it is exactly the 36 defs — and 34 of
them still need PS1204 relaxation first (`tests` show the clean count only exists under
the relaxed fence capture). S effort for 0.7% of the gap: not the lever.

### (2) Per-method planes (class object island, clean methods native) — L effort, same 36 defs, rejects for now

Same 36-def ceiling: per-method planes help a class whose *base* is native but whose
body fences; here the base itself fences, so its methods are island regardless of the
subclass's plan. The real per-method-plane work (dual-plane receiver, the `#628` handle
extended to methods) is worth building only when there are many "native base, fenced
method" classes — the measurement says yt-dlp is not that shape. Defer until the
S5-class-field-shapes stage of the inference roadmap produces such classes.

### (3) Make the base lowerable — M per stage (already roadmap-planned), 4777 defs, **recommended**

The base's own fences, measured under the relaxed capture (`InfoExtractor`, 200 fences):
145 unannotated parameters, 13 unannotated returns, 17 class-body assigns, 5 decorated
methods, 2 nested ClassDefs, 6 PS2002 name resolutions, 1 PS1202 `**kwargs`
(`RetryManager`), 1 unlowered call (`random_letters`). Nothing structural: no generators
in class scope, no metaclass beyond the two known, no closures.

Every fence is on an existing roadmap:

- PS1001 → the `--infer` pin pass (`work/ytnative` line, `lower/_paraminfer.py`;
  inferplan S1–S4). The ytbottle-line census already reports native 2,440 of 7,884 defs
  on that lane — consistent with pins clearing most of the 2310 PS1001 defs counted here.
- PS1204 → S5 of the inference roadmap (field shapes) plus a const-table variant for
  plain constants (the `_VALID_URL` shape): a class-body `Assign` whose value
  `literal_eval`s lowers into the existing module-const machinery, named
  `Class.name` in the const table. ~14.2k of ~14.8k sites qualify.
- PS1201 → admit the erased-form decorators (`classmethod`, `staticmethod`,
  `property`) that already have restlower dispatch; refuse the rest as today.
- PS1202 → the one `**kwargs` (`RetryManager.__call_wrapper`) either admits keyword
  forwarding for this shape or keeps that one method island — with the base's other
  methods native this is the per-method-plane case, but one method at S cost.

Sequencing: PS1204-const first (M, standalone, flips 2159 defs and dissolves most
extractor-class fences), then pins (M, planned), then PS1201 (S). Risks:

- Fence-message changes: `tests/test_package.py:216` pins "this class base is not
  lowered" as the first fence for a missing base — that message stays (the base is
  genuinely absent); only the island-floor-path message changes behavior, and
  `tests/test_island_program.py:398` / `test_plane_split.py` pin those — affected tests
  only, updated in the same PR per the docs rule.
- IR hashes: class-body consts enter the const table and change IR for any class with
  class-body assigns; `benchmarks/compat2` `IR_HASHES.json` regeneration needs the A/B
  byte-identical discipline, and any `must_fence` probe that expects PS1204 needs the
  recorded-decision route. The gate-adjacent prerequisites are already listed in
  inferplan S4/S5.
- Const-table semantics: a class-body assign executes at class-creation time in
  definition order and can reference earlier members (`BinOp` chains, 80 sites); the
  const table must preserve order or fence the dependent forms (the ~600 non-const
  sites fence PS1204 as today).

### (4) Do nothing — rejected, and the "50% not reachable" claim is now false

stdlibtab concluded the 50% goal needs "native records for island-floor base classes or
per-class island routing". That conclusion rested on the capture-time view in which the
cascade hides everything behind it. The relaxed measurement shows the walls behind it
are the *planned* inference walls, and 45.9% of the native share is reachable with the
PS1204 const table alone, 93.7% with the pins lane that already exists on
`work/ytnative`.

## 4. Recommendation

Build option 3, in the order above, on the existing roadmap branches; do not build the
mixed-plane subclass (options 1–2). Effort: PS1204-const M, pins M (planned S1–S4),
PS1201 S, RetryManager PS1202 S; total an M-epic, not an L one, because every stage is
independent and gate-safe. The 36-def mixed-plane slice stays available as a later
per-method-plane milestone once the bases lower and produce the "native base, fenced
method" shape that makes it worthwhile.

Artifacts: `/tmp/cascade_probe.py` (fence-relaxation harness),
`/tmp/cascade_relaxed_fences.json` (relaxed capture),
`/tmp/cascade_semantics.py` (CPython mixed-plane probe), this note.
