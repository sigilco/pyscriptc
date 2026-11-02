---
title: "Comparison with other tools"
description: "How pyscriptc relates to Nuitka, Cython, mypyc, Codon, Numba and scriptc, by what each project's own documentation says."
---

Several tools compile or accelerate Python.
They differ in what they take as input, what they produce, and how close they stay to CPython semantics.
This page states the differences without claiming one is faster than another: the projects measure themselves differently, and pyscriptc publishes its own numbers on the [landing page](https://pyscriptc.pages.dev/) instead of comparing here.
The one measured cross-tool row on this page is the micro-benchmark at the bottom, run on one VPS with the code attached; treat it as an anecdote, not a ranking.

## At a glance

| | Input | Output | Types | CPython semantics |
| --- | --- | --- | --- | --- |
| **pyscriptc** | typed Python subset | native binary; untyped imports run on an embedded CPython island | annotations required, checked by pyrefly before lowering | subset; deviations are fenced with a `PSxxxx` diagnostic, numbered divergences documented |
| **CPython** | standard Python (baseline) | interpreted bytecode, needs an interpreter | not needed | the reference |
| **PyPy** | standard Python | JIT inside its own interpreter process | not needed | compatible with caveats (C-extension support through `cpyext`, slow first call) |
| **Nuitka** | standard Python | extension module, standalone folder or onefile executable | not needed | aims for full compatibility, including Python 2.6/2.7 modes |
| **Cython** | Python or the Cython language (`.pyx`) | CPython extension module (`.so`) | optional; static types add speed and C interop | its own compiled language; documented limitations page |
| **mypyc** | typed Python | CPython extension module per compiled module | standard hints, enforced at runtime (`TypeError` at boundaries) | mostly compatible; restricts some dynamic features |
| **Codon** | Python-like source | native executable or extension module | not required; its own Hindley-Milner inference, monomorphization | explicitly not a drop-in replacement (64-bit `int`, no monkey-patching, C-style numerics by default) |
| **Numba** | decorated Python functions | JIT-compiled machine code in-process | not required; argument types inferred at call time | nopython-mode subset with a documented supported-features list; object mode covers the rest |
| **Shed Skin** | implicitly statically typed Python | C++ extension module or standalone | none written; types inferred and must not vary per variable | restricted subset, standard-library slice documented |
| **scriptc** | TypeScript/JavaScript | native executable and WASI module | standard TypeScript via `tsc` | not Python; listed here for the architectural parallel |

## The tools

### CPython

The baseline: bytecode interpreted on a stack machine, every dynamic feature, every C extension, no build step.
Distribution means shipping an interpreter or depending on one being installed.
Every tool on this page exists to trade some of that flexibility for speed or a self-contained artifact.

### PyPy

[PyPy](https://www.pypy.org/) runs standard Python on its own JIT-compiling virtual machine.
No annotations, no build step, a drop-in interpreter replacement; the program speeds up after warmup while the first iterations pay JIT and garbage-collection tuning costs.
Its known weak spot is C extensions: the `cpyext` compatibility layer works but costs more than CPython's, so NumPy-class wheels are the ecosystem pain point (the Fastest CPython/PyPyson pages and FAQ document this).
Distribution is an interpreter runtime, not a binary of your program.

pyscriptc differs in the artifact (a compiled binary vs an interpreter runtime) and in refusing, at compile time, the dynamic patterns PyPy handles at run time.

### Nuitka

Nuitka translates a whole Python program into a C-level program that links `libpython`, so the compiled result executes the way CPython does.
It compiles standard Python as-is: no type annotations are needed, and its docs claim full compatibility with Python 3.4 through 3.14 (plus Python 2.6/2.7), with optimizations that never remove compatibility.
Its three modes are `--module` (an importable extension), `--mode=standalone` (a distributable folder with the runtime bundled) and `--mode=onefile`.
The typical use case in its docs is distributing applications.

pyscriptc differs in the input contract and the output: it compiles a typed subset, refuses the rest with a named diagnostic instead of lowering it slowly, and a native binary carries no interpreter except where you opt into the island.

### Cython

Cython compiles `.pyx` (or plain `.py`) files to C, and a C compiler turns that into an extension module that you import back into a CPython session.
Static typing is optional; adding it speeds up hot paths and enables C/C++ interop.
The language is a superset of Python with its own limitations page, and the output is always an extension module, not a standalone program.

pyscriptc differs in output shape (a binary instead of a module for CPython to import) and in making annotations mandatory rather than an accelerator.

### mypyc

mypyc compiles Python modules with standard type hints into C extensions, using mypy for checking and inference.
It is a strict, gradually typed Python variant: dynamic features are restricted to gain performance, and annotations are enforced at runtime with `TypeError` at static/dynamic boundaries.
Output is one extension module per compiled module (or a whole package via `mypycify`), which mypy itself uses to speed up its own codebase.

This is the closest input contract to pyscriptc's: typed Python in, checked before lowering.
The difference is the artifact and the boundary. mypyc produces code that runs inside a CPython process; pyscriptc produces a binary, and unknown or untyped imports route to an embedded CPython island rather than to `Any`-erased compiled code.

### Codon

Codon, from Exaloop, is an ahead-of-time compiler for Python-like source with its own type checker (a modified Hindley-Milner system) and LLVM backend.
Annotations are not required because the compiler infers types and monomorphizes per call site.
It builds native executables and extension modules, and its docs state plainly that it is not a drop-in replacement for CPython: `int` is 64-bit rather than arbitrary precision, static checking forbids monkey-patching and heterogeneous containers, numerics follow C semantics by default, and unimplemented stdlib modules run under a real CPython via `from python import`.

pyscriptc shares the "native binary, no interpreter inside" goal but keeps CPython semantics as the target within the subset: an `int` is a Python `int`, and anything the subset cannot compile is refused with a diagnostic or routed to an embedded CPython, never reinterpreted.

### Numba

Numba is a JIT: you decorate functions with `@numba.jit` (or `@njit`), and LLVM-compiled machine code is produced at first call, with argument types inferred then.
It is aimed at array and numerical code, with deep NumPy integration and CPU/GPU targets, and its `nopython` mode supports a documented subset of Python while object mode catches the rest.
Compilation happens inside a running CPython process; there is no standalone binary.

pyscriptc is ahead-of-time and whole-program: the compile happens once, before distribution, and the output runs with no CPython in the loop outside the island.

### Shed Skin

[Shed Skin](https://shedskin.readthedocs.io/) is an experimental compiler from implicitly statically typed Python to C++.
No annotations are written, but the inference requires each variable to hold one type, and the supported standard-library slice is documented per module.
Output is a CPython extension module or a standalone binary; the project describes itself as experimental.

pyscriptc differs in demanding the types be written (they steer the lowering instead of being inferred once) and in routing what it cannot compile to embedded CPython rather than leaving the program without a path.

### scriptc

[scriptc](https://github.com/vercel-labs/scriptc) (Apache-2.0) is Vercel Labs' experimental compiler for a related problem in a different language: it compiles TypeScript and JavaScript to native executables and WASI modules, with the real `tsc` checking types and unsupported operations fenced with an `SC` diagnostic plus a rewrite hint.
It also ships a `scriptc coverage` command that reports, per construct, what compiles.

pyscriptc is an independent project, but the shape of the problem is the same one scriptc describes for the Node ecosystem: a typed subset lowered through LLVM IR to native code, with everything outside the subset routed to a dynamic engine instead of guessed at.
The architectural difference comes from the languages: scriptc's dynamic fallback runs on an embedded QuickJS, while pyscriptc embeds CPython itself, because Python's runtime introspection is idiomatic in its ecosystem (frameworks read `__annotations__` and build schemas at import time) and only real CPython provides it.
pyscriptc keeps that island as a permanent tier rather than a transition state, and credits scriptc as the closest prior art for the coverage-diagnostic developer experience.

## Choosing

- You want to ship your existing, untyped script as an executable and need full-language behavior: Nuitka's territory.
- You want to run untyped code faster without a build step and accept interpreter-runtime distribution: PyPy.
- You want to speed up a module inside a CPython process: Cython or mypyc.
- You want a native binary from numeric, statically-inferable code and accept its numeric and typing divergences: Codon.
- You want decorated numeric/array functions to run faster inside a running interpreter: Numba.
- You have typed Python and want a native binary that refuses, names, and explains what it cannot compile, with untyped dependencies delegated to an embedded CPython: that is pyscriptc.

## A micro-benchmark, with the code

One task, one machine, one afternoon: count the words of a 44-character sentence repeated 20,000 times, 20 passes, timing the whole loop (`perf_counter`).
This is a dict-update and string-splitting loop, not a numeric kernel, so it favors none of the tools in particular.
The machine is a 6-vCPU Haswell-class VPS; each tool ran the same logic, and the times below are the best of 2-3 runs after warmup.
The code is inline so the comparison can be checked; treat this as an anecdote about tool classes, not a ranking.

Task code (the untyped spelling; the typed spelling adds `str`/`dict[str, int]`/`int` annotations only):

```python
import time


def count_words(text):
    counts = {}
    for word in text.split():
        counts[word] = counts.get(word, 0) + 1
    return counts


def main():
    text = "the quick brown fox jumps over the lazy dog " * 20000
    t0 = time.perf_counter()
    total = 0
    for _ in range(20):
        total += sum(count_words(text).values())
    dt = time.perf_counter() - t0
    print(total, dt)
```

| Tool | What ran | Version | Time (s) | Notes |
| --- | --- | --- | --- | --- |
| CPython 3.14 | same code | 3.14.9 | 0.96 | baseline |
| PyPy | same code, untyped | PyPy 8.0.1 (Python 3.12.15) | 0.67 | warmup included |
| mypyc | typed spelling, compiled to an extension | mypy/mypyc 2.4.1 | 1.04 | little gain on a dict-heavy loop |
| Cython | same code, `cythonize` to an extension | Cython 3.3.1 | 0.72 | |
| pyscriptc | typed spelling, native binary | master `9d6c831` | 0.84 | plane map: all functions native, no island |

A second, numeric-shaped loop (sum `i % 7` over 840,000 elements, 20 passes, typed integer only) shows the class difference between dict-heavy application code and tight numeric loops: numba 0.68.1's `@njit` ran it in 0.038 s against CPython's 1.66 s, about 40x, because the loop lowers to machine code with no container operations; the same dict-heavy task above stays within one order of magnitude for every tool.
numba only reaches numbers like that on code it can type and lower in `nopython` mode; it does not compile the dict/string task at all.

Versions and runner code: the drivers were `python typed.py` (CPython, PyPy, the mypyc and Cython extension modules through a `drive.py` that imports and calls `main()`), and the compiled pyscriptc binary invoked with `--call main`.
The mypyc build required the variable annotation `counts: dict[str, int] = {}` to pass its checker; the untyped spelling failed mypy with `var-annotated`.

No other tool was benchmarked; everything else on this page is from the projects' own documentation.

