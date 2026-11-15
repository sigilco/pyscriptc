# The tagged dynamic value (issue #1226)

Status: design note, 2026-11-15. Not a decision. The decision row proposed at the end of this note is what turns it into one.

## The gap

`object` and `Any` are in the dynamic annotation set (`src/pyscriptc/lower/_annotations.py:50`), so a container of heterogeneous values has no native spelling:

```python
def handle(msg: dict[str, object]) -> int:  # PS1104
    return 1
```

`dict[K, Any]` is the one exception, and it is not a dynamic value: it is an *unresolved* slot (`_UNRESOLVED_KIND = "any"`, `src/pyscriptc/lower/_core.py:576`). The container's shape is known, the element type is not, and every read has to prove its type from a local source or fences with `PS1107` (`src/pyscriptc/lower/_expr.py:737`). `object` keeps the fence because it names a *shape* ("any value at all"), not an unresolved type, and the IR has no slot for a shape.

That leaves no spelling for a JSON-RPC message, a JSON:API response or a config blob: a payload whose values are a mix of `int`, `str`, `bool`, `None`, `list` and `dict` has no type, so it has no parameter type, so it cannot be passed, stored or returned.

This note designs the missing value: a tagged dynamic value, the sum of the lowered shapes, that `object` and `Any` erase to.

## The sum

The sum is exactly the set of shapes the compiler already lowers, so the value never needs a shape the IR cannot express:

| arm | payload | note |
| --- | --- | --- |
| `None` | none | the null arm |
| `bool` | 1 byte | CPython's `bool` is a subclass of `int`; the tag decides, so `isinstance(True, int)` is true but the value keeps its own arm |
| `int` | 8 bytes | `i64`, the compiler's int width |
| `float` | 8 bytes | `double` |
| `str` | `PsBuf` (16 bytes) | the `%ps.str` / `%ps.buf` prefix the runtime already moves |
| `list` | `ptr` | a `PsSeq` of `dyn` cells |
| `dict` | `ptr` | a `PsMap` keyed by `str` with `dyn` values |

`bytes` is deliberately **not** an arm: JSON has no bytes, and `bytes` in the compiler is a str-shaped buffer with different semantics (`ps_rt.h` `PsBuf` vs the `%ps.str` code-point count). Adding it later is a new tag, not a redesign.

Two arms are deliberately excluded: `tuple` (the compiler lowers a fixed-arity tuple as a synthetic class, so a tuple arm would be a second, conflicting spelling of a class value) and user classes (a class value carries a vtable the dynamic cell has no room for; a class arm would need the class pointer plus its own dispatch).

The name for the sum is `dyn` in the IR. It is not `any`: `any` already means "unresolved slot, the read site must prove the type", and the two must stay distinct, because `any` is a compile-time obligation and `dyn` is a runtime fact.

## How `object` and `Any` erase to it

The erasure has two distinct cases, and the difference is the whole design.

**`object` in a value slot** erases to `dyn`. `object` names a shape, and `dyn` is that shape's only representation. `dict[str, object]`, `list[object]` and a bare `object` parameter all become `dict[str, dyn]`, `list[dyn]` and `dyn`.

**`Any` in a value slot** keeps its current meaning (`any`, the unresolved slot). `Any` is a claim about what the *caller* will prove, and the `PS1107` read fence is that claim's enforcement. Changing `Any` to mean `dyn` would silently remove a fence the compiler has earned over a thousand probes: `d[k]` on a `dict[str, Any]` would stop requiring a proof.

So `object` becomes the spelling of a dynamic value and `Any` stays the spelling of an unresolved slot. That is a deliberate divergence from CPython, where the two are aliases, and it has to be recorded as one: **divergence: `object` and `Any` are not interchangeable in pyscriptc**. The alternative (make `Any` mean `dyn` too) is rejected alternative A below.

A `dyn` value is *readable* without a proof: a `dyn` read is a runtime tag, not a compile-time obligation. `d["k"]` on a `dict[str, dyn]` yields a `dyn`. What a `dyn` read may not do is be used as a `str` or an `int` without narrowing: `len(d["k"])`, `d["k"] + "x"` or `print(d["k"])` all need the arm. Narrowing is the next section.

## The runtime type test and the narrowing operation

Narrowing is `isinstance`, and it must become a *runtime* test on a `dyn` operand, where today it is a compile-time constant fold (`src/pyscriptc/restlower/_builtins.py:29` folds `isinstance` to a literal `True`/`False` through `_isinstance_holds`).

