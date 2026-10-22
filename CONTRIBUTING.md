# Contributing

Setup, checks and the PR flow are in [site/guide/contributing.md](site/guide/contributing.md). Agent and maintainer notes are in [AGENTS.md](AGENTS.md) and [.agents/docs/](.agents/docs/README.md).

Quick start: `mise trust && mise install && uv sync`, then `uv run pytest -n 6 -m "not linux_container"`, `uv run ruff check` and `uv run ruff format --check`. Master is PR-only; use conventional commits and keep each PR to one logical change.
