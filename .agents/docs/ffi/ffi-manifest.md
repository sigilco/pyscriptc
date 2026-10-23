# Static FFI manifest design

Epic F design doc. `--ffi <manifest>` binds `.pyi`-style signature
declarations to C ABI symbols and links the C library into the single
binary. The plan line is `.agents/plans/2026-10-14-waves-after-scorer-v2.md:79`;
the seam and ownership model are `docs/http-seam-design.md`; the ABI rules it
must respect are `docs/abi-strategy.md`. The F0 gate (manifest parser and
validator, `src/pyscriptc/ffi_manifest.py`) ships as a draft PR ahead of the
v0.7.0 milestone; the lowering and linker work below it is not wired yet.

## 1. Manifest shape

**Pick: TOML, one file per adapter.** `requires-python = ">=3.14"`
(`pyproject.toml`) means `tomllib` is always in the stdlib, so parsing costs
nothing and adds no dependency — the "no new dependencies" rule holds.
Comments and trailing commas matter: a manifest is a hand-edited contract
reviewed like a header file, and JSON forbids comments. Every manifest is
authored (adapter owners write it by hand); nothing generates one, so the
writer ergonomics decide.

```toml
# pyscriptc-sqlite/ffi.toml — shape sketch, see §5 for the pilot
[manifest]
module = "pyscriptc_sqlite"   # importable module name this file claims
ps_abi = 1                    # REQUIRED: the epoch this manifest was written against
format = 1                    # manifest schema version; bumps independently of ps_abi
# libs = ["z"]                # system libraries: each entry becomes one -l flag
# lib_dirs = ["/opt/local/lib"]  # search dirs: each entry becomes one -L flag

[[source]]                    # C units / archives added to the link line
path = "vendor/sqlite3.c"     # relative to the manifest file
# objects = ["vendor/sqlite3.o"]   # pre-built archive members instead of .c

[[handle]]                    # opaque pointer type; no fields cross the boundary
name = "sqlite3"              # Python-side name in the .pyi surface
c_type = "sqlite3 *"
frees = "sqlite3_close_v2"    # ownership: which bound symbol releases it

[[const]]                     # enum constants inlined at lowering time
name = "SQLITE_OK"
type = "int"
value = 0

[[fn]]
name = "connect"              # Python-visible name on the module
py = "(path: str) -> sqlite3" # .pyi-shaped signature string
c_symbol = "sqlite3_open_v2"
abi = "ps_ffi_sqlite_open"    # thunk in the adapter's shim .c; see §3
```

Field rules:

- `[manifest].module` is the dotted name the import table binds; one manifest
  claims exactly one module. The compiler refuses two manifests that claim
  the same module.
- `ps_abi` is REQUIRED and is a single integer. This resolves the caveat
  `docs/abi-strategy.md` line 63-67 defers here: **per-manifest ABI pinning
  is confirmed** — each manifest pins the `PS_ABI` epoch it was written
  against, and the compiler refuses a manifest whose pin does not match the
  `PS_ABI` stamped by the running compiler. The manifest pin covers the
  `.ll` ↔ `ps_rt.h` contract the thunk relies on (`%ps.str`, `ps_alloc`,
  raise codes); it does not version the C library itself — that is the
  adapter's own release axis.
- `abi` names the *shim* symbol, not the C library's symbol. The adapter
  ships a thin `ps_ffi_*.c` thunk file that owns struct layouts, varargs
  (`sqlite3_open_v2` is variadic — the shim fixes a calling convention),
  out-params, and error-code mapping. Foreign symbols are never emitted into
  `.ll` directly; `c_symbol` documents the thunk's target for review and for
  a future direct-call fast path.
- `[[handle]]` is the only way a C pointer enters Python-land: an opaque
  token the subset treats like today's `Hash`/`Pattern`/`Deque` class kind —
  a `ptr` at the ABI, no field access, freed only by its `frees` symbol
  (compare `llvm.py:1072-1074`, where named classes already return `ptr`).
- `[[const]]` values are compile-time literals; `type` is one of `int`,
  `float`, `str`. Enum-style constants (`SQLITE_OK`, `Z_OK`) inline as
  `{"kind": "const"}` nodes and never reach the link.
