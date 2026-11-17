# socket and http.server (issue #1270, email-ai probes g10 and g10b)

Status: slice 1 (`socket`) and slice 2 (`http.server`) have landed; the fences of slice 3 stand (`ssl`, UDP, non-blocking mode, `select` on sockets, the `socketserver` mixins and `ThreadingHTTPServer`). Source probes: `.agents/probes/email-ai/g10_http_server.py` and `g10b_socket.py`; the slice-1 runtime is `src/pyscriptc/runtime/ps_sock.c` with `src/pyscriptc/restlower/_socket.py`, the slice-2 runtime is `src/pyscriptc/runtime/ps_http_server.c` with `src/pyscriptc/restlower/_httpserver.py` and the differential probe `probes/differential/http_server_loopback.py`.

## Slices

1. `socket` client and server over blocking TCP: `socket.socket(AF_INET, SOCK_STREAM)`, `bind`, `listen`, `accept`, `connect`, `recv`, `send`, `sendall`, `close`, `setsockopt(SO_REUSEADDR)` and the context-manager form. IPv4 loopback and named hosts only. The differential probe runs a loopback echo against the pinned CPython and byte-compares stdout, stderr and the exit code.
2. `http.server`: `HTTPServer`, `BaseHTTPRequestHandler` subclass with `do_GET` and `do_POST`, `send_response`, `send_header`, `end_headers`, `wfile.write`, `rfile.read(n)`, `serve_forever` and `shutdown`. Built on slice 1 as a lowered class, not a runtime parser: request-line and header parsing stay small and explicit in a new `ps_http.c`.
3. Fences kept: `ssl`, UDP, non-blocking mode, `select` on sockets, `socketserver` mixins and `ThreadingHTTPServer` (PS2002 with the divergence entry saying why).

## Constraints

- New runtime files (`ps_sock.c`, `ps_http.c`) are host-only. Add them to `MOBILE_EXCLUDED_SOURCES` and their symbols to `MOBILE_EXCLUDED_SYMBOLS` in `src/pyscriptc/mobile.py`, and guard them out of wasm32-wasi (no sockets there), as `ps_sub.c` and `ps_aproc.c` are.
- New MIR op families take fresh id blocks above the highest id recorded in `mir.py`, and only one open PR appends MIR ops at a time.
- Handlers are classes with overridden methods: reuse the existing class and dunder-call lowering, no new dispatch mechanism.
- Every slice carries a differential probe and a divergence or gap entry in the same PR.

## Order and cost

Slice 1 is one session (about 60 minutes if the worker starts from this note). Slice 2 depends on it. Do not run them in parallel: both append MIR ops.
