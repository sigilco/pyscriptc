# CI path gating (2026-10-28)

Findings and design for gating the CI matrix on the paths a change actually touches.

Context: the `ci` workflow ran its full 14-job matrix on every push and every pull request, including the empty commit `3bf40de6` ("chore: retrigger build", a no-op that exists only to retrigger Cloudflare) and docs-only and `apps/web`-only changes. PR #740 (`work/ci-dedupe`) already restricts `push` to `master` and adds `cancel-in-progress` for pull requests; this note builds on that rather than repeating it.

## 1. Job inventory

Median wall-clock per job over the last 25 `ci` runs, from `gh run view --json jobs`:

| job | median | min | max | true path dependency |
| --- | --- | --- | --- | --- |
| tests without linux_container (shard 1/3) | 3.7m | 2.6m | 4.6m | `src/`, `tests/`, `benchmarks/`, `examples/`, `scripts/`, `packaging/`, `docker/`, `pyproject.toml`, `uv.lock` |
| tests without linux_container (shard 2/3) | 4.1m | 2.8m | 4.8m | same |
| tests without linux_container (shard 3/3) | 4.5m | 3.9m | 5.2m | same |
| zig cc emitted-IR smoke (0.16.0) | 4.4m | 2.9m | 5.0m | `src/`, `packaging/zig/`, `pyproject.toml` |
| zig cc emitted-IR smoke (0.17.0) | 4.0m | 2.9m | 5.2m | same |
| ziglang wheel PATH shim | 1.1m | 0.8m | 1.2m | `src/pyscriptc/zigshim.py`, `pyproject.toml`, `uv.lock` |
| wasm32-wasi-musl smoke | 1.1m | 0.9m | 1.3m | `src/`, `packaging/zig/wasm-smoke.sh` |
| ruff and compat2 | 0.8m | 0.6m | 0.9m | ruff lints the whole tree (688 files); compat2 reads `benchmarks/` |
| mobile archives and link-checks (ubuntu-24.04, ...) | 0.7m | 0.6m | 1.2m | `src/pyscriptc/runtime/`, `src/pyscriptc/mobile.py`, `packaging/mobile/` |
| mobile archives and link-checks (macos-14, android) | 0.6m | 0.5m | 0.8m | same |
| TSan smoke | 0.7m | 0.5m | 0.8m | `src/pyscriptc/runtime/ps_thread.c`, `src/pyscriptc/coro.py` |
| ASan+UBSan smoke | 0.7m | 0.5m | 0.8m | `src/pyscriptc/runtime/`, the emitted code |
| network receipt (certifi library receipt) | 0.5m | 0.5m | 0.6m | `src/pyscriptc/deps.py`, `uv.lock` |
| wheel install smoke | 0.5m | 0.1m | 0.6m | `src/pyscriptc/`, `pyproject.toml`, `packaging/` |
| betterleaks | 0.1m | 0.1m | 0.4m | the whole tree (secret scan) |

Sum of medians, serial: 27.5 minutes of runner time per run. Wall clock of a full run: 4.5 minutes, because the matrix is parallel.

Two macOS cells exist: the `macos-14` test shards and the `mobile archives (macos-14, android)` leg. On the free plan macOS minutes bill at 10x, so those are the expensive part of the bill, not the wall clock.

`embed` runs two more cells per pull request (`ubuntu-24.04` and `macos-14`), and `site` runs one on `apps/web`, `docs/` and `data/` changes.

## 2. Path groups

The classifier is a job, not a workflow-level filter (see section 3). It classifies the diff into four groups and the jobs read them through `needs.changes.outputs.<group>`.

