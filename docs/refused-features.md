# Refused features

The features pyscriptc rejects by design: constructs that are legal CPython but make compiled programs less reliable, less predictable, or unverifiable statically. Spec only — nothing here is implemented. This is the same posture vercel-labs/scriptc takes against TypeScript reflection and experimental decorators: a closed subset is only trustworthy when it also refuses the host language's footguns, not only its unimplemented surface.

A fence today answers "not lowered yet" (PS2xxx, the epics in `.agents/plans/`). A refusal answers "never, and here is what to write instead". The distinction matters to users: a PS2101 row is a queue position, a refusal is a decision. This document proposes a dedicated diagnostic range, **PS4xxx — refused by design**, registered in `src/pyscriptc/diagnostics.py` with the same `title` / `why` / `fix` shape as existing entries. Existing fence codes and messages are untouched; PS4xxx codes are all new assignments.

## Decision table

| Feature | Verdict | Write instead |
| --- | --- | --- |
| `eval`, `exec`, `compile`, `__import__`, `importlib.import_module` | refuse (PS4001) | closed import table; compile-time constants |
| `globals()` / `locals()` / `vars()` mutation, `dir()` on live objects | refuse (PS4002) | a `dict` passed explicitly |
| Monkeypatching: `setattr` non-literal, `Class.attr = x`, `obj.__dict__` writes | refuse (PS4003) | constructor args, module constants |
| Naive `datetime` construction: `utcnow()`, `now()` without tz | refuse (PS4004) | `datetime.now(timezone.utc)`, `date.today` equivalents |
| Full `%` formatting (format specs, `%(...)s` mapping) | refuse (PS4005) | f-strings |
| `os.path.*` string-path API | refuse (PS4006) | `pathlib.Path` |
| Mutable default argument (`def f(x: list[int] = [])`) | refuse (PS4007) | required parameter, or a module constant copied per call |
| Implicit string concatenation (`"a" "b"`) | refuse (PS4008) | one literal, `+`, or an f-string |
| `is` on scalars and literals | refuse (PS4009) | `==` (or `is None`) |
| `str`/`bytes` mixing in `==`, `in`, concatenation | refuse (PS4010) | explicit `.encode()` / `.decode()` |
| `bool` in arithmetic (`True + True`, `sum` of bools) | refuse (PS4011) | `int(flag)` before arithmetic |
| In-place mutator used as a value (`x = xs.sort()`) | refuse (PS4012) | `sorted(xs)` / the method, then the name |
| Lazy `map` / `filter` / `zip` as values | refuse (PS4013) | comprehensions |
| Interpreter hooks (`setrecursionlimit`, `settrace`, `setprofile`, `getrefcount`) | refuse (PS4014) | none — compiler-owned process |
| `__del__`, `weakref`, `gc` module | refuse (PS4015) | `with` / explicit `close()` |
| `id()`, `hash()`, default `repr` addresses | refuse (PS4016) | a field that carries the identity you need |
| `__getattr__` / `__setattr__`, `property`, descriptors on classes | refuse (PS4017) | plain annotated fields and methods |
| `from x import *` | refuse (PS4018) | named imports |
| `**kwargs`, `*args` variadic signatures | refuse (PS4019, door open) | plain positional parameters |
| `asyncio.gather` | refuse if async ever lands | structured concurrency only (`TaskGroup`) |
| `bare except` | refuse permanently (currently PS1205) | `except Exception` (never the bare form) |
| Recursion + `itertools` recursion helpers | caveated | loops; deep recursion needs evidence |
| `%s` / `%d` already lowered | supported-with-caveat | prefer f-strings in new code |
| `x is None` → `==` desugar | supported-with-caveat | fine for `None` only; anything else is PS4009 |
| Set iteration order = insertion order | supported-with-caveat | `sorted(s)` for deterministic output |
| `int` overflow / `//` by zero abort | supported-with-caveat | real exceptions pending R10 |
| `assert` always on (no `-O`) | supported-with-caveat | never put side effects in `assert` |
| `tuple` synthetic-class repr | supported-with-caveat | cosmetic divergence only |

## Tier A: refuse with a diagnostic now

