# R-1: retire `IR_HASHES.json` and the dict-shape canonical rules (#830)

Date: 2026-11-06. Epic: #817. Milestone: v0.9.0. Docs only; no code and no frozen-file edits in this note or its PR.

Issue #830 is the R5a owner decision: retire the dict-IR canonicity gate after the streaming TIR gate has either been byte-stable A/B on the full visible corpus through the migration and green for two calendar weeks, or has caught one real nondeterminism. This note is the retirement path, not the retirement. It does not edit `IR_HASHES.json`, `TIR_HASHES.json`, `canonical.py`, `ir.py`, probes, or CI.

Read first: `.agents/plans/decisions/2026-11-02-tir-gate-streaming-serializer.md` (approved 2026-11-03), `.agents/plans/decisions/2026-11-02-mir-backend-only-contract.md`, `.agents/docs/spec/ir-canonicity.md` section 7, `.agents/notes/2026-10-22-ir-hashes-regen.md`, `.agents/notes/2026-11-04-nightly-gates-clock.md`, `.agents/notes/2026-11-06-mir-digest-gate.md` (M-5 sibling, not this file), `.github/workflows/ir-hashes.yml`, `.github/workflows/nightly-gates.yml`. PR #937 is the owner-signature draft of the layered-gate amendment; this note does not treat it as signed and does not set its flip bar.

Handoff 5 Group T owns the test inventory and migrations. Class (b) tests that pin a dict shape (canonical, IR hash, serialization) stay until the steps below say they go. This note does not migrate them.

## Measured on master (2026-11-06)

- `benchmarks/compat2/probes/IR_HASHES.json`: 1.4 MB, 13,692 probes. Header keys: `canonical_schema`, `hash_algo`, `oracle`, `probes`, `revision`, `version`.
- `benchmarks/compat2/probes/TIR_HASHES.json`: 1.4 MB, 13,692 probes. Header keys: `canonical_schema`, `hash_algo`, `oracle`, `probes`, `tir_schema`.
- `src/pyscriptc/canonical.py`: 167 lines (strip, sort, path scrub, `hash_module` via `json.dumps`).
- `src/pyscriptc/ir.py`: 518 lines (`validate_module` dict schema, `IR_VERSION`, type kinds).
- `src/pyscriptc/irverify.py`: 392 lines (soundness over the dict IR).
- `src/pyscriptc/tirserde.py`: 949 lines. `hash_module` still calls `canonicalize` on a dict. `hash_module_node` reconstructs the dict (`node_to_dict`) and hashes that.
- Drivers: `irscore.py` 214 lines, `tirscore.py` 245 lines. Both lower through `irscore._lower_one` (dict IR).
- Stage-2: `fixpoint.py` hashes with `pyscriptc.canonical.hash_module`, not `tirserde`.
- Other dict-hash callers in-tree (not tests): `benchmarks/compat2/fndigest.py`. Compiler import of `canonical` besides `tirserde`: none.
- Class (b) test files that pin this surface (do not edit here; T-0 classifies): `tests/test_canonical.py` (283), `tests/test_irscore.py` (254), plus `hash_module` goldens in `tests/test_e1_entry.py` and `tests/test_e1_append.py`.
- CI: `.github/workflows/ir-hashes.yml` compares `IR_HASHES.json` on PRs that touch `src/pyscriptc/**`, probes, `canonical.py`, or `irscore.py`. Not a required check. No `tir-hashes.yml`. `ci.yml` does not run `irscore` or `tirscore`. `mise run ir:hashes` wraps the IR compare; there is no `tir:hashes` task.
- Nightly clock (`nightly-gates.yml`, cron 03:17 UTC, always checks out `master`): TIR via `tirscore --baseline` plus stage-2 `fixpoint`. Artifact `nightly-gate-result` is one line: date SHA TIR-outcome fixpoint-outcome. Consecutive green on master as of this note: **1**. The green run is `42015994856` (workflow_dispatch, 2026-11-05, SHA `0128333f`). The scheduled run immediately before it on `09f1f072` was red (`gate=failure`, `fixpoint=success`). Earlier dispatch runs on master the same day were also red. Any later red run resets the counter to zero. Today's scheduled run had not fired at write time (before 03:17 UTC on 2026-11-06).

## What the TIR streaming gate already covers

Same probe universe as `irscore` (13,692 visible probes; the holdout is never read). Same SHA-256. Same `canonical-schema/2` payload contract when hashing a dict: `tirscore --verify-bytes` requires streaming bytes equal `json.dumps` bytes. Same Linux-only two-run `cmp` regen ritual, same oracle stamp, same exit 2 on a stale header. Streaming path never materializes the payload string (accepted 1.9x wall time vs dict `json.dumps` on the 2026-11-02 bootstrap receipt). Nightly runs the compare on master every day. The gate can fail a hash mismatch (exit 1) independently of `IR_HASHES.json`, which is why slices were allowed to leave the 1.4 MB IR file untouched.