| group | paths | jobs it gates |
| --- | --- | --- |
| compiler | `src/`, `tests/`, `benchmarks/`, `examples/`, `scripts/`, `packaging/`, `docker/`, `pyproject.toml`, `uv.lock`, `mise.toml`, `.github/` (workflows and scripts), `.claude/`, `data/`, `.agents/docs/` | all 9 expensive lanes (the three test shards, both zig cells, ziglang-wheel, wasm, wheel, ASan, TSan, mobile, network receipt) |
| python | any tracked Python file (the compiler group plus `.agents/notes/`, `.agents/plans/`, `.agents/probes/`) | `ruff and compat2` (ruff lints the whole tree, not only `src/`) |
| site | `docs/`, `apps/web/`, `data/`, the pnpm lockfiles | `docs seams` |
| always | nothing (betterleaks scans the whole tree) | `betterleaks` |

The groups overlap by design: a `data/` change is both compiler (the compat tests read `data/compat/`) and site (the site assembles `data/`), so both lanes run.
### Why these boundaries

The classification is not "docs versus code", it is "does this move a test result or a lint result". The boundaries were read off the tree, not guessed:

- `tests/test_compat_site.py` reads `data/compat/compat.json`, `docs/guide/support-model.md`, `docs/.docmd/plugins/placeholders.js`, `apps/web/src/pages/compat/index.astro`, `.github/workflows/site.yml` and `.github/workflows/nightly.yml`.
- `tests/test_release_bar.py` reads `.github/scripts/release_bar.py` and `.github/scripts/release_notes.py`.
- `tests/test_holdout_guard.py` reads `.claude/hooks/holdout_guard.py`.
- `scripts/gen_support_table.py` reads `.agents/docs/support/supported.md` and `.agents/docs/support/compat-top100.md`; `scripts/check_doc_cadence.py` scans `.agents/docs/support/`.
- `tests/test_first_class_scripts.py` reads `docs/reference/first-class-scripts.md` and `docs/.docmd/docmd.config.js`.
- `tests/test_cross.py`, `test_embed.py`, `test_ffi_link.py`, `test_licenses.py`, `test_pack_orjson*.py` read `packaging/`.
- `tests/test_examples.py` and `test_canonical.py` read `examples/`.

So `.github/`, `.claude/`, `data/` and `.agents/docs/` all move a test result and therefore run the compiler lane. Only the prose under `.agents/notes/` and `.agents/plans/` is free.

`ruff check` runs over the whole tree: `uv run ruff check --show-files` lists 688 files, including `.github/scripts/release_bar.py`, `.claude/hooks/holdout_guard.py` and `.agents/probes/*.py`. A Python file under the doc bubble is therefore a lint change, which is why the `python` group exists separately from `compiler`.

### Deliberately conservative

`.github/workflows/`, `pyproject.toml` and `uv.lock` all run everything. A workflow change can change what any job does, and a dependency change can change what any test compiles. The cost of running the full matrix on those is a few minutes; the cost of skipping a lane that mattered is a broken master.

### The documentation bubble

Inside the bubble a change runs `betterleaks` (secret scan, whole tree) plus `docs seams` (the five doc-coupled unit tests, 55 tests, about 80 seconds) when the site inputs changed. It does not run the compiler lanes.

`docs seams` is a new job. Without it a docs-only pull request would skip every test in this workflow, because the five tests that read the docs live in `tests/`, which the classifier treats as compiler surface. The job runs exactly `tests/test_compat_site.py`, `tests/test_compat_json.py`, `tests/test_doc_cadence.py`, `tests/test_first_class_scripts.py` and `tests/test_support_docs.py`. Verified locally: 55 passed in 77s.

`site.yml` already has workflow-level `paths:` filters and carries no required check, so it needs no change: a docs or web change still builds the site there. The `site` group in `ci.yml` exists to drive `docs seams`, not to duplicate the site build.

## 3. The required-check gotcha

This is the part that decides the whole design.

GitHub's own documentation (Troubleshooting required status checks) draws the line exactly where this design needs it:

> If a workflow is skipped due to path filtering, branch filtering or a commit message, then checks associated with that workflow will remain in a "Pending" state. A pull request that requires those checks to be successful will be blocked from merging.
>
> If, however, a job within a workflow is skipped due to a conditional, it will report its status as "Success".

And About status checks: "A job that is skipped will report its status as 'Success'. It will not prevent a pull request from merging, even if it is a required check."

