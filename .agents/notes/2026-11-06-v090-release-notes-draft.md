# v0.9.0 release notes — DRAFT skeleton

**Status: DRAFT for the owner. v0.9.0 is not cut, no tag exists, and CHANGELOG.md has
not been touched.** This file groups what has *merged* into `master` since the
`v0.8.0` tag and leaves clearly-marked slots for work still in flight. Nothing
here is a number: every figure must be produced by the receipt command named
in its slot at release time (see `.agents/docs/release/release-process.md`,
"Release notes" / "Number rules").

Window covered: `git log v0.8.0..origin/master` — 38 commits at draft time.
Open PRs (e.g. the fence-reduction batch F-0..F-6, `PYSC_MIR` escape-hatch docs,
the MIR default-flip checklist) are NOT yet in any section below; add them when
they merge.

## Theme paragraph

PLACEHOLDER — owner writes this. The v0.9.0 milestone theme is **compute
two-plane** (the FFI data plane proven on a compute library and the island
showing a real wheel; exit per `.agents/plans/2026-10-23-compute-two-plane-next.md`).
Note: a large share of what has merged since v0.8.0 is actually compiler-side
(the `emit/_ir` seam migration, MIR leaf-slice growth, receipt tooling), so the
owner should decide how much of that belongs in the theme paragraph versus
Highlights. Do not describe the theme as done — the two-plane exit criteria
have not been met.

## Breaking changes

None expected. `git log v0.8.0..origin/master` shows no `!` commits and no
`BREAKING CHANGE:` footers as of this draft. Re-check at release time — the
release generator refuses to finish without this section if one appears.

## Highlights

PLACEHOLDERS (2–5 bullets, owner picks and verifies each):

- PLACEHOLDER: compute two-plane progress — FFI data plane / island wheel demo
  status. Needs a receipt or an honest "still landing" statement.
- PLACEHOLDER: MIR leaf slice now accepts `None` returns (#941) and has a
  `PYSC_MIR` escape-hatch contract pinned by tests (#981 merged? verify).
- PLACEHOLDER: new compat outcome-flip reporting both directions (#982) and a
  MIR-vs-direct performance ratio receipt (#977) — verify whether these are
  user-visible enough for Highlights or belong under Features/Internal.
- PLACEHOLDER: runtime A/B harness landed as the emit-flip gate (#943) —
  Internal at most.

## Features

Merged since v0.8.0 (grouped from commit subjects; verify issue numbers via
`gh pr view` before release notes use them):

- **Compat scorer reports outcome flips in both directions** — `scripts/compat_no_flip.py`
  surface (#982). Receipt command at release: `uv run python scripts/compat_no_flip.py`.
- **MIR-vs-direct performance ratio receipt** — `scripts/mir_perf_ratio.py`
  (#977). Receipt command: `uv run python scripts/mir_perf_ratio.py`.
- **MIR leaf slice accepts `None` returns** (#941).
- **MIR slice coverage metric in the compiler trend table** (#944).
- PLACEHOLDER: in-flight — the F-0..F-6 fence-reduction PRs (#988–#1002 range),
  `PYSC_MIR` escape-hatch documentation (#996), `--emit mir` text output (#991),
  stdlib itertools/bisect lowering (#946, #945), runtime int()/shutil.which
  parity fix (#965). Add what merges before the cut.

## Performance

No `perf` commits merged since v0.8.0. The MIR-vs-direct ratio receipt (#977)
is the instrument that will produce the number if the MIR lane is claimed as a
performance or parity story; do not quote a ratio without running it on the
release commit.

## Fixes

- **Synthetic class install order sorted** so emitted `.ll` is independent of
  `PYTHONHASHSEED` (#936, closes #817).
- PLACEHOLDER: in-flight — `fix(runtime): CPython 3.14 int() literal parity and
  shutil.which None` (#965) is open at draft time; add if it merges.

## Internal

At most three collapsed bullets; do not enumerate the seam migration.

- `emit/llvm` and lowering consumers migrated to read IR records through the
  `emit/_ir` typed seam (#829 epic: #939, #942, #947–#950, #952, #953, #967,
  #985, #986, #988, #989 and more).
- Test-side migration onto typed IR accessors instead of dict `kind` keys
  (#953-adjacent: #952, #953, #957, #958, #969–#972).
- MIR design notes M-1..M-5 (str value type, list/dict value types, try/handlers
  CFG, closures/env cells, MIR digest gate) and inventories for dict-IR reads
  (#960–#964, #968, #976, #987, #990).

## Receipts needed at release time

Fill each slot with a command-produced number; never write a number from memory
or from a frozen-gate file without a recorded decision.

- Compat outcome flips (both directions): `uv run python scripts/compat_no_flip.py`
  — required because #982 added the both-directions report; cite the probe count
  and the flip counts it prints.
- Boxed ledger window at the boundary: `mise run boxed:cost`
  (`uv run python benchmarks/compat2/boxed_cost.py --window-commits 10`, plus
  `--reach` / `--probe-times` if fed). Record code/churn/bug/tax shares and the
  drop flag in the theme or Receipt section.
- MIR-vs-direct performance ratio: `uv run python scripts/mir_perf_ratio.py`
  on the release commit, vs the v0.8.0 baseline.
- Visible corpus score: the nightly `visible-score` job
  (`.github/workflows/nightly.yml`, `nightly/visible-score.json`) on the release
  commit — cite scored and weighted pass, baseline v0.8.0.
- If any headline claims "byte-identical" or "no regressions": the runtime A/B
  harness (#943) and/or `benchmarks.baseline` (`mise run bench:baseline`) on the
  exact commit, with the baseline named.
- Minor-release bar (per AGENTS.md): milestone exit criteria met, 0 open issues,
  at least 2 user-visible features, and one measured receipt — pick which of the
  above is the headline receipt.
