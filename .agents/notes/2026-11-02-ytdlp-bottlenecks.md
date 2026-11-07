# yt-dlp bottlenecks: is a compiled-helper (Rust-style) split worth it? (2026-11-02)

Assessment only. No compiler features were written. The question: is moving yt-dlp's
bottlenecks into pyscriptc-compiled modules that stock CPython yt-dlp imports (the
way projects move hot paths to Rust) worth it, versus compiling the whole app?

Verdict up front: **no.** The profile is not a flat Python-hot-path profile; it is
dominated by `re` module *compilation* and by C-level stdlib work (json, regex
matching, I/O). The single largest yt-dlp-owned cost is an O(n*m) algorithm whose
fix is a one-line data-structure change in upstream Python, not a compiler. And
the extension-module path that a Rust-style split requires does not exist in
pyscriptc today, and the one artifact pyscriptc can hand CPython is *slower* than
the CPython it replaces.

## Corpus and method

- yt-dlp 2026.10.26 source tree with a generated `yt_dlp/extractor/lazy_extractors.py`
  at `/var/tmp/ytbench2/src` (the tree the frozen composite in
  `2026-10-30-ytdlp-composite.md` used).
- Local offline media: 28 900-byte ffmpeg `testsrc` mp4 served by
  `python -m http.server` on `127.0.0.1:8614`. Generic extractor, one URL.
- Profiling: `cProfile` (cumulative and tottime) plus a `time.perf_counter`
  timer harness; every number below is a median of >=5 samples after a warmup.
- Interpreter: CPython 3.15.0 on the host for the profile runs; the composite
  note's 3.14.9 baseline is unaffected by this choice (regex compile and json
  costs are C-level in both).

Scripts: `/tmp/ytp*.py`. Raw profile dumps: `/tmp/ytbottle/profile_cli_2026-11-02.txt`,
`/tmp/ytbottle/profile_extract_2026-11-02.txt`, `/tmp/ytbottle/cli_stats.dump`.

## Phase breakdown of a real offline extraction

`yt-dlp -j --no-warnings --user-agent pinned http://127.0.0.1:8614/sample.mp4`
measured from inside the process (fresh interpreter, cold):

| Phase | Cost (median) | Owner |
|---|---|---|
| `import yt_dlp` | 237 ms | yt-dlp / CPython import machinery |
| `gen_extractor_classes()` (1754 classes, lazy) | 46 ms | yt-dlp |
| **first `suitable()` over 1754 extractors** | **643-715 ms** | **CPython `re` compile** |
| second `suitable()` over the same set (regex cache warm) | 9-10 ms | CPython `re` match |
| `YoutubeDL.__init__` | 86-165 ms | yt-dlp `orderedSet_from_options` |
| `extract_info` (steady, warm ydl) | 17-19 ms | yt-dlp + I/O |
| whole CLI process wall | 1430-1680 ms | sum of the above + ffmpeg probe + startup |

The last `suitable()` number is the whole story: **643 ms cold vs 9 ms warm.** The
cold cost is `re.compile` of the 1704 distinct `_VALID_URL` patterns, measured
directly at **518 ms** for the same 1704 patterns with a purged cache. The regex
*matching* is 9 ms. CPython's `re` module caches compiled patterns in a dict of
512; 1704 patterns blow past it.

## Ranked hotspots (cProfile, full CLI run, 3.334 s total tottime)

Aggregated by category over all 3 157 recorded frames:

| Category | tottime | share |
|---|---|---|
| stdlib `re` module (pure-Python parse/compile) | 2.134 s | **67.6%** |
| C builtins and C methods (incl. `re` matching, json) | 0.748 s | 23.7% |
| **yt-dlp's own Python** | **0.197 s** | **6.2%** |
| other stdlib Python | 0.055 s | 1.7% |
| import machinery | 0.024 s | 0.8% |

Top individual frames:

| # | Frame | tottime | note |
|---|---|---|---|
| 1 | `re/_parser.py:511(_parse)` | 0.667 s | compiling 1704 `_VALID_URL` patterns |
| 2 | `re/_compiler.py:39(_compile)` | 0.326 s | same |
| 3 | `re/_parser.py:168(__getitem__)` | 0.235 s | same |
| 4 | `utils/_utils.py:774(_iter)` | 1.447 s (steady runs) | `orderedSet` O(n^2) dedup |
| 5 | `utils/_utils.py:5329(orderedSet_from_options)` | 0.771 s (steady runs) | ditto, plus a 1747x1754 regex scan |
| 6 | `extractor/lazy_extractors.py:63(_match_valid_url)` | 0.088 s | the actual URL matching |
| 7 | `utils/traversal.py:251(apply_path)` | 0.017 s | `traverse_obj` |

## Ranked candidates, with share of a typical extraction run

Shares are against the 1430-1680 ms whole-process offline extraction.

| Rank | Candidate | Cost | Share of run | In the typed subset today? |
|---|---|---|---|---|
| 1 | `re.compile` of 1704 `_VALID_URL` patterns (stdlib, not yt-dlp) | 518-643 ms | **36-40%** | n/a — CPython C `re`, already native |
| 2 | `orderedSet` / `orderedSet_from_options` (yt-dlp) | 86 ms | **5-6%** | **no** — `island` (fenced: `PS1001 parameter iterable needs an annotation`) |
| 3 | `orderedSet_from_options` regex scan (1747 compiles x 1754 fullmatch = 3.06 M regex calls) | included in #2 | ~5% | **no** — `island` |
| 4 | `traverse_obj` (`utils/traversal.py`) | 0.098 s / 1000 calls | <0.1% | **no** — `island` (0 native of 27 defs) |
| 5 | `FormatSorter` preference computation | 13.3 ms / 100 formats | <0.1% | **no** — `island` |
| 6 | m3u8 manifest parse (2000 segments) | 32.9 ms | ~2% of a real HLS extraction | **no** — `island` (`extractor/common.py`: 0 native of 215) |
| 7 | `JSInterpreter` compile + call (trivial) | 0.41 ms | negligible | **no** — `island` (0 native of 54 defs) |
| 8 | `parse_duration` | 3.5 us/call | negligible | **no** — `island` |
| 9 | `json.loads` of a 112 KiB player JSON | 2.14 ms | ~0.1% | n/a — CPython C json, already native |
| 10 | `determine_ext` / `sanitize_url` | 1.7 / 1.4 us per 1000 | negligible | **no** — `island` |

`pyscriptc assess` on the real yt-dlp files (`/var/tmp/ytbench2/src/yt_dlp/...`):

| File | definitions | native | island | blocked |
|---|---|---|---|---|
| `utils/_utils.py` | 407 | **0** | 401 | 6 (370 fence statements: 344 unsupported-construct, 24 untyped-import, 2 reflection) |
| `utils/traversal.py` | 27 | **0** | 19 | 8 |
| `YoutubeDL.py` | 187 | **0** | 181 | 6 |
| `jsinterp.py` | 54 | **0** | 54 | 0 |
| `extractor/common.py` | 215 | **0** | 214 | 1 |

Every candidate function fences on `PS1001` (missing parameter/return
annotation). The module top level fences on `PS2002` untyped imports
(`binascii`, `codecs`, `collections.abc`, `email.header`, `html.parser`, `locale`,
`mimetypes`, `netrc`, `secrets`, `shlex`, `xml.etree.ElementTree`, and relative
imports), which routes the whole top level island-side. This matches the
measured yt-dlp native fraction of 0% in `2026-10-30-plane-split.md`.

A hand-typed rewrite *is* accepted by the compiler: `orderedSet(iterable:
Iterable[str]) -> list[str]` assesses as `native`, and `parse_duration(s: str) ->
float` as `native`. So the fence is annotation-level, not a language-level wall —
but upstream yt-dlp is unannotated, and the task is stock-CPython-imported
yt-dlp, so a typed rewrite is not available without patching upstream.

## Extension-build gap

**There is no way to build a pyscriptc-compiled module that stock CPython
yt-dlp imports as a normal module.** What exists:

- `pyscriptc compile --emit binding` (`src/pyscriptc/cli.py:2120-2177`,
  `src/pyscriptc/binding.py`) emits a shared library plus a generated
  `<stem>_binding.py` ctypes loader. It is *not* a CPython extension module:
  no `PyInit_*` entry point is generated by pyscriptc anywhere in `src/`.
- The binding scope is deliberately tiny (`binding.py:22-23`): top-level
  functions with `int`/`float`/`bool`/`str` parameters and returns only.
  Measured: `orderedSet(iterable: Iterable[str]) -> list[str]` is **skipped**
  by the generated loader ("parameter 'iterable' has unsupported type
  'list[str]'; return type has unsupported type 'list[str]'"), and so is
  `parse_duration` once it returns a list. `list`/`dict`/`Any` are not
  lowerable in the binding ABI, and `Any` is rejected outright
  (`PS1104: Any is not a lowered type in the IR`).
- The island lane runs imported modules **from source** inside embedded
  CPython (`2026-10-25-island-imported-modules.md`), not as compiled code.
- `roadmap.md:97` states a `PyInit_*` wheel `.so` can only be consumed via
  embedded CPython (the island), at roughly 1x speedup.
- Mixed native/island importability from stock CPython is explicitly deferred
  (`2026-10-25-mixed-plane-modules.md`; the plane-split design in
  `2026-10-30-plane-split.md` is unimplemented).

So the Rust-style split would require: (a) a `PyInit_*` emitter in pyscriptc,
(b) a `list`/`dict`/opaque-handle ABI for the binding surface, or per-call
marshalling of whole structures, and (c) per-module namespace seeds so an
island module can hold native functions and still be imported by CPython.
None of (a), (b), (c) exist.

## Prototype (cheap; measured before/after against stock)

Two prototypes were built, both under 30 minutes, because the tooling already
existed in the tree.

### P1: native program, no extension ABI (the ceiling)

A typed `orderedSet` compiled with `pyscriptc compile --emit bin`, doing the
same 1754-element dedup as the stock helper:

| | wall (whole process, median of 11) |
|---|---|
| pyscriptc native binary | **2.77 ms** |
| CPython, same program | **68.51 ms** |

24.8x faster as a whole program. This is the *ceiling* for a compiled helper:
the native lane itself is fast. It is also unreachable in the proposed design,
because the helper has to be called *from* CPython.

### P2: real CPython extension module (`PyInit_*`) over the binding `.so`

A hand-written `PyInit_durfast` wrapper (the pattern is already in
`/tmp/ytbottle/proto/ext/durfast.c`) over `--emit binding` output, importing
a compiled `parse_duration` into stock CPython:

| case | stock | compiled (`PyInit` ext) | ratio |
|---|---|---|---|
| `parse_duration('1:23:45')` | 3.53 us | 7.46 us | **2.11x slower** |
| `parse_duration('1234')` | 6.76 us | 12.80 us | 1.89x slower |
| `parse_duration('1:30')` | 6.66 us | 11.75 us | 1.76x slower |
| `parse_duration('P1DT2H3M4S')` | 10.29 us | 14.35 us | 1.39x slower |
| `parse_duration('not-a-duration')` | 3.31 us | 5.94 us | 1.80x slower |
| `parse_duration('12:34:56.789')` | 3.93 us | 9.22 us | **2.35x slower** |

Values are correct (all six cases match stock to 1e-9, modulo stock's `None`
for an unparseable string vs the compiled `0.0`), so the slowness is not a
correctness bug. Two causes, both measured:

1. **The extension ABI floor.** A compiled `add(a: int, b: int) -> int` called
   through the generated ctypes loader costs 1.488 us per call, against 0.112 us
   for the same operation in plain CPython. The binding's C ABI routes every
   call through a `setjmp` guard frame (`ps_bind_guard` in the generated
   `*_binding_shim.c`), which is what the 1.5 us is.
2. **pyscriptc's own regex engine is slower than CPython's C `re`.** CPython
   `re.match` on the duration pattern: 0.536 us. The compiled version does the
   same match and takes 7.46 us. This matches the committed note
   `2026-10-25-real-script-bench.md`, which measures the `classify` workload at
   **1.51x slower** natively because "the runtime's own regex engine
   (fuel-capped backtracking subset) dominates".

So the extension path is not merely unavailable; the one prototype that exists
is **1.4x to 2.4x slower than the CPython it replaces**, because the hot
patterns in yt-dlp are regexes and CPython's `re` is C while pyscriptc's is
a backtracking interpreter.

## The profile is flat where it matters

Two structural facts make compiled helpers unable to move the composite:

1. **The dominant cost is C already.** 67.6% of the CLI profile is the stdlib
   `re` module's *pure-Python compile* path, and 23.7% is C builtins (which
   includes the actual regex matching, json, and I/O). Only 6.2% of the profile
   is yt-dlp's own Python. Even a perfect compiler for yt-dlp's Python can
   address at most ~6% of the run.
