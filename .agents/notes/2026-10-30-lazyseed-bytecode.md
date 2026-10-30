# The bytecode-backed lazy island seed (2026-10-30)

## What the seed was

Under `--fallback=cpython` (`--island`) a multi-module program runs whole
island-side (Refs #641). The seed that made that work was one Python source
string: a small finder bootstrap with every module's source text inlined as
a string literal in one dict, `_PS_MODULES`, plus the entry module's source
inlined as a literal too. The binary carried it as one global
(`island.seed`), and `ps_island_top_seed` handed it to `PyRun_String`, which
compiles and runs it.

Two costs came with that shape, both measured on the yt-dlp tree (1042
modules, 10.6 MB of source):

- The runtime compiled the 10.6 MB blob at every startup: 0.22 s on
  CPython 3.14.9.
- The source text stayed resident: about 28 MiB of the 163 MiB peak RSS of a
  `--version` run.

The finder was already lazy. A `--version` run imports 66 of the 1042
island modules; the other 976 were compiled and held for nothing.

## What the seed is now

Three parts, all in the binary:

- `island.seed`: the finder bootstrap, about 2.6 KB. It holds no module
  source any more.
- `island.seed.index`: the marshalled `{name: (origin, is_package, offset,
  length)}` table, about 90 KB.
- `island.seed.body`: the concatenation of the per-module deflated code
  objects, about 5.6 MB.

`ps_island_top_seed_payload` hands the index as a bytes object and the body
as a read-only `memoryview` over the binary's own bytes (no copy, so the
pages stay file-backed until a module body is read out of them) through a
`__pyscriptc_seed__` module in `sys.modules`. The bootstrap's `_PsFinder` and
`_PsLoader` are unchanged in shape: the loader now reads one body out of the
blob, inflates it, unmarshals it, and execs the code object.

The build side is `boxed.program_island_source`, which compiles every module
at optimization level 0 (`compile(..., dont_inherit=True, optimize=0)`),
marshals the code object, deflates it, and appends it to the body blob.

## The decisions, and why

**The bytecode is compiled at build time by the compiler's own interpreter.**
pyscriptc already refuses to run when the compiler's CPython is not the
target CPython (PS3001), so the compiler's `compile()` produces bytecode the
target interpreter loads. The marshal format is stable inside a minor
version, so the blobs are valid for the pinned 3.14.9 target.

**The optimization level is pinned to 0.** `compile()` inherits the running
interpreter's `-O` level by default, so a compiler run under `python -O`
would bake `__debug__ = False` and drop `assert` statements into the
bytecode of a program that then runs under a plain interpreter. Pinning
`optimize=0` makes the bytecode independent of how the compiler was invoked.
The consequence is documented: an island module's `__debug__` is True and
its asserts are live, whatever `-O` the embedded interpreter was started
with. The bundle never starts it with `-O`.

**The source text is not shipped.** A module's `co_filename` stays the
compile-time path, exactly as it was when the runtime compiled the source
(`compile(source, path, "exec")` used the same path). So a traceback names
the same file and the same line as before. `linecache` finds no source text
for it, which is also what happened before: the bundle never shipped the
`.py` files, so `linecache.getline` returned nothing for an island module
either way. Shipping the source to fix that would put the 10.6 MB back into
the binary and the resident set, which is the cost this change exists to
remove, so the decision is to keep the traceback's file and line and not the
source line.

**`__file__` and the origins are still rebased at run time.** The index
carries the compile-time origin; the bootstrap rebases it onto the staged
data tree (issue #665) exactly as it did before, so `spec.origin`,
`module.__file__` and `spec.submodule_search_locations` are unchanged.

**The entry module keeps its source in the bootstrap.** The entry runs as
`__main__` and its `co_filename` has always been the *rebased* `__file__`,
which is a run-time value. A precompiled code object cannot carry it, so the
entry's source stays inline and is compiled at run time, as before. It is
one small file.

**The body blob is exposed as a memoryview, not a bytes copy.** The copy
would put 5.6 MB in the heap for the whole run. The memoryview keeps the
blob in the binary's read-only pages, and only the pages a run actually
touches become resident.

**Compression is zlib, and zlib is available.** The bundle's `lib-dynload`
is empty, so `zlib` is not a loadable extension module there. It is
available anyway, because CPython 3.14 links zlib into libpython itself
(`PyInit_zlib` is exported) and `libpython3.14.so` carries it. The bootstrap
imports `zlib` and `marshal`; `marshal` is a builtin module and `zlib` is
in CPython's own stdlib as a pure wrapper over the built-in extension. Both
were verified to import inside a minimal-trim bundle.

## What it bought

On the yt-dlp composite, same runner and baselines, before and after
re-measured in the same session:

| | `--version` | `--list-extractors` | extract | download | RSS |
|---|---|---|---|---|---|
| before | 1.084 s | 1.554 s | 2.335 s | 2.376 s | 162.8 MiB |
| after | 0.404 s | 0.523 s | 1.396 s | 1.368 s | 54.8 MiB |

The composite against B1 went from 1.374 to 0.925, and against B2 from 1.441
to 0.973. The self-compiled pyscriptc bundle went from 0.294 s to 0.238 s
and from 45.1 MiB to 29.5 MiB.

The build pays for it: about 4 s of extra `compile`/`marshal`/`compress` on a
1042 module tree, against a 42 to 58 s build whose `lower` stage dominates.
The linked binary shrank from 10.7 MB to 5.9 MB, because the deflated
bytecode is smaller than the source text.

## What is left

`files_n` against B2 (7.267 on log2) is the only dimension still worse than
2.0x: the bundle is a 153 file tree and B2 is one file. That is the
onefile lever, and it did not move in this session. `version_s` against B1
is 1.465: what is left is the interpreter's own start and the bootstrap's
`importlib.abc`/`importlib.util`/`zlib`/`marshal` chain, which a program
that exits immediately could defer.
