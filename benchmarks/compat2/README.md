# compat2

Scorer v2: typed outcomes per probe, CPython oracle, tiered selection and caching. The shared
contract is `outcomes.py`. `score.py` drives a run; `loader.py` reads probe JSONL; `cache.py`,
`coverage_map.py` and `headroom.py` support the tiers; `verdict.py` judges an iteration.

## IR hash baseline (`irscore.py`)

`irscore.py` lowers every probe and records the canonical IR hash per probe, as a lockfile style
baseline. The hash covers the canonical IR form from `pyscriptc.canonical` (`hash_module`): spans,
`sourceFile`, `cpython` and `_notes` stripped, `classes` sorted by name, handler `codes` and
`fences` sorted. It does not cover paths or the interpreter stamp; those live in the header. The
frozen status of the hash semantics is `docs/ir-canonicity.md`.

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
