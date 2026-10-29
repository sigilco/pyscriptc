---
title: "Serving Flask and FastAPI"
description: "Compiling a Flask or FastAPI app into one binary: what works, what it costs, measured numbers, and the known gaps."
---

# Serving Flask and FastAPI

A Flask or FastAPI app compiles into one binary with `--island` (the alias is `--fallback=cpython`).
The framework is dynamic Python, so it runs on the island floor: the binary embeds CPython and the app inside it.
What the compile buys is the deployable shape: one artifact for app plus framework plus interpreter, no venv and no `pip install` on the target.
It does not buy native speed; the request path is the framework's own.

Every number on this page was measured on the reference setup below, with the commands shown, so the page can be re-measured when the compiler moves.
The receipts are statements about one machine and one framework version, not guarantees.

## The apps

`examples/web/flask_app.py` serves `/` and `/health` through Flask 3.1.
`examples/web/fastapi_app.py` serves the same two paths plus the generated `/docs` through FastAPI 0.162 with uvicorn.

```bash
uv run pyscriptc compile examples/web/flask_app.py --island --embed-python host --emit bin -o flask_bin
uv run pyscriptc compile examples/web/fastapi_app.py --island --embed-python host --emit bin -o fastapi_bin
```

`--emit bin` is explicit because `--emit` defaults to `llvm` (the textual IR), which is what the compiler's own pipelines want.

The compile needs the framework importable so the type-checker and the island seed can read it (run the compile from a venv that has `flask` or `fastapi` installed, or pass `PYTHONPATH`).
The binary needs it importable at run time too: the `--embed-python host` build adds `VIRTUAL_ENV`'s `site-packages` and `PYTHONPATH` entries to the embedded interpreter's path, the same rule the [libraries page](../guide/libraries.md) documents for fallback mode.

With the framework on the path, the binary serves:

```bash
PYTHONPATH=.venv/lib/python3.14/site-packages ./flask_bin &
curl -s localhost:8080/
# hello from flask
```

## Divergences and gaps

- The binary is not self-contained under `--embed-python host`: it links the host `libpython` and reads the framework from disk. A target without the framework cannot run it.
- `--embed-python bundle` writes a relocatable directory (`bin/` + `lib/`) that carries `libpython`, the trimmed stdlib and the app's pure-Python dependencies, and it runs with `env -i` (no inherited environment). Neither example can use it today, both refused with `PS4302` by design: Markupsafe for Flask and pydantic-core for FastAPI ship compiled extensions, and a bundle carries pure Python only. A program whose dependency closure is pure (`anyio` alone) bundles and runs with `env -i`; the same refusals bound the shape for every framework with a compiled wheel.
- Dev-server warnings are the frameworks' own: Flask's "use a production WSGI server" banner and uvicorn's startup lines print from inside the binary exactly as they print under CPython.
- No inbound socket compiles natively: `socket`, `ssl` and `http.server` are fenced (`PS2001`/`PS2002`), so a server in the native tier does not exist yet (divergence 29). The servers above run entirely on the island floor; `ps_loop.c` has no file-descriptor demultiplexing and the native HTTP client is outbound-only.
- The framework's own divergences apply unchanged: the embedded CPython is the host interpreter, not the 3.14 pin (divergence 15).

## Measured receipts

Reference setup: Apple M-series laptop, macOS 26 arm64, CPython 3.14.9 host and island, Flask 3.1.3 / Werkzeug 3.1.9, FastAPI 0.162 / Starlette 1.10 / uvicorn 0.54 / pydantic 2.14, uv venv. `ab` was avoided: the Werkzeug dev server answers `Connection: close`, and a macOS load client that opens a fresh socket per request exhausts its ephemeral port range in seconds (`OSError 49`), which reads as a server fault but is a client artifact. The load generator below pins a source port per worker.

