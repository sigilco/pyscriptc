# `json_like_dump`: what unfencing `object` + `isinstance` narrowing would cost

Date: 2026-11-06. Scope question from the MIR-slice work on `benchmarks/compat2/corpus/scripts/`: the script `json_like_dump` is fenced at `def dump(value: object) -> str` (`PS1104`) with a cascading `PS2002` on the recursive `dump(...)` calls. This note sizes what it would take to lower it natively, compares that against the annotation-only typing scope, and recommends whether to implement it or keep the documented fence. Docs only; no code.

Read first: `benchmarks/compat2/corpus/scripts/json_like_dump/main.py`, `docs/guide/python-subset.md` (the typing table; `docs/guide/typing.md` does not exist on this branch — the per-construct table lives in `python-subset.md`), `.agents/docs/python-style.md`, `src/pyscriptc/lower/_classes_more.py` (`_isinstance_type`, `_isinstance_holds`), `src/pyscriptc/restlower/_builtins.py` (`_isinstance`), `src/pyscriptc/lower/_stmt_a.py` (`_narrow_names`, `_narrowed`), `src/pyscriptc/lower/_annotations.py` (union refusals), `src/pyscriptc/lower/_infer.py` (`_infer_signature`, `_infer_yield_type`).

## The script

```python
def dump(value: object) -> str:
    if isinstance(value, bool): ...
    if isinstance(value, int): ...
    if isinstance(value, str): ...
    if isinstance(value, list): ...   # ",".join(dump(v) for v in value)
    if isinstance(value, dict): ...   # value.items(), dump(k) + ":" + dump(v)
    return "null"
```

One function, recursive, over a genuinely dynamic value: bool, int, str, list, dict, or None. Every operation on `value` (`str()`, truthiness, iteration, `.items()`, the recursive call) is only meaningful after an `isinstance` guard picks the arm.

## What blocks it today

1. `PS1104` on `value: object`. Bare `object` (and bare `Any`) is deliberately not a lowered type; the annotation table in `docs/guide/python-subset.md` lists it under the unsupported rows. The IR has no dynamic or sum type: every value's type is fully static, decided at lowering time.
2. `PS2002 call to dump is not lowered` — cascade from (1); the signature never resolves.

Even if `object` were admitted, three more gaps sit behind it:

3. **`isinstance` is a compile-time fold, not a runtime check with narrowing.** `restlower/_builtins.py::_isinstance` evaluates `_isinstance_holds(obj["type"], want)` — a static type comparison that returns a `const` bool (the only runtime case is caught exceptions via `ps_exc_isa_value`). There is no flow-sensitive narrowing: `_narrow_names`/`_narrowed` in `lower/_stmt_a.py` only unwrap `T | None` optionals on `is None` / truthiness tests. Nothing rewrites `value`'s type inside the `isinstance(value, int)` arm, so `str(value)` and `value.items()` still would not typecheck.
4. **Container element types do not survive the guard.** `isinstance(value, list)` spells a *bare* leaf (`_bare_leaf`); the element type is unknown. Iterating gives an unresolved element (the `PS1107` "unproven read" fence), and `dump(v)` must pass it back into a parameter whose type must accept every arm — i.e. the dynamic type again. Same for `dict.items()` pairs and `",".join(<generator>)` over the recursively produced strings.
5. **No annotation-only spelling exists for the sum type.** `X | Y` and `Union[...]` are refused `PS1101`/`PS1102` ("union types are not in the IR"). `dict[str, Any]` / `list[Any]` compile as container elements but bare `Any`/`object` parameters are `PS1104`, element reads need a proven type (`PS1107`), and `@overload` is dropped with no runtime enforcement (divergence 14). The script is exactly the shape the annotation-only scope excludes on purpose: one name, many unrelated runtime types, dispatched by `isinstance`.

## Cost of unfencing

| Piece | Cost | Notes |
| --- | --- | --- |
| Dynamic value representation | **XL, multi-milestone** | A runtime-tagged union or boxed `PyObject` lane through IR, LLVM emit, and every operator/call/print path. This is the real price of `object`; it is also the feature the boxed `--fallback=cpython` tier already provides as the designed escape hatch (`docs/guide/libraries.md`). |
| `isinstance` narrowing | **M–L, only pays off on top of the above** | Needs runtime type tags on values (today types are static-only), a narrowed-type environment per branch, and a join at merge points. `_narrow_names` is the existing seam but only models optional unwrapping. |
| Element typing through narrowing | **M** | `list`/`dict` guards must produce `list[dynamic]`/`dict[dynamic, dynamic]` and iteration/`.items()` must propagate it — meaningless until the dynamic element exists. |
| `join` over generator, recursion | **S once the above land** | Ordinary call/iter lowering; the genexpr and recursive call add no new mechanism. |

Total: unfencing this script natively is effectively "add a dynamic value type to the compiler" — the single largest feature the annotation-only design has deliberately excluded. It is not a fence tweak; it is a different typing model.

## Comparison with the annotation-only scope

The typing contract (`docs/guide/python-subset.md`, `.agents/docs/python-style.md`) is explicit: annotations are the lowering input, `typing` is annotation-only, unions are not in the IR, bare `Any`/`object` are `PS1104`, and constructs with runtime meaning beyond their annotation run through the boxed fallback. `json_like_dump` needs runtime dispatch on a value whose type is not statically knowable — precisely what that contract refuses to represent. The fence is the design working, not a missing feature.

## Recommendation

**Keep it as a documented fence.** Do not implement object typing or `isinstance` narrowing for this script:

- The cost is a dynamic-value representation through the whole pipeline — out of all proportion to one corpus script, and it would re-open the exact boundary the annotation-only scope was drawn to hold.
- The designed path for genuinely dynamic code already exists: the boxed `--fallback=cpython` tier runs scripts like this through embedded CPython. The user-facing answer is "use fallback mode for JSON-shaped recursive data", which `docs/guide/libraries.md` already documents.
- `isinstance` narrowing alone buys nothing here: with static types only, `isinstance` already folds at compile time, and the script's arms are unreachable without the dynamic type underneath.

If a future release ever wants native JSON-shaped data, the honest sequencing is: (1) decide the dynamic representation (tagged union in IR vs. island/boxed values), (2) land it as its own epic with divergence entries, (3) then isinstance narrowing becomes a small follow-up. Until then the `PS1104` + `PS2002` pair on `json_like_dump` is the correct, documented outcome.
