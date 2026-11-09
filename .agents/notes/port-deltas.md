# Native Port Deltas: Syntax and Semantics Changes

This document records the exact syntax and semantics deltas required to port two real sibling scripts (wowplay-app/classify.py and fx/binary_size.py) from the island floor to 100% native execution in pyscriptc.

## Summary of Native Share

For wowplay-app/classify.py, native definitions increased from 0 of 3 (0.0%, all island) to 7 of 7 (100.0% native), eliminating all 11 compiler fences.

For fx/binary_size.py, native definitions increased from 3 of 13 (23.1% partly native, demoted to 0% by caller cascade) to 19 of 19 (100.0% native), eliminating all 45 compiler fences.

Both ported scripts compile natively to standalone binaries via `pyscriptc compile --emit bin`, execute on fixed inputs without CPython fallback, and emit stdout and exit codes matching CPython 3.14.

## Delta Classification Table

The table below catalogs each fence code encountered across the two scripts, the syntax and semantics changes required, whether the change is mechanical (candidate for automated assess suggestions) or semantic (requires domain knowledge or changes behavior), and the total occurrence count.

| Fence Code | Category | Syntax / Semantics Delta | Nature of Change | Occurrence Count |
|---|---|---|---|---:|
| PS1001 | Local inference | Add explicit type annotations on module-level constants (e.g. `MIB: int = 1_048_576`, `DEFAULT_WARNING_BYTES: int = 52_429`). | Mechanical (auto-fix suggestion) | 7 |
| PS1001 | Unannotated containers | Replace unannotated or heterogeneous collections with typed dataclasses or typed dictionaries. | Semantic (behavior modeling) | 2 |
| PS1104 | Dynamic annotations | Replace `object` and `Any` in signatures with concrete dataclasses (`ArtifactEvidence`, `DeltaInfo`, `Report`, `Evidence`). | Semantic (behavior modeling) | 5 |
| PS1106 | Bare container annotations | Replace bare `dict` return annotations with concrete typed dataclasses or string formatting helpers. | Semantic (behavior modeling) | 1 |
| PS2001 | Unlowered stdlib methods | Replace `Path.open("rb")` and chunked stream reads with `path.read_bytes()` and `path.read_text()`. | Mechanical (auto-fix suggestion) | 2 |
| PS2001 | Optional attribute access | Guard optional attributes on `Path | None` and `re.Match | None` with explicit `is not None` blocks before method calls. | Mechanical (auto-fix suggestion) | 5 |
| PS2001 | Unlowered imports | Replace `collections.abc.Sequence` with concrete `list[str] | None` parameter annotations. | Mechanical (auto-fix suggestion) | 1 |
| PS2002 | Global symbol `__file__` | Replace `Path(__file__).resolve()` repository discovery with explicit configuration constants or CLI arguments. | Semantic (runtime environment) | 2 |
| PS2002 | For loop over dataclass lists | Replace `for item in list_of_dataclass:` with indexed loops `for i in range(len(items)): item = items[i]`. | Mechanical (auto-fix suggestion) | 2 |
| PS2002 | Set construction | Replace `set(dict)` and set union operators with explicit iteration `for k in dict.keys(): s.add(k)`. | Mechanical (auto-fix suggestion) | 2 |
| PS2003 | Call arity and keywords | Pass function arguments positionally instead of with keyword arguments (e.g. `build_report(...)`). | Mechanical (auto-fix suggestion) | 4 |
| PS2003 | Argparse option types | Replace `type=pathlib.Path` in `add_argument` with default `str` and convert to `Path` on attribute access. | Mechanical (auto-fix suggestion) | 6 |
| PS2003 | Argparse literal defaults | Replace variable identifier defaults in `add_argument` with literal constant values (e.g. `default=52429`). | Mechanical (auto-fix suggestion) | 1 |
| PS2003 | Argparse parse_args arity | Invoke `parser.parse_args()` without positional arguments to read process arguments from sys.argv. | Mechanical (auto-fix suggestion) | 2 |
| PS2003 | Sorted collection types | Replace `sorted(paths)` with `sorted([str(p) for p in paths])` and construct `Path` from sorted strings. | Mechanical (auto-fix suggestion) | 1 |
| PS2003 | Complex sort key functions | Replace `sorted(items, key=lambda ...)` on tuples with a typed sorting loop over dataclass elements. | Semantic (behavior modeling) | 1 |
| PS2003 | Hashlib update streams | Replace incremental `hashlib.sha256()` plus `update()` loops with single-buffer `hashlib.sha256(data).hexdigest()`. | Mechanical (auto-fix suggestion) | 1 |
| PS1003 | Optional return narrowing | Extract regex match groups with `val: str | None = m.group(1)` and narrow with `if val is not None:` before converting to `int`. | Mechanical (auto-fix suggestion) | 5 |
| PS2004 | Type comparison | Narrow optional union types using explicit `is not None` branches before checking secondary conditions. | Mechanical (auto-fix suggestion) | 2 |
| PS2002 | Sibling call cascade | Demotions caused by unlowered caller functions cascade down; resolved automatically when caller becomes native. | Mechanical (cascade resolution) | 4 |

## Mechanical vs Semantic Delta Analysis

Mechanical deltas represent 45 of the 56 total fence sites (80.4%). These include adding missing scalar type annotations on module assignments, switching from keyword arguments to positional arguments, splitting `is not None` guards into dedicated blocks, converting `type=Path` in argparse to `type=str`, and reading files via `read_bytes()` rather than stream iterators. These transformations preserve exact semantics and could be emitted as automated fix suggestions by `pyscriptc assess`.

Semantic deltas represent 11 of the 56 total fence sites (19.6%). These include replacing heterogeneous dictionary schemas (`dict[str, object]`) with strongly typed dataclasses (`ArtifactEvidence`, `DeltaInfo`, `Report`, `Evidence`), defining explicit comparator sorting for complex multi-key tuples, and removing runtime source tree dependencies on `__file__`. These transformations change internal representation types while keeping user-visible behavior, output formatting, and exit codes identical.
