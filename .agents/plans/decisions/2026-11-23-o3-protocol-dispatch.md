# Recorded decision: protocol dispatch for user classes resolves statically against the operand's class (O3d, #435)

Status: Recorded 2026-11-23 (issue #435), awaiting owner review in the PR. Decision only: no lowering lands here. Unblocks O3a (#445, operators) and O3b (#446, container, iter and with protocols).

## Decision

User-class dunders get their CPython protocol roles through static resolution at lower time, the same mechanism `_dunder_call` (`src/pyscriptc/lower/_classes.py`) already uses for `str`, `repr`, `len`, same-class `==`, `for` over `__iter__`/`__next__` and `with`.
There is no vtable, no type word and no runtime table.
The operator, builtin or statement lowers to a direct call of the first definer of the dunder in the receiver's static class, or to the family fallback below, or to a fence.
Nothing is approximated: when the static class set cannot decide what CPython decides at run time, the use site fences.

Rules that hold for every family:

1. The dispatch key is the static class of the operand. Class slots hold exactly one class (divergence 70; the narrow carve-out keeps the concrete subclass in the slot), so the static class is the runtime class and static resolution matches CPython's type-level lookup. CPython looks dunders up on the type, never the instance, which is also what the lowering does.
2. The definer is the first class in the C3 `mro` that defines the dunder as a plain method (`method_kind == "method"`, the existing `_emit_owner` rule). A dunder bound as an attribute, a decorated one, a `staticmethod` or a `classmethod` counts as undefined, and the family fallback or fence applies.
3. Signatures are checked once, in `_dunder_call`, by extending its `want` table: `__eq__`, `__ne__`, `__lt__`, `__le__`, `__gt__`, `__ge__`, `__contains__` and `__bool__` return `bool` (`__exit__` is the `with` family's own rule below); `__len__` and `__hash__` return `int`. A mismatch is `PS1003` like today. No new diagnostic codes: an unresolvable operator is `PS2004`, an unresolvable construct is `PS2002`, as today.
4. `NotImplemented` stays a fence (`PS2002`, "NotImplemented is not a local or parameter"). CPython's try-left-then-fall-through-to-right is replaced by the fixed order below, which is the same as CPython for every class pair whose dunders never return `NotImplemented`. The guard that usually precedes a `NotImplemented` return, `isinstance(other, C)` on a class, is already fenced (`PS2001`) and belongs to O6 (closed-world `isinstance`), not here.
5. The `__eq__`-family parameter is annotated with the operand class (or an ancestor). The checker rejects that against `object.__eq__`, so the source carries `# type: ignore[override]`, exactly the shape `tests/test_dunder.py` uses. The checker-legal `other: object` form needs `isinstance` narrowing to a class and stays fenced until O6.
6. Derived consumers (`sorted`, `list.sort`, `min`, `max`, `in list[C]`, `list.index`, `==` on `list[C]`, dict and set keyed by `C`) follow the resolved dunder but are separate lowerings. A consumer that has no lowering yet keeps its current fence. Each lands in its own PR with a differential probe (#524); none is O3a or O3b scope unless named below.

## Per-family resolution order

### `==` and `!=` (O3a)

For `a == b` with static classes `Ca` and `Cb`:

1. If `Cb` is a strict subclass of `Ca` and its definer of `__eq__` differs from `Ca`'s, call `Cb.__eq__(b, a)` first (CPython's subclass-priority rule).
2. Else if `Ca` has a definer of `__eq__` whose parameter accepts `Cb`, call `Ca.__eq__(a, b)`.
3. Else if `Cb` has a definer of `__eq__` whose parameter accepts `Ca`, call the reflected `Cb.__eq__(b, a)`. `a` is evaluated before `b` (temporaries keep CPython's operand order).
4. Else the identity fallback: pointer compare when the classes are related by single-chain inheritance (today's same-class rule); constant `False` when no lowered class inherits from both (operands still evaluated); `PS2004` when a lowered class inherits from both (the two static views are different prefix-layout pointers, so a pointer compare is not identity).

`a != b` runs the same order on `__ne__`. If no class resolves a user `__ne__`, it is the logical negation of the `==` result, identity fallback included, which is `object.__ne__`'s behavior.
Same-class `==` through `__eq__`, the tuple-class compare, `COMPARE_CLASSES` and the dataclass-synthesized `__eq__` keep their current path and run before this order.

Behavior this changes today: a user `__ne__` is silently ignored (`a != b` negates `__eq__`; measured, native `False`, CPython `True`), and mixed-class `==` fences `PS2004`.

### `<`, `<=`, `>`, `>=` (O3a)

1. Subclass-priority reflected call, as for `==`.
2. The left definer of the operator's dunder (`__lt__` for `<`, and so on) whose parameter accepts the right class.
3. The right class's reflected dunder with swapped operands (`__gt__` for `<`, `__lt__` for `>`, `__ge__` for `<=`, `__le__` for `>=`), left evaluated first.
4. Else `PS2004` (CPython raises `TypeError`).

No operator is derived from another: `<=` is not synthesized from `<` and `==`, and `functools.total_ordering` stays fenced as a decorator. `sorted`, `list.sort`, `min` and `max` over class elements use `__lt__` only in CPython; they stay fenced (`PS2002`) until the comparator-taking runtime sort lands, and are not O3a.

### `hash` (O3a: the builtin; the containers are deferred)

1. `hash(x)` with a definer of `__hash__`: direct call, result `int`, `-1` mapped to `-2` (CPython reserves `-1`). Our `int` is `i64` (divergence 1), so CPython's overflow branch, which folds a wider result through `hash(int)`, cannot occur, and the `i64` result passes through verbatim as in CPython's slot.
2. No `__hash__` definer in the mro and the class or an ancestor below `object` defines `__eq__`: unhashable. CPython sets `__hash__ = None` for a class that defines `__eq__` without `__hash__` (inherited until a subclass defines `__hash__`). The use site fences `PS2004`; CPython raises `TypeError` at run time, and a program that does that is not a compile target.
3. Neither dunder defined: identity hash, the pointer hash CPython uses (the pointer rotated right by 4 bits). The value is address-dependent in both implementations, so no byte comparison is promised.
4. A dataclass: `eq=True` without `frozen` is unhashable (rule 2); `frozen=True` with `eq=True` synthesizes a field hash, owned by O5, and is fenced until then (today `hash(D(1))` fences `PS2002`).
5. `dict[C, V]` and `set[C]` stay fenced (`PS2002`, "dict keys are int or str"). Class keys need a container runtime that calls the resolved hash and eq per key class, which is a runtime change and not static resolution. File it as its own ticket under the same epic once O3a lands (proposed name: O3c, class-keyed dict and set).
6. The eq/hash consistency invariant is not checked, as in CPython.

### `bool` (O3a)

The truthiness test of a class-typed value, in `if`, `while`, `not`, the `and`/`or` test, the conditional-expression test, comprehension conditions and `bool(x)`:

1. `__bool__` definer: direct call, must return `bool` (`PS1003`; CPython raises `TypeError` for a non-bool return).
2. Else `__len__` definer: `len(x) != 0`, with the same negative check as `len(x)`: a negative result raises `ValueError("__len__() should return >= 0")`.
3. Else constant `True`; the operand is still evaluated for its side effects.

`Optional[C]`: truthiness is the presence tag, then the rule above on the payload when `C` has a `__bool__` or `__len__` definer; the tag alone when it has neither.
`and`/`or` keep their operand-returning shape; only the test is replaced.
The rule replaces today's `PS2004` "condition must be a bool, not a truthy value" for class operands, and corrects two measured silent divergences: `Optional[C]` truthiness ignores `__len__` and `__bool__` (native `truthy`, CPython `falsy`), and `len(x)` with a negative `__len__` returns the negative number (native `-1`, CPython `ValueError`).

### `str`, `repr` and `format` fallbacks (O3a)

`str(x)`: `__str__`, else `__repr__` (the existing order). `repr(x)`: `__repr__`. With neither, the default `<C object at 0x...>` is address text and stays fenced.
`format(x, spec)` and f-strings: a `__format__` definer is called with the spec (`(self, spec: str) -> str`); with no definer, an empty spec is the `str` order above and a non-empty spec fences (CPython raises `TypeError`). Today a user `__format__` is silently ignored (measured: native `str`, CPython `fmt:`), so this corrects a third silent divergence.

### `iter` (O3b; the non-generator `__iter__`, generator methods ride the G plan)

1. `for x in obj` and every other iteration consumer resolve through one shared resolver. The class needs an `__iter__` definer; the checker already rejects a `__next__`-only class (`not-iterable`), so the lowering keeps requiring `__iter__`.
2. The `__iter__` call result decides the loop by its own static type: a lowered class with `__next__` drives the loop through `__next__` with `StopIteration` ending it (today's `_for_dunder`); a list, dict, set, str, bytes or range goes through the existing loop for that type; anything else fences `PS2002`.
3. `iter(x)` and `next(x)` resolve through the same call. The consumers `list()`, `tuple()`, `set()`, `sum`, `any`, `all`, `enumerate`, `zip`, unpacking and `in` adopt the resolver one by one; an unadopted consumer keeps its fence and its message names the consumer.
4. The legacy sequence protocol, iteration through `__getitem__` from `0` until `IndexError`, is not lowered, now or later (a recorded non-goal: it needs an unbounded index probe and no modern library relies on it).
5. `__iter__` as a generator method depends on the generator plan (O3b depends on G3a, #446).

### `getitem`, `setitem`, `delitem`, `len`, `in` (O3b)

1. `x[i]`, `x[i] = v` and `del x[i]` on a class-typed `x` call the `__getitem__`, `__setitem__` and `__delitem__` definer with the index expression as the one argument, checked against the declared parameter (`PS1003`). `__setitem__` and `__delitem__` return `None`.
2. `x[i] += v` is the getitem call, the operation, then the setitem call, with the index evaluated once into a temporary.
3. A slice index (`x[a:b]`) and a multi-index tuple index fence until the `slice` and tuple index values are lowered values; the decision does not widen them.
4. `len(x)` is the existing call plus the negative check.
5. `i in x` and `i not in x`: the `__contains__` definer; else the iteration resolver above with the `==` order for each element; else `PS2002`. The legacy `__getitem__` fallback is not lowered (item 4 of the iter family).

### `with` (O3b)

1. The class needs both `__enter__` and `__exit__` definers, checked before the body (CPython raises the missing-method error before `__enter__` runs, so a static fence is equivalent); one missing is `PS2002`.
2. `__enter__` may return any lowered type, and it is the `as` target's type. `as` takes a name only (`PS2002` for a pattern, as today). Multiple items nest left to right and exit in reverse; that is the existing lowering and is not reopened here.
3. `__exit__` keeps the three-slot carrier (class-name string or none, exception or none, traceback string or none), bound by position. The return type may be `bool`, `None` or `bool | None`; `None` never suppresses. The parameter annotation spellings CPython code uses (`type[BaseException] | None`, `BaseException | None`, `TracebackType | None`) are mapped to the carrier by an erasure table that O3b owns and records in the typing construct table named in `AGENTS.md` in the same PR (`docs/guide/typing.md` is not on master yet, so that PR also creates or locates the table); a spelling outside the table keeps `PS2003`.
4. `async with` (`__aenter__`/`__aexit__`) belongs to the async plan.

## Divergences this choice creates or leaves

Every entry below lands in `docs/reference/divergences.md` and the ledger (`scripts/gen_divergence_ledger.py`) in the PR that implements it, with the next free number at that time (91 is the highest today; parallel PRs collide, so the number is taken at merge time). Drafts, to be edited when the lowering exists:

- Draft A (O3a): `==`, `!=` and the ordering operators resolve their dunder from the static classes of the operands, left first, then the reflected dunder, then the identity fallback. A dunder that returns `NotImplemented` fences at its `return` (`PS2002`) instead of falling through to the other operand, so a class pair whose comparison relies on that fall-through does not compile. An `__eq__` whose parameter is `object` and narrows with `isinstance` fences `PS2001`. The checker's `override` complaint on a narrowed `__eq__` parameter is silenced with `# type: ignore[override]`.
- Draft B (O3a): `hash(x)` compiles for a class with a `__hash__` definer or with neither dunder; a class that defines `__eq__` without `__hash__` fences at the call where CPython raises `TypeError`. `dict` and `set` keys stay `int` or `str`; a class key is refused (`PS2002`), not hashed. The identity hash is address-based in both implementations.
- Draft C (O3b): iteration over a class needs `__iter__`; the legacy `__getitem__` sequence iteration and the `in` fallback through it are refused. `sorted`, `list.sort`, `min` and `max` over class elements stay refused until the comparator runtime lands.
- Draft D (O3b, a gap that exists today and is not yet recorded): the `__exit__` carrier passes the exception's class name as a string and a traceback that is always `None`, where CPython passes the class object and a traceback object. Code that tests `exc_type is None` or ignores the arguments behaves the same; code that calls `issubclass(exc_type, ...)` or reads the traceback fences or differs.

The three silent divergences above (user `__ne__` ignored, `Optional[C]` truthiness and negative `__len__`, user `__format__` ignored) are fixed by lowering the protocol, not recorded as divergences. If the O3a PR is delayed past the next release, file them as divergence entries first so the gap is stated.

## Why this and not the alternatives

The compiler's model is static monomorphization with prefix layouts and exact class slots (divergence 70). Under that model the static class of an operand is its runtime class, so resolving the dunder at lower time costs nothing at run time and cannot disagree with CPython except where CPython needs a run-time fall-through (`NotImplemented`, an `isinstance` guard, a legacy protocol), and those are exactly the fenced cases. The mechanism is already in the tree for six dunders, so O3a and O3b extend `_dunder_call` and its call sites instead of introducing a second dispatch system.

## Rejected alternatives

1. A type word and a per-class vtable with runtime dispatch (CPython-shaped, handles `NotImplemented`, `isinstance` and mixed classes uniformly). Rejected: it overturns the static model the plan declares a non-goal, adds a word to every instance and a table per class for the 99 percent of programs that never need it, and the dynamic-attributes plan is the place that decision would be made.
2. Per-site runtime fallback: emit the static call and, on a mismatch, a boxed island call. Rejected: the island floor is per definition (decision 2026-10-25), and an operator site is not a definition, so there is no unit to fall back at.
3. Identity-only `==` and no ordering dunders (keep today's partial behavior and document it). Rejected: jinja2's runtime and every value-object library define these dunders, and today's behavior silently disagrees on `__ne__`, `__format__`, `Optional` truthiness and negative `__len__`.
4. Interim fences for the three silent divergences before O3a lands. Rejected: O3a is already queued (#445), and a fence that is removed by the next PR is churn; the divergence note above covers the case where it slips.

## What reopens the decision

- A library the pins need compares or hashes two class operands whose pair the fixed order cannot decide (a mixed-class `NotImplemented` fall-through): then O6's closed-world `isinstance` or a runtime type word is back on the table.
- Class-keyed `dict` and `set` become a blocker for the library corpus: then O3c, not this decision, is reopened.
- A frozen gate (`TIR_HASHES.json`) moves in the O3a or O3b PR: each PR follows the limited-regen discipline from `AGENTS.md` and records its rows in `decisions.md`.

## Receipt expectations for O3a and O3b

Each family lands with a differential probe under `probes/differential/` that byte-compares stdout, stderr and exit code against the pinned CPython (rule #524), covering the resolved order and one fence per refusal above. The user-facing diagnostics keep their codes, and the `PS2004` messages name the classes. The typing construct table named in `AGENTS.md` updates in the same PR as the lowering.
