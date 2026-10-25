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

Both baselines are recorded on the declared target CPython version (`TARGET_CPYTHON`). `--record` requires the exact target CPython micro, refusing any host micro mismatch so recorded reference files stay on the sanctioned version. `--check` warns on a host micro mismatch but requires the reference file itself to have been recorded on the target CPython version.

## Host slots

`reference.json` is the Linux reference committed in this tree. A full `--record` on Linux replaces it. A partial run belongs in `--json`, so the committed Linux file stays a complete suite.

`reference-macos-arm64.json` is the macOS arm64 slot. On Darwin arm64, `--record` and `--check` use that path, which leaves `reference.json` untouched. Any other host refuses to write the macOS file. The file is produced only by `python -m benchmarks.baseline.run --record` on macOS arm64. No macOS millisecond or RSS numbers are stored in this tree.
