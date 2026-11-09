# M-2 (A9): MIR list and dict value types

Date: 2026-11-06. Epic: #817. Milestone: v0.9.0. Docs only; no code in this note.

Builds on M-1 (A8, the `str` value type). Do not land list/dict ops until `MIR_S` and the additive `const … s` serializer line exist: a list of strings is the first interesting container, and the operand encoding M-1 froze (value ref versus const-pool index, four ints per instruction) is the one this slice reuses.

Read first: `src/pyscriptc/mir.py`, `mirlower.py`, `miremit.py`, `mirverify.py`, `.agents/plans/decisions/2026-11-02-mir-backend-only-contract.md`, and `.agents/notes/2026-11-06-mir-str-value-type.md` (M-1). HIR remains the semantic source of truth; element types of `list[T]` / `dict[K, V]` stay on the HIR. MIR only needs to know the ABI shape the emitter already uses.

Handoff baseline: 3 of 22 corpus functions in the MIR slice today; 15 of 22 after None returns; about 18 of 22 after calls and short-circuit; 95 to 100 percent after `str` and list value types. Dict is the same heap-pointer family as list, so it rides this note even if the first implementation hour ships list only.

## What exists today

The leaf MIR has no container ops. `supported()` rejects list and dict expressions; those functions stay on the direct path.

The direct emitter (`emit/llvm/_exprs.py`):

- `list` → `ptr`. `ps_seq_new(i64 elem_size, i64 n)` then per element `ps_seq_set(ptr seq, i64 i, ptr cell)`. The cell is `_stash_cell` of the element type (scalars boxed into a heap slot; `str` stored as the 16-byte `%ps.buf` prefix).
- `dict` → `ptr`. `ps_map_new(i32 key_kind, i64 key_size, i64 val_size, i64 n)` then `ps_map_put`. `key_kind` is 1 for int keys, 2 otherwise (the runtime hash). Subscript is `ps_seq_get` / `ps_map_get` into an alloca of `_memory_type(elem)`.
- Assignment of a list or dict copies the pointer (Python sharing). The runtime comment in `ps_rt.c` is explicit: "Lists and dicts are pointers, so assignment shares the buffer the way Python does."
- `str` is *not* this shape (`%ps.str` fat value). Mixing the two in one tag is how A/B identity dies.

`MIR_COPY` already moves a value id. For a `ptr`-typed id that is enough for assignment and `return`. Construction and element access need new ops or helper-backed copies. The leaf already uses helper-backed `MIR_COPY` for `//` and `%`; `fn.helpers` is omitted from `serialize()`, so the digest covers the instruction stream, not the `ps_seq_*` spelling.

## Proposed shape

Keep the four-int contract and M-1's operand encoding. Add the reserved pointer tag from M-1:

- `MIR_P = 4` (heap pointer: list, dict, and later class/exc/func cells). Runtime LLVM type `ptr`.
- A list value is a `MIR_P` value id. A dict value is also `MIR_P`. The HIR type (list vs dict, elem/key/value kinds) is not re-encoded in the operand; the lowerer picks the helper, the verifier only checks pointer-vs-scalar.
- No constant-pool encoding of a container *value*. An empty list is constructed at the use site (`ps_seq_new` with `n=0`), not interned. Interning a heap object would make identity and mutation disagree with Python. `MirConst` does not grow a list/dict field.
- Construction is a sequence of instructions, not one "list literal" op with N operands (N would not fit in two argument slots).

Two implementation styles fit the existing leaf:

**Style A — helper-backed `MIR_COPY` (recommended for the first landing).** `ps_seq_new`, `ps_seq_set`, `ps_seq_get`, `ps_map_new`, `ps_map_put`, `ps_map_get` are recorded in `fn.helpers` the way `floordiv` already is. Each helper copy has a documented arity in the lowerer (new: 0-arg does not exist; `new` takes sizes as constant-pool `w` operands). Extra operands that do not fit in `arg1`/`arg2` become preceding `MIR_COPY`s of constants, or a small dedicated op (style B). Style A keeps `OP_ARITY` unchanged and matches the "helpers are a target spelling" rule. Downside: `ps_seq_set` is three operands (seq, index, cell). Two-slot ops cannot express it without a prelude (pack, or a sequence of copies into a scratch).

