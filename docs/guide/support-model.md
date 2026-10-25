---
title: The Python subset
description: What pyscriptc compiles today, what it refuses, and where the live status lives.
---

pyscriptc compiles the typed subset of Python 3.14.
Native compilation is the ceiling and the CPython island is the floor; the compiler provides no shims or dynamic runtime emulation for untyped Python.
When a construct is outside the typed subset and not routed to the island floor, compilation stops with a `PSxxxx` fence that names the exact line, it never guesses.

## Support model

- **Typed code only**: functions and variables carry annotations, and a type-checker preflight ([pyrefly](https://pyrefly.org/)) runs before lowering.
- **Island is the floor**: when a script touches untyped third-party libraries, dynamic reflection, or unlowered modules, execution routes through the in-process CPython island runtime (`libpython`).
- **Native is the ceiling**: when a script uses lowered standard-library modules, explicit annotations, and scalar constructs, it compiles to a standalone native binary with no interpreter runtime.
- **Two unidirectional seams**: compiled to island in-process, and island to compiled via `pyscriptc --emit binding`. No compiled to island to compiled call chain exists.
- **Honest refusals**: anything the subset does not prove stops at a named fence (`PSxxxx`); the compiler never guesses.

## Where the live status lives

The exact, per-construct, per-builtin, per-module and per-library status is tracked on the compat site, not in this page, so it cannot go stale: [pyscriptc compat site]({{PYSC_COMPAT_URL}}).

The compat site is fed by the [nightly CI workflow](https://github.com/sigilco/pyscriptc/actions/workflows/nightly.yml), which scores the visible corpus, records the boxed ledger window, and refreshes the site data files (`compat/data/compat.json`, `compat/data/ladder.json`, `compat/data/what_will_fail.json`, `compat/data/support.json`).
Internal developer notes and reference documentation link to these data files rather than embedding volatile counts.

Deliberate differences from CPython are numbered in [Divergences](../reference/divergences.md).

## HTTP

`urllib.request` and `urllib.error` lower to a native client (`ps_http.c`): `urlopen` with `data`, `timeout`, headers and method via `Request`, redirects and error types over the `OSError` chain.
`https://` raises `OSError` until TLS lands, `http.client` and `http.server` are not lowered, and the rest of the module fences.
The shape and its divergences are on the [HTTP](../guide/http.md) page; the remaining network work (TLS, `http.client`, inbound servers, the event loop) is tracked on epic [#219](https://github.com/sigilco/pyscriptc/issues/219).

## Real scripts

The compiler is measured against third-party scripts, not only against code written for it: `benchmarks/real_scripts/` holds real-world CLI and automation scripts vendored from public repositories at pinned commits, and a nightly lane scores them the way the rest of the ladder is scored.
A script that stops at a fence is a signal about the compiler, so the corpus scripts are never edited to make them compile.

Common failure modes encountered when a script is outside the native ceiling:

- An assignment without an annotation (`files = os.listdir(d)`) fences with `PS1001`, and every later read of the name fences with `PS2002`; the first fence is the cause and the rest follow from it.
- A keyword argument to a stdlib call (`shutil.copy(src, dst, follow_symlinks=False)`, `subprocess.run(args, capture_output=True)`) fences with `PS2003`.
- A module outside the closed import table (`platform`, `calendar`, `zipfile`, `tarfile`, `sqlite3`) fences with `PS2002` on the import.

The first-class script bar and its beta-readiness checklist are documented in [First-class scripts](../reference/first-class-scripts.md).
The per-module gap list, generated from the same corpus into `compat/data/what_will_fail.json`, is documented in [What will fail](../reference/what-will-fail.md); until then the first fence of a run, `pyscriptc compile FILE`, and `pyscriptc coverage FILE` name the exact gap in your script.
The grind plan and its tickets are #500 to #511; the measurement tool is `uv run python -m benchmarks.real_scripts.measure`.

## Checking your own code

Run `pyscriptc coverage FILE` to get a per-line fence report before compiling, or `pyscriptc explain PS2001` to look up what a fence code means and the smallest rewrite that clears it.

`pyscriptc coverage` needs no C toolchain, so it is the cheapest way to find out whether a script is inside the subset.
