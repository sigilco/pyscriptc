# PEP 681 `dataclass_transform`: what pyscriptc does today, and a library-agnostic design

Date: 2026-11-02. Branch `work/metatab` (base `origin/work/ecoreflect`, `ad3b94ef`),
worktree `~/.worktrees/pyscriptc-metatab`. Companion notes: the runtime-reflection
note (roadmap item 3, the compile-time metadata tables, which this session landed on
`work/metatab`), the ecosystem-reflection note (the top-100 compatibility ranking), and
the pydantic-support note (the seam verdict). Owner direction for this session: no
library-specific compiler changes; the general mechanisms are approved, so everything
below is written against the *standard* (PEP 681) and never names a library.

Question: does pyscriptc honor `typing.dataclass_transform`? A decorator, base class
or metaclass marked with it declares that its subclasses behave like dataclasses: the
subclass's own annotations become fields, and a constructor is synthesized from them.
This is how the model-shaped libraries declare themselves, so it is the standard's
entry point for the whole "a class's annotations are its fields" idea.

**Answer: no. Today pyscriptc fences every PEP 681 shape, and it fences them for a
reason that has nothing to do with the transform — the decorator that carries the mark
is itself a decorator the compiler does not lower. The standard is also not in the
compiler's typing-name table, so even a class that somehow reached the compiler with
the mark applied would not be recognized as dataclass-shaped.** The good news, measured
below, is that pyrefly (the checker whose types we consume) *does* honor PEP 681 fully,
so the checker lane already believes in the shape and the compiler lane does not: that
gap is exactly the seam a design has to close.

---

## 1. What happens today, measured

Four shapes, the three PEP 681 spellings plus a control. Every case is the smallest
program that uses the shape; the compiler is invoked through
`pyscriptc coverage` (which runs the pyrefly preflight first) and through
`pyscriptc compile`.

### 1.1 The base-class form

```python
from typing import dataclass_transform

@dataclass_transform()
class Base:
    pass

class M(Base):
    x: int
    y: str

def run() -> int:
    m = M(1, "a")
    print(m.x, m.y)
    return 0
```

`pyscriptc coverage` reports **4 fences (3 root) in 3 of 3 definitions (0 native)**:

| fence | family | in |
|---|---|---|
| `PS1201: decorators are not lowered` (at `class Base:`) | decorators | `Base` |
| `PS2002: this class base is not lowered` (at `class M(Base):`) | class-cascade (derived) | `M` |
| `PS2002: call to M is not lowered` | unlowered-call | `run` |
| `PS2002: m is not a local or parameter` | name-resolution | `run` |

The first fence is the root cause and the rest are cascades. Two facts matter:

- The root fence is on **`Base`**, not on `M`. `M` is fenced only because its base
  did not lower. That is the class-base cascade the class-base-cascade note measures;
  the transform is downstream of it.
- The root fence is **PS1201 "decorators are not lowered"**, not anything about
  dataclasses, typing, or field tables. The compiler has no idea what
  `@dataclass_transform()` means; it sees a decorator it does not recognize on a class.

The control (the same program with the decorator removed) fences differently:

```
PS1001: M needs __init__          <- the class is not dataclass-shaped
PS2002: call to M is not lowered
PS2002: m is not a local or parameter
```

So the control is *one fence better* than the transform form: the transform form's
decorator fence replaces the control's "needs __init__" fence and then cascades into
the base. **Today, adding the standard marker to a class makes the program strictly
worse to compile.** That is the concrete user-visible defect.

### 1.2 The decorator form

```python
@dataclass_transform()
def model(cls: type) -> type:
    return cls

@model
class M(Base):
    x: int
    y: str
```

**5 fences (5 root) in 4 of 5 definitions (1 native)**: PS1201 on the metaclass
declaration, PS1106 (`cls: type` — `type` is not a lowered type), PS1201 on `@model`
itself, then the two call-site cascades. Same story, one more root fence: the
*applying* decorator is refused as well as the *marking* one.

### 1.3 The metaclass form

```python
@dataclass_transform()
class ModelMeta(type):
    pass

class M(metaclass=ModelMeta):
    x: int
    y: str
```

