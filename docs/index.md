---
title: pyscriptc
description: Compile annotated Python to a native binary.
---

<p align="center"><img src="/assets/logo-256.png" width="320" alt="pyscriptc"></p>

# pyscriptc

Compile annotated Python to a native binary.

[Get started](/getting-started/) · [GitHub](https://github.com/sigilco/pyscriptc) · [PyPI](https://pypi.org/project/pyscriptc/)

<p align="center">
<a href="https://buy.polar.sh/polar_cl_Mv1gdlG7bw3I70EC9IHtfeSHJj4PEKvA7JAUz23CFhj"><img alt="Sponsor" src="https://img.shields.io/badge/sponsor-%2330363D.svg?style=for-the-badge&logo=GitHub-Sponsors&logoColor=%23EA4AAA"></a>
</p>

For agents: [llms.txt]({{PYSC_DOCS_URL}}/llms.txt), [llms-full.txt]({{PYSC_DOCS_URL}}/llms-full.txt)

## Where it differs from CPython

pyscriptc compiles a typed subset of Python to machine code.
Anything the subset cannot prove is refused with a numbered diagnostic (`PSxxxx`) that names the
line, or routed to an embedded CPython island inside the same binary.
The rules, the numbered divergences from CPython and the known gaps are in
[Divergences from CPython](/reference/divergences/) and [The Python subset](/guide/python-subset/);
the live per-module and per-library status is on the [compat site]({{PYSC_COMPAT_URL}}).

Two things to know before you plan around it:

- Annotations are required. A parameter or local without one stops the build (`PS1001`).
- Code outside the subset does not fail silently, but it does not become native either: it runs on
  the island, at interpreter speed, and every call crossing the boundary pays a marshalling cost.
  [Libraries and fallback mode](/guide/libraries/) states what the two planes cost.

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
  - [Comparison with other tools](/guide/comparison/)
- [CLI reference](/reference/cli/)
  - [Diagnostic codes](/reference/diagnostics/)
  - [Mobile apps](/guide/mobile/)
  - [Stability and contracts](/reference/stability/)
