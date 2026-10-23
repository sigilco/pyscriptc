# Refused features

The features pyscriptc rejects by design: constructs that are legal CPython but make compiled programs less reliable, less predictable, or unverifiable statically. Spec only - nothing here is implemented. This is the same posture vercel-labs/scriptc takes against TypeScript reflection and experimental decorators: a closed subset is only trustworthy when it also refuses the host language's footguns, not only its unimplemented surface.

A fence today answers "not lowered yet" (PS2xxx, the epics in `.agents/plans/`). A refusal answers "never, and here is what to write instead". The distinction matters to users: a PS2101 row is a queue position, a refusal is a decision. Refusals live in a dedicated diagnostic range, **PS42xx - refused by design**, registered in `src/pyscriptc/diagnostics.py` with the same `title` / `why` / `fix` shape as existing entries. Existing fence codes and messages are untouched; a PS42xx code is a later rollout gated on this census.

The `PS4xxx` numbers this document first proposed are taken: `PS4001`-`PS4016` are the FFI manifest range (`src/pyscriptc/ffi_manifest.py`) and `PS4101` is the mobile-target refusal. The refusal range therefore starts at `PS4201`.

## Re-census (2026-10-22, master `0b66ca2`)

Before registering any refusal code, every row below was re-verified by compiling and running a probe against master `0b66ca2` (Python 3.14, `uv run python -m pyscriptc compile FILE --run --call f --args ...`), comparing against the CPython oracle. A refusal may only claim a construct that is actually fenced today; a row that already lowers moves to Tier C (supported with caveat) or gets a bug filed.

Rows that changed since PR #72 wrote the table:

| Doc row | Probe verdict on master | Consequence |
| --- | --- | --- |
| `def f(*args: int)` iterating `args` (PS4019) | **works** - lowers as a `list`, internal calls `f(1, 2)` and iteration match CPython | `*args` half of PS4019 is stale; only starred call sites (`g(*xs)`) stay fenced |
| `**kwargs` (PS4019) | **works** - `def f(**kwargs: int)` with `f(a=1, b=2)` returns 3 | the whole PS4019 row is stale; keyword passing at call sites also works |
| `@property` getter (PS4201) | **works** - attribute read calls the getter (see the Tier C list in this file); only calling `C.a` and the setter stay fenced | PS4201 narrows to `__getattr__`/`__setattr__`/descriptors |
| `class E(Exception)` user exceptions | **works** - raise and catch of a user exception class match CPython | not a refusal row; recorded as supported |
| `bare except` (listed "currently PS1205") | **works** - E2 M3 landed, bare `except` and `except E as e` both run | the PS1205 note is stale; PS1205 now only fires on a name conflict |
| `eval`/`exec` (PS4001) | fenced PS2002/PS2001 as claimed | row stands |
| `setattr` with a **literal** name (PS4003 claims only the non-literal half is refused) | **fenced** - `setattr(c, 'a', 1)` is PS2002 today | PS4003's scope note was wrong in the other direction: all of `setattr` refuses, while `getattr`/`hasattr` with a literal name lower |
| `os.path.join` (PS4006 refuses the whole API) | **works** - `import os; os.path.join('x','y')`, `from os.path import join`, and `posixpath` all return `x/y` identical to CPython | PS4006 as written would claim a lowered surface; see the contradiction resolution below |
| `'%s' % x` with specs and mappings (PS4005 refuses everything beyond `'%s'`/`'%d'`) | **works** - `'%.2f' % x`, `'%(a)s' % {'a': x}`, `'%s %s' % (x, x)`, `'%d%%' % x` all match CPython | PS4005 is stale beyond repair: the whole printf surface that was supposed to refuse is lowered; only the warning-lane argument survives |
| `map`/`filter` as values (PS4013 refuses laziness) | **eager lowering exists** - `map`/`filter` desugar to comprehensions (`restlower/_iterators.py:187`), `list(map(g, xs))` matches CPython; binding `m = map(...)` without a use fences PS1001 | PS4013's premise (no eager form) is false; the row narrows to `itertools.count`/`cycle`, which fence PS2001 "infinite iterators are not lowered" |
| `is` on scalars (PS4009 says "already fenced") | **lowers** - `x is 5`, `x is 1000`, `x is 'abc'` compile and run; `_identity` in `lower/_expr.py:526` lowers scalar `is` to `==` and heap `is` to pointer compare | PS4009 keeps a reduced claim, see the divergence note in Tier C |
| implicit concat `"a" "b"` (PS4008) | **compiles** - and cannot be detected after `ast.parse`, which merges it into one `Constant` | PS4008 as written is unimplementable at the AST level; see Tier 2 |
| mutable default argument (PS4007) | fenced PS2002 for `list[int] = []` and `dict[str, int] = {}`; `int = 3` lowers | row stands |
| `datetime.utcnow()` (PS4004) | fenced PS2001 | row stands, but see the tier move below |
| `datetime.now(timezone.utc)` | **fenced** - PS2003 "now expects 0 argument(s)"; `datetime.timezone` is not in the lowered subset | the "write instead" hint in PS4004's draft message points at a construct that is itself fenced; the fix has to land first (R-row `datetime` surface) |

