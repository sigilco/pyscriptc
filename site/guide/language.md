---
title: The Python subset
description: What pyscriptc compiles today and what it refuses.
---

pyscriptc compiles a deliberately strict subset of Python.
When a construct is not supported, compilation stops with a `PSxxxx` fence that names the exact line, it never guesses.

## Support model

- Typed code only: functions and variables carry annotations, and a type-checker preflight ([pyrefly](https://pyrefly.org/)) runs before lowering.
- The supported surface is builtins, standard-library modules, and language features that the compiler can prove, one construct at a time.
- Anything the subset does not prove stops at a named fence (`PSxxxx`); the compiler never guesses.
- In the future, code that does not compile can run in the CPython runtime instead (the fallback tier); that runtime is not bundled and is expected on the host.

The exact, live per-construct, per-builtin, per-module and per-library status is tracked on the compat site rather than in this page, so it cannot go stale: [pyscriptc compat site](https://pyscriptc-compat.pages.dev) (placeholder URL, `PYSC_COMPAT_URL`; the site is not deployed yet).

The in-repo reference is [`docs/supported.md`](https://github.com/sigilco/pyscriptc/blob/master/docs/supported.md).

## Checking your own code

Run `pyscriptc coverage FILE` to get a per-line fence report before compiling, or `pyscriptc explain PS2001` to look up what a fence code means and the smallest rewrite that clears it.
