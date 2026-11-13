# Recorded decision: retire `IR_HASHES.json` and the `canonical.py` dict-shape rules; `TIR_HASHES.json` is the one canonicity gate (#830, R5a)

Status: Signed by the owner 2026-11-09. The retirement itself runs in the P1-P3 / D1-D4 order below; nothing in this decision edits a frozen file.

## Decision

Retire the dict-IR canonicity gate: `benchmarks/compat2/probes/IR_HASHES.json` and the dict-shape rules in `src/pyscriptc/canonical.py`. `benchmarks/compat2/probes/TIR_HASHES.json` (streaming TIR gate, same 13,692 visible probes, same SHA-256 contract, same Linux-only A/B `cmp` regen ritual and oracle stamp) is the one canonicity gate from then on.

Order (each step a separate PR; do not reorder; P steps may run before the owner comment, D steps only after it):

- **P1** (prerequisite, may land before the signature): a TIR PR compare path beside `ir-hashes.yml` — `tir-hashes.yml` running `uv run python -m benchmarks.compat2.tirscore benchmarks/compat2/probes --baseline benchmarks/compat2/probes/TIR_HASHES.json` on the same path filter as the IR job, non-required to start, plus a `workflow_dispatch` generate mode (two `tirscore --out` runs, `cmp` byte-identical, artifact upload). Add `mise run tir:hashes`.
- **P2**: `tirserde.hash_module_node` hashes typed nodes in schema field order without `node_to_dict` + `canonicalize`; corpus byte-identity against the then-current TIR baseline, or a recorded regen. Path scrub, class order, handler `codes` order and fence total order get an owner (deterministic lowering or the typed serializer). Blocks D2, not the clock.
- **P3**: `fixpoint.py` hashes with the TIR hasher, still twice per module; nightly stays TIR + fixpoint. A digest move is a receipt, never a frozen-file edit.
- **P4**: Group T has classified every `"kind"` test; class (b) dict-shape pins stay until D2/D3 and are never weakened early.
- **D1** (after the owner signs): stop the old compare — delete the `irscore --baseline` PR job in `ir-hashes.yml`, convert generate mode to TIR, point `mise run ir:hashes` at `tir:hashes` or delete the alias. `ci.yml` required checks unchanged. Nightly never runs `irscore`.
- **D2**: delete `src/pyscriptc/canonical.py` only after P2 and P3 and after `tirserde` stops importing it (`CANONICAL_SCHEMA_VERSION` moves into `tirserde`/`_irnode`; `fndigest.py` switches or dies; `tirscore --verify-bytes` drops the `json.dumps` dict path). Class (b) strip/sort/`sort_keys` tests retire here. Frozen-list amendment: AGENTS.md drops `canonical.py` rules and `probes/IR_HASHES.json`, keeping `TIR_HASHES.json` as the frozen canonicity gate; sections 1-6 of `ir-canonicity.md` and the IR JSON paragraph in `docs/reference/stability.md` become historical in the same PR as a divergence/gap update.
- **D3**: retire `validate_module` and the dict key/kind tables after no compile path hands a module dict to the CLI or emitter; `IR_VERSION`/`SUBSET`/`assert_same_cpython`/`fits_i64` move to typed HIR or a thin ABI stamp; `irverify.py` is rewritten against typed nodes or replaced. Blocked on handoff 4 and Group T class (a), not on the JSON file.
- **D4**: delete `probes/IR_HASHES.json` and `irscore.py` last, when nothing in CI, mise or docs names them; `flipcheck.py` gets a TIR-shaped reader in the same PR. Delete, never hand-edit down, never regenerate in the deletion PR.

`ir.py` before the JSON file is deliberate: while dict IR still ships, the 1.4 MB file is frozen history a fire-drill `irscore --out` can still diff against.

Frozen-list amendment (D2, same PR): the frozen v2 gates list in `AGENTS.md` drops the `canonical.py` rules and `probes/IR_HASHES.json`; `probes/TIR_HASHES.json` remains the frozen canonicity gate with the same regen discipline (A/B byte-identical, Linux only, recorded decision for any change).

## Why

The dict gate and the TIR gate hash the same payload over the same probe universe: `TIR_HASHES.json` is today a second digest of the dict canonical form, so keeping both gates is a redundant 1.4 MB baseline plus a second driver to keep green. The streaming TIR gate is the contract that survives the dict-IR deletion (`canonical.py`, `ir.py`, `irverify.py` go at the v0.10.0 drop), it is already the nightly watcher, and it has proven byte-stable A/B on the full corpus. The PR compare was the one thing it lacked (retirement note, gap 3): without it, D1 would remove the only on-PR digest check and a moved digest could land before nightly runs.

## Alternatives rejected

- Keep both gates forever: two 1.4 MB baselines and two drivers for one payload; `canonical.py` rules must survive only to feed a gate the typed migration is deleting.
- Retire the TIR gate and keep IR: the dict IR is scheduled for deletion at the v0.10.0 drop, so its gate would then hash a shape nothing produces.
- Make the TIR PR compare required in its landing PR: the owner may promote it before D1, but matching today's non-required IR job is enough to start, and a new required check in the same PR as its own landing risks noise.
- Fold MIR (`MIR_HASHES.json`, M-5) into `TIR_HASHES.json` to widen the retirement: different contract; an MIR nightly red must not reset the #830 clock.

## What reopens

- Any change to `probes/TIR_HASHES.json`, `tirscore.py`, `tirserde.py` hashing internals, or the frozen-list wording needs a new recorded decision (the regen A/B `cmp` ritual stays; `IR_HASHES.json` regen rules die with D4).
- If P2 moves TIR digests (typed field-order hashing vs `sort_keys` dict bytes), the retirement stops at that step and a blocker note is written before any regen.
- The owner may still promote the P1 TIR compare to a required check before D1 (recommended in the retirement note); that promotion is an owner comment on #830, not a decision artifact.
- Reordering P1-P4 / D1-D4 reopens this decision.
