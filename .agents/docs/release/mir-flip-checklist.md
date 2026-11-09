# MIR default-flip checklist

One page for the owner flip decision (epic #817, milestone v0.9.0). Every
threshold below is quoted from its source; nothing here invents a number.
The flip bar itself is the draft recorded decision in PR #937
(`.agents/plans/decisions/2026-11-05-mir-contract-amendment-and-layered-gate.md`,
awaiting owner signature). The escape-hatch contract is
`.agents/docs/mir-escape-hatch.md`.

## Flip criteria

| # | Criterion | Measure | Threshold | Source |
| --- | --- | --- | --- | --- |
| 1 | Slice coverage: share of non-fenced corpus functions that lower to MIR | `uv run python scripts/mir_coverage.py --json` | at least 80% in-slice; under 60% at the scope check moves the flip to v0.10.0 | #937 flip bar and kill criteria |
| 2 | Runtime A/B differential: direct vs `PYSC_MIR=1` binaries agree on stdout, stderr and exit code | `uv run python scripts/runtime_ab.py` (mir_leaf fixtures today; the 20 corpus scripts join the default set with the FR-2 extension) | 0 FAIL and 0 ERROR rows; report-only nightly lane (`runtime-ab` job, PR #966) | #937 layered gate |
| 3 | Per-function byte identity of the MIR lane | `uv run pytest tests/test_mir_leaf.py tests/test_mir_hashseed.py -q` | green; this gate stays through v0.9.0 | #937 layered gate |
| 4 | Adapter telemetry sane: fallback volume and reasons known before the default moves | `uv run python scripts/compiler_progress.py --json` `mir_coverage` block, the out-of-slice reasons table (PR #993) and the B11 telemetry note (PR #999) | reviewed, no surprise fallback share | #937 amendment (bounded typed-HIR exception) |
| 5 | Escape hatch verified: `PYSC_MIR=0` forces the direct emitter, unset follows `MIR_DEFAULT` | `uv run pytest tests/test_mir_escape_hatch.py -q` | green on both sides of the flip; the flip PR inverts the default assertion in the same commit | `.agents/docs/mir-escape-hatch.md` |

Whole-module byte equality is retired at the flip by the #937 decision; it is
not a checklist row. Module-scope text (runtime header, ABI declarations,
helper bodies, entry point) is shared code in both lanes.

## Kill criteria (flip slips to v0.10.0)

- The A/B harness is not reliable within two sessions of trying.
- Slice coverage is under 60% at the scope check.

Both are from #937. A slip is a milestone change, not a doc edit.

## Release gates around the flip (v0.9.0 minor bar)

These are the standing release-policy gates, listed so the flip PR is not the
only thing read at close:

| Gate | Command / check | Threshold |
| --- | --- | --- |
| Rehearsal on the exact commit | `mise run release:rehearse` | build matrix, smoke and wheel verify green |
| Open issues at milestone close | `gh issue list --milestone v0.9.0` | 0 open |
| Measured receipt | `mise run boxed:cost`; census delta; `uv run python scripts/mir_perf_ratio.py` for the lane ratio | numbers cited in the release notes come from committed receipts only |
| User-visible changes | CHANGELOG entry per `.agents/docs/release/release-process.md` | at least 2 for a minor; every number traceable |

## The flip itself

One assignment: `MIR_DEFAULT = True` in `src/pyscriptc/miremit.py`, in the same
PR that inverts the default assertion in `tests/test_mir_escape_hatch.py`.
After it lands, `PYSC_MIR=0` is the user-facing escape hatch until the
direct-emit path (typed HIR stays the middle stage for every function) is dropped at v0.10.0.

Owner policy (2026-11-07): the direct-emit path is not a permanent home for what the MIR slice cannot lower yet. Anything outside the slice is a gap to close on the new architecture, not a reason to keep both backends maintained. The old architecture, if kept at all, lives vendored in a separate package.
