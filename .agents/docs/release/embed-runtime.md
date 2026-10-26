# The embedded runtime pack: pinning, bumping and the security cadence

Operational doc for the `--embed-python=bundle` slice (plan:
`.agents/plans/2026-10-25-embedding-release-plan.md`, issues #657, #663, #664,
#662 under epic #428). The user-facing page is
`docs/reference/runtime-pack.md`; this one is how we maintain the pack.

## What is pinned where

- `packaging/runtime.lock.toml` pins the python-build-standalone (PBS) release
  tag, the per-platform asset name and sha256, and the CPython micro. It is
  the single source of truth; the fetched pack is verified against it and a
  mismatch refuses the compile.
- `packaging/third-party.toml` holds the third-party component inventory of
  the matching `full` archive. `scripts/gen_third_party_notices.py` renders
  `NOTICE` from it (`--check` mode is what CI-style verification uses).
- The pack itself is produced by the release workflow from the pinned PBS
  archive and attached to our GitHub release, mirrored so we do not depend on
  a third-party asset staying up. The `licenses/` directory of the `full`
  archive travels inside the pack.

## Manual runtime bump (works before the scheduled workflow lands)

1. Find the newest PBS release with a 3.14 micro newer than the pinned one
   (`gh release view --repo astral-sh/python-build-standalone`, or the
   releases page).
2. Update `packaging/runtime.lock.toml`: new release tag, asset names,
   sha256 (from the PBS release's `SHA256SUMS`), new CPython micro.
3. Update `packaging/third-party.toml` from the new `full` archive's
   `licenses/` directory and re-run
   `uv run python scripts/gen_third_party_notices.py`.
4. Open the PR. The embed CI jobs (`.github/workflows/embed.yml`) must be
   green; they run the empty-environment acceptance programs against the new
   pack, so the PR merge is the test.

A minor-version change of CPython (3.14 -> 3.15) is not a lock bump; it is a
pack format decision and follows the plan's pinning section.

## Scheduled bump workflow

`.github/workflows/runtime-bump.yml` runs weekly (`workflow_dispatch` for
manual runs plus a `schedule` trigger) and does the check above: it reads the
PBS latest release, and only when a newer 3.14 micro exists opens a PR that
bumps the lock (and regenerates `NOTICE`). It never publishes and never
pushes a tag. Merging the PR requires the embed jobs, so a bumped runtime has
passed the acceptance programs before it lands.

The workflow is written against the lock schema; until #657 lands the lock
file and the jobs it needs, dispatches exit cleanly with "no lock file" so the
schedule is inert rather than red.

## Security SLA

A CPython security release is shipped as a pyscriptc patch release within 7
days: check for the PBS rebuild on the first working day after python.org
announces it, run the manual bump (or trigger the scheduled workflow by hand),
and cut a patch through the normal rehearsal and bar process
(`release-process.md`). The 7-day ceiling matches the release policy's "no
unreleased user-visible change older than 7 days".

## CI: the embed jobs and the minutes budget

`.github/workflows/embed.yml` runs the acceptance slice on Linux x86_64
(`ubuntu-24.04`, glibc 2.39) and macOS arm64 (`macos-14`). Pull requests run
it path-filtered on the embedding surface (`boxed.py`, `embed.py`,
`_driver.py`, `ps_island.c`, the lock and inventory, the embed scripts and
the two workflows); pushes to master, the nightly schedule and the release
rehearsal (`workflow_dispatch` with `always_run=true`) always run it.

- Budget: 4 minutes per cell target, 6 minutes hard timeout, 2 cells, so a
  full run costs at most about 12 runner minutes. Measured minutes are
  recorded in issue #662 at close (estimate until then: compile plus link
  dominates).
- The pack is cached with `actions/cache` keyed on the lock file hash
  (`pyscriptc-runtime-<platform>-<lockhash>`), so one pack download per lock
  change.
- macOS minutes cost 10x Linux on free plans. If the macOS PR cost proves
  high, set the repository variable `EMBED_PR_SKIP_MACOS=true`; the macOS
  cell then runs on nightly and rehearsal only, without a code change.
- Before the runtime pack lands (#657), the jobs are a documented clean skip
  (no lock file), not red runs.

## Version in receipts

The runtime micro the pack pins is printed by `pyscriptc --version --verbose`
and written to every compile receipt (the `summary.json` of a `--debug-dir`
run and the island report), so a support question about an embedded-binary
program can be answered from the artifact alone. The field is the pack id
(PBS release tag plus CPython micro), set when the lock is read; with no pack
in play it is absent and nothing else in the receipt changes. The wiring into
`--version` and the receipt writer lands with the runtime pack code (#657);
the field name (`runtime`) is fixed here so consumers can rely on it.
