# R-1: retiring `IR_HASHES.json` and the dict-shape canonical rules (#830)

Date: 2026-11-05. Epic: #817. Issue: #830. Docs only. No frozen files touched.

The recorded decisions this note follows: `.agents/plans/decisions/2026-11-02-tir-gate-streaming-serializer.md` (approved 2026-11-03) and `.agents/plans/decisions/2026-11-02-mir-backend-only-contract.md` (approved 2026-11-03). PR #937 is the owner-signature draft of the layered-gate amendment; this note does not treat #937 as signed.

## What the TIR streaming gate already covers

`benchmarks.compat2.tirscore` hashes the canonical payload `{"schema": canonical-schema/2, "ir": canonicalize(module)}` by streaming SHA-256 over JSON bytes. Contract: `.agents/docs/spec/ir-canonicity.md` section 7 and `pyscriptc.tirserde`.

Covered today:

- Byte identity with `pyscriptc.canonical.hash_module` (`tirscore --verify-bytes`). Bootstrap receipt 2026-11-02: 13,692 probes, 0 mismatches.
- The A/B regen ritual (run twice, `cmp` byte-identical) on `probes/TIR_HASHES.json`.
- Header discipline: `canonical_schema`, `hash_algo`, `oracle`, `tir_schema`, then per-probe digests. Stale oracle is exit 2 (regenerate, do not hand-edit).
- Linux-only generation (ubuntu-24.04, CPython 3.14.x), same host-constant reason as `IR_HASHES.json`.
- Nightly clock: `.github/workflows/nightly-gates.yml` runs tirscore against `TIR_HASHES.json` and `benchmarks.compat2.fixpoint` (self-vs-self per compiler module). Accrual rules: `.agents/notes/2026-11-04-nightly-gates-clock.md`.

The streaming path still calls `canonicalize(module)` on a dict module, then walks that dict. TIR is a new digest of the old payload, not yet a field-order serializer over typed nodes. Section 7.1 of ir-canonicity.md says field order replaces `sort_keys` once the typed schema lands; that switch is not the current `tirserde` payload path (`payload_bytes` still goes through `node_to_dict` then the dict stream).

## What the TIR gate does not cover

- MIR hashes. The MIR backend-only decision puts those in a `mir` section of `TIR_HASHES.json` or a sibling, after R4. Absent until then.
- Dict-key accidents that serialize to the same sorted JSON. `sort_keys=True` hides insertion-order bugs the typed field-order serializer is meant to make visible.
- Island / print / exit extra keys that `dict_to_node` leaves as plain dicts (`_CALL_KEYS` escape in `tirserde.py`). Those bytes stay on the dict path.
- LLVM text, runtime ABI, fence wording, and program stdout. Those have other gates (goldens, `llvverify`, oracle `--run`).
- The structural verifier (`irverify.py`) and the schema validator (`ir.py`). TIR does not replace them.
- macOS host-constant drift. Same as `IR_HASHES.json`: Linux is the oracle host.

