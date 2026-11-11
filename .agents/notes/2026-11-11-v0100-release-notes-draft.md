# v0.10.0 release notes - DRAFT skeleton

**Status: DRAFT for the owner. v0.10.0 is not cut, no tag exists, and CHANGELOG.md has
not been touched.** This file groups what has *merged* into `master` since the
`v0.9.0` tag and leaves clearly-marked slots for work still in flight. Nothing
here is a number: every figure must be produced by the receipt command named
in its slot at release time (see `.agents/docs/release/release-process.md`,
"Release notes" / "Number rules").

Window covered: `git log v0.9.0..master` - 61 commits at draft time (head
`cf9cc3e1`, 2026-11-11). Open milestone items at draft time: 7 open PRs
(#785, #788, #799, #802, #804, #806, #1204) and 7 open issues (#485, #668,
#670, #672, #732, #829, #830). Add what merges before the cut; the minor bar
needs 0 open milestone items.

**Theme caveat for the owner:** the v0.10.0 milestone description still lists
the async/event-loop (#177) and http.server (#227) themes, but those moved to
Future/v0.11.0. What actually defines the release is embed-engine breadth plus
stdlib/self-host gains - see the proposal below and decide.

## Theme paragraph

PLACEHOLDER - owner writes this. Proposal to react to:

> v0.10.0 widens what compiles and runs without a Python install. More of the
> stdlib lowers natively (bisect, the finite itertools names, os.symlink and
> readlink, tempfile.mkstemp, with uuid/binascii/shlex/secrets and selectors in
> flight), inference picks up chained facts from pyrefly and an own-inference
> arm, decorators and class bodies lower further, and the tool itself now runs
> on any CPython 3.11–3.15 host with the C toolchain one `zig` extra away. The
> embed engine grows a capability manifest, an engine descriptor with
> `--island-engine`, and a PyPy seam, with onefile bundles in flight.

## Breaking changes

None found. `git log v0.9.0..master` shows no `!` commits and no
`BREAKING CHANGE:` footers as of this draft. Re-check at release time - the
release generator refuses to finish without this section if one appears.

## Highlights

PLACEHOLDERS (2–5 bullets, owner picks and verifies each):

- PLACEHOLDER: the tool host runs on CPython 3.11–3.15 while compiled programs
  still target 3.14 (#1202); `pip install pyscriptc[zig]` pulls the C toolchain
  in one step (#1196, #1198).
- PLACEHOLDER: embed engine seam - capability manifest v1 with imports/assess
  consumers (#669), engine descriptor schema + loader + `--island-engine`
  (#1181), PyPy driver seam via `-DPS_ENGINE_PYPY` (#1193); onefile output in
  flight (PR #802).
- PLACEHOLDER: stdlib breadth - bisect.insort over int/float/str (#945), the
  remaining finite itertools eager names (#946), os.symlink/os.readlink (#1164),
  tempfile.mkstemp returning (fd, path) natively (#1177); uuid/binascii/shlex/
  secrets (PR #806) and the selectors subset (PR #788, issue #485) in flight.
- PLACEHOLDER: self-host fences ~3.4k → 3,131 by census_pins sum (verify at
  release commit; the v0.9.0 notes cited ~3,328 - repin drift, reconcile the
  baseline before quoting a delta).
- PLACEHOLDER: a Flask and a FastAPI app each compile to one binary (#775) -
  demo/docs value, verify it still builds at release time.

## Features

Merged since v0.9.0 (grouped from commit subjects; verify issue numbers via
`gh pr view` before release notes use them):

- **Embed engine seam:** capability manifest v1 and the imports/assess
  consumers (#669); engine descriptor schema v1, loader and `--island-engine`
  (#1181); `ps_island.c` engine seam for PyPy with driver-emitted
  `-DPS_ENGINE_PYPY` (#1193).
- **Host floor and packaging:** the tool host runs on CPython 3.11–3.15, the
  program still targets 3.14 (#1202); the `zig` extra installs the tool and the
  C toolchain together (#1196, #1198).
- **Inference:** chained param and return facts supplied via pyrefly infer
  (#731); the own-inference arm as `infer --bridge own` (#731).
- **Lowering:** erase method/function decorators outside the closed set (#732);
  erase non-dataclass class decorators with a PS3099 divergence note (#732);
  class-body constant attributes (#1166); stacked user decorators on module and
  nested defs (#1163); module-scope call results bind a global slot (#502);
  `next(iter(d))` peeks the first key instead of materializing it (#1170).
- **Stdlib:** bisect.insort over int, float and str lists (#945); os.symlink
  and os.readlink (#1164); the remaining finite itertools eager names (#946);
  tempfile.mkstemp returns the (fd, path) tuple natively (#1177).
- **Tooling/CI:** an R0 real-scripts lane in the nightly ladder (#1172); the
  infer fence delta breaks down by family and code (#1173); TIR hashing over
  typed nodes in schema field order, no dict round-trip (#830 P2, #1201 -
  moves every TIR digest, receipt in
  `.agents/notes/2026-11-10-tir-hash-p2-blocker.md`).
- **Site:** Polar sponsor link, badges and CTA (#755); privacy-respecting
  PostHog analytics (#760).
- PLACEHOLDER: in-flight - PR #806 (uuid, binascii, shlex, secrets subsets +
  yt-dlp wall measurement), PR #788 (closed selectors subset over the loop,
  #485), PR #802 (embed onefile), PR #804 (site island verdict), PR #1204
  (class-body assignments with runtime values, #732), PRs #785/#799 (docs).
  Add what merges before the cut.

## Performance

No `perf` commits merged since v0.9.0. The TIR hash change (#1201) is a
canonicalization fix (all 13,692 digests moved by contract), not a claimed
speedup - do not quote a perf number without running the receipt below.
PLACEHOLDER: if a compile-time or hash-throughput number is wanted, measure on
the release commit (`mise run bench:baseline` for the perf gate; the nightly
`visible-score` job for corpus pass rates).

## Fixes

- assess: stop inferring an island top level from closed-import-table fences
  (#1175)
- boxed: a module island-value binding adopts the seed's object (#769)
- cli: do not stage a bundle for programs without island references (#1146)
- package: skip .py files CPython cannot name in tree scans (#764)
- ci: count four doc-coupled seams tests after the first_class_scripts removal
  (#762); hash the scorer cache key once (#1143)
- site: keep the hero backdrop behind the CTA row (#754)

## Internal

At most three collapsed bullets; do not enumerate the self-host sweep.

- Self-host census sweep: ~20 typed-annotation refactor PRs across cli/_main,
  lower/*, emit/llvm, assess, check, coverage, package, ffi_manifest,
  stdlibzip, typefacts, factsbridge and restlower (fences 3,414 → 3,131 by pin
  sum); plus the emit/llvm `_irnode` typed-record migration slices (#829: #1195
  and the _stmts slice).
- TIR hash gate: PR-compare hash gate + the retire-IR_HASHES decision record
  (#830 P1, #1182); typed-node hashing (#830 P2, #1201).
- Social/site plumbing: the social card pipeline and the v0.8.0 seam card
  (#938, #1180); nightly compat snapshots (bot/compat-snapshot merges).

## Receipts needed at release time

Fill each slot with a command-produced number on the release commit; never
write a number from memory or from a frozen-gate file without a recorded
decision.

- Boxed ledger window at the boundary: `mise run boxed:cost`
  (`uv run python benchmarks/compat2/boxed_cost.py --window-commits 10`, plus
  `--reach` / `--probe-times` if fed). **Draft-time reading at `cf9cc3e1`:**
  window 0 code 5.3%, churn 1.4%, bugs 0.0% (0/0), tax 0.0%; window 1 code
  5.3%, churn 0.9%, bugs 100% (1/1 - one boxed fix, tiny sample), tax 0.0%;
  window 2 code 5.4%, churn 0.0%, bugs 0.0% (0/1), tax 0.0%. drop_flag false.
  Re-run at the boundary; the bug-share windows are single-fix denominators.
- Self-host fences: `uv run python -m benchmarks.compat2.census_self` (or the
  pin sum under `tests/census_pins/`). Draft-time: 3,414 at `v0.9.0` → 3,131 at
  `cf9cc3e1`. Reconcile the v0.9.0 notes' "~3,328" figure before quoting a
  delta - pin files were added/regenerated since.
- Visible corpus score: the nightly `visible-score` job
  (`.github/workflows/nightly.yml`, `nightly/visible-score.json`) on the
  release commit - cite scored and weighted pass, baseline v0.9.0.
- Compat outcome flips (both directions): `uv run python
  scripts/compat_no_flip.py` - cite the probe count and flip counts it prints.
- If any headline claims "byte-identical" or "no regressions": the runtime A/B
  harness and/or `benchmarks.baseline` (`mise run bench:baseline`) on the exact
  commit, with the baseline named.
- Minor-release bar (per AGENTS.md and `.github/scripts/release_bar.py`):
  milestone theme exit criteria met, 0 open milestone issues **and PRs** (the
  milestone counter includes PRs - 14 open at draft time), at least 2
  user-visible features, and one measured receipt - pick which of the above is
  the headline receipt. The `receipt` check in the bar is manual: the measured
  line must appear in the changelog entry the release PR writes.
