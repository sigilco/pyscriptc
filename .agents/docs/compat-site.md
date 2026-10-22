# Compat site

Status: design, 2026-10-22. Owner decisions pending (Cloudflare project, deploy secret, docmd versus plain page).

The compat site is the public, always-current answer to "what does pyscriptc support right now?". It renders the same numbers the compiler produces, so it cannot drift from the docs the way a hand-written table can.

## Shape of the pipeline

| stage | producer | output | cadence |
| --- | --- | --- | --- |
| measure | `scripts/gen_support_table.py` | `docs/supported.md`, `docs/compat-top100.md` (generated) | on demand, checked by `tests/test_support_docs.py` |
| measure | `benchmarks/compat2.census` | `benchmarks/compat2/census-snapshot.json` | on demand |
| measure | `benchmarks.compat2.score` | `nightly/visible-score.json` (run artifact) | nightly cron, 06:17 UTC |
| measure | `benchmarks.compat2.census_self` | `benchmarks/compat2/census-self.json` | on demand, pinned by `tests/test_census_self.py` |
| aggregate | `scripts/build_compat_json.py` (new) | `site/compat-data/compat.json` | on demand and in the nightly job |
| publish | Cloudflare Pages project `pyscriptc-compat` | `https://pyscriptc-compat.pages.dev` | on push that changes `site/compat/**` |

One JSON file is the contract between measurement and presentation. The site never reads the compiler, the corpus or the scorer; it reads one file.

## Data model

`site/compat-data/compat.json` is the single file. It is deterministic: same inputs, same bytes. No timestamp that changes on every run unless it comes from a recorded measurement date.

### Top level

| key | type | meaning |
| --- | --- | --- |
| `schema` | string | schema version, currently `1`. A bump means the site must be updated in the same PR. |
| `generated` | string | UTC date of the measurement run (`YYYY-MM-DD`). |
| `commit` | string | git SHA of the tree that produced the file. Empty when built from a dirty tree. |
| `sources` | object | per-section provenance: which file or command produced it, and the date. |
| `summary` | object | headline counters, so the site renders a summary without walking every section. |
| `builtins` | array | builtin support rows. |
| `stdlib` | array | stdlib module support rows. |
| `packages` | array | pinned PyPI top-100 package rows. |
| `language` | array | language-feature rows from the compat2 scorer. |
| `fences` | array | fence-code counters. |
| `history` | array | dated history points, one per measurement date. |

### Status vocabulary

| status | emoji | rule |
| --- | --- | --- |
| `full` | 🟢 | every probed form lowers with no fence |
| `partial` | 🟡 | some probed forms lower, the rest fence |
| `none` | 🔴 | nothing probed lowers |
| `unknown` | — | not measured yet |

### Section: `builtins` and `stdlib`

One row per entry in `scripts/support_data.toml`.

| field | type | meaning |
| --- | --- | --- |
| `id` | string | stable slug, `builtin.abs` or `stdlib.math` |
| `kind` | string | `builtin` or `stdlib` |
| `label` | string | display name, the comma-joined names |
| `names` | array of string | the probed names |
| `status` | string | status vocabulary |
| `count` | int | how many names the row covers |
| `unsupported` | array of string | the "not supported" labels, hand-written in `support_data.toml` |
| `fences` | array of string | the `PSxxxx` codes the compiler really emits for those forms |
| `note` | string | optional free-text note from `support_data.toml` |

The `fences` array is measured, not hand-written: `gen_support_table.py` already lowers every `ok` and `no` snippet to find the real codes.

### Section: `packages`

One row per pinned package in `benchmarks/compat/top100/pins.json`, measured by `pyscriptc coverage` over `benchmarks/compat/top100/src`.

| field | type | meaning |
| --- | --- | --- |
| `name` | string | project name, for example `python-dotenv` |
| `import_name` | string | the importable name, for example `dotenv` |
| `version` | string | pinned version |
| `status` | string | status vocabulary |
| `fences` | int | total fences in the package |
| `definitions` | int | definitions found |
| `fenced_definitions` | int | definitions carrying at least one fence |
| `top_families` | array | up to 3 `{"family": string, "count": int}` root families, biggest first |
| `codes` | array | up to 3 `{"code": string, "count": int}` fence codes, biggest first |
| `pypi` | string | link to the project page |

Status rule, same as `docs/compat-top100.md` today: no fences is 🟢, at most half the definitions fenced is 🟡, more than half is 🔴.

### Section: `language`

One row per compat2 probe area group, aggregated from the scorer output.