The lowering rule:

- `isinstance(v, int)` where `v: dyn` lowers to a runtime tag compare against the `int` arm.
- `isinstance(v, bool)` where `v: dyn` compares against the `bool` arm. Because CPython's `bool` is a subclass of `int`, `isinstance(v, int)` on a `dyn` holding `True` must be **true**. The runtime test therefore is `tag == INT || tag == BOOL` for the `int` arm, `tag == FLOAT` for `float`, `tag == STR` for `str`, `tag == LIST` for `list`, `tag == DICT` for `dict`, `tag == NONE` for `NoneType`.
- `isinstance(v, X)` where `X` is a user class keeps fencing with `PS2001`: no class arm exists, so no class can be an arm of the sum.

After the test, the `dyn` narrows to the arm's type inside the `if` body. This is the one new operation in the lowering, and it is a *narrowing*, not a coercion: inside `if isinstance(v, int):` the value `v` has type `int` and the emitted code loads the `i64` payload. Outside the body it is still a `dyn`.

The narrowing has to survive control flow: `if isinstance(v, int): n = v` and `if not isinstance(v, int): return 0` then `v` is `int` after the early return. The compiler already has this machinery for the optional tag (`is None` / `is not None` narrowing, `python-style.md` rule 5), so the `dyn` narrowing reuses the same shape: the narrowing binds a *refined local* in the branch, and the join of a narrowed and an unnarrowed path is a `dyn` again.

The runtime type test is one runtime helper (section "Runtime" below): `int ps_dyn_is(const PsDyn *v, int32_t kind)`.

`isinstance` on a `dyn` is the only narrowing the first slice needs. `match` statements, `type(v) is int` and `v.__class__` stay fenced: `type()` on a `dyn` would need a type object per arm, which the sum does not carry.

## Interaction with `json.loads` and `json.dumps`

This is the reason the sum exists, and it is where the design either pays off or does not.

**`json.loads`** today requires the result annotation and checks it against a closed shape list (`_json_type`, `src/pyscriptc/lower/_calls_more.py:257`): scalars, optionals, `list[T]`, `dict[str, T]`. The runtime walks a `PsType` descriptor table (`ps_json.c`, `ps_json_loads(int64_t n, char *p, const PsType *types, int32_t root, void *out)`) and parses into a statically shaped tree. A heterogeneous payload has no such shape, so `json.loads` cannot be annotated today.

With `dyn`, `json.loads(text)` annotated as `dyn` (or `dict[str, dyn]`) becomes possible: the parser walks the JSON text and builds a `dyn` tree directly. The runtime needs a second entry point, `ps_json_loads_dyn`, that does not take a `PsType` table but produces the tagged cells. This is the smallest and highest-value slice of the whole design, because it is what a JSON-RPC handler needs first.

The type check the parser performs today ("JSON value does not match the annotation", `ps_json.c:186`) becomes, on the `dyn` path, a *tag assignment* instead of a check. The `dyn` parser accepts every JSON value: `null` -> `None` arm, `true`/`false` -> `bool` arm, integers -> `int` arm, reals -> `float` arm, strings -> `str` arm, arrays -> `list` arm, objects -> `dict` arm.

The number arms need a rule CPython does not need: CPython's `json` returns `int` for an integer literal and `float` for a real one, and `parse_float`/`parse_int` are hooks. The `dyn` parser follows CPython: an integral literal with no fraction or exponent is an `int` arm, anything else a `float` arm. A JSON integer too large for `i64` is a `float` arm in CPython (it becomes an arbitrary-precision int, which the compiler has no representation for); the `dyn` parser must round it to a `double` and that is a recorded divergence.

**`json.dumps`** today walks the same `PsType` table in reverse. On the `dyn` path it walks the tagged tree and writes each arm by its tag: `None` -> `null`, `bool` -> `true`/`false`, `int` -> the decimal digits, `float` -> the `repr` form CPython's `json` uses (`float.__repr__`, including the `Infinity`/`NaN` literals exactly like the existing `ps_dump_float` writes them, `ps_json.c:422`), `str` -> the escaped string, `list` -> the array, `dict` -> the object with the key order the insertion order gives.

The round-trip property the issue asks for is therefore: `json.loads(text)` into a `dyn`, then `json.dumps` of that `dyn`, gives back the same text for every JSON text whose numbers are representable in `i64`/`double` and whose strings are ASCII (the current parser refuses non-ASCII strings with "non-ASCII JSON string is not lowered", `ps_json.c:100`). That last restriction is a pre-existing property of the JSON pack, not of `dyn`.