## What it does not cover

1. **Typed field-order hashing.** Section 7 of `ir-canonicity.md` says the typed-IR contract is node id then schema field order, with field order replacing `sort_keys`. On master, `tirserde.hash_module` still runs `canonicalize` on a dict, and `hash_module_node` hashes `node_to_dict(module)`. The TIR file is a second digest of the dict canonical form, not yet a digest of typed nodes. Retiring the dict-shape rules while this is true would leave the TIR gate calling deleted code. `sort_keys=True` on the dict payload also hides insertion-order bugs the typed serializer is meant to make visible.
2. **Lossless dict leftovers.** `dict_to_node` leaves some `call` nodes as plain dicts when extra keys sit outside `_CALL_KEYS` (island `island_fn` / `island_npos` / `island_nkw`, print `sep`/`end`/`file`, `sys.exit` `exitMessage`, reduce `fn`, `json_flags`). Those bytes stay on the dict path. A typed-only hasher that dropped them would move TIR digests; stop and write a blocker rather than regen.
3. **PR-path enforcement.** `ir-hashes.yml` is the only pull-request compare, and it is not required. TIR is nightly-only. A merge can land with a moved TIR digest if nightly has not run, and the old IR compare can still be skipped by path filters that miss a lowering change's true blast radius. Retirement of `IR_HASHES.json` without a TIR PR job would drop the only on-PR IR digest check.
4. **Stage-2 hashes.** `fixpoint.py` uses `canonical.hash_module`. The nightly clock requires fixpoint green, but that green is still a dict-canonical signal. Switching the hasher is a prerequisite of deleting `canonical.py`, not of turning off `ir-hashes.yml`.
5. **Schema and soundness.** `ir.py.validate_module` and `irverify.py` are not hash oracles. They reject bad dict shape and unsound modules on the compile path (`cli`, `emitter`). The TIR gate does not replace them. They retire with the dict IR, after consumers and tests stop producing it (handoff 4, Group T).
6. **Behavior, LLVM, MIR.** Compat2 stdout, `tests/test_emitter_goldens.py`, and the proposed `MIR_HASHES.json` sibling (M-5) are different contracts. M-5 already requires that a MIR nightly red must not reset the #830 TIR clock. Do not fold MIR into `TIR_HASHES.json` as a way to retire IR hashes.
7. **Host constants.** Canonical IR still folds Linux vs macOS constants (`socket`, `errno`, `os`, `ssl`, `time`, `sys`, `ctypes`, `pathlib`). TIR inherits that. Regen stays Linux-only. A macOS `tirscore --baseline` is noise, not a retirement signal.
8. **Environment-derived probe noise.** Slice receipts recorded two `irscore` diffs (`stdlib.sys.base_exec_prefix`, `stdlib.sys.base_prefix`) that reproduce on unmodified master. Those are not TIR catching nondeterminism. Stale-TIR diffs in the hundreds (r3b/r3c/r3e receipts, then red nightlies on 2026-11-05 before `0128333f`) are baseline drift, not the "one real nondeterminism" criterion.
9. **Incidental-key policy after `canonicalize` dies.** Today spans, `sourceFile`, `cpython`, `_notes` are stripped; `classes` and handler `codes` are sorted; fences are totally ordered; environment paths become `<PATH>`. A typed serializer that omits non-schema fields and emits fields in schema order still needs an explicit home for path scrub and for any remaining order leaks in lowering. Those rules are not free just because the payload is no longer a dict.
10. **On-disk dict JSON.** `--emit ir` and `--debug-dir ir.json` remain dict dumps. R-3 inventories serializers. Deleting `canonical.py` does not delete that format; it only deletes the hash-side strip/sort unless `--emit ir` is switched to the streaming/typed payload in the same drop.

## Exact order of removal

Do not reorder. Each step is a separate PR after the owner signs #830, except the prerequisites which are allowed before the signature when they do not delete the old gate.

### Prerequisites (before the #830 signature)

P0. Accrue 14 consecutive green `nightly-gates` runs on master, or record one real nondeterminism (definition below). Audit with `gh run list --workflow nightly-gates.yml` and the `nightly-gate-result` artifact. Clock at write: 1 of 14.

P1. TIR PR compare exists and is green on the same corpus: a `tir-hashes.yml` (or an extra job in `ir-hashes.yml`) that runs `tirscore --baseline` on the same path filter as today's IR job, plus `workflow_dispatch` generate A/B `cmp` for `TIR_HASHES.json`. Add `mise run tir:hashes`. Do not make it a required check in the same PR as the first landing unless the owner says so; matching today's IR job (non-required) is enough to start.

