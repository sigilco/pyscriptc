---
title: HTTP
description: The native urllib.request client, what it covers, and where it still diverges from CPython.
---

HTTP goes through a native client: `urllib.request` and `urllib.error` lower to `urlopen` calls served by `ps_http.c`, with no subprocess and no host `curl`.
This page shows the shape that lowers, the caveats that remain, and the `curl` escape hatch for the cases the client does not cover.

## The shape that lowers

`urlopen(url)` returns an `HttpResponse` with `status`, `headers` and `body` (`bytes`), plus `getcode()`, `info()` and `read()`.
`data` sends a POST, `timeout` bounds the whole request and raises `TimeoutError` on expiry.

```python
import json
import urllib.request


def fetch(url: str) -> str:
    resp: urllib.request.HTTPResponse = urllib.request.urlopen(url)
    body: bytes = resp.read()
    return body.decode("utf-8")


def status(body: str) -> str:
    doc: dict[str, str] = json.loads(body)
    return doc["status"]
```

`json.loads` needs the result annotation (`dict[str, str]` above), and the decoded string must be UTF-8, since `bytes.decode` lowers the `utf-8` encoding only.

`Request(url, data=..., headers=..., method=...)` builds the request record that `urlopen` consumes, for methods beyond GET and POST and for custom headers.
Redirects (301, 302, 303, 307, 308) follow CPython's method rules, relative `Location` resolution included, with a 10-hop cap.
Chunked transfer-encoding is decoded.

## Divergences from CPython

- TLS is not there yet: an `https://` URL raises `OSError`.
  TLS via mbedTLS is [#235](https://github.com/sigilco/pyscriptc/issues/235).
- `urlopen` takes `url`, `data` and `timeout`; other keywords fence.
  Headers go on the `Request`, as a dict display with constant string keys and values; runtime-built header dicts fence.
- 4xx and 5xx raise `HTTPError` carrying `status`, `headers` and a readable body; malformed URLs and connection failures raise `URLError`.
  Both catch through the `OSError` chain like CPython, but the error types are synthetic records, not the full `urllib.error` class surface.
- Only `urlopen` and `Request` lower; the rest of `urllib.request` (`urlretrieve`, openers, proxies) fences, and `http.client` is not lowered (#237).
- No streaming: the whole body arrives as one `bytes` value.
- These are the numbered divergences 27 and 28 in the [divergence list](../reference/divergences.md).

## The curl escape hatch

For the cases the native client does not cover, such as an unusual TLS configuration or a proxy setup, the `curl` bridge still works through the island runtime:

```python
import subprocess


def fetch(url: str) -> str:
    out: bytes = subprocess.check_output(["curl", "-s", url])
    return out.decode("utf-8")
```

It needs `curl` on `PATH` at run time and the libpython-linked binary shape; see divergence 15 in the [divergence list](../reference/divergences.md).

## Keeping the example honest

The example on this page is compiled and run by the test suite against a local HTTP listener, so it cannot rot without the suite failing.

## Keeping the example honest

The example on this page is compiled and run by the test suite against a local HTTP listener, so it cannot rot without the suite failing.