**The `dict[str, dyn]` case** is a hybrid worth naming: the outer shape is known (a `str`-keyed map) and the values are `dyn`. The parser can then build a `PsMap` with `dyn`-valued slots directly, and `dumps` writes a map whose values are `dyn` cells. The `PsType` table needs one new kind for it (`PS_JSON_DYN`), and the map's `val_size` becomes the `dyn` cell size.

## The IR representation

The IR is a typed record tree (`src/pyscriptc/_irnode.py`) and a dict form (`src/pyscriptc/ir.py`). A new type kind appears in both.

**The type record.** A new kind `dyn` in `ir.TYPE_KINDS` (`src/pyscriptc/ir.py:24`), spelled `{"kind": "dyn"}` in the dict form and `T(kind="dyn", ...)` in the typed form. It carries no shape fields: the sum is fixed, so there is nothing to parameterize. `type_key` spells it `dyn` (`src/pyscriptc/ir.py`, the `kind == "any"` branch's neighbour), and `types_equal` compares it like any other leaf.

**The name collides with `FORBIDDEN_KINDS`, and that is a decision.** `ir.FORBIDDEN_KINDS` (`src/pyscriptc/ir.py:65`) is the set of scriptc's JavaScript-shaped kinds that the validator rejects so a reused scriptc node fails closed instead of being misread. It already contains `"dyn"` (scriptc's `dyn` is a JavaScript dynamic type). `validate_type` calls `_reject_forbidden` before the `TYPE_KINDS` check, so a `{"kind": "dyn"}` type record would be **rejected by the validator** until `dyn` leaves `FORBIDDEN_KINDS`.

Two ways out, and the choice belongs in the decision row:

- **Rename the kind** (`dynval`, `dyncell`, `anyval`): `FORBIDDEN_KINDS` keeps its scriptc guard whole, and the collision never existed. The cost is a name that reads less well than `dyn` in the IR dumps and in the type facts.
- **Keep the name and drop `dyn` from `FORBIDDEN_KINDS`**: the kind reads better everywhere, and the scriptc guard loses exactly one member. The risk is the one the set exists to prevent: a scriptc node whose type kind is `dyn` would now pass the validator as if it were ours. The set is a deny-list of names, and the two meanings are different types, so the reuse would be a misread.

The note's recommendation is the **rename**: the deny-list is a safety rail and this design should not weaken it. The name in the rest of this note is `dyn` for readability; the IR kind should be `dynval` (or whatever the decision row picks), and the note uses the chosen name in the code snippets once it is fixed.

**The value representation.** A `dyn` value is a pointer to a heap cell:

```c
typedef struct PsDyn {
    int32_t tag;   /* PS_DYN_NONE, PS_DYN_BOOL, PS_DYN_INT, PS_DYN_FLOAT,
                       PS_DYN_STR, PS_DYN_LIST, PS_DYN_DICT */
    int32_t pad;   /* keeps the payload 8-aligned */
    int64_t i;     /* the int, bool and float payloads (the float bits) */
    PsBuf b;       /* the str payload (16 bytes) */
    void *p;       /* the list and dict payloads */
} PsDyn;
```

The cell is 48 bytes (`i64` + `PsBuf` + `ptr` + two `i32`s plus padding). A cheaper layout is possible (a union of the payloads would make it 24 bytes) but the wide cell keeps the load and store paths branch-free: the emitter writes the arm's payload at a fixed offset and never has to switch on the tag to find it. The 24-byte union layout is the alternative considered and rejected (alternative B).

The cell is a **heap** cell, not an SSA value: a `dyn` is a pointer at the ABI, exactly like a `list` or a `dict` (`_llvm_type`, `src/pyscriptc/emit/_ir.py:861`, maps `list`/`dict`/`class`/`exc`/`func` to `ptr`). So `dyn` adds nothing to the LLVM type system.

**The narrowings.** A narrowed `dyn` is not a different runtime value; it is the *same* cell read through a different projection. Inside `if isinstance(v, int):` the value `v` is an `i64` and the emitter loads `i` from the cell at offset 8 (the tag check already proved the arm). This is the same trick the optional tag plays today (`is None` narrows to the element type, and the tag cell carries the value).

