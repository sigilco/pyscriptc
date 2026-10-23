# Contributing

Setup, checks and the PR flow are in [docs/project/contributing.md](docs/project/contributing.md). Agent and maintainer notes are in [AGENTS.md](AGENTS.md) and [.agents/docs/](.agents/docs/README.md).

Quick start: `mise trust && mise install`, then `mise run setup` (uv sync + pnpm install). The default task runner is `mise`: `mise run check` (ruff + compat2 unit tests), `mise run test` (full suite), `mise run docs:build` and `mise run docs:validate` (the documentation site). Master is PR-only; use conventional commits and keep each PR to one logical change.