These constructs are fenced today by generic codes or are checker-caught. The proposal is a dedicated PS4xxx entry so `pyscriptc explain` and `pyscriptc coverage` can tell the user *why it will never work*, not just that it didn't.

### PS4001 — dynamic code execution: `eval`, `exec`, `compile`, `__import__`, `importlib.import_module`

A compiler that statically resolves every name cannot honor a string that becomes code at run time. `eval`/`exec` also defeat the whole point of the subset: any program accepting them is a shell, and no static guarantee survives one. `importlib.import_module` is fenced today (closed import table, `docs/supported.md:76`); it is listed here because it belongs to the same refusal even after the import table grows.

Diagnostic: `eval()/exec()/compile()/import_module() are refused: dynamic code defeats static resolution` — hint: `name the import statically, or move the dispatch into a dict of functions`.

### PS4002 — namespace reflection and mutation: `globals()`, `locals()`, `vars()`, `dir()`

`locals()` writes are documented by CPython as not affecting the local scope (`locals()` docs: "modifications to it may not affect the values"), which already makes mutation unreliable across interpreters; `globals()` mutation is monkeypatching-by-dict and breaks the closed world pyscriptc resolves at compile time. Both are refused regardless of whether the dict is read or written.

### PS4003 — monkeypatching: non-literal `setattr`, `delattr`, `Class.attr =`, `__dict__` writes

`getattr`/`hasattr` with a *string literal* already lower. The refused half is what makes the attribute set unpredictable: `setattr(obj, name, v)` with a computed `name`, assigning to attributes of a class or module object, and direct `__dict__` writes. Class layouts are fixed at lowering; a field that can appear at run time has no slot.

### PS4004 — naive datetime construction: `datetime.utcnow()`, `datetime.now()` without `tz`, `date`/`time` naive mixing

`datetime.utcnow()` is deprecated since CPython 3.12 (`docs.python.org/3/library/datetime.html`: "use timezone-aware objects... `datetime.now(timezone.utc)`"), and naive-vs-aware arithmetic raises `TypeError` in CPython — a trap that fires late, at the subtract/compare, far from the construction. pyscriptc's `datetime` row (`docs/supported.md:75`) is a closed set; naive *now* constructors should refuse and point at `datetime.now(timezone.utc)`.

### PS4005 — printf-style `%` formatting beyond the `'%s' % x` / `'%d' % x` subset

CPython's own docs call printf-style formatting "a variety of quirks" and note it "leads to common errors (such as failing to display tuples and dictionaries correctly)"; `'%s' % (a, b)` vs `'%s' % tuple` is a classic silent divergence. The two lowered forms stay (see Tier C); everything else — format specs, `%(...)s` mapping keys, `%%` mixes — refuses and points at f-strings.

### PS4006 — `os.path` string-path API

`os.path.join`/`dirname`/`split` return `str`, `pathlib.Path` returns `Path`; code that mixes both compiles fine in CPython and produces subtle type drift (`Path`/`str` concat fails, `os.fspath` normalization traps). pyscriptc already lowers `pathlib` (`docs/supported.md`), so the refusal is cheap: one path API, the object one.

### PS4007 — mutable default argument

`def f(x: list[int] = [])` shares one list across all calls — the single best-known Python footgun, in the official FAQ ("why are default values shared between objects?"). The subset cannot fix the semantics (a fresh list per call is a hidden allocation the IR would have to model), so it refuses the signature. Hint: take the list as a required parameter, or define the default as a module constant and copy it.

### PS4008 — implicit string concatenation

`"a" "b"` silently merges — inside a list literal it hides a missing comma and produces a shorter list with a longer element. CPython itself added a SyntaxWarning only inside parens; pyscriptc refuses it everywhere. Hint: one literal, `+`, or an f-string.

### PS4009 — `is` on scalars and literals

`x is 5`, `x is "abc"` depend on interpreter interning — CPython warns (`SyntaxWarning: "is" with a literal`), and results differ between CPython versions and interpreters. `x is None` is lowered (`docs/supported.md:141`) and stays; `is` against anything else is already fenced — this code gives it a name and a fix: `==`.

### PS4010 — `str`/`bytes` mixing

