# Engine conformance corpus

The island surface must behave the same on every shipped engine, so the
corpus is a set of island programs that run on each engine and must match
CPython byte for byte (stdout, stderr and the exit code).

## What is in here

| Path | What it holds |
| --- | --- |
| `programs/<name>/` | one corpus program: the island module, the typed entry and the `pyscriptc.toml` that routes the module to the island |
| `engines/<name>.toml` | one engine of the matrix (issue #670) |
| `run.py` | the runner: compiles every program on every engine and byte-compares with CPython |
| `marshalling.py` | the micro-benchmark: the per-call cost of the island seam, per type |
| `summary.py` | the markdown summaries the CI job writes into its step summary |

The programs are the areas the issue names: `fractions`, `json`, `re`,
`dataclasses`, `exceptions`, `bytes` and `lists`.

## Running it

```bash
uv run python -m benchmarks.engine_corpus.run                    # the whole matrix
uv run python -m benchmarks.engine_corpus.run --only re          # one program
uv run python -m benchmarks.engine_corpus.run --engine engines/cpython.toml
uv run python -m benchmarks.engine_corpus.marshalling --calls 20000
```

The matrix is **descriptor-driven**: every `*.toml` under `engines/` is one
engine, so the PyPy leg turns on by itself when #668 ships a PyPy
descriptor — the file lands in the directory and neither the runner nor
the CI workflow needs an edit. A descriptor opts out with a top-level
`conformance = false` key (an engine without an island seam yet); the
runner reports it as skipped and does not fail.

## The CI lane

`.github/workflows/engine-conformance.yml`: one Linux cell runs the matrix
and the marshalling micro-benchmark, writes both reports into the job
summary and uploads them as artifacts. The lane is path-filtered on this
directory, the island runtime and the engine descriptor loader.

## The marshalling numbers

`marshalling.py` measures the seam: the per-call cost of an island call
for each type the ABI carries, against a no-argument baseline that covers
the GIL re-entry, the namespace lookup and the call machinery. Each case
runs a warm-up loop first (the first call pays the embedded interpreter's
initialization) and is then timed five times inside the binary; the best
run is reported.

The numbers are the release receipt for the embed epic, not a gate. The
absolute figures are noise-sensitive — compare shapes across hosts, not
absolutes. The current reading is in `docs/reference/island-engine.md`.
