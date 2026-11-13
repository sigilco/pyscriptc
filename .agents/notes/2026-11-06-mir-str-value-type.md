# M-1 (A8): the MIR `str` value type

Date: 2026-11-06. Epic: #817. Milestone: v0.9.0. Docs only; no code in this note.

This is the first value-type expansion of the R4 3AC MIR. Today the slice is scalars (`MIR_W` int, `MIR_D` float, `MIR_B` i1). The measured baseline in the handoff is 3 of 22 corpus functions in the MIR slice today, 15 of 22 after None returns, about 18 of 22 after calls and short-circuit, and 95 to 100 percent after `str` and list value types. `str` is the first non-scalar that still has to live in the four-int instruction stream.

Read first: `src/pyscriptc/mir.py`, `mirlower.py`, `miremit.py`, `mirverify.py`, and the approved decision `.agents/plans/decisions/2026-11-02-mir-backend-only-contract.md`. MIR is the backend-only contract; HIR remains the semantic source of truth. A `str` type tag is instruction-selection detail, not a new language rule.

## What exists today

Operand encoding in `mir.py` already keeps everything an int, constants included:

- a nonnegative operand is a value reference (an instruction `dst`, a parameter slot, or a block-argument slot in the same global value space)
- a constant is an index into the function's constant pool, encoded as `-(index + 1)`
- an unused operand slot is the sentinel `-1` (`none_ref()`)

The pool (`MirConst`) carries a type tag and exactly one of two value fields: `ival` for `MIR_W`/`MIR_B`, `dval` for `MIR_D`. Interning is `const_w` / `const_b` / `const_d`. `const_d` keys on the IEEE-754 bit pattern so `0.0` and `-0.0` stay distinct. `serialize()` walks lists by index and emits one line per pool entry (`const N w …` / `b …` / `d <bits>`). `module_hash` is SHA-256 of that text. Helper names on `MIR_COPY` are deliberately omitted from the digest (they are a target spelling, not the instruction stream).

The builder and verifier only admit `MIR_W`, `MIR_D`, `MIR_B` for parameters, results, `emit_val`, and `copy_result_type`. `mirlower.emit_const` therefore cannot intern a string; a `const` of kind `str` is out of slice and the function stays on the direct path.

The direct emitter's ABI for `str` is not an `i64`. `%ps.str = type { i64, ptr, i64, i32 }` (length, data pointer, cached count, flags). At rest in memory the runtime reads a 16-byte `%ps.buf` `{ i64, ptr }`; the emitter widens on load (`insertvalue` of length and pointer, then `ps_str_counts` to refill count/flags) and narrows on store. A string constant is interned as a module-level global and assembled with `string_value`. Runtime helpers (`ps_str_get`, `ps_str_cmp`, `ps_str_find`, …) take `(i64 length, ptr data)`, not the aggregate. Lists and dicts, by contrast, are heap `ptr`s (`ps_seq_new` / `ps_map_new`); they are M-2, not this note.

`MIR_RAISE` already exists: its operand is a constant-pool word carrying an `EXCEPTIONS` table code. A raise that builds a message string is out of this slice until `str` values exist.

## The encoding question

The four-int instruction (`op`, `dst`, `arg1`, `arg2`) must survive. Splitting a string into two operand slots (length in `arg1`, pointer in `arg2`) would consume both operand slots of every consumer and break unary ops, copies, and returns. The operand encoding therefore stays "value ref versus const-pool index"; what changes is what a pool entry and a value id of tag `MIR_S` *mean*.

Rejected alternatives:

- **Two-operand fat encoding.** Breaks the four-int contract. A `MIR_RET` of a string would have nowhere to put the unused-slot sentinel.
- **Pointer-only `MIR_P` with length loaded at every use.** Would not match the direct path's `%ps.str` SSA values, so A/B LLVM identity would fail on every `extractvalue` / `insertvalue` sequence.
- **Keep strings as interned `i64` ids into a side table the serializer ignores.** The digest would not cover the bytes, so two modules that differ only in a string constant would hash equal. The backend-only contract says MIR hashes are a backend property checked the same way as HIR hashes: the canonical text is the byte source.

Proposed encoding (owner decision 1):