**Style B — new ops with a side list for extra args (cleaner for set/get).** Add `MIR_NEWSEQ`, `MIR_SEQSET`, `MIR_SEQGET`, `MIR_NEWMAP`, `MIR_MAPPUT`, `MIR_MAPGET`. `NEWSEQ` fits today: `arg1` = elem_size const, `arg2` = n const, `dst` = the `MIR_P`. `SEQGET` fits: `arg1` = seq, `arg2` = index, `dst` = loaded elem (tag is *not* always `MIR_P`: `list[int]` loads a `MIR_W`). `SEQSET` needs three inputs. Options:

- B1. A parallel `arg3` list on `MirFunction`. Touches every serializer line (`instr i op dst a1 a2` becomes `a1 a2 a3`). Moves **every** existing MIR digest, including the current leaf fixtures. M-5 would pin the moved hashes. Do not do this in the same PR as the first container landing if a header-token bump can be avoided; if `arg3` is required, land it as its own hour with an empty `-1` on every current instruction so the format change is mechanical and reviewable.
- B2. Keep two slots. `SEQSET` is `arg1` = seq, `arg2` = index, and the stored value is `dst` used as an in/out (no new value). Unusual: every other value op writes `dst`. Verifier special case. Not recommended.
- B3. Prelude: `MIR_PACK2` produces a scratch holding `(index, cell)` as a tuple pointer, then `SEQSET` reads seq + pack. Extra heap traffic, A/B identity against `ps_seq_set(seq, i, cell)` would fail unless the emitter unpacks and does not emit the pack.

**Recommended:** style A for `NEW`/`GET` (two operands) plus one new op `MIR_SEQSET`/`MIR_MAPPUT` whose third operand lives in a new `arg3` list that is `-1` for every existing op. That is a serializer format change. Cost: full MIR digest move of the leaf fixtures. Do it once, document in M-5, do not also bump a `mir_schema` header in the same change if M-5 has not landed the baseline file yet (M-1 decision 4: additive changes preferred; `arg3` is the one justified non-additive exception because three-operand stores cannot be faked without breaking A/B).

If the owner rejects `arg3`, fall back to style A with a helper calling convention: the lowerer emits `seq` in `arg1`, `index` in `arg2`, and stashes the cell in `fn.helpers` as `seqset <vid>`. The digest would *not* cover the stored value id (helpers are omitted). That is a correctness hole: two functions that store different cells into the same seq would hash equal. Rejected. The stored value must be in the instruction stream. Hence `arg3`.

## Element types and the verifier

`OP_RESULT_TYPE` cannot know the element type of `SEQGET`: it depends on the HIR `list[T]`. Same problem `MIR_COPY` already has (`copy_result_type` reads `arg2` or the helpers entry). Do the same: pack the loaded tag in `arg3` or in a helpers suffix (`seqget 0` for `MIR_W`, …) *and* still put the seq/index in `arg1`/`arg2`. Prefer packing the result tag in `arg2` of GET overlapping the index? No: both are needed. Pack the result tag next to the helper name (`seqget 0`) and teach `copy_result_type` to read it, *or* store it in `arg3` of GET (seq, index, tag) with `dst` the value. Recommended: GET uses `arg1` seq, `arg2` index, result tag from a helpers entry, matching copy. Verifier: `MVS01` if the loaded tag is not `W/D/B/S/P`.

`SEQSET` does not produce a value (`dst = -1`). Verifier: seq is `MIR_P`; index is `MIR_W`; the stored operand's tag is not checked against HIR (backend-only). A `MIR_BR` on a `MIR_P` is `MVS01`.

Block arguments of type `MIR_P` are allowed (a join that yields a list). `MirBlockArg` comments today say `MIR_W`/`MIR_D`; they already gained `MIR_B`; M-1 adds `MIR_S`; this slice adds `MIR_P`.

## Emitter A/B

