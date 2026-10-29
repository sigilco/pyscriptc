---
title: Proven patterns
description: Measured cases where pyscriptc paid off, with the command that produced the number, the receipt, and the limit.
---

This page records patterns that were measured on real programs, not a feature list.
Each pattern states when it fits, the exact command that produced the number, the receipt, and the honest limit.
The measurements were taken on macOS arm64 with CPython 3.14.9 as the reference; a different machine moves the absolute numbers, not the shape of them.
Where a pattern did not pay off, it says so.

The support state of every construct lives on the [compat site]({{PYSC_COMPAT_URL}}); the gaps live in [What will fail](../reference/what-will-fail.md) and in the numbered [divergences](../reference/divergences.md).

## Pattern 1: island-compile an existing CLI into one binary

**When it fits.** A pure-Python CLI you want to ship as a single file, where the behavior has to match CPython exactly and the program is too large or too dynamic to lower natively.

**How to do it.** Install the program, compile the package in program mode with the island tier on, and compare the output against CPython byte for byte.

```bash
uv pip install --target ./ytpkg yt-dlp
rm -rf ./ytpkg/yt_dlp/__pyinstaller
uv run pyscriptc compile ./ytpkg/yt_dlp --program --island --embed-python host \
  --emit bin --skip-check -o ./ytdlp
```

The `--program` flag treats the directory as a program (`python -m pkg` parity) and embeds every module in the binary, so the result runs with no `PYTHONPATH` and no source tree next to it.
The `--island` flag (alias of `--fallback=cpython`) routes the definitions the typed subset refuses to the embedded CPython, which is what makes an untyped package compile at all.
Non-Python files in the program directory (data files, JavaScript assets) are staged beside the binary under `pyscriptc-data/<program name>/`.
The `rm -rf` line removes `yt_dlp/__pyinstaller/hook-yt_dlp.py`, a packaging hook that is not an importable module; the compiler refuses a program directory that holds one, because the file name is not a chain of identifiers.

Verify the result against CPython before you ship it:

```bash
./ytdlp --list-extractors > bin.txt
python -m yt_dlp --list-extractors > cpy.txt
cmp bin.txt cpy.txt
```

**Receipt.** yt-dlp 2026.10.26, 1,043 files and 10,024 definitions, compiled in 2 min 20 s to a 11.4 MB binary.
`--version` and `--list-extractors` (1,755 lines) are byte-identical to `python -m yt_dlp` on stdout, stderr and exit code, verified with `cmp`.
The binary links one shared library, the host `libpython3.14.dylib`; the whole package source travels inside the binary.

**The limit.** This is a packaging win, not a speed win, and the numbers say so plainly:

| Measurement | CPython | Island binary |
| --- | --- | --- |
| `--version` (best of 7) | 150 ms | 523 ms |
| `--list-extractors` (best of 3) | 0.18 s | 0.63 s |
| HLS manifest over a local server | 0.62 s | 1.06 s |
| DASH manifest over a local server | 0.66 s | 1.76 s |
| HTML extraction over a local server | 1.19 s | 2.35 s |
| Binary size | 24 MB of source tree | 10.9 MB binary plus a 18.1 MB `libpython` |

The binary is 1.5 to 3.4x slower than CPython on the same work, and the reason is measurable: the island recompiles the embedded sources at every start (`exec(compile(source, path, "exec"), module.__dict__)`), while CPython reads cached `.pyc` files. Importing `yt_dlp.extractor` costs 994 ms with a cold bytecode cache and 151 ms with a warm one, so roughly 0.84 s of every cold start is source compilation that CPython does not pay.
The binary also does not remove the Python requirement: it links the host `libpython` by absolute path, so it runs only where that library exists.
The honest summary is one binary, one command, byte-identical output, and no interpreter install for the user, at the price of startup time.

## Pattern 2: profile first, then compile only the hot module