- `libs` and `lib_dirs` are the system-library escape hatch, added by the F2
  gate (decision below). `libs` holds bare sonames (`z`, not `-lz` or
  `libz.so`); `lib_dirs` holds directories. Both are optional, and a manifest
  that declares neither contributes nothing to the link line.
- `[[source]]` is either `path` (one C source the target cc compiles) or
  `objects` (prebuilt archives and objects that go straight on the link
  line), never both. The compiler refuses a declared unit that is not on
  disk (`PS4009`) before the build starts.
- Ownership is per-arg, not per-call: a `[[fn]]` entry may carry
  `borrows = ["db"]` / `consumes = ["stmt"]` annotations listing which
  `[[handle]]` args the call borrows vs frees. Default is borrow; only
  `frees`-like calls (`close`, `finalize`) list `consumes`. There is no
  refcounting — this is the sqlite precedent's opaque-handle discipline,
  not ctypes.

## 1a. The schema the validator enforces

`src/pyscriptc/ffi_manifest.py` is the F0 gate: it parses a manifest and
refuses anything the lowering could not honour. Nothing lowers yet; the
module is the contract the later gates build on. The API is three calls:

```python
from pyscriptc.ffi_manifest import load_manifest, load_manifests, parse_manifest_text

manifest = load_manifest(Path("ffi.toml"))  # one file, raises FfiManifestError
manifests = load_manifests([Path("a.toml"), ...])  # several files, refuses duplicate module claims
parsed = parse_manifest_text(text)  # the same, from a string
```

`load_manifest` returns an `FfiManifest` with `module`, `ps_abi`, `format`,
`path`, `sources`, `handles`, `consts` and `functions`. Each `[[fn]]` becomes
a `ManifestFunction` with the parsed `params` (`ManifestParam`: name, type,
default), the `returns` type, the `c_symbol` documentation string and the
`abi` thunk name. Ownership annotations become `HandleRef` values that keep
the argument name and the handle type apart, so two arguments of one handle
type stay distinct.

The type vocabulary is the subset's, and the validator refuses anything
outside it: `int`, `float`, `bool`, `str`, `bytes`, `list`, `dict`, plus the
handles the manifest declares. `Any`, `object`, `Optional[T]`, `list[int]`
and every other annotation are rejected at parse time, because the emitter
never inspects a value at runtime and the marshalling has to be fixed at
lower time.

