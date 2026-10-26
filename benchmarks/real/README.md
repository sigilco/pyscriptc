# Real sibling script benchmarks

The eight scripts in the weekly dogfood (`/tmp` notes, issue #592) fit the v0.7.0 gate in support terms: they run with matching output under `pyscriptc run --fallback=cpython`. This directory measures what that costs, on the scripts' own inputs, against `python3`.

`cases.json` lists each script, its argument list and the fixed inputs under `benchmarks/real/<name>/`. The scripts themselves are not copied here: they live in the sibling projects under the corpus root, which defaults to `<project-root>` and can be overridden with `PYSC_REAL_CORPUS`.

```
uv run python benchmarks/real/run.py
```

Each case compiles once with `--emit bin --fallback=cpython`, reads the plane map from the artifact's `.report.json`, runs two warmups and twelve samples of `python3 script` and the compiled binary on the same arguments, interleaved, and writes median, p10, p90, the ratio and the per-sample load average to `results.json`. Compile time and binary size are recorded separately. The run is sequential on purpose; the machine is shared.

A case with a `port` block also compiles the native port in `benchmarks/ports/<name>/` with no `--fallback` and times three runners on one fixed argument list: `python3` on the original, the fallback binary on the original, and the native binary on the port. That block records its own compile time, binary size, plane map and per-runner statistics under `port` in `results.json`.

Two port blocks use an input of their own. `classify` reads a 3200 line prefix of the launch log (`launch-log-3200.txt`), because on the full log the native regex engine raises `RuntimeError: regular expression fuel exhausted` (the fuel limit of [divergence 4](../../docs/reference/divergences.md)). `binary_size` reads `base-large.bin` and `head-large.bin`, a four and five MiB pair from the same fixed LCG fill as the small pair, because on the one and 1.25 MiB pair the three way comparison is dominated by CPython's interpreter startup and module imports rather than by hashing.

When a port block names `compare`, the harness runs each runner once more after the samples and records a SHA-256 of every listed file, so a script whose product is a report file rather than stdout is checked for agreement too.

The inputs are synthetic and deterministic (a fixed LCG for the binaries, generated log, records and session files), so they are safe to commit and to rerun. `check_deploy_secrets` also needs pyyaml on the island path:

```
export PYSC_REAL_PYYAML=$(uv run --with pyyaml python -c 'import yaml, pathlib; print(pathlib.Path(yaml.__file__).parent.parent)')
```

Without it that case is skipped and recorded as such.
