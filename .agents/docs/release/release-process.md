# Release process

Releases are cut from master by a manual workflow and built by a tag-triggered workflow. Publishing to PyPI stays behind an approval gate.

## Rehearse before tagging

Every release, including rc and hotfix, needs a successful rehearsal of the exact commit first. A rehearsal is a `workflow_dispatch` run of `release.yml`: the full build matrix, smoke, wheel build and wheel verify, with the version read from `pyproject.toml`. It never creates a GitHub Release and never publishes to PyPI.

Dispatch it on the commit you intend to release (the tip of master) with the full SHA, so the run title is `rehearsal <full SHA>`:

```sh
mise run release:rehearse
```

That wraps `gh workflow run release.yml --ref master -f ref="$(git rev-parse origin/master)"`.

Leaving `ref` empty rehearses the dispatched commit, which has the same title. A branch name works for a trial run, but only a run titled `rehearsal <full SHA>` counts for the gates below. The matrix takes about 20 minutes.

## Cut a release

Master is PR-only, so a release is two runs of the `cut-release` workflow (Actions tab, run from master) around a merge.

0. Validate doc cadence: run `uv run python scripts/check_doc_cadence.py` to ensure no per-nightly metric or volatile count is hard-coded into markdown files (see `.agents/docs/doc-cadence.md`).
1. Rehearse the tip of master (above) and wait for it to pass.
2. Run `cut-release` with `stage=prepare`, `dry_run` on (the default). It prints the new version, the changelog entry and the bar report (one pass or fail line per check). Entries come from conventional commit subjects since the last stable `v*` tag: `feat` goes to Added, `fix` to Fixed, `!` to Breaking, `chore`, `test` and `style` are left out, merge and `chore(release)` commits are skipped. The dry run is red when the real run would be refused.
3. Re-run with `dry_run` off. It opens a PR from `release/vX.Y.Z` titled `chore(release): vX.Y.Z` that bumps `pyproject.toml`, runs `uv lock` and prepends the changelog entry. Merge it (squash or rebase) while master is still at the rehearsed commit. If master moved, rehearse the new tip first.
4. Run `cut-release` with `stage=tag` and `dry_run` off. It checks that master HEAD is the release commit (only `pyproject.toml`, `uv.lock` and `CHANGELOG.md` changed, version matches, tag free) and that its parent was rehearsed, then pushes the annotated tag `vX.Y.Z`.
5. The tag push starts `release.yml`. It builds the five binaries, smoke-tests them, drafts the GitHub Release with `sha256sums.txt`, and runs the wheel verify job.
6. The PyPI publish job first checks that a successful rehearsal run covers the tagged commit, or its parent when the release commit only touched the three release files. It has no bypass: rehearse, then re-run the job. After that it waits for a required reviewer in the `pypi-publish` environment. Approve it only after the draft release and smoke results look right.

## The bar

`cut-release` computes the checks in `.github/scripts/release_bar.py`. Thresholds live in the top-level `env` block of `cut-release.yml`: `PATCH_MIN_COMMITS`, `PATCH_MIN_HOURS`, `PATCH_MIN_PRS`, `PATCH_MIN_USER_CHANGES`, `MINOR_MIN_FEATURES`, `MINOR_MIN_DAYS`, `MAX_PRE` (the cap shared by beta and rc iterations). Change one line there to tune. A user-visible change is a `feat`, `fix` or `perf` commit touching `src/` (runtime C included), `packaging/`, `README.md` or a user doc (anything under `docs/`).

Inputs:

- `bump`: `patch`, `minor`, `major` or `prerelease`. On a prerelease version, a stable bump keeps it a prerelease of the same base and kind (`0.6.0b2` stays `0.6.0b2`); promote by cutting a stable from a stable checkout, or run the stable bar against the base version.
- `pre_of`: with `prerelease` from a stable version, which release the first prerelease is for (`patch` gives `0.4.2rc1`, `minor` gives `0.5.0rc1`).
- `pre_kind`: `beta` or `rc` (default `beta`). Only used when cutting the first prerelease off a stable version; an existing prerelease always counts up in its own kind. A beta of `0.6.0` is tagged `v0.6.0-beta.1` and carries `0.6.0b1` in `pyproject.toml`.
- `hotfix`: patch only. Skips the content and gap checks, keeps the rehearsal. It needs a linked issue labelled `hotfix` that reproduces an install or first-run break; this is a manual check.
- `bypass_reason`: required to proceed past any failing check. It is echoed in the job summary, the release PR and the release commit, and the tag message. Nothing is bypassed silently. A bypass cannot fix "nothing releasable" (only chore, test and style commits) and does not skip the publish gate.
- `dry_run` (default on) and `stage` (`prepare` or `tag`).

Checks by class:

- Patch: user-visible change count or merged PR count, commits since the last stable tag, hours since it, and a rehearsal of the base commit.
- Hotfix: patch bump and a rehearsal; the evidence line is manual.
- Minor and major: user-visible `feat` count, days since the previous minor, 0 open issues in the milestone titled `vX.Y.Z` (a title like `v0.5.0` or `v0.4.1 alpha` matches; no milestone is a failure), and a rehearsal. The measured receipt is a manual line.
- Prerelease, rc: rc number at most `MAX_PRE`, and a rehearsal. Use an rc for packaging changes and before any minor.
- Prerelease, beta: beta number at most `MAX_PRE`, at least `MINOR_MIN_FEATURES` user-visible `feat` commits since the last stable tag, a rehearsal, and the manual triage line: every open issue on the milestone is either in the beta or moved to `Future`. The minor-only gates (0 open issues, 5-day gap, measured receipt) do not apply to a beta; they apply again when the beta is promoted to the stable minor.