2. **The one real yt-dlp-side hotspot is an algorithmic bug, not a language
   problem.** `orderedSet` is an O(n^2) list-membership dedup: 36.3 ms for 1754
   items. The same dedup with a `set` is **0.4 ms** — a 90x algorithmic win in
   plain Python, versus the 24.8x a native rewrite buys on the same work
   (and the native rewrite cannot be imported by CPython at all). The upstream
   fix is one line: `if x not in seen` over a list becomes a set membership
   test. `orderedSet_from_options` additionally does 1747 `re.compile` calls
   each followed by a `fullmatch` scan over all 1754 names (3 064 238 regex
   calls); the equivalent plain-Python membership filter is 29 ms.

Note also that the frozen composite's own numbers already say the same thing
from the other end: the compiled bundle's `extract_s` (2.325 s) is **2.06x
slower** than the CPython venv baseline (1.131 s), and the whole-program
compiled bundle's losses are dominated by bootstrap and memory, not by
execution speed.

## What the numbers say, in order

1. Move the ~643 ms regex-compile cost. That is CPython's `re` module compiling
   1704 `_VALID_URL` patterns at import time. It is 36-40% of a run, it is not
   yt-dlp code, and it is not addressable by compiling yt-dlp. A precompiled
   regex cache or a lazily-compiled matcher set would move it; so would
   shipping `.pyc`-style prebuilt patterns.
2. Fix `orderedSet` upstream (list -> set). 36.3 ms -> 0.4 ms in plain Python.
   This is the only yt-dlp-owned hotspot above 1% of a run, and it is a 2-line
   upstream patch, not a compiler feature.
3. Everything else in yt-dlp's Python is below 1% of the run: `traverse_obj`
   (0.098 s per 1000 calls), `FormatSorter` (13.3 ms per 100 formats),
   `parse_duration` (3.5 us), `determine_ext`/`sanitize_url` (under 2 us per
   1000), `JSInterpreter` (0.41 ms for a trivial script), json (2.14 ms for a
   112 KiB player). None of these justify a compiled-helper split.

## Owner decisions needed

1. **Extension-module ABI.** If the Rust-style split is still wanted, pyscriptc
   needs a `PyInit_*` emitter plus a binding ABI that carries `list`/`dict` (or
   an opaque-handle + accessor protocol). Today `--emit binding` skips every
   function with a container parameter or return, which is every candidate on
   the list. This is a compiler feature and is out of scope for this note.
2. **Whether to keep the `re`-engine gap on the table.** The native regex
   engine is measurably slower than CPython's C `re` (2.11x on `parse_duration`,
   1.51x on the committed `classify` workload). Until that flips, any yt-dlp
   helper whose hot path is a regex loses when moved to the native lane, even
   if the ABI were free.
3. **Upstream vs compiler.** The 643 ms regex-compile cost and the 36 ms
   `orderedSet` are both better fixed in yt-dlp (or in CPython) than in
   pyscriptc. If the goal is a faster yt-dlp, these are the levers; the
   compiler is not on the critical path for them.
4. **Do not start the compiled-helper split on the current evidence.** The
   ceiling for a whole native helper is 24.8x on one 86 ms hotspot (5-6% of a
   run), the importable path is 1.4-2.4x *slower* than stock, and the profile
   says 93.8% of the run is not yt-dlp's Python.