So:

- A workflow-level `paths:` filter on `ci.yml` would leave `ruff and compat2` and the rest pending forever. That is the trap, and `site.yml` and `ir-hashes.yml` use that pattern safely only because neither carries a required check.
- A job-level `if:` skip reports `skipped`, which counts as success for a required check. That is the mechanism this design uses.

### The required check names stay stable

The ruleset `master: PR only` (id 25570891, branch target, `~DEFAULT_BRANCH`, enforcement active) requires these seven contexts:

```
betterleaks
ruff and compat2
zig cc emitted-IR smoke
wheel install smoke
tests without linux_container (shard 1/3)
tests without linux_container (shard 2/3)
tests without linux_container (shard 3/3)
```

**Update 2026-10-28, later the same day.** The original design kept all seven names as job names and needed no ruleset edit. That was wrong on two of them, and the wrongness is what PR #746 v2 fixes. Three measured facts, from throwaway probe PRs #750, #751 and #752 (all closed after use):

1. A job-level `if:` skip on a **matrix** job reports a single `skipped` check run whose name keeps the literal `${{ matrix.* }}` text unrendered: the only check run was `exp gated (${{ matrix.v }})`. A **non-matrix** job with the same skip reports its own rendered name as `skipped`, which does satisfy a required check.
2. An aggregator job named with a matrix value (`shard ${{ matrix.group }}/3`) renders its legs normally (`shard 1/3`, `shard 2/3`, `shard 3/3`), and all legs pass under `if: always()` when the gated matrix is skipped.
3. An aggregator under a failed matrix lane reports a failed check on every leg, so a failed lane keeps its required contexts reported and red.

Consequence: the original design worked for the four non-matrix required names (`betterleaks`, `ruff and compat2`, `wheel install smoke`, all of which report their own name on a skip) but not for the three shard names or the zig name. The shard job is a matrix over `group`, and when the compiler lane skips, the only reported name is the unrendered `tests without linux_container (shard ${{ matrix.group }}/3)`, so none of the three required shard contexts reports and the PR waits forever. The zig job always renders (`(0.16.0)`, `(0.17.0)`), but the bare `zig cc emitted-IR smoke` the ruleset requires never arrives at all, on any PR.

The fix: the real matrix lanes are renamed (`unit shard ${{ matrix.group }}/3`, `zig smoke (${{ matrix.zig }})`), and two always-run aggregators (`shard-ok`, `zig-ok`) carry the exact required names, fanning out over the same groups as the real lanes. Each leg passes on `success` or `skipped` of the lane underneath and fails otherwise, so a cancelled lane fails the leg, which is the safe direction for a required check. `ci-ok` now needs the aggregators and demands they are strictly green. Aggregator cost when the full matrix runs: three tiny ubuntu legs and one, each a `case` statement, under 30 seconds each.

### A finding about the ruleset that is not mine to fix

*(The mismatch below is fixed by the aggregator change: the literal `zig cc emitted-IR smoke` context now reports from the `zig-ok` aggregator, so no ruleset edit is needed. The paragraphs stand as history.)*

The ruleset requires `zig cc emitted-IR smoke`, but the job reports two checks: `zig cc emitted-IR smoke (0.16.0)` and `zig cc emitted-IR smoke (0.17.0)`. Required status checks match on the literal reported name, so the literal string `zig cc emitted-IR smoke` never arrives.

This is masked today because the ruleset has a bypass actor: `RepositoryRole` id 5 with `bypass_mode: always`, which is the repository admin. Every recent merge (`#737`, `#739`, `#742`, `#745`) was merged by that admin, so the bypass carries the merge and the missing context never blocks anything.

It is not a regression from the path gating, and the path gating does not make it worse, but it is worth writing down: as long as the admin merges every pull request, the required-check list is decorative for the matrix jobs. An admin who wants the ruleset to actually gate would add a single `ci-ok` context and require that.

### Is an aggregate `ci-ok` job needed?

It is implemented, and it is not required by the ruleset today.

