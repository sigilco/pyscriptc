# The strong pure-dependency receipt: candidate measurement and target choice

Issue #415 (closes #137's exit criterion). Feasibility spike, measured on
master `bb923e63` (2026-11-23). This note records the fence profile of every
candidate, the target decision, and the receipt plan. It changes no code.

## Method

For each candidate a PEP 723 consumer (`/tmp/pysc/c415/consumers/*_consumer.py`)
was resolved with `pyscriptc deps --json`, then the package directory inside the
materialised wheel source root (`~/.cache/pyscriptc/deps/src/<sha256>/<pkg>`)
was profiled with `pyscriptc coverage --json`. A second, realistic consumer
(`*_real.py`) went through `pyscriptc compile --emit ir` to count the fences in
the *reachable* slice, so the number that actually decides a receipt, since
unreached modules are pruned.

## Fence profile (measured, not quoted)

Whole-tree `rootFences` is `pyscriptc coverage` on the wheel source root.
"Reachable" is the fence list in the emitted IR of a consumer that calls into
the package. "Non-cascade" drops the `is not exported / is not a local` tails
that follow from a root fence.

| candidate (resolved) | lane | transitive deps | tree rootFences | reachable fences | non-cascade |
| --- | --- | --- | --- | --- | --- |
| shellingham 1.5.4 | pure | none | 41 | 5 | 2 |
| colorama 0.4.6 | pure | none | 72 | 141 | ~83 |
| tomli 2.5.0 | pure | none | 44 | 82 | ~61 |
| iniconfig 2.3.1 | pure | none | 22 | 26 | ~20 |
| wcwidth 0.9.5 | pure | none | 423 | 563 | ~242 |

For comparison, the issue's pre-#412 numbers were shellingham 42, colorama 147,
tomli 63, iniconfig 43, wcwidth 302. The #412 typing slices (collections.abc in
annotations, TypeVar common shapes, merged 2026-11-12 in #1220) landed since:
iniconfig dropped 43→22, tomli 63→44, colorama 147→72. wcwidth went 302→423
mostly because today's resolution is wcwidth 0.9.5, a much larger rewrite
(52 files, grapheme/linebreak tables) than the version measured for the issue.

### Per-candidate root-fence breakdown (whole tree)

- **shellingham**: module-value 19 (PS2002), param-inference 12 + return-inference
  6 (PS1001), plus one each of module-assign, import-table, unlowered-call,
  stdlib:ctypes.
- **colorama**: param-inference 29 + return-inference 7 (PS1001),
  construct-other 13, name-resolution 8, module-value 6, unlowered-call 5,
  call-arity 3, stdlib:atexit 1.
- **tomli**: unlowered-type-name 31 (PS1106: `Final`, `Pos`, `Key`,
  `ParseFloat`, `timezone`), generic-alias 4 (PS1103: `re.Match[str]`,
  `IO[bytes]`, `Final`-in-IR), field-lookup 2, stdlib:datetime 2,
  typing-value 2 (`Callable`/`Tuple` used as values in `_types.py`),
  construct-other/import-table/name-resolution 1 each (`types` module).
- **iniconfig**: type-mismatch 5 (PS1003 `tuple[none, X]` vs
  `tuple[str | None, str | None]`), call-arity 4, construct-other 3,
  optional-types 2 (PS1102 multi-arm unions), local-inference 2,
  name-resolution 2, unlowered-call 2, class-cascade 1, generators 1,
  generic-alias 1.
- **wcwidth**: name-resolution 169, export-cascade 88, type-mismatch 55,
  construct-other 39, unlowered-call 38, local-inference 34, module-assign 23,
  operator 22, decorators 14, and 11 more families.

## Disqualifications, by measurement

- **wcwidth (0.9.5)**: 423 tree-root / 563 reachable across ~17 families
  (enum-typed returns, `frozenset`, `lru_cache` decorators, tuple table
  shapes, heterogeneous dicts). Out on size alone.
- **colorama**: 141 reachable. The useful API (`init`, `Fore`, `Style`) reaches
  `ansitowin32`→`win32` (ctypes), module `sys` used as a value, module-global
  stream state, `atexit.unregister`, class-as-value. The package *is*
  platform glue; the fences are its semantics, not its surface. Out.
