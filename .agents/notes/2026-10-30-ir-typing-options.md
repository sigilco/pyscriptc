# IR typing options: tagged unions in the subset vs a dataclass IR

Date: 2026-10-30. Question: should the typed subset accept tagged unions
(Option B), should the compiler switch its own IR from `dict[str, Any]` to a
dataclass (Option A), or stage both (Option C)? The motivation is the
compiler's own lowering rate: 732 PS1104 fences in `src/pyscriptc`, 638 of
them IR dict annotations.

## Measurements

All numbers from `pyscriptc coverage --json` (`.venv314`, CPython 3.14) and
the existing corpora; prototype probes are throwaway files in `/tmp`.

- Baselines: `lower/_core.py` 45 fences (34 PS1104, 8 PS1106, 3 PS2002);
  `emit/llvm/_abi.py` 40 fences (33 PS1104, 1 PS1204, 6 PS2002).
- Annotation-site census: 1925 IR-dict annotations across 84 files. Top:
  `coro.py` 175, `lower/_calls.py` 171, `lower/_stmt.py` 144,
  `lower/__init__.py` 137, `lower/_expr.py` 128, `lower/_classes.py` 85,
  `lower/_core.py` 78. 1283 sites are in files that read `["value"]`, the
  heterogeneous const payload.
- Perf at 50k nodes (yt-dlp module scale): dict build 760ms / 54.6MB,
  dataclass 678ms / 47.0MB, slots 755ms / 45.0MB; read pass 14.0ms vs 9.3ms.
  Canonical serialize ~220ms either way. The `as_dict()` round trip at the
  JSON boundary costs 687ms for 50k nodes, which eats the win wherever IR
  must be serialized (`cli.py:1997`, `debug.py:195`, boxed run report read
  at `cli.py:902`).
- User-facing union fences (Option B's win) are negligible today: yt-dlp
  census 1 PS1101, 0 PS1102 out of 14595 fences; R0 real-scripts 0;
  `must_fence` 2 each (expected-refusal probes).

## Prototype probes (what the subset actually refuses)

Concretized, fence-free forms were found; each refusal is a rule, not a bug.

1. A fully concrete same-module dataclass (`Node` with `str`, `Node | None`,
   `list[Node]` fields plus `Span`/`Type` dataclasses) compiles with
   **0 fences**, including recursive fields and `list[Node]`. So Option A is
   mechanically possible.
2. `bool` fields are refused (PS2002 "bool fields are not lowered"). The
   `is_bool` flag workaround fails. Payload discrimination must use a `str`
   tag and str payloads; a fence-free variant with `ptag: str` and
   `svalue: str` also reaches 0 fences. This means the const `value`
   heterogeneity (`str`/`bool`/`None` across kinds, `_expr.py:322-333`) is
   solved only by per-kind tag+str fields or by changing the IR shape.
3. `Any` fields fence (PS1104). A Node carrying `value: Any` keeps the very
   fence Option A is meant to remove, just relocated.
4. Dataclass inheritance is refused (PS2002 "a dataclass has no base"), so
   the per-kind class hierarchy variant (`Const(Node)`, ...) is dead in the
   subset as it stands.
5. Cross-module import of the dataclass fences even when the class is fully
   concrete (PS2002 "not in the closed import table" + PS1106). A real
   rollout needs the import table (or per-module copies) extended, which is
   a frozen-gate-adjacent change.
6. `len(list_field)` fences (PS2003); a local rebind plus `len(elems)` on
   the narrowed local compiles. Small, but every helper needs the idiom.

## Comparison

| | Option A: dataclass IR | Option B: tagged unions in subset | Option C: staged |
|---|---|---|---|
| Effort | L: Node schema with per-kind key order for byte-identical JSON, convert ~84 files / 1925 sites, import-table change, `as_dict()` boundary | M-L: class-tagged layout, narrowing on class tags in `match`/if-chain, LLVM layout for tagged payload, erasable-typing parity | L overall |
| Risk | Canonical JSON must stay byte-identical (frozen `IR_HASHES.json`, 1.4MB baseline); `as_dict()` perf tax at serialize boundaries; frozen-gate touches | CPython typing-parity divergence (subset would lower what CPython erases); new divergence-doc entries; widens the spec | Same as whichever stage lands |
| Gate impact | Frozen IR hashes must be re-blessed only if `as_dict()` order slips; import-table rule change needs a recorded decision | PS1101/PS1102 semantics change; `must_fence` probes that assert those refusals must be updated (recorded decision) | Both of the above, split |
| Compiler lowering gain | Large if fully concrete: the 638 IR PS1104 fences become lowerable; measured 0 fences on the extracted cluster | Tiny for users (1 PS1101 in yt-dlp), but would also unlock the compiler's own `A | B` uses | Same as A |
| User lowering gain | None directly | Negligible on existing corpora (1 fence) | None |
| Perf | Slightly faster to build/read, smaller; loses ~687ms/50k nodes at serialize boundaries unless IR stays objects end-to-end | No change | n/a |

## Assessment

Option B buys almost nothing today: the user-facing union-refusal fences
number 1 in the largest available corpus. Its cost is a real divergence from
erasable typing plus LLVM tagged-payload work. Not worth it for the fence
count alone.

Option A is feasible but its payoff depends on full concretization: `Any`
payloads and bool fields fence regardless of the container, so the honest
form is tag-plus-str payloads, a `Type`/`Span` dataclass family, an
import-table extension for cross-module sharing, and `as_dict()` only at the
JSON boundary. That is an L-effort change touching the canonical-hash gate,
with a modest perf win in memory and a real tax at serialize points.

The compiler's 638 PS1104 fences are real friction, but they are friction the
project chose: the dict IR is what keeps canonical JSON trivially stable.
The measured user-side win of Option B does not justify its divergence cost,
and Option A's gate coupling means it should not be snuck in as a cleanup.

## Recommendation

Do not start either option now. If the compiler-lowering friction becomes a
priority, the staged path is:

1. First slice (S): adopt the measured idioms in a prototype branch only
   (`work/irtype` / `proto/irtype-dataclass-ir` has `src/pyscriptc/irnode.py`
   with per-kind key-order `as_dict()`; probes in this note). Re-measure
   fences on `lower/_core.py` and `emit/llvm/_abi.py` with the concrete
   forms; publish the delta before committing to the migration.
2. Only then decide the import-table question, which is the single rule
   change everything else depends on.
3. Option B stays closed unless a real corpus shows union-refusal fences
   materializing; re-check PS1101/PS1102 counts when the next external
   census runs.

## Decisions the owner would need to record if proceeding

- Extend the closed import table to admit a lowered shared IR module
  (changes a frozen-gate-adjacent rule).
- Re-bless `benchmarks/compat2/probes/IR_HASHES.json` under a provably
  byte-identical `as_dict()` contract (ir-canonicity.md).
- If Option B is ever opened: PS1101/PS1102 refusal semantics change and the
  `must_fence` expected-refusal probes must be updated.