`PS1201` on the metaclass declaration, then **PS1203** with the metaclass note:
*"runtime reflection (metaclass) hands class creation to a metaclass hook at run time,
and native code fixes the class layout at compile time; this code routes to the island
lane (requires libpython); typed idiom: write the class explicitly, or use PEP 695
generics"*. The metaclass form is refused *by design* under the island model — a
metaclass hook genuinely runs at class-creation time, and native code has no such
moment. That refusal is a separate, deliberate decision from the transform support,
and section 4 keeps them separate.

### 1.4 The typing name itself

`from typing import dataclass_transform` on its own is free (the import is erased, as
every `typing` import is). Reading the name as a value fences:

```
PS2002: dataclass_transform is a typing name and is not a value
```

which is the correct `typing`-is-annotation-only framing (never "typing is not
supported"). But note *why* it fences: it is in the compiler's erased-name table and
is not on the list of names the compiler knows how to *apply*. `abstractmethod` and
`overload` are on that list (`_ERASED_DECORATORS` in `lower/_classes.py:58`) and
therefore erase off the class; `dataclass_transform` is not, so it cannot.

### 1.5 The compiler's own knowledge

- `grep dataclass_transform src/` returns nothing outside the vendored typeshed
  stubs (`src/pyscriptc/_typeshed/stdlib/typing.pyi:1035`,
  `typing_extensions.pyi:341`). Those stubs exist so **pyrefly** sees the standard; the
  compiler itself has no notion of it.
- The class decorator path (`lower/_classes.py:540`, `_class_decorator`) accepts exactly
  one class decorator, `dataclasses.dataclass` (`_is_dataclass_deco`). Every other
  class decorator is PS1201.
- The base path (`lower/_classes.py:656`, `_erased_base_name`) accepts exactly three
  erased bases, `ABC`, `ABCMeta`, `Protocol` (`_ERASED_BASES`). Every other base must
  resolve to a lowered class.
- The dataclass field machinery is **already** the mechanism a transform needs: the
  synthesized `__init__`, the `kw_only`/`init`/`repr` flags, the field table, and (as
  of this session's `work/metatab` commits) the `fields`/`asdict`/`astuple`/`replace`
  and `vars()` surface all read one field-row list the class record owns.

So the compiler has the *machinery* and lacks the *marker*. That is the whole gap.

---

## 2. What pyrefly reports

pyrefly 1.4.0 (the checker `check.py` drives as the preflight) **honors PEP 681
completely**. Measured with a config file present so the "basic" preset does not
suppress errors (`pyrefly check --output-format json`):

| program | pyrefly verdict |
|---|---|
| `class M(Base)` with `x: int, y: str`, `M(1, "a")` | 0 errors — the synthesized `__init__` is accepted |
| `M("not an int", "a")` | 1 error: `bad-argument-type`: "Argument `Literal['not an int']` is not assignable to parameter `x` with type `int` in function `M.__init__`" |
| `M(1, "a", 3)` | 1 error: `bad-argument-count`: "Expected 2 positional arguments, got 3 in function `M.__init__`" |
| `m.x = "now a string"` | 1 error: `bad-assignment`: "`Literal['now a string']` is not assignable to attribute `x` with type `int`" |
| the decorator form | 0 errors |
| the metaclass form | 0 errors |

The synthesized `M.__init__` in the error messages is pyrefly's own: it reports
`in function M.__init__` for a class that declares no `__init__`, which is exactly
what PEP 681 asks a checker to synthesize. The wrong-type and wrong-arity diagnostics
are the same diagnostics a real `@dataclass` produces.

**This matters for how the types are consumed.** Two facts from the code:

1. `check.py` consumes pyrefly as an **error oracle only** (`_run_pyrefly` parses the
   JSON error list and raises `CheckFailed`). It does not read pyrefly's types.
2. The compiler's own types come from the annotation reading in `lower/_classes.py`
   (`_field_options`, the `fields` list) and the optional checker-bridge lane
   (`factsbridge.py`, `PyreflyFactsAdapter`), which answers *expression* and *def*
   facts (parameter types, return types) — **not** class shape.

So "we consume pyrefly's types" is true at the expression and def level, and the class
shape — the field table — is the compiler's own today. That is the load-bearing fact
for the design: **PEP 681 support cannot be delegated to the checker.** pyrefly is
right and the compiler is wrong, and the compiler's answer is what becomes the binary's
layout.

There is a concrete consequence for the preflight: today, a program that uses the base
form passes pyrefly (0 errors) and then fails pyscriptc's own lowering with PS1201.
The two lanes disagree, and the compiler lane is the one that decides the binary.

---

## 3. The mechanism: a class-shape table, marked by a standard, with a data-driven registry

### 3.1 The shape of the idea

The general mechanism is not "support dataclass_transform"; it is **"a class's
annotations can be its fields, and the class can carry a synthesized constructor"**,
with the trigger being a *mark* rather than a hardcoded decorator name. The mark
already has a standard spelling (PEP 681) and the compiler already has the machinery
(the dataclass field rows and the synthesized `__init__`).

So the mechanism has two parts:

**(a) A class-shape flag on the class record.** The class record already carries
`fields`, `own_fields`, `methods`, `method_kind`, `method_owner`, `synthetic`,
`record`, `enum`. Add one more: the *shape* the class was declared in — `record`
(today's dataclass shape) or `plain` (today's default). A `dataclass_transform`-marked
subclass is collected with the `record` shape, which means it reuses **the existing
field collection, the existing flag reading (`kw_only`, `init`, `repr`), the existing
`__init__` synthesis, and the existing metadata-table surface** with no new emission
path. The metadata tables this session landed are then the *read* side of the same
table: `dataclasses.fields(M)` over a transform-shaped class returns the same rows.

**(b) A mark registry: how a class is told to be dataclass-shaped.** PEP 681 defines
three ways to apply the mark, and each maps onto something the compiler already knows
how to resolve:

| PEP 681 form | what the compiler must recognize | compiler state today |
|---|---|---|
| a **base class** marked `@dataclass_transform()` | "this base is marked; a subclass of it is dataclass-shaped" | the base must itself lower (today it fences on PS1201), and the base must be recorded as marked |
| a **decorator** marked `@dataclass_transform()` | "this decorator is marked; a class it decorates is dataclass-shaped" | the decorator must lower as an identity decorator over a class, and be recorded as marked |
| a **metaclass** marked `@dataclass_transform()` | "this metaclass is marked; classes with it are dataclass-shaped" | PS1203 by design; the metaclass form stays refused (section 4) |

The registry is **data, not code**. The natural home is the same shape the compiler
already uses for erased names (`_ERASED_DECORATORS`, `_ERASED_BASES` in
`lower/_classes.py:57-58`): a frozenset of *marking* names that the compiler treats as
"this name applies the dataclass shape" rather than "this name is dropped".

But a bare frozenset is not enough for the "library-supplied adapter" requirement
(section 3.4), so the registry needs to be a small table, not a set:

```
# name -> what the mark means
dataclass_transform -> marks the *decorated* thing as a dataclass-shape source
```

and the *source* of the mark is a class or a function, recorded on the class record:

```
"shape_source": "transform"   # this class's shape comes from a dataclass_transform mark
"shape_marked_by": "Base"     # the base class that carried the mark
```

### 3.2 The collection path, concretely

The compiler already resolves a class's bases and its decorator in
`_collect_class`. The change is:

1. **Recognize the marking decorator.** `dataclass_transform` joins the erased-name
   handling: `from typing import dataclass_transform` binds an erased name, and
   `@dataclass_transform()` on a class or a function is *consumed* (not PS1201) and
   records the mark on the decorated thing. The mark is a compile-time fact about the
   class's *shape*, so the decorator call itself has no runtime effect and is erased —
   exactly like `@abstractmethod` and `@overload` are erased today.
   - The `dataclass_transform` *arguments* matter for full PEP 681 compliance:
     `field_specifiers=(...)` names the functions that customize field construction,
     `eq_default`, `order_default`, `kw_only_default`, and `frozen_default` set
     defaults. The compiler's subset has no runtime equality/order, no freezing, so
     most of them are inert; `field_specifiers` is the one that matters and it is
     exactly the `dataclasses.field` surface this session already supports.
   - **The arguments that would change semantics must be checked, not ignored.** A
     `frozen_default=True` would promise that the class's fields cannot be assigned;
     the lowered record has no freezing, so honoring the mark without the freezing
     would be a silent divergence. The honest move: accept the mark, and refuse the
     shapes whose promise the subset cannot keep, with a named fence that says which
     promise is dropped and what the native answer is (the same shape as the
     `PS1203` metaclass note and the `PS2002` typing-name framing).

2. **Recognize the marked base.** When a class's base resolves to a lowered class
   whose record carries the mark, the subclass is collected with the `record` shape:
   the same field collection and the same `__init__` synthesis as `@dataclass`. The
   marked base's own annotations are *not* fields (PEP 681 says the base's own
   annotated attributes are not fields of the subclass unless the base is itself
   dataclass-shaped — the base class marks the *shape*, it does not contribute
   fields). That is a real difference from "inherit from a dataclass", and it is the
   reason this cannot be done by simply treating the base as a dataclass.

3. **Recognize the marked decorator.** `@model` where `model` is a marked function
   lowers as an identity decorator over the class. The compiler does not lower
   arbitrary decorators today (PS1201); an *identity* decorator is the one shape it
   can lower soundly (it is also roadmap item 5 of the runtime-reflection note,
   "wraps-shaped identity decorators"). The mark makes this decidable: a marked
   decorator is *declared* to be shape-only, so erasing it is correct rather than a
   guess.

### 3.3 What the shape buys, and what it does not

With the mark recognized, `class M(Base)` where `Base` is marked lowers as a record:
the annotations become fields, the constructor is synthesized from them, the field
table exists, and the metadata surface (`fields`, `asdict`, `astuple`, `replace`,
`vars()`) reads it — the same tables this session landed, over the same rows.

That is the *whole* of the native shape. What a marked library would then need on top:

- **Validation and coercion.** A model library validates on construction and coerces
  types ("the string `'3'` becomes the int `3`"). The native record constructor
  *cannot* do that: the field types are compile-time facts and the constructor's
  parameter types are checked at the call site. Coercion is a runtime decision about
  the *value*, and the native path has no place to make it — the value is already
  stored in a typed slot.
- **Default factories, validators, aliases, computed fields, `__post_init__`,
  `__init_subclass__` hooks, serialization hooks.** Each is either a runtime callable
  (an island/live-Python concern) or a per-class metaclass computation.

### 3.4 What must stay island-side, and how an adapter plugs in

**The island boundary is the right place for everything whose answer depends on the
value.** Concretely, the split is:

| concern | where | why |
|---|---|---|
| the field table (name, annotation type, default, `kw_only`, `init`, `repr`) | native, compile-time | already the compiler's own; it is a static fact |
| the synthesized constructor's *shape* (arity, parameter types, keyword names) | native, compile-time | a static fact; the call-site checks are the compiler's own |
| the constructor's *body* (validation, coercion, hooks) | **island** | depends on the value, and the library's callable is live Python |
| `__setattr__`/assignment validation | **island** | same |
| `model_dump` / serialization | **island** | the library's own code, over live values |
| `__eq__`, `__hash__`, ordering | **island** | runtime semantics over values |
| the class object itself (registry membership, identity, `isinstance` against it) | **island** | the closed-world model has no type objects |

So a marked library's model is, natively, a **record with a known layout**; and
island-side, **the same object with the library's behavior**. The seam is the
constructor call: the native record is the storage, and the island-side callable is
the validation and coercion wrapper.

**How an adapter plugs in without compiler code.** The requirement is a
*data-driven registry entry*, not a compiler change per library. The shape:

```
# a registry the compiler reads, keyed by the mark
"shape_adapters": [
  {
    "mark": "dataclass_transform",       # the standard mark
    "shape": "record",                   # native layout: record
    "validate": "island",                # validation semantics: island
    "coerce": "island",
    "field_specifiers": ["field"],       # the field-customizing callables the subset knows
  }
]
```

The compiler's *only* knowledge is: "a class marked this way has shape `record`, and
these concerns are island-side". It never learns a library's name. The library-specific
parts (which callable validates, how it coerces, what its serialization looks like)
are **the library's own island-side code**, reached through the existing island lane
and the existing class-handle mechanism (`island_classes`, `ps_island_fn_invoke`).

Concretely, the adapter for a marked library is a small island-side shim:

1. The library's base class is imported island-side and marked (its own
   `@dataclass_transform()` declaration travels with the import).
