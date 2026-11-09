# Post-flip plan: verticals, timeboxes and capacity

Date: 2026-11-07. Status: in force from the moment MIR became the default (`MIR_DEFAULT = True`, #1067).
Refs: epic #817, milestones v0.9.0 (18 open), v0.10.0 (96 open), v0.11.0 (28 open), `.agents/plans/2026-11-06-user-gains-plan.md`, `.agents/plans/2026-11-06-fence-reduction-4000-to-2000.md`.

## Where we are (measured 2026-11-07)

- MIR is the default lane on master. Receipts: CI green on the flip commit, scorer parity 0 flips over 13,798 probes, `benchmarks.baseline.run --check` within reference.
- Owner item still open: the signature on draft decision #937 (amends the 80% flip bar to a reported figure). Agents never edit it.
- Self-host fences: 3944 in `tests/census_pins` (target 3500, then 3000, then 2000 before v0.10.0, new architecture only).
- Open PRs: the yt-dlp stack (#1071, #1072, #797), about 25 older drafts and fixes (#775 to #807), docs and CI fixes (#935, #938, #762), stdlib lowering (#945, #946, #777), and the held any-leaf draft #1057.
- Open v0.9.0 issues: #1052 rsplit maxsplit, #1044 print of containers, #924 nightly red, #852, #830, #829, #93, #90.

## Principles

1. One vertical is one workstream with a stable file ownership area, so sessions in different verticals never touch the same files and merges stay disjoint.
2. Every task is 45 to 60 minutes, one deliverable, one PR, written down as a prompt with files, acceptance and gates. Chain sessions; never one long box.
3. Keep sessions saturated, not oversubscribed: VPS 4 sessions on `swe-2-flash-medium` (the only free VPS model), laptop 2 to 3 sessions on `swe-2-high` (free; use `swe-2-max` only for design-heavy tasks). Check `uptime` and free memory before each dispatch; the laptop is the orchestrator, so it runs fewer sessions.
4. The laptop has `gh`, so laptop sessions open their own PRs and handle board and milestone. VPS sessions push branches and the orchestrator opens PRs.
5. New-architecture only for lowering and fence work (typed HIR plus MIR). CPython behavior is the baseline, never old-emitter parity.
6. A queue per vertical: when a session finishes, the next task in that vertical is dispatched at once, so the vertical never idles.

## Verticals

Capacity split. VPS (4 slots): V1, V2, V3, V5. Laptop (3 slots): V4, V6, and the orchestrator slot (review, PR plumbing, V7).

### V1. Stack landing (VPS, file area: whatever the PR touches, one PR at a time)

Goal: empty the open-PR backlog so master stays the only line. Each task rebases one PR or a small chain onto the MIR-default master, fixes MIR-default breakage with typed code, repins census, force-pushes.

- V1.1 land #1071, #1072, #797 (in progress via loop; follow-up if CI fails).
- V1.2 #802 onefile and #803 import cycles (one session, in order).
- V1.3 #804 site verdict, #805 pydantic, #806 stdlib subsets (one session).
- V1.4 #779 code cache and #775 flask/fastapi (one session).
- V1.5 stdlib lowering drafts: #786 heapq, #787 and #945 bisect (dedupe), #788 selectors, #789 json, #946 itertools (one session each pair).
- V1.6 small fixes: #777, #769, #767, #766, #762, #935 (one session, merge each when green).
- V1.7 docs and notes drafts: #774, #798 to #800, #785, #807, #938, #759 (docs-only, parallel safe; close any that are stale instead of rebasing).
- V1.8 decide per draft: close what the flip made obsolete (state the reason in the closing comment).

### V2. Post-flip correctness (VPS, file area: `src/pyscriptc/mirlower.py`, `miremit.py` bug fixes, runtime C)

Goal: wrong output is worse than a fence; fix what real programs hit first.

- V2.1 #924 nightly visible-score red (6 wrong-output rows): reproduce, split oracle drift from compiler bug, make the nightly open its own failure issue.
- V2.2 #1052 `str.rsplit` maxsplit.
- V2.3 #1044 print of list, dict and set (PS2003).
- V2.4 #1038 unannotated generator yield types.
- V2.5 `runtime_ab` and CPython-baselined A/B sweep on the MIR default over the visible corpus; each diff is a minimal repro and a fix PR.
- V2.6 compile-failure triage on MIR default: any `PS0001` internal error is a bug (zero allowed); sweep the visible corpus for `PS0001` and file or fix each.

### V3. Fence reduction (VPS, file area: `src/pyscriptc/lower/*`, census shards)

Goal: 3944 to 3500 to 3000 to 2000, push only when the census delta is net negative.

- V3.1 `fenceb2` (running): next batch from the fence plan section 6.
- V3.2 to V3.8: B3 to B7 batches in plan order, one per session. T3 any-leaf (#1057) waits on the typed-vocabulary decision; T5 waits on #784.
- Each batch: before/after census in the PR body, probe delta, no raw dict reads.

### V4. Dict-IR drop prerequisites (laptop, `swe-2-high`/`max`, file area: `coro.py`, `emit/llvm/*`, `_irnode`, `mir*.py`)

Goal: unblock the v0.10.0 deletion of the dict IR (about 30k lines). Design-heavy, so these run on the laptop where `max` is available.

- V4.1 R-2 typed coroutine port design note then first slice (generators are both a compat gap and a fence whale).
- V4.2 #829 emit mixins to MIR reads, one mixin per PR (parallel safe across mixins; one session per mixin).
- V4.3 P6 consumers to typed accessors (about 15 modules; split in groups of three).
- V4.4 #830 hash-gate retirement evidence (the two-week nightly clock runs unattended; prepare the decision text only).
- V4.5 retire the byte-equality tests and remaining knobs (`PYSC_MIR_BYTE_IDENTITY`) now that parity is off.

### V5. Compat breadth (VPS, file area: `src/pyscriptc/boxed.py`, runtime island C, stdlib lowering)

Goal: more real programs run. Ordered by user impact.

- V5.1 island floor: #678, #679, #765, #690, #778, one per session.
- V5.2 type facts for unannotated code: #731 and #784.
- V5.3 lowering clusters: #732, #483, #481, #505.
- V5.4 stdlib surface: #509, #877, #485.
- V5.5 PyPI ladder #428 measurement refresh after V5.1 to V5.3.

### V6. Compile speed and DX (laptop, file area: `benchmarks/baseline`, `cli/`, docs)

- V6.1 compile-time receipt: `benchmarks.baseline` `compile_ms` MIR default against the v0.7.0 reference, profile the top stage with `--debug=stages`.
- V6.2 selfbin compile-speed gap (1.27 s vs 0.53 s) diagnosis.
- V6.3 DX cluster: #522 to #527 (divergence ledger, silent-divergence diagnostics, differential probe lane, verdict ratchet), #730, #484.
- V6.4 MIR printable IR user surface: `--emit mir` and the verifier codes (MVS) documented for users, with the dump-format decision text for the owner.

### V7. Release and owner items (orchestrator)

- Owner: sign #937, decide the `--emit ir` dump format at v0.10.0, decide W5 slot, release cuts (never agent-dispatched), C6 (#945/#946), #1052 triage.
- Orchestrator: milestone hygiene, board fields (Horizon, Effort), boxed ledger at the epic boundary (`mise run boxed:cost`), `decisions.md` row per substantive PR, release bar check (`.github/scripts/release_bar.py`).

## Capacity schedule

| Slot | Host | Model | Vertical | Next tasks (queue order) |
|---|---|---|---|---|
| 1 | VPS | swe-2-flash-medium | V3 fence | fenceb2, then B3, B4 |
| 2 | VPS | swe-2-flash-medium | V1 stack | #802/#803, #804/#805/#806, #779/#775 |
| 3 | VPS | swe-2-flash-medium | V2 correctness | #924, #1052, #1044, #1038, PS0001 sweep |
| 4 | VPS | swe-2-flash-medium | V5 breadth | #678, #765, #778, #731 |
| 5 | laptop | swe-2-high | V4 drop prereqs | R-2 design, #829 mixins, P6 groups |
| 6 | laptop | swe-2-high | V6 DX | compile-time receipt, selfbin gap, DX cluster |
| 7 | laptop | orchestrator | V7 | PR plumbing, merges, owner items |

Dispatch helpers: `/tmp/vps-dispatch.sh NAME CHECKOUT` (VPS) and `/tmp/lap-dispatch.sh NAME` (laptop, tmux `dv-NAME`, report `/tmp/dv-NAME.out.md`). One monitor watches all `dv-*` sessions and fires when a report lands; the orchestrator then reads it, opens the PR, starts the merge loop and dispatches the next task in that vertical in the same turn.

## Parallelism rules

- At most one open PR that appends MIR ops, at most one editing `mirlower.py` core paths; V2 owns those files, V3 and V5 stay in `lower/` and `boxed.py`.
- Generated artifacts (census shards, `data/compat/support.json`, `docs/reference/cli.md`, `compat.json`) are regenerated, never hand-merged; `scripts/rebase_helper.py` handles the mechanical conflicts.
- Frozen gates stay frozen (`outcomes.py`, `IR_HASHES.json`, probes, calibration); a change needs a recorded decision first.
- No session touches evaluation-set material.

## Exit criteria to close v0.9.0

1. MIR default on master (done).
2. #937 signed or superseded by a recorded owner decision.
3. Zero `PS0001` internal errors on the visible corpus under the MIR default (V2.6).
4. #924, #1052, #1044 closed or moved with a reason.
5. Fence count at or below 3500 (V3.1 to V3.3), boxed ledger measured.
6. Owner cuts the release; agents never dispatch it.

## Risks

- Free-model quality: `swe-2-flash-medium` stalls on conflict-heavy rebases. Mitigation: smaller tasks, one conflict region per task, and escalate a stuck task to the laptop `swe-2-high`.
- Hotspot files (`miremit.py`, `mirlower.py`): serialize V2 and V4 changes there; V4 mixin work targets `emit/llvm/*`.
- Review bound: merge loops must be running for every open PR; check for UNKNOWN or BLOCKED states and expired monitors immediately.
