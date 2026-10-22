# Script corpus

The R1 rung of the adoption ladder: single-file CLI scripts, the smallest unit a user adopts.

The plan that asks for this lives in `.agents/plans/2026-10-21-adoption-compat-ffi-strategy.md`, section B. R1 is the whole-file pass rate: the share of scripts that compile and match CPython on stdout, stderr and exit code for a recorded invocation. This directory holds the scripts, the CPython recordings and the runner that produces `REPORT.md`.

This directory sits outside the frozen v2 gates. It never writes to `benchmarks/compat2/`, it does not import `outcomes.py` for anything but the outcome names, and it reuses the frozen `coverage_map` only through the public `pyscriptc.coverage` API. The v2 scorer and its corpus are untouched.

## What is in here

| Path | What it holds |
| --- | --- |
| `scripts/<slug>/main.py` | one script: a single file, no third party imports |
| `scripts/<slug>/meta.json` | licence and origin for that script |
| `trees/<slug>/__main__.py` | one tree: a thin entry over sibling modules |
| `trees/<slug>/meta.json` | licence, origin, the recorded invocation and the module list for that tree |
| `fixtures/` | the small inputs the scripts read, committed so a run is reproducible |
| `oracle/<slug>.txt` | the stdout CPython 3.14 produced for the recorded invocation |
| `oracle/<slug>.json` | the invocation, the stdout, the stderr and the exit code |
| `run.py` | the runner: compiles every script and reports the blocking features |
| `trees.py` | the R2 runner: the same measurement over the multi-module trees |
| `ladder.py` | the ladder metrics: R1, R2 and the R4 lane shares, the numbers the site publishes |
| `oracle.py` | records the CPython output for every script |
| `make_fixtures.py` | rebuilds `fixtures/`, including the fixed modification times |
| `REPORT.md` | the generated report, one row per script |

## The scripts

Every script is a real single-file CLI, written the way a sysadmin or a data worker would write one: `sys.argv` or `argparse` for input, `print` or `sys.stdout.write` for output, an `int` return from `main()` for the exit code, and only the stdlib.

They cover the shapes an early user actually has: reading and writing text files, walking directories, `csv` and `json` conversion, `re` matching, `pathlib` and `os` path work, `datetime` dates, `subprocess` calls, `hashlib` and `base64` digests, `urllib.parse` URLs, `argparse` flags, `tarfile` archives, `shutil` copies.

Two rules keep the corpus honest:

- The scripts are not written to fit the compiler. Where a natural script shape does not lower, the script keeps the natural shape and the fence is recorded. Tuning every script until it compiles would measure nothing.
- The scripts are not written to fail either. Each one is a working tool that runs under CPython and produces the same output every time.

All scripts are MIT licensed and original. No upstream source was copied, the same rule the compat2 scripts corpus follows.

## Running it

```bash
uv run python -m benchmarks.scripts_corpus.run
```

prints the per-script table: compile pass, fence count and the top blocking features.

```bash
uv run python -m benchmarks.scripts_corpus.run --markdown benchmarks/scripts_corpus/REPORT.md
uv run python -m benchmarks.scripts_corpus.run --json /tmp/corpus.json
uv run python -m benchmarks.scripts_corpus.run --only word_count,csv_totals
uv run python -m benchmarks.scripts_corpus.run --exec
```

`--exec` is the full R1 measurement: it builds a native binary for every script that compiles, runs it with the recorded invocation, and compares stdout and the exit code against the CPython recording. It needs `clang` or `zig` on PATH. The three states are kept apart on purpose: `pass`, `wrong-output` and `build-error` are a different failure class from `fence`, and a script that compiles and still differs is a soundness signal.

To refresh the CPython recordings after a script or a fixture changes:

```bash
uv run python -m benchmarks.scripts_corpus.make_fixtures
uv run python -m benchmarks.scripts_corpus.oracle --all
```

The R2 trees have their own runner and their own report:

