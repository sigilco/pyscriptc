# Draft recorded decision: rewrite the stdlib.sys.argv probe to drop the argv[0] dependence

Status: draft, awaiting owner signature

Issue: #924 (last remaining red row of the 2026-11-05 nightly triage, `.agents/notes/2026-11-07-924-triage.md`).

## Decision (proposed)

Rewrite the `stdlib.sys.argv` probe source from `return len(r) * 1000003 + len(r[0])` to `return len(r) * 1000003 + (1 if len(r[0]) > 0 else 0)`, in both `benchmarks/compat2/probes/surface.jsonl` and the matching row of the secondary oracle set `benchmarks/compat2/oracle-3.14/surface.jsonl`. The probe keeps covering `sys.argv` but no longer hashes `len(sys.argv[0])`, so the result does not depend on the invocation path.

## Why

`sys.argv[0]` is invocation-dependent by design: the scorer's CPython oracle runs `python -c` so `argv[0]` is `'-c'` (len 2), while the native binary honestly reports its own path (len 27 under the scorer's tmpdir). CPython itself gives different answers for `-c`, `script.py` and `-m`, so no single native value can match the oracle's. The old fold `len(r) * 1000003 + len(r[0])` baked that dependence into the expected value, which is why the row is permanently red: native `1000030` vs CPython `1000005` on every run, on every interpreter.

The new fold keeps the two things that are stable across invocations: the argument count `len(r)` and whether `argv[0]` is non-empty. Under the scorer both sides run with argc 1 (oracle `python -c`, native the bare compiled binary), so both produce `1000004`. Under the version axis (`probes/versions.py`) every interpreter sees the same argv shape (`[versions.py, probes.jsonl]`, argc 2), so the row's `versions` field is unchanged and stays `["3.10", "3.11", "3.12", "3.13", "3.14"]`.

## Rejected alternative

Exclude the row from the probe set. Rejected because `stdlib.sys.argv` is the only `sys.argv` coverage in the corpus (`stdlib.sys.orig_argv` is fenced, `PS2001`), and dropping it would silently shrink the denominator rather than fix the comparison.

## Frozen-gate consequences

This touches frozen gate files, so the change rides as a draft PR that must not merge without the owner's signature:

- `benchmarks/compat2/probes/surface.jsonl` and `benchmarks/compat2/oracle-3.14/surface.jsonl`: the probe `source` string only; `versions` verified unchanged (see above).
- `benchmarks/compat2/probes/IR_HASHES.json` and `benchmarks/compat2/probes/TIR_HASHES.json`: regenerated with the A/B byte-identical ritual (two `irscore`/`tirscore` runs, `cmp`, copy). Only the `stdlib.sys.argv` row moves.
- `benchmarks/compat2/probes/CALIBRATION.json`: not regenerated. The committed file is a sweep artifact (last written by the calibration sweep, oracle stamp `3.14.8` on the older corpus), nothing in CI or tests validates it against the current probe set, and the probe's outcome cell (`surface/constant/value`) is unchanged; whether its per-id outcome moves is a sweep-time question, not a per-PR one.

Note on the oracle file: `oracle-3.14/surface.jsonl` is a generated artifact (`uv run python -m benchmarks.compat2.probes.generate --oracle 3.14`, the HEAVY docker path), but the generator emits its own fold shapes and cannot emit this hand-written probe. The row is mirrored byte-identically in both files, matching how the two sets already share identical row bodies where they overlap.

## What reopens the decision

If the scorer ever invokes either side with a different argc (for example passing probe args through argv), the `len(r)` term breaks the same way `len(r[0])` did, and the probe needs another rewrite or exclusion.