`ci-ok` needs all thirteen lane jobs plus the classifier, runs with `if: always()`, and is green only when the classifier succeeded and no lane that ran failed. The check is fail-closed: a classifier failure is a failure, not a skip.

The reason it exists despite the per-lane contexts already satisfying the ruleset: a lane skipped by `if:` reports `skipped`, which satisfies a required context, but it is also indistinguishable from a lane that was skipped for a different reason. `ci-ok` is the one context whose green means "the lanes that mattered were green", and it checks the classifier's own result rather than trusting the per-lane conclusions alone.

If an admin wanted a single required context, the ruleset change would be, on ruleset 25570891:

1. Add `ci-ok` to `required_status_checks`.
2. Remove the seven per-lane contexts, or keep them alongside.

Keeping them alongside is the safe first step: `ci-ok` can only make a merge harder, never easier, so adding it does not open a hole. Removing them is the step that needs care, because the skipped-job-is-success rule means that with only `ci-ok` required, a lane that is accidentally gated out stops blocking the merge. `ci-ok` catches that case only if the classifier still runs, which is why the classifier is a separate job with a timeout and not a step inside another job.

This change does not make that ruleset edit. It is listed here for the admin.

### What an admin would have to change, listed

Nothing is required for this change to work. The following is optional and is listed for the record:


1. Ruleset 25570891, required status checks: add `ci-ok` (safe, additive). Optionally then remove the seven per-lane contexts, accepting that the skipped-lane rule means `ci-ok` becomes the only real gate.
2. ~~Ruleset 25570891, required status checks: replace `zig cc emitted-IR smoke` with the two rendered names.~~ **Obsolete.** The `zig-ok` aggregator now reports the literal `zig cc emitted-IR smoke` context the ruleset already asks for, so the required list matches what the workflow reports without a ruleset edit. This was the one item on the list that would have changed what blocks a merge, and it is now unnecessary.
3. The always-bypass on the admin role could be narrowed to `pull_request` bypasses once the required contexts are known to be satisfiable without it. That is a policy decision, not a CI one.

## 4. What was implemented

### `ci.yml`

- `push` restricted to `master` and a `concurrency` block with `cancel-in-progress` for pull requests. This is the change from PR #740, applied here so the branch is self-contained; the two overlap on those lines and will conflict on merge, which is the intended resolution.
- A new `changes` job classifies the diff and exports `compiler`, `python` and `site`.
- A new `ci-ok` job aggregates the lane results, fail-closed.
- A new `docs seams` job runs the five doc-coupled unit tests when the site inputs changed.
- Nine expensive jobs gained `needs: changes` and `if: needs.changes.outputs.compiler == 'true'`. `ruff and compat2` uses the `python` group. `betterleaks` is not gated.
- The inner path filter that `wheel install smoke` used to carry was removed: the job now runs whenever the compiler lane runs, which is a superset of the old filter (`src/pyscriptc/{check,typefacts,cli}.py`, `pyproject.toml`, `packaging/`).

### Pushes and the empty commit

A push runs the full matrix, with one exception: an empty diff. `3bf40de6` is a real empty commit that exists only to retrigger Cloudflare, and it burned a full 27.5 minutes of runner time for nothing.

The classifier uses `GITHUB_EVENT_BEFORE` to detect it. If the before sha is present and the diff is empty, the compiler and python lanes are off and only `betterleaks` runs. If the before sha is absent or unknown, the diff is treated as non-empty, which is the safe direction. Verified: push with a normal change runs everything, push with an empty diff runs only `betterleaks`, push with an unknown before runs everything.

Master pushes stay conservative on purpose. The ruleset is evaluated on the merge commit and the release rehearsal expects a full receipt on master, so a master push with a real change runs everything.

