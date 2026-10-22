# E0 cost baseline

Measured on `src/pyscriptc/` at commit `2a07bb875914cda7111e0f6280ab5f7c031f7521` (master, 2026-10-15). Line counts are `wc -l`. Import graph and per-symbol dependency data come from an AST walk of the sources, not runtime imports. This is the "before" number for the E0 exit criterion: largest lowering file down at least 60% in lines.

## Module line counts

Modules above 5 KB plus the rest of the package. The compiler side is 11,625 lines of Python; `runtime/*.c` (about 268 KB, dominated by `ps_unicode.c` and `ps_rt.c`) is out of scope for E0.

| Module | Lines | Share of package |
|---|---:|---:|
| lower.py | 5554 | 47.8% |
| llvm.py | 1884 | 16.2% |
| stdmore.py | 1611 | 13.9% |
| restlower.py | 844 | 7.3% |
| coverage.py | 388 | 3.3% |
| ir.py | 365 | 3.1% |
| package.py | 254 | 2.2% |
| stdlib_queue.py | 191 | 1.6% |
| diagnostics.py | 179 | 1.5% |
| cli.py | 143 | 1.2% |
| builtins.py | 101 | 0.9% |
| errors.py | 56 | 0.5% |
| check.py | 49 | 0.4% |
| \_\_main\_\_.py / \_\_init\_\_.py | 6 | <0.1% |

`lower.py` is the target. E0's 60% bar means the largest file under `lower/` must land at or below 2222 lines.

## Import graph

AST-derived `pyscriptc`-internal imports, fan-out = imports, fan-in = imported by.

| Module | Out | In | Imports |
|---|---:|---:|---|
| cli | 8 | 1 | check, coverage, diagnostics, errors, ir, llvm, lower, package |
| lower | 6 | 3 | builtins, errors, ir, restlower, stdlib_queue, stdmore |
| llvm | 4 | 1 | builtins, errors, ir, stdlib_queue |
| package | 3 | 2 | errors, ir, lower |
| coverage | 3 | 1 | errors, lower, package |
| restlower | 2 | 1 | ir, stdmore |
| ir | 1 | 5 | errors |
| \_\_main\_\_ | 1 | 0 | cli |
| errors | 0 | 6 | (none) |
| builtins | 0 | 2 | (none) |
| stdlib_queue | 0 | 2 | (none) |
| stdmore | 0 | 2 | (none) |
| check | 0 | 1 | (none) |
| diagnostics | 0 | 1 | (none) |

No cycles. Layers are clean: `errors` at the bottom, `ir`/`builtins`/`stdlib_queue`/`stdmore` above it, then `restlower`, then `lower`, then `package`/`coverage`, then `cli`. Note the one wrinkle: `lower.py` imports `restlower`, and `restlower` imports `stdmore` plus a function-local `pyscriptc.ir` import, so a `lower/` package importing `restlower` is safe but pulls the rest-lowering layer with it.

## lower.py dependency depth

`lower.py` has 60 top-level symbols: `Fence`, `Binding`, `Lowerer`, and 57 module-level helpers. `Lowerer` alone spans 4798 of 5554 lines and holds 197 methods. Per-symbol dependency on sibling compiler modules (count of referenced names from each import):

| Imported module | Top-level symbols using it | Lowerer methods using it | Name references in lower.py |
|---|---:|---:|---:|
| errors | 2 | 26 | 46 |
| ir | 3 | 19 | 27 |
| builtins | 2 | 8 | 11 |
| restlower | 1 (Lowerer) | 11 | 15 |
| stdlib_queue | 1 (Lowerer) | 9 | 23 |
| stdmore | 1 (Lowerer) | 14 | 6 distinct names (13 constants + 2 functions) |

