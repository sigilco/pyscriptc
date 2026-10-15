# C9 sweep: generation A/B repro on the 3.12 corpus

Verdict: generation is byte-identical across two fresh runs, and the test_gen_repro substance is byte-identical at the worker level.

## Method

`test_gen_repro` is `linux_container`-marked, so its substance was replicated off-container on Linux, branched off 823654f.

Two fresh `python -m benchmarks.compat2.probes.generate --oracle 3.12 --out <dir>` runs under `PYSC_ORACLE_DIR=~/oracles` wrote all six artifacts to /tmp/c9/gen-a and /tmp/c9/gen-b, then every file was compared with `cmp`.

For the worker half, `python -m benchmarks.compat2.probes.surface <mod>` was run twice per module for random, secrets, time, os, posix with the same env the test uses (`PYTHONPATH=ROOT`, `PYSC_TYPESHED`, `PYTHONHASHSEED=0`, `PYTHONDONTWRITEBYTECODE=1`) and stdout byte-compared.

## Results

All six generation artifacts are byte-identical between runs A and B: builtins.jsonl, surface.jsonl, modules.jsonl, language.jsonl, v1equiv.jsonl, MANIFEST.json. Each run took about 16 minutes and produced 13692 rows across the same area counts (builtins 403, surface 12657, modules 192, language 440), plus 1233 rows in the v1 continuity file.

All five worker modules are byte-identical across two runs each: random 76 rows, secrets 33, time 53, os 499, posix 380.

Run A output also byte-matches the committed corpus: the four `probes/*.jsonl` files and `continuity/v1equiv.jsonl` are identical, so the worktree stayed clean after generation. No drift between what generation emits today and what is committed.

## Guard compliance

No writes under benchmarks/compat2/probes/, no frozen-list edits, no merges or tags, no container runs. Report-only.
