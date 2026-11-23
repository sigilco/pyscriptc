# Baseline

Reference metric set for the workloads `pyscriptc` compiles today. Each sample is one process. The runner records wall time and peak resident set size (`VmHWM`) for the compiled binary and for CPython, and it times nothing whose stdout disagrees with CPython. `ru_maxrss` is not the source: on this host it follows the cgroup high-water mark, so a small binary and CPython report the same number.

`reference.json` is that set, recorded on one Linux host. Absolute milliseconds and kilobytes belong to that host. The runner prints speedups as `Nx CPython` (higher is better). The stored ratio, and the check, stay pyscriptc / CPython, so a later machine is not asked to hit the same millisecond. A run fails when a time ratio grows past 2× the reference, or an RSS ratio grows past 1.25×. The time band is wide because these kernels finish in under a millisecond.

The kernel geomean is still the original four loops (`sum-squares`, `floor-sum`, `horner`, `repeat-len`). Every other lowered module has its own row. `group_geomeans` holds `stdlib`, `scripting`, and `island`. The island group links libpython, so its RSS speedup sits near 1x CPython. A full `--check` compares each row and each group geomean with this file.

Fenced programs stay out of this set. They have no native binary to time.

```bash
uv run python -m benchmarks.baseline.run --check
uv run python -m benchmarks.baseline.run --record
```

`--check` reads the reference file for this host. `--record` replaces that same file with the run just taken. The report also keeps compile time and the linked binary's size; those are not part of the ratio gate.

## The inference suite

The compile-time inference suite (issue #1319) prices the pin engine: the same trees compiled without inference and with `--infer`, so the run records what inference costs a tree. It is report-only, like the `bench` workflow that runs it: the rows are recorded per release and the ratio gate arms after two releases of data, so `--record` and `--check` refuse this suite and nothing here fails on a number.

```bash
uv run python -m benchmarks.baseline.run --suite inference --json=infer.json
uv run python -m benchmarks.baseline.delta --inference infer.json
```

Three workloads, all in-repo: `examples/wordcount` (fully annotated, so the skip fast path of issue #1317 takes the engine out and the row shows it skipped), `benchmarks/real_scripts/scripts` (39 script modules, a real mixed-annotation tree) and a generated ~150-module chain (`synthetic.py`, written to a temporary directory at run time so the suite stays hermetic). Each row carries the plain compile time, the compile time with `--infer`, the engine's own wall time and its report (pins, call sites, skipped flag).

`delta.py --inference` prints the rows of one run, or the change in `infer_ms` per workload between two runs.

## Deltas between runs

`delta.py` compares two recorded runs (the JSON files `--json` writes) and prints a markdown table: the median wall time and peak RSS of each run, the ratio between them, and each run's interquartile spread, so a difference that sits inside the noise is visible as such. It is report-only; it never fails on a number.

```bash
uv run python -m benchmarks.baseline.delta base.json head.json
```

The `bench` workflow (`.github/workflows/bench.yml`) runs the suite on a pinned ubuntu-24.04 runner for every release tag, every checkpoint tag and every manual dispatch, writes `bench-<tag-or-sha>.json`, uploads it as a workflow artifact, attaches it to the release for a tag, and puts the delta against the previous tag in the run summary.

Both baselines are recorded on the declared target CPython version (`TARGET_CPYTHON`). `--record` requires the exact target CPython micro, refusing any host micro mismatch so recorded reference files stay on the sanctioned version. `--check` warns on a host micro mismatch but requires the reference file itself to have been recorded on the target CPython version.

## Host slots

`reference.json` is the Linux reference committed in this tree. A full `--record` on Linux replaces it. A partial run belongs in `--json`, so the committed Linux file stays a complete suite.

`reference-macos-arm64.json` is the macOS arm64 slot. On Darwin arm64, `--record` and `--check` use that path, which leaves `reference.json` untouched. Any other host refuses to write the macOS file. The file is produced only by `python -m benchmarks.baseline.run --record` on macOS arm64. No macOS millisecond or RSS numbers are stored in this tree.