**The MIR.** The MIR is the lower, QBE-shaped IR (`src/pyscriptc/mir.py`) with its own type tag space: `MIR_W` (int), `MIR_D` (float), `MIR_B` (bool), `MIR_VOID`, `MIR_S` (str aggregate), `MIR_P` (shared heap pointer for list and dict). A `dyn` is a heap pointer, so it fits `MIR_P` exactly: `MIR_P` is already "the shared heap-pointer tag: list and dict values are `ptr` at the ABI. HIR keeps the list-vs-dict and element-type semantics; MIR carries only the pointer shape" (`mir.py:57`). The HIR keeps the `dyn` semantics; the MIR carries the pointer shape. **No new MIR op and no new MIR tag is needed for the value itself.**

What the MIR *does* need is the tag test. The tag compare is a load of the cell's tag word and a compare against a constant: in MIR that is a `loadw` + `ceqw`, which the existing ops cover (`MIR_CEQW`, `MIR_CNEW`). The type-test op therefore needs no new MIR op either, as long as the *arm* is a compile-time constant at the lowering (which it is: `isinstance(v, int)` names the arm).

So the MIR change for slice 1 is: **none**. The MIR carries a `MIR_P` pointer, and the tag test is an ordinary load-and-compare on the cell. That is the single most important fact for the slicing, because the MIR op-id blocks are the most expensive thing to touch (new op families take fresh id blocks above the highest id, `.agents/docs/parallel-pr-rules.md`).

Where the MIR *will* need work is later: a `dyn` that is *constructed* (a literal `None`/`int`/`str` stored into a `dyn` slot) needs a "make a `dyn` cell" operation. That is a new MIR op family (`MIR_DYN_NEW`, plus `MIR_DYN_GET`/`MIR_DYN_PUT` for the arm projections) and it is a later slice, not slice 1.

**The lowering sites.**

| site | change |
| --- | --- |
| `lower/_annotations.py:229` | `object` no longer fences; it returns `{"kind": "dyn"}` |
| `lower/_annotations.py:311` | `_any_annotation` keeps `Any` -> `any` and keeps the fence for the other dynamic names |
| `lower/_annotations.py:349` | `_fail_annotation` no longer reaches `object` (the name resolves earlier) |
| `restlower/_builtins.py:29` | `isinstance` on a `dyn` operand lowers to a runtime tag compare instead of a constant fold |
| `lower/_calls_more.py:257` | `_json_type` accepts `dyn` (and `dict[str, dyn]`, `list[dyn]`) |
| `emit/llvm/_stdlib.py:269` | `_json_global` gets a `dyn` kind in the `PsType` table, and a `dyn` parser entry |
| `emit/_ir.py:861` | `_llvm_type` maps `dyn` to `ptr` |
| `ir.py:24` | `TYPE_KINDS` gains `dyn` |

## The TIR canonicity and hash impact

This section is the frozen-gate part, and it is the reason the slicing starts where it starts.

**The gate.** `benchmarks/compat2/probes/TIR_HASHES.json` is the one canonicity gate (`.agents/docs/spec/ir-canonicity.md` section 7, AGENTS.md "Frozen v2 gates"). It carries one SHA-256 per corpus probe (13,692 probes today). Changing `tirhash.py`'s rules, or changing the IR a probe lowers to, moves digests. Regenerating the file requires the A/B byte-identical ritual (two `tirscore` runs, `cmp`), on Linux only.

**What the typed serializer does with a new type kind.** `tirhash._write_type` (`src/pyscriptc/tirhash.py:742`) writes the kind and then the fields that kind carries. For `class`, `list`, `buffer`, `dict`, `func` and `typevar` it writes the shape fields; for every other kind it writes only `{"kind": "<kind>"}`. A `dyn` kind would therefore serialize as `{"kind":"dyn"}` with **no change to `tirhash.py`**: the serializer's kind whitelist is not a closed set, it is a set of "kinds that carry extra fields", and a kind that carries none falls through to the bare-kind write.

**So the serializer needs no decision.** The canonicity *rules* (the incidental-key strip, the class order, the handler `codes` order, the fence total order, the path scrub) are untouched by a new leaf kind: none of them is about type kinds.

**What does move: the digests.** Any corpus probe whose IR changes gets a new digest. `builtins.object` is such a probe today:

```python
def probe() -> int:
    r: object = object()
    return 1
```

Its source is in `benchmarks/compat2/probes/builtins.jsonl` (a **frozen** file: `probes/*.jsonl` is on the frozen list). Today it fences with `PS1104` (the `object` annotation), so the fence record is part of its IR and its digest (`31a6a066...` in the current baseline). If `object` erases to `dyn`, the annotation stops fencing but the `object()` *call* still fences with `PS2002` ("call to object is not lowered", the generic row at `lower/_calls.py:328`) - so the probe's fence set changes shape (one fence becomes another), and a digest that is computed over a module with fences changes with it. `TIR_HASHES.json` has to be regenerated.

