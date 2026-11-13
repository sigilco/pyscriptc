# P2 blocker note: typed field-order hashing moves every TIR digest (#830, P2)

Date: 2026-11-10. Milestone: v0.10.0. Issue #830. Decision: `.agents/plans/decisions/2026-11-09-retire-ir-hashes.md` (signed 2026-11-09). No frozen file is edited by this note or its PR.

The decision says, under "What reopens":

> If P2 moves TIR digests (typed field-order hashing vs `sort_keys` dict bytes), the retirement stops at that step and a blocker note is written before any regen.

This is that note. P2 landed the typed hasher and moved every digest. No regen follows in this PR: `probes/TIR_HASHES.json` and `probes/IR_HASHES.json` are untouched, and the frozen gate stays green because the gate driver (`tirscore`) still hashes the dict form.

## What landed (P2)

`src/pyscriptc/tirhash.py` (new) walks the typed `_irnode` records directly and streams the payload into SHA-256. `tirserde.hash_module_node` and `tirserde.payload_bytes_node` delegate to it: no `node_to_dict` reconstruction, no `canonicalize` call, no dict materialization of the payload.

The canonicity rules `canonical.py` used to own now have their owner in the typed serializer:

| rule | owner now |
|---|---|
| incidental keys (`span`, `sourceFile`, `cpython`, `_notes`) never emitted | the typed walker has no place for them |
| `classes` sorted by `name` | `tirhash._write_classes` |
| try-handler `codes` sorted | `tirhash._write_handler` |
| `fences` totally ordered by `(code, message, function or "", span.line, span.col)` | `tirhash._write_fences` |
| environment absolute paths become `<PATH>` | `tirhash._scrub_paths` |

The field order replaces `sort_keys` as the canonicity mechanism, which is the typed-IR contract of `.agents/docs/spec/ir-canonicity.md` section 7.1: per node kind the serializer emits the node id first, then the schema field order of that kind, then children.

## Why every digest moves

The dict gate's payload is `json.dumps(..., sort_keys=True)`: object keys in Python string sort order. The typed payload is the schema field order of each record. For a `return` node the dict payload spells `{"kind":...,"span":...,"value":...}` (span first, because `s` sorts before `v`) and the typed payload spells `{"kind":"return","value":...}`. The two are different byte strings over the same tree, so the SHA-256 differs.

This is not a bug and not a regression: it is the intended consequence of the recorded contract. Section 7.1 says field order in the serializer replaces `sort_keys`; a digest that did not move would mean the serializer was still sorting.

## Measured (2026-11-10, Linux, CPython 3.14.x, visible corpus only)

- Tree identity: for all 13,692 visible probes, the tree the typed walk spells equals `canonicalize(module)` exactly, key for key and value for value, with the incidental keys absent and the three orders applied. **0 tree mismatches.** The typed payload carries the same canonical information the dict gate carries.
- Digest stability: every probe hashed twice in one process, 13,692 probes, **0 unstable typed hashes**. The A/B property the regen ritual demands holds for the typed serializer.
- Digest movement: 13,692 of 13,692 typed digests differ from the dict gate digest. Every digest moves; none is stable-by-accident.
- Frozen gate: `tirscore` still hashes the dict form, so `probes/TIR_HASHES.json` is still the right baseline for the gate that runs. `tests/test_irscore.py`, `tests/test_tir_ir_hash_coverage.py` and `tests/test_tirserde_island_call.py` pass.

## The blocker: the gate cannot switch hasher without a regen, and the regen is D-gated

`benchmarks/compat2/tirscore.py` hashes the dict IR (`tirserde.hash_module`), because `lower_file` still returns a module dict. The typed hasher is reachable only for a module the lowerer hands over as typed records, which the lowerer does not do yet (handoff 4 of the typed-IR migration).

So there are two ways to make the typed hasher the gate's hasher, and each is out of P2's scope:

1. **Switch the gate driver to the typed hasher** (`tirscore` calls `hash_module_node` over the typed module). This needs the lowerer to emit a typed module, which is the typed-IR handoff, not a P2 change.
2. **Regen `TIR_HASHES.json` under the typed serializer** (the two-run `cmp` ritual). This is a frozen-file edit, which the decision forbids in P2 and which the "What reopens" clause explicitly blocks until a new recorded decision.

The retirement therefore stops at P2. D1 (stop the old compare), D2 (delete `canonical.py`) and D3/D4 (retire the dict IR) all depend on the TIR gate running the typed hasher, and the gate cannot run the typed hasher against the current baseline without a regen that only a new recorded decision can authorize.

## What this blocks and what it does not

- **Blocked**: D2 (`canonical.py` deletion, which needs `tirserde` to stop importing `canonical` and `tirscore --verify-bytes` to drop the `json.dumps` dict path). `canonical.py` is still imported by `tirserde.payload_bytes` / `hash_module` (the dict gate path) and by `fixpoint.py` (P3).
- **Not blocked**: P1 (merged, #1182), P3 (`fixpoint.py` switches to the TIR hasher), P4 (the Group T classification). P3 hashes the same dict IR through the typed hasher, so it inherits the same movement and needs the same regen; the decision already orders P3 before D2.
- **Not blocked by this PR**: the frozen gate. `probes/TIR_HASHES.json` is unchanged and the gate that runs against it is unchanged.

## The recommendation for the next decision

Two coherent shapes, both needing a recorded decision:

- **A (regen in place).** Bump `tir_schema` to `tir/2`, regenerate `TIR_HASHES.json` with the typed serializer through the two-run `cmp` ritual (Linux only), then switch `tirscore` to the typed hasher. Cost: one frozen-file regen, one schema bump, one hasher switch in the driver. Risk: the digest baseline moves wholesale, so the first green nightly after the regen carries no history; the tree-identity receipt (0 of 13,692 mismatches) is the compensating evidence that the movement is a key-order change and not a content change.
- **B (keep the dict gate until the lowerer is typed).** Leave `tirscore` on the dict hasher and let the typed hasher take over when the lowerer hands over typed modules. Cost: `canonical.py` stays alive longer than D2 planned. Risk: none to the frozen gate, but the retirement clock slips to the typed-IR handoff.

Option A is the one the decision's ordering implies (P2 then P3 then D2), and its blocker is exactly what this note records. Until the owner signs one of them, the retirement stays at P2.

## Reproducing the receipts

The tree-identity and digest-stability receipts come from a throwaway driver (not committed: it reads the visible corpus only and touches no frozen file). The same properties are pinned by `tests/test_tirhash.py` (ownership of the four rules, the field order, the path scrub, no `node_to_dict` on the typed path) and `tests/test_irnode_import.py` (the typed payload carries the canonical tree; the bytes differ from the `sort_keys` bytes by design).

The visible corpus is the whole probe universe of the gate: 13,692 probes across `builtins.jsonl`, `language.jsonl`, `modules.jsonl` and `surface.jsonl`. The holdout is never read.
