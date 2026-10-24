# Compat site

Status: assembled, 2026-10-24. The compat page is part of the single pyscriptc.pages.dev site (landing at `/`, docs at `/docs`, compat at `/compat`). Owner decisions pending: Cloudflare secrets and switching the existing git-connected Pages project to direct upload.

The compat site is the public, always-current answer to "what does pyscriptc support right now?". It renders the same numbers the compiler produces, so it cannot drift from the docs the way a hand-written table can.

## Shape of the pipeline

| stage | producer | output | cadence |
| --- | --- | --- | --- |
| measure | `scripts/gen_support_table.py` | `docs/supported.md`, `docs/compat-top100.md` (generated) | on demand, checked by `tests/test_support_docs.py` |
| measure | `benchmarks/compat2.census` | `benchmarks/compat2/census-snapshot.json` | on demand |
| measure | `benchmarks.compat2.score` | `nightly/visible-score.json` (run artifact) | nightly cron, 06:17 UTC |
| measure | `benchmarks.compat2.census_self` | `benchmarks/compat2/census-self.json` | on demand, pinned by `tests/test_census_self.py` |
| aggregate | `scripts/build_compat_json.py` (new) | `compat/data/compat.json` | on demand and in the nightly job |
| aggregate | `scripts/build_compat_json.py --summary` | `compat/data/summary.json` | on demand and in the nightly job |
| publish | Cloudflare Pages project `pyscriptc` | `https://pyscriptc.pages.dev/compat` | on push that changes site, compat or docs paths |

One JSON file is the contract between measurement and presentation. The site never reads the compiler, the corpus or the scorer; it reads one file.

## Data model

`compat/data/compat.json` is the single file. It is deterministic: same inputs, same bytes. No timestamp that changes on every run unless it comes from a recorded measurement date.

### Top level

| key | type | meaning |
| --- | --- | --- |
| `schema` | string | schema version, currently `2`. A bump means the site must be updated in the same PR. |
| `generated` | string | UTC date of the measurement run (`YYYY-MM-DD`). |
| `commit` | string | git SHA of the tree that produced the file. Empty when built from a dirty tree. |
| `sources` | object | per-section provenance: which file or command produced it, and the date. |
| `summary` | object | headline counters, so the site renders a summary without walking every section. |
| `builtins` | array | builtin support rows. |
| `stdlib` | array | stdlib module support rows. |
| `packages` | array | pinned PyPI top-100 package rows. |
| `language` | array | language-feature rows from the compat2 scorer. |
| `fences` | array | fence-code counters. |
| `targets` | array | per-target status rows, from `compat/data/targets.json`. |
| `divergences` | array | the numbered divergences from `docs/reference/divergences.md`. |
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
| `probes` | int | how many compat2 probes cover this row |
| `pass_rate` | float | the share of those probes that pass, `0.0` to `1.0` |
| `probe_outcomes` | object | the outcome counts for those probes, same vocabulary as the `language` section |
| `probe_codes` | object | the fence codes those probes emitted, `{"PS2002": int}` |

The `fences` array is measured, not hand-written: `gen_support_table.py` already lowers every `ok` and `no` snippet to find the real codes.

The probe fields are measured too, by the compat2 scorer. A builtin row joins the probes whose id starts with `builtins.<name>`, a stdlib row joins the probes under `stdlib.<module>`, and a dotted module probe joins the first segment only, so `stdlib.urllib.parse` counts under `stdlib.urllib`. A row with no probe has `probes` at `0` and `pass_rate` at `0.0`.

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
| `probes` | int | how many compat2 probes cover this package |
| `pass_rate` | float | the share of those probes that pass |
| `probe_outcomes` | object | the outcome counts for those probes |
| `probe_codes` | object | the fence codes those probes emitted |

A package row joins the probes whose id starts with `top100.<project>`, where the project is the pinned project name (`python-dotenv`), not the import name (`dotenv`).

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

The `fences` rows also carry `why` and `fix`, taken from the `Diagnostic(code, title, why, fix)` registry in `src/pyscriptc/diagnostics.py`. The page shows them as the remediation hint next to every fence, so a reader learns what to do about a code, not just that it exists.

### Section: `targets`

One row per cross-compilation target. The source is `compat/data/targets.json`, a hand-maintained file, because the target matrix lives in the packaging scripts and the release workflow, not in something the compiler measures at run time.

| field | type | meaning |
| --- | --- | --- |
| `id` | string | stable slug, for example `linux-x86_64` |
| `triple` | string | the target triple or the Zig target name |
| `label` | string | display name |
| `status` | string | `native`, `experimental` or `planned` |
| `family` | string | the OS family, `linux`, `macos`, `windows` or `wasm` |
| `note` | string | one line on what the status means for this target |
| `proof` | string | where the claim comes from, a path in the tree |

Status vocabulary: `native` means the release binary ships for it, `experimental` means it compiles and smokes but is not released, `planned` means it is announced but not built.

### Section: `divergences`

One row per numbered divergence in `docs/reference/divergences.md`. The parser is `scripts/compat_divergences.py`; the file is the source of truth, so a divergence added there shows up on the site without touching the builder.

