# pyscriptc agent notes

- GitHub Project: https://github.com/orgs/sigilco/projects/6 (decision 2026-10-21, replaces 2026-10-14 "none yet"). Versions are tracked as GitHub milestones (v0.4.1 alpha, v0.5.0, v0.6.0 public, v0.7.0 to v0.9.0) that hold scope and exit criteria; every product PR carries a milestone and `Closes #N`. The board holds triage and horizon: field `Horizon` (Current = the v0.4.1 alpha, Next = v0.5.0, Later = the rest), `Status` (Todo, In Progress, Done), `Effort` (S/M/L/XL), `Iteration` (quarter, `2026-Q4 M3`), `Start date` and `Target date`, `Track` (V1 oracle to V5 harness), `Release class` (patch, minor, hotfix). Add every new issue and PR to the board and set Horizon and Effort when it is refined; group by Milestone for the per-release view, by Horizon for triage.
- Active plan: `.agents/plans/2026-10-21-roadmap-v0.2-to-v0.9.md` (release table: class, theme, exit criterion) with `.agents/plans/2026-10-21-release-cadence-strategy.md` (release policy) and `.agents/plans/2026-10-21-go-public-readiness.md` (alpha and public gate). `2026-10-14-waves-after-scorer-v2.md` and `2026-10-14-scorer-v2-and-strategy.md` are history. Handoff: `.agents/notes/2026-10-14-wave-a-handoff.md`.
- v1 scorer (`benchmarks/compat/`) is frozen history. v2 lives in `benchmarks/compat2/`; shared contract is `benchmarks/compat2/outcomes.py`.
- Frozen v2 gates (tag `gates-v2`, 2026-10-16). Changes need a recorded decision first: `outcomes.py`, `score.py` weights, `coverage_map.py`, `cache.py`, `calibrate.py`, `CALIBRATION.json`, `TRIAGE.md`, `probes/*.jsonl`, `probes/IR_HASHES.json`, `canonical.py` rules (`docs/ir-canonicity.md`). Regenerating `IR_HASHES.json` requires the A/B byte-identical discipline (`python -m benchmarks.compat2.irscore` twice, `cmp`).
- Sweep evidence for the 3.12 corpus: #22 (triage zero-deltas, verdict replays), #23 (calibration cells >= 26, headroom 0 capped), #24 (generation byte-identical). Boxed ledger at the boundary (2026-10-16, window `06ed55b`): code 1.39%, churn 0%, bugs 0%, tax 0%, drop_flag false.
- Package manager: `uv`. Checks: `uv run pytest`, `uv run ruff check`, `uv run ruff format --check`, `uv run python -m benchmarks.baseline.run --check`.
- Compiler debug mode: `pyscriptc compile FILE --debug[=stages,fences,cc,paths,all]` (env `PYSC_DEBUG`) prints a stage/fence/cc table to stderr; `--debug-dir DIR` dumps `ir.json`, `out.ll`, `cc.sh`, `summary.json`. Details in `.agents/docs/debug.md`.
- Master changes come from PRs: the ruleset `master: PR only` requires a PR and the CI checks `betterleaks`, `ruff and compat2`, `zig cc emitted-IR smoke`, `wheel install smoke`, blocks force push and deletion, and lets only the repository admin role bypass. Direct push is exceptional (emergency only; `cut-release` opens a release PR and pushes only the tag); give the reason in the commit message. Details in `docs/release-process.md`.
- Running tests: `uv run pytest -n 6 -m "not linux_container"` (CI selection adds `--ignore=tests/compat2`). `-n auto` is bounded by CPU and free memory via `conftest.py` + `scripts/pytest_workers.py`; override with `PYTEST_WORKERS=N`. Details: `.agents/plans/2026-10-21-xdist-workers-and-split.md`.
- Worktrees go in `~/.worktrees/`. Every delegated agent or subprocess prompt must name its worktree path and branch.
- Delegation models: Haiku and Sonnet subagents only. Opus-level work (lowering, IR semantics, design docs) runs as a subprocess: `maki -m bifrost/codex-plan -p "<PROMPT>"`; fast Haiku/Sonnet-level tasks can also go to `maki -m bifrost/codex-execute -p` to save session limits (always maki for this project; fall back to agy first, then codex, crush, kimi or `claude -p --model claude-haiku-5-5`, only if the previous one fails). Have it write output to `/tmp/<task>.md` or run it in tmux. Full syntax in `~/HARNESSES.md`.
- Structured audit logging: `.agents/docs/pyscriptc-debug.md` — JSONL at `~/.cache/pyscriptc/log/`, `PYSC_DEBUG=1`, `pyscriptc logs`.
- No co-author trailers on commits. Atomic conventional commits.
- The scorer holdout is closed to generator agents (hook, deny rules, container mask, sparse worktrees). Never read it, ask for it or try to get around the guards; if a command is blocked for mentioning it, rephrase or stop. Details in `benchmarks/compat2/corpus/README.md`. Create agent worktrees with `.agents/bin/agent-checkout <name>`.