| Metric | Flask compiled | Flask venv + `python app.py` | FastAPI compiled | FastAPI venv + `python app.py` |
|---|---|---|---|---|
| Binary (host embed) | 629 KB | n/a | 629 KB | n/a |
| Deployed size | 629 KB + framework on disk | 20.3 MB venv | 629 KB + framework on disk | 20.3 MB venv |
| Bundle directory (`--embed-python bundle`) | refused (PS4302, Markupsafe) | n/a | refused (PS4302, pydantic-core) | n/a |
| Cold start to first 200 | 128 ms median | 211 ms median | 198 ms median | 203 ms median |
| RSS after warm-up | 28-35 MiB | 33-35 MiB | 30-56 MiB | 56-203 MiB |
| Throughput, 8 workers, loopback | ~690 req/s | ~686 req/s | ~3,400 req/s | ~3,370 req/s |
| Latency p50 / p99 | 11.5 / 12.9 ms | 11.6 / 13.3 ms | 2.3 / 3.0 ms | 2.3 / 3.0 ms |

Reading the table honestly:

- Throughput and latency are the framework's dev servers on both sides of every row; the compiled column and the venv column are within noise of each other. There is no speed claim.
- The compiled Flask binary's RSS overlaps the venv run's because both are the same interpreter running the same code. The FastAPI RSS range is uvicorn's single-worker spread; the two 200 MiB readings were multi-worker starts.
- The size win is real but conditional: 629 KB plus a framework that must already be on disk is smaller than a 20 MB venv only when several apps share one framework install. The `env -i`-runnable bundle, measured on a pure-dependency program (`anyio`), is 39 MB against a 20 MB venv, because it also carries the stdlib and `libpython`.
- Cold start saves roughly 40 percent on Flask (128 ms vs 211 ms) and is inside noise on FastAPI. Both include the framework's own import cost, which dominates.

The load generator used for the throughput and latency rows, kept so the numbers can be reproduced:

```python
import json, socket, statistics, sys, threading, time, urllib.parse

results, lock = [], threading.Lock()


def worker(host, port, path, stop):
    local = []
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(5)
    local_port = sock.getsockname()[1]
    while time.monotonic() < stop:
        t0 = time.perf_counter()
        try:
            sock.connect((host, port))
            sock.sendall(b"GET %s HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n\r\n" % (path.encode(), host.encode()))
            data = b""
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
            if data.startswith(b"HTTP/1.1 200"):
                local.append((time.perf_counter() - t0) * 1000.0)
        except OSError:
            pass
        finally:
            sock.close()
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.settimeout(5)
            try:
                sock.bind(("127.0.0.1", local_port))
            except OSError:
                sock.bind(("127.0.0.1", 0))
                local_port = sock.getsockname()[1]
    with lock:
        results.extend(local)


u = urllib.parse.urlsplit(sys.argv[1])
stop = time.monotonic() + float(sys.argv[2]) if len(sys.argv) > 2 else time.monotonic() + 8.0
ts = [threading.Thread(target=worker, args=(u.hostname, u.port or 80, u.path or "/", stop)) for _ in range(8)]
t0 = time.monotonic()
for t in ts:
    t.start()
for t in ts:
    t.join()
elapsed = time.monotonic() - t0
results.sort()
n = len(results)
print(
    json.dumps(
        {
            "requests": n,
            "rps": round(n / elapsed, 1),
            "p50_ms": round(results[int(n * 0.5)], 2),
            "p95_ms": round(results[int(n * 0.95)], 2),
            "p99_ms": round(results[int(n * 0.99)], 2),
        }
    )
)
```

## When this shape is the right one

- One artifact per app for a deployment target that has no Python tooling: the binary plus the framework tree is the whole contract, and `env -i` works for the bundle shape.
- Reproducible-ish deploys without Docker: no `pip install` step can drift, because there is none.
- Where it is not the right shape: when the target already has a Python installation and a venv workflow, the venv column of the table is the same app with fewer moving parts; when the framework needs a compiled dependency that `PS4302` refuses, the bundle shape is closed and the host-embed shape still needs the venv.
