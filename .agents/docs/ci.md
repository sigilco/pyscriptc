# CI map and speed notes

The `ci` workflow (`.github/workflows/ci.yml`) is the merge gate for
`master: PR only`. This page records what each job does, what it costs,
and how the cheap checks are shifted left into a pre-push hook.

## Job map

| Job | Required context | Runs when | Typical duration |
| --- | --- | --- | --- |
| `classify paths` | - | always | ~10 s |
| `betterleaks` | `betterleaks` | always | ~10 s |
| `ruff and compat2` | `ruff and compat2` | any Python or non-docs change | ~1 min |
| `docs seams` | - | docs/data/site changes | ~1 min |
| `tests without linux_container (shard N/3)` | `tests without linux_container (shard N/3)` x3 | compiler changes (PRs); always on master push | ~5-10 min each |
| `zig cc emitted-IR smoke` | `zig cc emitted-IR smoke` | compiler changes (PRs); always on master push | ~5-12 min (two zig versions) |
| `wheel install smoke` | `wheel install smoke` | compiler changes (PRs); always on master push | ~1 min |
| `stage-2 fixpoint`, `ziglang wheel PATH shim`, `wasm32-wasi-musl smoke`, `ASan+UBSan smoke`, `TSan smoke`, `mobile archives`, `network receipt` | - | compiler changes (PRs); always on master push | ~0.5-2 min each |
| `ci-ok` | - | always (aggregator) | ~10 s |

Durations are medians from `gh run view --json jobs` over recent master
and PR runs; the zig 0.16.0 smoke is the noisiest lane (4-12 min).

## Required checks

The ruleset `master: PR only` requires exactly these contexts, matched by
reported name:

- `betterleaks` (the `secrets` job reports this name directly)
- `ruff and compat2` (the `unit` job reports this name directly)
- `zig cc emitted-IR smoke` (reported by the `zig-ok` aggregator, because
  the real lane is a matrix and would report `zig smoke (0.16.0)` etc.)
- `wheel install smoke` (the `wheel-smoke` job reports this name directly)

The shard aggregators (`shard-ok`, three matrix legs) exist for the same
reason: a matrix job cannot report the bare `tests without linux_container
(shard N/3)` names the ruleset expects, and a skipped matrix job reports
unrendered `${{ matrix.* }}` names. Do not rename these jobs or the
aggregators; a renamed context blocks every merge.

## Path gating

A `classify paths` job diffs the PR and sets three outputs. Jobs gated on
them report `skipped`, which satisfies a required check, so docs-only PRs
finish in under a minute while still reporting every required context.
A workflow-level `paths:` filter was rejected on purpose: it would leave
required checks pending forever. `push` to master always runs the full
matrix except the empty "retrigger" commit. The classification rules and
the list of test-read paths that force the full matrix are documented in
the header comment of `ci.yml`.

## What was already fast, and what changed

Already in place before this pass: per-PR concurrency with
`cancel-in-progress`, job-level path gating, the zig tarball cache,
`setup-uv` caching, and pytest-split shards driven by
`tests/.test_durations`.

This pass added:

- `curl --retry 3 --retry-delay 5 --retry-all-errors` on every external
  download (zig, wasmtime, wasm-tools, NDK, betterleaks). Download flakes
  were the only retry-worthy failure class; test failures are never
  retried.
- `actions/cache` entries for the wasmtime and wasm-tools tarballs in the
  wasm smoke lane (previously downloaded on every run).
- A refreshed `tests/.test_durations` so pytest-split balances the three
  shards on current timings (the file had gone stale; shard 3/3 was the
  critical path at ~10 min vs ~4.5 min for shard 1/3).

Measured baseline (30 most recent `ci` runs, 2026-11-06): full-matrix PR
runs took ~13-20 min wall time; docs-only PRs ~1 min; queue time was
seconds. Expected after: the critical path is still the zig 0.16.0 smoke
or the slowest shard, so wall time lands around ~10-13 min on a warm
cache. The bigger wins are reliability (no download flakes) and the
pre-push hook below, which removes the lint/format/support-table class
of failure before CI ever starts.

## Pre-push hook

`scripts/hooks/pre-push` runs the cheap gates locally in well under a
minute, scoped to the files being pushed:

- conflict markers in changed files
- `ruff check` + `ruff format --check` (whole tree, CI excludes) when
  Python or ruff config changed
- `scripts/gen_support_table.py --check` when its inputs changed
  (`src/`, `scripts/support_data.toml`, `.agents/docs/support/`,
  `data/compat/`)
- em-dash and absolute-home-path scan on added lines of changed markdown
- census pin check (`tests/test_census_self.py`) is opt-in via
  `PYSC_HOOK_CENSUS=1`: it lowers the whole compiler tree and costs
  minutes, over the hook budget. The CI shards always run it.

Install once per clone or worktree:

```
mise run hooks:install
```

That sets `core.hooksPath` to `scripts/hooks`. Skip a single push with
`git push --no-verify`. `mise run check:fast` runs the same checks
against uncommitted working-tree changes.

## What is deliberately not done

- No required check was renamed, split or removed.
- `linux_container` tests stay out of the shard jobs; the TSan job is
  the only lane that runs them, as before. They are not in
  `nightly-gates.yml`, so moving them would drop a gate.
- No pytest retries anywhere: flakes are fixed, not hidden.
- Lint is not hoisted into a separate gate job in front of the shards:
  `ruff and compat2` already fails in ~1 min and the shards run in
  parallel anyway, so serializing would add latency to every green run
  to save minutes only on the rare lint-failure path.