Everything outside `Lowerer` is nearly dependency-free: of the 59 non-`Lowerer` symbols, only 5 touch a sibling module (`render_fences` -> errors; `_const_list_concat`, `_type_slug` -> ir; `_handler_names` -> builtins). The remaining helpers are pure AST-to-string utilities. Dependency-wise the file splits at the `Lowerer` boundary almost for free.

Inside `Lowerer`, 134 of 197 methods reference no sibling compiler module at all. The coupled ones are the dispatcher entries: `module`, `_bind_*` (stdlib_queue, stdmore), `_expr`/`_name`/`_call_name`/`_call_attr`/`_module_call`/`_lookup_attr` (builtins, restlower, stdmore, stdlib_queue), and the error-heavy binding paths. No `Lowerer` method touches `llvm.py`, and `llvm.py` never imports `lower.py`; the lowering and emit stages meet only through `cli.py`. Same for `ir.py`: lower.py uses it read-only (`SUBSET`, `type_key`, `types_equal`, `cpython_version`), never constructs IR nodes.

## Coupling table for the planned `lower/` split

Best-effort assignment of every top-level symbol and `Lowerer` method to one of the six planned areas, by name and docstring keywords. This is a guess about grouping, not a call-graph analysis; methods dispatch into each other, so the real split will move helpers across areas.

| Planned module | Symbols | Lines | Notes |
|---|---:|---:|---|
| expr | 68 | 1222 | `_const*`, `_binary`, `_compare*`, `_bool*`, `_ifexp*`, `_slice*`, `_joined`, `_percent`, `_tuple*` |
| stmt | 50 | 1262 | `_stmt*`, `_for*`, `_user_while`, `_try`, `_match*`, `_with`, loop plumbing |
| classes | 29 | 590 | `_collect_class`, `_lower_class`, `_method*`, `_enum*`, decorators, `_signature`, `_generated_method` |
| calls | 19 | 447 | `_call*`, `_fill_args`, `_fill_varargs`, `_construct`, `_method_call`, arg placement |
| intrinsics | 4 | 53 | by name only: `_json_*`, `_refuse_pattern` |
| stdlib/ | 31 | 704 | `module`, `_bind_*`, `_resolve_module`, `_use_status`, `_note_module`, `_finish_resolve` |
| (unmatched) | 55 | 883 | pure helpers: `Fence`, `Binding`, `_ir_name`, `_temp`, `_show`, const-fold utils |

The `intrinsics` number is misleadingly low because name matching cannot see dispatch: `_call_name`, `_call_attr`, `_module_call` and the `_struct_call`/`_hash_method`/`_pattern_method`/`_math_call`/`_nativeprobe_call` cluster (about 300 lines classified under `calls`) are the intrinsic entry points, reached via `_CMP`-style tables and name checks. A table-driven split (the plan says "intrinsics (one table)") would collect those explicitly.

## Notes for E0

Clean seams: the split is mostly a move, not a rewrite. 134 of 197 `Lowerer` methods and 54 of 59 helpers touch no sibling module. `stdlib/` dispatch is the cleanest first cut: the `_bind_*`/`_resolve_*` cluster (about 700 lines) only needs `errors`, `stdlib_queue` and `stdmore`, all leaf modules. `llvm.py` and `ir.py` are untouched by lowering and impose no ordering constraints.

Unclean seams: `Lowerer` is one 4798-line stateful class; every area method reads and writes the same instance state (`self.temp`, slots, fences, loop flags), so a package split needs either mixin classes or an explicit context object, and pure-move commits cannot avoid touching shared state declarations. `errors` is called from 26 methods across all areas, so every split module imports it; that is fine (it is a leaf) but means error-context plumbing should be settled first. The `intrinsics` area cannot be carved by name alone; its real boundary runs through `_call_name`/`_call_attr` dispatch, which also serves the `calls` area, so those two splits should land together. Finally, `package.py` and `coverage.py` import `lower.py` names (`Lowerer`, `lower_file`, `render_fences`), so the package's public surface must keep those three re-exported or the callers move in the same commit.
