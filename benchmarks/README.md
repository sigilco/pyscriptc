# Benchmarks

These folders follow [scriptc's `benchmarks/`](https://github.com/vercel-labs/scriptc/tree/main/benchmarks). scriptc times a compiled binary against Node. pyscriptc times a compiled binary against CPython, which is the island for code that does not lower.

| scriptc | pyscriptc | What runs today |
| --- | --- | --- |
| `benchmarks/runtime` | `benchmarks/runtime` | one kernel per lowered module, plus the original integer, float, and string loops. `import_module` and `asyncio.run` have no timed call. `fenced-strip` stays a fence |
| `benchmarks/scaling` | `benchmarks/scaling` | the original loops across sizes. scriptc's array, collection, regex, and async probes need types this subset does not lower |
| `benchmarks/builds` | `benchmarks/builds` | catalog only. `log-summary` and `inventory-report` are multi-module programs |
| | `benchmarks/baseline` | reference time and peak RSS. The kernel geomean is the original four loops. `stdlib`, `scripting`, and `island` have their own geomeans |
| | `benchmarks/adoption` | which outside test suites can be compiled, and the fences that keep them on CPython |
| | `benchmarks/native` | a C ABI `.so` linked from the small runtime, and the same work through a `PyInit_*` extension on the island |
| | `benchmarks/compat` | frozen language, builtin, stdlib, and top-100 denominators. `python -m benchmarks.compat.score` writes a snapshot. `python -m benchmarks.compat.delta` compares two snapshots |
| | `benchmarks/net` | the network receipt: loopback HTTP client, compiled against CPython, and sustained `http.server` throughput with p50/p99. Opt-in, no baseline gate

```bash
uv run python -m benchmarks.baseline.run --check
uv run python -m benchmarks.runtime.run --runs=5
uv run python -m benchmarks.scaling.run
uv run python -m benchmarks.native.run
uv run python -m benchmarks.compat.score --out benchmarks/compat/iterations/NNN.json
uv run python -m benchmarks.compat.delta
uv run python -m benchmarks.net.run
```

A row is timed only after its stdout matches CPython. A call outside the lowered subset, or any syntax outside `gates-v1`, is a fence: the runner records it and does not emit a time.