2. The compiler sees the mark, gives the subclass the record shape, and lowers the
   *storage*.
3. The library's `__init__` (validation and coercion) stays island-side and is
   invoked through the existing seam when the program constructs the model.
4. The native code never asks what the library is.

**The hard part, stated honestly**: step 3 means the *constructor* crosses the seam,
so the object that crosses must be a handle, not a native struct — which is exactly
the class-base cascade the class-base-cascade note measures, and the reason the
cascade note's option (3) ("make the base lowerable") dissolves the problem
differently: the *base* lowers natively and the library's behavior comes from a
different route.

There are therefore **two shapes of support, and they are not the same feature**:

- **Shape A — the shape only.** The marked class lowers as a native record. The
  program's own code (field reads, field writes, the metadata surface, the
  constructor call with the right arity and types) is native. Any behavior the
  library adds (validation, coercion, serialization) is **not** available, and if the
  program calls it, the call fences or routes island. This is honest and it is
  useful: it is what "the class is dataclass-shaped" means, and it is what the
  metadata tables already support.
- **Shape B — the shape plus the library's behavior.** The constructor crosses the
  seam. This is the mixed-plane problem the cascade note measured, and it is a
  separate, larger piece of work (it needs the copy-coherence and dispatch answers
  the cascade note lists).

