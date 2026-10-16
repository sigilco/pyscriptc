# HTTP client seam design

Design survey for the HTTP seam, report only. Unblocks C11's five Net rows
and gates the 18 service-client SDK rows that follow. A later epic implements
this (post-E1, alongside F); nothing here ships code.

Locked context: the sqlite decision (2026-10-16) sets the adapter ownership
model. A separately owned adapter package, `pyscriptc-http`, owns the vendored
C and the Python wrapper. The compiler only adds a thin lowering/registration
layer. Dummy boundaries for testing are `httpbin.org` and `example.com`;
`echo.websocket.org` covers WS/SSE later.

## 1. C seam options

Three candidates for how emitted programs reach HTTP:

**Static-linked vendored client.** `pyscriptc-http` vendors a C client, built
to objects by `link_binary` alongside `src/pyscriptc/runtime/*.c`. Two shapes:
vendored libcurl, or a minimal hand-rolled HTTP/1.1 client on BSD sockets.
libcurl brings redirects, cookies, proxies, TLS backends — far more surface
than the closed subset needs, at a vendoring cost of roughly a hundred source
files and a configure step per target. A hand-rolled client is about 300
lines for the milestone surface (connect, write request, read to EOF, parse
status line and headers) and matches the runtime's existing style
(`ps_url.c`, `ps_io.c`).

**ps_mix-style dlopen.** `ps_mix.c` opens a `.so` found through an env var at
call time. It avoids libpython but adds the `dlfcn` portability tax — `ps_mix.c`
is already one of the four runtime files blocking `-windows-gnu` — and an
env-var lookup breaks the self-contained binary story the sqlite adapter set.

**Island.** `ps_island_http_get` would work today (embedded CPython does the
fetch) but links libpython and pins speedup near 1x, the `.so`-heavy row of
the native probe. It is the fallback for the long tail (cookies, pools,
streaming, multipart), not the seam.

**Recommendation: static-first, hand-rolled HTTP/1.1, plaintext first.**
This is the sqlite precedent: self-contained binaries, no dlfcn. TLS is the
hard part and is scoped out of the first milestone — scope it explicitly as
follow-up rather than hiding it: HTTPS is plan step H2, bound through
**mbedTLS** (already on the roadmap) via the F FFI manifest, statically
linked. Linkage cost: two vendored C trees (`ps_http.c` plus mbedTLS) add on
the order of 200-500 KB to the binary and zero runtime dependencies; the
plaintext milestone adds only `ps_http.o` and no external deps at all.

## 2. Python-facing surface

Two options:

- `http.get(url)`-style single-call helpers exposed by the adapter package.
- A documented `urllib.request` / `http.client` subset (the plan's H1/H3
  names: `urlopen`, then `http.client` closed subset).

**Pick: single-call helpers shaped like `urlopen`, under the same sqlite
rules — rows as records, positional params, no callbacks, no context
managers.** Concretely:

```python
get(url: str) -> HttpResponse
post(url: str, body: bytes) -> HttpResponse
```

where `HttpResponse` is a plain record: `status: int`,
`headers: dict[str, str]`, `body: bytes`. Returning `bytes` (caller
`.decode()`s) keeps `charset-normalizer` out of the binary, matching the
roadmap's guidance. `urlopen` returning a context-manager stream is rejected:
it forces `with` protocol support and lazy reads the subset does not model.
`http.client`'s request/response objects are likewise deferred to H3. The
`http.get` spellings can still land inside a `urllib.request` module binding
later if probes demand it — the seam is the C symbol, not the import path.

## 3. Integration shape

Registration follows the existing pattern, thinned to match adapter
ownership:

- Import side: `urllib.request` is already in `QUEUE`/`LOWERED` via
  `stdlib_queue.py`; `module_status` already reports `urllib` as queued
  parent of a lowered child. The adapter adds its module (e.g.
  `pyscriptc_http`) to the closed import table the same way — one entry in
  `QUEUE`/`LOWERED`, no new machinery.
- Call side: `WAVE_FUNCTIONS` in `stdlib_queue.py` (or `REST_FIXED` in
  `stdmore.py` for method-like members) maps each helper to a `ps_http_*`
  symbol via `_fixed`/`_sig` specs — exactly the `urllib.parse` → `ps_url_*`
  binding that already exists. `restlower.rest_call` dispatches through
  `fixed_spec` with no new lowering code.
- Compiler-side stub: under adapter ownership the `.pyi` signature
  declarations and symbol bindings live in `pyscriptc-http` and arrive
  through F's `--ffi <manifest>` (signature-only declarations bound to C ABI
  symbols, statically linked). Until F lands, the same spec can be hardwired
  as one `WAVE_FUNCTIONS` block — the design intends the manifest path, with
  the hardwired table as the bootstrap.
- Link side: `link_binary` globs `runtime/*.c` today; the adapter's vendored
  objects need an additive hook (manifest-declared object/extra sources on
  the link line) rather than dropping files into `runtime/`, keeping
  ownership out of the compiler tree.
- Smoke test: compile a fixture that calls `get("http://example.com")` and
  prints `status` and `len(body)`. CI asserts the ELF compiles and links
  under clang and zig — container rules mean no network in CI. A runtime
  smoke bound to `httpbin.org` runs only outside CI; an opt-in local run can
  use a `/tmp`-served listener instead.

## 4. Sequencing

- Order from the wave plan: E0, E1, then E2 and F in parallel, then Wave C
  stdlib families. The HTTP epic is plan step 10: **H1 HTTP/1.1 `urlopen`
  in the C runtime** needs E2 (`URLError`, `HTTPError`, `TimeoutError`,
  `OSError`) and E4 (headers as full `str`), so implementation lands after
  E4; H2 (mbedTLS HTTPS) after H1 and F; H3 (`http.client` subset) last.
- This design doc is cheap and should land now so the F manifest design and
  the Wave C family files can reserve the seam (the `urllib` parent is
  already half-open).
- Minimal first milestone: **`get(url) -> bytes`-equivalent — GET, `http://`
  only, blocking, `Connection: close`, read to EOF**. URL split reuses
  `ps_url_split`. That covers most of C11's five Net rows. Deferred to later
  milestones, in order: chunked transfer-encoding (httpbin needs it),
  status/header record fields, `post`, redirects off then on, keep-alive,
  mbedTLS HTTPS (H2), WS/SSE against `echo.websocket.org` (separate seam).

## Risks

- Hand-rolled client drifts from real-world edge cases (chunked bodies,
  100-continue, redirects); mitigate by scoping M1 to `Connection: close`
  and documenting deferrals per milestone.
- Blocking sockets only; async is out of scope by definition of the subset.
- If F slips, the bootstrap `WAVE_FUNCTIONS` path must not grow into a
  second manifest format — keep it to the fixed-spec table.
