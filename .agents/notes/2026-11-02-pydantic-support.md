# Pydantic support: the seam, the corpus, the honest feasibility verdict (2026-11-02)

Owner direction (final): support pydantic's CORE and make it work for
user-defined models, aimed at people using pydantic through AI SDKs (openai,
anthropic and similar, whose request/response types are pydantic or
pydantic-shaped) and agent frameworks (langchain, langgraph). FastAPI/Flask
validation is nice to have, not prioritized. Architecture: compile the Python
seam (pydantic's Python layer and the user models) and interface with
pydantic-core through a PyO3-style boundary, instead of reimplementing the
core and instead of running the Python layer in an embedded interpreter where
avoidable. Target: about 80% on a measurable corpus, ideally out of the box or
needing only S to M changes.

Verdict up front, evidence below: **the "compile the Python seam, call
pydantic-core across a PyO3-style boundary, no interpreter" architecture is
not achievable as stated.** pydantic-core is a PyO3 crate: every one of its
~314 schema- and input-handling sites holds, reads, or constructs a `Py<PyAny>`
(`Bound<'_, PyAny>`), and its build entry point
(`build_validator`, `src/validators/mod.rs:527`) takes the schema as a
`&Bound<'_, PyDict>`. There is no schema-serializable IR inside the crate to
hand a dict over the FFI; the dict IS the contract, and building that dict
needs a CPython runtime. The least-CPython design is therefore: keep
pydantic-core as an island-bound library object (it works today — a `User`
model validates, dumps and prints through the island path after this session's
libpython fix), and add the compile-time seam in the *Python* layer's place:
the compiler derives the core-schema dict and the field struct from the
annotations it already checks, and the user's model methods lower natively
against the struct. That is the ecosystem note's option (B) plus the island as
the boundary — not a reimplementation of pydantic-core, and not a PyO3 bridge
into native code.

## 1. What pydantic v2's Python layer actually needs

Measured on pydantic 2.14.2, pydantic-core 2.50.2, CPython 3.14.9 (the
`/tmp/pydenv` venv, installed fresh this session). The `pydantic` distribution
is 105 Python files (the shipped v1 compat layer included); the load-bearing
v2 surface:

| file | lines | what it owns |
| --- | --- | --- |
| `main.py` | 1,947 | `BaseModel`: `__init_subclass__`, `model_*` API, `__repr__`, pickling |
| `_internal/_generate_schema.py` | 2,984 | the GenerateSchema pass: annotation -> core-schema dict |
| `_internal/_model_construction.py` | 833 | `ModelMetaclass`, class build, validator/serializer attach |
| `_internal/_fields.py` | 910 | FieldInfo collection, defaults, aliases |
| `_internal/_decorators.py` | 906 | validator decorator collection, signature introspection |
| `_internal/_typing_extra.py` | 502 | `get_type_hints`-family helpers, `eval_type_backport` |
| `_internal/_discriminated_union.py` | 510 | discriminator resolution over built schemas |
| `_internal/_generics.py` | 556 | parametrized generics (`Model[int]`) |
| `fields.py` | 2,065 | public `Field`/`FieldInfo` API |
| `json_schema.py` | 3,261 | JSON-schema generation (langchain tool schemas) |
| `type_adapter.py` | 780 | `TypeAdapter`: arbitrary-type validation |
| `root_model.py` | 189 | `RootModel[T]` |

The five constructs the native plane cannot offer today, all in the class
construction path (`_model_construction.py`), and all tier (e) in the runtime
reflection note: `ModelMetaclass.__new__` (a real metaclass, `ABCMeta`
derived), `__init_subclass__` driven build, `__annotations__` reads plus
`get_type_hints` for every field annotation, `__dict__` layout surgery
(`namespace[...] = ...` on the class dict under construction), and
`inspect.signature` over user validators (`_decorators.py:601`,
`_fields.py:878`). A sixth, `_generate_schema.py`'s exec of forward-ref
strings, is behind `eval_type_backport` and only fires for string
annotations. These are not accidental: the library's design converts
declarative class bodies into schema dicts at import time by walking live
namespace and annotation objects.

## 2. What pydantic-core exposes, and the shape of the seam

`pydantic_core._pydantic_core` (the 4.8 MB `.so`) exposes, from `lib.rs`:

- `SchemaValidator(schema_dict, config_dict=None)`: methods
  `validate_python`, `validate_json`, `validate_strings`,
  `validate_assignment`, `isinstance_python`, `get_default_value`
  (`src/validators/mod.rs:188-407`). Built by `build_validator`
  (`mod.rs:527`) which casts the schema to a `PyDict` and dispatches on
  `dict["type"]` across ~50 validator types (typed_dict, union, model,
  dataclass, str, int, list, literal, ...).
- `SchemaSerializer(schema_dict, config_dict=None)`: `to_python`, `to_json`
  (`src/serializers/mod.rs:63-195`).
- Errors: `ValidationError` (with `errors()`, `title()`, `state()`),
  `PydanticCustomError`, `PydanticKnownError`, `PydanticOmit`,
  `PydanticUseDefault`; `from_json`, `to_json`, `to_jsonable_python`; helpers
  `ArgsKwargs`, `Some`, `PydanticUndefined`, `TzInfo`, `PyUrl`,
  `PyMultiHostUrl`; `_schema_gather.gather_schemas_for_cleaning`.
- `core_schema.py` (171 KB, pure Python, shipped in the same wheel) is the
  schema-dict *constructor* the Python layer calls.

What crosses into Rust, concretely:

1. At build time: the schema dict — a plain `dict[str, object]` whose values
   are nested dicts, lists, strings, ints, floats, bools, None, tuples,
   and **Python callables** (the `function`/`with_default`/`custom_error`
   schema types carry the user's validator functions) and **Python type
   objects** (`is-instance`, `model`'s `cls`, enum classes).
2. At validate time: the input — any Python object (`validate_python`) or
   bytes/str (`validate_json`); the return is a new Python object (a
   `BaseModel` instance the Rust side constructs by calling the model class
   and setting `__dict__`/`__pydantic_fields_set__` attributes directly,
   `src/validators/model.rs:212-230`).
3. At dump time: a model instance (it reads `__dict__`,
   `__pydantic_fields_set__`, `__pydantic_extra__` back off the instance) or
   arbitrary values; the return a Python object or a JSON bytes/str.

The seam is therefore callable-shaped in both directions, not just
data-shaped: the schema dict embeds user validator callables and model class
objects, and validation returns instances of user classes. Any boundary that
is not a CPython runtime has to bridge PyObject identity in three places, not
one. That is the load-bearing fact for the architecture decision.

### Why a libpython-free PyO3 boundary is not possible

- The crate is `#[pymodule]` (`lib.rs:100`): the entry point is `PyInit`
  itself, resolved by CPython's import machinery, and the module init takes
  `Python<'_>` tokens. `pyo3` without the `auto-initialize` feature (this
  build has it only as a dev-dependency) refuses to start an interpreter; with
  it, it calls `Py_InitializeEx` — that is libpython.
- `nm -D` on the shipped `.so`: 200+ `U Py*` symbols (the full object model:
  `PyType`, `PyDict`, `PyUnicode_*`, `PyCapsule_Import`, ...). It does not
  link against `libpython` at build time (symbols resolve from the host
  process), but at run time the host process must *provide* them — i.e. an
  embedded or real CPython. There is no static subset: the schema dict it
  consumes is built from dicts, lists, and interned strings the CPython
  runtime allocates, and the validators it builds hold `Py<PyAny>` handles
  that only a CPython GC can trace (the crate ships `impl_py_gc_traverse!`).
- The one place a non-PyObject contract could exist — the validator tree
  itself — is built at runtime from the dict, and the crate's own
  `__reduce__` support (for cloudpickle) round-trips through the Python
  schema objects again (`py_schema: Py<PyAny>`, `mod.rs:124`), not through a
  serializable form. There is no serde model of the schema in the crate to
  copy.

So: "interfaces with pydantic-core through a PyO3-style boundary" in native
code means linking libpython and running the object bridge — i.e. what the
island already is. The honest framing: **the island IS the PyO3-style
boundary** for pydantic-core. The seam work is not bridging to Rust; it is
making the user's model layer (and the hot validation path) native while the
core stays island-side.

## 3. The least-CPython design that actually works

Layered, hot path first:

1. **Island boundary (works today, this session).** `pydantic` and
   `pydantic_core` route to the island lane under the per-import policy, the
   user's whole module (or its fenced definitions) seeds island-side, and the
   program crosses through `ps_island_fn_invoke`/`ps_island_def_handle` with
   the scalar/str/bytes/list marshalling the boundary already has. Baseline
   proof: `/tmp/pyd/t1.py` and `/tmp/pyd/t2.py` (BaseModel + model_dump)
   compile and run green under `--fallback=cpython --embed-python=host` after
   the libpython-resolution fix in this session (see section 6). Cost: every
   field access and construction crosses the marshalling seam, so per-request
   model workloads pay the bridge on every call.
2. **Compile-time schema + struct for user models (the new work, option B).**
   The compiler already checks every annotation and lays out user classes as
   C structs with field tables (`lower/emit/llvm/_core.py` field-index
   tables). The new piece: recognize `BaseModel`-shaped classes (a base the
   import policy binds to the island), derive from the annotations the
   compile-time core-schema dict the Rust side would build for the subset of
   annotation forms the typed plane holds (`str`, `int`, `float`, `bool`,
   `bytes`, `list[T]`, `dict[K,V]`, `Optional[T]`, `Literal`, enums, nested
   BaseModel), and attach it to the island-held model class so
   `model_validate`/`model_dump`/`model_validate_json` on that model runs
   with a real SchemaValidator. The struct-side fields mirror the dict; field
   access stays native; only construction/dump/validation crosses. The
   unsupported-annotation fence stays a fence: it names the island route
   instead of refusing the model outright.
3. **Hot-path native validators (later, only if measured).** For the subset
   in (2), `model_validate(dict)` lowers to a native loop over the struct
   fields calling the existing scalar coercions, and raises a
   `ValidationError`-shaped island error only on failure. This skips the
   seam for the common all-scalar payload. pydantic-core is then on the path
   only for the shapes the native loop does not hold (unions with validators,
   discriminated unions, JSON parse errors).

What this design does NOT do: run pydantic's Python layer natively (16,677
lines of the v2 surface need the five tier-(e) constructs; the ecosystem note
measured 309 reflection sites and rejected option (D) on that evidence), and
it does not put pydantic-core behind a hand-written C ABI (section 2: no such
contract exists in the crate).

## 4. What the AI SDK models actually use

Fresh installs (openai 3.42.1, anthropic 2.3.0, langchain-core 1.7.3,
langgraph 1.2.19) in `/tmp/pydenv`, scanned with AST over every `*.py`
(`/tmp/pyd/feature_scan.py`, counts are files-with-feature):

| feature | openai (types/) | anthropic (types/) | langchain_core |
| --- | --- | --- | --- |
| `class X(BaseModel)` | 967 | 1,127 | 13 |
| `Literal[...]` in annotations | 1,392 | 1,356 | 41 |
| `Annotated[...]` | 208 | 172 | 8 |
| bare `X \| Y` unions | 1 | 2 | 118 |
| `Generic[T]` | 2 | 3 | 8 |
| `Field(...)` in a body | 0 | 0 | 22 |
| `Field(default_factory=...)` | 0 | 0 | 16 |
| `Field(alias=...)` | 0 | 0 | 1 |
| `Field(discriminator=...)` | 0 | 0 | 1 |
| `model_config = ConfigDict(...)` | 0 | 0 | 18 |
| `@field_validator` / `@model_validator` | 0 | 0 | 1 / 14 |
| `@computed_field` | 0 | 0 | 0 |
| `RootModel` base | 0 | 0 | 0 |
| `TypeAdapter` | 4 (client layer) | 0 | 0 |
| `model_dump` / `model_validate` calls | 8 / 1 | 0 | 10 / 3 |
| `model_json_schema` calls | 3 | 0 | 7 |

Annotation vocabulary across openai + anthropic model class bodies
(`/tmp/pyd/annot_scan.py`, distinct forms: ~2,900, top roots): `str` 3,115
uses, `Optional[str]` 1,937, `int` 1,257, `Optional[int]` 614,
`Optional[bool]` 298, `bool` 212, `datetime` 158, `Optional[List[str]]` 115,
`Optional[float]` 106, `List[str]` 90, `Literal[...]` 1,177 uses,
`Optional[X]` (nested model) 695, `List[X]` 138, `Union[...]` 62, `Dict[K,V]`
10. Zero `Annotated` constraints on SDK fields' own declarations (the 208
`Annotated` uses are the SDK's own `PropertyInfo` metadata wrapper, a
`_utils.py` construct, not validation constraints). Zero `Field(...)`,
zero validators, zero `model_config` — the SDK models are the *plain*
subset: annotated fields, defaults (`= None`), `Optional`, nested models,
`Literal`, lists/dicts of the above.

The langchain shape is the opposite: few models, but every feature —
`ConfigDict`, default factories, model validators, generics, a discriminator.
`langchain_core.tools` builds tool schemas via `model_json_schema()` over
BaseModels; the JSON-schema side (`json_schema.py`, 3,261 lines) is pure
Python and would stay island regardless.

**Consequence for the 80% target:** the openai/anthropic models are the
measurable corpus, and they sit squarely in the annotation subset a
compile-time seam holds — no constraints, no validators, no config, no
generics in the model bodies (generics appear only as 2-3 `Generic[T]` page
containers, loadable island-side). The langchain share of the corpus is the
part that stays on the island path in full.

## 5. Baseline corpus (Step 2) and what it scores

40 snippets under `benchmarks/pydantic/corpus/` (new directory; no frozen
file touched). Each compiles with CPython as the oracle for stdout equality.
Scored pass/partial/fail with the failing PS code; harness
`benchmarks/pydantic/run.py` (stdlib only, no pytest dep). The corpus covers:
BaseModel fields/defaults/Optional/nested; validators; `model_dump` /
`model_validate` / `model_validate_json`; `Field` constraints; generics;
discriminated unions; `TypeAdapter`; `RootModel`; openai/anthropic-style
response models; langchain-style tool schemas via `model_json_schema`.
Two planes score per snippet: `island` (whole module island-routed) and
`native` (model class lowered natively where the compiler accepts it).

Baseline (recorded in `benchmarks/pydantic/BASELINE.json` on this box):
island plane 35/40 pass (87.5%), native plane 0/40 (every snippet fences
PS2002 on the `pydantic` import or PS2002/PS1204 on the class body). The gap
to "about 80% out of the box" is therefore not the island plane — it is the
default lane: out-of-the-box today refuses with PS2002 before any lane is
chosen, so "out of the box" for this target means the import policy defaults
pydantic to the island lane (an S-sized policy default) plus the class-body
acceptance for BaseModel-shaped annotated classes (the M-sized seam work).

## 6. What this session changed (interop fix)

- `emit/llvm/_driver.py`: the host-mode island link resolved `-L`/`-lpython`
  from `sysconfig.get_config_var("LIBDIR")`, which on a relocated
  interpreter (uv-managed toolchains, the compiler's own release binary)
  answers a build-machine prefix (`/install/lib` on this box) that does not
  exist. The link failed for every island compile from such an interpreter.
  The fix falls back to `sys.base_prefix/lib|lib64` when the configured
  libpython is absent. Without it, no island pydantic path was reachable on
  this machine at all — measured before writing anything else.

## 7. Owner decisions this note asks for

1. Accept the reframed architecture: pydantic-core stays island-side (that is
   the PyO3 boundary; a libpython-free seam is impossible, section 2), and
   the compiler work is the compile-time schema/struct derivation for
   BaseModel-shaped classes plus the island-default import policy. The
   alternative — a native reimplementation behind pydantic-core's API — is
   the ecosystem note's option (C), rejected there for the API-fork risk, and
   nothing in this session's evidence changes that.
2. Out-of-the-box meaning: make `[imports] pydantic = "island"` the default
   policy for `pydantic`/`pydantic_core` when the distributions are
   installed (an S-sized change in `stdlib_queue.py`/`config.py`), so the
   80% target is measured on the default lane, not on a config the user has
   to know to write.
3. Scope the corpus's "pass" for the AI-SDK models to the plain subset
   (section 4): fields, defaults, Optional, nested, Literal, dumps. Validators
   and `model_config` are the langchain slice and may score island-plane-only
   without failing the target.

## Sources

- pydantic 2.14.2 / pydantic-core 2.50.2 trees: `/tmp/pydenv` (venv, uv),
  `/tmp/pydantic_core-2.50.2` (sdist with Rust sources, fetched from PyPI).
- `lib.rs`, `src/validators/mod.rs`, `src/serializers/mod.rs`,
  `src/validators/model.rs`: seam inventory, section 2. `nm -D` on
  `_pydantic_core.cpython-314-x86_64-linux-gnu.so`: symbol evidence,
  `ldd`: no libpython at build time.
- AI SDK feature scan: `/tmp/pyd/feature_scan.py`, `/tmp/pyd/annot_scan.py`
  over openai/anthropic/langchain_core trees in the same venv; table in
  section 4.
- Island boundary behavior verified with `pyscriptc run --fallback=cpython
  --embed-python=host` on `/tmp/pyd/t1.py`, `/tmp/pyd/t2.py` (both green
  after the libpython fix; both fenced PS0001 before it).
- Prior notes this builds on: `.agents/notes/2026-11-02-runtime-reflection.md`
  (tiers, pydantic verdict), `.agents/notes/2026-11-02-ecosystem-reflection.md`
  (options A-D, download weights).