The design should ship Shape A first, and Shape B only after the mixed-plane
questions are settled. **Shape A does not require the library to be island-side at
all** — a marked class with no library behind it (the `Base` in the example above) is
just a record, and that is the case that matters for the standard.

### 3.5 The interaction with this session's metadata tables

The metadata tables landed on `work/metatab` are the *read* side of the field table.
With the transform support, they read transform-shaped classes too, with no extra
work:

- `dataclasses.fields(M)` over a transform-shaped class returns the same rows the
  class's own record carries.
- `dataclasses.asdict` / `astuple` / `replace` / `vars()` are the same.

The one thing that must be decided: **does a transform-shaped class get the
`dataclasses.*` surface at all?** The surface is named after the `dataclasses`
module, and a transform-shaped class is not a `dataclasses` dataclass — CPython's
`dataclasses.fields` on a non-dataclass raises `TypeError`. Two consistent answers:

- **(a) Strict.** `dataclasses.fields(M)` on a transform-shaped class raises (fences
  in the native subset), exactly as CPython does. The transform shape is the *layout*,
  not the `dataclasses` API.
- **(b) Structural.** The transform shape *is* the dataclass shape, so the
  `dataclasses` surface applies to it too.

**(a) is the honest default** and the one this design takes: the divergence entry
should say that the metadata surface reads "a lowered dataclass", and a
transform-shaped class is dataclass-*shaped* but is not a `dataclasses` dataclass, so
the `dataclasses.*` calls on it keep their fence. If the evidence later says (b) is
what users need, the change is one predicate in `lower/_metadata.py`.

