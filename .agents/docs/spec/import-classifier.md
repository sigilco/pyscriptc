# Import classification

`pyscriptc imports FILE|DIR` classifies every import of a program or a package tree into the compat lane it can take. It answers the first question of the adoption ladder: which imports compile from source, which need a replacement pack, which reach native code, and which are standard library. It never emits code and never touches the network. The capability ladder lowers the target through the coverage report to attribute fences; the classification itself stays compiler-free.

```
$ pyscriptc imports tool/
tool/: 10 imports (12 statements) by capability island 2, native 8
  numpy: island (requires libpython) [native-backed, lane extern]
    native-backed distribution numpy (C, C++ or Rust extension)
  orjson: native [curated-replacement, lane pack]
    curated replacement pack pyscriptc-orjson covers this import
```

`--json` prints the same report as a JSON document with a `schemaVersion`.

## The capability ladder

Each import carries a `capability` next to its kind and lane:

| Routing | Meaning |
| --- | --- |
| `native` | the import lowers as it is; no fence stands in the way |
| `fenced` | the import lowers, but this tree has a fence on code that uses it; `codes` lists the PS20xx codes |
| `island` | the import needs the boxed CPython tier (lane `island` or `extern`), or a fence on it traces to runtime reflection; `requires` is `libpython` |

The ladder is the default user-visible view. A reflection fence (`inspect.signature`, `get_type_hints`, `__annotations__`, a metaclass, a dynamic `getattr`) is attributed to the import root named in its message, so `import inspect` used through `inspect.signature` reports `island (requires libpython)` even though the module itself is in the closed import table. The `mechanisms` list names the culprit exactly, and `files` says where the fence sits. The capability seam lowers the target through the coverage report; a target that does not lower keeps the lane-based routing and adds no fence data.

## Kinds and lanes

Each import gets one of four kinds.

| Kind | Meaning |
| --- | --- |
| `stdlib` | a standard-library module; the closed table in `pyscriptc.stdlib_queue` says whether it lowers today or is queued |
| `native-backed` | the import reaches a compiled extension (C, C++ or Rust), so it cannot be compiled from pure-Python source |
| `curated-replacement` | a planned replacement pack mirrors the library's API subset with our own native code |
| `pure-python` | a third-party import with no known compiled extension, so the compiler can compile it from source |

The lane is what the import would do under the policy table of the adoption plan: `native` (compile from source, the default), `pack` (use the replacement pack), `extern` (call the underlying C library through an FFI manifest), `island` (opt-in boxed CPython, only when the policy table says so) or `absent` (compile the `ImportError` branch). The classifier reports which lane an import can take, not which one the user picked; the policy table that overrides it is part of the v0.5.0 config work.

A stdlib module that is outside the closed import table reports lane `extern` with a hint pointing at the manifest path, because that is the lane that can reach it today. A module that is queued reports lane `native` with a queued note, because it lowers without extra work once its wave lands.

## How a verdict is reached

Three sources are consulted, in order. The first that answers wins.

1. The compiler's own import table (`pyscriptc.stdlib_queue`) for every standard-library module.
2. The data file `src/pyscriptc/native_packages.json`, the curated list of known native-backed distributions and their planned packs. It grows from real pin data, one entry per distribution, and is the only offline source for the curated verdicts.
3. The metadata of the installed distribution, when the caller resolved the dependency graph and passed the distribution name in: the wheel `RECORD` listing a `.so` or `.pyd` file, or an unpacked tree holding a C, C++ or Rust source beside the Python. A wheel without a compiled extension is evidence the package is pure Python; a missing distribution is not evidence of anything, and the verdict stays `pure-python` with a heuristic note instead of claiming knowledge it does not have.

`from X import y` is classified by the module `X`, not the bound name `y`, because the lane depends on the module that holds the code. Relative imports stay inside the tree and report lane `native` with no further lookup.

## The report

The report function is `pyscriptc.imports.build_report(target)` and is the seam the coverage tooling calls. It takes a file or a directory and returns the same JSON document the CLI prints: one entry per import root, grouped by the first dotted component, each carrying the kind, the lane, the reason, the distribution and pack when known, the source of the verdict, every source site (`file`, `line`, `statement`), and the `capability` ladder rung. `pack_candidates(report)` extracts the curated subset ordered by how often the import appears, which is the input to ranking which pack to build first.