- **shellingham**: only 5 reachable fences, but they understate the distance.
  `detect_shell` dispatches through `importlib.import_module("." + os.name)`
 , so probe-verified: `PS2001 import_module returns a module`, a hard fence
  into `posix` backends that run `subprocess.check_output(["ps", ...])` and
  walk `/proc`. `subprocess` does lower, but only onto the island lane (the
  emitted binary links host libpython, PS4304), which is the weak tier this
  receipt is succeeding. The two non-cascade fences (unannotated `pid`, a
  set-literal module constant) mask a runtime surface that cannot match
  CPython natively even at zero fences. Out on semantics, not count.
- **tomli**: 44/82, and the visible count is optimistic. ~55 of the reachable
  fences are annotation-erasure (`Final`, `Pos`, `Key`, `ParseFloat`,
  `re.Match[str]`, `IO[bytes]`, `Callable`/`Tuple` as values), which masks
  the second wave underneath. Probe-verified pieces of it: a `re.compile`
  result held as a module constant fences (`PS2002 global RE_NUM of Pattern
  is not lowered`, so `re` lowers the *function* surface only, so `RE_*`
  constants and every `Match` call are gated), genexp-built `frozenset`
  unions and set difference need annotations/features (literal
  `frozenset(" \t")` does lower). Estimated but unprobed: `Match` methods,
  `types.MappingProxyType`, `sys.getrecursionlimit`, `str |
  type[DEPRECATED_DEFAULT]` unions, `*args`, `-> Any` returns, `lru_cache`,
  a multi-arg `TOMLDecodeError` with custom attributes, and
  `Output`/`Flags`/`NestedDict` classes that set fields without declaring
  them. Eight-plus distinct feature clusters, several unknowable until
  erasure lands. Out for now; re-measure after the annotation-alias erasure
  slice.
- **iniconfig**: 22 tree-root / 26 reachable, and the floor test below shows
  the gap is enumerable and small.

## Target: iniconfig