Four probes found live divergences between the compiled binary and CPython, recorded here because they sit exactly on the T0/T3 boundary this doc draws:

- `a is b` on two runtime-built equal strings answers `0` where CPython answers `1` (`'he' + 'llo' is 'hello'`). The lowering compares backing-buffer pointers; CPython's constant folding made them one object. This is the `is`-on-scalars row's real hazard and the reason PS4009 keeps any claim at all.
- `b'x' + 'y'` (str/bytes concat) is accepted by the type checker (exit 2 from the checker layer, not a fence), where the doc assumed a clean PS4010 fence. The refusal is correct; the enforcement point is the checker, not lowering.
- A class with a `__del__` method compiles and runs, but the finalizer never fires: `del c` prints `del ran` under CPython and nothing in the binary. The method is silently dropped. The hook has to be fenced, not just the `weakref`/`gc` surface.
- A class with a `__setattr__` hook compiles and runs, and the hook is never called: `c.a = 1` writes the field directly. Same shape as the `__del__` divergence and the same consequence: fence the hook definition, not only the dynamic-protocol surface.

## Per-row probe table

Every row below was probed by compiling and running a snippet against master `0b66ca2` and comparing with the CPython oracle. `fenced` means the construct is refused today (the code in the last column is what the user sees); `works` means it compiled, ran, and matched the oracle; `checker` means the type checker rejected it before lowering. The probe sources live in the issue's session report; the harness is `python -m pyscriptc compile FILE --run --call f --args ...`.