P2. `tirserde.hash_module_node` hashes typed nodes in schema field order without `node_to_dict` + `canonicalize`. Prove corpus byte-identity against the then-current TIR baseline (or a recorded regen). Path scrub, class order, handler `codes` order, and fence order must have an owner: either deterministic lowering or the typed serializer. Until this lands, `canonical.py` cannot be deleted.

P3. `fixpoint.py` hashes with the TIR hasher (`tirserde`), still twice per module. Nightly stays TIR + fixpoint. A hasher switch that moves digests is a receipt, not a frozen `IR_HASHES.json` edit.

P4. Group T has classified every `"kind"` test. Class (b) files that exist only to pin `canonicalize` / `irscore` are listed and left in place until D2/D3. Do not weaken them.

### After the owner signs #830

D1. **Gate.** Stop the old compare. Delete or empty the PR job in `ir-hashes.yml` that runs `irscore --baseline`. Convert generate mode to TIR if P1 did not already. Point `mise run ir:hashes` at `tir:hashes` or delete the alias in the same PR. `ci.yml` required checks (`betterleaks`, `ruff and compat2`, `zig cc emitted-IR smoke`, `wheel install smoke`) do not change. Nightly keeps TIR + fixpoint; it does not start running `irscore`.

D2. **`canonical.py` rules.** Delete `src/pyscriptc/canonical.py` only after P2 and P3 and after `tirserde` no longer imports it (`CANONICAL_SCHEMA_VERSION` moves into `tirserde` or `_irnode`). `fndigest.py` switches to the TIR hasher or is deleted if unused. `tirscore --verify-bytes` drops the `json.dumps` dict path or redefines identity as two TIR streams. Class (b) tests in `tests/test_canonical.py` that exist only for strip/sort/`sort_keys` retire here; T-1 does not migrate them earlier. Frozen-list update in `AGENTS.md`: drop `canonical.py` rules and `probes/IR_HASHES.json`; keep `TIR_HASHES.json` as the frozen canonicity gate (recorded decision, this issue). Mark sections 1-6 of `ir-canonicity.md` and the IR JSON paragraph in `docs/reference/stability.md` historical in that PR, as a divergence/gap update, not a feature list.

D3. **`ir.py`.** Retire `validate_module` and the dict key/kind tables after no compile path still hands a module dict to the CLI or emitter. `IR_VERSION` / `SUBSET` / `assert_same_cpython` / `fits_i64` move to the typed HIR module or stay as a thin ABI stamp file; that is not "keeping dict IR". `irverify.py` is rewritten against typed nodes or replaced; it is not a hash-file problem. This step is blocked on handoff 4 (dict-IR consumers) and on Group T finishing class (a). It is not blocked on deleting the JSON file.

D4. **Hash files.** Delete `benchmarks/compat2/probes/IR_HASHES.json` and `benchmarks/compat2/irscore.py` last, when nothing in CI, mise, or docs still names them. `flipcheck.py` comments that parse irscore output need a TIR-shaped achieved-flip reader in the same PR or earlier. Do not hand-edit the file down; delete it. Do not regenerate it as part of the deletion PR.

`ir.py` before the JSON file is deliberate: while dict IR still ships, the 1.4 MB file is frozen history an owner can still diff against a fire-drill `irscore --out`. Once `validate_module` is gone, the file cannot be produced by the live compiler in the old shape and is only a cost.

## What CI and the nightly change

| Surface | Today | After P1 | After D1 | After D4 |
| --- | --- | --- | --- | --- |
| PR `ir-hashes.yml` compare | `irscore` vs `IR_HASHES.json`, not required | still present | gone | gone |
| PR TIR compare | none | `tirscore` vs `TIR_HASHES.json`, not required unless owner flips | the on-PR digest gate | same |
| `ir-hashes.yml` generate | A/B `irscore` artifact | TIR generate added or split | TIR only | TIR only |
| `nightly-gates.yml` | TIR + fixpoint; red resets #830 | unchanged (hasher of fixpoint may already be TIR from P3) | unchanged | unchanged |
| `nightly.yml` (corpus score) | unrelated | unrelated | unrelated | unrelated |
| `ci.yml` | no digest job | no digest job | no digest job | no digest job |
| `mise run ir:hashes` | IR compare | keep until D1 | alias or delete | delete |
| Required checks | no digest among them | no change unless owner requires TIR | no change | no change |

M-5 MIR nightly, if landed, is a third step that must not reset this clock (already recommended there).

## Evidence required

The 2026-11-02 decision: new gate byte-stable A/B on the full corpus through the migration, **and** green two weeks, **or** one real nondeterminism caught.

**Two weeks** means 14 consecutive green `nightly-gates` runs on `master`, both steps success. Dispatch runs on `master` count. Runs on other branches do not. A red TIR compare or a red fixpoint resets to zero. A stale-header exit 2 is red and resets; it is not "nondeterminism caught". Oracle micro bumps that fail the header are regen work, not clock credit.

