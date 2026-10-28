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

Every one of them is a job name that this change keeps exactly as it is. No job is renamed, no check name changes, so the ruleset needs no edit to keep working. `betterleaks` in particular is never gated: it always runs, because a secret can be in any file.

### A finding about the ruleset that is not mine to fix

The ruleset requires `zig cc emitted-IR smoke`, but the job reports two checks: `zig cc emitted-IR smoke (0.16.0)` and `zig cc emitted-IR smoke (0.17.0)`. Required status checks match on the literal reported name, so the literal string `zig cc emitted-IR smoke` never arrives.

This is masked today because the ruleset has a bypass actor: `RepositoryRole` id 5 with `bypass_mode: always`, which is the repository admin. Every recent merge (`#737`, `#739`, `#742`, `#745`) was merged by that admin, so the bypass carries the merge and the missing context never blocks anything.

It is not a regression from this change, and this change does not make it worse, but it is worth writing down: as long as the admin merges every pull request, the required-check list is decorative for the matrix jobs. An admin who wants the ruleset to actually gate would change the required context to the two rendered names, or add a single `ci-ok` context and require that.

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
2. Ruleset 25570891, required status checks: replace `zig cc emitted-IR smoke` with `zig cc emitted-IR smoke (0.16.0)` and `zig cc emitted-IR smoke (0.17.0)`, so the required list matches the names the workflow actually reports. This is a separate fix from the path gating and should be its own change, because it changes what blocks a merge.
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
