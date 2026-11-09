# M-5: the MIR digest gate (propose; do not land)

Date: 2026-11-06. Epic: #817. Milestone: v0.9.0. Docs only; no code and **no frozen-file edits** in this note or its PR.

How MIR hashes should be recorded beside `TIR_HASHES.json`, what the regen ritual is, and what the nightly should check. The approved backend-only contract already says:

> MIR canonical hashes are recorded in the same new probe file as the HIR hashes or a sibling beside it (`probes/TIR_HASHES.json` and, when R4 lands, a `mir` section or sibling file under the same regen ritual). MIR stability is a backend property, checked the same way: byte-stable across two runs, changed only through the repin ritual with a recorded reason.

This note picks a shape for the owner. It does not regenerate `TIR_HASHES.json`, `IR_HASHES.json`, `probes/*.jsonl`, `probes/IR_HASHES.json`, `probes/TIR_HASHES.json`, or `canonical.py`.

Read first: `src/pyscriptc/mir.py` (`serialize`, `module_serialize`, `module_hash`), `tests/test_mir_leaf.py` (`test_mir_hash_stable`), `benchmarks/compat2/tirscore.py`, `.agents/docs/spec/ir-canonicity.md` §7, `.agents/notes/2026-10-22-ir-hashes-regen.md`, `.agents/notes/2026-11-04-nightly-gates-clock.md`, `.github/workflows/nightly-gates.yml`, `.agents/plans/decisions/2026-11-02-mir-backend-only-contract.md`, `.agents/plans/decisions/2026-11-02-tir-gate-streaming-serializer.md`.

## What exists today

- **HIR/TIR gate:** `probes/TIR_HASHES.json`, driver `benchmarks/compat2/tirscore.py`. Header: `tir_schema`, `canonical_schema`, `hash_algo`, `oracle`, `probes: {id: hex}`. Serialization `json.dumps(..., sort_keys=True, indent=1)` + newline. Exit 0 clean, 1 hash diff, 2 header mismatch. Regen: two runs A and B, `cmp` byte-identical, Linux (ubuntu-24.04, CPython 3.14.x) only. Nightly runs `tirscore --baseline probes/TIR_HASHES.json` and `benchmarks.compat2.fixpoint`.
- **Dict-IR gate:** `IR_HASHES.json` + `irscore.py`, frozen, retirement clock on #830 (14 consecutive green nightlies). Unrelated to MIR; this proposal does not touch it.
- **MIR digest:** `module_hash(module) = sha256(module_serialize(module))`. Canonical text is one line per param/const/block/instr, lists by index, one dict (`helpers`) **omitted** on purpose. Stability is tested on `tests/fixtures/mir_leaf/` by hashing twice in-process (`test_mir_hash_stable`). **No probe file, no nightly, no pinned hex.** Out-of-slice functions are listed on `MirModule.skipped` and are not in `functions`; their bodies are not in the digest.
- **Slice size:** 3 of 22 corpus functions in the MIR slice today. A corpus-wide MIR baseline would be mostly empty skipped-lists until str/list land. That is a reason to keep MIR hashes **out** of the TIR probe map (decision below).

`helpers` omission is load-bearing: two lowerings that disagree on a helper spelling fail LLVM A/B (`test_direct_vs_mir_bytes`) instead of the digest. The gate this note proposes must keep that split: digest = instruction stream; A/B = LLVM text including helper names and `.clo` symbols.

## Proposal

### File: sibling, not a section

Use a sibling file `benchmarks/compat2/probes/MIR_HASHES.json`, not a `mir` key inside `TIR_HASHES.json`.

Reasons:

- TIR regen today rewrites every HIR digest. Folding MIR into that file would move TIR diffs when only `mirlower` changed, and MIR diffs when only `_irnode` changed. The nightly clock on #830 is TIR + fixpoint; mixing MIR into that artifact would reset or muddy the clock.
- The MIR slice is a subset. TIR hashes every probe; MIR can only hash probes whose functions are all in-slice, or hash a per-probe record that includes `skipped`. A section inside TIR would still need a different compare policy (missing key vs mismatch).
- The contract explicitly allows a sibling "under the same header discipline and the same ritual."

Rejected: a `mir` object next to `probes` in `TIR_HASHES.json`. Cheaper one-file grep, worse blast radius.

### Document shape

Mirror TIR's header discipline, with MIR-specific schema tokens:

