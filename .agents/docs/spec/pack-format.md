# Pack format spec

Spec for compiled libraries as installable packs (issue #377, epic #430, parent #152, plan row P11 of `.agents/plans/2026-10-21-adoption-compat-ffi-strategy.md`).

A pack is a distributable artifact that replaces a PyPI distribution's import with natively compiled code: a `.pyi` surface, a manifest, C sources (or prebuilt objects) and a conformance suite, one directory, buildable into a `py3-none-<platform>` wheel that both `pyscriptc` and CPython can consume. The ground truth this spec formalizes: the in-tree orjson pack (`src/pyscriptc/restlower/_orjson.py`, stub `src/pyscriptc/_pyright_stubs/orjson/__init__.pyi`, lowered onto `runtime/ps_json.c`), the jwt pack (`restlower/_jwt.py`, `runtime/ps_jwt.c`), the sqlite FFI pilot (`packaging/adapters/pyscriptc-sqlite/ffi.toml`, sections 1-5 of `.agents/docs/ffi/ffi-manifest.md`), the FFI marshalling vocabulary (`--ffi`, PR #367) and the static-library mode (`--emit lib`, PR #364).

Status: draft, reviewable. Nothing here is implemented; #411 (migrate pyscriptc-orjson) and #378 (conformance runner) are the implementing children. This document is the contract they build against; changes to it after they land need a recorded decision.

## 1. On-disk layout

A pack is a directory named `pyscriptc-<name>` (matching the PyPI distribution it publishes):

| Path                  | Req | Contents |
| --------------------- | --- | -------- |
| `pack.toml`           | yes | identity and manifest, section 2 |
| `surface/<import>/__init__.pyi` | yes | the public `.pyi` per claimed import name |
| `src/*.c`             | yes* | C sources the target cc compiles (*or `prebuilt/` objects, never both) |
| `prebuilt/<triple>/*.o` | no | per-triple prebuilt objects, alternative to `src/` |
| `conformance/suite.toml` | yes | the #378 suite: import name, call sequences, expected CPython outcomes |
| `tests/`              | no | pack-local fixtures the suite runner may use |
| `LICENSE*`, `NOTICE*` | yes | license of the pack and of any vendored code |
| `vendor/VENDOR.toml`  | no | upstream pin for vendored sources (sqlite precedent) |

Rules:

- `pack.toml` replaces the pilot's `ffi.toml` as the single entry point. The FFI manifest tables (`[[source]]`, `[[handle]]`, `[[const]]`, `[[fn]]`) embed verbatim under a `[ffi]` prefix; the validator (`src/pyscriptc/ffi_manifest.py`, codes PS4001-PS4009) enforces them unchanged.
- `surface/` holds exactly the names the pack claims. Everything a user program can call must appear in a `.pyi`; anything outside the surface fences at compile time (`PS2001`, the `_orjson.py` precedent: `orjson.Fragment` and `default=` are fenced, not approximated).
- `src/` xor `prebuilt/`: a pack ships either compilable sources or per-triple objects. Shipping both is a manifest error (PS4005 class).

Worked skeleton for pyscriptc-orjson:

```text
pyscriptc-orjson/
  pack.toml
  surface/orjson/__init__.pyi
  src/ps_pack_orjson.c
  conformance/suite.toml
  LICENSE-APACHE.md
  LICENSE-MIT.md
```

## 2. Manifest schema (`pack.toml`)

TOML, hand-authored, comments matter (same reasoning as `.agents/docs/ffi/ffi-manifest.md` section 1: `tomllib` is stdlib on `requires-python = ">=3.14"`, and manifests are reviewed like header files).

```toml
[pack]
name = "pyscriptc-orjson"       # distribution name, must equal the directory name
version = "1.0.0"               # the pack's own SemVer release
replaces = "orjson"             # the PyPI distribution whose imports it covers
replaces-version = ">=3.8,<4"   # the upstream versions the surface mirrors
ps-abi = 1                      # compiler/runtime ABI epoch this pack was built against
format = 1                      # this schema's version; bumps independently of ps-abi

[[imports]]                     # import names the pack binds; one or more
name = "orjson"

# one [[imports]] per additional name the same pack covers:
#   pyscriptc-orjson also claims ujson and simplejson today
#   (src/pyscriptc/native_packages.json maps all three dists to the pack)

[[platform]]                    # platform targets; one row per shipped triple
triple = "aarch64-apple-darwin"
wheel-tag = "macosx_11_0_arm64"

[[platform]]
triple = "x86_64-unknown-linux-musl"
wheel-tag = "manylinux_2_17_x86_64"

[ffi]                           # everything from the FFI manifest, verbatim
# [[ffi.source]], [[ffi.handle]], [[ffi.const]], [[ffi.fn]]
# with the same field rules and the same PS4001-PS4009 refusals

[conformance]
suite = "conformance/suite.toml"
```

Field rules:

| Field              | Rule |
| ------------------ | ---- |
| `pack.name`        | lowercase, `pyscriptc-` prefix; the compiler refuses a mismatch with the directory name (new code, PS40xx class, registered in `diagnostics.py`) |
| `pack.version`     | SemVer, the pack's release axis, independent of the replaced dist |
| `pack.replaces`    | one dist name; must match a `dist` in `src/pyscriptc/native_packages.json` or the compile refuses with the existing lane diagnostics |
| `pack.ps-abi`      | REQUIRED integer; the compiler refuses a pack whose pin differs from the running compiler's stamped `PS_ABI` (reuse of PS4004 semantics) |
| `pack.format`      | REQUIRED integer; refuse unsupported values (PS4003) |
| `imports[].name`   | dotted import names; two installed packs claiming one name is PS4007 |
| `platform[].triple`| the zig/cc target triple the pack was built for; a compile for an unlisted triple refuses before lowering, with the mobile-refusal precedent (`PS4101` style) |
| `platform[].wheel-tag` | the PyPI platform tag the built wheel carries, section 5 |

## 3. Surface and hashes

- The `.pyi` files are the entire Python-visible contract. Their signatures are the marshalling contract, exactly as in the FFI design (`.agents/docs/ffi/ffi-manifest.md` section 1: the `.pyi` fixes every arg's marshalling at lower time; `int`, `float`, `bool`, `str`, `bytes`, `list`, `dict`, declared handles; `Any`, `Optional`, unions of aggregates refused at parse time).
- `dumps(obj: Any) -> bytes` is legal only because the pack lowers `Any` internally against a fixed shape rule (the `_orjson.py` precedent: `loads` reads the annotated type, `dumps` accepts the JSON-representable subset and fails otherwise). The spec does not extend the type vocabulary; it reuses it.
- `pyscriptc deps` records every consumed pack wheel's sha256 in the lock, same as pure wheels today (`deps.py` already refuses locked wheels without a hash). A compile refuses a pack whose on-disk hash differs from the lock entry.

## 4. Versioning and compatibility

| Axis               | Owner | Compatibility rule |
| ------------------ | ----- | ------------------ |
| `pack.version`     | pack author | SemVer of the pack; major bump when the surface narrows or the ABI pin moves |
| `replaces-version` | pack author | range of upstream dist versions the surface mirrors; `pyscriptc deps` warns when the lock pins an upstream version outside it |
| `format`           | this spec | bumped only by a recorded decision; the compiler supports the current and previous value |
| `ps-abi`           | compiler | must equal the compiler's stamped ABI epoch exactly; no range, the ABI strategy forbids cross-epoch linking |
| upstream dist      | PyPI    | irrelevant to the wheel beyond the warn above; the pack never links upstream code |

`native_packages.json` keeps its `pack` pointer as today but gains the installed pack's resolved version at lock time, so `pyscriptc config` and the lane report show `pack pyscriptc-orjson 1.0.0` instead of a bare name.

A pack is compatible with a compiler release iff: `ps-abi` matches, `format` is supported, and every `[[ffi.fn]]` thunk still satisfies the `[[handle]]` ownership rules the compiler enforces. Anything else refuses at compile time, never mislinks.

## 5. Distribution: the wheel

A pack builds into one platform wheel per `[[platform]]` row:

| Property | Value |
| -------- | ----- |
| Distribution tag | `pyscriptc-<name>` |
| Python tag | `py3` (no CPython C-API anywhere, section 6) |
| ABI tag | `none` |
| Platform tag | from `platform[].wheel-tag` |
| Example | `pyscriptc_orjson-1.0.0-py3-none-macosx_11_0_arm64.whl` |

Wheel contents:

```text
pyscriptc_pack/<name>/            importable loader package (pure Python)
    __init__.py                   ctypes loader, section 6
    _native.so|dylib|dll          the compiled shared object (renamed per platform)
    pack.toml                     the manifest, shipped for introspection
pyscriptc_pack-<name>.pth         optional: puts the pack on pyscriptc's pack search path
pyscriptc_pack-<name>-<version>.dist-info/
    METADATA WHEEL RECORD LICENSE
```

The shared object is the pack's `src/*.c` plus the pyscriptc runtime archive compiled for that triple (the `--emit lib` machinery of #364 produces exactly this artifact shape for mobile triples; the wheel builder reuses it with `--emit lib` desktop targets). The `.pyi` surface does not ship inside the wheel: CPython consumers import the loader's Python re-export of the ctypes surface; pyscriptc consumers read the surface from the installed `pack.toml` + `surface/`, which ship inside the wheel's loader package when the consumer is pyscriptc (kept in `pyscriptc_pack/<name>/surface/`).

## 6. CPython consumption (ctypes, no C-API)

The wheel's loader package exposes the same import names the pack claims, backed by `ctypes.CDLL` against the shipped shared object:

- Every `[[ffi.fn]]` becomes a thin Python function that marshals through ctypes primitives (`c_char_p`, `c_int64`, buffers) and returns plain Python values.
- Marshalling reuses the #367 FFI vocabulary: the same types, the same ownership (`borrows`/`consumes` become explicit `free()` calls on the Python side; `frees` handles are finalized in a `__del__`).
- No `Python.h`, no extension modules, no CPython C-API: this is what makes `py3-none-<platform>` honest and keeps the wheel installable on any interpreter claiming that platform.
- Exceptions: a thunk failure raises a pack-defined exception subclassing `RuntimeError` with the pack name and the C message, matching the sqlite pilot's raise surface (`RuntimeError` with `sqlite3: ` plus SQLite's own text).

Fidelity note: the CPython path and the compiled path share the C implementation but not the marshalling layer, which is exactly the gap the conformance runner (#378) exists to close.

## 7. How the compiler consumes a pack

Flow for `pyscriptc deps` and `pyscriptc compile`:

| Step | Actor | Behavior |
| ---- | ----- | -------- |
| 1 | `deps` | a locked dist whose `native_packages.json` row names a pack is classified lane `pack`, not `native`; the resolver looks for an installed `pyscriptc_pack/<name>` whose `pack.toml` `replaces` matches the dist |
| 2 | `deps` | found: the pack wheel is hash-locked into the lock file like a pure wheel. Not found: the lane stays `native` with today's hint |
| 3 | `compile` | an import matching a claimed name binds against the pack's `surface/*.pyi`; calls lower through the pack's `[[ffi.fn]]` entries (the `_orjson.py` lowering becomes manifest-driven, not in-tree) |
| 4 | `compile` | a call outside the surface fences (`PS2001`) with the pack name in the reason |
| 5 | `compile` | the target triple must appear in `[[platform]]`, else refuse before lowering |
| 6 | link | the pack's objects/sources join the link line through the F2 `[[source]]` seam (the sqlite pilot path), or the prebuilt shared object is linked as-is |
| 7 | emit | the binary carries the pack name and version in `summary.json` for receipts |

In-tree precursors migrate: `native_packages.json` rows keep their `pack` pointer, but the lowering modules (`restlower/_orjson.py`, `_jwt.py`, ...) move into their packs and are deleted from the tree once #411 and the jwt follow-up land. The `pack` lane in `docs/reference/config.md` points at this spec instead of describing the format inline.

## 8. Install flow (pip and uv)

```bash
# author / CI side: build the wheels
pyscriptc pack build packaging/adapters/pyscriptc-orjson   # emits dist/*.whl
uv publish dist/pyscriptc_orjson-1.0.0-py3-none-*.whl

# consumer side, either manager:
uv pip install pyscriptc-orjson==1.0.0          # resolver picks the platform wheel
pip install pyscriptc-orjson==1.0.0
```

- Nothing executes at install time: no `setup.py`, no build backend, no entry-point hooks. The wheel is unzip-and-import; the loader's only import-time work is one `ctypes.CDLL` on first call (lazy), so `import orjson` in a CPython venv without a call never touches the native file.
- `--no-binary` consumers get a clear error pointing at the platform wheels; sdists are explicitly not part of this format (the `deps.py` sdist refusal rationale applies: building would run arbitrary code).
- Version pinning is ordinary pip semantics plus the lock hash from section 3.
- `pyscriptc deps` installs pack wheels into the same content-addressed cache as pure wheels, keyed by sha256.

## 9. Security

| Control | Mechanism |
| ------- | --------- |
| install-time code execution | none; wheels are data plus prebuilt native code, no build backend |
| supply chain | sha256 pinning in the lock file, required by `deps.py` today, extended to pack wheels |
| provenance | `pack.toml` records `replaces` + `replaces-version`; wheels carry normal PyPI metadata; vendored sources carry `vendor/VENDOR.toml` pins (sqlite precedent) |
| license compliance | LICENSE/NOTICE mandatory in the pack directory and the wheel |
| native attack surface | the shared object exports only the pack's thunk symbols plus the two `--emit lib` runtime symbols; hidden visibility elsewhere (#364 discipline) |
| consumer trust | the compile refuses a hash mismatch between lock and installed pack |

Deliberately out: signature verification beyond the lock hash (PyPI attestations are the platform's job), sandboxing of the shipped native code.

## 10. Conformance contract (#378)

Every pack ships `conformance/suite.toml`: data, not code. The runner (#378) executes each case twice and diffs by compat2 outcome categories (`benchmarks/compat2/outcomes.py`), never raw bytes:

| Suite field | Meaning |
| ----------- | ------- |
| `import` | the import name under test |
| `[[case]]` | one call sequence: expressions, ordered |
| `case.expected` | the CPython (real lib) outcome: value, exception name, or exit code |
| `case.compare` | outcome category to compare (`stdout`, `exception`, `exit`) |

Contract:

1. Run the sequence against the real library under CPython in a clean venv (real `orjson` installed from PyPI).
2. Run the same sequence against the compiled pack (the pack wheel's loader, or the compiled binary embedding the pack).
3. Compare by outcome category; byte-identical output is the default requirement for value-returning cases, so a pack passes only when the compiled output matches CPython exactly (the receipt standard used for `ps_json` and the jwt pack).
4. A deliberately broken case must fail the runner; that negative check is part of #378's acceptance.

The runner interface, not the suites, is what this spec fixes; #378 defines the exact CLI and CI wiring.

## 11. Worked example: migrating pyscriptc-orjson (#411)

Today (in-tree, master):

| Piece | Location |
| ----- | -------- |
| lowering | `src/pyscriptc/restlower/_orjson.py` (dumps with `OPT_SORT_KEYS`/`OPT_INDENT_2` onto `ps_json_dumps_opts`, loads via `ps_json_loads`) |
| surface | `src/pyscriptc/_pyright_stubs/orjson/__init__.pyi` (dumps, loads, two OPT constants) |
| registry | `native_packages.json`: `orjson`, `ujson`, `simplejson` all point at `pyscriptc-orjson` |
| native core | `runtime/ps_json.c` (shared with stdlib json) |
| tests | ad-hoc CPython differential tests |

After migration:

| Piece | New location |
| ----- | ------------ |
| whole pack | `packaging/adapters/pyscriptc-orjson/` (the sqlite adapter's home, so a pack is buildable outside the compiler tree) |
| `pack.toml` | `name = "pyscriptc-orjson"`, `version = "1.0.0"`, `replaces = "orjson"`, `replaces-version = ">=3.8,<4"`, `ps-abi = <current>`, `format = 1`, three `[[imports]]` rows (orjson, ujson, simplejson), one `[[platform]]` per shipped triple |
| surface | `surface/orjson/__init__.pyi`, byte-identical to today's vendored stub |
| manifest | `[ffi]` tables express dumps/loads as `[[ffi.fn]]` entries with `abi = "ps_pack_orjson_dumps"` / `"ps_pack_orjson_loads"` thunks; the option-flag mapping (writer flags 0x1 / 0x100 / 0x10000) moves into the thunk C source |
| thunk | `src/ps_pack_orjson.c`: thin wrappers over `ps_json_dumps_opts` / `ps_json_loads`, the only new C in the pack |
| conformance | `conformance/suite.toml`: dumps/loads round-trips, `OPT_SORT_KEYS`, `OPT_INDENT_2`, the fenced names (`default=`, `Fragment`) asserted as refusals |
| tree deletion | `restlower/_orjson.py` and `_pyright_stubs/orjson/` removed; `native_packages.json` unchanged (the pointer already names the pack) |

Migration is mechanical by design. If a step needs a compiler change, that is a spec gap recorded on #377, not a patch inside #411.

## 12. Open questions

| # | Question | Notes |
| - | -------- | ----- |
| 1 | Does `ps_json.c` stay in the compiler runtime or get copied into the pack? | Sharing it keeps one JSON implementation; copying makes the pack self-contained. Decide before #411. |
| 2 | Should `[[imports]]` support a `shadow` marker for packs covering dists whose import the compiler already lowers natively (the dotenv counterexample)? | `native_packages.json` already models the shadowing case. |
| 3 | Cross-pack dependencies (pyscriptc-jwt wanting pyscriptc-orjson's JSON) | nothing in the format yet; defer until a pack needs it. |
| 4 | Do wheels embed the pyscriptc runtime, or link a shared `libpyscriptc` wheel? | Embedding ships N copies; sharing adds a wheel dependency. Start embedded, revisit when the census says the size hurts. |
| 5 | Windows tags and musl-vs-glibc mapping table | the `[[platform]]` wheel-tag is author-supplied; a validation table may be wanted. |
| 6 | Conformance suite determinism on float formatting | `ps_dump_float` vs CPython repr; pinned by the byte-identical rule, but confirm on the suite data. |
| 7 | Should the loader re-export type stubs for CPython IDE users? | Ship `surface/` inside the wheel's `py.typed`-marked package if yes. |
