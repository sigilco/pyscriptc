# Compat site

Status: assembled, 2026-10-22. The compat page is part of the single pyscriptc.pages.dev site (landing at `/`, docs at `/docs`, compat at `/compat`). Owner decisions pending: Cloudflare secrets and switching the existing git-connected Pages project to direct upload.

The compat site is the public, always-current answer to "what does pyscriptc support right now?". It renders the same numbers the compiler produces, so it cannot drift from the docs the way a hand-written table can.

## Shape of the pipeline

| stage | producer | output | cadence |
| --- | --- | --- | --- |
| measure | `scripts/gen_support_table.py` | `docs/supported.md`, `docs/compat-top100.md` (generated) | on demand, checked by `tests/test_support_docs.py` |
| measure | `benchmarks/compat2.census` | `benchmarks/compat2/census-snapshot.json` | on demand |
| measure | `benchmarks.compat2.score` | `nightly/visible-score.json` (run artifact) | nightly cron, 06:17 UTC |
| measure | `benchmarks.compat2.census_self` | `benchmarks/compat2/census-self.json` | on demand, pinned by `tests/test_census_self.py` |
| aggregate | `scripts/build_compat_json.py` (new) | `data/compat/compat.json` | on demand and in the nightly job |
| publish | Cloudflare Pages project `pyscriptc` | `https://pyscriptc.pages.dev/compat` | on push that changes site, compat or docs paths |

One JSON file is the contract between measurement and presentation. The site never reads the compiler, the corpus or the scorer; it reads one file.

## Data model

`data/compat/compat.json` is the single file. It is deterministic: same inputs, same bytes. No timestamp that changes on every run unless it comes from a recorded measurement date.

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
| `ladder` | object | the adoption ladder: R1 and R2 rung rows and the R4 lane shares. Absent rungs render as "not measured"; an absent `ladder` key means the page shows no ladder section. |

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

Fence-code counters over the pinned packages, from the same measurement that produces the `packages` section. One measurement, so the two sections can never disagree.

| field | type | meaning |
| --- | --- | --- |
| `code` | string | the `PSxxxx` code |
| `title` | string | the one-line title from `src/pyscriptc/diagnostics.py` |
| `count` | int | how many times the code appears in the measured corpus |
| `packages` | int | how many packages carry it |

This is the "census fence counts per code" table. It answers "what blocks the top-100" and it is the section that moves most as the compiler improves.

The pinned `benchmarks/compat2/census-snapshot.json` is the input to that table in the current tree. The builder measures the same trees directly, because the snapshot is dated and the table must be current. The snapshot stays the record of the ranked family set-cover (`benchmarks/compat2/corpus/README.md`).

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
| `data/compat/compat.json` | the contract the site reads | yes |
| `apps/web/src/pages/compat/index.astro` | the page, static build over `data/compat/compat.json` | yes |
| `nightly/visible-score.json` | raw scorer output, nightly run artifact | no, artifact only |
| `benchmarks/compat2/census-snapshot.json` | raw census, pinned in the tree | yes, already |

The JSON is committed because Cloudflare Pages builds from the repository, and because a committed snapshot means the site and the docs can never disagree without a diff showing it. The nightly commits the file like bolo does.

## What feeds the JSON

| section | source | command |
| --- | --- | --- |
| `builtins`, `stdlib` | `scripts/support_data.toml` probed through the compiler | `mise run support:table` (wraps `uv run python scripts/gen_support_table.py`) |
| `packages` | `benchmarks/compat/top100/pins.json` plus `pyscriptc coverage` | `mise run compat:json` (wraps `uv run python scripts/build_compat_json.py`) |
| `language` | `benchmarks.compat2.score` over the visible probes | `uv run python -m benchmarks.compat2.score benchmarks/compat2/probes --corpus --out ...` |
| `fences` | the same package measurement, aggregated per code | same builder command |
| `history` | the previous `compat.json` plus the current measurement | append in the builder |
| `ladder` | `benchmarks.scripts_corpus.ladder`, written to `data/compat/ladder.json` | `mise run compat:ladder` (wraps `uv run python -m benchmarks.scripts_corpus.ladder --json data/compat/ladder.json`) |

The builder reuses `scripts/gen_support_table.py` internals (`evaluate`, `library_rows`) rather than re-implementing them, so there is one definition of "supported".

## How nightly updates it

`.github/workflows/nightly.yml` gains one step in the `visible-score` job, after the scorer has written `nightly/visible-score.json`.

| step | what it does |
| --- | --- |
| build | `uv run python -m benchmarks.scripts_corpus.ladder --json data/compat/ladder.json` then `uv run python scripts/build_compat_json.py --out data/compat/compat.json --score nightly/visible-score.json --ladder data/compat/ladder.json` |
| commit | if the file changed, open (or update) a bot PR with just that change via `gh`, and auto-merge if the ruleset allows |
| deploy | the merge to master triggers the site workflow build and deploy |

The nightly job needs `contents: write` and `pull-requests: write` for the bot PR step. That is a permissions change on an existing workflow, so the owner must approve it before the PR merges.

