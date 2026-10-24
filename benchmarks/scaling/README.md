# Scaling probes

scriptc's `benchmarks/scaling/run.mjs` builds each probe, runs every case at increasing sizes under the compiled binary and under Node, checks stdout, and reports in-process time from a `T=<ms>` line on stderr.

`numeric.py` is the part of that suite that lowers. Cases:

| Case | Sizes | Growth |
| --- | --- | --- |
| `sum_squares` | 2e4, 2e5, 1e6, 2e6 | linear integer loop, kept inside i64 |
| `floor_sum` | same | linear `//` |
| `horner` | same | linear float recurrence |
| `repeat_len` | 100, 200, 400, 800 | quadratic concatenation |

`data.py` (and individual files in `workloads/`) adds the data-shaped cases:

| Case | Sizes | Growth |
| --- | --- | --- |
| `dict_grow` | 1e4, 2e4, 5e4, 1e5 | linear map insertion and key scan |
| `set_grow` | same | linear set addition and iteration |
| `dict_lookup` | same | linear map lookup |
| `list_append` | 2e4, 5e4, 1e5, 2e5 | linear sequence append |
| `json_loads` | 2e3, 5e3, 1e4, 2e4 | linear JSON object parse |
| `str_build` | 1e4, 2e4, 5e4, 1e5 | linear string list join |
| `copyfile` | 1e5, 2e5, 5e5, 1e6 | linear file copy throughput |
| `sha256_blocks` | 5e3, 1e4, 2e4, 5e4 | linear SHA-256 block hashing |

Wall time includes process startup. This subset has no clock, so there is no in-process `T=` line. The exponent is still taken from the two largest sizes. The line prints `speed=` as `Nx CPython`. The `>3x` flag still means the stored native/CPython ratio of the largest size is above 3. The other flags match scriptc's names: `FAIL`, `MISMATCH`, and `SUPERLINEAR`.

```bash
uv run python -m benchmarks.scaling.run --only=numeric:sum_squares --reps=3 --json=scaling.json
```

Arguments are baked into the binary. The runner compiles once per size. scriptc's probes read `argv` instead; that is why one binary there covers every size.

Not lowered, and not timed: `arrays.ts`, `strings.ts` beyond concatenation, `collections.ts`, `misc.ts`, and `async-io.ts`.
