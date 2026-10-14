# pyscriptc agent notes

- GitHub Project: none yet (decision 2026-10-14: track phases locally in `.agents/plans/` until the user shares a URL).
- Active plan: `.agents/plans/2026-10-14-scorer-v2-and-strategy.md`.
- v1 scorer (`benchmarks/compat/`) is frozen history. v2 lives in `benchmarks/compat2/`; shared contract is `benchmarks/compat2/outcomes.py`.
- Package manager: `uv`. Checks: `uv run pytest`, `uv run ruff check`, `uv run ruff format --check`, `uv run python -m benchmarks.baseline.run --check`.
- Worktrees go in `~/.worktrees/`.
- No co-author trailers on commits. Atomic conventional commits.
- The scorer holdout is closed to generator agents (hook, deny rules, container mask, sparse worktrees). Never read it, ask for it or try to get around the guards; if a command is blocked for mentioning it, rephrase or stop. Details in `benchmarks/compat2/corpus/README.md`. Create agent worktrees with `.agents/bin/agent-checkout <name>`.

## Boxed fallback: cost and drop flag

The opt-in `--fallback=cpython` tier is isolated so it can be deleted. Boxed code is `src/pyscriptc/boxed.py`, `src/pyscriptc/runtime/ps_island*`, and any lines between `boxed:begin` and `boxed:end` marker comments in other compiler files (for example `# boxed:begin` ... `# boxed:end`). Every boxed-only branch outside those files must carry the markers, or the ledger undercounts it.

Measure at every epic boundary and in every iteration report:

```
uv run python benchmarks/compat2/boxed_cost.py --window-commits 10 [--reach reach.json] [--probe-times times.json]
```

It reports, per window, code share, churn share, bug share (`fix` commits) and iteration tax (commits that touch boxed code without naming boxed, island or fallback in the subject). Record the latest values and their trend here after each run.

Latest measurement: none yet. Baseline at snapshot 046: `ps_island.c` is about 1.4% of compiler lines, all other metrics are zero.

Drop flag rules. Raise the flag in the iteration report when any holds:

- churn share above 20% or bug share above 25% in each of two consecutive windows
- iteration tax above 15% in the newest window
- boxed-only reach flat or falling for three windows while churn share plus bug share rose

When the flag is raised, stop adding boxed features and ask the user whether to drop the tier. Dropping means deleting `boxed.py`, the island runtime, the marked regions and the boxed matrix column. Thresholds are starting values and the user can tune them in `boxed_cost.py`.