| field | type | meaning |
| --- | --- | --- |
| `number` | int | the item number in the document |
| `text` | string | the divergence text, markdown inline formatting kept |
| `note` | string | the trailing source note, when the item has one |
| `family` | string | `numeric`, `text`, `collections`, `objects`, `stdlib`, `island` or `target` |
| `sources` | array of string | the source links found in the item |

The family is a keyword classification, so a new divergence lands in the nearest family without anyone curating it. The page groups by family and lets the reader filter.

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
| `compat/data/compat.json` | the contract the site reads | yes |
| `compat/data/summary.json` | the headline numbers a landing page can consume | yes |
| `compat/data/targets.json` | the hand-maintained target matrix | yes |
| `compat/site/index.html` | the page, fetches `{{BASE}}data/compat.json` at runtime | yes |
| `nightly/visible-score.json` | raw scorer output, nightly run artifact | no, artifact only |
| `benchmarks/compat2/census-snapshot.json` | raw census, pinned in the tree | yes, already |

The JSON is committed because Cloudflare Pages builds from the repository, and because a committed snapshot means the site and the docs can never disagree without a diff showing it. The nightly commits the file like bolo does.

## What feeds the JSON

| section | source | command |
| --- | --- | --- |
| `builtins`, `stdlib` | `scripts/support_data.toml` probed through the compiler | `uv run python scripts/gen_support_table.py` |
| `packages` | `benchmarks/compat/top100/pins.json` plus `pyscriptc coverage` | `uv run python scripts/build_compat_json.py` |
| `language` | `benchmarks.compat2.score` over the visible probes | `uv run python -m benchmarks.compat2.score benchmarks/compat2/probes --corpus --out ...` |
| `fences` | the same package measurement, aggregated per code, with `why`/`fix` from the diagnostics registry | same builder command |
| `targets` | `compat/data/targets.json` | the builder reads and validates it |
| `divergences` | `docs/reference/divergences.md` | `scripts/compat_divergences.py`, called by the builder |
| `history` | the previous `compat.json` plus the current measurement | append in the builder |

The builder reuses `scripts/gen_support_table.py` internals (`evaluate`, `library_rows`) rather than re-implementing them, so there is one definition of "supported".

### `compat/data/summary.json`, the landing-page contract

The landing page needs a handful of headline numbers, not the whole file. `scripts/build_compat_json.py --summary compat/data/summary.json` writes a second, small JSON next to the main one.

| key | meaning |
| --- | --- |
| `schema` | `1`, versioned separately from the main file |
| `generated`, `commit` | the measurement run stamp |
| `url` | the canonical compat page URL |
| `packages_full`, `packages_partial`, `packages_none`, `packages_total` | the pinned-package split |
| `stdlib_modules`, `stdlib_full` | the stdlib module count and how many are 🟢 |
| `builtins_full`, `builtins_total` | the builtin 🟢 count and the row count |
| `fences` | total fences over the pinned packages |
| `divergences` | the numbered divergence count |
| `probes`, `probe_pass`, `probe_failures`, `pass_rate` | the scorer headline over the visible corpus |
| `weighted_pass` | the scorer weighted pass |
| `targets_native`, `targets_experimental` | the target split |

The landing page copy is owned elsewhere, so nothing on the landing page reads this file yet. The file is produced, committed and tested so the contract exists before someone wires it in. A `schema` bump there means the landing page and the builder change in the same PR.

Probe-derived keys (`probes`, `probe_pass`, `probe_failures`, `pass_rate`, `weighted_pass`) come from the scorer output, so they exist only in a `--score` run. The nightly is the producer of record: it always passes `--score`, so the committed file always carries real probe numbers.

A build without a score zeroes them instead of guessing, which means a plain `uv run python scripts/build_compat_json.py` would rewrite the committed file with empty probe data. Do not run it that way against the committed file. Use `--check` (which ignores the probe-derived fields when there is no score) or run it with `--score nightly/visible-score.json`.

## How nightly updates it

`.github/workflows/nightly.yml` gains one step in the `visible-score` job, after the scorer has written `nightly/visible-score.json`.

| step | what it does |
| --- | --- |
| build | `uv run python scripts/build_compat_json.py --out compat/data/compat.json --summary compat/data/summary.json --score nightly/visible-score.json` |
| commit | if either file changed, open (or update) a bot PR with just those two files via `gh`, and auto-merge if the ruleset allows |
| deploy | the merge to master triggers the site workflow build and deploy |

The nightly job needs `contents: write` and `pull-requests: write` for the bot PR step. That is a permissions change on an existing workflow, so the owner must approve it before the PR merges.

The PR branch follows the pattern `bot/compat-snapshot`, and the commit message the pattern `ci: nightly compat snapshot YYYY-MM-DD`, so the history of the file is a readable timeline. The builder writes both files atomically (write to a temp path, then rename), so an interrupted run never leaves a half-written JSON.

The PR never touches anything else. A nightly that finds the same numbers produces no PR and no deploy. When auto-merge is blocked, the PR stays open for the owner and the raw JSON is attached as a workflow artifact.

