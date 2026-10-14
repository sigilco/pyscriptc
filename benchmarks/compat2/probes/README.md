# compat2 probes

Executable denominator for scorer v2. One JSON object per line, the fields of `outcomes.Probe`; `args` is a JSON list.

Regenerate (Linux only, because generation executes stdlib calls in forked children with a temp cwd):

```
.agents/docker/run.sh uv run python -m benchmarks.compat2.probes.generate
```

Output is sorted by id, so the same CPython 3.14 and mypy typeshed reproduce it byte for byte. `MANIFEST.json` holds counts per area and category, kinds, fold types, mention reasons, GUI exclusions and the language node coverage.

## Files

| file | area | content |
| --- | --- | --- |
| builtins.jsonl | builtins | every name in `denominators.json` builtins, plus public methods of str, bytes, list, dict, set, tuple, int, float |
| surface.jsonl | surface | public names of all stdlib modules in the frozen denominator, with class methods |
| modules.jsonl | modules | one import probe per stdlib module |
| language.jsonl | language | ast node types by context, plus adapted CPython `Lib/test` cases |

## Probe shape

A probe source is a self-contained module with `def probe(<scalar params>) -> int`. The int is derived from the CPython oracle result by a typed fold (see `fold.py`). Scalar arguments are lifted into parameters (`args`) so the compiler cannot fold them away. The CLI has no argument passing, so a scorer wraps the probe in a zero-argument entry that passes `args` (`calibrate.wrapped` does this).

Fold kinds and what they prove:

- value folds (`int`, `bool`, `float`, `str`, `bytes`, `seq-*`, `len`): the native result equals the oracle.
- `raises`: every candidate call raised the same exception under CPython; the probe expects that exception name.
- `handle`, `exec`, `none`, `shape`: the call compiles and runs with the right static type, but the result itself is not compared (opaque object, nondeterministic value, or no return value). Weak evidence, counted separately in the manifest.
- `mention`: the name could only be referenced. These can never beat partial, and the headroom check must mark them unreachable. Reasons are in the manifest.

## Language profile

Denominator is every concrete `ast` node type of the pinned CPython (abstract bases, parse modes and deprecated nodes are listed as not applicable). Expression nodes get four contexts (assign, arg, cond, loop), statement nodes get function and method. Each probe records its Language Reference page in the manifest `meta`. Every probe is verified under CPython before it is emitted.

`cpython_tests/` holds pinned files from CPython tag v3.14.8 (PSF license), stored as `.txt` so pytest does not collect them. `cpython_adapter.py` keeps the longest pure prefix of each `test_*` method and turns the assertions into `if not (cond): return 0`.

## Calibration

`calibrate.py` compiles a sample of probes with the current compiler and reports pass, fence and wrong-output per area, category and kind, plus the v1 lowered rows of the newest frozen iteration.
