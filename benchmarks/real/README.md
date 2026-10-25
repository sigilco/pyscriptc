# Real sibling script benchmarks

The eight scripts in the weekly dogfood (`/tmp` notes, issue #592) fit the v0.7.0 gate in support terms: they run with matching output under `pyscriptc run --fallback=cpython`. This directory measures what that costs, on the scripts' own inputs, against `python3`.

`cases.json` lists each script, its argument list and the fixed inputs under `benchmarks/real/<name>/`. The scripts themselves are not copied here: they live in the sibling projects under the corpus root, which defaults to `~/Documents/prjcts/_own` and can be overridden with `PYSC_REAL_CORPUS`.

```
uv run python benchmarks/real/run.py
```

Each case compiles once with `--emit bin --fallback=cpython`, reads the plane map from the artifact's `.report.json`, runs two warmups and twelve samples of `python3 script` and the compiled binary on the same arguments, interleaved, and writes median, p10, p90, the ratio and the per-sample load average to `results.json`. Compile time and binary size are recorded separately. The run is sequential on purpose; the machine is shared.

The inputs are synthetic and deterministic (a fixed LCG for the binaries, generated log, records and session files), so they are safe to commit and to rerun. `check_deploy_secrets` also needs pyyaml on the island path:

```
export PYSC_REAL_PYYAML=$(uv run --with pyyaml python -c 'import yaml, pathlib; print(pathlib.Path(yaml.__file__).parent.parent)')
```

Without it that case is skipped and recorded as such.
