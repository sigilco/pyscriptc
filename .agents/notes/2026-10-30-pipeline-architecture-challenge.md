# Pipeline architecture challenge: finding and removing the self-inflicted bottlenecks

Session: archchal, 2026-10-30. Worktree `~/.worktrees/pyscriptc-archchal`, branch `work/arch-challenge`.
Evidence base: this session's measurements plus `/tmp/infres.out.md` and `/tmp/inffast2.out.md`
(sessions infres and inffast2 on `work/inference-fast` / `work/inference-fast2`).
Corpus: yt-dlp at `/tmp/pkgs/yt_dlp` (1,041 files, 7,883 defs package-wide; utils/ is 8 files,
522 defs). Checker floor: `pyrefly check .` over the whole package is 1.19s; every pyrefly tsp
request in one full `infer` run totals 4.4s.

The one-line verdict: the checker answers in seconds, and we then spend minutes re-deriving,
re-walking and re-lowering the same ASTs dozens of times. The architecture treats
`Lowerer.module()` as a monolithic whole-module transaction, and every consumer of any of its
outputs re-runs the whole transaction.

## 1. The current pipeline as measured

### Stages

```
pyscriptc coverage DIR / infer DIR
  discover units                     (package.py _discover, _prune_unreached)
  catalog fixpoint                   (package.py _catalog -> _catalog_once, line 932:
                                      for _ in range(len(units)+1): lower EVERY unit with
                                      lowerer.cataloguing=True, i.e. full module() per round)
  final lowering pass                (package.py _lower_units: full module() per unit)
  coverage report assembly           (coverage.py build_report: dedupe, annotate)
for infer, wrap that twice:
  build_facts                        (inferrunner.build_facts: one tsp server, per-file facts)
  build_report baseline              (the above catalog fixpoint + final pass)
  pinned_facts                       (inferrunner.pinned_facts: pinned mirror + tsp re-check)
  build_report inferred              (the above catalog fixpoint + final pass AGAIN)
```

Single-file `pyscriptc compile FILE` runs `Lowerer.module()` once (`lower_source`). The
monolithic-transaction problem only bites on the directory paths, but those are exactly the
paths that matter at scale, and `infer` and `coverage` are directory commands.

### How many times each stage runs per file (measured)

cProfile of `pyscriptc infer` on yt-dlp utils/ (8 files, 522 defs), total 865s under the
profiler (3m29s without, commit 7346dd8):

| Stage | Runs per file | Seconds total | Share |
|---|---|---|---|
| `module()` | 10 (6 catalog rounds + 1 final, x 2 build_report passes) | 822 | 95% |
| inside it: `_pin_call_site_params` | 10 | 812 | 94% |
| inside it: `_call_site_types` | 362 per pass (2,896 total) | 811 | 94% |
| `_catalog` fixpoint (all rounds) | 2 build_report passes x rounds | 637 | 74% |
| tsp: build_facts + pinned_facts | once per run | 28.5 | 3% |
| subprocess/teardown waits | 3 servers | 15 | 2% |

The hot profile line is 117.5M calls to `ast.iter_child_nodes` and 60.8M to `ast.walk`:
we are repeatedly walking whole module ASTs to answer per-definition questions.

### Where whole-program fixpoints and re-lowering occur

- `package.py:932`: the catalog fixpoint re-lowers every unit a full `module()` until the
  export table stops changing, bounded by `len(units)+1`. On utils/ that is 6 rounds where
  2 would do in a sane world (the table converges after the first cross-module edge chain)
  and where 0 full re-lowerings would do if exports came from signatures only (see 3a).
  The only delta between rounds is a handful of signatures crossing module boundaries, yet
  the round re-runs body lowering, pinning, boxed planning and IR-shaped dict building.
- `coverage.py build_report` and the `infer` runner each drive the whole pipeline above; the
  inferred pass reuses nothing from the baseline pass (`inffast2` fix 4 targets this).
- `_pin_call_site_params` (lower/__init__.py:771) runs inside every `module()` call, including
  the cataloguing ones, and its per-def `_call_site_types` recomputes caller-body facts
  (`_bindings_types`, `_infer_type_of`, `_visit`) that are def-independent, so per pass the
  cost is defs x callers, not callers.

