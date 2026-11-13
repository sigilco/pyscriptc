# MIR unfence asks: direct-lowerer fences blocking corpus coverage

Date: 2026-11-06. Branch: `work/mir-recursion`. Companion to `.agents/notes/2026-11-06-mir-coverage-gap-map.md` (PR #1020). This note lists the direct-lowerer (non-MIR) fences that keep corpus functions out of the coverage denominator entirely, the exact lowerer location of each, a suggested fix, and the denominator warning that unfencing is not free: every unfenced function is counted by `scripts/mir_coverage.py`, so it must also reach the MIR slice or the percentage drops.

## advance (nbody): PS2002 augmented assignment on a non-name target

`benchmarks/compat2/corpus/scripts/nbody/main.py:37-45` writes `vx[i] -= dx * m[j] * mag` and friends: `ast.AugAssign` whose target is a `Subscript`. `StmtAMixin._aug_assign` in `src/pyscriptc/lower/_stmt_a.py` (the `self.fail(stmt, "PS2002", "augmented assignment is only lowered for a name")` at the top of the method, around line 503 on master) refuses every target that is not a `Name`. Suggested fix: desugar `t[i] op= v` into `t[i] = t[i] op v`, an `ast.Assign` with the same `Subscript` target (Store context) and a `BinOp` whose left is a Load-context copy of the target's value and slice; the existing name-target path already does exactly this rewrite for globals/nonlocals, and the `Assign`-with-`Subscript` path (`setitem`) is already lowered. Verified in a scratch trial (see below): the whole `nbody` script then compiles with 0 fences.

## shift (caesar_cipher) and sieve (sieve): PS1001 annotation inference, then PS2002 fallout

`caesar_cipher/main.py:2` is `out = []` inside `shift`; `sieve/main.py:2` is `flags = [True] * (n + 1)` inside `sieve`. Both die at `src/pyscriptc/lower/_infer.py` `_infer_fence` (`self.fail(node, "PS1001", f"{target} needs an annotation before use")`, around line 702), reached from the empty-or-unproven-display path in `src/pyscriptc/lower/_stmt.py` around line 362. For `shift`, `_infer_accumulated_type` (`src/pyscriptc/lower/_stmt_a.py`) scans `out.append(...)` uses but the appended values are `chr(...)` calls that `_infer_type_of` cannot type, so no element type is proven and the name keeps its fence; every later `out` read then fences PS2002 "out is not a local or parameter" at `src/pyscriptc/lower/_expr.py:417`. For `sieve`, `[True] * (n + 1)` is a `BinOp` `Mult` of a list display and an int, a shape `_infer_type_of` does not handle, so `flags` fences the same way with PS2002 fallout at `_expr.py:417`. Suggested fixes: teach `_infer_type_of` the `chr`/`ord` builtin return types so the accumulated-type pass can prove `list[str]` for `out`, and teach it `list[T] * int -> list[T]` (here `list[bool]`) for `flags`. Both also need the direct lowerer to accept `list[str]`/`list[bool]` element kinds end to end; that part is unverified.

## check (binary_trees): PS2001 attribute is not a field

`binary_trees/main.py` declares `class Tree` with no class-level annotated fields; `__init__` takes `left: Tree | None` / `right: Tree | None` and does `self.left = left`. Class fields are collected only from class-body `AnnAssign` nodes in `src/pyscriptc/lower/_classes.py` (the `fields.append(field)` loop, around lines 397-447); a `self.<attr> = ...` assignment then hits the setattr fence `self.fail(target, "PS2001", f"{class_name}.{target.attr} is not a field")` in `src/pyscriptc/lower/_misc_emit.py` around line 153 (the `__init__` writes at main.py:3-4), and attribute reads hit the matching read-side fence `self.fail(node, "PS2001", f"{class_name}.{name} is not a field")` in `src/pyscriptc/lower/_classes.py` around line 1230 (the `t.left` reads in `check` at main.py:14 and 16). Suggested fix: during class registration, treat `self.<name> = <param>` assignments in `__init__` as field declarations taking the parameter's annotation, including the optional-class spelling `Tree | None`; alternatively require class-level annotations, but the corpus is frozen so the lowerer must infer them.

## Denominator warning and trial measurement

Unfencing any of these removes the function from the fenced set, so `scripts/mir_coverage.py` counts it in `functions` (the denominator) even though its blockers (`str`, `list[str]`/`list[bool]` element kinds, class fields) are nowhere near the MIR slice today. The percentage can only drop until MIR grows to match.

Trial unfence, run in a scratch worktree on top of this branch: only the `advance` desugar above was applied (`src/pyscriptc/lower/_stmt_a.py`, about 15 lines). `pyscriptc compile benchmarks/compat2/corpus/scripts/nbody/main.py --debug=fences` then reports `fences: 0`, and `uv run python scripts/mir_coverage.py` reports:

```
TOTAL                           8        32  25%
```

versus `TOTAL 8 31 26%` on the branch without the trial: the denominator grew by one (`advance`, reason `param:list`) and the numerator did not move. Projecting the same mechanism to the remaining asks: `shift`, `sieve` and `check` are each currently the single fenced function in their script (`caesar_cipher` counts 1, `sieve` counts 1, `binary_trees` counts `bottom_up` and `main`), so unfencing all four asks lands the denominator at 35 with the numerator still at 8 unless MIR independently grows str/list-bool/class support: roughly 8/35 = 23%. Unfence only together with the matching MIR work, or the bar moves backwards.