The PR branch follows the pattern `bot/compat-snapshot`, and the commit message the pattern `ci: nightly compat snapshot YYYY-MM-DD`, so the history of the file is a readable timeline. The builder writes the file atomically (write to a temp path, then rename), so an interrupted run never leaves a half-written JSON.

The PR never touches anything else. A nightly that finds the same numbers produces no PR and no deploy. When auto-merge is blocked, the PR stays open for the owner and the raw JSON is attached as a workflow artifact.

## How a data change triggers a rebuild and a deploy

Two paths reach Cloudflare Pages.

| path | trigger | what happens |
| --- | --- | --- |
| nightly | the nightly job opens a bot PR with `data/compat/compat.json` | the merge to master triggers the site workflow build and deploy |
| pull request | a PR changes `compat/**`, `site/**` or `docs/**` | the site workflow builds and validates, the owner reviews it before merge |

The deploy is a GitHub Actions job in `.github/workflows/site.yml` using `wrangler pages deploy` against the existing Pages project switched to direct upload. The project is `pyscriptc`, already serving `https://pyscriptc.pages.dev`; the workflow assembles the three parts (landing, docs, compat) into one `deploy/` tree and publishes it.

| setting | value |
| --- | --- |
| project name | `pyscriptc` |
| deployment mode | direct upload (`wrangler pages deploy`), git connection removed |
| secrets | `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` |
| assembly | `mise run site:build` produces `deploy/` (landing at root, docs at `/docs`, compat at `/compat`) |

The compat page is part of the `apps/web` Astro app (`apps/web/src/pages/compat/`). The build bakes `data/compat/compat.json` into the page as static HTML (no runtime fetch); `apps/web/scripts/sync-data.mts` copies the JSON into `apps/web/public/compat/data/` so the raw files ship next to the page.

Two things are the owner's to do, because they need the Cloudflare account: add the `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` secrets, and switch the existing `pyscriptc` Pages project from git connection to direct upload. The docs site links to the compat page through the `PYSC_COMPAT_URL` placeholder, default `https://pyscriptc.pages.dev/compat`, so the URL can change without a docs edit.

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

The page lives in `apps/web/src/pages/compat/` (one Astro app with the landing page, sharing the landing navbar) and the data lives in `data/compat/compat.json`. The docs site builds from `docs/` with docmd; the site workflow assembles all three parts into `apps/web/deploy/` and publishes the whole tree as one deployment.

## CI validation link from the docs

The docs link to the site in two places.

| place | link | text |
| --- | --- | --- |
| `docs/` support model page | `{{PYSC_COMPAT_URL}}` | "the compat site has the live numbers" |
| `docs/guide/support-model.md` | `{{PYSC_COMPAT_URL}}` | same |

The placeholder is defined the same way as the other template variables the docs already use, so one substitution covers both. A test asserts the docs link to the placeholder and not a hard-coded host, so a stale URL cannot land.

CI validation, in this order.

| check | command | what it proves |
| --- | --- | --- |
| schema | `uv run pytest -q tests/test_compat_json.py` | the committed JSON matches the schema and the builders agree with it |
| drift | `mise run compat:json --check` | the committed JSON is what the current tree produces |
| page | `uv run pytest -q tests/test_compat_site.py` | the HTML page reads every section the schema defines |

`--check` is the same shape as `gen_support_table.py --check` and `benchmarks.baseline.run --check`, so the repo keeps one convention.

## Slices, what shipped when

| item | detail | status |
| --- | --- | --- |
| builder | `scripts/build_compat_json.py`, deterministic, schema-validated, `--check` mode, `--out` path | shipped |
| schema | `scripts/compat_schema.py`, the schema and the validator, no third-party dependency | shipped |
| tests | `tests/test_compat_json.py`, unit tests over the builder and the schema | shipped |
| page | `apps/web/src/pages/compat/` in the Astro app, static build over data/compat/compat.json | shipped |
| assembly | `apps/web/scripts/assemble.mts`, `mise run site:build`, `apps/web/deploy/` with `_headers` and `_redirects` | shipped |
| nightly | bot PR via `gh` instead of direct master push | shipped |
| deploy | `.github/workflows/site.yml`, wrangler direct upload | in review |

The PR that carries the page and the assembly is the single reviewable unit; the deploy goes live when the owner adds the Cloudflare secrets and switches the Pages project to direct upload.

## Risks

| risk | mitigation |
| --- | --- |
| the nightly gains `contents: write` and `pull-requests: write` | the owner approves before merge, and the bot PR only ever carries the one file |
| a nightly merges a change without review | the file is generated, the build is reproducible, and the PR diff shows the movement; a bad change is reverted with one commit |
| the JSON grows without bound | `history` is capped at 180 points, the rest is a fixed number of rows |
| the coverage pass takes minutes | it lowers every pinned package, the same work `benchmarks.compat2.census` does |
| the docs and the site disagree | both read the same builder output, and the docs table test fails when either drifts |
