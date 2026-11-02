# Progress audit: docs and the compat site vs the measured state (2026-11-02)

Scope: `README.md`, `docs/`, `data/compat/`, `apps/web/`, `scripts/build_compat_json.py`,
`scripts/compat_schema.py`, `tests/test_compat_json.py`.
Branch: `work/progress` (base `origin/work/story`, PR #799).
Host: CPython 3.14.9 venv (`uv venv --python 3.14.9`).

## Verdict

| claim the outside reader made | verdict |
| --- | --- |
| "PyPI has an old version while master is newer" | **Stale, and now false.** PyPI's newest release is `0.7.0` (uploaded 2026-10-27), and `pyproject.toml` on master also says `0.7.0`. The   gap was real on 2026-10-22 (`0.4.1`) and was closed by the `0.5.0`/`0.5.1`/`0.6.0`/`0.6.1`/`0.7.0` releases. Master is 77 commits ahead of the `v0.7.0` tag, but of those only two touch the compiler (`16c3bcf4` subprocess `text=True`/`encoding=`, `545da33f` island call in an f-string placeholder); the rest are docs, site, CI, release plumbing and the data regeneration. The doc gap that remains is not "PyPI is old" but "master carries two compiler fixes the released wheel does not", which is true and must be stated as such. |
| "library support is a native-only verdict of none/partial/full per package" | **Was correct about the site; the site is fixed now.** The package status was computed only from the fence ratio over the package's own definitions. It never asked whether the package can run on the island tier. A package with a high fence ratio was shown as `none`, which reads as "not usable". Schema 2 adds the island verdict and the plane split, and the page renders both axes. |
| "the listed packages are not really usable" | **False, now measured rather than argued.** The island probe compiles one consumer program per pinned package with the island fallback and diffs the binary's output against CPython: all 18 pinned packages build and run with matching output. Under the old native-only verdict 9 of 18 read as `none`. The receipts are committed in `data/compat/island.json` and the nightly refreshes them. |
| "apps with untyped third-party dependencies simply fail" | **False as stated, and true in a narrower sense that the docs do not say.** An untyped dependency does not fail the build by itself: the island tier routes it. What fails is the *construct* fences inside the app's own code (measured on one anonymized Flask-plus-LLM backend: 1285 fences over 548 of 934 definitions, of which PS1001 missing annotations and PS1104 `Any` are the fixable half). `docs/guide/libraries.md` states the routing; nothing states the cost. |

## (a) The compat site

### Shape

- Generator: `scripts/build_compat_json.py` -> `data/compat/compat.json` (schema `1`, validated by
  `scripts/compat_schema.py`).
- Page: `apps/web/src/pages/compat/index.astro` over
  `apps/web/src/components/compat/{HeatGrid,SupportTable,PackageTable}.astro`, types in
  `apps/web/src/lib/compat.ts`, headline math in `apps/web/src/lib/stats.ts`.
- The site is static: every number is baked at build time. It cannot claim more than the JSON says.
- The build is `pnpm run build` in `apps/web` (`prebuild` runs `scripts/sync-data.mts`, which copies
  `data/compat/*.json` into `apps/web/public/compat/data/`).

### What it shows today for a package

`data/compat/compat.json` -> `packages[]`: name, import_name, pinned version, `status`,
`fences`, `definitions`, `fenced_definitions`, `top_families`, `codes`, `pypi`.

`status` is computed **only** from the fence ratio over the package's own definitions:

```
no fences                              -> full
fenced_definitions * 2 <= definitions -> partial
otherwise                             -> none
```

That rule is duplicated in `tests/test_compat_json.py::test_package_status_follows_the_fence_ratio`,
so it is the contract, not an accident.

### What it does not show

1. **No island dimension.** The vocabulary is `full` / `partial` / `none` and the three are
   presented as a single native-only verdict. The island tier is invisible on the site. A reader
   sees `none` for a package and concludes "unusable".
2. **No run evidence.** There is no link to a nightly run, a probe, a benchmark result or a
   receipt. The only provenance is `sources` in the JSON (`scripts/support_data.toml`,
   `benchmarks/compat/top100/pins.json`, `pyscriptc coverage`, the score path) and the
   `generated`/`commit` stamp in the hero. The site therefore shows *how a package measures* but
   not *whether it runs*.
3. **No plane share per package.** The plane map (native vs island per definition) is computed by
   the compiler (`pyscriptc.coverage.capabilities` returns per-file `native`/`fenced`/`island`
   buckets; `pyscriptc.assess` prints the per-definition plane map with `demotedBy`), but the
   builder throws that away: only `fences`/`definitions` reach the JSON.
4. **The lane share exists but only globally.** `data/compat/ladder.json` has the R4 lane shares
   (`native` 82.7%, `extern` 17.0%, `island` 0) over the locked dependency corpus, and the page
   renders them in the ladder strip. It is the corpus-wide number, not per package, and the
   `island` lane reads `0` because the pinned corpus has no `[tool.pyscriptc.imports]` island
   policy lines. That zero is read as "the island tier does nothing".
5. **The package table header says "PyPI top 100"** but `pins.json` pins **18** packages, and the
   section note says "the pinned packages of the PyPI top-100 corpus". The heading overstates the
   corpus.
6. **`language` is empty** in the committed JSON (`score` is `""`, because the scorer output is a
   nightly artifact), and `weighted_pass` is `0.0`. The page's own summary math counts
   `full`/`partial` as "supported", which is fine, but the headline reads "N of M probed builtins
   and stdlib modules compile (full or partial)" with no mention that a `partial` row means
   "some probed forms fence".
7. **No install path and no version provenance per row.** The page links to the PyPI project page
   per package, but says nothing about which pyscriptc channel the measurement came from, so a
   reader cannot tell whether the numbers describe the released wheel or master.

### Frozen gates (must not be touched)

`benchmarks/compat2/outcomes.py`, `benchmarks/compat2/score.py` (weights),
`benchmarks/compat2/coverage_map.py`, `benchmarks/compat2/cache.py`,
`benchmarks/compat2/calibrate.py`, `benchmarks/compat2/CALIBRATION.json`, `benchmarks/compat2/TRIAGE.md`,
`benchmarks/compat2/probes/*.jsonl`, `benchmarks/compat2/probes/IR_HASHES.json`, and the
`canonical.py` rules.

**The changes below need none of them.** The new fields are computed by
`scripts/build_compat_json.py` from what `pyscriptc.coverage.build_report` already returns, and the
schema gains optional keys. `scripts/compat_schema.py` is not a frozen gate (it is the data
contract the task names as in scope), but it is bumped with the data in the same PR.

## (b) The docs

### Stale or misleading statements

| place | statement | problem |
| --- | --- | --- |
| `README.md` | "Live per-module and per-library support status: the compat site." | true, but the site shows a native-only verdict, so the link propagates the wrong impression. Needs the channel note. |
| `README.md` | "Using PyPI packages: We keep track of the share of the top 100 PyPI libraries that compiles." | The corpus is 18 pinned packages, not the top 100. The heading overstates. |
| `README.md` | "Mind that the compiler requires typed code." | Reads as "untyped dependencies are unusable". The island tier routes them; the cost is unstated. |
| `README.md` | Install section: `uv tool install pyscriptc`, plus "Prebuilt binaries ... on the releases page". | No statement that the released wheel is `0.7.0` and that master is ahead of it. A reader cannot tell which channel the docs describe. |
| `docs/index.md` | "What it does" bullet list: type-first, native output, gradual adoption, clear debugging. | This is exactly the feature list the documentation policy (AGENTS.md, decision 2026-10-23) forbids in `docs/`. |
| `docs/index.md` | "Native output: ... The result has no interpreter inside." | Not true of an island build, and the page never says so. The landing page had the same claim dropped in `c7cd6eef`, the docs page kept it. |
| `docs/getting-started.md` | "If your app is framework-heavy ... does not compile to native code and, once an untyped import sits at a module top level, pulls that module's functions onto the embedded CPython island with it." | The direction is right, but "does not compile to native code" is stated without the price: the whole module top level rides the island, and calls across the boundary pay a marshalling cost. `docs/guide/libraries.md` carries the cost; the entry point does not. |
| `docs/getting-started.md` | Install section: `uv tool install pyscriptc`, "Prebuilt binaries ... on the releases page". | No channel statement (released version vs master). |
| `docs/guide/libraries.md` | "An untyped dependency is never impossible to use: it runs in fallback mode." | True and important, and it is the sentence the outside reader needed. But it is the only place, and it is contradicted in effect by the compat site's `none` verdicts. Needs the native/island split in one place. |
| `docs/guide/libraries.md` | "Consider a script that mixes a typed function with an untyped one" ... "Note that `helper` runs there too" | The example is correct. |
| `docs/guide/libraries.md` | "On one measured Flask-plus-LLM backend this took the run-time native share from 41% (definitions passing in isolation) to 0%". | Correct and measured, but it names no third-party application. Good. The same app is measured in `.agents/notes/2026-11-02-app-assessment.md`. The doc sentence and the note must stay consistent. |
| `docs/guide/python-subset.md` | "Per-construct, per-builtin, per-module and per-library support status is tracked on the compat site ... This page states the rules and the known gaps only." | Correct policy, correct pointer. The site then has to be honest. |
| `docs/guide/comparison.md` | The whole page | It names other projects (Nuitka, Cython, mypyc, Codon, Numba, scriptc). The task forbids naming third-party applications, repos or authors in **anything I write**; the page already exists on the branch and is not mine to rewrite. My additions must not add names. |
| `docs/reference/divergences.md` | 42, 48 | Both already state the island propagation and the demotion cost. No change needed except where support state changed. |
| `docs/reference/divergences.md` preamble | "stdout: byte-exact for every compiled call. The corpus scorer today compares whitespace-stripped stdout; tightening that to unstripped bytes is planned work." | "planned work" is a planning statement in user docs. The policy says planning lives in GitHub milestones. Low-risk to reword. |

### Feature lists the policy forbids

`docs/index.md` "What it does" (four bullets). `docs/index.md` "Try it now" is an example, fine.
`docs/getting-started.md` has no such list. `README.md` is not under `docs/`, but its
"What it does not do yet" section is already gap-shaped, which is the right shape.

### Where the native/island split should live

One place: `docs/guide/libraries.md` already owns the island mechanics. The split itself
(native = compiled to machine code, no interpreter; island = runs on an embedded CPython in the
same process, with a marshalling cost per crossing) belongs there, and `docs/getting-started.md`
should point at it rather than restate it. `docs/index.md` should link, not list.

## (c) The install story

- **PyPI**: newest release `0.7.0`, uploaded 2026-10-27. `pyproject.toml` on master: `0.7.0`.
  The releases in between: `0.5.0`, `0.5.1`, `0.6.0`, `0.6.1`, `0.7.0`.
- **Master**: 77 commits ahead of the `v0.7.0` tag. Two of those touch the compiler
  (`16c3bcf4` subprocess `text=True`/`encoding=` in native run, `545da33f` island call inside an
  f-string placeholder); the rest are docs, site, CI, release plumbing and the regenerated
  `compat.json`.
- **The install commands work**: `uv tool install pyscriptc` resolves `0.7.0`;
  `docs/install.sh` and `docs/install.ps1` are on master and referenced from `README.md` and
  `docs/getting-started.md`. The install story is sound; what is missing is a statement of
  *which* channel the docs describe.
- **No beta page.** `b6dc2991` dropped the beta testing page. There is no beta page to fix.
  `pre_kind=beta` in the release policy is a future channel, not a documented one.

## What this branch changes

### Docs

1. `docs/index.md`: replace the feature list with a short statement of what the tool does plus
   links; state the native/island split once, by link; drop "The result has no interpreter inside"
   as an unqualified claim.
2. `docs/getting-started.md`: state which channel the docs describe (released version vs master)
   in the install section; keep the framework-heavy section but name the price (module top level
   rides the island, crossings pay a marshalling cost) and link the split.
3. `docs/guide/libraries.md`: add one explicit "native vs island" subsection: what each plane is,
   what a crossing costs, and how to see which one a program uses (`assess --json`,
   `run --report`). Link the compat site for support state.
4. `docs/guide/python-subset.md`: no change beyond what the split link needs.
5. `docs/reference/divergences.md`: reword the "planned work" phrase; add or update the gap entry
   for the native/island cost where a support change touches it.
6. `README.md`: fix the "top 100" claim to the pinned corpus; state the released-vs-master channel
   note; point the library section at the split.
7. No release numbers and no "planned for" statements in `docs/`.

### Compat site

1. New per-package fields in `data/compat/compat.json`:
   - `native_status`: the existing `full`/`partial`/`none` verdict, kept, and renamed in the UI to
     "native".
   - `island`: whether the package's tree runs on the island tier, and the evidence link.
   - `plane`: the per-package native/island definition split from
     `pyscriptc.coverage.capabilities` (`native`, `fenced`, `island` buckets), so the plane share
     is per package and not only corpus-wide.
   - `evidence`: a link to the artifact that proves the claim (the nightly run, a probe, a
     benchmark receipt).
2. A new top-level `install` section: the released version, the measured channel, and the
   install command. The page then says which channel it measures.
3. `last_updated`: derived from `generated` plus the commit, already rendered in the hero; make it
   explicit and link the commit.
4. The site must not claim a package "runs" without a receipt. Only packages with a receipt get the
   island verdict; the rest say "not measured".
5. `scripts/build_compat_json.py` and `scripts/compat_schema.py` change together with the data.
6. `tests/test_compat_json.py` gains the contract tests for the new fields.

### Frozen gates

Untouched. No change to `outcomes.py`, `score.py`, `coverage_map.py`, `probes/`, `CALIBRATION.json`
or `canonical.py`.

### Owner decisions this needs

1. **The site vocabulary.** The task asks for "native", "island (runs, with marshalling cost)"
   and "does not build". The data has two independent axes (does it lower, does it run). The
   proposal: keep `status` as the native verdict and add a separate `island` verdict, and render
   both columns, rather than collapsing them into one status. Collapsing would lose the
   distinction between "fences in the package's own code" and "runs on the island".
2. **The package corpus size.** The section is headed "PyPI top 100" and pins 18 packages. Either
   the corpus grows to 100 or the heading changes to "the pinned corpus". The heading change is
   in this branch; growing the corpus is a separate, expensive change.
3. **Whether the island evidence may be published per package.** Running a package on the island
   needs the package installed on the host with its dependencies. The 18 pinned trees are pure
   Python sdists, so the island run is reproducible in CI; the receipt would be a nightly job.
   This branch publishes the plane split and the evidence link; the nightly job that produces the
   receipts is the follow-up.

## Measurement: every pinned package runs on the island tier

The audit's third owner decision was answered by measuring it rather than by asking. The island
probe (`benchmarks/compat/top100/island_probe.py`) compiles one consumer program per pinned
package with `--fallback=cpython --embed-python=host --emit bin`, runs the emitted binary, runs
the same program under CPython, and requires the two to agree on stdout and exit code.

**All 18 pinned packages build and run on the island tier, with matching output.**

That is the single most important finding of this pass, and it inverts the reading of the old
site. Under the old native-only verdict, 9 of the 18 packages read as `none` ("not usable") and 7
as `partial`. Under the measured island verdict, all 18 run. The fence census was telling the
truth about the native plane and the site was reading it as a verdict about the package.

| package | native verdict | island verdict | plane split (native / island / blocked) |
| --- | --- | --- | --- |
| certifi | full | runs | 5 / 0 / 0 |
| colorama | none | runs | 26 / 0 / 51 |
| python-dotenv | none | runs | 29 / 0 / 46 |
| filelock | partial | runs | 382 / 4 / 255 |
| h11 | none | runs | 50 / 17 / 66 |
| idna | none | runs | 18 / 0 / 38 |
| iniconfig | partial | runs | 19 / 0 / 15 |
| jmespath | none | runs | 89 / 1 / 110 |
| pyjwt | partial | runs | 99 / 0 / 72 |
| mdurl | none | runs | 4 / 0 / 8 |
| packaging | partial | runs | 274 / 0 / 284 |
| pathspec | none | runs | 30 / 2 / 100 |
| platformdirs | partial | runs | 226 / 0 / 162 |
| pytz | partial | runs | 70 / 0 / 32 |
| shellingham | none | runs | 6 / 0 / 19 |
| trove-classifiers | partial | runs | 0 / 0 / 0 |
| tzdata | full | runs | 0 / 0 / 0 |
| zipp | none | runs | 16 / 0 / 59 |

Corpus-wide plane share over the definitions the measurement placed: 50% native, 1% island,
49% blocked. The island share is small because the plane split counts *definitions*, and the
island tier takes over a *module's top level* when an import fence sits there, not definition by
definition. That is why a package can be `none` natively and still run: the whole tree rides the
island.

Two measurement notes, both about the probe and not about the compiler:

- The consumer program needs the `if __name__ == "__main__":` guard. Without it the compiler
  emits `PS0001: module has no functions to emit`, which reads as a package failure and is not
  one.
- The probe needs the host `libpython` visible. A virtual environment inherits `sysconfig` from
  the base install, so `LIBDIR` can name a directory that does not exist; the probe resolves the
  directory that actually holds the shared library and passes it on as `LIBRARY_PATH` and
  `LD_LIBRARY_PATH`.

### What this changes in the plan

- The island verdict is **receipt-driven**, not inferred. The first implementation inferred it
  from the plane split and was wrong: it called `certifi` blocked when the tree has five
  definitions, zero fences and a full native verdict. Only a run says whether a package runs.
  The schema now has three island values (`runs`, `blocked`, `unmeasured`), and a package with no
  receipt reads `unmeasured`.
- The plane split counts definitions **per occurrence**, not per name. `coverage.capabilities`
  keys its buckets by definition name, so a module that defines the same name twice (certifi
  defines `where` and `contents` twice) collapses into one bucket and the counts stop adding up.
  The builder counts per occurrence now, and a test asserts the three planes always add up.
- The nightly runs the probe before rebuilding the compat file, so the published verdicts stay
  current. A probe failure keeps the previous receipts rather than blocking the snapshot.

### Still open for the owner

- **The corpus.** 18 pinned packages, all measured, all running. The heading now says "pinned
  packages". Growing the corpus is a separate piece of work.
- **The native verdict's meaning.** The old `status` vocabulary is unchanged and still means
  "the fence ratio over the package's own definitions". The site now labels it "native" and
  shows the island verdict beside it. Whether `partial` should be reworded ("some definitions
  fence") is a wording call.
- **The island cost is not on the site.** The site says a package runs on the island. It does not
  say what that costs at run time (interpreter speed plus a marshalling cost per native-island
  crossing). The measurement for that is a benchmark, not a verdict, and it is not in this pass.

