"""The net receipt's client: loopback GET and POST, sequential and threaded.

The same file runs as a compiled binary and on CPython, so the runner can
compare the two. The stdout line is deterministic (request count and bytes
received), so the runner checks the two sides agree before it reports a
time. The elapsed seconds go to stderr, because the runner times the
process from the outside anyway.

Modes:

    seq-get URL N        N sequential GET requests
    seq-post URL N       N sequential POST requests
    thread-get URL N M   N GET requests spread over M threads
    thread-post URL N M  N POST requests spread over M threads

The threads are real `threading.Thread` objects. The compiler lowers them
(`ps_thread.c`), so the threaded modes measure the compiled thread path
against CPython's, not a single-threaded program wearing a threaded label.
"""

import sys
import threading
import time
import urllib.request

# A fixed body, written out: `bytes * int` is a fence on the compiled side.
PAYLOAD: bytes = (
    b"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    b"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    b"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    b"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    b"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    b"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
)


def get(url: str) -> int:
    resp: urllib.request.HTTPResponse = urllib.request.urlopen(url)
    body: bytes = resp.read()
    return len(body)


def post(url: str) -> int:
    req: urllib.request.Request = urllib.request.Request(url, data=PAYLOAD, method="POST")
    resp: urllib.request.HTTPResponse = urllib.request.urlopen(req)
    body: bytes = resp.read()
    return len(body)


def seq_get(url: str, count: int) -> int:
    total: int = 0
    i: int = 0
    while i < count:
        total = total + get(url)
        i = i + 1
    return total


def seq_post(url: str, count: int) -> int:
    total: int = 0
    i: int = 0
    while i < count:
        total = total + post(url)
        i = i + 1
    return total


def thread_get(url: str, count: int) -> None:
    total: int = 0
    i: int = 0
    while i < count:
        total = total + get(url)
        i = i + 1


def thread_post(url: str, count: int) -> None:
    total: int = 0
    i: int = 0
    while i < count:
        total = total + post(url)
        i = i + 1


def run_threaded(url: str, count: int, workers: int, mode: str) -> int:
    # The compiler tracks a fixed set of named threads; a loop that appends
    # to a list does not (PS2002 on the list binding). Four workers is the
    # receipt's shape anyway, so the fan-out is written out. Each thread
    # gets its share; the remainder rides on the first thread.
    per: int = count // workers
    left: int = count - per * workers
    first: int = per + left
    if mode == "thread-get":
        t1: threading.Thread = threading.Thread(target=thread_get, args=(url, first))
        t2: threading.Thread = threading.Thread(target=thread_get, args=(url, per))
        t3: threading.Thread = threading.Thread(target=thread_get, args=(url, per))
        t4: threading.Thread = threading.Thread(target=thread_get, args=(url, per))
        t1.start()
        t2.start()
        t3.start()
        t4.start()
        t1.join()
        t2.join()
        t3.join()
        t4.join()
    else:
        p1: threading.Thread = threading.Thread(target=thread_post, args=(url, first))
        p2: threading.Thread = threading.Thread(target=thread_post, args=(url, per))
        p3: threading.Thread = threading.Thread(target=thread_post, args=(url, per))
        p4: threading.Thread = threading.Thread(target=thread_post, args=(url, per))
        p1.start()
        p2.start()
        p3.start()
        p4.start()
        p1.join()
        p2.join()
        p3.join()
        p4.join()
    return count


def main() -> int:
    url: str = sys.argv[1]
    mode: str = sys.argv[2]
    count: int = int(sys.argv[3])
    workers: int = int(sys.argv[4])
    started: float = time.monotonic()
    total: int = 0
    if mode == "seq-get":
        total = seq_get(url, count)
    elif mode == "seq-post":
        total = seq_post(url, count)
    elif mode == "thread-get":
        total = run_threaded(url, count, workers, mode)
    else:
        total = run_threaded(url, count, workers, mode)
    elapsed: float = time.monotonic() - started
    print(f"requests={count} bytes={total}")
    print(f"elapsed_s={elapsed}", file=sys.stderr)
    return total


if __name__ == "__main__":
    main()