That regeneration is not blocked on a decision about `tirhash.py` (the serializer does not change), but it is gated by the ritual: the A/B byte-identical run, on Linux, through the `tir-hashes.yml` workflow, because the baseline is host-sensitive ("canonical IR folds host constants and the macOS baseline differs", `.agents/docs/spec/ir-canonicity.md` section 7.4). The hint in the task ("if the tir hashes compare job fails, dispatch the tir-hashes.yml workflow on your branch and copy its tir-hashes artifact") is exactly this path.

**The frozen probe file itself is not edited.** `builtins.jsonl` stays byte-identical: the probe is a *CPython* probe, and CPython accepts `r: object = object()`. The probe's *pyscriptc* outcome changes (it stops being a fence), and the digest follows. `probes/*.jsonl` is frozen, and the digest file is regenerated, never hand-edited.

**The cache.** `benchmarks/compat2/cache.py` is frozen and its key includes the compiler-file hash, so a compiler change invalidates the cache by itself; no cache change is needed.

**The coverage map.** `benchmarks/compat2/coverage_map.py` is frozen and maps a probe to the compiler files that built it, with the runtime files (`RUNTIME.glob("*")`) in the key. A new runtime file `src/pyscriptc/runtime/ps_dyn.c` is picked up automatically by `runtime_files()` (it globs the directory), so the map does not need an edit, but the map's *contents* are a generated artifact: the coverage map is rebuilt by its own tooling, and it is not part of the frozen-file list's edit rules (the frozen list names the file, so a rebuild is the mechanism, not a hand edit). This has to be checked at slice 1: if the map's stored content is a hash of the file list, a new runtime file changes it and the map must be regenerated through its script, not by hand.

