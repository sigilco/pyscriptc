# The orca repo method for splitting very large files

Mined 2026-11-04 from ~/Documents/prjcts/_own/orca (read only): `.agents/plans/`,
`.oxlintrc.json`, `config/max-lines-baseline.txt`, `tools/class-method-extractor/`,
git history (`46f53de`, `eddbcd0d`, `4405219c`, `17671a25`), `.github/workflows/pr.yml`.
Transfer target: `src/pyscriptc/lower/` mixins (#852), current splits #910-#915.

## The loop they ran

1. Measure. Inventory every class member: LOC, kind, fan-in/fan-out (`this.name`
   hits in the body), and a field-access matrix (method x attribute). One-off
   JSON dump in /tmp, regenerated per session because HEAD moves.
2. Cluster. Union-find over the matrix; report component sizes and the
   "connector" members that fuse otherwise separate components (one field read
   by 94 methods fused the spine; removing connectors gave 20 clean sub-clusters).
3. Extract state ownership first when a connector fuses everything, so the
   remaining components separate.
4. Codemod, do not hand-edit. A spec-driven mover (`tools/class-method-extractor`,
   ts-morph): input is JSON `{target, methods[], deps {name: kind}, stateFields[]}`
   where kind is direct/callback/lazy per dep. It refuses to move when the
   computed external capture set does not match the spec.
5. Move per batch: generate the new file (facade class + deps interface), leave
   net-zero delegation stubs in the old file, rewrite `this.x` to `deps.x`.
6. Equivalence oracle per batch: normalize bodies per the substitution map
   (this.x -> deps.x), then compare AST equivalence (difftastic); exit non-zero
   on drift.
7. Gates split by cost. Per batch (cheap, parallel-safe): cycles count, lint of
   changed files, pattern asserts, mover self-check. At milestones only
   (expensive, serialized on one worker): full typecheck + dead-code audit.
   Rationale: parallel heavy gates OOM'd an 8GB machine.
8. Commit per batch, conventional message with stats: `refactor(runtime):
   extract RuntimeManagedWorktrees facade (67 methods, ~3.7k LOC)` plus the LOC
   delta and gate results in the body. Escalation rule: a batch failing gates
   twice gets a narrower spec (fewer methods), not hand-edits.
9. Ratchet, not suppression. `config/max-lines-baseline.txt` lists the files
   allowed over budget; the list may only shrink. A CI check greps for new
   `max-lines` disables or per-file bumps and fails against the baseline.
   AGENTS.md forbids disables and vague names (`helpers`, `utils`, `misc`).

## Exact thresholds

oxlint `max-lines`, skipBlankLines+skipComments: ts 300, tsx 400, mjs 600,
tests 800. No max-statements/complexity/depth/params rules: file size was the
single enforced size knob. Target band for new files: 500-2500 LOC, one concern
per file; oversized legacy test files over 2.5k LOC were left as-is (low value).

## Cycle and dead-code tooling

Per-batch cycle gate: `rev-dep` (Go), budget "cycles <= 174", fallback
`knip --cycles`; full knip pass only at milestones (OOM-prone). `audit:dead-code`
= `pnpm dlx knip@5.88.1 --config config/knip.json` (~30 explicit entry points,
`ignoreExportsUsedInFile: true`) to delete wrapper stubs once no external callers
remain. Neither runs in CI; cycles were a local per-batch gate. PR CI runs lint
plus the max-lines ratchet check (pr.yml lines 210, 293-303).

## What the commits looked like

Whole methods moved to a new file, delegation stubs left behind, spec JSON
committed alongside, stats in the message body, no barrel/re-export layer:
`46f53de` extract RuntimeAutomationCommands facade (15 methods), 31473 -> 31195
LOC, gates listed in the body; `4405219c` extract worktree cluster (67 methods,
~3.7k LOC); `17671a25` pty cluster (135 methods, ~3.3k LOC). When one ~500-line
function exceeded the 300 budget they split pure computation from side effects
instead of adding a disable. Test files split under the same regime at 800.

## Pitfalls they hit

- Parallel heavy gates OOM: serialize expensive gates, keep batch gates cheap.
- Baseline drift between sessions: refresh measurements at session start.
- Suppression creep: "Do NOT add entries to get CI green"; ratchet shrinks only.
- Connectors fuse clusters: extract state ownership before moving behavior.
- Diffstat asymmetry (removed >> added with no explanation) = structural drift,
  monitor it between batches.
- Naming: never `helpers`/`utils`/`common`/`shared-stuff`; name by concern.

## Transfer to Python (pyscriptc/lower)

- ts-morph capture sets -> stdlib `ast`: per-method attribute reads/writes and
  `self.` call graph is computable without new deps (`scripts/lower_split_report.py`).
- Union-find over shared-attribute + call edges, same clustering idea; clusters
  become mixin modules, matching the existing #910-#915 pattern (methods moved
  into `Lowerer` via mixins, delegation is free: one class, many mixins).
- difftastic oracle -> `ast.dump()` compare of moved methods after renames;
  cheap Python gates: `ruff check` on changed files, import-cycle check of
  lower/, fence/census counts (pinned in tests/census_pins.json, `pyscriptc.lower`
  ceiling 201). Expensive gate: full pytest suite, serialized at milestones.
- max-lines -> a Python ratchet file like `tests/lower_lines.json` or pylint
  `max-module-lines`; the 1500-line target for lower/ sits in the orca band.
- Naming by concern transfers: `_expr_compare`, `_calls_more` already follow it.
- One commit per cluster, stats in the body; narrow the spec on repeated gate
  failure. The orca facade-with-deps shape is NOT needed in Python: mixin
  methods keep sharing `self` state, so the capture-set check is advisory
  (which state a cluster touches) rather than a correctness gate.
