# Split plan: src/pyscriptc/lower/__init__.py (3,736 lines, under about 1,500)

Issue #852. Method: `.agents/notes/2026-11-04-orca-file-split-method.md` (the
orca playbook). Data: `mise run lower:report src/pyscriptc/lower/__init__.py
--json` (scripts/lower_split_report.py, commit 6841b8cd). Everything below is
from that report unless noted.

## What the report says

`Lowerer` declares 86 methods, 2,966 method lines (the other 770 lines are
module-level IR node builders, `lower_file`/`render_fences`, and imports).
Call-graph clustering with entry points excluded as connectors (the orca
"spine" rule) gives 39 clusters. The top ones:

| cluster | lines | methods | family |
|---|---|---|---|
| #0 | 880 | 29 | boxed/island resolution + emit plumbing (`_boxed_resolve`, `_boxed_probe`, `_boxed_island_source`, `_drain_instances`, `_body`, `_globals_for`, `_activate_slot`, slot/global bind family) |
| #16 | 352 | `module` | the module driver (root; calls 13 clusters) |
| #2 | 480 | 8 | type inference and pinning (`_infer_type_of`, `_call_site_types`, `_pin_call_site_params`, `_bindings_types`, `_pin_namespace_locals`, `_pin_annotation`, `_has_default`, `_tuple_fields_of`) |
| #1 | 179 | 10 | entry-point construction (`_entry_function`, `_make_entry`, `_is_program_main`, version/TYPE_CHECKING gates, `_live_statements`) |
| #4 | 114 | 2 | `_split_entry`, `_diverted_literals` |
| #5 | 227 | `__init__` | state setup, 80 attributes |
| #10 | 112 | `_infer_signature` | signature inference (calls #2) |
| #30 | 67 | `_comp` | comprehensions |
| plus | ~260 | 21 | small singletons: `_linked_call` 27, `_setitem` 49, `_finish_resolve` 25, `_use_status` 17, `_bind_one_export` 58, `_infer_type_known` 29, `_is_program_module` 23, `_bridge_*` 5 each |

Greedy cut set (report `best_cut`): move clusters {2, 1, 4, 10, 16, 30 and the
small singletons}, keep #0 resident. That lands `__init__.py` at 1,149
remaining method lines (+ helpers, imports, and the module-level builders
still there: realistically 1,600-1,900 total), so one more wave after it. The
cut crosses 25 call edges, all of them `self.` calls that keep working across
mixins for free.

The report also flags: `module` 352 lines cc 92, `_call_site_types` 198 cc 80,
`_boxed_resolve` 159 cc 51, `_infer_signature` 112 cc 39, `__init__` 227
writes 80 attributes; 24 attributes are written only in `__init__` (config
echo: `config`, `filename`, `source`, `target`, `program`, `bridge`...).
No import cycles among lower/ modules today. Ten imported names are unused
in the module itself (re-export shims from the #910-#915 splits, e.g.
`_concat_str`, `_const_list_concat`, `_static_int`, plus `lower_file`/
`render_fences` which cli/_main.py and coverage.py import from here).

## Target shape: five new mixin modules

Follow the #910-#915 recipe: whole methods move into a mixin class, `Lowerer`
keeps composing mixins, `self` state keeps working (no deps-map facade needed;
in Python the capture-set check is advisory, not a correctness gate). One
conventional commit per module, moved methods byte-identical.

