# Island remainder: what native plus island still cannot run (2026-10-25)

Assessment only, no compiler changes. Worktree `~/.worktrees/pyscriptc-complement`, branch `work/complement`, master `30ca38df`, CPython 3.14.9 host, arm64 macOS. Tracking epic: #428. Raw outputs and the measuring scripts are in `/tmp/complement` (`measure.py`, `probes.py`, `out/*.json`, `raw/*.json`); they are not committed.

Answer in one line: the island floor already plans 97 percent of the definitions in 27 real trees and runs 80 percent of the scripts and idiom probes with matching output, and the remainder is not a feature wall but a short list of seam and program-mode defects, plus the deliberate i64 `int`.

## Method

Three measurements, each verified by running it.

1. Definition shares: `pyscriptc assess --json` per tree, summed over the per-file `planeMap` entries (functions and classes). Native is a definition planned native, island is planned island, unplanned is `summary.definitions.total` minus the plane entries. Unplanned is an upper bound for blocked-even-on-island, because the plane map keys definitions by name and loses overloads, property setters and same-name methods (checked on `_pytest`: 71 of 2287 definitions have no entry, one of them fenced, the rest are collisions or entries in the top-level map).
2. Scripts end to end: `pyscriptc run --fallback=cpython SCRIPT` against `python3 SCRIPT`, same working directory, stdout and exit status compared (stderr is not compared). Timeout 60 s for CPython, 300 s for the compile and run.
3. Idiom probes: 44 small hand-written programs under `/tmp/complement/probes` covering the root-cause list in the task (classes, decorators, generators, async, SystemExit, argv, seams, program mode, cycles). They are adversarial by design and not a sample of real code, so they size the failure classes and say nothing about their frequency.

Not measured or inconclusive, so treat as unverified: `q09` (multiprocessing) fails identically under CPython and the binary with `OSError: [Errno 28] No space left on device` because the laptop disk is at 97 percent, so it says nothing; `p15` (a package `__main__` with a relative import) is an invalid probe as a script, and `run --program --fallback=cpython pkgp/app` does match CPython; #643 did not reproduce and was not minimized.

## Headline numbers

| Population | Items | Native only, matches | Island involved, matches | Blocked or wrong | Match share |
|---|---|---|---|---|---|
| Visible corpus scripts (`benchmarks/compat2/corpus/scripts`) | 20 | 8 | 8 | 4 wrong, 0 blocked | 80 percent |
| 8 dogfood scripts (run as in `/tmp/oc/dogfood3.out.md`, no arguments) | 8 | 0 | 7 | 1 (`split.py` without `PYTHONPATH`) | 87.5 percent, 100 percent with `PYTHONPATH` |
| 8 libraries via `/tmp/oc/lib3` call programs | 8 | 0 | 8 | 0 | 100 percent |
| Idiom probes | 44 | n/a | 35 | 9 | 79.5 percent |

