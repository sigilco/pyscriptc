# Real script support: the grind to first-class scripts before apps

Date: 2026-10-24. Branch: `docs/real-scripts-strategy`. Research plus planning
only, no src changes. Tickets: #500 to #511, on the board with Horizon and
Effort set. Measured on today's master (`5860541`).

## Strategy

The owner's direction: like Vercel's scriptc, do not chase the whole of PyPI.
Build first-class compat on the language's builtin and stdlib surface, plus a
short blessed list of packages later. For pyscriptc the surface is the one a
single-file or small multi-file CLI script reaches for: argparse, pathlib,
json, csv, subprocess, os, sys, re, datetime, logging, shutil, urllib,
dataclasses, typing, itertools, functools, collections.

The strategy answers one question: what does a beta tester's script hit today,
and what order do we grind the walls down?

## Measurement: the R0 corpus

`benchmarks/real_scripts/` is a new corpus: 39 third-party real-world scripts,
vendored byte-identical from public repositories at pinned commits, with the
upstream licences in `licenses/` and the provenance in `manifest.json`. The
sources: CPython `Tools/scripts` (PSF-2.0), psf/black, pypa/pipx,
jazzband/pip-tools (BSD-3), geekcomputers/Python, Logan1x/Python-Scripts and
avinashkranjan/Amazing-Python-Scripts (MIT). Every script passes
`ast.parse` on CPython 3.14. No edits: a fence the corpus finds is a real gap,
not a fixture bug.

The existing `benchmarks/scripts_corpus/` is the R1 rung: original scripts
written to the lowered shape. R0 is below it: scripts that bring their own
shape. The ladder gains R0 at the bottom.

### The number today

`uv run python -m benchmarks.real_scripts.measure`:

- 0 of 39 scripts lower with zero fences. 386 fences total.
- 0 compile without `--skip-check` being tested yet; the checker is a second
  wall behind the first (see P4).

### Where each script stops (first fence in line order)

| first fence | count | what it is |
| --- | --- | --- |
| `PS1001` needs an annotation (param, local or return) | 23 | the name was assigned without an annotation; the assignment never landed |
| `PS2003` call shape (keyword args, arity, argument type) | 6 | `hashlib.sha256()` takes more than 1 arg, `input()` takes a prompt, `print(list)` |
| `PS2002` import table | 6 | `platform`, `calendar`, `zipfile`, `sqlite3` are not in the closed table |
| `PS2002` module-assign | 2 | module-level assignment that is not a constant |
| `PS2001` stdlib name not lowered | 6 | `json.load`, `time.perf_counter`, `itertools.product`, `dataclasses.asdict`, `random.Random`, `subprocess.Popen` |
| `PS1104`/`PS1106` annotation kinds | 2 | `Any` as a value type, `random.Random` as a type |
| `PS2001` field-lookup | 1 | class attribute assignment (`Scheduling.jobs = ...`) |

### The unlock ladder (counterfactual, upper bound)

Assume a root cause lands completely, nothing new surfaces underneath. The
counterfactual releases are from `measure.py`:

| root cause lands | released scripts | out of |
| --- | --- | --- |
| inference (PS1001 local/param/return from the bound value) | 3 | 39 |
| + module scope (foldable assignments, module statements) | 6 | 39 |
| + call table (keywords, arity rows over stdlib) | 23 | 39 |
| + stdlib surface (os.walk, json.load, time.perf_counter, ...) | 29 | 39 |
| + operators (true division, modulo, container algebra) | 29 | 39 |
| + import table rows (platform, calendar, zipfile, tarfile) | 34 | 39 |
| + classes (field-lookup, class-body) | 37 | 39 |
| + checker alignment | 39 | 39 |

Read: inference alone releases almost nothing, because every script that gets
past inference immediately hits the call table. Inference plus the call table
releases 23 of 39. The two are one phase in practice, but they are separable
work and the call table cannot be measured until inference stops drowning the
signal.

### Demand vs gap, per stdlib module

How many scripts import the module, and how many carry a fence naming it:

| module | importing | fenced | the API that fences |
| --- | --- | --- | --- |
| os | 23 | 3 | `os.walk`, `os.system` |
| sys | 13 | 0 | (none: sys is first class) |
| argparse | 12 | 0 | (fences arrive via keyword-args and name-resolution instead) |
| time | 11 | 3 | `time.perf_counter`, `time.process_time` |
| shutil | 6 | 0 | (blocked by keyword-args: `shutil.copy(..., )` shapes) |
| subprocess | 6 | 1 | `subprocess.Popen` |
| typing | 6 | 0 | annotation-only, imports free |
| random | 5 | 1 | `random.sample` |
| datetime | 4 | 0 | (blocked by `strftime` in an inference cascade) |
| json | 4 | 2 | `json.load` |
| pathlib | 4 | 0 | (blocked by inference cascades) |
| collections | 3 | 0 | |
| hashlib | 3 | 0 | (blocked by call-arity: `hashlib.sha256()` with 0 args) |
| re | 3 | 0 | |
| itertools | 2 | 1 | `itertools.product` |
| calendar, platform, zipfile, sqlite3 | 1 each | 1 each | the import itself fences |

Read: the modules are mostly in the table. The gaps are per-function inside
the modules, and the biggest module in the table (`os`) is fenced on exactly
two functions, `walk` and `system`.

## The first-class script bar

What a beta tester must be able to expect. Written up as #507, to land as
`docs/reference/first-class-scripts.md`.

1. Compiles: `pyscriptc compile script.py` exits 0, no `--skip-check`, no
   flags, on a script the tester already wrote.
2. Matches CPython: the binary's stdout, stderr and exit code match CPython
   3.14 on the same invocation, recorded and compared, not eyeballed.