### 3.6 The typing rule (the framing the docs must use)

Per `AGENTS.md` and the python-style rules, this is **not** "typing is not
supported". `dataclass_transform` is a `typing` name, and `typing` is an
annotation-only module; the name here is *not* an annotation, it is a **declaration
marker**, and the honest framing is:

- Reading a typing name as a value: PS2002, unchanged.
- Applying `@dataclass_transform()` to a class or function: **this is the new
  capability**, and it is the standard's mechanism for declaring a class shape.
- The unsupported shapes keep **named fences**: an unrecognized class decorator is
  still PS1201; a metaclass form is still PS1203 (with the metaclass note); a marked
  base whose own body does not lower still cascades.

The construct table in `docs/guide/typing.md` (or `docs/guide/python-subset.md`, its
current name in this tree) gains a row for the mark, and the divergence or gap entry
changes in the same change.

---

## 4. What stays refused, and why

| shape | verdict | reason |
|---|---|---|
| a **base class** marked `@dataclass_transform()` | **supported** (Shape A) | the standard's most common spelling; the base's own body must lower, but that is the ordinary class rule |
| a **decorator** marked `@dataclass_transform()` | **supported** (Shape A) | needs the identity-decorator lowering (runtime-reflection roadmap item 5), which the mark makes decidable |
| a **metaclass** marked `@dataclass_transform()` | **refused** (PS1203) | a metaclass hook runs at class-creation time; native code fixes the layout at compile time. Unchanged by this work |
| `frozen_default=True` | **refused** with a named fence | the subset cannot freeze a record; honoring the mark would silently drop a promise |
| `field_specifiers` naming a callable the subset does not know | **refused** with a named fence | the field's construction would differ from the library's |
| validation / coercion / serialization | **island** | depends on the value, and belongs to the library's live code |
| the class object as a value, `isinstance` against it | unchanged | the closed-world model has no type objects |

---

## 5. Risks, and the open questions