| Row | Probe | Verdict | Diagnostic or result |
| --- | --- | --- | --- |
| PS4001 dynamic code | `eval('x + 1')` | fenced | `PS2002` |
| PS4001 dynamic code | `exec('y = x')` | fenced | `PS2002` |
| PS4001 dynamic code | `compile('1', '<s>', 'eval')` | fenced | `PS2002` |
| PS4001 dynamic code | `importlib.import_module('math')` | fenced | `PS2001` |
| PS4001 dynamic code | `__import__('math')` | fenced | `PS2002` |
| PS4002 namespace | `globals()['x'] = 2` | fenced | `PS2002` |
| PS4002 namespace | `len(globals())` | fenced | `PS2002` |
| PS4002 namespace | `len(locals())` | fenced | `PS2002` |
| PS4002 namespace | `len(vars(c))` | fenced | `PS2002` |
| PS4002 namespace | `len(dir(c))` | fenced | `PS2002` |
| PS4003 monkeypatch | `setattr(c, 'a', 1)` | fenced | `PS2002` |
| PS4003 monkeypatch | `delattr(c, 'a')` | fenced | `PS2002` |
| PS4003 monkeypatch | `C.a = 5` | fenced | `PS2002` |
| PS4003 monkeypatch | `c.__dict__['a'] = 2` | fenced | `PS2001` |
| PS4004 datetime | `datetime.datetime.utcnow()` | fenced | `PS2001,PS2002` |
| PS4004 datetime | `datetime.datetime.now()` | fenced | `PS1001,PS2002` |
| PS4004 datetime | `datetime.datetime.now(timezone.utc)` | fenced | `PS2002,PS2003` |
| PS4004 datetime | `datetime.date.today()` | fenced | `PS1001,PS2002` |
| PS4005 printf | `'%s' % x` | works | result `1` |
| PS4005 printf | `'%d' % x` | works | result `1` |
| PS4005 printf | `'%.2f' % x` | works | result `1.50` |
| PS4005 printf | `'%(a)s' % {'a': x}` | works | result `1` |
| PS4005 printf | `'%s %s' % (x, x)` | works | result `1 1` |
| PS4005 printf | `'%d%%' % x` | works | result `1%` |
| PS4006 os.path | `os.path.join('x','y')` | works | result `x/y` |
| PS4006 os.path | `os.path.dirname('x/y')` | works | result `x` |
| PS4006 os.path | `from os.path import join` | works | result `x/y` |
| PS4006 os.path | `posixpath.join('x','y')` | works | result `x/y` |
| PS4006 os.path | `from posixpath import join` | works | result `x/y` |
| PS4006 os.path | `str(Path('x/y').parent)` | works | result `x` |
| PS4007 mutable default | `def f(x: list[int] = [])` | fenced | `PS2002` |
| PS4007 mutable default | `def f(x: dict[str,int] = {})` | fenced | `PS2002` |
| PS4007 mutable default | `def f(x: int = 3)` | works | result `3` |
| PS4008 implicit concat | `'a' 'b'` | works | result `ab` |
| PS4009 is | `x is 5` | works | result `1` |
| PS4009 is | `x is None` | works | result `1` |
| PS4010 str/bytes | `b'x' == 'x'` | fenced | `PS2004` |
| PS4010 str/bytes | `b'x' in 'xy'` | checker | Argument `Literal[b'x']` is not assignable to parameter `key` with type `str` in function  |
| PS4010 str/bytes | `b'x' + 'y'` | checker | Protocol `Buffer` requires attribute `__buffer__` |
| PS4011 bool arith | `True + True` | fenced | `PS2004` |
| PS4011 bool arith | `sum([True])` | fenced | `PS2002` |
| PS4012 mutator | `x = xs.sort()` | fenced | `PS1001` |
| PS4012 mutator | `y = xs.append(1)` | fenced | `PS1001` |
| PS4013 lazy | `m = map(g, [1,2])` | fenced | `PS1001` |
| PS4013 lazy | `m = filter(g, [1,2])` | fenced | `PS1001` |
| PS4013 lazy | `z = zip([1],[2])` | fenced | `PS1001` |
| PS4013 lazy | `itertools.count()` | fenced | `PS2001` |
| PS4013 lazy | `itertools.cycle([1])` | fenced | `PS2001` |
| PS4014 hooks | `sys.setrecursionlimit(100)` | fenced | `PS2001` |
| PS4014 hooks | `sys.settrace(None)` | fenced | `PS2001` |
| PS4014 hooks | `sys.getrefcount(1)` | fenced | `PS2001` |
| PS4015 finalizers | `class C: def __del__` + `del c` | fenced | `PS4203` (registered #432) |
| PS4015 finalizers | `weakref.ref(c)` | fenced | `PS2002` |
| PS4015 finalizers | `gc.collect()` | fenced | `PS2002` |
| PS4016 identity | `id(7)` | fenced | `PS2003` |
| PS4016 identity | `hash(7)` | works | result `7` |
| PS4016 identity | `hash('a')` | fenced | `PS2002` |
| PS4016 identity | `repr(C())` | fenced | `PS2004` |
| PS4016 identity | `str(c)` | fenced | `PS2003` |
| PS4201 attrs | `c.missing` with `__getattr__` | fenced | `PS2001` |
| PS4201 attrs | `c.a = 1` with `__setattr__` | fenced | `PS4201` (registered #432) |
| PS4201 attrs | `c.a = 5` on a `@property` | checker | \|     ^^^ |
| PS4201 attrs | `c.d` with `__get__` | fenced | `PS1003,PS1104` |
| PS4018 star import | `from math import *` | fenced | `PS2002` |
| PS4018 star import | `from os.path import *` | fenced | `PS2002` |
| PS4019 variadics | `def f(*args: int)` + `f(1,2,3)` | works | result `6` |
| PS4019 variadics | `def f(**kwargs: int)` + `f(a=1,b=2)` | works | result `3` |
| PS4019 variadics | `g(a=1)` keyword call | works | result `1` |
| PS4019 variadics | `for a in args` inside `f(*args)` | works | result `1` |
| T0 exceptions | `raise E('x')` / `except E` | works | result `1` |
| T0 exceptions | `except:` | works | result `1` |
| T0 exceptions | `except ValueError as e` | works | result `2` |
| T0 generators | `def f(): yield 1` | checker | \|            ^^^ |
| T0 async | `async def f()` | fenced | `PS2002` |
| T0 async | `asyncio.gather()` | fenced | `PS2002` |
| T0 scopes | `global x` | fenced | `PS2002` |
| T0 scopes | `nonlocal x` in a nested def | works | result `2` |
| T0 encodings | `'\u20ac'` | fenced | `PS2005` |
| T0 encodings | `'\u00e9'` | works | result `1` |

Two rows deserve a note beyond the verdict. `class C: def __del__` compiles and runs, but the compiled binary never calls the finalizer where CPython does (`del c` prints `del ran` under CPython and nothing under the compiler): the method is silently dropped, which is the divergence PS4015 has to fence. `c.a = 1` with a `__setattr__` hook also compiles and runs, and the hook is not called: the attribute write goes straight to the field. Both divergences are closed by issue #432: the hook definitions refuse (`PS4203` and `PS4201`), so a class that defines them no longer compiles.

## Contradiction resolved: PS4006 `os.path` vs R31 alias

PS4006 refuses the `os.path` string-path API; backlog R31 plans an `os.path` → `posixpath` table alias. Both cannot hold. The census settles it: the alias is **already live** - `import os; os.path.join(...)`, `from os.path import join`, and `import posixpath` all compile, run, and match CPython byte-for-byte on this master. There is nothing left to refuse.

Resolution: **drop PS4006 as a refusal**. `os.path` is a lowered alias of the `posixpath` table (R31's remaining half, `import` inside functions, is untouched by this). The original motivation - `str`/`Path` type drift - is a checker concern, not a lowering refusal, and it stays enforced there. `pathlib` remains the recommended surface in examples and docs; no code change is needed because nothing is fenced today. The R31 backlog row keeps only its import-form items.

## The tier model (T0-T3)

Every construct in this doc routes into exactly one support tier. The tier decides what a PS4xxx code is allowed to claim and what the fix looks like, so a refusal can never regress into claiming a lowered construct again (the failure mode this re-census just cleaned up).

| Tier | Meaning | Enforcement | Codes |
| --- | --- | --- | --- |
| **T0** | refuse forever - semantics cannot be made deterministic or statically resolvable | hard fence, dedicated PS42xx row | PS42xx, permanent |
| **T1** | island-only - correct only through the CPython island (`--fallback=cpython`), never native | fence in native builds, allowed through the island | PS42xx with island hint |
| **T2** | build-time desugar - provably result-identical rewrite before lowering | lowered; no diagnostic | none |
| **T3** | warning lane - legal and deterministic, refused only as style | warning (channel does not exist yet); fence until it does | PS42xx while fenced, warning later |

Assignment rule: a construct is T2 only when every CPython-observable result is preserved by the rewrite (same value, same order, same exceptions, same side-effect timing); anything that depends on interpreter state, addresses or laziness is T0; anything whose only objection is style is T3; anything that needs a real CPython to be correct is T1. The boxed ledger (`benchmarks/compat2/boxed_cost.py`) arbitrates T1: if the island's cost share rises or the drop flag trips, the T1 rows convert to T0 rather than growing the runtime. Latest window (2026-10-22, at this re-census): code 0.7%, churn 0.0%, bug 0.0%, tax 0.0%, drop_flag false - the T1 tier is affordable as of this doc.

### Tier assignment

**T0 - refuse forever.** Interpreter hooks (`setrecursionlimit`, `settrace`, `setprofile`, `getrefcount`); `__del__`/`weakref`/`gc` (PS4203 for the hook, `weakref`/`gc` calls still PS2002); `id()`/default `repr` - process identity is non-reproducible by construction; `__getattr__`/`__setattr__`/`__getattribute__`/descriptors on classes (PS4201, post-census scope); non-literal monkeypatching and `__dict__` writes; `globals()`/`locals()`/`vars()`/`dir()`; `itertools.count`/`cycle` - an unbounded iterator is a hang with no stack; `asyncio.gather` (if async ever lands); generators-as-values outside the lowered lanes (R24 territory); scalar `is` where the operands are runtime-built (the divergence above; the eager literal case is T2-adjacent but the pointer semantics make the general form T0); full UTF-8 `str` (Latin-1 by design - the one epic-sized refusal, documented as bounded rather than refused row-by-row); async/await (unchartered epic).

**T1 - island-only.** `eval`/`exec`/`compile` (PS4001's native half stays T0; the island lane is a ~30-line `PyRun_String` shim); `importlib.import_module`/`__import__` (needs a `ps_island_call(mod, fn, args)` trampoline over lowered types only). These never lower natively: dynamic code defeats static resolution, which is a T0 property, but the island can honor it, which is a T1 property. The boxed ledger decides whether libpython linkage is worth carrying; if it is dropped, these convert to T0.

**T2 - build-time desugar, provably correct.** `map`/`filter` on concrete iterables (already implemented as comprehensions; `count`/`cycle` stay T0); `str`/`bytes` mixed `==`/`!=` → constant false/true (that is CPython's answer); literal `setattr(obj, "x", v)` → plain store (not yet lowered, but result-identical); intra-package `from .mod import *` (exports are statically resolved); mutable default argument → per-call copy of a frozen constant (the doc's earlier "cannot model hidden allocation" overstated it: the default is a constant display, so a copy is the CPython-fixed semantics, though this is a behavior *change* from CPython and therefore needs a caveat row, not a silent rewrite); implicit string concatenation is **not** T2 - see below.

**T3 - warning lane (does not exist yet).** `datetime.utcnow()` (trivially lowerable as `now(timezone.utc)` - the objection is deprecation style, not semantics, and the fence exists only because the `timezone` surface is not lowered); bool arithmetic (`True + True`); mutator-as-value (`x = xs.sort()`, R26-gated anyway); `hash()` of int/bool (PsMap already hashes internally; a deterministic own-hash is feasible and correctly refused on CPython-salt grounds - the refusal is a reproducibility decision, not an impossibility).

### AST blind spot: PS4008 cannot be implemented as specified

`"a" "b"` compiles and runs today, and no AST-level pass can see it: `ast.parse` merges implicit concatenation into a single `Constant` node before lowering ever runs, so the span and the two-literal structure are gone. PS4008 as written (a lowering-time fence) is unimplementable. Two honest options: (a) a tokenize-level lint in the checker lane, where the two tokens are still visible, or (b) drop the row and let it ride as a CPython `SyntaxWarning`-adjacent style note. This doc records the blind spot and leaves the choice to the checker epic; no PS4xxx code is reserved for it.

## Decision table (post-census)

| Feature | Verdict | Tier | Write instead |
| --- | --- | --- | --- |
| `eval`, `exec`, `compile`, `__import__` | refuse (PS4001) | T0 native, T1 island | closed import table; compile-time constants |
| `importlib.import_module` | refuse (PS4001) | T1 | static import names |
| `globals()` / `locals()` / `vars()` mutation, `dir()` on live objects | refuse (PS4002) | T0 | a `dict` passed explicitly |
| Monkeypatching: `setattr` any form, `delattr`, `Class.attr = x`, `obj.__dict__` writes | refuse (PS4003) | T0 | constructor args, module constants |
| Naive `datetime` construction: `utcnow()` | refuse (PS4004) | T3 | `datetime.now(timezone.utc)` once that lowers |
| `os.path.*` string-path API | **supported** (alias of `posixpath`) | - | `pathlib.Path` preferred, `os.path` works |
| Mutable default argument (`def f(x: list[int] = [])`) | refuse (PS4007) | T2 candidate | required parameter, or a module constant copied per call |
| Implicit string concatenation (`"a" "b"`) | unenforceable at AST level | - | one literal, `+`, or an f-string; tokenize-lint if wanted |
| `is` on runtime-built scalars/strings | refuse (PS4009, reduced) | T0 | `==` (or `is None`) |
| `str`/`bytes` mixing in `==`, `in`, concatenation | refuse (PS4010) | T2 for constant folds, T0 as a value | explicit `.encode()` / `.decode()` |
| `bool` in arithmetic (`True + True`, `sum` of bools) | refuse (PS4011) | T3 | `int(flag)` before arithmetic |
| In-place mutator used as a value (`x = xs.sort()`) | refuse (PS4012) | T3 | `sorted(xs)` / the method, then the name |
| Lazy/infinite iterators as values: `itertools.count`, `itertools.cycle` | refuse (PS4013, reduced) | T0 | comprehensions; `map`/`filter` are already eager |
| Interpreter hooks (`setrecursionlimit`, `settrace`, `setprofile`, `getrefcount`) | refuse (PS4014) | T0 | none - compiler-owned process |
| `__del__`, `weakref`, `gc` module | refuse (PS4203 for the hook) | T0 | `with` / explicit `close()` |
| `id()`, `hash()` of str/bytes, default `repr` addresses | refuse (PS4016) | T0 (`hash` of int/bool is T3) | a field that carries the identity you need |
| `__getattr__` / `__setattr__` / `__getattribute__`, `__delattr__`, descriptors on classes | refuse (PS4201, PS4202; property getters lower) | T0 | plain annotated fields and methods |
| `from x import *` (cross-module) | refuse (PS4018) | T0 | named imports |
| `*args` / `**kwargs` signatures | **supported** (lower as list/dict) | - | plain positional parameters still preferred |
| `asyncio.gather` | refuse if async ever lands | T0 | structured concurrency only (`TaskGroup`) |
| `bare except` | **supported** (E2 M3) | - | `except Exception` preferred in new code |
| `*args`/`*xs` at call sites, `super()` | fence (PS2002) | queue | positional arguments |
| Recursion + `itertools` recursion helpers | caveated | - | loops; deep recursion needs evidence |
| `'%s' % x` / specs / mappings (whole printf surface) | **supported-with-caveat** | T3 (style) | prefer f-strings in new code |
| `x is None` → `==` desugar | supported-with-caveat | - | fine for `None` only; anything else is PS4009 |
| Set iteration order = insertion order | supported-with-caveat | - | `sorted(s)` for deterministic output |
| `int` overflow / `//` by zero abort | supported-with-caveat | - | real exceptions pending R10 |
| `assert` always on (no `-O`) | supported-with-caveat | - | never put side effects in `assert` |
| `tuple` synthetic-class repr | supported-with-caveat | - | cosmetic divergence only |
| `@property` getter, user exception classes, `getattr`/`hasattr` literal | **supported** (post-census) | - | as documented in `.agents/docs/support/supported.md` |

## Tier A: refuse with a diagnostic now

These constructs are fenced today by generic codes or are checker-caught. The proposal is a dedicated PS4xxx entry so `pyscriptc explain` and `pyscriptc coverage` can tell the user *why it will never work*, not just that it didn't. Only the post-census rows survive here; a row whose probe compiled is gone from this section.

### PS4001 - dynamic code execution: `eval`, `exec`, `compile`, `__import__`, `importlib.import_module`

A compiler that statically resolves every name cannot honor a string that becomes code at run time. `eval`/`exec` also defeat the whole point of the subset: any program accepting them is a shell, and no static guarantee survives one. `importlib.import_module` is fenced today (closed import table, `.agents/docs/support/supported.md`); it is listed here because it belongs to the same refusal even after the import table grows. Tier: T0 natively, T1 through the island.

Diagnostic: `eval()/exec()/compile()/import_module() are refused: dynamic code defeats static resolution` - hint: `name the import statically, or move the dispatch into a dict of functions`.

### PS4002 - namespace reflection and mutation: `globals()`, `locals()`, `vars()`, `dir()`

`locals()` writes are documented by CPython as not affecting the local scope, which already makes mutation unreliable across interpreters; `globals()` mutation is monkeypatching-by-dict and breaks the closed world pyscriptc resolves at compile time. Both are refused regardless of whether the dict is read or written. Tier: T0.

### PS4003 - monkeypatching: `setattr` (all forms), `delattr`, `Class.attr =`, `__dict__` writes

Post-census scope: `getattr`/`hasattr` with a *string literal* lower, but `setattr` refuses in every form, including a literal name - the probe fenced PS2002 where the previous draft claimed only the computed-name half refused. Assigning to attributes of a class or module object and direct `__dict__` writes refuse for the same reason: class layouts are fixed at lowering, and a field that can appear at run time has no slot. Tier: T0 (a literal-name `setattr` desugar is a T2 candidate, listed below).

### PS4004 - naive datetime construction: `datetime.utcnow()`

`datetime.utcnow()` is deprecated since CPython 3.12, and naive-vs-aware arithmetic raises `TypeError` in CPython - a trap that fires late, at the subtract/compare, far from the construction. The naive `now()` without tz currently *lowers*; `utcnow()` fences PS2001. Tier: T3 - the objection to `utcnow` is style/deprecation, not semantics, and the substitute `datetime.now(timezone.utc)` is itself fenced until the `timezone` surface lands, so the diagnostic's fix hint cannot be honored yet. Register the code only when the substitute lowers.

### PS4005 - printf-style `%` formatting

Dropped as a refusal. The census shows the entire surface this row meant to refuse - format specs, mapping keys, tuple arguments, `%%` escapes - already lowers and matches CPython. What remains is a Tier C style note: prefer f-strings in new code. No PS4xxx code is reserved.

### PS4006 - `os.path` string-path API

Dropped. See the contradiction resolution above: `os.path` is a live alias of the lowered `posixpath` table. The `str`/`Path` drift concern belongs to the checker, which already enforces it.

### PS4007 - mutable default argument

`def f(x: list[int] = [])` shares one list across all calls - the single best-known Python footgun, in the official FAQ. The signature refuses today (PS2002) and the row stands. Tier: T2 candidate - the default is a constant display, so lowering it as a per-call copy is the CPython-fixed semantics, but that is a deliberate divergence from CPython's shared-list behavior and needs its own caveat row and a corpus decision before it lands, not a silent rewrite.

### PS4008 - implicit string concatenation

Unimplementable at the lowering layer: `ast.parse` merges `"a" "b"` into one `Constant` before any pass can see the two literals. Enforcement, if wanted, belongs to a tokenize-level check in the checker lane. No PS4xxx code is reserved.

### PS4009 - `is` on runtime-built values (reduced scope)

Post-census scope. Literal-and-scalar `is` (`x is 5`, `x is 'abc'`) lowers to value equality and matches CPython for the interned/small-int cases; heap `is` compares pointers. The refusal keeps only the divergent half: runtime-built operands, where the compiled binary's pointer answer differs from CPython's folded-constant answer (probe: `('he' + 'llo') is 'hello'` → `0` compiled, `1` CPython). Tier: T0 for the general form.

### PS4010 - `str`/`bytes` mixing

`b"x" == "x"` is always `False` in CPython - no error, just silently wrong. Cross-type `==`/`in` fence PS2004 at the operator; cross-type `+` is caught by the checker rather than a lowering fence (probe exits at the checker layer). Tier: the constant-fold half is T2 (folding to CPython's constant answer); the value-level half is T0. Hint: decode or encode explicitly at the boundary.

### PS4011 - `bool` in arithmetic

`True + True == 2` works in CPython because `bool` subclasses `int`; pyscriptc already refuses `list[bool]` and dict `bool` values, so arithmetic on `bool` is the same refusal at the operator (PS2004). Tier: T3 - deterministic, legal CPython; the refusal is a style/policy decision, so it migrates to a warning if that lane ever lands.

### PS4012 - in-place mutator used as a value

`x = xs.sort()`, `return d.update(...)`, `y = xs.append(v)` all evaluate to `None`. Today the binding fences PS1001 (annotation) before the mutator question is reached. Tier: T3, R26-gated: once optional/None values land, the honest behavior is a warning that the bound value is always `None`, not a fence.

### PS4013 - lazy/infinite iterators as values (reduced scope)

Post-census scope. `map`/`filter` already desugar to eager comprehensions (`restlower/_iterators.py`) and `list(map(g, xs))` matches CPython; `zip` is the iterator of a `for`. The refusal keeps only the unbounded forms: `itertools.count`/`cycle` fence PS2001 ("infinite iterators are not lowered") - an unbounded iterator in a compiled program is a hang with no stack, and that is a T0 property, not a queue position.

### PS4014 - interpreter hooks

`sys.setrecursionlimit`, `sys.settrace`, `sys.setprofile`, `sys.setswitchinterval`, `sys.getrefcount` - there is no CPython interpreter to configure; the emitted binary has a fixed model. Any call is a refuse. Tier: T0.

### PS4015 - finalizers and the GC surface

`__del__`, `weakref`, `gc.*` - finalizer timing is explicitly unspecified in CPython, and the runtime has no GC to control. Resource lifetime is `with` + `close()` only. Post-census scope: `weakref.ref(c)` and `gc.collect()` fence today (PS2002), but a class that *defines* `__del__` compiles and runs with the finalizer silently dropped (probe: `del c` prints `del ran` under CPython and nothing in the binary). The code has to fence the `__del__` definition, not only the `weakref`/`gc` surface, or the binary diverges silently. Tier: T0.

### PS4016 - process-identity values: `id()`, `hash()`, default `repr`

`hash()` of `str`/`bytes` is salted by `PYTHONHASHSEED` and differs between runs; `id()` is an address; a default `<object at 0x...>` repr embeds both. Programs that print or compare these are non-reproducible by construction. Tier: T0 for `id`/default repr and str/bytes `hash`; T3 for `hash()` of `int`/`bool` (deterministic own-hash is feasible; the refusal is a reproducibility policy choice).

### PS4201 - dynamic attribute protocol (reduced scope)

`__getattr__`, `__setattr__`, `__getattribute__`, descriptors (`__get__`/`__set__`). Attribute access is resolved statically to declared fields; a hook that can intercept any name lookup defeats that resolution. Post-census scope: `@property` getters lower and stay; the setter and the call form fence; `__getattr__` fences PS2001 when it is actually reached. But a class that *defines* `__setattr__` compiles and runs with the hook never called (the write goes straight to the field), so the code has to fence the hook definition, not only the dynamic-protocol surface. Tier: T0.

**Registered (issue #432, D1/O1).** The hook *definition* now fences with `PS4201` at the definition site, so the silent `__setattr__` divergence is gone: a class that defines the hook no longer compiles and runs with the hook never called. The refusal is on the hook names only; `@property` getters keep lowering and the getter is still called at the read site. Coverage family: `refused-dynamic-attrs`.

### PS4202 - attribute deletion protocol

`__delattr__` intercepts `del obj.name`. The lowered subset has no attribute deletion (fields are fixed at lowering and never removed), so the hook would never run. Tier: T0. **Registered (issue #432)**: the definition fences with `PS4202`, coverage family `refused-dynamic-attrs`.

### PS4203 - finalizer hook

`__del__` is the finalizer half of the PS4015 row (finalizers and the GC surface). CPython calls it when the object is garbage-collected and that timing is explicitly unspecified; the runtime has no GC to trigger it, so the method was silently dropped (probe: `del c` prints `del ran` under CPython and nothing in the binary). The `weakref` and `gc` surfaces already fence; the hook definition now fences too. Tier: T0. **Registered (issue #432)**: the definition fences with `PS4203`, coverage family `refused-finalizer`.

### PS4018 - `from x import *` (cross-module)

Star imports make the name set unknowable statically - exactly what the closed import table exists to prevent. Refuse in every cross-module context; the intra-package form (statically resolved exports) is a T2 candidate. Tier: T0 as written, T2 for the intra-package case.

## Tier B: refused, door open

- **`global` writes beyond the declared process-slot form, `nonlocal`.** The probe shows plain `global` statements fence PS2002 and `nonlocal` inside a nested function works when reached through a lowered path; verdict is deferred to R22 (closures).
- **`asyncio.gather`.** Async itself is fenced. If it ever lands, `gather` still refuses: its documented semantics are the canonical unstructured-concurrency trap; `asyncio.TaskGroup` (3.11+) is the only acceptable shape. Tier: T0.
- **Recursion helpers.** Plain recursion lowers; recursion *as a strategy* (deep trees, `itertools.accumulate` chains) needs corpus evidence on stack behavior before any caveat is written.
- **Generators and `yield` as general values.** The lowered coroutine lanes exist (`src/pyscriptc/coro.py`); the general generator-expression form is R24 territory and stays a queue row, not a refusal.

## Tier C: supported with caveats

Deliberate divergences that are already lowered and documented; kept because the cost of removing them exceeds the footgun risk. Documented here so the refusal list doesn't quietly grow. Post-census additions marked.

- `'%s' % x`, `'%d' % x`, format specs, mapping keys, tuple arguments, `%%` escapes all lower and match CPython. Caveat: prefer f-strings; this whole surface moved here from the refused table after the census.
- `x is None` lowers as `==` against `None`. Caveat: correct only because `None` is a singleton; runtime-built operands are the PS4009 divergence.
- Scalar/heap `is` on literals lowers to value equality or pointer compare (`lower/_expr.py:526`). Caveat: runtime-built equal strings diverge from CPython (compiled answers `0`, CPython `1`); the general form is the PS4009 refusal.
- `*args` lowers as a `list` (not a `tuple`), `**kwargs` lowers as a `dict`; internal calls and iteration match CPython. Caveat: starred call sites `g(*xs)` still fence. Post-census.
- `@property` getters lower; user exception classes raise and catch; `getattr`/`hasattr` with a string literal lower; bare `except` and `except E as e` lower (E2 M3). Post-census.
- `os.path.*` is a live alias of the `posixpath` table. Post-census; `pathlib` remains the recommended style.
- Set iteration is insertion order, not hash order. Caveat: deterministic and stable, but different from CPython - use `sorted()` where order matters.
- `int` overflow and `//` by zero `abort()` instead of raising. Caveat: a known trap-vs-raise gap, tracked for E2.
- `assert` always evaluates - there is no `-O` flag on a compiled binary. Caveat: never put side effects in the condition.
- `tuple_N_*` synthetic classes: `repr` and `type()` name differ from CPython. Cosmetic only.

## Does refusing change the corpus scorer?

Mostly no. compat2 scores probe *outcomes* against the CPython oracle; every refused construct above is fenced today, so flipping a PS2002 to a PS4201 leaves the weighted pass untouched - a fence is a fence either way. The two ways a refusal moves the score:

1. **Refusing something that currently lowers** (e.g. a row this census just moved to Tier C) flips its probes from pass to fence - a real score regression. This is exactly what the re-census prevents: PS4005 and PS4006 were about to claim lowered surfaces.
2. **`coverage`/`census` families.** `family_of` in `src/pyscriptc/coverage.py` maps codes to families; each PS42xx code needs a family row so refusals don't collapse into `construct-other` - cheap, and it makes the census report "refused-by-design" as its own blocker class, which is honest signal for the top100 port lanes.

Net: refusal is a *diagnostic* change, not a capability change - provided the refuse list only claims constructs already fenced, which is now enforced by the tier model rather than by hope.

## Fences or warnings?

Recommendation: **hard fences for every T0 row, no warning tier yet.**

- Fences for silent-divergence semantics (the T0 rows above): if a compiled program could produce a different result than CPython - or a result that is unspecified - continuing the compile is exactly the failure mode this list exists to prevent. The whole value of pyscriptc is "the binary does what the source says"; a warning on a runtime-built `is` compare abandons that.
- The T3 candidates - `utcnow()`, bool arithmetic, mutator-as-value, int/bool `hash()` - are legal, deterministic CPython; a warning would suffice. But there is no warning channel today: the IR has `fences`, `errors.py` has `IRError`, and nothing emits a non-fatal diagnostic. Adding one is a small epic (IR `warnings` field, `pyscriptc compile --warn` surface, `coverage` classification) and should not smuggle itself into the PS42xx rollout. Until it lands, the T3 rows keep their current fence or lowered state and carry no new code.
- If a warning tier lands later, the split is the tier column above: T0 semantics stay fences forever; T3 style migrates from fence (or lowered-with-caveat) to warning.

## Implementation notes

- Register the codes in `src/pyscriptc/diagnostics.py` (`title`/`why`/`fix`), add a `_RULES` row per code in `src/pyscriptc/coverage.py` mapping to new `refused-*` families with the substitute as the hint.
- Lowering emits them at the same sites that produce the generic fence today - the detection points already exist (`builtins.py` name table, `stdmore.py` sig table, `_stmt.py`/`_expr.py` construct checks); only the code and message change. No existing fence message is edited.
- Registration order follows the tiers: T0 rows first, because those constructs are fenced today and the code only renames the fence. Rows whose target surface is lowered or undetectable are **not** registered.
- `pyscriptc explain PS42xx` then answers "why is this refused" directly, which is the deliverable: a diagnostic that teaches the substitute instead of reporting a gap.

### Registered so far

| Code | Row | Fences at | Family |
| --- | --- | --- | --- |
| `PS4201` | dynamic attribute protocol (`__getattr__`, `__setattr__`, `__getattribute__`, `__get__`, `__set__`) | the hook definition in a class body | `refused-dynamic-attrs` |
| `PS4202` | attribute deletion protocol (`__delattr__`) | the hook definition in a class body | `refused-dynamic-attrs` |
| `PS4203` | finalizer hook (`__del__`) | the hook definition in a class body | `refused-finalizer` |

The rows landed with issue #432 (plan D1, OOP plan O1). The remaining rows keep their numbers from the tables above until they land, so a reader can tell a registered code from a proposed one.
