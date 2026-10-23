# Network receipt

The network receipt: loopback HTTP, compiled against CPython, in two
parts. Opt-in: nothing in CI or `benchmarks.baseline.run --check` reads
this package, so the frozen baseline gates are untouched.

```bash
uv run python -m benchmarks.net.run
uv run python -m benchmarks.net.run --runs=3 --json=net.json
```

A full matrix run takes about 20 s. Port selection is the kernel's (the
server binds port 0); every connection stays on 127.0.0.1 and nothing
touches an external network.

## Part 1: the script-based client

`workloads/client.py` is one annotated script. It runs as a compiled
binary and as CPython against the same local `http.server`, over four
modes: sequential GET, sequential POST, threaded GET and threaded POST
(real `threading.Thread` objects, four workers). The runner compiles the
script, checks both sides print the same request and byte counts, then
times `--runs` interleaved rounds per side and compares the medians.

Each row reports wall time (whole process, both sides), the client's own
in-process elapsed time on stderr (startup excluded, so it reads as the
per-request CPU: the row divides it by the request count), and requests
per second over the in-process window.

What the compiled side exercises: `urllib.request.urlopen` with the
native HTTP client (`ps_http.c`: blocking sockets, no keep-alive), and
`threading.Thread` through `ps_thread.c`. The sequential rows are
mostly the HTTP client; the threaded rows add the thread fan-out.

## Part 2: sustained server throughput

`http.server` is still a fence in the compiler: the import is outside
the table (PS2001), and the server-side lowering tracks #227-230. Until
that lands, the sustained rows time CPython's `http.server` - once as
the single-threaded `HTTPServer`, once as `ThreadingHTTPServer` - under
a fixed load: 600 GET and 300 POST requests at 4 concurrent
connections, driven by `workloads/load_client.py`. The rows report
requests per second and p50/p99 request latency.

The compiled server row reads `pending` with a pointer at #227-230.
`--compiled-server PATH` times a server binary in the same row shape
when one exists, so the compiled row slots in without touching the
runner again.

## Notes

- The load client prints one latency line per request in seconds, and
  the runner converts. The earlier millisecond format read 1000x too
  large in the runner's percentile math; keep the unit in the line name
  (`latency_s=`).
- The client's threaded fan-out is written out as four named threads.
  The compiler does not track a thread list grown by `append` (the list
  binding fences, PS2002), and four workers is the receipt's shape.
- `bytes * int` is a fence on the compiled side (PS2004), so the fixed
  request body is written out as adjacent literals, which fold at parse
  time on both sides.
- The measured matrix (2026-10-23, macOS arm64, CPython 3.14): the
  compiled client is faster than CPython on every mode, roughly 1.1x
  sequential GET to 2.8x threaded POST, and CPython's own threaded
  `http.server` serves about 3000 req/s at p50 1.1 ms under this load.