| field | type | meaning |
| --- | --- | --- |
| `id` | string | probe id prefix, for example `lang.` or `builtins.` |
| `label` | string | display name |
| `probes` | int | probes in the group |
| `outcomes` | object | `{"pass": int, "fence": int, "wrong-output": int, "crash": int, "timeout": int, "build-error": int, "excluded": int}` |
| `weighted_pass` | float | weighted pass from the scorer for this group |
| `fence_codes` | object | `{"PS2002": int}` for the fences in this group |

### Section: `fences`

Fence-code counters for the whole compiler, taken from the census.

| field | type | meaning |
| --- | --- | --- |
| `code` | string | the `PSxxxx` code |
| `title` | string | the one-line title from `src/pyscriptc/diagnostics.py` |
| `count` | int | how many times the code appears in the measured corpus |
| `packages` | int | how many packages carry it |

This is the "census fence counts per code" table. It answers "what blocks the top-100" and it is the section that moves most as the compiler improves.

### Section: `history`

| field | type | meaning |
| --- | --- | --- |
| `date` | string | measurement date |
| `commit` | string | git SHA measured |
| `weighted_pass` | float | compat2 weighted pass over the visible corpus |
| `packages_full` | int | packages at 🟢 |
| `packages_partial` | int | packages at 🟡 |
| `packages_none` | int | packages at 🔴 |
| `fences` | int | total fences over the pinned packages |

One point per measurement date. The site renders it as a line chart per metric. The file keeps the last 180 points, so the file stays small and the chart stays readable.

## Where the JSON lives

| path | role | committed |
| --- | --- | --- |
| `site/compat-data/compat.json` | the contract the site reads | yes |
| `site/compat/index.html` | the page, fetches `compat.json` at runtime | yes |
| `nightly/visible-score.json` | raw scorer output, nightly run artifact | no, artifact only |
| `benchmarks/compat2/census-snapshot.json` | raw census, pinned in the tree | yes, already |

The JSON is committed because Cloudflare Pages builds from the repository, and because a committed snapshot means the site and the docs can never disagree without a diff showing it. The nightly commits the file like bolo does.

## What feeds the JSON

| section | source | command |
| --- | --- | --- |
| `builtins`, `stdlib` | `scripts/support_data.toml` probed through the compiler | `uv run python scripts/gen_support_table.py` |
| `packages` | `benchmarks/compat/top100/pins.json` plus `pyscriptc coverage` | `uv run python -m benchmarks.compat2.census --json ...` |
| `language` | `benchmarks.compat2.score` over the visible probes | `uv run python -m benchmarks.compat2.score benchmarks/compat2/probes --corpus --out ...` |
| `fences` | `benchmarks/compat2/census-snapshot.json` plus `src/pyscriptc/diagnostics.py` | same census command |
| `history` | the previous `compat.json` plus the current measurement | append in the builder |

The builder reuses `scripts/gen_support_table.py` internals (`evaluate`, `library_rows`) rather than re-implementing them, so there is one definition of "supported".

## How nightly updates it

`.github/workflows/nightly.yml` gains one step in the `visible-score` job, after the scorer has written `nightly/visible-score.json`.

| step | what it does |
| --- | --- |
| build | `uv run python scripts/build_compat_json.py --out site/compat-data/compat.json --score nightly/visible-score.json` |
| commit | if the file changed, `git commit` and `git push` with the bot identity |
| deploy | push to master triggers the Pages build |

The nightly job needs `contents: write` for the commit step. That is a permissions change on an existing workflow, so the owner must approve it before the PR merges.

The commit message follows the pattern `ci: nightly compat snapshot YYYY-MM-DD`, so the history of the file is a readable timeline. The builder writes the file atomically (write to a temp path, then rename), so an interrupted run never leaves a half-written JSON.

The commit never touches anything else. A nightly that finds the same numbers produces no commit and no deploy.

## How a data change triggers a rebuild and a deploy

Two paths reach Cloudflare Pages.

| path | trigger | what happens |
| --- | --- | --- |
| nightly | the nightly job commits `site/compat-data/compat.json` to master | the Pages build runs on the new commit |
| pull request | a PR changes `site/compat/**` or `site/compat-data/**` | the Pages preview build runs, the owner reviews it before merge |

The deploy itself is a Cloudflare Pages project build, not a GitHub Actions deploy job. That is the cheapest option and it matches the existing `pyscriptc.pages.dev` setup: the repository already has `package.json` with `docs:build` and `docmd.config.js`. The Pages project needs its build command and output directory set once, by the owner.

| Pages setting | value |
| --- | --- |
| project name | `pyscriptc-compat` |
| production branch | `main` (the repository default branch) |
| build command | `pnpm install && pnpm compat:build` |
| output directory | `dist/compat` |
| root directory | repository root |

