# Tiered lowering + checker-facts seam (notes adoption)

Date: 2026-10-18. Source: two design notes from Quim on refused-feature
analysis and checker strategy. This records the architecture decision;
implementation is sequenced after.

## Three lowering tiers for refused constructs

Every currently-refused construct routes into exactly one tier:

1. **Native lowering** — emit into the binary (default).
2. **Typed compile-time lowering** — erase, specialize, or desugar before
   LLVM emission: `Generic[T]`/`TypeVar`/`Protocol`/`Final` erasure,
   `Box[int]` → concrete `Box_int` specialization, known decorators
   (`@dataclass`, `@property`, …), kw-only/pos-only signature normalization,
   statically evaluable `eval`/`repr`/`hash` folds.
3. **Dynamic companion lowering** — CPython island or subprocess with an
   explicit boundary: `eval`/`exec` (island `eval_python(source, locals)`),
   dynamic `repr`/`hash` on user dunders, unknown decorators, runtime
   introspection of type parameters.

Routing rule: a construct goes to tier 2 when its semantics are fully
determined at compile time; tier 3 only when runtime Python behavior is
observable; tier 1 for everything else. Decorator order: compiler-known →
statically evaluable → companion-or-fence.

## Checker-facts seam (`TypeFacts` / `CallableFacts`)

Neither mypy nor pyright exposes a stable compiler API, so neither becomes
the compiler contract. pyscriptc owns a narrow facts IR:

```python
class TypeFacts:
    kind: str
    optional: bool
    args: tuple[TypeFacts, ...]
    callable: CallableFacts | None
    protocols: frozenset[str]
    runtime_repr: str | None


class CallableFacts:
    parameters: tuple[ParameterFacts, ...]
    return_type: TypeFacts
    positional_only: int
    keyword_only: frozenset[str]
    defaults: dict[str, StaticValue]
```

Adapters: `MypyFactsAdapter` now (already a dep, narrow seam only — query
facts, never leak `mypy.types` into lowering); `PyrightFactsAdapter` later,
consuming `sigilco/pyright`'s native binary in JSON/LSP mode as an external
oracle — NOT a vendored analyzer (Option C is only justified if broad typing
compat becomes the primary product goal).

### T2-5 boundary (landed)

`repr`/`str`/`hash`/`len`/`chr`/`ord`/`int`/`bool`/`abs` fold on
compile-time-constant arguments (`_static_builtin_fold` in `restlower.py`).
The boundary is CPython reproducibility: `chr` folds only inside the
Latin-1 literal range, `hash` only on int/bool (str/bytes hashing is
seed-randomized → tier 3), and `eval` only through `ast.literal_eval`
with a scalar result — arbitrary `eval`/`exec` stay fenced (tier 3).

## Sequencing

- E-TF (facts seam): extract the TypeFacts/CallableFacts interface + mypy
  adapter; no lowering changes. Foundation for everything below.
- Then tier-2 epics in payoff order: generics/ABC erasure, signature
  normalization (kw-only/pos-only/`**kwargs`), decorator desugaring,
  compile-time `repr`/`hash`/`eval` folds.
- Tier-3 epics where companion pays off: `eval_python` island, dynamic
  `repr`/`hash` dispatch.