## Boxed fallback: cost and drop flag

The opt-in `--fallback=cpython` tier is isolated so it can be deleted. Boxed code is `src/pyscriptc/boxed.py`, `src/pyscriptc/runtime/ps_island*`, and any lines between `boxed:begin` and `boxed:end` marker comments in other compiler files (for example `# boxed:begin` ... `# boxed:end`). Every boxed-only branch outside those files must carry the markers, or the ledger undercounts it.

Measure at every epic boundary and in every iteration report:

```
uv run python benchmarks/compat2/boxed_cost.py --window-commits 10 [--reach reach.json] [--probe-times times.json]
```

It reports, per window, code share, churn share, bug share (`fix` commits) and iteration tax (commits that touch boxed code without naming boxed, island or fallback in the subject). Record the latest values and their trend here after each run.

Latest measurement (2026-10-21, after the E2 M2-M5 exception merges on PR #186): code 0.7%, churn 0.0%, bug 0.0%, tax 0.0%, drop_flag false, three windows all flat (previous 0.8% windows on 2026-10-18). Baseline at snapshot 046: `ps_island.c` is about 1.4% of compiler lines, all other metrics are zero.

Drop flag rules. Raise the flag in the iteration report when any holds:

- churn share above 20% or bug share above 25% in each of two consecutive windows
- iteration tax above 15% in the newest window
- boxed-only reach flat or falling for three windows while churn share plus bug share rose

When the flag is raised, stop adding boxed features and ask the user whether to drop the tier. Dropping means deleting `boxed.py`, the island runtime, the marked regions and the boxed matrix column. Thresholds are starting values and the user can tune them in `boxed_cost.py`.

## Release policy

Decision 2026-10-21 (`.agents/plans/2026-10-21-release-cadence-strategy.md`): the default is not releasing. PyPI versions are irreversible, so ceremony is the cost of shipping.

- Patch: at least 1 user-visible change (`feat`, `fix`, `perf` touching `src/`, `packaging/`, runtime C or user docs) or 3 merged PRs, plus 10 commits and 12 hours since the last tag. Refactor, test, ci and docs-only changes ride along under "Internal" and never trigger a release alone.
- Hotfix: bypasses the content and gap checks only, for a documented install or first-run break (label `hotfix`).
- Minor: one theme with written exit criteria in its milestone, 0 open issues, at least 2 user-visible features, a measured receipt (census delta, boxed ledger window, or perf number with a baseline), at least 5 days after the previous minor.
- Rehearsal: before any tag, the release build matrix, smoke and wheel verify must pass on the exact commit with no publish. Packaging changes also go through an `X.Y.ZrcN` tag first.
- Enforced by workflow: `release.yml` dispatch (`ref` input) is the rehearsal, `cut-release.yml` runs the bar (`.github/scripts/release_bar.py`, thresholds in its `env` block, `bypass_reason` to override) and the PyPI publish job refuses without a rehearsal of the release commit. See `docs/release-process.md`.
- The old "keep releasing versions" standing goal is retired: ship when the bar is met. Ceiling: no unreleased user-visible change older than 7 days.
