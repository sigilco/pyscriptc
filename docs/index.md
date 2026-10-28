---
title: pyscriptc
description: Compile annotated Python to a native binary.
---

<p align="center"><img src="/assets/logo-256.png" width="320" alt="pyscriptc"></p>

# pyscriptc

Compile annotated Python to a native binary.

[Get started](/getting-started/) · [GitHub](https://github.com/sigilco/pyscriptc) · [PyPI](https://pypi.org/project/pyscriptc/)

<p align="center">
<a href="https://buy.polar.sh/polar_cl_Mv1gdlG7bw3I70EC9IHtfeSHJj4PEKvA7JAUz23CFhj"><img alt="Sponsor" src="https://img.shields.io/badge/Sponsor-polar-crimson"></a>
</p>

For agents: [llms.txt]({{PYSC_DOCS_URL}}/llms.txt), [llms-full.txt]({{PYSC_DOCS_URL}}/llms-full.txt)

## What it does

- **Type-first**: pyscriptc compiles a Python subset that follows strict rules: typed, with no shims or dynamic runtime emulation for untyped Python.
- **Native output**: Python code is compiled to machine code via LLVM. The result has no interpreter inside. Ship a single file and cross-compile to your target platform.
- **Gradual adoption**: code that doesn't fit in the subset can be wrapped in an in-process embedded CPython island (`libpython`). You can also integrate compiled code via bindings.
- **Clear debugging**: anything the subset does not prove is a fence with a diagnostic code (`PSxxxx`).

## Try it now

```bash
uv tool install pyscriptc
pyscriptc compile examples/sum_squares.py --run --call sum_squares --args '[10]' -o /tmp/sum_squares
```

That prints `285`, the same value CPython returns, from a native binary.

## Read next

- [Getting started](/getting-started/)
- [Examples](/examples/)
- [The Python subset](/guide/python-subset/)
  - [Using libraries](/guide/libraries/)
- [CLI reference](/reference/cli/)
  - [Diagnostic codes](/reference/diagnostics/)
  - [Mobile apps](/guide/mobile/)
  - [Stability and contracts](/reference/stability/)
