# Release process

Releases are cut from master by a manual workflow and built by a tag-triggered workflow. Publishing to PyPI stays behind an approval gate.

## Cut a release

1. Run the `cut-release` workflow from the Actions tab on master. Choose `bump` (patch, minor or major). `dry_run` is on by default: the run prints the new version and the changelog entry, and creates nothing.
2. Read the dry run output in the job summary. Entries come from conventional commit subjects since the last `v*` tag. `feat` goes to Added, `fix` to Fixed, `!` or a breaking marker to Breaking. `chore`, `test` and `style` are left out. Merge commits and `chore(release)` commits are skipped.
3. Re-run with `dry_run` unchecked. The workflow bumps `version` in `pyproject.toml`, prepends the entry to `CHANGELOG.md`, runs `uv lock`, commits `chore(release): vX.Y.Z` to master, creates an annotated tag `vX.Y.Z`, and pushes both.
4. The tag push starts `release.yml`. It builds the five binaries, smoke-tests them, drafts the GitHub Release with `sha256sums.txt`, and runs the wheel verify job.
5. The PyPI publish job waits for a required reviewer in the `pypi-publish` environment. Approve it only after the draft release and smoke results look right.

## Guards

The `cut-release` concurrency group queues concurrent runs and never cancels one. The job refuses to run from any branch other than master. It refuses if the tag already exists on origin, and it refuses if there are no releasable commits since the last tag.

## Secret and branch protection

Master changes come from pull requests. Direct push is exceptional, and the reason goes in the commit message.

Enforcement is the repository ruleset `master: PR only` (on sigilco/pyscriptc, target branch the default branch). It requires a pull request with 0 approvals (solo maintainer plus agents, so a required review would only block; the gate is CI), requires the status checks `betterleaks`, `ruff and compat2`, `zig cc emitted-IR smoke` and `wheel install smoke` to pass on the PR head, blocks force pushes and deletion, and lets the Repository admin role bypass (bypass mode always, so an admin can still push directly in an emergency). Required checks are not branch-up-to-date strict. Merge, squash and rebase are all allowed. The `tests without linux_container` check is not required yet: PR #128 shards it into `tests without linux_container (shard N/3)`, and a required name that no job reports blocks every PR. Add the three shard names to the ruleset when #128 lands. Flaky checks must not be added to the required list.

Inspect it with `gh api repos/sigilco/pyscriptc/rulesets`. The repo is public under the free `sigilco` organization, which is why rulesets are available without a paid plan.

A tag pushed with the default `GITHUB_TOKEN` does not start other workflows, so `release.yml` would never run. `cut-release` therefore checks out and pushes with the `RELEASE_TOKEN` secret. Use a fine-grained personal access token or a GitHub App token with contents write on this repository. The job fails before doing anything if the secret is missing. As of 2026-10-21 the secret is not set on the repository, so `cut-release` cannot run until the owner adds it.

How `cut-release` coexists with the ruleset: it pushes the `chore(release): vX.Y.Z` commit straight to master and then the tag. Tags are not covered by the ruleset. The commit push is accepted when the identity behind `RELEASE_TOKEN` holds the Repository admin role (a PAT of an admin user does) because that role bypasses the rule. If you use a GitHub App token instead, add the app as a bypass actor of the ruleset (`gh api` bypass_actors with actor_type `Integration`, bypass_mode `always`). A release commit is the one routine direct push; its subject is `chore(release): vX.Y.Z` and it only touches `pyproject.toml`, `uv.lock` and `CHANGELOG.md`. The alternative, making `cut-release` open a version-bump PR and tag after merge, is cleaner but adds a second workflow run and a merge wait, so it is deferred. Do not weaken the ruleset for other pushes.

## Nightly

`nightly.yml` runs at 06:17 UTC and can be started by hand. It scores the visible corpus, records the boxed cost ledger window, and uploads both as a run artifact kept for 90 days. The job summary shows the weighted pass. A failed step opens an issue labelled `nightly-failure`, or comments on the open one. A nightly failure does not block master.

The bolo `repository_dispatch` step is a stub. It is not wired until a token decision is made.

## Release notes

The draft release body comes from `release.yml` and is unchanged by this process. Changelog entries are written from conventional commit subjects, so keep subjects accurate: they become the notes.

## Release classes and bar

See the Release policy section in `AGENTS.md` for the thresholds (patch, hotfix, minor, rc) and `.agents/plans/2026-10-21-release-cadence-strategy.md` for the data behind them. Rehearse the exact commit (full build matrix, smoke, wheel verify, no publish) before tagging, and use an `X.Y.ZrcN` tag for packaging changes. The automated bar check and the rehearsal dispatch input are not implemented yet; until then apply them by hand.

## Starting thresholds in plain language

These are the numbers behind the bar in `AGENTS.md`. They are starting values, tuned after the next two releases. They live in the plan and the AGENTS.md policy for now; the plan is to move them into an `env` block at the top of `cut-release.yml` so the owner changes one line. That block is not implemented yet.

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