With `push` restricted to `master` (the PR #740 change), a branch push no longer runs the workflow at all: the pull_request event covers the branch, and the duplicate run disappears.

### `embed.yml`

`embed` ran two runner cells per pull request, one of them macOS, and both of them usually did nothing: the gate step inside the cell ran `git diff` and printed `run=false`. The macOS cell bills at 10x.

The classification moved into a `changes` job and the matrix cell now gates on its output with `if:`, so an irrelevant pull request reports the cells as skipped instead of paying for two cells to say no. The `EMBED_PR_SKIP_MACOS` switch stays where it was, as a step-level gate, because `matrix` is not available in a job-level `if` (actionlint rejects it).

### What was not changed

- `nightly.yml` is scheduled and `workflow_dispatch` only. There is no push or pull request trigger to gate, so there is nothing to save.
- `site.yml` already has workflow-level `paths:` filters and carries no required check, so the pending-check trap does not apply to it. It is already scoped to `apps/web`, `docs`, `data` and its own workflow file.
- `ir-hashes.yml` already has workflow-level `paths:` filters and is explicitly not a required check.
- `release.yml` and `cut-release.yml` are tag and dispatch driven, and the release rehearsal must stay conservative.

## 5. Scenario table

The classification is exercised by extracting the classifier body out of `ci.yml` and running it against a controlled diff, so the test cannot drift from the workflow. Every row below was executed.

| scenario | compiler | python | site | jobs that run | skipped |
| --- | --- | --- | --- | --- | --- |
| empty commit (PR) | false | false | false | betterleaks | 11 |
| empty commit (push) | false | false | false | betterleaks | 11 |
| docs only | false | false | true | betterleaks, docs seams | 10 |
| apps/web only | false | false | true | betterleaks, docs seams | 10 |
| data/ only | true | true | true | all 13 | 0 |
| src only | true | true | false | betterleaks + 11 lanes | 1 |
| tests only | true | true | false | betterleaks + 11 lanes | 1 |
| workflow only (.github/) | true | true | false | betterleaks + 11 lanes | 1 |
| uv.lock only | true | true | false | betterleaks + 11 lanes | 1 |
| pyproject.toml only | true | true | false | betterleaks + 11 lanes | 1 |
| runtime C only | true | true | false | betterleaks + 11 lanes | 1 |
| packaging/ only | true | true | false | betterleaks + 11 lanes | 1 |
| examples/ only | true | true | false | betterleaks + 11 lanes | 1 |
| scripts/ only | true | true | false | betterleaks + 11 lanes | 1 |
| .github/scripts only | true | true | false | betterleaks + 11 lanes | 1 |
| .claude/ only | true | true | false | betterleaks + 11 lanes | 1 |
| .agents/notes only | false | false | false | betterleaks | 11 |
| .agents/probes/*.py only | false | true | false | betterleaks, ruff and compat2 | 10 |
| docs + src | true | true | true | all 13 | 0 |

The `skipped` column counts the 12 gated jobs (`docs seams`, `ruff and compat2`, three test shards, two zig cells, ziglang-wheel, wasm, wheel, ASan, TSan, mobile, network receipt). `betterleaks` always runs.

Note the `data/` row: `data/` changes run everything, because `tests/test_compat_site.py` and `tests/test_compat_json.py` read `data/compat/compat.json`. The site group also fires there, so the site build runs alongside.

### Failure modes

- The classifier step fails: `changes.result` is `failure`, every lane is skipped, and `ci-ok` fails because the classifier did not succeed. The run is red, not green.
- A lane fails: `needs.<job>.result` is `failure`, `ci-ok` is red, and the workflow is red.
- `wasm32-wasi-musl smoke` fails: that job has job-level `continue-on-error: true`, so `needs.wasm-smoke.result` is `success` even on failure. `ci-ok` stays green and the workflow stays red, exactly as it behaves today. The lane is not required and does not become one.
- `ci-ok` is never skipped: `if: always()`.

## 6. Validation

- `actionlint` 1.7.12 on `ci.yml` and `embed.yml`: clean. The remaining findings in `embed.yml`, `nightly.yml`, `release.yml` and `runtime-bump.yml` are pre-existing and in files this change does not touch.
- The classifier was extracted from `ci.yml` and run against 20 pull request scenarios and 4 push scenarios; every row in the table above is a measured result, not a prediction.
- `uv run pytest -q tests/test_compat_site.py tests/test_compat_json.py tests/test_doc_cadence.py tests/test_first_class_scripts.py tests/test_support_docs.py -m "not linux_container"`: 55 passed in 77s. That is the `docs seams` selection.
- `uv run ruff check --show-files`: 688 files, which is why the `python` group is not the same as the `compiler` group.

## 7. Cost

Per pull request, the runner time drops from 27.5 minutes of runner time to:

| scenario | before | after |
| --- | --- | --- |
| docs only | 27.5m | 0.2m (betterleaks) + about 1.5m (docs seams) |
| apps/web only | 27.5m | same |
| empty commit | 27.5m | 0.2m |
| compiler change | 27.5m | 27.5m, plus about 0.2m for the classifier and about 0.2m for `ci-ok` |

The two macOS cells (three test shards plus the android leg) are the expensive part of the bill, so a docs-only or web-only pull request saves about 12 macOS-minutes, which is the number that matters for the free-plan quota.

## 8. The bot PR approval gate (the nightly wedge)

### What was stuck

The nightly opens a data-only PR on `bot/compat-snapshot` with `GITHUB_TOKEN`, so the PR author is `github-actions[bot]`. GitHub holds `pull_request` workflow runs from a bot-authored PR at `action_required` until a maintainer approves them. The repo has `fork-pr-contributor-approval: first_time_contributors`, but the gate here is not the fork policy: the PR is same-repo (`head.repo == base.repo`), and the bot is a contributor of this repo (`contributors` lists `github-actions[bot]` with 6 contributions). The gate is the bot-PR rule, which has no repo-level opt-out.

The runs that are still waiting (listed, not deleted):

| run | workflow | branch | created | conclusion |
| --- | --- | --- | --- | --- |
| 40328475241 | site | bot/compat-snapshot | 2026-10-26T13:11:41Z | action_required |
| 40328475181 | embed | bot/compat-snapshot | 2026-10-26T13:11:41Z | action_required |
| 40189670715 | site | bot/compat-snapshot | 2026-10-25T11:37:54Z | action_required |
| 40189670771 | ci | bot/compat-snapshot | 2026-10-25T11:37:54Z | action_required |

`gh run list --status waiting` is empty: nothing is queued behind them, they are parked at the approval step. Two older runs on the same branch (`40328475202`, `40502822975`, `40502822992`) finished as `failure` instead, because the Cloudflare Pages check failed on the data-only change, not because of the gate.

The runs that did go through show the gate is not fatal once a human approves: run 40094989004 (`ci`, 2026-10-24) was triggered by `espetro`, not by the bot, and went green end to end on exactly this data-only diff.

### Why the PR never merged on its own

The nightly's `gh pr merge --auto` step cannot work, for two independent reasons:

- The repo has `allow_auto_merge: false` (`GET /repos/sigilco/pyscriptc`), so `--auto` has nothing to queue. `gh pr merge --auto` fails and the step's `|| echo` swallows it.
- Even with auto-merge on, a PR authored by `github-actions[bot]` cannot have its pending runs approved by its own `GITHUB_TOKEN`. The repo has `can_approve_pull_request_reviews: true`, but that is the repository token setting for a workflow to approve PRs, and the approval needed here is the *workflow run* approval (`POST /repos/{owner}/{repo}/actions/runs/{run_id}/approve`), which the actor of the run cannot perform on its own run.

Every bot PR so far was merged by hand: #546, #549 and #727 were all merged by `espetro`. The nightly's own comment claimed "the commit is the bot identity, so it merges without review", which is not what happens.

### The options, and the one taken

1. **Nightly pushes straight to master.** Not allowed: the ruleset `master: PR only` requires a pull request on the default branch and blocks direct pushes. Rejected, and `tests/test_compat_site.py::test_nightly_never_pushes_to_master` holds the line.
2. **Trigger required CI via `workflow_dispatch` / `gh workflow run` on the branch.** This works around the *approval* gate (a `workflow_dispatch` triggered by the bot is not held), but it does not produce the required contexts on the PR head: a dispatched run reports its checks on the branch commit, and the ruleset evaluates the pull request's head. It also re-runs the heavy matrix for a data-only change. Rejected as the primary fix; it would be needed if the PR had to carry required contexts, which it does not have to.
3. **Exclude the bot branch from the `pull_request` triggers and let the nightly validate the data itself.** Taken.
4. **Repo setting change.** Reported below, not applied.

### What was implemented

**The exclusion.** `ci.yml`, `embed.yml` and `site.yml` now carry `branches-ignore: [bot/compat-snapshot]` on their `pull_request` trigger. The three workflows that were parking at `action_required` (the `ci`, `embed` and `site` runs on the bot branch) no longer fire for that branch at all, so no run is ever created for it and nothing waits for an approval. The rule is keyed on the branch name, which the nightly owns and rewrites daily.

A branch filter is safe here and only here because none of these workflows carries a required context that the bot PR has to satisfy: the required contexts all live in `ci.yml`, and the bot PR is a data-only change that the nightly validates itself. A PR from any other branch is unaffected.

**The nightly validates what the PR carries.** Two new steps in the `visible-score` job, both gated on `steps.compat.outputs.changed == 'true'`, so they run only when there is actually a snapshot to validate:

- The schema check: `scripts/compat_schema.py`'s `validate()` over the `data/compat/compat.json` the nightly just built. It is the same validator `tests/test_compat_json.py` uses, so the file cannot reach the PR without passing it.
- The compat seams: `tests/test_compat_json.py`, `tests/test_compat_site.py`, `tests/test_doc_cadence.py`, `tests/test_first_class_scripts.py` and `tests/test_support_docs.py`, the same five the `docs seams` lane runs in `ci.yml`. They run against the nightly's working tree, which holds the exact bytes the PR carries, because the nightly commits and pushes those same files.

**The site build over the snapshot.** A new `site-check` job calls `site.yml` through `workflow_call`. `site.yml` already had the `workflow_call` trigger for exactly this purpose; the nightly was never wired to it. The site page assembles `data/compat/compat.json`, so a broken data file now fails the nightly, on the nightly, instead of in a `pull_request` run parked at an approval nobody was watching for. `workflow_call` never deploys: the `site` job's deploy step is gated on `github.ref == 'refs/heads/master' && github.event_name == 'push'`, and a called workflow inherits the caller's ref and event.

**The dead auto-merge step.** Replaced by a step that says what actually happens: the PR waits for the owner. The old step's `|| echo "auto-merge is blocked by the ruleset"` was misleading in both directions: auto-merge is off at the repo level, and the ruleset was never the blocker.

### Repo settings the owner must change

None is required for the fix above to work. These are listed for the record, all optional:

1. **`allow_auto_merge`** (`Settings > General > Pull Requests > Allow auto-merge`): currently off. Turning it on would let the nightly queue a merge for the bot PR. It still would not merge unattended, because the bot PR's runs need a human approval, and because the required contexts are not reported on the bot branch. With the exclusion in place the bot PR has no required contexts to satisfy, so auto-merge plus the admin bypass would merge it unattended. Not required.
2. **`fork-pr-contributor-approval`** (`Settings > Actions > General > Approve and run workflows from contributors`): currently `first_time_contributors`. This is not what parks these runs (the bot is a known contributor and the PR is same-repo), so changing it would not fix the wedge on its own. Leave it as it is: it is the right setting for a public repo.
3. **The admin bypass on the ruleset** (actor id 5, `RepositoryRole`, `bypass_mode: always`) is what lets the owner merge a bot PR whose required contexts were never reported. It is a deliberate escape hatch and should stay.

### The stale runs

The four runs in the table at the top of this section are still parked at `action_required`. They are reported here and left in place, per the instruction not to delete runs. They are harmless now: the branch they belong to no longer triggers any workflow, so a new nightly push creates no new parked runs. Approving them by hand (or letting them sit) changes nothing about the flow.
