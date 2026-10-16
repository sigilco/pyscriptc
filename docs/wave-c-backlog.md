# Wave C stdlib family backlog ranking

Date: 2026-10-16. Base: master `9c60a8f`. Sources cited per number; method at the bottom.

## Headline

`itertools` does **not** rank first once weights x coverage are applied. On the
committed data it lands 12th of 18 families (score 20.6 vs the leader's 262.3).
The waves plan named it on the census rank (11) and the jmespath unblocking
argument, both recorded before the weighted backlog existed. It is still the
correct first *consumer-facing* epic if the ranking key is top100 packages
unblocked, but it is not the weighted-backlog leader.

## Ranked table

Score = weighted backlog rows (non-pass, weight > 0) x market coverage proxy.
Census rank and packages touched are the plan's tiebreak signals. Rows are
sorted by score; the "E2/E3/E5/E6" columns count backlog rows gated on
not-yet-lowered features, plus weight-0 rows parked behind the feature
(`raises` at 0 until E2, `mention` at 0 until a receiver factory exists).

| Rank | Family | Weighted backlog | Coverage | Score | Census rank (fences) | Pkgs touched | E2 parked (raises) | E2 rows (exc) | E3 rows (class) | E5 rows (method) | Mentions parked |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `os`/`os.path` | 267.0 | 0.98 | 262.3 | 22 (15) | 5 | 91 | 1 | 12 | 33 | 36 |
| 2 | `enum` | 184.3 | 0.82 | 151.6 | 44 (5) | 1 | 19 | 0 | 15 | 160 | 60 |
| 3 | `decimal` (FFI) | 146.8 | 1.00 | 146.8 | - | 0 | 7 | 14 | 3 | 123 | 0 |
| 4 | `collections` | 143.3 | 1.00 | 143.3 | 26 (3) | 3 | 15 | 0 | 8 | 133 | 0 |
| 5 | `argparse` | 68.6 | 1.00 | 68.6 | - | 0 | 19 | 2 | 10 | 55 | 0 |
| 6 | `datetime` (FFI) | 52.4 | 1.00 | 52.3 | 38 (3) | 2 | 9 | 0 | 2 | 48 | 4 |
| 7 | `operator` | 43.0 | 1.00 | 42.8 | 24 (9) | 5 | 5 | 0 | 3 | 0 | 0 |
| 8 | `re` | 42.0 | 0.98 | 41.0 | - | 0 | 3 | 1 | 4 | 24 | 17 |
| 9 | `math` | 41.0 | 0.98 | 40.2 | 48 (3) | 1 | 0 | 0 | 0 | 0 | 0 |
| 10 | `string`/`textwrap` | 24.9 | 0.98 | 24.3 | - | 0 | 0 | 0 | 3 | 14 | 0 |
| 11 | `csv` | 24.5 | 0.99 | 24.2 | - | 0 | 5 | 1 | 7 | 5 | 0 |
| 12 | `itertools` | 21.0 | 0.98 | 20.6 | 11 (4) | 3 | 0 | 0 | 19 | 1 | 0 |
| 13 | `functools` | 20.5 | 0.96 | 19.7 | 37 (2) | 1 | 3 | 0 | 6 | 3 | 0 |
| 14 | `json` (floats/null) | 15.2 | 1.00 | 15.2 | - | 0 | 2 | 1 | 2 | 5 | 0 |
| 15 | `dataclasses` | 12.3 | 0.91 | 11.2 | 47 (3) | 1 | 5 | 1 | 2 | 0 | 0 |
| 16 | `zlib` (FFI) | 10.2 | 1.00 | 10.2 | - | 0 | 1 | 1 | 0 | 0 | 0 |
| 17 | `fractions` | 8.4 | 0.97 | 8.1 | - | 0 | 0 | 0 | 1 | 6 | 0 |
| 18 | `copy` | 4.0 | 1.00 | 4.0 | - | 0 | 1 | 1 | 0 | 0 | 0 |

## Top 3 by weight x coverage

1. **`os`/`os.path`** — largest backlog by far (267.0 weighted, 211 of it in
   `function` rows that need no class machinery). 91 parked `raises` rows
   weight 0 today flip to weight 1.0 the moment E2 lands, so its real ceiling
   is ~358. Scripts need `os.path` for entry work; the census already shows 15
   package fences under `stdlib:os`. It is big enough to be two epics
   (`os.path` pure functions first, `os` IO/process surface second).
2. **`enum`** — 184.3 weighted backlog, but 160 of it is `method` rows that
   presuppose E3/E5, and 60 `mention` rows sit at weight 0 behind receiver
   factories. High payoff, but genuinely a Wave B follower, not a cheap Wave C
   starter.
3. **`decimal`** — 146.8 weighted backlog concentrated in 123 `method` rows,
   and the plan already defers it behind FFI (mpdecimal). Top-3 by the raw
   metric, but an FFI epic, not an E0-adjacent one.

## Reading the table

Two different questions produce two different "first batch":

- **Raw weighted payoff** (the metric as written): `os`/`os.path`, `enum`,
  `decimal`. But `enum`/`decimal`/`collections`/`argparse`/`datetime` backlogs
  are dominated by `method` rows that cannot move until E3/E5 land, so ranking
  by weight alone overstates their near-term value.
- **Cheap first batch after E0/E1** (few E-gated rows, decent weight):
  `math` (41 backlog rows, all `function`, zero E-deps), `operator` (43 rows,
  40 `function`), `re` (78 rows but 40 are `constant` folds at 0.1),
  `string`/`textwrap`, `csv`, `json`, `fractions`, `zlib`, `copy`. `math` and
  `operator` are the obvious openers: 84 combined weighted rows with almost no
  E2/E3/E5/E6 dependency.

`itertools` sits between the two readings: only 21.0 weighted rows, all of
them `class` rows for iterator types whose real payoff requires E6
(`iter`/`next`, generators). Its case stays the packages, not the ledger
weight: census shows 3 packages fenced on it (jmespath among them per the
plan), each a 5.0-weight program row once unblocked.

## Dependency-blocked list (wait for E2/E3/E5/E6)

| Family | Blocker | Evidence |
| --- | --- | --- |
| `itertools` | E3 + E6 | 19/21 backlog rows are iterator classes; the feature itself is iteration protocol |
| `collections` | E3 + E5 | 133/146 backlog rows are `method`; `deque`/`OrderedDict`/`Counter` are classes |
| `enum` | E3 + E5 | 160 method rows, 60 mention rows needing receiver factories |
| `decimal` | E5 + FFI | 123 method rows; plan defers to mpdecimal FFI |
| `datetime` | E5 + FFI | 48 method rows; FFI epic |
| `argparse` | E5 | 55 method rows on `ArgumentParser` |
| `dataclasses` | E3 + E5 | decorator/class machinery plus generated methods |
| `re` (methods) | E5 | 24 method rows on match/pattern objects |
| `os` raises tail | E2 | 91 parked `raises` rows (weight 0 until E2) |
| `functools` partial | E5 | 3 method rows, class-backed decorators |
| `fractions`, `csv`, `json`, `string` | E5 partial | small method-row minorities |

Effectively unblocked now (post-E0/E1): `math`, `operator`, `re` constants,
`zlib`, `copy`, `csv` functions, `json` module/function rows.

## Method and data sources

- Probe rows: `benchmarks/compat2/probes/{modules,surface,builtins,language}.jsonl`
  (13,692 probes). Weights read from each row's `weight` field (computed per
  `benchmarks/compat2/outcomes.py`: category weights, weak folds 0.25,
  raises/mention 0).
