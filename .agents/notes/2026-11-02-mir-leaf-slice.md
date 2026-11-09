# MIR leaf slice — receipts (2026-11-02)

Branch: `work/mir` (worktree `~/.worktrees/pyscriptc-mir`), roadmap R4.

## A/B byte identity (direct emit vs `emit_llvm_mir`)

| Sample | Result |
|---|---|
| /tmp/leafint.py | IDENTICAL |
| /tmp/leafbool.py | IDENTICAL |
| /tmp/mirleaf.py | IDENTICAL (3 pure functions) |
| /tmp/mirmix.py | IDENTICAL (mixed: `g` in slice; `main`, `big` ride direct path) |

CLI-path check: `cmp /tmp/mirmix_direct.ll /tmp/mirmix_mir.ll` → IDENTICAL
(files differ by env `PYSC_MIR=1` opt-in only; default path unchanged).

## Canonical hash stability

`module_hash(lower_module(m))` equal across two runs for all four samples
(pinned in `tests/test_mir_leaf.py::TestByteIdentity::test_mir_hash_stable`).

## Fences

All leaf samples: 0 fences (`len(module["fences"]) == 0`).
`emit_llvm_mir` refuses modules with fences (frozen gate preserved).

## Skipped (out-of-slice) functions on mirmix.py

`skipped == ['big', 'main']` — emitted by the direct path through the splice.

## Tests

`tests/test_mir_leaf.py`: 20 passed.
- Byte identity + hash stability + verify-clean for all 4 samples (12 tests).
- Verifier MVS codes pinned: MVS01 (type), MVS02 (use-def), MVS03 (control),
  on hand-broken functions: use-before-def, word br-condition, missing
  terminator, jmp to missing block, mismatched ret type; builder refuses
  dangling open block; copy tag rides arg2.

## `mise run check`

- lint: All checks passed.
- format-check: 612 files already formatted.
- tests (tests/compat2, minus linux_container): 131 passed, 1 failed —
  `test_must_fence_case[code.PS1001]`.
  **Pre-existing on master**: reproduced with all work/mir changes stashed
  and on `master` checkout. Not caused by this branch.

## Commits (work/mir, this session)

- 15aab82b test: miremit byte identity and mirverify MVS codes
- 6c419947 fix(mir): lint cleanup in the leaf slice
(earlier: e6ff27f4, aa0314a9, 5d4248d1, 67ef1882, 30e6c3ab, 96d8093f, d81c5b03)