```
{
  "mir_schema": "mir/1",
  "hash_algo": "sha256",
  "oracle": "<oracle_version()>",
  "slice": "leaf",
  "probes": {
    "<probe_id>": {
      "hash": "<64-hex of module_serialize>",
      "skipped": ["<fn>", "..."]
    }
  }
}
```

`json.dumps(..., sort_keys=True, indent=1)` + trailing newline, same as `tirscore.serialize`. `skipped` is a sorted list so hash-seed iteration order cannot move the file (the serializer already sorts nothing for `helpers`; the baseline file must sort).

- `mir_schema`: versions the probe file. Bump when `module_serialize` changes meaning (new required header field, `arg3` always-printed, const line kinds). Additive optional lines (`jmpbind` omitted when empty, `const N s` only when a string is interned) do **not** bump the schema if existing leaf probes intern no strings; they still move hashes of probes that use the new form.
- `oracle`: same `oracle_version()` as TIR/IR. Header mismatch is exit 2, regenerate, never hand-edit.
- `slice`: a short name of the `supported()` predicate that produced the hashes (`leaf`, later `leaf+str`, …). A slice widening without a schema bump is a recorded regen reason, not a silent compare against the old skipped-sets.
- Per-probe `hash`: `module_hash` of `lower_module` of that probe's module. Empty `functions` (all skipped) still hashes the module header (`module {file} entry=… n=0`). That pins "this probe is entirely out of slice" so a silent slice growth (a function starts lowering) is a mismatch, not an invisible improvement.
- Per-probe `skipped`: copy of `MirModule.skipped`, sorted. Compare policy: hash mismatch is exit 1; skipped-set mismatch is also exit 1 (a function entering the slice is a gate failure until regen). Do not treat skipped-only changes as header exit 2.

Do not include LLVM text. Do not include `fn.helpers`. Do not include HIR.

### Universe

Same probe universe as `tirscore` (`benchmarks/compat2/loader.load_probes` over `benchmarks/compat2/probes`). One driver `benchmarks/compat2/mirscore.py` shaped after `tirscore.py` (generate / compare / `--verify-bytes` analogue: two in-process `module_hash` calls per probe, plus the A/B run-twice file `cmp`).

Do **not** hash the holdout corpus. The holdout stays closed; `mirscore` reads the same probe tree `tirscore` does, nothing under `corpus/holdout`.

Unit fixtures (`tests/fixtures/mir_leaf/`) stay on `test_mir_leaf.py` (LLVM A/B + hash-stable + verify-clean). They are not copied into `MIR_HASHES.json`. The probe file is the corpus gate; the fixtures are the slice gate.

### Regen ritual

Exactly the 2026-10-22 ritual, with `mirscore` in place of `tirscore`:

1. Linux only (ubuntu-24.04, CPython 3.14.x). Never regenerate on macOS.
2. `uv run python -m benchmarks.compat2.mirscore benchmarks/compat2/probes --out /tmp/mir-A.json`
3. Same command `--out /tmp/mir-B.json`
4. `cmp /tmp/mir-A.json /tmp/mir-B.json` must be byte-identical
5. Copy one file to `benchmarks/compat2/probes/MIR_HASHES.json`
6. Record the reason (schema bump, slice widening, `arg3` landing, serializer additive line that actually moved a probe) in the PR body. Same standard as IR hash regen notes.

`--verify-bytes` on `mirscore`: for each probe, `module_hash` twice; additionally, for probes with `skipped == []` (fully in slice), optional LLVM A/B (`emit_llvm` vs `emit_llvm_mir`) behind a flag, because full-corpus A/B is too slow for the default compare path. Default compare is hash + skipped-set only.

Exit codes: 0 clean or generated, 1 any hash or skipped-set diff, 2 header mismatch (`mir_schema` / `oracle` / `hash_algo`).

Hand-editing hex is forbidden (same as TIR). `scripts/repin_census.py` is unrelated; census pins are per-module fence ceilings.

### What the nightly checks

Today (`nightly-gates.yml`): TIR gate + stage-2 fixpoint; artifact line `DATE SHA TIR FIXPOINT`; #830 clock accrues on both success.

Propose, **after** `mirscore` exists and a baseline has been generated on Linux CI (not in this PR):

