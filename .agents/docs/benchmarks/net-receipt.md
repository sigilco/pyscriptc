# Network receipt

The loopback HTTP receipt: what runs, how to run it, and what the numbers
were on 2026-10-23.

## Where it lives

`benchmarks/net/`, a self-contained package next to `benchmarks/generators/`.
Opt-in: no CI job, no test in the frozen baseline path, and no read of
`benchmarks/baseline/reference*.json`. The baseline suite keeps its own
workload list and its own reference files; the net receipt does not add
rows to it, so `python -m benchmarks.baseline.run --check` is unaffected.

```bash
uv run python -m benchmarks.net.run
uv run python -m benchmarks.net.run --runs=3 --json=net.json
uv run python -m benchmarks.net.run --compiled-server /path/to/server
```

A full matrix run takes about 20 s. The server binds port 0 and the
kernel picks the port; every connection stays on 127.0.0.1.

## Part 1: the script-based client

`benchmarks/net/workloads/client.py` is one annotated script that runs
both as a compiled binary and as CPython, against the same local
`http.server`. Four modes: sequential GET, sequential POST, threaded GET
and threaded POST, the last two over four real `threading.Thread`
workers.

The runner compiles the script, checks that both sides print the same
request and byte counts, then times `--runs` interleaved rounds per side
and compares the medians. Each row carries four numbers:

- wall time of the whole process, both sides (this is what the
  comparison table prints)
- the client's own in-process elapsed time, on stderr, startup excluded
- per-request CPU, the in-process time divided by the request count
- requests per second over the in-process window

The in-process window is the CPU proxy: the wall time of a short script
is mostly process startup, which says nothing about the request loop.

On the compiled side this exercises `urllib.request.urlopen` through the
native HTTP client (`ps_http.c`: blocking BSD sockets, no keep-alive,
`Connection: close`) and `threading.Thread` through `ps_thread.c`.

## Part 2: sustained server throughput

`http.server` is still a fence in the compiler: the import is outside the
table (PS2001), and the server-side lowering tracks #227-230. The
receipt therefore measures CPython's own server, twice: the
single-threaded `HTTPServer` and the `ThreadingHTTPServer`, both under a
fixed load of 600 GET and 300 POST requests at 4 concurrent connections
from `workloads/load_client.py`. The rows report requests per second and
p50/p99 request latency.

The compiled server row reads `pending` with a pointer at #227-230.
`--compiled-server PATH` times a server binary in the same row shape, so
the compiled row slots in without a runner change.

## The measured matrix (2026-10-23, macOS arm64, CPython 3.14.9)

Client rows, compiled against CPython, wall time of the whole process:

| Mode | Compiled | CPython | per-request CPU, compiled / CPython |
| --- | --- | --- | --- |
| sequential GET | 221 ms | 247 ms | 0.705 / 0.483 ms |
| sequential POST | 96 ms | 191 ms | 0.442 / 0.532 ms |
| threaded GET | 123 ms | 244 ms | 0.289 / 0.373 ms |
| threaded POST | 61 ms | 176 ms | 0.268 / 0.434 ms |

A later run of the same matrix on the same host put the compiled client
between 1.1x and 2.8x CPython on wall time; the threaded POST mode has
the widest margin, because the compiled thread fan-out is cheaper than
CPython's. The numbers move with the host, so quote the shape (the
compiled client wins on every mode, and the margin grows with the number
of threads) rather than a single ratio.

Server rows, CPython's `http.server` under the fixed load:

| Server | GET req/s | GET p50 | GET p99 | POST req/s |
| --- | --- | --- | --- | --- |
| single threaded | 2714 | 1.10 ms | 2.19 ms | 2536 |
| threaded | 2999 | 1.08 ms | 2.31 ms | 2569 |

The compiled server row is pending (#227-230).

## Gotchas the receipt hit

- The load client prints one latency line per request. The unit belongs
  in the line name (`latency_s=`). An earlier millisecond format read
  1000x too large in the runner's percentile math, and the p50 looked
  like seconds.
- The compiler does not track a thread list grown by `append`: the list
  binding fences (PS2002). The threaded fan-out is written out as four
  named threads, which is also the shape the receipt measures.
- `bytes * int` is a fence on the compiled side (PS2004), so the fixed
  request body is written out as adjacent literals. They fold at parse
  time on both sides, so the compiled and the CPython bodies are equal.
- `urlopen` needs the response annotated as
  `urllib.request.HTTPResponse`; an unannotated binding fences, and the
  annotation of a plain `object` fails the type check.
- `urlopen(url, timeout=...)` is a cast fence (PS0001); the receipt does
  not pass a timeout.

## What is not measured

- TLS. `https://` raises `URLError` on the compiled side until #235
  lands, so every request here is plaintext `http://`.
- Keep-alive. The native client sends `Connection: close` and reads to
  EOF, so each request is a fresh connection. That is the shape of the
  CPython `urllib` client too.
- The server side of the compiled stack, which is exactly what #227-230
  covers.