`b"x" == "x"` is always `False` in CPython — no error, just silently wrong. Cross-type `in`, `+`, and comparisons in compiled code should refuse outright rather than inherit CPython's silent-False. Hint: decode or encode explicitly at the boundary.

### PS4011 — `bool` in arithmetic

`True + True == 2` works in CPython because `bool` subclasses `int`; pyscriptc already refuses `list[bool]` and dict `bool` values (`docs/supported.md:58`), so arithmetic on `bool` is the same refusal at the operator. Hint: `int(flag)` first.

### PS4012 — in-place mutator used as a value

`x = xs.sort()`, `return d.update(...)`, `y = xs.append(v)` all evaluate to `None` — CPython's deliberate API design to catch the confusion with `sorted()`/`set()`. Compiled code refuses to bind a mutator call's result; the diagnostic names the returning variant: `sorted(xs)`.

### PS4013 — lazy `map` / `filter` / `zip` as values

These are fenced today because there are no iterator values (R24 root cause). Even once iterators land, `map(f, xs)` should stay refused: it is lazy (the function runs at consumption time, possibly never), while the same comprehension is eager and obvious. `itertools.count`/`cycle` share the disease — an unbounded iterator in a compiled program is a hang with no stack. Refuse laziness-by-default in favor of comprehensions.

### PS4014 — interpreter hooks

`sys.setrecursionlimit`, `sys.settrace`, `sys.setprofile`, `sys.setswitchinterval`, `sys.getrefcount` — there is no CPython interpreter to configure; the emitted binary has a fixed model. Any call is a refuse.

### PS4015 — finalizers and the GC surface

`__del__`, `weakref`, `gc.*` — finalizer timing is explicitly unspecified in CPython ("it is not guaranteed that `__del__` is called"), and the runtime has no GC to control. Resource lifetime is `with` + `close()` only.

### PS4016 — process-identity values: `id()`, `hash()`, default `repr`

`hash()` of `str`/`bytes` is salted by `PYTHONHASHSEED` and differs between runs; `id()` is an address; a default `<object at 0x...>` repr embeds both. Programs that print or compare these are non-reproducible by construction — the opposite of what a compiled artifact should be.

### PS4017 — dynamic attribute protocol

`__getattr__`, `__setattr__`, `__getattribute__`, `property` on classes, descriptors (`__get__`/`__set__`). Attribute access is resolved statically to declared fields; a hook that can intercept any name lookup defeats that resolution. (Module-level `@staticmethod` already lowers and stays; `docs/supported.md:171` lists the fenced decorator forms.)

### PS4018 — `from x import *`

Star imports make the name set unknowable statically — exactly what the closed import table exists to prevent. Refuse in every context.

## Tier B: refused, door open