- Pass/fence status: `benchmarks/compat2/probes/CALIBRATION.json` per-id
  outcomes (15,518 rows; oracle 3.14.8). 2 backlog rows had no calibration
  entry and are counted as backlog.
- Census: `benchmarks/compat2/census-snapshot.json` (rank, fences, packagesTouched).
- Coverage proxy below.

## Deviations

- **`benchmarks/compat2/market/2026-10-14.json` does not exist** anywhere in
  the repo or its branch history; it is an A1 deliverable that has not landed.
  Substituted proxy: per-probe `versions` field (typeshed-declared version
  range), averaged as `len(versions)/5` over a family's backlog rows. This
  measures API availability across 3.10-3.14, not market share of the
  interpreter versions; it is monotone in the same direction but flatter
  (range 0.82-1.00). Rerun the score column when the real snapshot lands.
- Calibration outcomes were generated under the 3.14.8 oracle on a 15,518-row
  corpus; the pinned 3.12 sweep (`SWEEP-calibration.md`) reports 14,925 rows
  with pass 2,328 vs 2,660 here. Backlog counts may be a few percent high;
  ordering is stable since no family flips pass status wholesale.
- E-dependency mapping: `exception`/`raises` rows to E2, `class` rows to E3,
  `method` rows to E5 (receiver construction + callable lowering), iteration
  surface to E6. Method rows are counted at family level, not per-row AST
  analysis; `re`'s 1 `for ... in` row is incidental.
- Branch name: the brief names `work/family-backlog`; the worktree tool minted
  `work/c24-family-backlog`. The PR is opened from `work/family-backlog`
  pointing at the same commit.
