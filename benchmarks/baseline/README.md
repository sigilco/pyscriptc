# Baseline

Reference metric set for the workloads `pyscriptc` compiles today. Each sample is one process. The runner records wall time and peak resident set size (`VmHWM`) for the compiled binary and for CPython, and it times nothing whose stdout disagrees with CPython. `ru_maxrss` is not the source: on this host it follows the cgroup high-water mark, so a small binary and CPython report the same number.

`reference.json` is that set, recorded on one Linux host. Absolute milliseconds and kilobytes belong to that host. The runner prints speedups as `Nx CPython` (higher is better). The stored ratio, and the check, stay pyscriptc / CPython, so a later machine is not asked to hit the same millisecond. A run fails when a time ratio grows past 2× the reference, or an RSS ratio grows past 1.25×. The time band is wide because these kernels finish in under a millisecond.

The kernel geomean is still the original four loops (`sum-squares`, `floor-sum`, `horner`, `repeat-len`). Every other lowered module has its own row. `group_geomeans` holds `stdlib`, `scripting`, and `island`. The island group links libpython, so its RSS speedup sits near 1x CPython. A full `--check` compares each row and each group geomean with this file.

Fenced programs stay out of this set. They have no native binary to time.

```bash
uv run python -m benchmarks.baseline.run --check
uv run python -m benchmarks.baseline.run --record
```

`--check` reads `reference.json`. `--record` replaces it with the run just taken. The report also keeps compile time and the linked binary's size; those are not part of the ratio gate.