1. **Silent divergence risk.** The dangerous failure mode is a marked class that
   lowers as a record while the program expects library behavior (validation on
   assignment, coercion on construction). The mitigation is that the *compiler* does
   not know the library: it grants the *shape* and nothing else. Any call to library
   behavior fences or routes island, so the user finds out at the call, not silently.
   The residual risk is a user who only ever reads fields and never triggers the
   library's behavior — they get a working record, which is *more* than they had, not
   less. That is a good failure mode.
2. **The `dataclass_transform` argument surface.** `eq_default`, `order_default`,
   `frozen_default`, `kw_only_default` and `field_specifiers` all have defaults the
   subset may not be able to keep. Each needs an explicit decision (honor, or refuse
   with a named fence). Refusing is safe; silently ignoring is not.
3. **The base's own body.** Today the base must lower for the subclass to lower at
   all (the cascade). A marked base that fences on its own body fences the subclass
   with it. That is the class-base cascade, not a transform problem, and the
   cascade note's option (3) (make the base lowerable) is the upstream fix.
4. **The identity-decorator lowering** (for the decorator form) is new machinery. It
   is small and it is already roadmap item 5 of the runtime-reflection note, but it is
   a separate commit.
5. **The `dataclasses.*` surface on transform-shaped classes** (section 3.5) needs a
   decision before the code lands, because the answer changes a predicate.
6. **The island bridge for Shape B** (the library's constructor crossing the seam) is
   the largest risk in this area, and it is the class-base cascade's mixed-plane
   problem again. It should not be attempted before the cascade note's measurements
   are re-run against a transform-shaped corpus.
7. **The checker disagreement is itself a finding.** pyrefly says the base form is
   fine; the compiler says PS1201. Any user who reads the checker output first is
   misinformed. Closing the gap removes the disagreement; until then, the fence
   message should name the standard ("this decorator is not lowered; PEP 681
   `dataclass_transform` is not applied yet") rather than a generic PS1201, so the
   user can tell *which* decorator is missing.

---

## 6. A measurement plan, when this is implemented

The same shape as the metadata-table probe
(`.agents/probes/metadata_tables.py`): one program per PEP 681 form, each compiled to
a native binary and diffed against the CPython oracle, plus the pyrefly lane on the
same programs, so the compiler and the checker agree by construction.

The programs:

1. base form, `M(1, "a")` → native, oracle-identical stdout.
2. base form, a wrong-typed argument → the native call-site check fires, the same
   diagnostic the checker gives.
3. base form, a wrong arity → the same.
4. decorator form → native.
5. metaclass form → still PS1203.
6. `frozen_default=True` → the named fence.
7. a marked class with a field the subset cannot lower → the named fence.

And the regression that matters most: the **marked base's own annotations are not
fields of the subclass** (PEP 681's rule), which is the one place where a
"just treat the base as a dataclass" shortcut would be wrong.

---

## Sources

- Repo: `src/pyscriptc/lower/_classes.py` (`_class_decorator:540`,
  `_is_dataclass_deco:547`, `_erased_base_name:656`, `_ERASED_BASES:57`,
  `_ERASED_DECORATORS:58`, `_field_options:580`, `_collect_class:174`),
  `src/pyscriptc/lower/_expr.py:235` (the typing-name-as-value fence),
  `src/pyscriptc/lower/_stdlib.py:1188` (erased member resolution),
  `src/pyscriptc/lower/_metadata.py` (this session's metadata tables),
  `src/pyscriptc/check.py` (`_run_pyrefly:331`, the error-oracle contract),
  `src/pyscriptc/factsbridge.py:365` (`PyreflyFactsAdapter`: expression and def facts,
  not class shape), `src/pyscriptc/imports.py:85` (the erased typing modules),
  `src/pyscriptc/_typeshed/stdlib/typing.pyi:1035` (the vendored standard).
- Companion notes: `.agents/notes/2026-11-02-runtime-reflection.md` (the roadmap, its
  tier (c) metadata tables and its item 5 on identity decorators),
  `.agents/notes/2026-11-02-ecosystem-reflection.md` (the compatibility ranking),
  `.agents/notes/2026-11-02-class-base-cascade.md` (the cascade measurement, on the
  `work/ps1204` branch).
- Measured with pyrefly 1.4.0 (`pyrefly check --output-format json`) and
  `pyscriptc coverage` / `pyscriptc compile` on CPython 3.14.9. The probe programs
  are in `/home/own-agents/pep681/` (not committed).
