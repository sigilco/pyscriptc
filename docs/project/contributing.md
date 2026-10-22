---
title: Contributing
description: How to set up the repo and land a change.
---

## Setup

```bash
git clone https://github.com/sigilco/pyscriptc.git
cd pyscriptc
mise trust && mise install   # python, uv, node, pnpm per mise.toml
mise run setup                # uv sync + pnpm install (the node deps live in docs/.docmd)
```

Tools already on your PATH that satisfy the `mise.toml` pins are reused, not reinstalled. You also need `clang` for linking compiled binaries.

## Tasks

`mise.toml` is the task runner for the mixed python and node toolchain. Every task is one command:

| Task | What it does |
| --- | --- |
| `mise run setup` | First-time install: `uv sync` and `pnpm install` in `docs/.docmd/` (the node workspace root) |
| `mise run docs:dev` | Public docs dev server over `docs/` |
| `mise run docs:build` | Build the public site to `dist/docs` |
| `mise run docs:validate` | Check every internal link in `docs/` |
| `mise run docs:internal` | Dev server for the internal notes in `.agents/docs/` (no build step) |
| `mise run test` | The python test suite, `linux_container` excluded |
| `mise run lint` | `ruff check` |
| `mise run format-check` | `ruff format --check`, with the same excludes as CI |
| `mise run check` | `lint` + `format-check` + the `tests/compat2` unit selection |

The same commands are available without mise (`uv run pytest`, `pnpm run docs:build`, `uv run python scripts/check_doc_links.py`), but mise pins the toolchain versions.

## The documentation site

The public site is built from `docs/` with [docmd](https://docmd.io). The docmd configuration lives at `docs/.docmd/` (with the placeholders plugin), and the internal developer notes in `.agents/docs/` are served by a second config, `docs/.docmd/docmd.internal.config.js`.

```bash
mise run docs:dev        # preview the public site
mise run docs:build      # build to dist/docs
mise run docs:validate   # every internal link must resolve
mise run docs:internal   # preview the internal notes
```

Two details worth knowing before you edit a page:

- The output directory is `dist/docs`, outside the source tree. Cloudflare Pages must publish `dist/docs`, not `docs/dist`.
- Pages may use the `{{PYSC_COMPAT_URL}}` and `{{PYSC_DOCS_URL}}` placeholders. They are expanded at build time from the environment variable of the same name, falling back to the default in `docs/.docmd/plugins/placeholders.js`.

## Checks before a PR

```bash
mise run check
mise run test
uv run python -m benchmarks.baseline.run --check
```

## Conventions

- Conventional commits (`feat(lower): ...`, `fix(llvm): ...`), no co-author trailers.
- The compat2 scorer files listed in `AGENTS.md` are frozen; fence messages are never reworded.
- Boxed (CPython island) code stays behind `boxed:begin`/`boxed:end` markers.
- Agent-facing notes and the debug/logging directives live in `AGENTS.md` and `.agents/docs/`.

## Reporting bugs

Attach `pyscriptc logs` output or a `--debug-dir` dump, the `PSxxxx` fence codes you hit, and the smallest source file that reproduces.