## How a data change triggers a rebuild and a deploy

Two paths reach Cloudflare Pages.

| path | trigger | what happens |
| --- | --- | --- |
| nightly | the nightly job opens a bot PR with `compat/data/compat.json` | the merge to master triggers the site workflow build and deploy |
| pull request | a PR changes `compat/**`, `site/**` or `docs/**` | the site workflow builds and validates, the owner reviews it before merge |

The deploy is a GitHub Actions job in `.github/workflows/site.yml` using `wrangler pages deploy` against the existing Pages project switched to direct upload. The project is `pyscriptc`, already serving `https://pyscriptc.pages.dev`; the workflow assembles the three parts (landing, docs, compat) into one `deploy/` tree and publishes it.

| setting | value |
| --- | --- |
| project name | `pyscriptc` |
| deployment mode | direct upload (`wrangler pages deploy`), git connection removed |
| secrets | `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` |
| assembly | `mise run site:build` produces `deploy/` (landing at root, docs at `/docs`, compat at `/compat`) |

`mise run compat:build` runs `compat/build.py`, which renders `compat/site/` into `dist/compat/` and copies `compat/data/compat.json` and `compat/data/summary.json` to `dist/compat/data/`. The page then fetches `{{BASE}}data/compat.json` with `{{BASE}}` baked at build time. `scripts/build_site.py` carries the summary file as optional, so a tree without it still builds the site; the nightly produces it.

`summary.json` is documented as a landing-page contract but nothing on the landing page reads it yet. The file exists, is committed, validated by tests and shipped in the deploy tree, so wiring it into the landing copy is a frontend-only change later.

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

The page leads with what does not work. The order is divergences, then the fence table, then targets, then language, then the support tables, then history. A reader who wants the bad news gets it in the first screen; the support tables are the reassurance, not the lead.

The page also carries:

| feature | how |
| --- | --- |
| deep links | every fence code, divergence number and target id is an anchor, so a URL points at one row and the row highlights when it is focused |
| a stale-data banner | the file carries `generated` and `commit`, and the page says so when the data is older than the deployment |
| remediation hints | the fence rows carry the `why` and `fix` text from the diagnostics registry |
| probe pass rates | each support row shows its measured pass rate, so a 🟡 row is not a mystery |
| design tokens | the page uses the landing page tokens from `site/landing/src/styles/global.css`, so the two look like one site |

The page lives in `compat/site/` (`index.html`, `compat.css`, `compat.js`) and the data lives in `compat/data/compat.json`. Neither directory is a docmd source: the docs site builds from `docs/` and the compat page is a plain static page, so the two never share a build root. The site workflow assembles all three parts into `deploy/` and publishes the whole tree as one deployment.

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
| drift | `uv run python scripts/build_compat_json.py --check` | the committed JSON is what the current tree produces |
| page | `uv run pytest -q tests/test_compat_site.py` | the HTML page reads every section the schema defines |
`--check` is the same shape as `gen_support_table.py --check` and `benchmarks.baseline.run --check`, so the repo keeps one convention.

## Slices, what shipped when

| item | detail | status |
| --- | --- | --- |
| builder | `scripts/build_compat_json.py`, deterministic, schema-validated, `--check` mode, `--out` path, `--summary` path | shipped |
| schema | `scripts/compat_schema.py`, the schema and the validator, no third-party dependency | shipped |
| tests | `tests/test_compat_json.py`, unit tests over the builder and the schema | shipped |
| page | `compat/site/` plus `compat/build.py`, one page with search, filters and the history chart | shipped |
| assembly | `scripts/build_site.py`, `mise run site:build`, `deploy/` with `_headers` and `_redirects` | shipped |
| nightly | bot PR via `gh` instead of direct master push | shipped |
| deploy | `.github/workflows/site.yml`, wrangler direct upload | in review |
| schema 2 | `targets`, `divergences`, per-row probe counters, fence `why`/`fix` | shipped |
| summary file | `compat/data/summary.json`, the landing-page contract, documented and tested, not wired into the landing copy | shipped |

The PR that carries the page and the assembly is the single reviewable unit; the deploy goes live when the owner adds the Cloudflare secrets and switches the Pages project to direct upload.

## Risks

| risk | mitigation |
| --- | --- |
| the nightly gains `contents: write` and `pull-requests: write` | the owner approves before merge, and the bot PR only ever carries the two data files |
| a nightly merges a change without review | the file is generated, the build is reproducible, and the PR diff shows the movement; a bad change is reverted with one commit |
| the JSON grows without bound | `history` is capped at 180 points, the rest is a fixed number of rows |
| the coverage pass takes minutes | it lowers every pinned package, the same work `benchmarks.compat2.census` does |
| the docs and the site disagree | both read the same builder output, and the docs table test fails when either drifts |
| the hand-maintained target matrix goes stale | each row carries a `proof` path, so a reader can check the claim, and the packaging scripts are the source to update |
| a divergence added to the doc misses the site | the builder parses the doc directly, so a new item appears on the next build with no extra step |
| the summary file drifts from the main file | both are written by the same builder run, and the tests compare them |