```bash
uv run python -m benchmarks.scripts_corpus.trees
uv run python -m benchmarks.scripts_corpus.trees --exec
uv run python -m benchmarks.scripts_corpus.trees --markdown benchmarks/scripts_corpus/REPORT_R2.md
```

The ladder metrics, the numbers the public page shows:

```bash
uv run python -m benchmarks.scripts_corpus.ladder
uv run python -m benchmarks.scripts_corpus.ladder --json compat/data/ladder.json
```

The recordings are the oracle. When the compiler lowers a script, the compiled binary is compared against `oracle/<slug>.txt` and the recorded exit code.

## What the first run found

The first `--exec` run found a soundness bug that no fence report shows: `sys.exit(main())` evaluates `main()` twice, so every compiling script doubles its output. The cause is in `src/pyscriptc/llvm.py`, where `emit_call` computes the argument values and `emit_module_call` then emits the same arguments again. That is issue #183, and it sits on the serialized lowering track, so this corpus does not fix it. The corpus reports it, and the report keeps the compile rate and the true R1 rate apart until the fix lands.

## How it feeds the adoption ladder

The ladder has six rungs. This directory is the evidence for R1 and the shape the other rungs will follow.

**R1, single-file script.** The metric is the whole-file pass rate. `REPORT.md` holds the current number and the family ranking underneath it. A family that blocks many scripts is the next compiler epic: the ranking is a set cover over the corpus, the same shape as the compat2 census, so the two lists can be compared.

**R2, multi-module tree.** The metric is the same pass rate over `trees/`: a thin `__main__.py` over sibling modules, compiled as one binary and compared with the CPython recording. `REPORT_R2.md` holds the current number. The tree shapes stay inside the lowered subset on purpose: a tree that fences on the same family as its scripts would measure the family, not the rung.

**R3 to R5, the other rungs.** R3 wants pyproject packages, R4 dependency lanes, R5 services. Each rung multiplies the shape of a script: R2 adds a second file, R3 adds a `pyproject.toml`, R4 adds a dependency and a lane, R5 adds a socket and a loop. The corpus structure already accommodates that. A rung is a directory next to `scripts/`, a runner that loads it, and one more metric in the report. The scripts here are the unit that multiplies, so the work list of R2 is the work list of R1 seen from a different angle.

**The per-rung metric.** The strategy plan asks for a per-rung, per-lane metric and never a single "Python compat percent". `REPORT.md` follows that shape: a pass rate per rung, never one number for the compiler. When the other rungs land, they get their own section in the same report and the page reads down the ladder.

**What a blocker means.** A fence family in the report is a named compiler epic with a hint attached, the same hints `pyscriptc coverage` prints. The corpus does not decide which family to fix first. It measures how many scripts each family holds back, so the decision is made on reach rather than on taste. The families that dominate here are the ones a single-file CLI user hits on the first day: `argparse` is not in the closed import table, `sys.argv` and keyword arguments have narrow surfaces, and the stdlib subset covers `os`, `re` and `json` only partly.

**The corpus feeds the public page.** The v0.6.0 `/compat` page shows R1 and R2 first, then the R4 lane shares. The numbers it shows come from this directory: `ladder.py` writes `compat/data/ladder.json`, and the site builder carries that into the page data. A PR that changes the compiler and moves the R1 rate is a PR that moves a published number, so the report is committed with every change that moves it.

## The rules this directory follows

- The frozen v2 gates do not move: `outcomes.py`, the `score.py` weights, `coverage_map.py`, `cache.py`, `calibrate.py`, `CALIBRATION.json`, `TRIAGE.md`, `probes/` and `canonical.py`.
- `benchmarks/compat2/` and its corpus are untouched, including the holdout, which stays closed.
- Nothing in `src/pyscriptc/lower/` changes. The corpus only reads the compiler.
- `REPORT.md` is generated. It is committed with the run that produced it, so the history shows the rate moving.
