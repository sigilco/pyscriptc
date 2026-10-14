# pyscriptc agent notes

- GitHub Project: none yet (decision 2026-10-14: track phases locally in `.agents/plans/` until the user shares a URL).
- Active plan: `.agents/plans/2026-10-14-scorer-v2-and-strategy.md`.
- v1 scorer (`benchmarks/compat/`) is frozen history. v2 lives in `benchmarks/compat2/`; shared contract is `benchmarks/compat2/outcomes.py`.
- Package manager: `uv`. Checks: `uv run pytest`, `uv run ruff check`, `uv run ruff format --check`, `uv run python -m benchmarks.baseline.run --check`.
- Worktrees go in `~/.worktrees/`.
- No co-author trailers on commits. Atomic conventional commits.
