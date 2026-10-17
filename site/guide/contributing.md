---
title: Contributing
description: How to set up the repo and land a change.
---

## Setup

```bash
git clone https://github.com/sigilco/pyscriptc.git
cd pyscriptc
mise trust && mise install   # python, uv, node, pnpm per mise.toml
uv sync
```

Tools already on your PATH that satisfy the `mise.toml` pins are reused, not reinstalled. You also need `clang` for linking compiled binaries, and `pnpm install` only when working on this site (`docs/` output).

Checks before a PR:

```bash
uv run pytest
uv run ruff check
uv run ruff format --check
uv run python -m benchmarks.baseline.run --check
```

## Conventions

- Conventional commits (`feat(lower): ...`, `fix(llvm): ...`), no co-author trailers.
- The compat2 scorer files listed in `AGENTS.md` are frozen; fence messages are never reworded.
- Boxed (CPython island) code stays behind `boxed:begin`/`boxed:end` markers.
- Agent-facing notes and the debug/logging directives live in `AGENTS.md` and `.agents/docs/`.

## Reporting bugs

Attach `pyscriptc logs` output or a `--debug-dir` dump, the `PSxxxx` fence codes you hit, and the smallest source file that reproduces.