**When it fits.** A program where a few modules carry the runtime and the rest is glue.

**How to do it.** Profile the CPython run, find the module that holds the time, and check whether it lowers natively before you plan anything.

```bash
python -m cProfile -s tottime your_program.py > profile.txt
uv run pyscriptc coverage path/to/hot_module.py    # 0 fences means it lowers
```

**The receipt, and the rule that came out of it.** Two seam costs decide whether splitting a program pays.
Both were measured with the compiler on the same machine, 200,000 crossings per run:

| Direction | Measured cost per crossing | Wins when |
| --- | --- | --- |
| Native calls island (in-binary trampoline) | 0.44 to 0.53 us | the island body costs more than the crossing |
| Island calls native (`--emit binding`, ctypes) | 0.75 us (0.54 us raw ctypes) | the CPython body costs more than the crossing |

The ctypes direction has a sharp cliff, and it is worth writing down because it is the direction most people try first.
A helper whose CPython body is `return x + 1` costs 0.124 us in CPython and 0.786 us through the generated binding loader: **6x slower than the code it replaced**.
A helper whose body is a ten-iteration loop costs 0.695 us in CPython and 0.540 us through the binding, which is where the direction starts to pay.

The rule that falls out of it: a binding only pays when the CPython body is worth more than the crossing.
At the measured 12 ns per bytecode, that is a body of roughly 60 to 100 bytecodes, not a three-line helper.
Measure the body before you build the seam, not after.

## Pattern 3: a typed hot loop in the subset

**When it fits.** A request path, a parse loop, or a dispatch table you control, written to the subset's rules.

**How to do it.** Write the hot path to the subset's rules, compile it, and time it against CPython on the same source.

```bash
uv run pyscriptc coverage micro.py     # 0 fences (0 root) in 0 of 10 definitions (10 native)
uv run pyscriptc compile micro.py --emit bin -o micro
```

**Receipt.** A router, request and response records, a query-string parser and a JSON response helper, all annotated and all native, dispatching 20,000 requests:

| Side | Time for 20,000 dispatches | Requests/s |
| --- | --- | --- |
| Compiled native | 25 ms | 790,607 |
| CPython 3.14.9 | 51 ms | 391,273 |
| Ratio | **2.0x** | |

Across three runs on the same machine the ratio was 2.0x to 3.2x; the absolute numbers move with the host, the direction does not.
The binary is 585 KB and links no `libpython`, because nothing in it needs the island.

**The limit.** The subset is the price of the speed.
Fields are declared in the class body and assigned in `__init__`, not annotated in `__init__`.
A list is mutated through a named local, not through an attribute expression.
`json.dumps` over a dict with mixed value types does not lower, so the JSON is built by concatenation.
The full per-construct table is in [Typing and idioms](typing.md); the rewrite cost is real and belongs in the estimate, not after it.

## What did not pay off

Two directions were measured and did not earn their place, and both are recorded so nobody repeats them.

**Compiling a hot helper and calling it from island code.** A binding call costs 0.75 us before any work happens.
The yt-dlp hot leaf it would replace, `FormatSorter._calculate_field_preference_from_value`, is about thirty lines of straight-line Python and costs well under the crossing, so the port is a measured regression.
The profitable direction is native calls island, and only for bodies heavy enough to pay the 0.5 us.

**Inferring types into untyped code.** The compiler infers what it can prove and refuses the rest: a missing annotation is a `PS1001` fence, never a guessed `Any`.
An annotation overlay applied to yt-dlp's untyped parameters moved 66 of 7,646 fenced definitions to native on the `url`-shaped parameters alone, and 223 when every remaining parameter was annotated `str`, because the wrong annotations create type-mismatch and name-resolution fences of their own.
A class-body constant table (3,949 fences) is the larger lever, but it is a compiler feature, not an inference one.
The [island floor guide](island-floor.md) is the reference for what the fallback tier does and does not do.