- **`**kwargs` / `*args` (PS4019).** Variadic calls defeat positional-arity checking (PS2003's whole job). The door stays open only for a compile-time-expanded `*args` of fixed literal arity; keyword-argument *passing* already works for declared parameters.
- **`asyncio.gather`.** Async itself is fenced (R-series, wave plan). If it ever lands, `gather` still refuses: its documented semantics (partial cancellation does not propagate, results list vs `return_exceptions` split, children orphaned on cancellation — `asyncio.gather` docs) are the canonical unstructured-concurrency trap. `asyncio.TaskGroup` (3.11+) is the only acceptable shape.
- **`bare except`.** Currently PS1205 alongside `except ... as`. Bare `except` swallows `KeyboardInterrupt`/`SystemExit` (`except` docs, PEP 8 E722). Recommend it *stays* refused permanently even after E2 lands binding forms — a future dedicated code can split it out of PS1205, but no existing message is reworded.
- **Recursion helpers.** Plain recursion lowers; recursion *as a strategy* (deep trees, `itertools.accumulate` chains) needs corpus evidence on stack behavior before any caveat is written.
- **`global` writes to module slots beyond the declared process-slot form, `nonlocal`.** Tied to R22 (closures); verdict deferred to that epic.

## Tier C: supported with caveats

Deliberate divergences that are already lowered and documented; kept because the cost of removing them exceeds the footgun risk. Documented here so the refusal list doesn't quietly grow:

- `'%s' % x` / `'%d' % x` lower (docs/supported.md:54). Caveat: prefer f-strings; the moment a spec or mapping appears it hits PS4005.
- `x is None` lowers as `==` against `None`. Caveat: correct only because `None` is a singleton; every other `is` is PS4009.
- Set iteration is insertion order, not hash order. Caveat: deterministic and stable, but different from CPython — use `sorted()` where order matters.
- `int` overflow and `//` by zero `abort()` instead of raising (`benchmarks/compat2` R10 row). Caveat: a known trap-vs-raise gap, tracked for E2.
- `assert` always evaluates — there is no `-O` flag on a compiled binary. Caveat: never put side effects in the condition.
- `tuple_N_*` synthetic classes: `repr` and `type()` name differ from CPython (`docs/supported.md:135`). Cosmetic only.
- `ABC`/`ABCMeta`/`Protocol` bases, `metaclass=ABCMeta`, `@abstractmethod`, and `@overload` are tier-2 erasures (`.agents/plans/2026-10-18-tiered-lowering-and-typefacts.md`): they lower as plain classes/methods with no CPython runtime semantics. Caveats: `ABC()` instantiates (CPython raises `TypeError` when abstract methods remain); a declaration-only body (`...`/`pass`) on a Protocol member or `@abstractmethod` is skipped, not emitted; `f(x: SomeProtocol)` accepts any subclass of the erased class — object-ish, not structural — and unrelated classes, scalars, `list`/`dict` still fail the annotation; `isinstance`/`issubclass` on a Protocol and `@runtime_checkable` stay fenced (structural checks are the checker's job); a lone `@overload` with no implementation erases to nothing, so calls to the name fence as unbound.

## Does refusing change the corpus scorer?

Mostly no. compat2 scores probe *outcomes* against the CPython oracle; every refused construct above is fenced today, so flipping a PS2002 to a PS4001 leaves the weighted pass untouched — a fence is a fence either way. The two ways a refusal moves the score:

1. **Refusing something that currently lowers** (e.g. extending PS4005 to take back `'%s' % x`) flips its probes from pass to fence — a real score regression. Any tier-A item that is already lowered must therefore stay in Tier C or accept a recorded score delta.
2. **`coverage`/`census` families.** `family_of` in `src/pyscriptc/coverage.py` maps codes to families; each PS4xxx code needs a family row so refusals don't collapse into `construct-other` — cheap, and it makes the census report "refused-by-design" as its own blocker class, which is honest signal for the top100 port lanes.

Net: refusal is a *diagnostic* change, not a capability change — provided the refuse list only claims constructs already fenced.

## Fences or warnings?

Recommendation: **hard fences for everything in Tier A, no warning tier yet.**

- Fences for silent-divergence semantics (PS4001–PS4018): if a compiled program could produce a different result than CPython — or a result that is unspecified — continuing the compile is exactly the failure mode this list exists to prevent. The whole value of pyscriptc is "the binary does what the source says"; a warning on `x is 5` abandons that.
- The *stylistic* candidates — implicit concat (PS4008), `%` formatting (PS4005), `os.path` (PS4006), bool arithmetic (PS4011), lazy `map` (PS4013) — are legal, deterministic CPython; a warning would suffice. But there is no warning channel today: the IR has `fences`, `errors.py` has `IRError`, and nothing emits a non-fatal diagnostic. Adding one is a small epic (IR `warnings` field, `pyscriptc compile --warn` surface, `coverage` classification) and should not smuggle itself into this doc's rollout. Until then these five refuse hard too — consistency beats granularity, and every one has a better-form substitute.
- If a warning tier lands later, the split is the one above: semantics-that-differ stay fences forever; style migrates from fence to warning.

## Implementation notes

- Register the codes in `src/pyscriptc/diagnostics.py` (`title`/`why`/`fix`), add a `_RULES` row per code in `src/pyscriptc/coverage.py` mapping to new `refused-*` families with the substitute as the hint.
- Lowering emits them at the same sites that produce the generic fence today — the detection points already exist (`builtins.py` name table, `stdmore.py` sig table, `_stmt.py`/`_expr.py` construct checks); only the code and message change. No existing fence message is edited.
- `pyscriptc explain PS4xxx` then answers "why is this refused" directly, which is the deliverable: a diagnostic that teaches the substitute instead of reporting a gap.