**The `tir_schema` version.** `TIR_HASHES.json` carries `"tir_schema": "tir/1"`. A new type kind is a schema change in the strict sense ("a serializer semantic change bumps it and forces regeneration", section 7.3). Adding a kind is not a *serializer* semantic change (the serializer's rules do not change), so `tir/1` stays; the digests move because the IR moved, not because the schema moved. If the owner prefers to treat a new type kind as a schema change, `tir_schema` becomes `tir/2` and every digest is regenerated anyway. Either way the work is the same; the choice is a decision-row item.

## Runtime: memory and ownership

**The runtime file.** A new `src/pyscriptc/runtime/ps_dyn.c` with the cell helpers, declared in `src/pyscriptc/runtime/ps_rt.h` (the `PsDyn` struct, the tag enum and the helpers). The runtime pack is built by `scripts/build_runtime_pack.py`, which globs the runtime directory, so the new file joins the pack with no build-script change.

**The helpers.**

```c
/* the tag test: 1 when the cell holds the named arm */
int ps_dyn_is(const PsDyn *v, int32_t kind);
/* the arm projections: the payload of a proven arm */
int64_t ps_dyn_int(const PsDyn *v);
double ps_dyn_float(const PsDyn *v);
int64_t ps_dyn_bool(const PsDyn *v);
PsBuf ps_dyn_str(const PsDyn *v);
void *ps_dyn_list(const PsDyn *v);
void *ps_dyn_dict(const PsDyn *v);
/* the constructors */
PsDyn *ps_dyn_new_none(void);
PsDyn *ps_dyn_new_bool(int64_t b);
PsDyn *ps_dyn_new_int(int64_t i);
PsDyn *ps_dyn_new_float(double f);
PsDyn *ps_dyn_new_str(PsBuf b);
```

**Ownership.** The runtime has **no refcounts and no GC**: "The runtime has no refcounts and no GC and the emitted IR has no free op, so every sequence and map a program builds lives until the process dies" (`src/pyscriptc/runtime/ps_rt.c:744`). Every heap object is anchored in a global `ps_owned` registry so LeakSanitizer stays silent at exit.

A `dyn` cell is therefore an ordinary heap object with the same lifetime rule: it is allocated with `calloc` and **anchored in `ps_owned`** (`ps_owned_push`, `ps_rt.c:824`), never freed. The cell's `str` payload is a `PsBuf` whose data pointer is an existing string buffer (owned by whoever made it, under the same rule), and whose `list`/`dict` payloads are `PsSeq`/`PsMap` pointers that are themselves anchored in `ps_owned` by their own constructors.

The consequence is the good one: **no ownership propagation is needed**. A `dyn` cell that holds a `list` arm does not have to keep the list alive, because the list is alive until exit anyway. There is no destructor, no refcount, no cycle problem. The cost is the one the runtime already pays: a program that builds a million `dyn` cells in a loop leaks a million cells, and the leak is the same class as the existing list/dict leak. Slice 1 does not make this worse in kind; it makes it visible in a new place.

**The cell and the `PS_T` table.** `json.dumps`/`json.loads` walk a `PsType` table (`{ i32 kind, i32 child }`) that the emitter builds per annotated type (`_json_global`, `src/pyscriptc/emit/llvm/_stdlib.py:269`). A `dyn` needs one new kind code, `PS_JSON_DYN = 8` (the current codes are `ps_rt.h:128-134`: 1 int, 2 bool, 3 str, 4 list, 5 dict, 6 float, 7 optional), and the `PsType` table gets a `dyn` entry whose `child` is unused.

**The `dict[str, dyn]` map.** `ps_map_new(key_kind, key_size, val_size, cap)` takes the value size, so a `dyn`-valued map has `val_size = sizeof(PsDyn)` (48). The map's cell hash and equality functions (`ps_seq_cell_hash` / `ps_seq_cell_eq`, `ps_rt.c:887`) are kind-driven; a `dyn` cell's hash and equality would have to compare by tag and payload, which is only needed if a `dyn` is used as a *key*. A `dyn` key is not in the first slice (the JSON object model keys by `str`), so the map keys stay `str`.

**The `str` payload.** A `str` arm carries a `PsBuf` (16 bytes: `len` + `data`). The compiler's `str` value at the ABI is the `%ps.str` aggregate `{ i64, ptr, i64, i32 }` (byte length, data pointer, code-point count, flags), and the memory type is the 16-byte `%ps.buf` prefix (`_memory_type`, `src/pyscriptc/emit/_ir.py:883`). The `dyn` cell's `str` arm uses the same 16-byte `PsBuf` prefix, so the copy is the same "store the prefix, refill the count on load" dance the existing `any` cell plays (`_widen_cell`, `src/pyscriptc/emit/llvm/_core.py:250`).

## The rejected alternatives

**Alternative A: make `Any` mean `dyn` too, and drop the `PS1107` read fence.**

`Any` would erase to `dyn` exactly like `object`, and the unresolved-slot machinery (`_UNRESOLVED_KIND`, `_resolve_any_read`, the `PS1107` fence) would be deleted.

Rejected because it removes a fence that has earned its place. The `PS1107` fence is what stops a program from reading a `dict[str, Any]` element as an `int` when the program has proved nothing: the `any` slot is a *promise the caller makes* and the compiler enforces. With `Any` meaning `dyn`, `d[k]` on a `dict[str, Any]` would yield a `dyn`, and `d[k] + 1` would need a narrowing that the program never wrote. Every existing program that relies on the fence (there are hundreds in the corpus) would change behaviour: values that used to be statically typed at the read site would become `dyn` values needing runtime tags, and the emitted code would get a tag check on every read. It would also make `dict[K, Any]` and `dict[K, object]` the same type, losing the distinction between "the caller promised" and "the value is dynamic". The cost of keeping the two apart is one extra type kind and a documented divergence; the cost of merging them is a silent semantic change across the corpus.

**Alternative B: a union-typed cell (a 24-byte cell with a union of the payloads, plus a per-arm switch on every access).**

The cell becomes `{ i32 tag, int32 pad, union { int64_t i; double f; PsBuf b; void *p; } }` (24 bytes), and every access switches on the tag to find the payload.

Rejected because it makes every access a branch. The compiler's value model is branch-free wherever it can be: the optional tag cell stores the value at a fixed offset and the tag is only checked at the narrowing point. A union cell would push a switch into every load, every store, every argument pass, and every map cell copy, and the switch would have to be duplicated in the LLVM emitter, the MIR emitter and the runtime. The 48-byte cell costs 24 bytes per dynamic value and buys a fixed-offset load for every arm. For a JSON message tree the values are short-lived and few, so the memory cost is noise next to the branch cost.

**Alternative C (considered, not rejected): a `dyn` that is a `PyObject*`-like boxed universal.** A single universal heap object with a type pointer and a payload, à la CPython's `PyObject`. Rejected for the same reason as B, amplified: it needs a type object per arm, a `tp_dealloc` slot, and a whole object model. The runtime has no GC and no refcounting, so a universal object needs its own lifetime story, which is a much larger change than the sum of the shapes needs. It is listed here because it is the shape a reader will expect, and it is worth recording that it was considered and why it lost.

## The sliced implementation plan

Each slice is one PR. The slices are ordered so that the ones with no frozen-file consequence come first, and the frozen-gate work is isolated in its own PR where it can be reviewed on its own.

### Slice 1: the `dyn` type kind, the `object` erasure and the runtime cell (no frozen-file change)

**No frozen file is touched.** This is the claim to check before starting, and it is the claim this note makes: `tirhash.py` needs no change (section "TIR canonicity"), the probe files `probes/*.jsonl` need no change (the corpus probe `builtins.object` keeps its source), and the only frozen consequence is the *digest* of that probe, which moves because the IR moved.

Wait. That is the thing to be careful about: the digest of `builtins.object` moving means `TIR_HASHES.json` (frozen) is regenerated in this PR, and the regeneration is the A/B ritual on Linux. Is that a "frozen-file change"? It is a frozen file's *content* changing, which the gate's own rules allow only through the ritual. The honest framing: **slice 1 needs a recorded decision on the `tir_schema` bump question** (does a new type kind bump `tir/1` to `tir/2`?), and it needs the digest regeneration ritual, but it needs **no change to any frozen file's rules** (`tirhash.py`'s canonicity rules are untouched, the probe files are untouched).

