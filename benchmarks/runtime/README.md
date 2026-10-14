# Runtime benchmarks

Fixed-size workloads. Each one is deterministic. The runner compiles it with pyscriptc, runs CPython on the same function and arguments, and times the binary only when stdout matches. Float results are printed with `%.17g` on both sides, which is the format the LLVM driver uses.

| Workload | Group | What it exercises |
| --- | --- | --- |
| `sum-squares`, `floor-sum`, `horner`, `repeat-len` | kernel | the original arithmetic and string loops. Their geomean is the regression gate |
| `math-loop` through `builtins-loop` | stdlib | one deterministic checksum per lowered module, including builtin helpers |
| `script-slice` | scripting | a short `json` + `re` + `pathlib` script, the slice of pyperformance's scripting set that lowers |
| `socket-host`, `cleandoc`, `threads`, `log-info`, `echo-out` | island | calls that embed CPython and link libpython |
| `fenced-strip` | fence | `str.encode` with an encoding name, which must not be timed |

`importlib.import_module` and `asyncio.run` are fences, so they have no kernel. `unicode-category`, `hmac-digest`, `resource-read`, `temp-dir`, `shutil-copy`, and `atexit-reg` time the closed names that do lower. `inherit-sum`, `set-sum`, and `args-sum` time single inheritance, `set`, and `*args`. Fences are not timed. `reference.json` is scriptc's workload list scored against `gates-v1`: `partial` where a pyscriptc kernel covers one piece, `fenced` where the program still has to run on CPython.

```bash
uv run python -m benchmarks.runtime.run --runs=5 --workloads=sum-squares,horner --json=result.json
```

Samples are interleaved. The stored ratio is median(native) / median(CPython). The printed figure is the reciprocal, `Nx CPython`, and the confidence interval is inverted the same way. A `faster` or `slower` verdict still requires the uninverted bootstrap 95% interval to sit entirely on one side of 1. The summary prints the geomean as `Nx CPython` for workloads that matched. Fences are checked and left out of that geomean.

scriptc can also compare two compiler checkouts (`--baseline` / `--candidate`) and can time a listening server. This runner's baseline is CPython, and there is no server workload yet.
