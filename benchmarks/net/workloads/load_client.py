"""The fixed-load client for the sustained server row.

Part 2 of the net receipt: one CPython process drives `count` requests at
`workers` concurrent connections against one URL and prints one latency
line per request, in seconds. The runner reads p50 and p99 from those
lines, and the wall time from stderr. No external network: the URL is the
loopback server the runner started.

    load_client URL get|post COUNT WORKERS [--quiet]
"""

import sys
import threading
import time
import urllib.request

PAYLOAD: bytes = (
    b"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    b"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    b"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    b"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    b"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    b"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
)

_lock = threading.Lock()
_latencies: list = []


def _one(url: str, mode: str) -> int:
    started = time.monotonic()
    if mode == "get":
        resp: urllib.request.HTTPResponse = urllib.request.urlopen(url)
        body: bytes = resp.read()
    else:
        req: urllib.request.Request = urllib.request.Request(url, data=PAYLOAD, method="POST")
        resp = urllib.request.urlopen(req)
        body = resp.read()
    elapsed = time.monotonic() - started
    with _lock:
        _latencies.append(elapsed)
    return len(body)


def main() -> int:
    url: str = sys.argv[1]
    mode: str = sys.argv[2]
    count: int = int(sys.argv[3])
    workers: int = int(sys.argv[4])
    quiet: bool = "--quiet" in sys.argv[5:]
    started = time.monotonic()
    threads: list = []
    base: int = count // workers
    extra: int = count - base * workers
    for i in range(workers):
        share = base + 1 if i < extra else base
        worker = threading.Thread(target=_drive, args=(url, mode, share))
        threads.append(worker)
    for worker in threads:
        worker.start()
    for worker in threads:
        worker.join()
    elapsed = time.monotonic() - started
    print(f"requests={len(_latencies)}")
    for value in _latencies:
        print(f"latency_s={value:.6f}")
    print(f"elapsed_s={elapsed}", file=sys.stderr)
    if quiet:
        return 0
    _latencies.sort()
    p50 = _latencies[len(_latencies) // 2]
    p99 = _latencies[min(len(_latencies) - 1, int(0.99 * (len(_latencies) - 1)))]
    print(f"p50_s={p50:.6f} p99_s={p99:.6f} rps={len(_latencies) / elapsed:.1f}")
    return 0


def _drive(url: str, mode: str, count: int) -> None:
    for _ in range(count):
        _one(url, mode)


if __name__ == "__main__":
    main()