The slice as scoped by the task is the smallest provable spike: the runtime tagged value plus the `dict[str, object]` acceptance for a parameter that is only passed through or `.get`/`in` tested. That is:

1. `ir.py`: `TYPE_KINDS` gains `dyn`; `type_key` spells it.
2. `_irnode.py`: the typed form carries the kind (no new field: `dyn` has no shape).
3. `lower/_annotations.py`: `object` resolves to `{"kind": "dyn"}` instead of fencing.
4. `emit/_ir.py`: `_llvm_type` maps `dyn` to `ptr`; `_elem_size` gives the cell size.
5. `emit/llvm/`: the `dyn` value is a pointer to a cell; a `dyn` parameter passes and returns as a pointer.
6. `runtime/ps_rt.h` + `runtime/ps_dyn.c`: the `PsDyn` cell, the tag enum, `ps_dyn_is`, the constructors, the `ps_owned` anchoring.
7. `restlower/_builtins.py`: `isinstance` on a `dyn` lowers to the runtime tag compare (`ps_dyn_is`), and the narrowing refines the local in the branch.
8. `lower/_calls_more.py` + `emit/llvm/_stdlib.py`: `json.loads`/`json.dumps` accept a `dyn` annotation (the `PS_JSON_DYN` kind in the `PsType` table).
9. A differential probe under `probes/differential/` that runs the new construct against the pinned CPython and byte-compares stdout, stderr and the exit code (the AGENTS.md differential probe rule).
10. The digest regeneration for the moved probe (`builtins.object`), through the ritual, on Linux.

**The narrowing is the risky part of slice 1, and slice 1 shipped without it.** The type test alone is easy; making the narrowed value *usable* as an `int` inside the branch means the lowering has to produce a refined local, and the join of the refined and unnarrowed paths has to produce a `dyn` again. The implementation confirms the note's reading: the type test landed as a runtime tag compare and works (`isinstance(v, int)` on a dynamic value is true or false at run time, and the `int` arm matches a `bool` cell because CPython's bool is a subclass of int), but the *use* of the value inside the branch fences with `PS2004` ("comparisons need two values of the same scalar type" and "`+` on int and dynval is not lowered"). So slice 1 ships the value and the test, and the narrowing lands in slice 2.

What slice 1 does land, measured against CPython 3.14 by the differential probe:

- `dict[str, object]` compiles; the display boxes each value into the cell.
- The `in` test, the 1-arg `.get` and the 2-arg `.get` work. The missing-key arm of the 1-arg `get` is the None arm of the sum, so the read's type is the sum, not `dynval | None`.
- The `isinstance` tag test works, including the bool-is-an-int rule.
- The pass-through works.

What the implementation settled that the note left open:

