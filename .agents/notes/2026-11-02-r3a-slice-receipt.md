# R3a slice 1 receipt - typed IR nodes, lower/_core conversion

Branch `work/r3a` (base: `work/tirgate` at 5009da79). Commits in order:

- `8316683b` feat(ir): the typed HIR node dataclasses, 0 fences under pyscriptc
- `69ce4620` feat(tirserde): typed-node reconstruction with byte-identical canonical bytes
- `f145a8a9` test(ir): pin the sibling-dataclass import path and resolve the admission decision
- `bd3b6b3c` feat(tirserde): dict_to_node admission, the reverse of node_to_dict
- `f54b15e`  fix(tirserde): preserve the typevar name across the typed-node round trip
- `c3e5853`  refactor(lower): build the _core helper nodes as typed _irnode values

## 1. Import-table admission (resolution)

`src/pyscriptc/_irnode.py` is admitted WITHOUT touching the closed import
table (`lower/__init__.py` QUEUE / LOWERED). Sibling imports inside the
`pyscriptc` package route through the own-package path
(`_bind_from` -> `_is_own_package`), so `from .tirserde import T` in a
sibling module fences nothing. Control experiment confirmed the absolute
spelling `from pyscriptc.tirserde import ...` DOES fence PS2002; the
relative spelling is clean. Adding `pyscriptc._irnode` to QUEUE would be
harmful (LOWERED = frozenset(QUEUE) -> PS2001 on member use).

Decision note: `.agents/plans/decisions/2026-11-02-import-table-admits-ir-module.md`.

## 2. The node module

`_irnode.py` holds concrete non-inheriting dataclasses: a `kind` tag, an
`flags: int` bitmask instead of bools, typed payload fields, and per-kind
tagged classes (`T` for types, `Span`, `Node`, `Pair`, `Stub`, ...).
Optional reference fields come first (no default), scalar str/int fields
after (defaulted); int sentinels default to 0 and callers pass -1.
Compiles under pyscriptc with **0 fences**.

`tirserde.py` gained `node_to_dict` (a typed node serializes to the SAME
canonical bytes as the equivalent dict IR) and, in bd3b6b3c, the reverse
`dict_to_node` / `dict_to_type` / `dict_to_span`.

One bug found and fixed during round-trip testing: `setitem_stmt` originally
dropped the container (stored `object=None`); `_nd_key` reads it from
`node.object`, so the container is now stored there. Also, a bare
`{"kind": "typevar"}` is lossy: `{"kind": "typevar", "name": "T"}` must
round trip because `_subst_type` resolves the placeholder by name. Fixed
in `_type_dict` + `dict_to_type` (f54b15e).

Round trip proven node-for-node on the merged 800-probe module.

## 3. Consumer conversion: lower/_core.py

Node-construction helpers in `lower/_core.py` now build typed nodes via
the `_ir.*` factories and hand back `node_to_dict(...)` behind the
compatibility accessor, so the emitter and all other readers still see the
live dict shape. Converted helpers: `_assign`, `_concat_str`,
`_const_str_join`, `_const_list_concat`, `name`, `_const_int`,
`_const_bool`, `_const_none`, `_len_call`, `_binary_int`, `_compare`,
`_tuple_assign`, `_zero`, `_attr`, `_item`, `_const_str`, `_raise_value`.
Each verified dict-identical against its old body.

## 4. Gate results (byte identity)

- `irscore` vs IR_HASHES.json: PASS, stable across two runs (cmp equal).
  Only the 2 known env diffs (stdlib.sys.base_exec_prefix / base_prefix).
- `tirscore` vs TIR_HASHES.json: "baseline matches", clean, stable across
  two runs (cmp equal). Run with TMPDIR=/tmp/r3atmp (shared /tmp fills up;
  the ryu session actively writes /tmp/ryupt).
- Census (parse-only, census_self --json): fences 2755 -> 2756, modules 96 -> 96.

## 5. Fence delta: +4, attribution-only

Unique-fence diff base vs new on (code, file, message): 1305 -> 1309.
The only additions are 4 export-cascade PS2002 fences in `lower/_core.py`
for `pyscriptc.tirserde.{dict_to_node,dict_to_span,dict_to_type,node_to_dict}`.
They cascade from PS1104 `Any` fences that ALREADY existed in tirserde's
own defs in the base (node_to_dict itself was PS1104 in base) but had no
importer until now. tirserde's own fence count is unchanged (50 in both).
All `_core.py` line-shift noise cancels in the set diff.

## 6. Environment incident (unresolved, recorded)

At 23:38:32 foreign uncommitted changes appeared in this worktree:
`emit/llvm/{_core,_exprs,_runtime,_stmts}.py` and
`runtime/{ps_rt.c,ps_rt.h}` (ps_str_get_at O(1) string-index work).
They are committed nowhere on any branch (pickaxe across --all finds
nothing) and match nobody's committed work. An AUTO_MERGE tree (76a9f8ce)
was written at the same timestamp; the reflog shows repeated resets just
before. Looks like another agent session's `git stash` spilled into the
shared worktree admin dir, or a failed autostash merge. They were NOT
committed and NOT discarded - they remain dirty in the worktree, flagged
for the owning session.
