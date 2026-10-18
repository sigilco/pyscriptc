# pyscriptc agent notes

- GitHub Project: none yet (decision 2026-10-14: track phases locally in `.agents/plans/` until the user shares a URL).
- Active plan: `.agents/plans/2026-10-14-waves-after-scorer-v2.md` (supersedes phases 7 to 11 of `2026-10-14-scorer-v2-and-strategy.md`, which stays as history). Handoff: `.agents/notes/2026-10-14-wave-a-handoff.md`.
- v1 scorer (`benchmarks/compat/`) is frozen history. v2 lives in `benchmarks/compat2/`; shared contract is `benchmarks/compat2/outcomes.py`.
- Frozen v2 gates (tag `gates-v2`, 2026-10-16). Changes need a recorded decision first: `outcomes.py`, `score.py` weights, `coverage_map.py`, `cache.py`, `calibrate.py`, `CALIBRATION.json`, `TRIAGE.md`, `probes/*.jsonl`, `probes/IR_HASHES.json`, `canonical.py` rules (`docs/ir-canonicity.md`). Regenerating `IR_HASHES.json` requires the A/B byte-identical discipline (`python -m benchmarks.compat2.irscore` twice, `cmp`).
- Sweep evidence for the 3.12 corpus: #22 (triage zero-deltas, verdict replays), #23 (calibration cells >= 26, headroom 0 capped), #24 (generation byte-identical). Boxed ledger at the boundary (2026-10-16, window `06ed55b`): code 1.39%, churn 0%, bugs 0%, tax 0%, drop_flag false.
- Package manager: `uv`. Checks: `uv run pytest`, `uv run ruff check`, `uv run ruff format --check`, `uv run python -m benchmarks.baseline.run --check`.
- Compiler debug mode: `pyscriptc compile FILE --debug[=stages,fences,cc,paths,all]` (env `PYSC_DEBUG`) prints a stage/fence/cc table to stderr; `--debug-dir DIR` dumps `ir.json`, `out.ll`, `cc.sh`, `summary.json`. Details in `.agents/docs/debug.md`.
- Worktrees go in `~/.worktrees/`.
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

Latest measurement (2026-10-18, after wave-3 merges #94-#100): code 1.0%, churn 0.0%, bug 0.0%, tax 0.0%, drop_flag false, three windows all flat (previous windows 1.1%/1.3%). Baseline at snapshot 046: `ps_island.c` is about 1.4% of compiler lines, all other metrics are zero.

Drop flag rules. Raise the flag in the iteration report when any holds:

- churn share above 20% or bug share above 25% in each of two consecutive windows
- iteration tax above 15% in the newest window
- boxed-only reach flat or falling for three windows while churn share plus bug share rose

When the flag is raised, stop adding boxed features and ask the user whether to drop the tier. Dropping means deleting `boxed.py`, the island runtime, the marked regions and the boxed matrix column. Thresholds are starting values and the user can tune them in `boxed_cost.py`.