- **The kind's name is `dynval`, not `dyn`**, for the `FORBIDDEN_KINDS` reason above. The decision row should say so.
- **A `dict[str, dynval]` display takes a `bool` value**, which the concrete `dict[str, bool]` slot does not. The bool is an arm of the sum, so the fence does not apply.
- **The census moves**: the compiler's own source annotates 21 places with `object`, so the erasure exposes the body fences those annotations were hiding. The pins are repinned (17 shards; `log` 17 -> 12, `emit._ir` 29 -> 31).
- **The compat table moves with it**: python-dotenv goes 42/75 -> 45/75.
- **Exactly one TIR digest moved** (`builtins.object`), and the header did not: `tir_schema` stays `tir/1` and the oracle stamp is the same. The note's prediction held.

**What slice 1 does not do:** it does not make `dyn` a *storable* value in a concrete container (`list[int]` does not take a `dyn`), it does not add `dyn` to the JSON codec's nested shapes (only the top level and the `dict[str, dyn]` case), and it does not add the MIR construction ops.

### Slice 2: the narrowing operation and the `dyn` in concrete containers

The refinement of a `dyn` to its arm inside `isinstance` branches, the ability to store a `dyn` in a `list[dyn]`/`dict[str, dyn]` (the MIR construction ops `MIR_DYN_NEW`, `MIR_DYN_GET`, `MIR_DYN_PUT` as a fresh id block above `MIR_RAISEMSG = 57`), and `dyn` values in user class fields.

### Slice 3: the JSON codec, full

`json.loads` into a `dyn` for nested shapes (a `dyn` inside a `list`, a `dyn` inside a `dict`), the `parse_int`/`parse_float` hook parity with CPython, the `i64` overflow rule, and the `dumps` of every arm including the float `repr` form.

### Slice 4: the surface and the docs

`docs/guide/typing.md`'s per-construct table (the `object` row), the divergence entry for `object` vs `Any`, the compat site's support data, and the `what_will_fail` gap ledger row for the constructs that stay fenced (`isinstance` on a user class, `type(v)`, `match`).

### Slice 5 (optional): `dict[str, object]` in the compat corpus

The corpus's `builtins.object` probe keeps its source; a new corpus probe for the `dyn` round-trip needs the frozen probe files, which is a recorded decision on its own (the probe files are frozen and only the owner adds probes to them).

## Proposed decision row

The row below is the text for the decisions log. It is proposed here, not written there: the decisions file is the owner's.

> **2026-11-15 | Tagged dynamic value (`dynval`) for `object` |** Issue #1226. A new IR type kind `dynval` is the sum of the lowered shapes (`None`, `bool`, `int`, `float`, `str`, `list`, `dict`), represented at the ABI as a pointer to a heap cell `{ i32 tag, i32 pad, i64 i, PsBuf b, ptr p }` owned until process exit like every other runtime heap object (the runtime has no refcounts and no GC; the anchoring is the new `ps_own` entry). The kind is spelled `dynval` and not `dyn` because `dyn` sits in `ir.FORBIDDEN_KINDS` (scriptc's JavaScript dynamic type) and the deny-list keeps failing a reused scriptc node closed; slice 1 picked the rename so the deny-list stays whole. `object` erases to `dynval` (a parameter and a container value slot); `Any` keeps its meaning (the unresolved slot with the `PS1107` read fence), so `object` and `Any` are not interchangeable and that is a recorded divergence from CPython. `isinstance` on a `dynval` is the runtime tag test (`ps_dyn_is`; the `int` arm also matches a `bool` cell, because CPython's bool is a subclass of int); the narrowing that lets the value be used as its arm inside the branch is slice 2. Slice 1 (PR #1256) lands the cell, the erasure, the `in`/`get`/pass-through reads and the type test, with the differential probe `probes/differential/dyn_dict_object.py` byte-identical against CPython 3.14. The typed serializer's canonicity rules (`pyscriptc/tirhash.py`) did not change: a kind that carries no shape fields falls through to the bare-kind write, so `dynval` serializes as `{"kind":"dynval"}`; the corpus probe `builtins.object` keeps its source and its outcome (the coarse `fence`), so `CALIBRATION.json` and `TRIAGE.md` do not move; its digest in `probes/TIR_HASHES.json` moved (one line) and the file was regenerated through the A/B byte-identical ritual on Linux (`tir-hashes.yml` on the branch), with the header unchanged: `tir_schema` stays `tir/1` because a new leaf kind is not a serializer semantic change. **Decisions taken by slice 1, for the owner to confirm or flip:** the `dynval` name; `tir_schema` stays `tir/1`; `bool` is its own arm and a `dict[str, dynval]` display takes a `bool` value where `dict[str, bool]` stays fenced. Design note: `.agents/docs/design/tagged-dynamic-value.md`.
