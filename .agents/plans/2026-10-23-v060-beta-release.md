# v0.6.0 beta channel

Status: proposed 2026-10-23. Ticket: to be filed against milestone `v0.6.0 public` (effort M, label `release`).

## Why

v0.6.0 is dense: project mode, generators and coroutines, async, FFI, threads, the pyrefly bridge, several native packs and the public site. A beta gives testers a separate, opt-in build to break before the stable tag, without touching the default `pip install pyscriptc` or the Latest GitHub release.

## Version scheme

Decision 2026-10-23 (owner): the public spelling is `0.6.0-beta.1`, then `0.6.0-beta.2`. The tag is `v0.6.0-beta.1` and the GitHub release title says `v0.6.0-beta.1`. PyPI and `pyproject.toml` use the PEP 440 canonical form `0.6.0b1` that pip, uv and PyPI normalize `0.6.0-beta.1` to, so one helper maps between the two forms and `release.yml` checks that the tag and `pyproject.toml` normalize to the same version (`packaging.version.Version`). pip and uv skip pre-releases unless asked, so `pip install pyscriptc` keeps resolving to the last stable. Testers use `pip install --pre pyscriptc`, `uv tool install --prerelease allow pyscriptc` or `pyscriptc==0.6.0b1`.

Cut timing: on the owner's signal only. The tooling work below lands ahead of time; the beta itself is not cut until the owner says so.

## Current state (what already works)

| Piece | State |
|---|---|
| `release.yml` triggers on `v*` tags, so `v0.6.0b1` runs the build matrix | works |
| Rehearsal (`workflow_dispatch` with `ref`) | works |
| `cut-release.yml` `prerelease` bump with `rc_of` | rc only: `VERSION` regex in `.github/scripts/release_bar.py:33` and the release commit subject regex in `cut-release.yml:244` accept `rcN`, not `bN` or `-beta.N` |
| PyPI publish job | publishes whatever the tag says, behind the `pypi-publish` environment approval |
| GitHub release | created as draft; needs the prerelease flag for `b`/`rc` tags |

## Work items

| # | Item | Effort | Notes |
|---|---|---|---|
| 1 | Accept `-beta.N` tags (and `bN` in `pyproject.toml`) in `release_bar.py` (`VERSION`, `next_version`, `--bump prerelease` with a `--pre-kind beta\|rc` input) and in the `cut-release.yml` commit-subject regex | S | `MAX_RC` becomes `MAX_PRE` or a separate `MAX_BETA` (suggest 4). Tests in the existing release-bar test file. |
| 2 | `release.yml`: mark GitHub releases for `b`/`rc` tags as prerelease, never Latest | S | `gh release create --prerelease`; keep draft so the owner publishes by hand |
| 3 | Release notes: the changelog section for a beta is "Unreleased since the last stable" plus a "How to test" block (install lines below, known gaps, where to report) | S | extend `release_notes.py`; no new dependency |
| 4 | Beta bar: no minor-style gates (0 open issues, 5-day gap) for a beta; require a green rehearsal on the exact commit, at least 2 user-visible features since the last stable, and the open-issue list triaged (each issue either in the beta, or moved to `Future`) | S | encode as the `prerelease` branch of the bar |
| 5 | Tester guide: `docs/` page "Trying the beta" with install per channel (pip, uv tool, the GitHub release binaries and `sha256sums.txt`), what is in scope, how to report (issue template `beta-feedback`), how to roll back to stable | S | user docs, so it counts as user-visible |
| 6 | Telemetry: tag events with the version (already in `run.finished`); confirm beta builds report as `0.6.0b1` and the privacy notice (#297, owner approval pending) covers beta testers before recruiting outside people | S | blocked on the owner for the notice wording |
| 7 | Tester feedback loop: GitHub Discussions or a `beta` label plus a pinned issue; a tester checklist (compile and run examples, project mode, one real script) | S | owner chooses the channel |

## Cut sequence for `0.6.0b1`

1. Land items 1 to 3 (one PR, tooling only). Rehearse `release.yml` against a `0.6.0b0`-style dry run via `cut-release` `dry_run`.
2. Triage the 21 open issues on the `v0.6.0 public` milestone: in the beta, in the stable only, or moved to `Future`. Owner decision for #304, #297 and the Windows and WASM PRs (#249, #283, #313).
3. Freeze merges (the `<maki-scratch>/MERGE_HOLD` mechanism), rehearse the exact tip commit, run `cut-release` with `bump=prerelease`, kind `beta`, merge the release PR with `--admin`, tag by hand (`RELEASE_TOKEN` is still unset), publish the draft as a pre-release.
4. Approve the PyPI publish (owner) so `pip install --pre` works. If you want a TestPyPI dry run first, add it as an optional job; not needed for the first beta.
5. Soak: at least 5 days or until the tester checklist is clean. Fixes land on master as normal; cut `b2` if more than 5 user-visible fixes land, or if a tester-blocking bug is fixed.
6. Promote: `cut-release` with a stable minor bump from `0.6.0bN` produces `0.6.0` (the same promotion rule rc uses today), then the normal minor bar applies: 0 open issues, measured receipt (census delta, boxed ledger window or perf number), at least 5 days after the previous minor.

## Risks

| Risk | Mitigation |
|---|---|
| A `b` tag publishing to PyPI as a normal release | PEP 440 pre-release is invisible to default installs; the environment approval is the second gate |
| Beta installs overwrite stable binaries for testers | document `pip install --pre` in a venv or `uv tool` and the rollback line |
| Telemetry opt-in for outside testers before the privacy notice is approved | do not recruit external testers until #297 is approved |
| Frozen gates (`outcomes.py`, `coverage_map.py` etc.) | untouched by this plan; #304 stays an owner decision |
| Dense milestone slips | the beta can ship with a subset; unfinished lanes (async D/E, FFI F3, mobile) go to `Future` |

## Decisions needed from the owner

- Done: spelling `0.6.0-beta.1`.
- Cut signal: owner's call, after the in-flight lanes and the milestone triage.
- Who the testers are and the feedback channel.
- Privacy notice (#297) approval before recruiting outside testers.