**One real nondeterminism** means a nightly (or A/B `cmp`) failure whose diff is run-to-run IR divergence at fixed source and fixed oracle: hash-seed order leak, counter drift, unset canonicalization. It is evidenced by the artifact line plus the `CHANGED` list, then a fix. It is not: a recorded lowering change, a stale `TIR_HASHES.json` vs master, the two `sys.base_*` env diffs, a macOS host-constant mismatch, or a probe added/removed. Until one such catch exists, the 14-run clock is the only path.

**A/B through the migration** is already the slice ritual (`tirscore` twice, `cmp`). Retirement additionally needs the PR TIR job (P1) so the nightly is not the only watcher.

Clock at this note: 1 of 14, no nondeterminism catch on file. #830 cannot be signed on this evidence.

## One-hour tasks (implementation, not this PR)

1. **P1 workflow.** Add TIR compare + generate beside `ir-hashes.yml`. `mise run tir:hashes`. Docs in `docs/project/contributing.md` one row. No frozen-file edit. Receipt: one PR that does not touch `src/pyscriptc`.
2. **P3 fixpoint hasher.** `fixpoint.py` calls `tirserde.hash_module` (dict) first, because lowering still returns a dict. Two runs, receipt that per-module hashes match the pre-change `canonical.hash_module` hashes. Nightly still green. No probe file edit.
3. **P2 typed stream.** `hash_module_node` writes schema field order into the SHA-256 sink with no `node_to_dict`. `--verify-bytes` against today's streaming dict bytes on the visible corpus. If bytes move, stop and write a blocker; do not regen `TIR_HASHES.json` in that PR.
4. **Path scrub and order in the typed serializer.** Port `_scrub_paths`, class sort, `codes` sort, fence total order. Unit tests move from `tests/test_canonical.py` only when this lands (Group T class (b) delete, not a weaken). Same blocker rule on hash movement.
5. **D1 after signature.** Remove `irscore` from CI and mise. Leave the JSON file. Update comments in `tirserde.py` that say the old gate keeps reading `canonical.py`.
6. **D2.** Delete `canonical.py`. Move `CANONICAL_SCHEMA_VERSION`. Frozen-list edit in `AGENTS.md` and `ir-canonicity.md` section 1-6 marked historical. Census repin only if import fences move.
7. **D3.** `validate_module` off the compile path; typed verifier in front of emit. Separate PRs, mixin-sized, not one dump of `ir.py`.
8. **D4.** Delete `IR_HASHES.json` and `irscore.py`. Flipcheck and compat2 README. Confirm `rg IR_HASHES` in `.github/` and `mise.toml` is empty.

Stop at the first hour that does not finish. Do not start D1 before the owner comment on #830.

## Open decisions for the owner

1. **Clock vs catch.** Keep both bars (14 greens or one real nondeterminism), or require the 14 even after a catch? Recommended: keep the OR, but define "real" as above so stale baselines cannot sign the issue.
2. **Required TIR check.** Promote the P1 TIR PR job to a required check before D1, or leave it non-required like today's IR job? Recommended: require it before D1, because D1 removes the only other on-PR digest.
3. **When the JSON file dies.** Delete `IR_HASHES.json` at D1 (gate off, file gone the same day) or at D4 (after `ir.py`)? Recommended: D4, so a fire-drill `irscore` still has a baseline while dict IR exists. Cost is 1.4 MB until then.
4. **`irverify.py`.** Rewrite in D3 vs keep dict verify until the last dict consumer dies? Recommended: keep it as long as `validate_module` runs; do not rewrite it as a way to retire hashes.
5. **Typed stream vs dict stream as the TIR file.** Must P2 land before the 14-day clock counts, or may the clock count on today's dict-identical TIR hashes? Recommended: the clock may count now (the decision text says the new gate, which is `TIR_HASHES.json` as shipped), but `canonical.py` cannot be deleted until P2. Signing #830 after 14 greens still leaves P2-P3 as D2 blockers.
6. **Fixpoint hasher.** Count today's `canonical.hash_module` fixpoint toward the 14, or require P3 first? Recommended: count it. The criterion is "the new gate" plus the stage-2 lane, and the lane already exists. P3 is a D2 blocker, not a clock blocker.

## Blockers (this note)

- Clock is 1 of 14. No recorded nondeterminism catch. #830 stays open.
- `tirserde` still imports `canonicalize`. That is a D2 blocker, not a docs blocker.
- Group T inventory is a parallel PR; D2/D3 must not delete class (b) tests before that table exists.
- Frozen files, including `IR_HASHES.json` and `TIR_HASHES.json`, are not regenerated here. If an implementation PR would move either digest, stop.

`0225bcdb` (c-int-parity hash edits) is out of this work. Never include it.

This PR is the note only.