`miremit` must produce the same `ps_seq_new` / `ps_seq_set` / alloca-cell sequence as `_exprs.emit_list`, including temp order. Element values are emitted first (the list comprehension in the direct path), then `new`, then the `set` loop. Dict: keys and values collected, then `new`, then `put`. Sharing on copy is a `MIR_COPY` of the `MIR_P` id; the leaf emitter's multi-use alloca rule applies to pointers the same way it does to ints (a list read twice is an alloca of `ptr`, not a second `ps_seq_new`).

Out of slice (first landing): list/dict *comprehensions*, unpacking, `*args`, mutation methods beyond subscript store, `set`/`tuple` (tuple may already be a list at HIR; follow HIR, do not invent a MIR tuple tag), nested functions that close over a list (M-4).

In slice: list/dict literals of scalar and (once M-1 lands) `str` elements, subscript load/store, `len`, `return`, assignment, pass as a call argument once calls are in the slice.

## Digest and frozen gates

- `arg3` (if accepted) rewrites every `instr` line. Every current `module_hash` of `tests/fixtures/mir_leaf/` moves. LLVM bytes of those fixtures must **not** move. `tests/test_emitter_goldens.py` stays byte-identical, no re-bless. `IR_HASHES.json` / `TIR_HASHES.json` / `probes/*.jsonl` / `canonical.py` are not edited. If an implementation change would move an IR or TIR hash, stop.
- Container construction adds `instr` lines, so hashes of *new* fixtures are new; hashes of old fixtures move only because of `arg3`. Record the reason next to M-5's regen ritual: "arg3 unused-slot on every instruction, 2026-11-06 M-2".
- Census pins move only if `mirlower.py` / `miremit.py` gain fences. This note does not.

## One-hour tasks

1. **`MIR_P` tag and (if owner accepts) `arg3`.** Builder defaults `arg3` to `-1`. Serializer prints `a3=` only when the owner chose always-print (always-print is simpler and is the digest move). Verifier: unknown extra slot ignored. Leaf LLVM A/B still green on existing fixtures. No container ops yet.
2. **List new/set/get/len.** Lower `list` literals of `int`/`bool`/`float`. One fixture. `test_direct_vs_mir_bytes`. Helper names `seqnew`/`seqset`/`seqget`/`seqlen`.
3. **List of str.** Depends on M-1. Second fixture. `_stash_cell` widen/narrow must match.
4. **Dict new/put/get.** `key_kind` as a `w` const. Fixture with `dict[int, int]` and `dict[str, int]` if M-1 is in. Subscript store (`setitem`) as `MAPPUT`.
5. **Verifier negatives.** `MIR_BR` on `MIR_P`; `SEQGET` of a `w`; `SEQSET` missing `arg3`. Prefer G-1's file if it has merged; otherwise extend `TestVerifierViolations`. Do not change `MVS` codes.
6. **Receipt.** Fixture names, whether `arg3` moved leaf MIR hashes, confirmation IR/TIR untouched, census pins unchanged or repinned with `scripts/repin_census.py`.

Stop at the first hour that does not finish.

## Open decisions for the owner

1. **`arg3` vs reject three-operand ops.** Recommended: add `arg3`, pay one digest move, store the cell in the instruction stream. Alternative: do not support `SEQSET`/`MAPPUT` in MIR and keep mutation on the direct path until a wider calling-convention change. That would cap the slice below the 95 percent claim.
2. **One `MIR_P` tag for list and dict, or `MIR_L` / `MIR_H`.** Recommended: one tag. The helper name distinguishes them. Two tags would make `copy_result_type` and block arguments branch for no LLVM difference (`ptr` either way).
3. **Empty-list intern.** Recommended: never intern. Construct at the use site so `a = []; b = []; a is b` stays false if the language ever exposes identity; today identity of lists is pointer equality of the `ps_seq_new` result.
4. **Land list-only first?** Recommended: yes, task 2 as its own PR; dict is task 4. The handoff's "95 to 100 percent" names `str` and list; dict is the same design and should not wait on a new note.
5. **Default lane.** No `PYSC_MIR` flip. Owner signal only.

Blocked on: M-1 if the first list fixture includes `str` elements (task 2 can proceed with `list[int]` without M-1). Blocked on the owner if they reject `arg3` (decision 1), because mutation then has no sound digest encoding.
