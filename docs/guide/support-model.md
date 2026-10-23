---
title: The Python subset
description: What pyscriptc compiles today, what it refuses, and where the live status lives.
---

pyscriptc compiles a deliberately strict subset of Python.
When a construct is not supported, compilation stops with a `PSxxxx` fence that names the exact line, it never guesses.

## Support model

- Typed code only: functions and variables carry annotations, and a type-checker preflight ([pyrefly](https://pyrefly.org/)) runs before lowering.
- The supported surface is builtins, standard-library modules, and language features that the compiler can prove, one construct at a time.
- Anything the subset does not prove stops at a named fence (`PSxxxx`); the compiler never guesses.
- Code that does not compile can run in the CPython runtime instead (the fallback tier); that runtime is not bundled and is expected on the host.

## Where the live status lives

The exact, per-construct, per-builtin, per-module and per-library status is tracked on the compat site, not in this page, so it cannot go stale: [pyscriptc compat site]({{PYSC_COMPAT_URL}}).

The compat site is fed by the [nightly CI workflow](https://github.com/sigilco/pyscriptc/actions/workflows/nightly.yml), which scores the visible corpus, records the boxed ledger window, and opens a bot PR that refreshes the site data file from the tree.
The in-repo snapshot of the same data, regenerated from the compiler by `scripts/gen_support_table.py`, lives in the developer notes: [`.agents/docs/support/supported.md`](https://github.com/sigilco/pyscriptc/blob/master/.agents/docs/support/supported.md) (builtins and standard library) and [`.agents/docs/support/compat-top100.md`](https://github.com/sigilco/pyscriptc/blob/master/.agents/docs/support/compat-top100.md) (top-100 libraries).
Both are generated output, not hand-edited pages.

Deliberate differences from CPython are numbered in [Divergences](../reference/divergences.md).

## HTTP

`urllib.request` and `urllib.error` lower to a native client (`ps_http.c`): `urlopen` with `data`, `timeout`, headers and method via `Request`, redirects and error types over the `OSError` chain.
`https://` raises `OSError` until TLS lands, `http.client` and `http.server` are not lowered, and the rest of the module fences.
The shape and its divergences are on the [HTTP](../guide/http.md) page; the remaining network work (TLS, `http.client`, inbound servers, the event loop) is tracked on epic [#219](https://github.com/sigilco/pyscriptc/issues/219).

## Checking your own code

Run `pyscriptc coverage FILE` to get a per-line fence report before compiling, or `pyscriptc explain PS2001` to look up what a fence code means and the smallest rewrite that clears it.

`pyscriptc coverage` needs no C toolchain, so it is the cheapest way to find out whether a script is inside the subset.
