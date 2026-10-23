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

`mise` is the default task runner. Every command in this document is `mise run <task>` first, with the raw command it wraps shown once below the table. The task names and what they do:

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
| `mise run bench:baseline` | The perf regression gate against `benchmarks/baseline/reference*.json` |
| `mise run compat:json` | Rebuild `compat/data/compat.json` (append `--check` to compare) |
| `mise run compat:ladder` | Rebuild `compat/data/ladder.json` |
| `mise run compat:census` | Compat2 census over the visible corpus (append `--top N` to limit) |
| `mise run support:table` | Regenerate the supported-API tables under `.agents/docs/support` (append `--check` to compare) |
| `mise run boxed:cost` | The boxed ledger window, per `AGENTS.md` |
| `mise run ir:hashes` | Canonical IR hash compare against the committed baseline (linux oracle; see the note in `mise.toml` for the 3.14 micro stamp and macOS behavior) |
| `mise run compile` | `pyscriptc compile`, extra args pass through: `mise run compile examples/hello.py --emit bin` |
| `mise run run` | `pyscriptc run` (content-cached compile and execute), args pass through |
| `mise run wheel:smoke` | Build the wheel, install it in a fresh venv, compile and run hello |
| `mise run release:rehearse` | Dispatch `release.yml` on the master tip (the rehearsal run, about 20 minutes) |

Extra args after the task name are appended to the wrapped command, so `mise run compat:json --check` is `uv run python scripts/build_compat_json.py --check`.

The same raw commands are available without mise (`uv run pytest`, `pnpm --dir docs/.docmd exec docmd build --config docmd.config.js`, `uv run python scripts/check_doc_links.py`), but mise pins the toolchain versions and is what CI's own scripts assume locally. CI itself runs the raw commands, not `mise run`, so the workflows keep their own pinned uv, node and pnpm setup.

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
mise run bench:baseline
```

`mise run check` wraps `uv run ruff check`, `uv run ruff format --check` and `uv run pytest -q tests/compat2 -m "not linux_container"`, and `mise run bench:baseline` wraps `uv run python -m benchmarks.baseline.run --check`. The raw commands work too.

## Conventions

- Conventional commits (`feat(lower): ...`, `fix(llvm): ...`), no co-author trailers.
- The compat2 scorer files listed in `AGENTS.md` are frozen; fence messages are never reworded.
- Boxed (CPython island) code stays behind `boxed:begin`/`boxed:end` markers.
- Agent-facing notes and the debug/logging directives live in `AGENTS.md` and `.agents/docs/`.

## Reporting bugs

Attach `pyscriptc logs` output or a `--debug-dir` dump, the `PSxxxx` fence codes you hit, and the smallest source file that reproduces.
