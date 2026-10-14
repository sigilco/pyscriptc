# Native libraries

A wheel's `.so` and a C library with a plain ABI are different files. This directory builds both from one checksum, then times the same Python against CPython.

```bash
uv run python -m benchmarks.native.run
```

`libmix.so` exports `mix_crc32` and `mix_crc32_n`. That is the C ABI, the shape of a Rust `cdylib` with `extern "C"`. `nativeprobe*.so` is a Python extension: `nm -D` shows `PyInit_nativeprobe` defined and `mix_crc32` undefined. cryptography, numpy, pydantic-core, and the other native wheels in the top 100 look like `nativeprobe`, not like `libmix`.

pyscriptc has two ways to reach the checksum. `nativeprobe.crc32` is `dlopen` of `libmix.so` from the small runtime (`ps_mix.c`). No libpython. `nativeprobe.crc32_island` asks embedded CPython to import the extension (`ps_island_mix_crc32`), which is how a `PyInit_*` wheel has to run. The import lowers only when `PYSCRIPTC_NATIVEPROBE=1`. A normal compile still fences it.

Five interleaved runs, median wall time, this host. Speedup is `Nx CPython` (higher is faster, or lighter for RSS). The `.so` loop is two million passes so process startup is not the result.

| Workload | What the binary does | Time | RSS | ms, native vs CPython |
| --- | --- | --- | --- | --- |
| `python-direct` | compiled loop, one C-ABI call | 28.0x | 5.5x | 0.6 vs 17.6 |
| `python-island` | compiled loop, one island call | 2.2x | 0.9x | 8.1 vs 17.4 |
| `per-call-direct` | C-ABI call every iteration | 3.9x | 5.4x | 4.0 vs 15.8 |
| `per-call-island` | island call every iteration | 0.8x | 0.9x | 21.2 vs 16.7 |
| `so-direct` | the loop is inside `libmix.so` | 1.0x | 5.4x | 350.1 vs 354.9 |
| `so-island` | one island call into that loop | 1.0x | 0.9x | 353.1 vs 358.4 |

Reading it:

- Compiling the Python and calling a C ABI keeps the small binary. One native call at the end is a 28x run. A native call on every iteration is still about 4x, because the loop around it was the expensive part.
- The same calls through the island link libpython. One call at the end is about 2x and uses more RSS than CPython (0.9x lightness). A call on every iteration has a median of 0.8x: each iteration re-enters the interpreter that was already running the whole program on the baseline. Five runs still leave that interval across 1.
- When the `.so` does the loop, time ties at 1.0x. The direct path is still about 5x lighter, because the process is the small runtime plus `dlopen`. The island path is heavier than CPython. A single sample at 100,000 passes showed 1.6x on the direct path; that was CPython's startup, and it disappeared once the checksum dominated.

So the end-to-end claim holds for the Python around a C ABI, and for the memory of a process that never links libpython. It does not hold for a `PyInit_*` extension on the hot path, and it does not make the `.so` itself faster. Those extensions stay on the island until they also ship a C ABI.