Every failure raises `FfiManifestError` (a `SpikeError` subclass, so the
CLI's existing handler catches it) with a stable code:

| code | meaning |
|---|---|
| `PS4001` | the manifest could not be read or is not valid TOML |
| `PS4002` | the `[manifest]` header is missing or malformed |
| `PS4003` | the `format` pin is missing or unsupported |
| `PS4004` | the `ps_abi` pin is missing or stale |
| `PS4005` | a key is unknown, missing or has the wrong type |
| `PS4006` | a signature or type is outside the vocabulary |
| `PS4007` | a name is declared twice, or two manifests claim one module |
| `PS4008` | a reference names something the manifest does not declare |
| `PS4009` | a declared source or object path does not exist on disk |

The codes are registered in `src/pyscriptc/diagnostics.py`, so
`pyscriptc explain PS4004` names the fix.

## 2. ABI discipline

Three pins, three axes, per `docs/abi-strategy.md` line 57-62:

| pin | axis | failure mode |
|---|---|---|
| `ps_abi` in `[manifest]` | `.ll`↔runtime struct/signature contract | link/compile refuse a stale adapter |
| `format` in `[manifest]` | manifest schema itself | parser refuses unknown versions loudly |
| adapter version (the package's own release tag) | C library + shim pair | none in the compiler; same-source builds only |

`PS_ABI` stays a third axis and never enters IR hashing
(`abi-strategy.md:57-62`). The manifest check is compile-time: `--ffi` inputs
are validated before lowering starts, so a mismatched epoch fails before any
`.ll` exists — same fail-closed posture as `assert_same_cpython`
(`abi-strategy.md:14-16`). `mix_crc32` remains the forever-frozen precedent
(`abi-strategy.md:63-67`); with per-manifest pinning confirmed here, it
graduates to the first manifest entry when FFI lands.

## 3. Lowering path

Today a foreign-looking call flows through three hand-rolled special cases:

1. `_module_call` (`src/pyscriptc/lower/_stdlib.py:298-332`) dispatches by
   module name: `nativeprobe` → `_nativeprobe_call`, `math` → `_math_call`,
   `REST_MODULES` → `rest_call`, then `json`/`struct`/`re.findall` inline
   cases, then `wave_function` → `_fixed_call`.
2. `_nativeprobe_call` (`_calls.py:179-199`) is the manifest's current
   stand-in: a hardwired symbol table mapping `crc32` → `ps_mix_crc32`,
   arg-type checks, then `_abi_call`.
3. `_abi_call` (`_calls.py:474-486`) attaches `"symbol"` to the
   `{"kind": "call", "callee": {"kind": "module", ...}}` IR node that
   `_mod_call` builds (`_stdlib_shared.py:73-82`).

**The manifest replaces steps 1-2 for adapter modules; step 3 is already the
target IR.** A manifest-loaded module gets a synthetic entry in the closed
import table (`QUEUE`/`LOWERED`, `stdlib_queue.py:15-60`) so `import
pyscriptc_sqlite` binds `Binding("module", ...)` exactly like a stdlib
module — one registration point, same as the http seam's plan
(`http-seam-design.md:77-81`). In `_module_call`, a new clause before the
`wave_function` fallback looks the call up in the manifest's `[[fn]]` table,
checks args against the `py` signature (same positional/min/defaults rules
`_fixed_call` applies today, `_calls.py:217-254`), and returns
`_abi_call(module, attr, args, ret, spec["abi"], span)`. The `.pyi` string is
parsed once at manifest load into the same `{"kind": ...}` type dicts the
lowerer already uses; the checker still sees a normal typed module via the
adapter's real `.pyi` file, so mypy and the lowerer read the same signature.

**IR node:** unchanged — `{"kind": "call", "callee": {"kind": "module"},
"symbol": "<abi thunk>"}`. `emit_module_call` (`llvm.py:712-713`) already
routes any module call carrying `"symbol"` to `_emit_abi` before the
per-module emit tables, so zero emitter dispatch changes are needed. The
only additions are type words: `handle` and `enum const` map onto the
existing `class`/`ptr` return path (`llvm.py:1072-1074`) and `const` nodes.

**Marshalling** is `_c_pieces` (`llvm.py:1033-1044`) exactly as today:
`int`→`i64`, `float`→`double`, `str`/`bytes`→`(i64 len, ptr)` pair,
`list`/`dict`/`class`/handle→`ptr`. Return mapping in `_emit_abi`
(`llvm.py:1046-1074`): `str`/`bytes` return through a `ptr` out-slot
(`_buf_out`, `llvm.py:999-1002`), `int`→`i64`, `float`→`double`,
`bool`→`i32`+flag, `none`→`void`, pointer-like→`ptr`. There is no runtime
type recursion: the `.pyi` signature fixes every arg's marshalling at
lower time, so the emitter never inspects a value — this is the property
that keeps the FFI off the island and off `PyObject`. ctypes-style
`c_void_p`/`byref`/callback objects are refused at manifest parse: the type
vocabulary is the subset's (`int`, `float`, `bool`, `str`, `bytes`,
handles, fixed `list`/`dict` where a thunk accepts them).

## 4. Static linking

Today `link_binary` (`llvm.py:40-102`) is two-stage: every unit
(the emitted `.ll` plus `runtime/*.c` from the glob at `llvm.py:58-60`)
compiles to `.o` in a loop (`llvm.py:83-91`), then all objects link with a
`-lm` tail (`llvm.py:62`, `llvm.py:93-99`). `zig cc` is selected by
`--driver zig` and `--target` supplies the triple with a bundled sysroot
(`llvm.py:55-57`); the same shape is what `packaging/zig/smoke.sh`
re-implements standalone for the tier-A matrix (x86_64/aarch64/riscv64
linux gnu+musl, macOS arm64/x64 compile-only — `smoke.sh` header and
`docs/getting-started.md:49-51`).

Changes are additive, in `link_binary` only:

- `--ffi <manifest>` is a repeat-argument on `compile` (`cli.py:29-50`),
  resolved before `lower_file`: the compiler loads each manifest, checks
  `ps_abi`/`format`, and hands the lowerer the module table and the linker
  the manifest's `[[source]]` list.
- The unit lists become `runtime/*.c` plus each manifest's
  `compile_units()` (C sources, the target cc compiles them) and
  `link_units()` (archives and objects, straight on the link line),
  resolved relative to the manifest file. They enter the same
  per-unit `.o` loop, so there is no second link shape. Per target this is
  free: `zig cc -c` already cross-compiles C for every tier-A triple, so a
  vendored `sqlite3.c` builds for macOS arm64 and linux x64/aarch64 with no
  extra tooling. `ManifestSource.is_compile_unit` is the split, not the
  suffix: `libfoo.a` declared under `objects=` links, one declared under
  `path=` would try to compile and fail loudly.
- `link_binary` refuses a declared unit that is not on disk
  (`_require_units`) before any cc runs, and `cli.py` refuses it earlier
  still (`PS4009`): a missing file names the manifest, not a cc invocation
  halfway through the link.
- `tail` gains per-manifest `-l`/`-L` flags only when a manifest declares
  `libs` or `lib_dirs` (e.g. `-lsqlite3` is *not* how sqlite ships, since
  the pilot vendors it, but `-lz` for a zlib manifest that does not vendor
  is). Default stays `-lm` only.
- `ps_mix.c`-style dlopen is explicitly rejected for FFI manifests: the
  whole point is self-contained binaries, and dlopen already fails on static
  musl (`smoke.sh` header; `ps_mix.c` is one of the POSIX blockers for
  windows-gnu). `ps_mix.c` stays as the `nativeprobe` test seam only.

## 5. The sqlite pilot

Recorded decisions (`http-seam-design.md:7-11`, plan line 79): package
`pyscriptc-sqlite` owns the vendored C, the `.pyi`, the manifest, and the
shim; opaque handles; milestone surface `connect -> execute/query ->
commit/close`. Mapped onto the format:

```toml
[manifest]
module = "pyscriptc_sqlite"
ps_abi = 1
format = 1

[[source]]
path = "vendor/sqlite3.c"
[[source]]
path = "ps_ffi_sqlite.c"        # adapter thunk file

[[handle]]
name = "Connection"
c_type = "sqlite3 *"
frees = "sqlite3_close_v2"
[[handle]]
name = "Statement"
c_type = "sqlite3_stmt *"
frees = "sqlite3_finalize"

[[const]]
name = "OK"                   # exposed as sqlite.OK -> 0
type = "int"
value = 0
[[const]]
name = "ROW"
type = "int"
value = 100
[[const]]
name = "DONE"
type = "int"
value = 101

# connect(path: str) -> Connection
[[fn]]
name = "connect"
py = "(path: str) -> Connection"
c_symbol = "sqlite3_open_v2"
abi = "ps_ffi_sqlite_connect"   # wraps the variadic open with fixed flags

# execute(db, sql) -> int         (SQLITE_OK or raises via thunk)
[[fn]]
name = "execute"
py = "(db: Connection, sql: str) -> int"
c_symbol = "sqlite3_exec"
abi = "ps_ffi_sqlite_execute"
borrows = ["db"]

# query(db, sql) -> list[dict[str, str]]  — M1 shape: rows as records.
# The thunk steps sqlite3_prepare_v2/column/step/finalize and builds the
# %ps.seq/%ps.map result behind the ps_* runtime ABI; that keeps ALL
# aggregate construction on our side of the thunk.
[[fn]]
name = "query"
py = "(db: Connection, sql: str) -> list"
c_symbol = "sqlite3_prepare_v2 + sqlite3_step + sqlite3_finalize"
abi = "ps_ffi_sqlite_query"
borrows = ["db"]

[[fn]]
name = "commit"
py = "(db: Connection) -> int"
c_symbol = "sqlite3_exec COMMIT"
abi = "ps_ffi_sqlite_commit"
borrows = ["db"]

[[fn]]
name = "close"
py = "(db: Connection) -> None"
c_symbol = "sqlite3_close_v2"
abi = "ps_ffi_sqlite_close"
consumes = ["db"]
```

Notes:

- `execute`/`commit` are both `sqlite3_exec` underneath with different fixed
  SQL handling inside the thunk — exactly why `abi` names the shim and
  `c_symbol` is documentation. Variadic or macro-only C entry points
  (`sqlite3_open_v2`'s `...`, `SQLITE_UTF8`) are always absorbed by the
  thunk.
- `query`'s `list` return rides the existing `ptr`-return ABI path
  (`llvm.py:1072-1074`): the thunk allocates through `ps_alloc` and fills a
  `PsSeq`/`PsMap` per `ps_rt.h`'s append-only layout — which is what makes
  `ps_abi` pinning load-bearing, and why the pin is REQUIRED in §1.
- Params are positional-only and `?`-bound by the thunk; no callbacks, no
  context managers, `rows as records` — the same rules the http seam
  adopted (`http-seam-design.md:54-57`).

## 6. Milestones

| gate | deliverable | proof |
|---|---|---|
| F0 | manifest parser + `ps_abi`/`format` validation; `--ffi` flag wired to `compile` | unit tests: reject bad pin, unknown format, duplicate module claim |
| F1 | lowerer clause + synthetic import-table entry; `nativeprobe` re-expressed as a manifest | `mixdemo` compiles through `--ffi` instead of the env-var gate; `packaging/zig` smoke still passes |
| F2 | `link_binary` source/object merge + per-manifest link flags | zig smoke extended: one manifest-built unit in the cross matrix (compile-only macOS OK); shipped as `feat/ffi-f2` (#220) |
| F3 | `pyscriptc-sqlite` pilot: `connect/execute/query/commit/close` | fixture script does open → create → insert → select → close; asserts rows; runs on host + `x86_64-linux-musl` |
| F4 | zlib or libm manifest by an outside-of-compiler adapter | proves the seam is not sqlite-shaped; CI gate = the same fixture pattern |

Each gate is mergeable alone; F3 is the seam the largest-blocker count points
at (`docs/compile-rate-essence-method.md` — sqlite3 is the single largest
seam win, 13 rows). F4 exists because a seam designed for exactly one
consumer rots into a special case.

### 6a. F1c decision: typed container annotations (2026-10-23, issue #236)

The manifest grammar accepts `list[T]`, `dict[K, V]` and the one nesting the
lane needs, `list[dict[K, V]]` (the sqlite row shape). `type_ir` maps them
onto the subset's `elem`/`key`/`value` IR forms, so a returned aggregate is
a regular subset list or dict: iteration, subscript, `len`, `in`, mutation
and freeing all go through the code paths the subset already has, and the
value is owned by the caller from the return on (the runtime owns freeing;
the library never frees a returned aggregate and never borrows one back).

The alternative, keeping the bare `list`/`dict` spelling with a `none` elem,
was rejected: `_llvm_type({"kind": "none"})` is `void` and `_elem_size`
refuses it, so element access could not lower without teaching the emitter
a special elem kind, and the checker could not catch a wrong key type on a
`row['nm']` read. Scalar-vocabulary elements, no deeper nesting, and no
handles inside containers (a handle elem would be a bare ptr the subset
cannot touch) keep the aggregate ABI inside what `ps_seq_*`/`ps_map_*`
already store. `for` over `list[dict[...]]` needed the loop guard in
`lower/_stmt.py` widened from scalars to scalar-or-dict; dicts as elements
emit through the same `ps_seq_get` slot copy as every other element.

### 6b. F3 receipt: the sqlite end-to-end pilot (2026-10-23, issue #66)

The pilot runs through the real pipeline with no compiler changes to the
FFI path: a fixture script (`examples/sqlite_rows.py`, the same source the
test lowers) does open, create, insert, select, close against the vendored
amalgamation, linked statically through the F2 `[[source]]` seam, and the
returned rows assert equal to what the stdlib `sqlite3` module prints for
the same sequence. The raise surface matches too: bad SQL and the
two-statements-in-one refusal come back as `RuntimeError` with "sqlite3: "
plus SQLite's own text, and use after close is the compile-time PS4010 of
F1b's ownership rule (stdlib raises `ProgrammingError` at runtime; the
subset refuses the program instead, the stricter contract
`pyscriptc_sqlite.pyi` documents).

Receipts:

- host: `tests/test_ffi_sqlite_pilot.py` builds the binary with the
  manifest's own units, matches CPython on the row output and on the raise
  text, runs leak-free under ASan+UBSan, and refuses use-after-close at
  the compile.
- cross: the same script links for `x86_64-linux-musl` through the zig
  driver and runs there (the `linux_container` suite; macOS hosts compile
  it but cannot exec an ELF binary).
- checker: a manifest module resolves through the adapter's `.pyi` (the
  search path gains the manifest directory and the stub root), so a wrong
  argument type fails the preflight with the same signature the lowerer
  enforces.
- corpus delta: the sqlite rows of the essence study
  (`.agents/docs/benchmarks/compile-rate-essence-method.md`, 13 rows
  shimmed through `python3` children) have their native seam; the
  seam-level receipt is the pilot itself, since the per-script catalogue
  stays private.

One manifest-level gap stayed open on purpose: `SQLITE_THREADSAFE=0` and
`SQLITE_OMIT_LOAD_EXTENSION` are compile definitions the manifest format
does not carry (no flag channel by design; the format stays
declarative). The pilot compiles the vendored `sqlite3.c` with its
defaults, which are thread-safe and extension-enabled and add a few
hundred KB; the host thunk test keeps the two defines for its own
differential build.

## 7. Integration points (open)

The F0 parser ships first; these are the seams the later gates plug into.

Lowering (`lower/`, F1):

- `load_manifests` runs in `cli.py` before `lower_file`: the compiler loads
  each `--ffi` manifest, validates the pins, and hands the lowerer the
  module table. A `FfiManifestError` surfaces through the existing
  `SpikeError` handler as exit 1 with the `PS4xxx` code.
- A manifest-loaded module gets a synthetic entry in the closed import table
  (`QUEUE`/`LOWERED`, `stdlib_queue.py`), so `import pyscriptc_sqlite` binds
  `Binding("module", ...)` exactly like a stdlib module.
- In `_module_call`, a new clause before the `wave_function` fallback looks
  the call up in the manifest's `[[fn]]` table, checks args against the
  parsed `params` (same positional/min/defaults rules `_fixed_call` applies),
  and returns `_abi_call(module, attr, args, ret, fn.abi, span)`. The parsed
  `ManifestParam` list is the input; no signature string is re-parsed there.
- The `py` types map onto the IR type dicts the lowerer already uses:
  `int`/`float`/`bool`/`str`/`bytes` to `{"kind": ...}`, `list`/`dict` to
  their `elem`/`key`/`value` forms, a handle to `{"kind": "class", "name":
  ...}` riding the `ptr` path (`llvm.py:1072-1074`).
- `[[const]]` entries inline as `{"kind": "const"}` nodes at the use site.

Linking (`llvm.py`, F2):

- `link_binary` merges each manifest's `compile_units()` into the per-unit
  `.o` loop (`.c` units compile) and each manifest's `link_units()` onto the
  final link line (archives and objects), resolved relative to the manifest
  file. `ManifestSource.resolved` already produces the absolute paths.
- `FfiManifest.link_flags()` produces the per-manifest `-L` and `-l`
  arguments; `cli.py` concatenates them across manifests and hands them to
  `link_binary` as `link_flags`.
- A manifest-free build produces a byte-identical link line: the manifest
  inputs are all optional, and the regression guard is
  `tests/test_ffi_link.py::test_a_build_with_no_manifest_has_the_master_link_line`.

Configuration (`pyscriptc.toml`, P3/v0.5.0):

- The planned config surface carries `ffi = ["packaging/adapters/pyscriptc-sqlite/ffi.toml"]`:
  a list of manifest paths resolved relative to the config file and passed
  to `load_manifests`. The per-import policy table maps a module to the
  `"extern"` lane (`.agents/plans/2026-10-21-adoption-compat-ffi-strategy.md`,
  section B); the manifest's `module` claim is what binds them.

Tooling:

- `pyscriptc coverage` lists the lane per import (`native`, `pack`, `extern`,
  `island`, `absent`) once the classifier lands (P7); a manifest-loaded
  module reports `extern`.
- `pyscriptc bind header.h` (P18, v0.8.0) generates a manifest in this
  format; the validator is the acceptance test for its output.

## Risks

- The thunk layer (`ps_ffi_*.c`) is where all real complexity lives —
  aggregates, error mapping, varargs. The manifest deliberately pushes it
  there rather than growing marshalling kinds; the risk is thunk code
  drifting from `ps_rt.h`, which is what the `ps_abi` pin + CI grep checks
  (`abi-strategy.md:51-56`) exist to catch.
- `format` bumps fragment the ecosystem early; mitigate by keeping the
  schema append-only inside a format epoch, same rule as `ps_rt.h`.
- Vendored C trees enlarge every binary that uses them (order 200-500 KB
  per `http-seam-design.md:41-44`); manifests make that opt-in per compile,
  which is the point of `--ffi` being a flag and not ambient.