1. `_infer.py` (`TypeInferMixin`, ~700 lines, from clusters #2, #10, #9, #11,
   plus `_bridge_type` #3-#8 family): `_infer_type_of`, `_infer_signature`,
   `_infer_type_known`, `_infer_fence`, `_call_site_types`, `_pin_*`,
   `_bindings_types`, `_tuple_fields_of`, `_has_default`, `_bridge_type`.
   Consumed by `_stmt.py` (4 call sites), `_expr.py`, `_calls.py` today;
   imports move with the methods.
2. `_entry.py` (`EntryMixin`, ~330 lines, clusters #1, #4, #21):
   `_entry_function`, `_make_entry`, `_is_program_main`, `_is_main_module`,
   `_is_program_module`, `_split_entry`, `_diverted_literals`,
   `_live_statements`, `_static_branch`, version/TYPE_CHECKING gates.
3. `_stmt_small.py` (name it by concern after reading, e.g. `_misc_emit.py`;
   ~250 lines, clusters #30, #24, #25, #26, #29, #31, #35-#38): `_comp`,
   `_setitem`, `_setattr`, `_bool_cond_of`, `_set_display`,
   `_store_tuple_constant`, `_is_none`, `_slot_read_name`,
   `_materialize_tuple`, `_temp`. The name must not be `helpers`/`utils`
   (orca rule); split by concern if it does not cohere.
4. `_module_driver.py` (`ModuleDriverMixin`, `module` itself, 352 lines):
   the driver moves last, after 1-3 have landed, so its body calls methods
   that already live in sibling mixins. Not split internally on wave one;
   the 352 lines/cc 92 internal decomposition (scan phase vs emit phase) is
   its own follow-up issue.
5. `_init_state.py` is NOT a code module: `__init__` stays put (227 lines).
   Instead add a class-level dataclass-ish grouping only if a later wave
   needs it; #5 documents the risk instead (below).

## Order and gates

Order: `_infer.py` first (biggest, zero inbound edges from siblings except
the four listed call sites that keep working), then `_entry.py`, then the
small emit family, then `_module_driver.py`. After each move: `ruff check` on
changed files, `uv run pytest tests/test_census_self.py -q` (`pyscriptc.lower`
fence ceiling is pinned at 201 in tests/census_pins.json; a move can shift
counts, `scripts/repin_census.py` regenerates, keep unrelated pins untouched),
the compat2 unit selection, and the report re-run to confirm the remaining
line count. Full `uv run pytest -n auto -m 'not linux_container'` at
milestones only (orca rule: cheap gates per batch, expensive serialized).
Commit messages carry the before/after LOC, like #913.

After wave one re-run the report and cut wave two (candidates: the boxed/
island family #0, 880 lines, and the slot/global bind family) until the file
sits under 1,500. Delete the ten unused import shims only in a separate
commit after grepping external users (`cli/_main.py` re-exports
`lower_file`/`render_fences` dynamically; keep those two).

## Risks

- `__init__` writes 80 attributes and `package.py` (two sites) pokes
  `lowerer.module_name`, `.is_package`, `.package`, `.exc_n`, `.program`,
  `.program_roots`, `.module_init`, `.entry_module`, `.bridge`,
  `.cataloguing` between construction and `module(tree)`. Any extraction
  that changes construction order or turns these into properties breaks the
  driver: keep `__init__` in `__init__.py` untouched and keep attributes
  plain.
- `module` is the external entry (package.py calls `lowerer.module(tree)`);
  moving it into a mixin is safe (same class) but it must stay the first MRO
  method that the driver path resolves; do not introduce same-named methods
  in other mixins.
- Sibling mixins already call `_infer_type_known`/`_infer_fence` (stmt),
  `_linked_call` (calls, call_attr), `_comp` (expr), `_finish_resolve`/
  `_use_status` (stdlib). These keep working through MRO, but the moved
  modules must not import each other in a cycle: the report's cycle check is
  the gate (currently none).
- `caught`/`prelude`/`declared`/`locals`/`temp_n` are written by up to 11
  methods across every family: they are the connectors. They stay shared
  instance state; no cluster may "own" them. The orca equivalent was
  extracting state ownership first; here the state stays on `Lowerer`, so
  the cut is behavioral-free but the families are not fully closed: 25
  cross-cluster `self.` edges remain and each moved method keeps reading
  them.
- The census pin (`pyscriptc.lower` 201) is a frozen-ish gate: splitting the
  file must not change fence counts. Byte-identical method moves make that
  hold; any "small cleanup while here" breaks it.