Catching one real nondeterminism (the alternate #830 bar) has not happened. Until it does, the clock is consecutive green nightly runs.

## Nightly clock (evidence for #830)

Requirement: 14 consecutive green `nightly-gates` runs on master, both TIR and fixpoint success. Any red run resets the counter.

Measured 2026-11-06 with `gh run list --workflow nightly-gates.yml`:

- 2026-11-05 `0128333f` success (the newest completed run).
- Four earlier 2026-11-05 runs failed (`09f1f072`, `1a201ebd`, `09f1f072` again, `42ab6545`).

Consecutive green as of this note: 1. Matches the plan's "1 so far". The 2026-11-04 start date in the clock note still holds; the same-day failures reset accrual. Do not retire on this evidence.

## Exact order of removal

Do not delete files out of this order. Each step is its own PR after the owner signs #830.

1. Evidence. 14 consecutive green nightly-gates, or one documented real nondeterminism caught by TIR or fixpoint. Write the receipt into the clock note. Owner comment on #830 that the bar is met.
2. Flip the PR gate. `.github/workflows/ir-hashes.yml` today compares `IR_HASHES.json` on PRs that touch `src/pyscriptc/**` and the probe tree (not a required check). Change that workflow to tirscore / `TIR_HASHES.json` (or delete the compare job if nightly-gates plus a required tirscore job already exist). Keep the generate-and-`cmp` dispatch for `TIR_HASHES.json`. Do not delete `IR_HASHES.json` in this step.
3. Stop writing new dict-shape canonical rules. `canonical.py` (167 lines) stays as the payload `tirserde` still canonicalizes. Freeze edits except bugfixes. Class (b) tests in `.agents/notes/2026-11-05-dict-ir-test-inventory.md` keep pinning it.
4. Retire `canonical.py` dict-shape rules only after the serializer walks typed nodes in schema field order (ir-canonicity.md 7.1) and tirscore no longer calls `canonicalize` on a dict. Then `canonical.py` becomes a thin wrapper or goes away. This is after R3 typed HIR is the hash input, not after T-1 tests.
5. `ir.py` schema validators. They refuse JavaScript kinds, check TYPE_KINDS / EXPR_KINDS, stamp `IR_VERSION`. They stay while `--emit ir` writes `json.dumps(module)` and while `validate_module` is the CLI validate stage. Replace with typed-node validation (`irverify` over HIR) in a later PR, then delete the dict validators.
6. Hash files. Delete `benchmarks/compat2/probes/IR_HASHES.json` and the `irscore.py` compare entry point only after step 2 has been green on master for at least one PR cycle and no in-tree caller remains (`rg IR_HASHES` is empty outside notes). `TIR_HASHES.json` stays. Do not hand-edit either file in the deletion PR beyond removing `IR_HASHES.json`.
7. Class (b) tests (`test_canonical.py`, `test_irverify.py` dict builders, `test_irnode_serde.py` dict goldens, emitter seam/package dict fragments). Rewrite or drop in the same wave as steps 4-5, not before.

`0225bcdb` (c-int-parity hash edits) is out of this work. Never include it.

## What CI and nightly change

| Surface | Today | After #830 |
| --- | --- | --- |
| `.github/workflows/nightly-gates.yml` | tirscore + fixpoint + progress report | Unchanged (already the new gate). |
| `.github/workflows/ir-hashes.yml` | irscore vs `IR_HASHES.json`; dispatch regenerates that file | tirscore vs `TIR_HASHES.json`; dispatch regenerates TIR. Then the workflow can be renamed. |
| Required PR checks (`betterleaks`, `ruff and compat2`, `zig cc emitted-IR smoke`, `wheel install smoke`) | Do not include ir-hashes | Still do not, unless the owner makes tirscore required. Owner decision below. |
| `mise` / `ir:hashes` task | wraps irscore compare | Point at tirscore or delete. |
| Nightly artifact `nightly-gate-result` | `DATE SHA gate fixpoint` | Unchanged format. |

## Evidence required before the owner signs

- Clock note updated with 14 consecutive green runs (or a linked TIR/fixpoint catch of real nondeterminism).
- `tirscore --verify-bytes` still 0 mismatches on the visible corpus on the retirement commit.
- Two independent `tirscore --out` runs, `cmp` byte-identical, on that commit (Linux).
- `rg IR_HASHES` and `rg irscore` report only the files the deletion PR removes, plus historical notes.
- No frozen-file edit in the same PR as a lowering change.

## One-hour tasks (after the owner signs)

1. Clock receipt: list 14 runs, paste the artifact lines into `.agents/notes/2026-11-04-nightly-gates-clock.md`.
2. Draft the ir-hashes.yml tirscore switch (compare + generate), no hash file edits.
3. Point mise `ir:hashes` at tirscore; keep the old name or add `tir:hashes`.
4. `rg` callers of `irscore` / `IR_HASHES.json` / `canonical.hash_module` and file follow-up PRs per caller.
5. Delete `IR_HASHES.json` and `irscore.py` once callers are gone (own PR, `Refs #830`).
6. Rewrite `tests/test_canonical.py` against the typed serializer, or delete it if tirscore is the oracle.
7. Docs: `docs/reference/stability.md` and ir-canonicity.md section 1 mark the dict gate historical. User docs stay divergence/gap, not a feature list.

## Owner decisions still open

- Sign #830 (and #937 if the layered-gate amendment is the vehicle) only after the clock or a real catch.
- Make tirscore a required PR check, or leave it nightly-only plus the path-filtered workflow.
- Whether MIR hashes share `TIR_HASHES.json` or a sibling (the MIR decision allows either).
- Flip bar in #937 (draft default 80 percent). This retirement note does not set it.
- Whether `canonical.py` dies in the same PR as `IR_HASHES.json` or one PR later, after the typed field-order serializer is the only hasher.