`pnpm compat:build` is a two-line script: copy `site/compat/` to `dist/compat/` and copy `site/compat-data/` to `dist/compat/data/`. The page then fetches `data/compat.json`.

Two things are the owner's to do, because they need the Cloudflare account: create the `pyscriptc-compat` Pages project with the settings above, and decide whether the domain is `pyscriptc-compat.pages.dev` or a custom one. The docs site links to it through the `PYSC_COMPAT_URL` placeholder, default `https://pyscriptc-compat.pages.dev`, so the URL can change without a docs edit.

## Static site generator choice

Two options were considered.

| option | what it needs | cost | verdict |
| --- | --- | --- | --- |
| reuse docmd | a second docmd config for the compat section, or a second Pages project with the same build | the site has to render markdown at build time, so the JSON has to be inlined into markdown at build time, which means a build step per data change | rejected for the compat site |
| plain static page plus JS | one HTML file, one CSS file, one JS file, no build | the page fetches `compat.json` and renders the tables in the browser | chosen |

The reason to reject docmd for this page is the shape of the data. docmd renders markdown at build time. The compat tables are data, not prose: one row per builtin, one per stdlib module, one per pinned package, one per language feature group, plus a history chart. Writing that as markdown means generating markdown from JSON at build time, so the site has a generator step and a build failure mode that a plain fetch does not have.

A plain page also keeps the compat site independent of the docs restructure. The docs site is being reorganised right now (`site/` moves under `docs/`), and the compat site should not be a second thing that has to move with it.

The plain page is not a toy. It needs:

| need | how |
| --- | --- |
| tables | render with plain DOM, no framework, no bundler |
| 🟢🟡🔴 | CSS classes, with the emoji as a text fallback for screen readers |
| sorting and filtering | one sort key and one free-text filter over the package and builtin tables |
| the history chart | inline SVG polyline computed from `history` |
| dark mode | `prefers-color-scheme`, no toggle |
| no build step | `pnpm install` is not even needed, the page is plain files |
| a link check | `tests/test_compat_json.py` validates the JSON against the schema, and a separate test asserts the page references every section key |

The page lives in `site/compat/` (`index.html`, `compat.css`, `compat.js`) and the data lives next to it in `site/compat-data/compat.json`. The data path is not inside `site/compat/` because docmd also builds from `site/`, and a nested build root inside a build root is a mistake the next docs change will trip over. The Pages project copies `site/compat/` as the root and `site/compat-data/` as `data/` beside it.

## CI validation link from the docs

The docs link to the site in two places.

| place | link | text |
| --- | --- | --- |
| `docs/` support model page | `{{PYSC_COMPAT_URL}}` | "the compat site has the live numbers" |
| `site/guide/language.md` | `{{PYSC_COMPAT_URL}}` | same |

The placeholder is defined the same way as the other template variables the docs already use, so one substitution covers both. A test asserts the docs link to the placeholder and not a hard-coded host, so a stale URL cannot land.

CI validation, in this order.

| check | command | what it proves |
| --- | --- | --- |
| schema | `uv run pytest -q tests/test_compat_json.py` | the committed JSON matches the schema and the builders agree with it |
| drift | `uv run python scripts/build_compat_json.py --check` | the committed JSON is what the current tree produces |
| page | `uv run pytest -q tests/test_compat_site.py` | the HTML page reads every section the schema defines |

`--check` is the same shape as `gen_support_table.py --check` and `benchmarks.baseline.run --check`, so the repo keeps one convention.

## Slice 1, what ships first

| item | detail |
| --- | --- |
| builder | `scripts/build_compat_json.py`, deterministic, schema-validated, `--check` mode, `--out` path |
| schema | `scripts/compat_schema.py`, the schema and the validator, no third-party dependency |
| tests | `tests/test_compat_json.py`, unit tests over the builder and the schema |
| nightly | one step in `.github/workflows/nightly.yml` that builds the JSON and commits it |
| docs | `.agents/docs/compat-site.md` (this file) |
| not in slice 1 | the page itself, the Pages project, the deploy, the docs links |

Slice 1 does not publish anything. The JSON lands in the repository and the nightly commits it. The site and the deploy are slice 2, after the owner creates the Pages project.

## Risks

| risk | mitigation |
| --- | --- |
| the nightly gains `contents: write` | the owner approves before merge, and the job only ever pushes one file |
| a nightly pushes to master and bypasses review | the file is generated, the build is reproducible, and the diff shows the movement; a bad push is reverted with one commit |
| the JSON grows without bound | `history` is capped at 180 points, the rest is a fixed number of rows |
| the census takes minutes | the census is the slow part, it already runs in the nightly budget |
| the docs and the site disagree | both read the same builder output, and the docs table test fails when either drifts |