- Add a step `MIR gate` running `uv run python -m benchmarks.compat2.mirscore benchmarks/compat2/probes --baseline benchmarks/compat2/probes/MIR_HASHES.json` with `continue-on-error: true` (same as TIR).
- Extend the artifact line to five fields: `DATE SHA TIR FIXPOINT MIR`.
- **Do not** fold MIR into the #830 consecutive-green predicate. #830 retires `IR_HASHES.json` in favor of TIR + fixpoint. MIR is a new backend gate; a red MIR step must fail the nightly job (`Check overall result`) so a digest break is visible, but it must not reset the TIR retirement clock. Record that split in the nightly-gates-clock note when the step lands.

PR CI (required checks today: `betterleaks`, `ruff and compat2`, `zig cc emitted-IR smoke`, `wheel install smoke`): do **not** add `mirscore` as a required check until the owner says so. Until then it can run non-required on PRs that touch `src/pyscriptc/mir*.py`, matching how `ir-hashes.yml` compared against `IR_HASHES.json` without being required (#211). Leaf A/B remains required via `tests/test_mir_leaf.py` in the existing pytest job.

`PYTHONHASHSEED`: G-2 (out of this batch's M-* work) pins fixture determinism. `mirscore` should still set `PYTHONHASHSEED=0` in the workflow for belt-and-braces, even though `module_serialize` walks lists by index. The baseline file's `sort_keys=True` is the JSON-side equivalent.

### Interaction with M-1..M-4

| Change | Probe file | Existing leaf `module_hash` |
|---|---|---|
| M-1 `const N s` additive, no header bump | no move until a probe interned a string | unchanged for current fixtures |
| M-2 `arg3` always printed | full regen, reason `arg3` | all leaf hashes move; LLVM must not |
| M-3 `jmpbind` omitted when empty | no move until a try probe | unchanged |
| M-4 `impl` / `callargs` omitted when empty | no move until a closure probe | unchanged |
| `mir_schema` bump | full regen | n/a (fixtures unpinned as hex) |

First generation of `MIR_HASHES.json` should happen **after** the owner accepts this shape and **on Linux CI**, in a follow-on PR that only adds `mirscore.py` + the generated file. That follow-on is G-work adjacent, not this note. This PR must not create the file.

## One-hour tasks (follow-on; not this PR)

1. **`mirscore.py` skeleton.** Generate/compare/exit codes, header checks, no baseline committed. Unit test on a temp dir with two fake probes.
2. **Skipped-set + empty-module hashing.** Pin that an all-skipped probe still has a stable header hash; a function entering the slice fails compare.
3. **Linux CI generate job** (workflow_dispatch, like `ir-hashes.yml`), two runs, `cmp`, upload artifact. Do not write `MIR_HASHES.json` to the repo until the owner copies the artifact.
4. **Commit baseline** in a dedicated PR with the regen reason. Still not a required check.
5. **Nightly step** + five-field artifact + job failure on MIR red, #830 clock unchanged. Update `.agents/notes/2026-11-04-nightly-gates-clock.md`.
6. **Optional `--verify-llvm`** on fully in-slice probes, off by default on nightly.

This note's PR is task 0: the written proposal. Stop here.

## Open decisions for the owner

1. **Sibling `MIR_HASHES.json` vs `mir` section in TIR.** Recommended: sibling. The contract allows either.
2. **Per-probe object `{hash, skipped}` vs two maps `probes` + `skipped`.** Recommended: one object per probe so a probe cannot have a hash without a skipped list. Two maps can drift.
3. **Hash all-skipped modules or omit those probes.** Recommended: hash them (module header with `n=0`). Omitting would hide slice growth.
4. **Nightly fails the job on MIR red, without resetting #830.** Recommended: yes.
5. **When to generate the first baseline.** Recommended: after M-2's `arg3` decision (a post-`arg3` baseline avoids an immediate full regen). If `arg3` is rejected, generate after M-1 lands so `const s` exists. Do not generate in this PR.
6. **Required PR check.** Recommended: not until the slice covers more than the leaf fixtures; `test_mir_leaf.py` is enough while `skipped` is the common case.

Blocked on: owner decisions 1 and 5 before any follow-on writes a probe file. Not blocked on M-1..M-4 for *this* note. Follow-on generation is blocked on a Linux runner and on not touching TIR/IR files.

## Explicitly out of this PR

Edits to `benchmarks/compat2/outcomes.py`, `score.py`, `coverage_map.py`, `cache.py`, `calibrate.py`, `CALIBRATION.json`, `TRIAGE.md`, `probes/*.jsonl`, `probes/IR_HASHES.json`, `probes/TIR_HASHES.json`, `canonical.py`, `mir.py`, `mirlower.py`, `miremit.py`, `mirverify.py`. No `mirscore.py` yet.
