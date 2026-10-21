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

A tag pushed with the default `GITHUB_TOKEN` does not start other workflows, so `release.yml` would never run. `cut-release` therefore checks out and pushes with the `RELEASE_TOKEN` secret. Use a fine-grained personal access token or a GitHub App token with contents write on this repository. The job fails before doing anything if the secret is missing.

If master has branch protection that requires pull requests, the push to master will be rejected. Either allow the token's identity to bypass that rule or cut releases from a release branch and merge it. Do not weaken protection for other pushes.

## Nightly

`nightly.yml` runs at 06:17 UTC and can be started by hand. It scores the visible corpus, records the boxed cost ledger window, and uploads both as a run artifact kept for 90 days. The job summary shows the weighted pass. A failed step opens an issue labelled `nightly-failure`, or comments on the open one. A nightly failure does not block master.

The bolo `repository_dispatch` step is a stub. It is not wired until a token decision is made.

## Release notes

The draft release body comes from `release.yml` and is unchanged by this process. Changelog entries are written from conventional commit subjects, so keep subjects accurate: they become the notes.

## Release classes and bar

See the Release policy section in `AGENTS.md` for the thresholds (patch, hotfix, minor, rc) and `.agents/plans/2026-10-21-release-cadence-strategy.md` for the data behind them. Rehearse the exact commit (full build matrix, smoke, wheel verify, no publish) before tagging, and use an `X.Y.ZrcN` tag for packaging changes. The automated bar check and the rehearsal dispatch input are not implemented yet; until then apply them by hand.
