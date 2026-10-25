# llms.txt: docmd plugin versus the custom generator

Assessment for #592, pinned version `@docmd/core` 0.9.7 (`docs/.docmd/package.json` allows ^0.9.5, the lockfile resolves 0.9.7).

docmd ships `@docmd/plugin-llms`, bundled with core and enabled by default; no config key is needed. It runs as a post-build hook and writes `llms.txt`, `llms-full.txt` and a `llms.json` manifest into the output directory. The links use `config.url`, which we already set to `https://pyscriptc.pages.dev/docs`. `llms.txt` lists title and description per page, `llms-full.txt` embeds each page's raw markdown source. The only options are `i18n` (per-locale files) and the top-level `title` and `description`, which fill the header.

It honors the page frontmatter `title`, `description`, `noindex: true` and `llms: false`. It does not follow our `navigation` config: pages are sorted alphabetically by output path. It escapes brackets and backticks in titles. Raw bodies are copied unescaped, so placeholders such as `{{PYSC_DOCS_URL}}` appear unsubstituted in `llms-full.txt` (the same as the previous custom output).

It cannot include non-page files. The plugin only iterates the pages docmd rendered, has no extra-files option, and no hook ordering lets a local plugin edit its output reliably. Our `compat/data/*.json` files and their schema notes therefore need an addition.

Coverage: docmd's output has all 24 built pages. The one source file it omitted, `docs/guides/island-floor.md`, was an unreferenced byte-identical duplicate of `docs/guide/island-floor.md` that the site never served, so the custom script was linking a dead URL. The duplicate is deleted.

Decision: switch the page listing to docmd and reduce `scripts/gen_llms_txt.py` to a post-build step that appends the "Public Data Files" section (description, schema, link) to docmd's `llms.txt` and `llms-full.txt`. It is idempotent and fails clearly if the docmd build has not run. `scripts/build_site.py` already runs it after the docmd build. `tests/test_llms_txt.py` now checks that every page under `docs/` and every `compat/data/*.json` appears in the final files (page checks skip when `dist/docs` is not built).

Left on the table: nav-ordered listing (docmd sorts by path) and the `llms.json` manifest does not list data files.