3. Diagnostics name the gap: a fence names construct and module
   (`PS2001: os.walk is not in the lowered subset of os`), `pyscriptc explain`
   says what to do, and the cascade does not bury the root fence (see #511).
4. Install story is one command: pip install, then compile; no CPython at run
   time for a native binary.
5. Perf is not a surprise: startup at or near CPython for the script shape,
   measured claims only (#380, #383, #384, #385 are the known regressions).

The claim is measured, not asserted: the bar names the corpus, the corpus
size and the measured rate. Per the docs policy (decision 2026-10-23), user
docs state divergences and gaps; the "what works" claim lives on the compat
site, which measures it. The gap page is the docs deliverable (#508):
`docs/reference/what-will-fail.md`, generated from the R0 corpus fences, one
row per module and fence code, dated, regenerable in CI.

## Beta-readiness checklist

Before the beta opens to testers:

- [ ] R0 pass rate at or above 60% on the committed corpus (24 of 39), and
      the number is on the site with a trend (#506).
- [ ] R1 pass rate at 100% of the ceiling (the compile rate; #183-style
      soundness bugs are closed).
- [ ] Every R0 fence code has a `pyscriptc explain` entry and a row on the
      what-will-fail page (#508, #511).
- [ ] A fresh clone on macOS and Linux: `pip install pyscriptc`, `pyscriptc
      compile <3 corpus scripts> --emit bin`, binaries run and match
      (wheel smoke exists; add the corpus scripts to it).
- [ ] Perf receipts for the shapes the corpus exercises: file walk, json
      round-trip, sha256 blocks (#380 epic, #390/#391 nightly gates).
- [ ] The beta-feedback issue template names the corpus and asks for the
      script shape, so feedback maps to a fence family.
- [ ] The what-will-fail page is less than a month stale (CI check).

Honest "what will fail" seed, per the measured gaps (the page is #508):

- `os.walk`, `os.system`, `json.load`/`dump` (file forms), `time.perf_counter`,
  `itertools.product`, `dataclasses.asdict`, `random.sample`,
  `subprocess.Popen`: fence with PS2001 until #503/#509 land.
- Any keyword argument to any stdlib call (`shutil.move(src, dst)`,
  `subprocess.run(..., capture_output=True)`, `open(..., encoding=)`): PS2003
  until #501.
- Any unannotated name whose value is a call: PS1001 plus a cascade of PS2002
  until #500.
- `platform`, `calendar`, `zipfile`, `tarfile`, `sqlite3` imports: PS2002
  until #502 (sqlite3 deliberately out of beta scope).
- Module-level work (a parser built at module scope): PS2002 until #502.
- https:// URLs at run time: OSError per divergence 28 until #235 lands.
- Strings are Latin-1, json has no non-ASCII, re is a byte engine: the S-wave
  (#472 to #479) is the fix, v0.8.0/v0.9.0.

## The grind plan

Four phases, ordered by the unlock ladder. Each phase has a measurable
receipt: the R0 number. Effort sizes are the repo's S/M/L/XL.

### P1: inference and module scope (Current, v0.8.0)

The first wall in 23 of 39 scripts. Two tickets.

- #500 (L) lower: local, parameter and return inference from the bound value.
  Infer from the RHS: literals, lowered calls, typed defaults. Parameters
  without a default stay fenced but name the call sites. Receipt: R0 moves
  from 0 to at least 5.
- #502 (M) module scope: foldable module assignments and the import rows
  `platform`, `calendar`, `zipfile`, `tarfile`. Receipt: the module-assign
  family empties; the import-table family falls to 2.

The inference work rides the existing `_bridge_type` seam in the lowerer: the
checker already proves types for a narrow set of unannotated assignments; the
ticket widens that to the shapes the corpus shows. It does not need the
tagged-union epic, and it must not grow into it: `x = None` and `T | None`
stay fenced, pointing at #413.

### P2: the call table and argparse (Current, v0.8.0)

The second wall, reachable only after P1. Three tickets.

- #501 (M) call table: keyword arguments and arity rows over the stdlib
  surface. Signature rows with parameter names and defaults; the call lowers
  by reordering keywords into the positional shape the runtime helper has.
  Receipt: the keyword-args family falls from 8 scripts to at most 2; R0 at
  least 23.
- #510 (L) argparse: the closed surface a real CLI needs (`type=`, `default=`,
  `action="store_true"`, static attribute reads on the parsed result). The
  three R1 argparse scripts are the acceptance fixtures. Receipt: 12 corpus
  scripts get past the argparse block.
- #511 (M) dx: compile prints the root fence and counts the cascade. Receipt:
  a corpus script with one root cause prints at most 3 fences.

### P3: the stdlib surface and operators (Next, v0.9.0)

The per-function gaps, once the calls can land at all.

- #509 (M) the collector surface: `os.walk`, `json.load`, `time.perf_counter`,
  split from #503 because these are the first fences a tester meets.
  `os.walk` needs the three-name loop target, which #481 touches.
- #503 (M) the rest of the script surface: `os.system`, `json.dump`,
  `subprocess.Popen`/`run`/`check_output`, `itertools.product` and friends,
  `dataclasses.asdict`, `random.sample`. Fold into the existing per-module
  tickets #486-#495 rather than duplicate.
- #504 (M) operators: true division, int modulo with the CPython sign rule,
  mixed int/float compare, `s * n`, container equality, set algebra.
  Receipt: R0 at least 29; the numeric tower matches CPython on a probe pack.

### P4: checker alignment and the beta gate (Current, before the beta)

- #505 (S) checker: a lowering-complete script never exits 2. The checker
  bridge widens to the corpus shapes (`os.getenv` with default,
  `struct.unpack`, `frozenset`). Receipt: every R0 script that lowers also
  compiles without `--skip-check`.
- #507 (S) the first-class bar page and the checklist above, so the beta
  opens against a written bar.
- #508 (M) the what-will-fail page, generated from the corpus, the honest
  seed above becomes the artifact.

### Out of scope for the script grind (explicitly)

- The tagged-union epic (#413) and closures/callables-as-values (#436-#443):
  the corpus shows they are not the walls scripts hit first. They stay on the
  object-model track.
- sqlite3, GUI toolkits, numpy-tier compute (#496, #497): the corpus
  deliberately excludes them; the import fences stay with a named diagnostic.
- The blessed third-party list: later, after the stdlib surface is first
  class, through the deps/pack track (#428, #430). The strategy order is
  stdlib first, packages second.

## The nightly lane

#506. The nightly already runs the ladder (`compat/data/ladder.json`) and
publishes it on the site. Add the R0 lane:

- `uv run python -m benchmarks.real_scripts.measure --json` in the nightly.
- `ladder.json` gains an `r0` key: pass rate, corpus size, blocker and
  root-cause rankings. The site shows R0 next to R1 and R2.
- The R0 pass rate is the grind's receipt: every phase above names the number
  it moves. The nightly keeps it honest, and a drop opens one
  `nightly-failure` issue, the shape the visible-score job already uses.
- Keep the last 30 nightly values per rung in the JSON so the trend is
  visible without an artifact download.

Guardrails: the R0 lane never touches `benchmarks/compat2/`, never reads the
holdout, and never gates master.

## Decisions

Recorded in `.agents/decisions.md`:

- 2026-10-24: R0 is a measurement corpus only. Third-party scripts are
  vendored byte-identical, never edited to fit the compiler; when a script
  blocks the measurement, drop the script, do not fix it. This keeps the
  fence signal honest and the corpus license-clean.

## Receipts

- Corpus: `benchmarks/real_scripts/`, 39 scripts, manifest with pinned
  commits and sha256 per file.
- Baseline measurement: 0/39 compile, 386 fences, first-wall table above.
  The counterfactual ladder: 3, 6, 23, 29, 34, 37, 39.
- The R0 number on the site, nightly, from #506.
