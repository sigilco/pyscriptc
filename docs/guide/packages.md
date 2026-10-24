---
title: Packages and apps
description: Which PyPI packages pyscriptc supports, in which tier, and what the beta testers get.
---

pyscriptc does not chase compatibility with all of PyPI.
It builds on the CPython builtin and standard-library subset, and it gives a short, blessed list of packages a stated surface with one of four tiers.
The tier is the promise: it says what you get, what you give up, and how the surface is kept honest.

## The tiers

| tier | meaning | what you get |
| ---- | ------- | ------------ |
| T1 | compile the real package from source | the real library, unmodified, matching CPython |
| T2 | a blessed native pack | the real import name, a subset surface, natively compiled |
| T3 | a pyscriptc-native alternative | a different import name with a CPython-compatible API subset; you change one import line |
| T4 | out of scope | not attempted, said plainly |

Two rules bind the tiers.
A tier is per package, never per feature: a part of a package that is not in its tier's surface is not supported, it is not "partially supported".
And the compiler never approximates a real package: every name outside a pack's or an alternative's surface stops at a compile-time fence that names the package, so what is supported is exactly what is written down.

## The blessed list

| package | tier | you get | you give up |
| ------- | ---- | ------- | ----------- |
| `requests` | T1 | the real package, compiled from source | proxies, cookies, multipart, streaming uploads; the transport is the native HTTP client, not urllib3's pool |
| `orjson` | T2 | `dumps` and `loads` under the real import name | `default=`, `Fragment`, the `OPT_` flags beyond the two shipped |
| `pydantic` | T3 | `pyscriptc_val`: dataclass-shaped models, `model_validate`, `model_dump` | `Annotated` metadata, validators, `model_config`, generics |
| `fastapi` | T3 | `pyscriptc_fastapi`: sync handlers, route decorators, path and query params, JSON responses | async handlers, dependency injection, middleware, OpenAPI schema |
| `numpy` | T3 | `pyscriptc_ndarray`: a flat typed buffer with vectorized arithmetic | broadcasting, strides, dtypes beyond the four, `linalg`, ufuncs |
| `pandas` | T3 | `pyscriptc_frame`: a columnar frame with one-key `groupby` | `Series` objects, `merge`, `pivot`, `apply`, time series, `NaN` semantics |
| `langchain` | T4 | nothing, by decision | everything |

`langchain` is T4 because it is three frameworks deep (`langchain` over `langchain-core` over `langgraph`), it pulls 40 distributions with 7 native extensions, and it is I/O bound against a remote API, so native compilation buys nothing over CPython.
The honest answer to a tester who asks for it is the HTTP client and the dataclass models you would use to call the model directly.

The adjacent packages a tester might expect are T4 as well: `uvicorn` (the compiled binary is the server, there is nothing to install), `httpx` (async-first, `requests` covers the sync need), `sqlalchemy` (an ORM; the data path is `sqlite3` and csv), `click` (`argparse` is the standard-library answer), `rich`, `pytest`.

## The two app archetypes

The tiering is measured against two shapes of program.

**The CPU-bound data pipeline** (read a CSV, aggregate, write JSON) compiles today.
The gaps that remain are type-system fences (a bare `list` annotation, a `for` loop over a container of user class instances) and missing standard-library surfaces (`statistics`, `heapq`, `csv.DictReader`).
This is the first beta showcase.

**The web server** (`http.server`, a JSON service) does not compile yet.
There is no server runtime to compile against: `http.server` is not in the closed import table, and the inbound HTTP server is tracked on epic [#219](https://github.com/sigilco/pyscriptc/issues/219).
This is the second beta showcase, after the server runtime lands.

## Where the status lives

Per-construct, per-module and per-library status is on the [compat site]({{PYSC_COMPAT_URL}}), which is generated from the compiler and cannot drift from it.
The strategy behind the tiers, the fence counts per package tree, and the ordered plan are in the repository: `.agents/plans/2026-10-24-apps-and-package-strategy.md` and the epic issues [#428](https://github.com/sigilco/pyscriptc/issues/428) (pure-Python libraries) and [#430](https://github.com/sigilco/pyscriptc/issues/430) (packs).