## Guards

The `cut-release` concurrency group queues concurrent runs and never cancels one. The job refuses to run from any branch other than master, refuses if the tag already exists on origin or the `release/vX.Y.Z` branch exists, and refuses if there are no releasable commits since the last tag.

## Secret and branch protection

Master changes come from pull requests. Direct push is exceptional, and the reason goes in the commit message.

Enforcement is the repository ruleset `master: PR only` (on sigilco/pyscriptc, target branch the default branch). It requires a pull request with 0 approvals (solo maintainer plus agents, so a required review would only block; the gate is CI), requires the status checks `betterleaks`, `ruff and compat2`, `zig cc emitted-IR smoke` and `wheel install smoke` to pass on the PR head, blocks force pushes and deletion, and lets the Repository admin role bypass (bypass mode always, so an admin can still push directly in an emergency). Required checks are not branch-up-to-date strict. Merge, squash and rebase are all allowed. The `tests without linux_container` check is not required yet: PR #128 shards it into `tests without linux_container (shard N/3)`, and a required name that no job reports blocks every PR. Add the three shard names to the ruleset when #128 lands. Flaky checks must not be added to the required list.

Inspect it with `gh api repos/sigilco/pyscriptc/rulesets`. The repo is public under the free `sigilco` organization, which is why rulesets are available without a paid plan.

A tag pushed with the default `GITHUB_TOKEN` does not start other workflows, so `release.yml` would never run. `cut-release` therefore prefers the `RELEASE_TOKEN` secret (a fine-grained personal access token or GitHub App token with contents write and pull requests write on this repository). The `prepare` stage works without it, the `tag` stage does not: it fails fast with the manual command (`git tag -a vX.Y.Z <merged release commit> && git push origin vX.Y.Z`, run by an admin; a person's tag push starts `release.yml`). As of 2026-10-21 the secret is not set on the repository.

How `cut-release` coexists with the ruleset: it never pushes to master. The `prepare` stage pushes `release/vX.Y.Z` and opens a PR with the `chore(release): vX.Y.Z` commit, which only touches `pyproject.toml`, `uv.lock` and `CHANGELOG.md`. The `tag` stage pushes only the tag, and tags are not covered by the ruleset. Without `RELEASE_TOKEN`, the PR is opened with `GITHUB_TOKEN`. That needs the repository setting "Allow GitHub Actions to create and approve pull requests" (Settings > Actions > General; it is off as of 2026-10-21), and a PR made that way does not start CI, so the required checks never report. Close and reopen the PR to start them, or merge with the admin bypass. With `RELEASE_TOKEN` set, none of that applies. Owner action to remove both caveats: add the `RELEASE_TOKEN` secret. Do not weaken the ruleset for other pushes.

## Nightly

`nightly.yml` runs at 06:17 UTC and can be started by hand. It scores the visible corpus, records the boxed cost ledger window, and uploads both as a run artifact kept for 90 days. The job summary shows the weighted pass. A failed step opens an issue labelled `nightly-failure`, or comments on the open one. A nightly failure does not block master.

The bolo `repository_dispatch` step is a stub. It is not wired until a token decision is made.

## Release notes

The draft release body comes from `release.yml` and is unchanged by this process. Changelog entries are written from conventional commit subjects, so keep subjects accurate: they become the notes.

Every release body opens with this standard line:

> pyscriptc targets CPython 3.14.x. Parsing, probes and the compatibility oracle follow 3.14. Compiled programs match CPython 3.14 byte-for-byte on stdout for everything that lowers; documented divergences are listed in docs/reference/divergences.md.

## Release classes and bar

See the Release policy section in `AGENTS.md` for the thresholds (patch, hotfix, minor, rc) and `.agents/plans/2026-10-21-release-cadence-strategy.md` for the data behind them. The rehearsal and the automated bar are described above in "Rehearse before tagging" and "The bar".

## Starting thresholds in plain language

These are the numbers behind the bar in `AGENTS.md`. They are starting values, tuned after the next two releases. They live in the top-level `env` block of `cut-release.yml`, so the owner changes one line (the minor receipt is a manual check).

Patch release (the frequent one), all of these must hold:

- At least 1 user-visible change (a `feat`, `fix` or `perf` commit that touches code or docs a user reads), or at least 3 merged PRs. Why: a release with nothing for a user to notice only burns a version number, and PyPI versions can never be reused.
- At least 10 commits since the last tag. Why: our three releases after v0.2.0 had 3, 11 and 25 commits; a floor stops a release made of one test tweak.
- At least 12 hours since the last tag. Why: those same releases were 26 to 61 minutes apart, and a gap gives CI, the nightly and early users time to find a problem before the next number is spent.

Minor release (a theme, not a calendar slot), all of these must hold:

- At least 2 user-visible features. Why: a minor is a story you can tell in a sentence; one small change is a patch.
- At least 5 days since the previous minor. Why: it caps the minor pace at about one a week and forces the theme to be finished, not sliced.
- 0 open issues in the matching GitHub milestone. Why: the milestone holds the exit criteria; open issues mean the theme is not done.
- A measured receipt (census delta, boxed ledger window, or a perf number with its baseline). Why: a claim in the release notes needs a number behind it, which the changelog plan already requires.

Hotfix patches skip the content and gap checks only, and need a documented install or first-run break. Every release, including hotfixes, needs a passing rehearsal on the exact commit first.
