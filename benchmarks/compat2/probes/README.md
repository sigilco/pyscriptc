# compat2 probes

Executable denominator for scorer v2. One JSON object per line, the fields of `outcomes.Probe`; `args` is a JSON list.

Regenerate (Linux only, because generation executes stdlib calls in forked children with a temp cwd):

```
HEAVY=1 .agents/docker/run.sh uv run python -m benchmarks.compat2.probes.generate [--oracle 3.12]
```

The primary oracle is CPython 3.12. Workers run on the oracle interpreter from `PYSC_ORACLE_DIR` (uv installs of 3.10 to 3.14, pinned by patch version in `.agents/docker/Dockerfile`). The secondary set, `--oracle 3.14`, goes to `../oracle-3.14/` (outside this directory, so a directory load never counts it twice) and uses the v3.14.8 pins in `cpython_tests/py314/`.

Output is sorted by id and workers run with `PYTHONHASHSEED=0`, so the same interpreters and mypy typeshed reproduce it byte for byte. `MANIFEST.json` holds counts per area and category, kinds, fold types, mention reasons, GUI exclusions, the language node coverage, the names the oracle does not have (`absent_in_oracle`) and the version axis counts.

## Oracle and version axis

The stdlib modules come from the frozen denominator, filtered by typeshed `stdlib/VERSIONS` for the oracle (`typeshed.available`). Names inside a module come from the oracle at run time, and stub `sys.version_info` branches resolve for the oracle. Modules removed before the oracle (for example `cgi` on 3.13) are not added, since the frozen denominator does not list them.

Every row carries `versions`, the subset of 3.10 to 3.14 where the probe returns the same result as under the oracle (same int, or the same exception for `raises`, or a clean run for `mention`). A probe is run once per interpreter, and once more on the oracle under a second hash seed: rows that are not valid on the oracle or whose result depends on the seed get an empty `versions` and are listed in the manifest (`unverified_on_oracle`, `seed_dependent`).

## Files

| file | area | content |
| --- | --- | --- |
| builtins.jsonl | builtins | every name in `denominators.json` builtins, plus public methods of str, bytes, list, dict, set, tuple, int, float |
| surface.jsonl | surface | public names of all stdlib modules in the frozen denominator, with class methods |
| modules.jsonl | modules | one import probe per stdlib module |
| language.jsonl | language | ast node types by context, plus adapted CPython `Lib/test` cases |

`../continuity/v1equiv.jsonl` holds weight 0.0 probes reproducing the v1 lowered rows. It lives outside this directory on purpose, so `load_probes(<this directory>)` never counts them.

## Probe shape

A probe source is a self-contained module with `def probe(<scalar params>) -> int`. The int is derived from the CPython oracle result by a typed fold (see `fold.py`). Scalar arguments are lifted into parameters (`args`) so the compiler cannot fold them away. The CLI has no argument passing, so a scorer wraps the probe in a zero-argument entry that passes `args` (`calibrate.wrapped` does this).

Fold kinds and what they prove:

- value folds (`int`, `bool`, `float`, `str`, `bytes`, `seq-*`, `len`): the native result equals the oracle.
- `raises`: every candidate call raised the same exception under CPython; the probe expects that exception name.
- `handle`, `exec`, `none`, `shape`: the call compiles and runs with the right static type, but the result itself is not compared (opaque object, nondeterministic value, or no return value). Weak evidence, counted separately in the manifest.
- `mention`: the name could only be referenced. These can never beat partial, and the headroom check must mark them unreachable. Reasons are in the manifest.

## Language profile

Denominator is every concrete `ast` node type of the oracle CPython. Nodes that only exist on a newer version (`TemplateStr`, `Interpolation`) have no 3.12 rows and are listed under `language.newer_nodes` with their first version. Nodes added after 3.10 are tagged in `language.min_version` (abstract bases, parse modes and deprecated nodes are listed as not applicable). Expression nodes get four contexts (assign, arg, cond, loop), statement nodes get function and method. Each probe records its Language Reference page in the manifest `meta`. Every probe is verified under CPython before it is emitted.

`cpython_tests/` holds pinned files from CPython tag v3.12.15 (PSF license), and `cpython_tests/py314/` the v3.14.8 files for the secondary oracle, stored as `.txt` so pytest does not collect them. `cpython_adapter.py` keeps the longest pure prefix of each `test_*` method and turns the assertions into `if not (cond): return 0`.

## Calibration

`calibrate.py` compiles every probe row in the four JSONL files plus `../continuity/v1equiv.jsonl`. It records one outcome per id (`id`, `outcome`, `cell`) and the CPython version that actually ran the comparison.

```
uv run python -m benchmarks.compat2.probes.calibrate --shard 0/8 --workers 2 --out /tmp/pyscriptc-a2-cal/shard-0.json
uv run python -m benchmarks.compat2.probes.calibrate --merge /tmp/pyscriptc-a2-cal --out CALIBRATION.json
```

`--shard I/N` keeps probes whose sorted-by-id index mod N equals I. A sibling cache jsonl lets an interrupted shard resume. `--merge` unions the shard files in id order and rebuilds the aggregates. `--oracle` selects the comparison interpreter. Until the A1 interpreter switch (`generate.interpreter` / `PYSC_ORACLE_DIR`) is on this branch, the only accepted value is the running interpreter; any other version exits non-zero and names that switch.

`triage.py` classifies the v1-lowered fence rows from those per-id outcomes into `TRIAGE.md`. It does not run the compiler. Unclassified fence rows make it exit non-zero.