Reading it: across the 28 scripts (corpus plus dogfood) 8 run native only (28.6 percent), 15 need the island and match (53.6 percent), 5 are wrong or fail (17.9 percent), so native plus island is 82.1 percent. None of the 28 is refused outright. The 4 corpus failures are all silent wrong results: three annotated `int` scripts (`bigint_factorial`, `gcd_lcm`, `pi_digits`) abort with `OverflowError` because native `int` is i64 (divergence 1), and `nbody` prints the same energy twice because the island call lost the list mutation (#674). The dogfood scripts only exercise their usage or error path (no arguments), so 7 of 8 is a floor on harness behavior, not on real runs. The `split.py` failure as run is the missing `PYTHONPATH` (preflight exit 2 against CPython exit 1); with `PYTHONPATH` set both exit 2 with empty stdout, and stderr differs only in the program name (`split.py` against the cache path of the binary, which is #635 territory).

Definition shares over 27 trees (the 22 pinned corpus sources, pytest 9.1.1 `_pytest`, requests 2.34.2, rich 15.0.0, pygments 2.21.0, and the `harness` CLI), 10,938 definitions, 2,014 native, 8,611 island, 313 unplanned:

| Share of definitions | Count | Percent |
|---|---|---|
| Planned native | 2014 | 18.4 |
| Planned island | 8611 | 78.7 |
| Unplanned (upper bound for blocked even on the island) | 313 | 2.9 |
| Memo: lowerable natively with no fallback (`summary.definitions.native`) | 6388 | 58.4 |

The gap between 58.4 percent lowerable and 18.4 percent planned native is the biggest number in this table and is a native-share problem, not an island-coverage problem: see cause 6 and #678.

Per tree (files, definitions, lowerable, planned native, planned island, unplanned):

| Tree | Files | Defs | Lowerable | Native | Island | Unplanned |
|---|---|---|---|---|---|---|
| `_pytest` | 78 | 2287 | 1146 | 8 | 2208 | 71 |
| pygments | 343 | 1698 | 680 | 32 | 1665 | 1 |
| rich | 100 | 1093 | 1093 | 1069 | 0 | 24 |
| pyasn1 | 32 | 809 | 809 | 800 | 0 | 9 |
| click | 17 | 667 | 325 | 0 | 615 | 52 |
| pyparsing | 17 | 615 | 331 | 2 | 605 | 8 |
| tomlkit | 12 | 527 | 380 | 21 | 483 | 23 |
| bs4 | 15 | 469 | 269 | 10 | 425 | 34 |
| dateutil | 18 | 355 | 165 | 0 | 347 | 8 |
| requests | 19 | 319 | 172 | 8 | 291 | 20 |
| markdown_it | 66 | 284 | 116 | 10 | 255 | 19 |
| tqdm | 31 | 279 | 125 | 9 | 263 | 7 |
| typing_extensions | 1 | 264 | 165 | 0 | 250 | 14 |
| attr | 13 | 245 | 82 | 12 | 228 | 5 |
| soupsieve | 7 | 220 | 108 | 0 | 215 | 5 |
| importlib_metadata | 12 | 199 | 100 | 22 | 175 | 2 |
| pluggy (corpus copy) | 8 | 109 | 53 | 0 | 105 | 4 |
| python_multipart | 4 | 115 | 91 | 2 | 111 | 2 |
| referencing | 7 | 106 | 62 | 3 | 103 | 0 |
| wheel | 15 | 94 | 42 | 1 | 91 | 2 |
| harness | 12 | 83 | 14 | 0 | 83 | 0 |
| markupsafe | 2 | 59 | 50 | 1 | 57 | 1 |
| typing_inspection | 3 | 21 | 3 | 0 | 19 | 2 |
| aiohappyeyeballs | 5 | 12 | 2 | 0 | 12 | 0 |
| annotated_doc | 2 | 5 | 4 | 4 | 1 | 0 |
| sniffio | 3 | 3 | 1 | 0 | 3 | 0 |
| jsonschema_specifications | 2 | 1 | 0 | 0 | 1 | 0 |

The 31 definitions of the 20 corpus scripts are 18 native, 13 island, 0 unplanned; the 53 dogfood definitions are 1 native, 52 island, 0 unplanned. rich has 24 unplanned definitions, and its routing report lists PS3001 refusals for `_unicode_data/unicode10-0-0.py` ("not a module name", a hyphen in the file name); I did not confirm that those account for all 24.

Big package end to end (library used from a small program, `PYTHONPATH` set to the venv): `requests.Request(...).prepare()` in a function and in a class method, and a `rich` Console plus Table rendered to a string, all match CPython. `pytest.main` under the island matches CPython after normalizing the durations (3 tests, 1 failing; exit 1 both) when wrapped in a lowered function; written as a top-level `raise SystemExit(pytest.main(...))` it compiles on this master (the `KeyError _ps_imod_1` of #650 did not reproduce) but exits 0 where CPython exits 1.

Program mode is the exception to all of the above. Multi-module trees compiled as programs: `harness` (12 modules) fails with `PS2002: a module assignment must be a constant` in two imported modules, `requests` fails with `PS2002: circular import: requests._types -> requests.auth -> requests.cookies -> requests._types` and, without `--skip-check`, with preflight exit 2 on checker errors, and the #641 repro still crashes with `KeyError: 'mp.helper.count'`. Untyped imported modules do work (two-module and cyclic untyped programs match CPython). So 1 of 1 realistic multi-module project in the sample is blocked.

## Island-blocked remainder by root cause

Ranked by items freed in the sample (28 scripts plus 44 probes = 72 items, 58 passing, 14 failing), then by reach beyond the sample. "Refused" means the compile stops; "wrong" means it runs and prints something else. Wrong is the worse class for the v0.7.0 gate, which is matching output.

| Rank | Root cause | Class | Sample items freed | Evidence | Issue |
|---|---|---|---|---|---|
| 1 | Native `int` is i64, annotated big-int code aborts where the unannotated island copy is right | wrong | 4 (3 corpus, `q13`) | `bigint_factorial`, `gcd_lcm`, `pi_digits`; `q12` untyped factorial matches | #677 (new, v0.8.0), divergence 1 |
| 2 | Island module identity: `__name__` is `__pyscriptc_island__`, pickle of island classes fails, `atexit` handlers do not run | wrong | 3 (`q04`, `q11`, `q15`) | `q04` prints `__pyscriptc_island__`, `q15` pickling error, `q11` loses `bye` | #679 (new, v0.8.0) |
| 3 | Native-to-island seam copies lists in, never back | wrong | 2 (`nbody`, `q20`) | second energy equals the first; `q20` prints `3.000` twice | #674 (new, v0.7.0) |
| 4 | Program mode and imported modules: unplanned imported modules, non-constant module assignments, typed import cycles, checker refusals on the tree | refused or crash | 0 in sample, 1 of 1 multi-module projects | harness, requests-as-program, `mp` repro | #641 (open, v0.7.0), checker part #505 |
| 5 | Decorated functions and decorator expressions | refused, crash | 1 (`p23`) plus click | `PS2002 user decorators are not lowered`; `@click.command()` `KeyError: 'params'` | #676 (new, v0.7.0), #626 (open, v0.7.0) |
| 6 | Island module top level demotes every lowerable function of the module | native share only | 0 | `_pytest` 1146 lowerable, 8 planned native; 71 of 78 files have an island top level | #678 (new, v0.8.0) |
| 7 | Native function read as a value by an island function | wrong at run time | 1 (`p17`) | `NameError: name 'sq' is not defined`, plane map `sq: native`, `apply: island` | #675 (new, v0.7.0) |
| 8 | `SystemExit` with a string payload from the island | wrong | 1 (`p06`) | exits 0, silent | #642 (open, v0.7.0) |
| 9 | argv is empty inside island functions when the top level stays native | wrong | 0 in this sample (dogfood `split.py` stderr only) | `split.py` usage line shows no program name | #635 (open, v0.7.0) |
| 10 | Lowerer crashes: `KeyError _ps_imod_1` peeking an island call | crash | 0 (not reproduced) | harness slice only, not minimized | #643 (open, v0.7.0), #650 (exit status part commented) |
| 11 | Native call table ignores a keyword (`subprocess.run(text=True)` returns bytes) | wrong, native | 1 (`q10`) | prints `b'2'` | #680 (new, v0.8.0) |
| 12 | Unplanned definitions (2.9 percent upper bound): imports outside the closed table (`gettext`, `types`, `collections.abc`), re-exports "not exported by this package", `TypeAlias`, `functools.update_wrapper`, hyphenated module names | refused | 0 in scripts | click 52, bs4 34, tomlkit 23, requests 20 | #412, #413, #414 (v0.9.0) |

Classes with no failing probe: untyped and decorated dataclass, untyped class hierarchies with `super()`, metaclasses, `__slots__` with property setters and rich comparisons, `abc` and `enum`, context-manager classes, generators and generator expressions, `async def` with `asyncio.run`, `gather`, async generators and `asynccontextmanager`, `lru_cache`, closures with `nonlocal`, `match` and walrus, `*args` and `**kwargs`, `exec` and `eval`, threads, stdin, argparse, recursion limits, exceptions crossing the seam, a program with a decorated dataclass in an imported module. The earlier fears (classes, generators, async) are no longer where the remainder is.

## Estimated coverage if the top five are fixed

Top five by sample items freed plus breadth: #677 (i64), #679 (module identity and atexit), #674 (seam copy back), #641 (program mode), #676 plus #626 (decorators). In the 72-item sample these free 4 + 3 + 2 + 0 + 1 = 10 of the 14 failures, so matching output goes from 58 of 72 (80.6 percent) to 68 of 72 (94.4 percent). The four left are #675, #642, #680 and the `split.py` environment case. Program mode frees nothing in this sample because the sample is single-file scripts, but it moves the one multi-module project from fully blocked to plannable, so it is the item that decides whether "weekly use on a real project" is possible. These are estimates from a small sample with hand-written probes, not a forecast for arbitrary code.

For definitions the island floor is already near its ceiling: 97.1 percent planned (native plus island), at most 2.9 percent unplanned, and none of the 28 real scripts has an unplanned definition. The remaining lever there is native share (18.4 percent planned against 58.4 percent lowerable), which #678 addresses and which the five above do not touch.

## Filed or updated

New, milestone v0.7.0: #674 seam list mutation, #675 native function as a value, #676 decorated functions. New, milestone v0.8.0: #677 i64 overflow, #678 island top level demotion, #679 island module name and atexit, #680 `subprocess.run(text=True)`. Commented with fresh evidence: #641, #642, #626, #650.

## Proposal: one headline metric

Publish a single three-way split, `native + island + blocked`, of the scripts in the visible corpus plus the dogfood set that run end to end with output matching CPython, with `blocked` including wrong results (today 8 + 15 + 5 of 28, so native 28.6, island 53.6, blocked 17.9), and carry the same split for definitions underneath it (today 18.4, 78.7, 2.9 over 10,938 definitions). The number to move is `native + island`, because the product promise is that a user can embed the island next to native code and shift work to native over time, and a second number, the native share of the lowerable definitions (18.4 of 58.4 today), tracks that shift. It belongs in `compat/data/` as a new `compat/data/coverage.json` (or a `complement` block in `compat/data/ladder.json`, next to the R1 and R2 rung totals), produced by a nightly job that runs `pyscriptc assess --json` plus `run --fallback=cpython` over the pinned corpora, per `.agents/docs/doc-cadence.md` class 3: structured JSON only, no markdown tables, published by the compat site, with `scripts/build_compat_json.py` or a sibling generator listed in the cadence table. The existing `weighted_pass` in `compat.json` is 0.0 and counts native lowering only, so it does not show the island at all.
