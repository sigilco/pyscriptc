# Apps and package strategy: basic apps to first-class support

Date: 2026-10-24. Branch: `docs/apps-strategy`. Research and planning only, no `src/` change.

Owner direction: share pyscriptc with beta testers; most real programs still do not compile. Follow Vercel's scriptc: do not chase PyPI compatibility, build on the CPython builtin and stdlib subset, and support a short blessed list of packages or a pyscriptc-native alternative. The blessed candidates the owner named: `requests`, `fastapi`, `pandas`, `numpy`, `orjson`, `langchain`, `pydantic`.

This plan answers the second half of that question. Another session covers real-life scripts; the overlap is called out in section 8.

## 1. The tiering policy

Four tiers. A package is assigned one tier, and the tier is the promise made to a beta tester.

| tier | meaning | the promise | cost |
| ---- | ------- | ----------- | ---- |
| T1 | compile the real package from source | the real library, unmodified, compiles and matches CPython | highest: every fence in its tree is ours |
| T2 | blessed native pack | the real import name resolves to a curated native pack, subset only, out of tree per the pack format | medium: one C surface plus a conformance suite |
| T3 | pyscriptc-native alternative | a different import name with a CPython-compatible API subset, the user edits the import | low to medium: we own the whole surface |
| T4 | out of scope | not attempted, stated plainly | zero |

The tier assignment is a promise about scope, not about quality. A T3 package is not "worse than the real one"; it is narrower and faster, and the difference is written down.

Two rules make the policy workable:

1. **A tier is per package, not per feature.** `pandas` is one tier for the whole import, not a tier per method. A user who needs the parts we left out is out of scope for that package, not partially in scope.
2. **The compiler never approximates a real package.** When a pack or an alternative covers only a subset, the names outside the subset fence at compile time with the pack name in the reason. This is the `PS2001` precedent already used by the `_orjson` surface, and it is what makes the conformance runner's byte-identical rule meaningful.

This is the same shape as the existing ladder in the compat site (`native`, `pack`, `extern`, `island`, `refused`) but at package granularity instead of import granularity. The decision row is in `.agents/decisions.md`.

## 2. Evidence: how the packages measure today

Measured on this worktree at `5860541` (master tip) with `uv run pyscriptc deps --json` and `uv run pyscriptc coverage <source_root> --json`. Deps versions are the current PyPI releases resolved on 2026-10-24.

### 2.1 Dependency lanes

`pyscriptc deps` resolves each package and classifies it into a lane. `pure` means compile-from-source is possible in principle; `native` means only ABI-specific wheels exist, so a pack or an alternative is the only route.

| package | resolved version | lane | lane reason |
| ------- | ---------------- | ---- | ----------- |
| `orjson` | 3.13.0 | native | only ABI wheels (cp314, cp315) |
| `pydantic` | 2.14.1 | pure (+ `pydantic-core` 2.50.1 native) | pydantic-core is a Rust extension |
| `requests` | 2.34.2 | pure (5 dists) | certifi, charset-normalizer, idna, requests, urllib3 |
| `numpy` | 2.5.4 | native | only ABI wheels (cp314, cp314t, cp315, cp315t) |
| `pandas` | 3.1.0 | native (+ numpy native) | only ABI wheels |
| `fastapi` | 0.162.0 | pure (10 dists) | starlette, pydantic, anyio, opentelemetry-api, idna, typing-* |
| `langchain` | 1.4.7 | pure (33 dists) + 7 native | orjson, ormsgpack, pyyaml, pydantic-core, uuid-utils, xxhash, zstandard native |

`langchain` is the tell: 40 distributions, 7 of them native extensions, and the top-level package is a thin re-export over `langchain-core` and `langgraph`. That is three frameworks deep, not a library.

### 2.2 Fence counts

`pyscriptc coverage` over each unpacked source tree. The counts are fences per tree, not per file, and they include cascades.

| tree | fences | top fence codes | top families |
| ---- | ------ | -------------- | ------------ |
| `certifi` | 3 | PS2002:3 | import-table:2, class-cascade:1 |
| `annotated-types` | 19 | PS1201:13, PS2002:2, PS1104:2 | decorators:13 |
| `charset-normalizer` | 619 | PS2002:267, PS2001:266, PS1001:26 | field-lookup:262, construct-other:120 |
| `idna` | 168 | PS2002:97, PS1001:30, PS2003:20 | name-resolution:55, local-inference:30 |
| `urllib3` | 698 | PS2002:431, PS1106:72, PS1001:47 | name-resolution:130, export-cascade:120 |
| `requests` | 414 | PS2002:267, PS1106:40, PS1001:37 | export-cascade:120, name-resolution:64 |
| `pydantic` | 2565 | PS2002:1210, PS1106:342, PS1104:307 | export-cascade:478, unlowered-type-name:342 |
| `starlette` | 492 | PS2002:267, PS1106:107, PS1104:32 | import-table:165, unlowered-type-name:107 |
| `fastapi` | 908 | PS2002:558, PS1106:114, PS1104:80 | export-cascade:199, import-table:170 |
| `httpx` | 694 | PS2002:412, PS2001:97, PS1001:56 | name-resolution:129, field-lookup:81 |
| `langchain` | 724 | PS2002:442, PS1106:74, PS1103:54 | export-cascade:165, import-table:109 |
| `langchain-core` | crashes | `ir.py` KeyError 'key' | a compiler bug, see section 3.4 |
| `numpy` | n/a | lane native, no source tree | |
| `pandas` | n/a | lane native, no source tree | |

