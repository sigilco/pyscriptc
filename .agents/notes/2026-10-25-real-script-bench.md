# Real script benchmark: island floor against CPython (2026-10-25)

Measurement on 2026-10-25 in `~/.worktrees/pyscriptc-bench8` on master `e1932b04` (the #627 merge), CPython 3.14.9 on arm64 macOS, with the shared machine at a 1 minute load average of 3.0 to 3.2 for every sample.

## Step 1: all eight scripts run with matching output

The eight dogfood scripts from `/tmp/oc/dogfood3.out.md` now run under `pyscriptc run --fallback=cpython --report PATH` with the same stdout and exit status as `python3`, up from 6 of 8 in dogfood3 and 4 of 8 in run 1. Both runs used the script's own directory as the working directory, `PYTHONPATH` set only for `split.py` (the dogfood did the same), a 60 second timeout, and no `--skip-check`.

| Script | Plane map (native / island) | Top level | CPython exit | Compiled exit | Stdout match |
|---|---|---|---|---|---|
| `fx/scripts/binary_size.py` | 0 / 12 | island | 2 | 2 | yes |
| `brioso/scripts/validate.py` | 0 / 9 | island | 0 | 0 | yes |
| `brioso/scripts/check-deploy-secrets.py` | 0 / 1 | island | 1 | 1 | yes |
| `maki/scripts/tool_token_analysis.py` | 0 / 17 | native | 1 | 1 | yes |
| `tinyharness/practice/parse_tickets.py` | 0 / 3 | island | 1 | 1 | yes |
| `agentplugins-caveman/skills/caveman-compress/scripts/cli.py` | 0 / 2 | island | 1 | 1 | yes |
| `HTLM/data/pipeline/split.py` | 0 / 5 | native | 2 | 2 | yes |
| `wowplay-app/tools/launch-diagnostics/classify.py` | 0 / 3 | island | 1 | 1 | yes |

The two that moved since dogfood3 are `validate.py` and the caveman `cli.py`, both of which were halted by a module level import fence and now route the module top level to the island. `validate.py` imports `datetime.timezone` and now exits 0 exactly like CPython; the caveman `cli.py` uses `from .compress import ...` and now reaches the same `ImportError: attempted relative import with no known parent package` that CPython raises when the file is run as a script, so a matching exit status here means matching CPython's refusal, not a working compression run.

`split.py` matching at exit 2 is the usage path, because the compiled binary cannot see its arguments yet: I filed #635 for it, with a minimal repro showing `sys.argv` reading as `['']` inside island calls when the module top level stays native, and `split.py` is one of the two plane maps with a native top level.

## Step 2: method

`pyscriptc assess --bench` cannot cover these scripts, because it times compilable native entry points and reports `(no compilable entry point to benchmark)` for every one of them; the plane maps are 100 percent island, so there is nothing native for it to time. The measurement uses `benchmarks/real/run.py` instead, added in this branch with `benchmarks/real/cases.json` and fixed inputs under `benchmarks/real/<name>/`.

Each case compiles once with `pyscriptc compile --fallback=cpython --emit bin -o <artifact>`, reads the plane map from the artifact's `.report.json`, and then times `python3 <script> <args>` against `<artifact> <args>` on the same argument list and working directory, after 2 warmups, for 12 samples, interleaved so drift hits both sides, sequentially on the shared machine.

The inputs are deterministic and committed: a 1 MiB and 1.25 MiB pseudorandom binary with two Mach-O section reports for `binary_size`, a 4500 line Wine launch log for `classify`, 1500 JSONL records for `split`, and six synthetic session files for `tool_token_analysis` (the case points `HOME` at them). `check_deploy_secrets` needs pyyaml on the island path, set through `PYSC_REAL_PYYAML`, and reads only the names in the brioso manifest, never a value.

## Results

All figures are wall clock milliseconds for the process, median with p10/p90 in brackets, and the ratio is the binary median over the CPython median. Island share is the share of planned definitions in the plane map; there are zero native definitions in every case.

| Case | Kind | Island share | CPython median [p10, p90] | Binary median [p10, p90] | Ratio | Compile s | Binary KiB |
|---|---|---|---|---|---|---|---|
| `binary_size` | workload | 12 / 12 (100%) | 38.4 [37.7, 39.3] | 36.2 [35.6, 36.9] | 0.94 | 1.91 | 630 |
| `classify` | workload | 3 / 3 (100%) | 52.8 [52.1, 54.6] | 51.8 [50.5, 52.7] | 0.98 | 0.75 | 614 |
| `tool_token_analysis` | workload | 17 / 17 (100%) | 31.1 [30.0, 32.0] | 29.8 [29.3, 30.6] | 0.96 | 1.02 | 629 |
| `check_deploy_secrets` | workload | 1 / 1 (100%) | 29.4 [28.9, 30.2] | 27.9 [27.4, 28.3] | 0.95 | 0.79 | 614 |
| `split` | startup | 5 / 5 (100%) | 37.4 [36.8, 38.8] | 35.4 [34.6, 36.3] | 0.95 | 0.79 | 614 |
| `parse_tickets` | startup | 3 / 3 (100%) | 32.7 [30.9, 34.5] | 21.4 [20.1, 22.9] | 0.65 | 0.75 | 614 |
| `caveman_cli` | startup | 2 / 2 (100%) | 30.8 [30.1, 31.9] | 19.9 [19.4, 20.6] | 0.65 | 0.82 | 614 |

`validate` is excluded from timing on purpose: its `main` shells out to git, `uv lock --check` and the per project gates, so the number would measure the host toolchain and would put several test suites on a shared machine.

## Reading the numbers

There is no speedup here, and the plane maps say why: every definition in every script plans to the island, so the compiled binary carries no native user code at all, and the ratio can only move by the difference in fixed costs, which is what the 0.94 to 0.98 band is.

The fixed cost is measurable and small. A one line module under `python3` takes 14.0 ms median in the same conditions and the same module compiled to a binary takes 13.4 ms, so both sides pay one libpython initialization, and the island binary is not a smaller runtime; the binaries are 614 to 630 KiB and `requiresLibpython` is true in every plane map, which also means nothing here is a standalone artifact yet.

The work component is therefore median minus that 14 ms floor: about 24 ms of work under CPython against 23 ms in the binary for `binary_size`, and 39 against 38 for `classify`. Those one to two millisecond gaps are the whole difference, they repeat across 12 interleaved samples with non overlapping p10/p90 in two of the four workload cases, and they favor the binary. My reading is that the gap comes from the startup and import path rather than from executing the script faster: importing `pathlib`, `csv` and `re` costs 6 ms on top of a bare interpreter start, and importing the `classify` set (`json`, `re`, `sys`, `pathlib`, `typing`) costs 9 ms, so small differences in how each side resolves its imports move the median by more than any per call marshalling would at this size.

The two 0.65 ratios are startup, not speed. `parse_tickets` is an unfinished drill whose `main` reads an undefined `lines`, and the caveman `cli.py` fails its relative import, so in both cases the script's actual work never runs; the binary reaches the same error 11 to 12 ms sooner, which is a statement about boot, not about the scripts, and I would not quote those two as a win anywhere.

An island call does marshal its arguments and return value, but none of these scripts call enough island functions often enough for that cost to surface above the 14 ms floor. That is the honest limit of this data set: it measures the island floor as a way to run unmodified scripts, and it does not measure the island as a hot path.

## Compile time and artifacts

Compile wall time is 0.75 to 1.02 s per script with a warm runtime cache, and 1.91 s for `binary_size`, which is also the module with the most planner work and the longest checker output; this is the single invocation cost, not a per run cost. Binary size is 614 KiB for the one function scripts and 630 KiB for the twelve function `binary_size`, so the per definition cost is small next to the runtime and libpython linkage.

## Not measured

`benchmarks/ports/` does not exist on master and PR #633 (`feat(ports): native ports of real sibling scripts and delta receipts`) is still open, so the original against port against CPython comparison is not in this report; it can be added to `cases.json` once that lands.

`validate.py` is out of the timed set for the reason above, and its earlier full run in step 1 is the only evidence for it.

Native share is zero everywhere in this set, so no case here exercises the native ceiling; the numbers say nothing about how fast lowerable code runs, only about what the island floor costs when it carries the whole program.

## Follow ups

#635 is filed in v0.7.0 for the argv gap, which blocks `split.py` as a compiled binary and blocks any script that parses its own arguments while keeping a native top level.

`pyscriptc run` also accepts no arguments for the script, so the two ways to run a fenced script with its own inputs are compiling with `--emit bin` and executing the artifact, which is what the harness does, or wrapping the call with `--call` and `--args`.

The four workload ratios land between 0.94 and 0.98, so if the goal is to beat CPython on scripts like these, the work has to come from native definitions and not from the island floor.