- Add type tag `MIR_S = 3`. Reserve `MIR_P = 4` for M-2 heap pointers so the tag space does not have to be renumbered twice.
- A nonnegative operand of a `MIR_S`-typed def is a value id whose runtime shape is `%ps.str`.
- A negative operand of a `MIR_S` use decodes with the existing `const_index` to a pool entry whose `typ` is `MIR_S`.
- Extend `MirConst` with a `sval: str` field (empty on non-`S` entries). Interning is a new `const_s(text: str) -> int` that keys on the exact Unicode string (UTF-8 bytes, not Python hash), so hash-seed variation cannot merge or split entries. Lone surrogates stay a lowering refusal (`FitError` on the direct path); they never reach the pool.
- `serialize()` grows one line shape: `const {index} s {nbytes} {hex}` where `nbytes` is the UTF-8 length and `hex` is lowercase hex of those bytes. Hex, not a quoted string: the serializer must not grow a JSON/escape dialect, and a newline or NUL in a constant must not split a line. Empty string is `const N s 0` with no hex token.
- `copy_result_type` and `MirBuilder.add_param` / `set_result` / `emit_val` admit `MIR_S` the same way they admit `MIR_B`.
- No new operand encoding. `is_const_ref` / `const_ref` / `const_index` / `none_ref` stay as they are. `-1` remains the unused-slot sentinel; it is not a valid `MIR_S` constant index (`const_ref(0)` is `-1` today — see open decision 2).

## Collision of `none_ref()` with `const_ref(0)`

`none_ref()` returns `-1`. `const_ref(0)` is also `-(0 + 1) == -1`. Today this is tolerable because unused slots are only read when `OP_ARITY` says they are not: the verifier skips a slot whose arity does not require it, and a real constant of pool index 0 is only read from a slot that *does* require a value. A `MIR_S` constant in pool slot 0 would encode as `-1`, identical to "unused". That is already true for `const_w` of the first interned int. The leaf slice survives because a required operand is never "unused"; the sentinel is only stored in `arg2` of arity-1 ops.

Keep this invariant: a required operand is always either a nonnegative value id or a const-ref whose decoded index exists. Do not introduce a second sentinel. Document it next to `none_ref` when the `MIR_S` tag lands, so a later pass does not "fix" `-1` by skipping pool index 0.

## What changes in `mir.py` and the digest gate

`mir.py` (shape only; this note does not edit it):

- `MIR_S` tag, `MirConst.sval`, `const_s`, admit `MIR_S` on params/results/copies.
- `serialize()`: the `const` line for `MIR_S` as above. Order remains insertion order of the pool. No dict, no set.
- `OP_ARITY` / `OP_RESULT_TYPE` / `VALUE_OPS`: no new ops in M-1 itself. String *construction* of a constant is intern-plus-`MIR_COPY` (or a use of the const-ref directly). Concatenation, indexing, `len`, and methods are helper-backed `MIR_COPY` rows, the same pattern `//` and `%` already use: the helper name lives in `fn.helpers` and is omitted from the digest. The first landing can be constants, params, copies, returns, and `==` / `!=` via `ps_str_cmp` as a helper copy producing `MIR_B`. Subscript and methods wait on a follow-on hour if the owner wants the 95 percent corpus step in one slice rather than two.

`mirlower.py`: `supported()` admits `const` of kind `str`, `name`/`assign`/`return` of type `str`, and the small compare/helper set the owner picks. Out-of-slice remains the fallback. Fenced modules stay refused by `emit_llvm_mir`.

`miremit.py`: a `MIR_S` constant materializes through the same `string_value` path the direct emitter uses (module global plus `%ps.str` `insertvalue`). A `MIR_S` name slot recovers the alloca/`store`/`load` shape the leaf emitter already uses for multi-use values, but the memory type is `%ps.buf` and the widen/narrow sequence must match `_core.py` byte for byte. This is the expensive part of A/B identity.

`mirverify.py`: type check `MIR_S` against `MIR_S`; a `MIR_BR` condition remains `MIR_B` (comparing strings produces a bool, it does not branch on a string). A const-ref whose pool tag is `MIR_S` used as a `w` operand is `MVS01`. Pool index out of range stays `MVS02`.

Digest gate (see also M-5; this note does not add a probe file):

- Adding `const N s …` lines moves the canonical text of every function that interns a string. Functions that intern no strings keep their current bytes **unless** the serializer grows a schema token on the `fn` header. Do not bump a header token in the same change as the first `MIR_S` landing if it can be avoided: a header bump repins the whole MIR corpus, including the current leaf fixtures, for no semantic reason.
- `tests/test_mir_leaf.py::test_mir_hash_stable` already asserts `module_hash` twice at fixed input. New fixtures with string constants join that test; existing fixtures must stay byte-identical on both LLVM (`test_direct_vs_mir_bytes`) and the MIR digest.
- `TIR_HASHES.json` / `IR_HASHES.json` do not move: HIR and dict IR are unchanged. Frozen gate files stay untouched. If a later implementation change would alter an IR or TIR hash, stop.
- Helper names remain outside the digest. Two lowerings that pick different helper spellings for the same `ps_str_cmp` copy would hash equal and fail LLVM A/B instead. That is the existing leaf-slice discipline; keep it.

## Repin ritual cost

Cost is paid in three layers, only the first of which this slice should touch:

1. **MIR digest of in-slice functions.** Cheap if the serializer change is additive (new `const` line kinds). Expensive if the `fn`/`module` header format changes: every existing `module_hash` moves and every MIR fixture hash in `test_mir_leaf.py` (today stability is computed, not pinned as a hex literal) still has to be re-checked for LLVM identity. Do not pin hex literals until M-5 lands a baseline file.
2. **LLVM goldens.** `tests/test_emitter_goldens.py` must stay byte-identical with no re-bless. The MIR lane is an A/B of the direct emitter; if `miremit` recreates `%ps.str` in a different temp order, goldens of the *direct* path do not move but `test_direct_vs_mir_bytes` fails. Fix the emitter, do not re-bless goldens.
3. **TIR / IR hashes.** Must not move. Ritual for those files is documented in `.agents/notes/decisions-and-platform.md` and `.agents/docs/spec/ir-canonicity.md` §7.4 (two runs, `cmp`, Linux only). M-1 does not invoke it.

Practical cost of a well-scoped M-1 implementation: one new leaf fixture, no frozen-file regen, no census pin move, LLVM A/B on the new fixture, and a verifier test that a `w` op cannot read an `s` const. The first time a string helper is added to `fn.helpers`, census pins move only if `mirlower.py` / `miremit.py` gain fences; docs-only this PR does not.

## Slice contents (proposed)

In, for the first implementation hour after this note:

- `str` constants, parameters, locals, copies, `return`
- `==` / `!=` on two `str` values via the existing `ps_str_cmp` helper, result `MIR_B`
- `len(s)` as a helper copy producing `MIR_W` (the cached count, matching the direct path)

Out, until a follow-on hour:

- concatenation, subscript, methods, f-strings, format
- `bytes` (same `%ps.str` LLVM shape, different tag or a flag; owner decision 3)
- lists of str (M-2)

Fences: a module that already has fences never enters the MIR lane. A new `str` operation the slice does not handle is out-of-slice (`skipped`), not a fence.

## One-hour tasks

1. **Tag and pool (shape).** Add `MIR_S`, `sval`, `const_s`, serializer line, builder admission. Unit-test intern-once, empty string, a string containing newline and NUL, and hash stability across two runs. No lowerer yet.
2. **Verifier.** Admit `MIR_S` in param/result/copy; `MVS01` when a `w`/`d`/`b` op reads an `s` operand; `MVS01` when `MIR_BR` sees `MIR_S`. Negative tests only, new cases in the existing verifier test class or the G-1 file once it exists. Do not change verifier codes.
3. **Lower constants/names/return.** Grow `supported()` and `emit_const`/`emit_name` for `str`. One new `tests/fixtures/mir_leaf/` module. `test_direct_vs_mir_bytes` plus hash-stable plus verify-clean.
4. **Emit `%ps.str`.** `miremit` materializes `MIR_S` constants through `string_value` and recovers the alloca/widen path for multi-use name slots. Byte identity on the new fixture. If temps diverge, match the direct walk; do not change the direct emitter.
5. **Compare and `len`.** Helper-backed `==`/`!=` and `len`. Second fixture. Still no frozen-file edits.
6. **Receipt.** Note the fixture names, whether any census pin moved, and that IR/TIR hashes were not regenerated. Point at M-2 for containers and M-5 for recording hashes beside `TIR_HASHES.json`.

Stop at the first hour that does not finish; the remainder is the next PR.

## Open decisions for the owner

1. **Tag value.** `MIR_S = 3` and reserve `MIR_P = 4` now, or wait until M-2 to pick the pointer tag? Reserving avoids a serializer-visible renumber. Recommended: reserve both in the same `mir.py` change as task 1.
2. **Sentinel vs pool index 0.** Keep the current `-(index + 1)` encoding (so index 0 is `-1`) and the "required operand is never unused" invariant, or shift const-refs to `-(index + 2)` so `-1` is unique? The shift would move every existing MIR digest of every leaf function. Recommended: do not shift.
3. **`bytes` in the same tag.** Same LLVM aggregate, different HIR kind. One MIR tag with a pool flag, or `MIR_Y` (bytes) beside `MIR_S`? Recommended: one tag for the first landing (`MIR_S` only); `bytes` is a follow-on so the slice stays one hour.
4. **Header bump.** Add `mir_schema=1` to the `fn`/`module` serialize header now (forces a full digest move when M-5 pins hashes) or keep the header unchanged until M-5? Recommended: keep the header unchanged until M-5 decides the probe-file shape; an additive `const … s` line is enough.
5. **Default lane.** This slice does not flip `PYSC_MIR`. Owner signal only, per the handoff.

Blocked on: nothing for this note. Implementation is blocked on the owner picking (2) if they disagree with "do not shift", because a encoding shift is a one-time digest move that M-5 would then pin.
