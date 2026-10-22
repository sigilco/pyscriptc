# compat2

Scorer v2: typed outcomes per probe, CPython oracle, tiered selection and caching. The shared
contract is `outcomes.py`. `score.py` drives a run; `loader.py` reads probe JSONL; `cache.py`,
`coverage_map.py` and `headroom.py` support the tiers; `verdict.py` judges an iteration.

## Pinned oracle

The CPython oracle is pinned to 3.14. `score.py` resolves the oracle executable in this order: `PYSC_ORACLE` (path to a python3.14 executable), else `PYSC_ORACLE_DIR` (a `cpython-3.14.*/bin/python3` under it), else `uv python find 3.14`, else the running interpreter only if it is already 3.14. Anything else exits with a message naming CPython 3.14, so a run never silently scores against another interpreter. The resolved full version is stamped into the `oracle` field of the score output and into the cache keys.

```
PYSC_ORACLE=$(uv python find 3.14) uv run python -m benchmarks.compat2.score --out /tmp/score.json
```

`--target-version X.Y` still selects a generation-axis interpreter through `PYSC_ORACLE_DIR`. A host interpreter change (for example 3.14.8 to 3.14.9) changes the `oracle` stamp, so `irscore --baseline` reports the `IR_HASHES.json` header as stale (exit 2) and cached oracle answers are recomputed. That is intended: regenerate the baseline with the A/B byte-identical discipline, never hand edit it.

## IR hash baseline (`irscore.py`)

`irscore.py` lowers every probe and records the canonical IR hash per probe, as a lockfile style
baseline. The hash covers the canonical IR form from `pyscriptc.canonical` (`hash_module`): spans,
`sourceFile`, `cpython` and `_notes` stripped, `classes` sorted by name, handler `codes` and
`fences` sorted. It does not cover paths or the interpreter stamp; those live in the header. The
frozen status of the hash semantics is `.agents/docs/spec/ir-canonicity.md`.

Generate:

```
python -m benchmarks.compat2.irscore benchmarks/compat2/probes --out probes/IR_HASHES.json
```

Compare (CI gate, exits 1 on any diff, 2 on a stale header):

```
python -m benchmarks.compat2.irscore benchmarks/compat2/probes --baseline probes/IR_HASHES.json
```

The file is `{"version", "revision", "canonical_schema", "oracle", "hash_algo", "probes"}`, with
`probes` mapping probe id to the 64 hex digest. A `canonical_schema` or `oracle` mismatch means the
baseline is stale: regenerate it, never hand edit it.