Two observations that decide the strategy.

**The blocking fence is not the library's logic, it is the typing surface.** `PS1104` (dynamic annotation), `PS1106` (unlowered type name), `PS1103` (generic alias) and `PS1201` (decorators) are annotation and decorator fences. `PS2002` (an unlowered construct) dominates the counts, but its `export-cascade` and `field-lookup` families are downstream of the annotation fences: a class whose base is not lowered cascades into every method that reads a field of that base.

**`charset-normalizer` alone is 619 fences for what is, functionally, a byte-to-`str` decoder.** The whole `requests` family is 1902 fences to make one HTTP GET. That is the shape of a library the compiler should not try to compile.

### 2.3 What the archetypes actually need

Two probe programs, written in the shape a beta tester would write.

Web server probe (the `http.server` shape from issue #230):

```python
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body: bytes = json.dumps({"path": self.path}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)
```

4 fences: `PS2002` import-table on `http.server`, `PS2002` class-cascade on `BaseHTTPRequestHandler`, `PS1106` on the `HTTPServer` annotation, and one `PS2002` name-resolution cascade.

Data pipeline probe (csv to JSON aggregation):

```python
import csv
import json
from dataclasses import dataclass

@dataclass
class Row:
    key: str
    value: float

def load(path: str) -> list[Row]:
    out: list[Row] = []
    with open(path, "r") as fh:
        for row in csv.reader(fh):
            out.append(Row(row[0], float(row[1])))
    return out
```

0 fences. `csv.reader`, `float()`, `dataclass`, the `with` on `open` and the loop all lower today.

The difference between the two is the whole story of this plan: **the data pipeline archetype is nearly compilable today; the web server archetype has no server to compile against.** The stdlib gaps for the pipeline are annotation-shaped, and the stdlib gaps for the server are runtime-shaped.

Two findings, both measured while writing the pipeline probe, and both about the same thing: the type system is the gate.

**Finding 1: a bare `list` or `dict` annotation fences the local and every use of it.**

```python
def load(path: str) -> list:      # PS1106 unlowered-type-name
    out: list = []                 # PS1106, then PS2002 name-resolution on every use
```

The same program with `list[Row]` and `dict[str, float]` has 0 fences. This is the highest-leverage single fix for the data archetype: it is a type-system change, not a runtime change, and it removes a fence class that every real script hits.

**Finding 2: `for r in rows` over a `list` of user class instances fences.**

```python
def aggregate(rows: list[Row]) -> dict[str, float]:
    for r in rows:                # PS2002: for over list[Row] is not lowered
        ...
```

The lowerer (`src/pyscriptc/lower/_stmt.py:931-936`) accepts a scalar element, a `list` element, a `dict` element, or a tuple class. A plain class is refused. The same shape with `list[int]`, `list[str]`, `list[float]`, `list[dict[str, float]]` and `list[list[str]]` compiles with 0 fences; only the user-class element fences. Iterating a `dict` (`for k in acc`) and `enumerate` and `zip` all lower.

The workaround is an index loop:

```python
i: int = 0
while i < len(rows):
    r: Row = rows[i]
    ...
    i = i + 1
```

which compiles and runs correctly (verified: a dataclass pipeline compiles and prints `4` for a two-row input). But it is the shape a real pipeline would have to write today, and it is exactly the kind of thing a tester will report as "pyscriptc is not Python". This is issue #481, and it sits in the OOP track next to #446, not in the stdlib track.

Note what this means for the tiering: **the data archetype's blocker is not the stdlib and not the packages, it is the compiler's handling of user classes in containers.** Every T3 alternative in section 3.2 (`pyscriptc-ndarray`, `pyscriptc-frame`, `pyscriptc-val`) also lands in the same territory, because a dataframe holds user-shaped records. The OOP track is therefore on the critical path for the data archetype, not only for the library track.

## 3. Tier assignment

### 3.1 T2: blessed native pack

| package | tier | rationale |
| ------- | ---- | --------- |
| `orjson` | T2 | Already the pack format's worked example. `native_packages.json` points `orjson`, `ujson` and `simplejson` at `pyscriptc-orjson`; the surface is `dumps`/`loads` plus two `OPT_` flags; the native core `ps_json.c` is shared with stdlib `json`. Issue #411 migrates it onto the #377 format. The `deps` lane is already `native` with `pack` set. |

`orjson` is the only package where T2 is already half built, and it is the proof that the format works. No new pack decision is needed for it.

### 3.2 T3: pyscriptc-native alternative

| package | tier | alternative | CPython-compatible subset | how a user switches |
| ------- | ---- | ----------- | -------------------------- | ------------------- |
| `pydantic` | T3 | `pyscriptc-val` (issue #156) | `BaseModel` with a `dataclass`-shaped field set, `model_validate`, `model_dump`, `model_dump_json`, field types `int`, `float`, `bool`, `str`, `bytes`, `list[T]`, `dict[str, T]`, nested models | change the import from `from pydantic import BaseModel` to `from pyscriptc_val import BaseModel`; the decorator and attribute spelling stay |
| `numpy` | T3 | `pyscriptc-ndarray` (#496) | a contiguous typed buffer: `array` of `int64`/`float64`/`int32`/`float32`, `dtype`, `shape`, `ndim`, `size`, `nbytes`, elementwise `+ - * /`, `sum`, `min`, `max`, `mean`, `std`, `reshape`, `astype`, slicing with a step, `tolist()`, `fromlist()` | change the import; `import numpy as np` becomes `import pyscriptc_ndarray as np` |
| `pandas` | T3 | `pyscriptc-frame` (#497) | a columnar frame over `pyscriptc_ndarray`: `DataFrame(dict[str, ndarray])`, `read_csv` (on the closed csv surface), `to_dict`, `to_json`, column selection, `groupby(...).sum()` over one key, `sort_values`, `head`, `shape`, `columns` | change the import; the API is the pandas subset, not the pandas object model |
| `fastapi` | T3 | `pyscriptc-fastapi` (issues #231 to #234) | `FastAPI()`, `@app.get`/`@app.post` with literal paths, `{param}` path segments typed `int` or `str`, query params, `JSONResponse`, `dict`/`list`/`str`/dataclass return, `@dataclass` request bodies, sync handlers only | change the import and the decorator target; the app object is `pyscriptc_fastapi.FastAPI()` |

Why T3 and not T1 for these three.

`pydantic` is 2565 fences, and the fences are `Annotated` metadata, `get_type_hints`, `__init_subclass__`, metaclass-driven model building, and generic aliases. Those are runtime introspection patterns. Issue #86 (build-time annotation introspection) and issue #88 (erase-after-check) describe the escape: keep the metadata through lowering and emit static tables. That is real work, but it is compiler work with a schema flavor, and it will not unblock the other packages on its own.

`numpy` and `pandas` are not compilable at all today: `deps` refuses them at lane `native` because only ABI-specific wheels exist. Compiling the real numpy would mean compiling and shipping the CPython C-API extension, which is the island tier by another name. The T3 route gives the user the thing they actually want from numpy in a compiled program: a contiguous numeric buffer with vectorized arithmetic, where the native compilation actually pays off, because a `list[float]` in CPython is a pointer-chasing box while the compiled buffer is flat memory.

`fastapi` compiles to 908 fences even before starlette's 492 and pydantic's 2565 are counted, and it depends on `anyio` and `opentelemetry-api` for machinery that a sync-only subset does not need. The `pyscriptc-fastapi` compat port already has a designed shape: issues #231 (route decorators), #232 (dataclass request models), #233 (query params and error responses), #234 (the v0 package and the showcase binary), all under the HTTP epic #219 and the tracker #85.

The switch is one import line. That is the property that makes T3 usable by a beta tester: the change to a tester's program is visible in the diff and does not require restructuring their code.

### 3.3 T1: compile the real package

| package | tier | rationale |
| ------- | ---- | --------- |
| `requests` | T1 | Already the epic's subject (#428). The pilot order is `certifi` (3 fences) then `idna` (168) then `requests` (414), with `urllib3` (698) and `charset-normalizer` (619) pulled in by the imports. This is the one package where the answer is "make it work", because it is the most common HTTP client and the pure-Python lane is already open. |

`requests` is T1, but T1 with a caveat that has to be stated honestly to a tester: the compiled `requests` will use the native HTTP client (`urllib.request` on `ps_http.c`, issue #322) rather than urllib3's own transport, or it will fence on the urllib3 connection-pool surface. Either way, `requests` on pyscriptc is a **narrower** `requests`: no proxies, no cookies, no multipart, no streaming uploads.

### 3.4 T4: out of scope

| package | tier | why |
| ------- | ---- | --- |
| `langchain` | T4 | 40 distributions, 7 native extensions, three frameworks deep (`langchain` over `langchain-core` over `langgraph`). The top-level package is a re-export; the real surface is `langchain-core` (724 fences for `langchain` alone, and `langchain-core` crashes the coverage tool today with a `KeyError: 'key'` in `src/pyscriptc/ir.py:80`). A language model agent framework is also the wrong shape for a natively compiled binary: it is I/O bound against a remote API, so native compilation buys nothing over CPython, while every dependency it pulls in costs fences. |

`langchain` is T4 for a second reason: it is not a library, it is a platform. Betting the beta on it means betting the beta on `pydantic` plus `httpx` plus `anyio` plus `pydantic-core` (Rust) plus `langsmith` plus `langgraph`, which is 40 distributions of surface we would be promising to keep compatible. The honest answer to a tester who asks for langchain is "not in the beta, here is the HTTP client and the dataclass models you would need to call the model directly".

### 3.5 Summary

| package | tier | the tester gets | the tester gives up |
| ------- | ---- | -------------- | ------------------ |
| `orjson` | T2 | `dumps`/`loads`, the real import name | `default=`, `Fragment`, other `OPT_` flags |
| `requests` | T1 | the real package, compiled from source | proxies, cookies, multipart, streaming |
| `pydantic` | T3 | `pyscriptc-val`, dataclass-shaped models | `Annotated` metadata, validators, `model_config`, generics, serialization aliases |
| `fastapi` | T3 | `pyscriptc-fastapi`, sync handlers | async handlers, dependency injection, middleware, `BackgroundTasks`, OpenAPI schema, `HTTPException` class hierarchy |
| `numpy` | T3 | `pyscriptc-ndarray`, flat typed buffers | broadcasting, strides, dtypes beyond the four, `np.linalg`, `np.random`, structured dtypes, ufuncs |
| `pandas` | T3 | `pyscriptc-frame`, one-key groupby | the full `DataFrame` API, `Index`, `Series` objects, `merge`, `pivot`, `apply`, time series, `Categorical`, `NaN` semantics |
| `langchain` | T4 | nothing in the beta | everything |

### 3.6 Adjacent packages worth a line

| package | tier | note |
| ------- | ---- | ---- |
| `starlette` | T3 (folded into `pyscriptc-fastapi`) | 492 fences, `import-table:165` on modules the sync subset never touches. Not a separate tier. |
| `uvicorn` | T4 | a pure-Python ASGI server; pyscriptc's server is the compiled binary itself, so there is no server to install |
| `httpx` | T4 for the beta | 694 fences, async-first, `anyio` transport. `requests` covers the sync HTTP client need |
| `sqlalchemy` | T4 | an ORM over a reflection layer; the data archetype needs `sqlite3` and csv, not an ORM. `sqlite3` is reachable through the FFI lane (the sqlite pilot, issue #221) |
| `click` | T4 | `argparse` is already lowered and is the stdlib answer |
| `rich` | T4 | terminal rendering; not on the two archetype paths |
| `pytest` | T4 | a test framework, not a runtime dependency of an app; the beta testers test with CPython |

## 4. Stdlib gaps for the two archetypes, mapped to tickets

### 4.1 Web server archetype

| gap | evidence | ticket | milestone | effort |
| --- | -------- | ------ | --------- | ------ |
| `http.server` is not in the closed import table | `PS2002` import-table on the probe | #230 (registers it), blocked by #229 and #227 | v0.9.0 | M |
| no inbound server runtime at all | the `ps_httpd.c` file does not exist | #229 (thread-per-connection HTTP/1.1) | v0.9.0 | L |
| no inbound surface design | nothing on disk | #227 (the design note) | v0.9.0 | S |
| runtime globals are not thread-safe | `ps_rt.c` keeps exception state in `_Thread_local`, which is safe, but other globals are not | #228 | v0.9.0 | M |
| `socket.socket()` is not in the lowered subset | `PS2001` stdlib:socket on the probe | #229 covers it at the C level; a Python-level `socket` surface is not planned | v0.9.0 | included in #229 |
| `selectors` is not in the closed import table | `PS2002` import-table on the probe | #485: a closed `selectors` subset over the `ps_loop.c` event loop | v0.9.0 | M |
| `ssl` contexts do not lower | `PS2001` on `create_default_context` | #235 (TLS via mbedTLS), #237 (`http.client` keep-alive) | v0.9.0 | L |
| no route decorator lowering | not reachable, `http.server` is fenced first | #231 | v0.9.0 | M |
| no request-model validation | same | #232 | v0.9.0 | M |
| no query params or error responses | same | #233 | v0.9.0 | M |
| no package and no showcase binary | same | #234 | v0.9.0 | L |
| no ASGI-shaped surface | `asyncio` is partial; `async def` handlers do not lower | not planned in the beta. The ASGI shape needs the async runtime (`#179`, `#181`) and an event loop (`#177`), all in Future | Future | XL |
| no WSGI-shaped surface | the CPython WSGI protocol is a callable protocol with an environ dict, which the closed subset cannot express | not planned. The `pyscriptc-fastapi` shape replaces it | v0.9.0 (documented as a non-goal) | 0 |

The honest reading: the web server archetype is a **new runtime**, not a set of stdlib gaps. `urllib.request` already works for the client half (issue #322), `json` works, `http.server` needs 600 to 1000 lines of C that do not exist yet. The fastest route to a first-class web server is the `http.server` closed subset, because it is a familiar spelling and it needs no framework.

### 4.2 Data pipeline archetype

| gap | evidence | ticket | milestone | effort |
| --- | -------- | ------ | --------- | ------ |
| a bare `list` or `dict` annotation fences the local and every use | `PS1106` then `PS2002` name-resolution, measured | #483: bare container annotations lower to a first-class container | v0.8.0 | M |
| `for r in rows` over a `list` of user class instances fences | `PS2002` "for over list[Row] is not lowered", measured | #481: iterate a container of user class instances | v0.8.0 | M |
| `sqlite3` is not in the closed import table | `PS2002` import-table on the probe | reachable today through the FFI lane with an `ffi.toml` manifest (the sqlite pilot, issue #221), not through the stdlib surface | v0.8.0 (documented) | S to wire |
| `array` is not in the closed import table | `PS2002` import-table on the probe | #496 covers it as the `pyscriptc-ndarray` surface | v0.9.0 | L |
| `statistics` is not in the closed import table | `PS2002` import-table on the probe | #486: a closed `statistics` subset (mean, median, stdev, variance, pstdev, pvariance, quantiles) | v0.9.0 | M |
| `heapq` is not in the closed import table | `PS2002` import-table on the probe | #487: a closed `heapq` subset (heappush, heappop, heapify, nsmallest, nlarge) | v0.9.0 | M |
| `csv.DictReader` is not lowered | `PS2001` on the probe | #488: `DictReader` and `DictWriter` over the existing `ps_csv.c` | v0.9.0 | M |
| `struct` is partial | `calcsize`, `unpack_from`, `pack_into` are `PS2001` | #489: the `struct` surface completion | v0.9.0 | M |
| `itertools` is partial | `takewhile`, `dropwhile`, `zip_longest`, `groupby`, `permutations`, `combinations`, `pairwise`, `batched`, `tee`, `compress`, `product` are `PS2001` | #490: the finite eager `itertools` subset | v0.9.0 | M |
| `collections` is partial | `ChainMap` is `PS2001`; `OrderedDict` and `defaultdict` need a typed annotation to exist at all | #491: the `collections` value form | v0.9.0 | M |
| `bisect` is partial | `insort`, `insort_left`, `insort_right` are `PS2001` | #492: the `bisect` surface completion | v0.9.0 | S |
| `math` is partial | `fsum`, `factorial`, `comb`, `perm`, `isqrt`, `degrees`, `radians`, `asin`, `sinh`, `cosh`, `tanh`, `copysign`, `fmod`, `cbrt`, `prod`, `erf`, `erfc`, `lgamma` are `PS2001`; `log` with a base is `PS2003`; `floor(3)` is `PS1003` | #493: the `math` surface completion | v0.9.0 | M |
| `random` is partial | `choices`, `sample`, `gauss`, `normalvariate`, `expovariate`, `randbytes`, `Random` are `PS2001` | #494: the `random` surface completion | v0.9.0 | M |
| no contiguous numeric buffer in the IR | the FFI vocabulary is closed; a numeric array travels as `list[float]` | #372 (the IR buffer type, gated on a canonicity decision) plus #496 | v0.9.0 | XL |
| no dataframe surface | not reachable | #497 | v0.9.0 | XL |
| `json.dumps` writes to files only through `load`/`dump` being unfenced | `PS2001` on `dump` and `load`; `ensure_ascii=False` and `JSONDecodeError` are `PS2001`; `loads` on `bytes` is `PS1003` | #495: the `json` file surface completion | v0.9.0 | M |

The completion issues are deliberately small and mechanical. `bisect` is S. `statistics`, `heapq`, `csv.DictReader` are M. They are the kind of work a session slot can absorb in a day, and each one removes a `PS2001` that a tester hits in the first ten lines of their script.

### 4.3 The compiler-level gaps that block the whole list

| gap | evidence | ticket | milestone | effort |
| --- | -------- | ------ | --------- | ------ |
| annotation surface blocks every library | `PS1104`, `PS1106`, `PS1103`, `PS1201` dominate the fence counts in section 2.2 | #412 (unions and generic aliases as erased annotations), #413 (collections.abc in annotations and TypeVar handling), and the typing items in the dispatch queue #453 | v0.8.0 to v0.9.0 | M each |
| the coverage tool crashes on a real library tree | `langchain-core` raises `KeyError: 'key'` at `src/pyscriptc/ir.py:80` | #484: the coverage tool must not crash on a dependency tree | v0.8.0 | S |
| the dynamic attribute protocol is refused by design | `PS4201`, `PS4202`, `PS4203`, landed in PR #461 | recorded, not planned | - | - |

## 5. Reference apps

Two apps. Both must compile to one self-contained binary, and both must match CPython on a recorded receipt. These are the beta showcase, the acceptance tests, and the perf receipts. The tickets are #499 (the JSON service) and #498 (the log pipeline).

### 5.1 App A: the JSON service

A small HTTP service: three routes, JSON in and out, one dataclass request model, one file-backed read. This is the app a tester writes in an afternoon.

```python
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pyscriptc_fastapi import FastAPI, JSONResponse
```

The scope: `GET /health` returning `{"status": "ok"}`, `GET /items/{id}` returning one item, `POST /items` accepting a dataclass body and returning the created item. The server is the compiled binary itself; there is no uvicorn, no gunicorn, no supervisor.

Acceptance tests:

1. The program compiles with 0 fences (`pyscriptc coverage` reports 0).
2. The binary serves the three routes over loopback; the responses match CPython byte for byte, including the JSON key order and the float formatting.
3. A `curl` loop of 1000 requests returns 1000 identical bodies.
4. The binary is one file, with no `libpython` and no `.so` beside it (`ldd` shows only the system libraries).
5. The CPython-vs-compiled request-per-second number is recorded in the milestone description.

Current baseline, measured on this worktree: **does not compile**. 4 fences on the `http.server` probe (section 2.3), and `http.server` is not in the closed import table at all. The path to compiling it is #227, #228, #229, #230, then #231, #232, #233, #234. That is one L, two M, one S and one L of work, which is one release.

### 5.2 App B: the log pipeline

A CPU-bound pipeline: read a CSV of access logs, aggregate by status class and by path, compute percentile latencies, write a JSON report. This is the app where native compilation pays.

Acceptance tests:

1. The program compiles with 0 fences.
2. The compiled binary and CPython produce byte-identical reports on the same input file.
3. The perf receipt: the compiled binary is faster than CPython on the same input, by a recorded factor, and the factor is recorded in the milestone description. The current baseline for the data path is the `benchmarks/baseline` suite: `json-roundtrip` 4.0x CPython, `struct-pack` 4.3x, `deque-ring` 5.2x, geomean 4.5x on this host (2 runs, macOS arm64). The pipeline app must beat CPython on its own workload, not on the suite's.
4. The memory receipt: peak RSS at or below 1.25x CPython, the existing baseline rule.
5. The pipeline must not need `numpy` or `pandas` to be fast. That is the point of the archetype: the stdlib subset plus native compilation is the answer, and the T3 `pyscriptc-ndarray` and `pyscriptc-frame` surfaces are the upgrade path for the workloads that need them.

Current baseline, measured on this worktree: **the probe compiles with 0 fences** (section 2.3). The pipeline shape is close to working today. The blockers are the two type-system fences (a bare `list` fences; a `for` over a container of user class instances fences and forces an index loop), the missing stdlib surfaces (`statistics`, `heapq`, `csv.DictReader`), and the fact that the perf receipt for a real pipeline workload does not exist yet.

This asymmetry is the strategic finding of this plan. **The data pipeline archetype is a documentation and surface-completion job. The web server archetype is a runtime-development job.** The beta should lead with the pipeline and schedule the server.

### 5.3 Beta showcase order

1. The log pipeline (#498), compiled, with its perf receipt. It ships as the first beta showcase because it works soonest.
2. The JSON service (#499), compiled, with its loopback receipt. It ships when the `ps_httpd.c` runtime lands.

## 6. Ordered grind plan

Phases, effort, receipts, and where each interleaves with the in-flight queue (#453).

### Phase 0: unblock the measurement (S, v0.8.0)

| item | ticket | effort | receipt |
| ---- | ------ | ------ | ------- |
| the coverage tool does not crash on a dependency tree | #484 | S | `pyscriptc coverage` over the `langchain-core` tree exits 0 and reports a fence count |
| bare container annotations lower | #483 | M | the `list`/`dict` probe compiles with 0 fences; the `scripts_corpus` R1 rate does not regress |
| a `for` loop over a container of user class instances lowers | #481 | M | the `for r in rows` shape compiles with 0 fences; the log pipeline probe drops its index loop |

#484 is first because a measurement tool that crashes on a real library tree hides exactly the evidence the beta decision needs. #483 is second because it is the highest-leverage single fence class for the data archetype. #481 is third because it is the one shape that makes the data archetype feel like Python: the current workaround is an index loop, and a tester will call that a bug.

Interleaves with #453: #484, #483 and #481 are not in the queue. They are small and independent, so they ride along any window that has a slot. #483 and #481 touch the lowerer, so they must not land inside the generator wave (#433 to #450) without a rebase check. #481 sits next to #446 (the `iter` protocol) in the OOP track and should be sequenced with it, not against it.

### Phase 1: the stdlib surfaces for the data archetype (M each, v0.9.0)

#486 (`statistics`), #487 (`heapq`), #488 (`csv.DictReader` and `DictWriter`), #489 (`struct`), #490 (`itertools`), #491 (`collections`), #492 (`bisect`), #493 (`math`), #494 (`random`), #495 (`json` file surface).

Receipt: the log pipeline reference app (#5.2) compiles with 0 fences and matches CPython byte for byte; the `benchmarks/baseline` suite gains a `pipeline` workload; the perf number is recorded.

Interleaves with #453: this phase is independent of the language track and can run in parallel with the generator and OOP waves. It is the natural home for a session slot that the language track does not need. Each issue is M and touches `src/pyscriptc/stdmore.py` and `src/pyscriptc/restlower/`, so two of them in flight at once is the practical limit (merge conflicts in `stdmore.py`).

### Phase 2: the HTTP server runtime (L, v0.9.0)

#227 (design note, S), #228 (thread safety, M), #229 (`ps_httpd.c`, L), #230 (the `http.server` surface, M).

Receipt: the `http.server` probe compiles with 0 fences; a fixture program serves JSON and is verified with a concurrent `curl` loop in CI; the boxed ledger window at the boundary shows no change.

Interleaves with #453: this phase is the long pole and is entirely independent of the language track. It should start now, in parallel, because it is the only path to the web archetype. #235 (TLS) and #237 (`http.client`) are the follow-ups and are also independent of the language track, but they land after the plaintext milestone.

### Phase 3: the compat ports (M to L, v0.9.0)

#231, #232, #233 (M each), #234 (L), #156 (`pyscriptc-val`, L), #496 (`pyscriptc-ndarray`, L), #497 (`pyscriptc-frame`, XL).

Receipt for the fastapi port: the JSON service reference app compiles, serves over loopback, and matches CPython byte for byte; the request-per-second receipt is recorded.

Receipt for the val pack: a `pyscriptc-val` pack exists with a `pack.toml`, a `.pyi` surface and a conformance suite, and the conformance runner (#378) passes against real pydantic under CPython.

Receipt for the ndarray and frame surfaces: a compiled buffer with vectorized arithmetic beats CPython's `list[float]` loop by a recorded factor; the frame's `groupby` matches CPython's output on the fixture.

Interleaves with #453: #231 to #234 depend on #230 (phase 2) and on the dataclass work (#452) for the request models. #156 depends on the dataclass and field work (#444, #452). #496 and #497 depend on #372, the IR buffer type, which is gated on a canonicity decision and therefore on the frozen-gate discipline (`IR_HASHES.json` A/B byte-identical regeneration).

### Phase 4: the T1 package that matters (M to L, v0.8.0 and v0.9.0)

#424 (reach pruning), #408 (import-name map), #409 (the certifi receipt), #410 (`importlib.resources`), #406 (refuse lane), #403 (root seam), #378 (conformance runner), then #235 and #237.

Receipt: a `requests`-dependent program compiles and matches CPython, per issue #409's shape extended from `certifi` to `requests`.

Interleaves with #453: this phase is already in the queue as the library track, and the queue says the `requests` family and the wheel-library work take priority over the rest of the language track. This plan does not reorder that. It says the `requests` family is the one T1 package in the blessed list, so it should finish, not be interrupted.

### Phase 5: the pack format (M each, v0.7.0 and v0.8.0)

#454 (pack search and install), #455 (bind lowering to pack manifests), #456 (wheel builder), #457 (ctypes loader), #458 (the pure-library pilot), #459 (manifest validation), #411 (the orjson migration).

Receipt: the `orjson` pack installs from a wheel into a clean venv, imports under CPython through the ctypes loader, and the conformance suite passes against real orjson.

Interleaves with #453: already in the queue. #411 is in flight.

### Phase 6: the `requests` real test (M to L, v0.9.0)

The `requests` family compiles, and the compile surfaces the narrower transport: the native HTTP client rather than urllib3's pool.

Receipt: a program that does a real loopback GET through the compiled `requests` matches CPython's status, headers and body, and the divergence (no proxy, no cookie, no multipart) is written into `docs/reference/divergences.md`.

### Phase 7: what is not in the beta (0)

`langchain` (T4). `uvicorn`, `httpx`, `sqlalchemy`, `click`, `rich`, `pytest` (T4). The ASGI and WSGI shapes (documented as non-goals).

The only thing owed to a tester who asks for one of these is a plain answer and the closest thing that does work.

## 7. Milestone placement

| release | theme | what lands |
| ------- | ----- | ---------- |
| v0.7.0 | libraries are consumable | the pack format children (#454 to #459), #411, the language-wave items already queued |
| v0.8.0 | the measurement and the type surface hold up | #484 (coverage does not crash), #483 (bare container annotations), #481 (the user-class container loop), #466, #467, #469, #468, the library-track items (#424, #408, #409, #410, #406, #403) |
| v0.9.0 | basic apps are first class | the HTTP server runtime (#227, #228, #229, #230), the compat ports (#231 to #234, #156, #496, #497), the stdlib surfaces (#485 to #495), #372, #235, #237 |
| Future | the rest | async, ASGI, the `langchain` shaped work, everything T4 |

The beta bar under `.agents/plans/2026-10-23-v060-beta-release.md` needs at least 2 user-visible features since the last stable. The JSON service and the log pipeline together clear that bar for the next beta cut.

## 8. Overlap with the other session

The other session covers real-life scripts. The overlap is exactly one thing: the **type surface**. A real script and a real library hit the same fences, in the same order, for the same reason (an annotation that does not lower). The fence families that dominate the `scripts_corpus` report (name-resolution 22 scripts, construct-other 13) and the ones that dominate the library census (`PS1104`, `PS1106`, `PS1103`, `PS1201`) are the same underlying problem seen from two sides.

The recommendation is that the two sessions agree on one thing: the next language-track investment is the annotation surface, not any single library. If the other session lands a fix in that area first, this plan's phase 0 shrinks by one issue.

One thing this plan adds that the scripts session cannot: the two reference apps. A script corpus measures the median program; a reference app measures a program a tester wrote on purpose, with the acceptance tests and the perf receipt that the beta milestone needs.

## 9. Risks, stated plainly

| risk | evidence | mitigation |
| ---- | -------- | ---------- |
| the type surface is bigger than the estimate | `pydantic` alone is 2565 fences and `PS1104`/`PS1106`/`PS1103`/`PS1201` dominate across every tree measured | the tiers are scoped so that only `requests` needs the full surface; the T3 alternatives are designed to avoid it |
| the web server is late | `ps_httpd.c` does not exist; #229 is L and needs #227 and #228 first | ship the data pipeline first; the server ships in v0.9.0 |
| `pyscriptc-fastapi` promises more than it delivers | 908 fences in real fastapi, 492 in starlette | the port ships a **closed** surface, and the names outside it fence at compile time |
| the ndarray surface becomes a second compiler | #372 is XL and gated on a canonicity decision | the first milestone is a flat buffer with elementwise arithmetic, not a numpy clone; anything more is a recorded non-goal |
| the coverage tool hides failures | the `langchain-core` crash is a live example | #484 in phase 0, and the coverage tool must exit 0 on every dependency tree in the beta list |
| a tester assumes T1 means full compatibility | `requests` loses proxies, cookies, multipart | the narrowing is written into `docs/reference/divergences.md` in the same PR |

## 10. The measurement recipe

Every number in this plan was produced with these commands, so any reader can reproduce it.

```bash
# the dependency lanes for a package
uv run pyscriptc deps <project-dir> --json

# the fence census for one dependency's source tree
uv run pyscriptc deps <project-dir> --json | jq -r '.packages[] | select(.source_root) | "\(.name) \(.source_root)"'
uv run pyscriptc coverage <source_root>/<package_dir> --json

# the archetype probes
uv run pyscriptc coverage .agents/probes/web_server.py --json
uv run pyscriptc coverage .agents/probes/log_pipeline.py --json

# the perf baseline
uv run python -m benchmarks.baseline.run --runs 5 --workloads json-roundtrip,struct-pack,deque-ring
```

The two archetype probes are committed under `.agents/probes/` so the numbers are re-measurable and the beta decision can be re-run against a newer compiler.

## 11. The ticket index

Every ticket this plan opens, with its milestone and effort. All carry the `enhancement` label and are on the project board with their horizon, effort, track and iteration set.

| ticket | what | milestone | effort | horizon |
| ------ | ---- | --------- | ------ | ------- |
| #481 | lower: a `for` loop over a container of user class instances | v0.8.0 | M | Current |
| #483 | lower: a bare `list` or `dict` annotation | v0.8.0 | M | Current |
| #484 | fix(coverage): the coverage tool must not crash on a dependency tree | v0.8.0 | S | Current |
| #498 | example: the log pipeline reference app | v0.8.0 | M | Current |
| #485 | stdlib: a closed `selectors` subset | v0.9.0 | M | Next |
| #486 | stdlib: a closed `statistics` subset | v0.9.0 | M | Next |
| #487 | stdlib: a closed `heapq` subset | v0.9.0 | M | Next |
| #488 | stdlib: `csv.DictReader` and `csv.DictWriter` | v0.9.0 | M | Next |
| #489 | stdlib: `struct.calcsize` and `struct.unpack_from` | v0.9.0 | M | Next |
| #490 | stdlib: the finite eager `itertools` subset | v0.9.0 | M | Next |
| #491 | stdlib: `OrderedDict` and `defaultdict` in the value form | v0.9.0 | M | Next |
| #492 | stdlib: `bisect.insort` and friends | v0.9.0 | S | Next |
| #493 | stdlib: the `math` surface completion | v0.9.0 | M | Next |
| #494 | stdlib: the `random` surface completion | v0.9.0 | M | Next |
| #495 | stdlib: the `json` file surface | v0.9.0 | M | Next |
| #496 | pyscriptc-ndarray, the numpy tier (T3) | v0.9.0 | L | Later |
| #497 | pyscriptc-frame, the pandas tier (T3) | v0.9.0 | XL | Later |
| #499 | example: the JSON service reference app | v0.9.0 | L | Next |

The tickets this plan reuses, unchanged: #227, #228, #229, #230 (the HTTP server chain), #231 to #234 (the fastapi port), #156 (`pyscriptc-val`, the pydantic tier), #372 (the IR buffer type), #235 and #237 (TLS and `http.client`), #424, #408, #409, #410, #406, #403 and #378 (the library track), #454 to #459 and #411 (the pack format), #412 and #413 (the annotation surface), #444 and #452 (the dataclass work), #446 (the `iter` protocol).