The typing-surface slices landed on master (the issue's precondition), so the
choice is between the two parsers. iniconfig wins on every count: 22 vs 44
tree-root fences, 26 vs 82 reachable, ~20 vs ~61 non-cascade, and, so decisive
a floor test that compiles and runs today.

### The floor test (the measurement that decides it)

A copy of the real iniconfig 2.3.1 wheel source root in the deps cache was
patched at exactly the points the compiler refuses today, keeping the public
API shape (`IniConfig`, `SectionWrapper`, `ParseError`, `lineof`, `get`,
`parse_ini_data`, `parse_lines`, `_parseline`, `ParsedLine`→plain class). A
consumer that parses an ini string and prints values, `lineof` and `get`
results then **compiles to a native binary whose stdout and exit code match
CPython byte-for-byte** (6 lines, `value / 42 / y / 2 / fallback`).

Every patch point maps to a named compiler slice. Single-statement probes
(`/tmp/pysc/c415/probes/`, each compiled to a binary and run) tightened the
list, so several suspected blockers already lower today: `frozenset(" \t")`
literals and membership, keyword-only parameters, two-arm unions including
`str | os.PathLike[str]` (the `os.PathLike` arm erases), `str.removeprefix`,
and `open(path, encoding=...)` with `with ... as fp` + `fp.read()`. The
remaining work list, in roughly increasing size:

1. `str.splitlines(True)` keepends row, so `splitlines()` lowers, the
   `keepends` argument does not (`PS2001 call of attribute is not lowered
   (splitlines)`, probe-verified); `parse_ini_data` uses keepends.
2. `os.fspath` stdlib row, so `PS2001 os.fspath is not in the lowered subset
   of os` (probe-verified); used unconditionally in
   `IniConfig.__init__`/`parse`. Identity for `str`.
3. `Final` in annotations, so `PS1103 Final is not a lowered type in the IR`
   (probe-verified; `UTF8_BOM: Final`, the `Final` fields). Extends the #412
   erased-name machinery to one more name.
4. `none`-literal → `opt_T` unification: `return None, None` infers
   `tuple[none, none]` and refuses `tuple[str | None, str | None]` (5×
   PS1003 in `_parseline`, probe-verified with a minimal repro). Note
   `print(x)` on an `opt_str` value also fences, so the receipt consumer
   should `str()`/unwrap or print concrete values.
5. Multi-arg exception constructors and exception attributes:
   `ParseError(path, lineno, msg)` + `super().__init__(path, lineno, msg)`
   (4× PS2003 "exception takes one scalar argument"), and `e.lineno` /
   `e.msg` reads ("exception has no attribute", probe-verified).
6. `tuple` as a dict key: `dict[tuple[str, str | None], int]` for
   `_sources` (3× "dict keys are int or str"), read via `.get((section,
   name))` and written `sources[section, name] = lineno`.
7. `NamedTuple` class base → record (`ParsedLine`, "this class base is not
   lowered", probe-verified); also unlocks `call to ParsedLine`.
8. One access path to sections: either dunder dispatch (`ini["s"]["k"]`,
   `"s" in ini`, so `__getitem__`/`__contains__` do not dispatch from `[]`/`in`
   today, probe-verified) or `IniConfig.get` (needs `@overload` stub
   skipping and the `Callable | None` / TypeVar `default` signature
   `get`'s body itself is small). `ini["s"]["k"]` is the documented API and
   the better demo; `get` is the smaller slice. The two PS1102 multi-arm
   unions (`_D | _T | str | None`, `Callable[[str], _T] | None`) live only
   in `get` (lines 70 and 228), so the `__getitem__` route avoids union
   work entirely.
9. Tuple-return unpack: `sections_data, sources = parse_ini_data(...)`
   ("unpacking needs a tuple or list value" on a call result; tuple
   subscript `parsed[0]` is also unlowered).

Not needed: the generator/`sorted(key=)`/`Iterator` internals (`__iter__`,
`items`), `lineof`'s `int | None` (single-arm optionals already lower),
`Mapping`/`collections.abc` names (erased by #412), keyword-only params,
two-arm unions, `os.fspath`-adjacent `os.path` basics. Fenced methods simply
do not export; the class still lowers.

## Receipt plan

Same shape as the certifi receipt (#409, `benchmarks/library/` +
`tests/test_deps_receipt.py`), so the machinery is proven:

- `benchmarks/library/iniconfig_receipt.py`: a PEP 723 script,
  `requires-python = ">=3.14"`, `dependencies = ["iniconfig"]`, no vendored
  code. The program exercises the documented API: build an `IniConfig` from a
  literal ini document passed as `data` (no file staging needed, so `data=`
  keeps the receipt I/O-free), read `ini["section"]["key"]` (or
  `ini.get(...)` pending slice 9), `ini.lineof(...)`, membership, and a
  `ParseError` path on a malformed document whose `str(e)` is printed. Every
  printed value is deterministic across the lanes, so no host paths, no
  `where()`-style shape asserts.
- `benchmarks/library/pylock.iniconfig_receipt.toml`: committed lock
  (iniconfig 2.3.1, wheel sha256) so `--frozen` runs without the resolver.
- `benchmarks/library/run.py` extended (or a sibling `iniconfig_run.py`):
  `deps.run_deps` → assert lane `pure` → `compile --skip-check --emit bin
  --debug-dir` → run the binary → run the same script under CPython with the
  deps source root on `PYTHONPATH` → require identical stdout and
  returncode 0 on both sides, and report the fence count from `ir.json`.
- `tests/test_deps_receipt.py`: a second test, same gates
  (`PYSC_NETWORK_TESTS=1`, zig or clang on PATH); CI picks it up on the
  existing `network-receipt` job.

The receipt lands when slices 1–10 above (minus whichever of 9 is chosen)
have shipped; the PR records the fence profile of all five candidates and the
choice, closing #415 and #137.

## Artifacts

Raw measurements: `/tmp/pysc/c415/out/*_{deps,coverage}.json`,
`*_dbg/ir.json`, `ini_native.txt` vs `ini_cpython.txt` (the floor-test diff).
Patched floor: `/tmp/pysc/c415/patched/iniconfig/` +
`consumers/iniconfig_real.py`. Consumers and locks:
`/tmp/pysc/c415/consumers/`.