### The other big consumer: coverage on the whole yt_dlp package

`pyscriptc coverage /tmp/pkgs/yt_dlp --json`: 1,041 files, wall time 302s, exit 0,
11.4 MB report JSON. That is roughly 0.29s per file for a report the checker floor
(1.2s for all 1,041 files) says should cost seconds, not minutes. Same architecture:
the catalog fixpoint over 1,041 units x N rounds of full `module()`.

### Cross-check against the owner target

tsc/pyrefly do whole-program analysis in seconds to low minutes for this tree. We are at
302s (coverage) to 865s (infer) on subsets plus 302s (coverage), i.e. 100-700x the checker
floor. The gap is not inference difficulty; it is repeated work.

## 2. Structural causes

1. **Lowering is a monolithic per-module transaction.** `module()` interleaves desugaring,
   class collection, signature discovery, call-site pinning, boxed/island planning, body
   lowering and fence assembly. A consumer that needs only the export table (the catalog)
   or only signatures must run all of it, and must rebuild a fresh `Lowerer` per unit per
   round, re-parsing the file text each time (`_catalog_once` does `path.read_text` +
   `ast.parse` on every round).
2. **The catalog fixpoint recomputes everything per round.** The fixpoint variable is the
   export table, but the recompute unit is the full module. A worklist that re-catalogues
   only the modules whose exports changed last round (or, after 3a, that converges
   signatures-only once) removes the multiplier. The `len(units)+1` bound exists only
   because the recompute unit is too coarse; a dependency-ordered propagation converges in
   (longest import chain) rounds, not (number of units) rounds.
3. **No per-definition or per-module summary cache.** Nothing is memoized across the 10
   `module()` invocations per file, across the baseline/inferred passes, or across runs.
   The pin pass re-derives caller-local facts per def; the catalog re-parses per round; the
   inferred pass re-lowers files no pin touched.
4. **Module dependency order is ignored.** Units are processed in discovery (directory)
   order, so export information propagates one hop per fixpoint round instead of being
   topologically propagated. The self-compilation target imports cleanly as a graph
   (a Tarjan SCC walk over `src/pyscriptc` shows 107 modules, 0 cyclic SCCs; the only
   back-edge is the deliberate function-level lazy import of `pyscriptc.package` inside
   `lower/__init__.py:3351`), so a topo order exists for our own tree and for most real
   packages; true cycles fall back to one extra round in a worklist scheme.
5. **The pin pass is quadratic by construction.** `_call_site_types` per pending def walks
   every caller's full body. Caller facts do not depend on the callee being pinned
   (except the pin-arrival-order subtlety inffast2 documents), so the def loop is pure
   recomputation.

## 3. Alternatives

Gate context: `IR_HASHES.json`, canonical IR byte-identity and the compat2 probes are frozen;
touching them needs a recorded decision plus the A/B byte-identical discipline
(two `irscore` runs, `cmp`). All options below are designed to keep generated IR
byte-identical; the only ones that could move bytes are 3f (needs a decision) and any change
to pin-arrival order that survives differently (inffast2 fix 2 documents exactly this risk).

### (a) Split lowering: signature collection vs body lowering

Signatures come from annotations on the AST plus synthetic/boxed planning; body lowering
needs signatures plus the export table. Split `module()` into:

- `collect_signatures(tree)`: cheap pass, annotations + defaults + synthetics, no bodies.
  Run once per unit, propagate exports to fixpoint over signatures only (an order of
  magnitude cheaper than today's round, since no bodies lower and no pins run), converging
  in dependency order.
- `lower_body(fn, sigs, exports)`: per definition, independent given the signature table;
  parallelizable across defs and modules; cacheable.

- Asymptotics: catalog fixpoint goes from rounds x whole-module to 1 signature pass + rounds x
  signature-only work. Body work runs exactly once per def per build_report pass.
- Migration cost: M. `module()` keeps its public shape (it can call both halves), the catalog
  switches to `collect_signatures`. The risk is hidden state coupling: the pin pass changes
  signatures (that is its point), so the signature fixpoint must include pins or the catalog
  contract must accept pre-pin signatures (infres fix 3; needs the owner decision about what
  the export table says for pin-rescuable defs).
- Gate risk: low if pins run to fixpoint inside the signature pass before it reports exports;
  byte-identical IR is achievable. The compat2 fence counts must not move (fences are
  currently emitted during full lowering; the split must keep catalog-time fence reporting
  identical, which is the delicate part, `_unexported_definitions` reads `lowerer.sigs` and
  `module["fences"]`).
- Stays byte-identical: yes, with the pin-order caveat above.

### (b) Summary-based interprocedural inference

Per-definition summaries (param pin types, return type, locals-relevant facts) keyed by
content hash of the def subtree; a worklist over the call graph instead of whole-module
reruns. This generalizes (a) from signatures to the pin/inference facts.

- Asymptotics: O(defs + call edges) per pass instead of O(defs x callers) per pass; a pin
  landing invalidates only its def's summary and its callers' pending pins.
- Migration cost: L. The lowerer is a mutable object with per-module state (`self.locals`,
  `self.sigs`, exc numbering), so extracting per-def summaries means threading that state
  explicitly. The pin-arrival-order effect (a pin landing mid-round influencing later defs in
  the same round) must either be preserved (priority order in the worklist) or proven
  order-insensitive, which needs the full suite plus the compat2 replay.
- Gate risk: medium; any change in pin outcomes moves fences and IR. Must be validated with
  the byte-identical A/B on the probe set.
- Stays byte-identical: achievable, with pin order pinned to source order initially.

### (c) Persistent on-disk cache keyed by (file content hash, dependency summary hashes)

This is the incremental step: memoize per-file lowering results (IR fragment, fences, export
table) at the `_lower_units` / `_catalog_once` boundary, keyed by (source hash, config hash,
hash of the export-table entries the file consumes). On re-run, unchanged files with
unchanged dependency summaries are reused; CI runs cost only what changed.

- Asymptotics: incremental re-run proportional to changed files plus their transitive
  consumers; first run unchanged.
- Migration cost: M for coverage/infer (the results are plain dicts of JSON-able data);
  S more if the merged-module exc numbering (`exc_n` handoff) is threaded per unit so
  fragments stay mergeable. Must define the summary hash carefully or false invalidations
  eat the win.
- Gate risk: none to frozen gates if the cache is transparent (same bytes out); the risk is
  staleness bugs, mitigated by content+dependency hashing and a `--no-cache` escape.
- Stays byte-identical: yes by construction (cache the exact dicts).

### (d) Parallelism across modules by SCC of the import graph

The catalog fixpoint and body lowering per module are independent given the export table;
multiprocessing over topological waves (SCCs) is straightforward. Keep CPU modest on this
shared VPS: worker count = min(os.cpu_count()//2, 4) with a flag, measured only off-peak.

- Asymptotics: wall time / effective parallel width per wave; bounded by the widest SCC wave
  (usually 1-2 for real packages; utils/ is 8 independent files).
- Migration cost: M (workers need `Lowerer` to be constructible from pickled args; it already
  is given path+text, the hard part is the merged exc numbering and deterministic fence
  order, which must stay sorted the way the single-process run sorts them).
- Gate risk: low to none if merge order is deterministic (sort by (file, line, col) as
  coverage.py already does); canonical IR is order-sensitive in the merged module body, so
  the merge must preserve current unit order regardless of worker completion order.
- Stays byte-identical: yes if the merge is order-canonical.

### (e) Moving hot passes out of Python

The typed subset compiling its own compiler is the long-term answer. What could realistically
move first, ranked by (hot, self-contained, subset-fitting):

1. `ast.walk`-shaped scanner kernels: `_bindings_types`, `_infer_type_of`, the pin-pass
   caller walk, `peek_namespace_fields`. These are 90% of the profile (117M
   `iter_child_nodes` calls), they are read-only over a tree, and they use the closed
   subset (dicts, isinstance chains, no reflection). A first step is not even moving them to
   native: replacing `ast.walk` with generated `_fields` iteration and caching per-node
   dispatch cuts constant factors in pure Python.
2. The canonical IR hasher and irverify (pure dict/string work, already subset-shaped).
3. The emitter (deterministic tree-walk to text) fits the subset well.
The self-compilation tie-in: pyscriptc's own source is 107 modules, ~1,822 defs today, no
import cycles, so it is exactly the kind of tree the summary pipeline parallelizes; the
reported 23.6% native share on it is the ceiling the island floor currently imposes, and
cycles in dogfood trees come from the deliberate lazy imports, which the summary/worklist
design tolerates without ordering (the worklist does not care about cycles). Moving the
scanner kernels to native both speeds every compile and grows the self-hosted fraction,
because those kernels are the first functions compiled with the improved summary pipeline.

- Asymptotics: constant-factor (5-20x on the moved kernels) on top of (b)'s algorithmic fix;
  do (b) first or the constants multiply.
- Migration cost: L for kernels (rewrite in the subset, differential-test against the Python
  version), XL for emitter or lowerer wholesale.
- Gate risk: high if the moved kernel disagrees on any input; keep the Python version as the
  oracle under a flag and differential-test.
- Stays byte-identical: must, verified by the existing golden corpora.

### (f) Use pyrefly's inference as the only engine, delete our call-site pin pass

pyrefly already infers types over the whole tree in 1.2s and answers per-expression facts in
4.4s total. `build_facts` collects call-site argument types; we could pin from those facts
only and drop `_pin_call_site_params` entirely (the lowerer's own proof, the "compiler's own
proof runs before the checker bridge" commit 881d8999 direction).

- What we lose: pins for call sites pyrefly cannot see or resolve (dynamic dispatch through
  aliases it fails on, the self-hosted closed-world cases where we know the callee exactly),
  and the property that pins are provable by our own lowering rules, so an IR a pin produced
  is by construction lowerable. Trusting pyrefly means trusting its type text maps 1:1 to our
  IR kinds (`parse_pin_type` already filters to what we can represent, so the mapping risk is
  bounded) and accepting pins our lowerer might fence on later, surfacing as fences at body
  lowering instead of being refused at pin time.
- Asymptotics: removes the pin pass, the single biggest line item (94% of utils/ infer time).
- Migration cost: S to try (facts exist), M to keep fence-parity.
- Gate risk: medium-high: pin sets change, so fences and IR change, and the compat2 pin
  receipts (`pinned_params`, `native_inferred`, `fences_inferred`) move. Needs a recorded
  decision and a calibration replay; if the deltas are all fence-count drops in the same
  families, that may be a feature, but it is not byte-identical.
- Stays byte-identical: no. This is the only option here that cannot ship as byte-identical.

## 4. Recommendation: ordered steps

Ordered so each step is independently shippable, measured and (except step 5) byte-identical:

| # | Step | As built on | Est. benefit (utils/ infer) | yt-dlp coverage | Cost | Gate risk |
|---|---|---|---|---|---|---|
| 1 | Ship inffast2 fixes 4+2 (memoize baseline->inferred; cache caller walk per pin round) | work/inference-fast2 | 3m29s -> ~60-90s | - | S | low, byte-identical required |
| 2 | Cache caller/def-independent facts across catalog rounds too (the same cache, kept across `_catalog_once` calls, invalidated on export-table change) | step 1 | catalog rounds drop from 6x to ~1.5x of one round | 302s -> est. 90-140s | M | low |
| 3 | Signature-only catalog (3a): catalog rounds run `collect_signatures` + pins, not bodies; final pass lowers bodies once | 3a | infer: remove ~40% of remaining module() work | coverage: remove body lowering from all fixpoint rounds | M | low-medium; fence-report parity is the test |
| 4 | Persistent on-disk cache (3c) at the unit boundary | 3 | re-runs proportional to change; CI pays deltas | re-runs ~O(changed) | M | none if transparent |
| 5 | Worklist summary inference (3b) absorbing the pin pass | 3 | O(defs+edges), removes the last quadratic term | same | L | medium; pin order must be pinned |
| 6 | Parallelism by SCC (3d) | after 3 | wall / wave width | 4x plausible | M | low |
| 7 | Native scanner kernels (3e), differential-tested | any time after 5 | 5-20x on the remaining walk cost | - | L | medium |

First step: land the inffast2 fixes (they are already implemented and measured
byte-identical on the probe outputs per infres), then step 2, which is the same
def-independent-cache idea applied to the catalog rounds where the multiplier is largest
(6 rounds x 8 files on utils/, N+1 rounds x 1,041 files on the package).

Pass/fail per step, on this VPS, same corpus, same commit discipline (byte-identical capture
before/after as inffast2 does):

- 1: utils/ infer <= 90s, byte-identical pins/fences/records. Fail if pin order changes any output.
- 2: catalog rounds contribute < 15% of build_report time; coverage yt_dlp <= 150s.
- 3: coverage yt_dlp <= 100s and `irscore` byte-identical A/B; the export table contract
  decision recorded if pre-pin signatures change any export.
- 4: warm re-run of coverage on yt_dlp after a 1-file touch <= 15s.
- 5: utils/ infer <= 20s; compat2 replay shows zero fence deltas.
- 6: 4-worker coverage wall <= 40% of single-worker on the extractor/ tree.
- 7: kernel outputs differential-equal on the whole yt_dlp tree; >= 5x kernel speedup.

## 5. Risks and what would reverse the recommendation

- **Pin-arrival-order sensitivity** (the known unknown from infres fix 2): if caching
  def-independent caller facts changes any pin outcome because a pin landing mid-round
  feeds `_infer_type_known` for later defs, the fix must serialize the cache per round or
  prove order-insensitivity. Reversal trigger: any compat2 fence delta attributable to order.
- **Fence-report parity in the catalog split (3a)**: `_unexported_definitions` reads
  lowering-produced fences today; if signature-only cataloging cannot reproduce the same
  fence set, the coverage report changes, which users see even though IR stays identical.
  Reversal trigger: the parity test cannot be made to pass without re-running bodies in
  catalog rounds, which deletes most of step 3's win.
- **Cache invalidation correctness (3c/step 4)**: a stale cache that reuses lowering across a
  dependency signature change produces silently wrong IR, worse than slow. Mitigation:
  dependency-summary hashing must include the full consumed export entries; reversal trigger:
  any staleness bug found in CI or dogfood that the hashing misses.
- **Frozen-gate churn**: steps that move fences or pins (3f, and a botched 3b) require the
  recorded-decision process; if the A/B cannot be kept identical, schedule them after a gate
  decision rather than as perf work.
- **Upstream replacement**: pyrefly already does package-wide inference in seconds; if our
  own inference stays needed only for the self-hosted closed world, the long-term shape may
  be (3f) plus a small local resolution pass, deleting the pin machinery entirely. That is
  the standing challenge to the architecture: we are maintaining a second inference engine
  whose measurable output (utils/: 154 of 522 defs native-inferred) pyrefly's 1.2s pass may
  already subsume. Measure that overlap before investing further in (3b); if pyrefly
  subsumes >= 90% of the pins on yt-dlp and dogfood, do (3f) with the recorded decision and
  stop building (3b).
- **Shared-VPS measurement noise**: all numbers here are single runs under modest CPU
  discipline; re-measure pass/fail criteria off-peak with two runs each before citing them
  as receipts.

## Current vs proposed pipeline

```mermaid
flowchart TB
    subgraph cur["Current (infer)"]
        BF[build_facts: tsp 8.5s] --> BR1[build_report baseline]
        BR1 --> PF[pinned_facts: tsp 15s] --> BR2[build_report inferred]
        subgraph br["each build_report"]
            C[catalog fixpoint: N+1 rounds<br/>x full module() per unit] --> F[final pass:<br/>full module() per unit]
            C -.->|each round re-parses,<br/>re-pins, re-lowers bodies| C
        end
        BR1 --> br
        BR2 --> br
    end
    subgraph prop["Proposed"]
        PB[build_facts once] --> SG[signature collection:<br/>once per unit, worklist<br/>to fixpoint over sigs+pins]
        SG --> WL[summary worklist:<br/>per-def lowering,<br/>content-hash keyed]
        WL --> MR[merge: deterministic order]
        MR --> RT[(on-disk cache:<br/>(file hash, dep summary hash))]
        WL -.->|cache hit: skip| MR
        WL --> PAR[optional: parallel<br/>by SCC wave]
    end
```
