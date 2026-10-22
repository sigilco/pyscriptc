---
title: HTTP today
description: The interim HTTP and HTTPS path, a curl bridge through subprocess, with its caveats and the native client that replaces it.
---

pyscriptc has no native HTTP client yet.
HTTP and HTTPS work today through a bridge: `subprocess.check_output` runs `curl` on the host, and the bytes come back into the compiled program.
This page shows the shape that works, the caveats it has, and what replaces it.

## HTTP today

The bridge is one call to `subprocess.check_output` with a `curl` argument list.
The result is `bytes`, so it decodes to `str` and then goes through `json.loads` when the answer is JSON.

```python
import json
import subprocess


def fetch(url: str) -> str:
    out: bytes = subprocess.check_output(["curl", "-s", url])
    return out.decode("utf-8")


def status(body: str) -> str:
    doc: dict[str, str] = json.loads(body)
    return doc["status"]
```

A JSON POST is the same call with the method, the header and the payload in the argument list.

```python
def post(url: str, payload: str) -> str:
    out: bytes = subprocess.check_output(
        ["curl", "-s", "-X", "POST", "-H", "Content-Type: application/json", "-d", payload, url]
    )
    return out.decode("utf-8")
```

The flags are plain `curl` flags, so anything `curl` accepts works: `-s` silences the progress meter, `-L` follows redirects, `-m` sets a timeout.
The argument list must be a `list[str]`, which is what the `subprocess` subset accepts.

The round trip is the whole shape of the path: fetch bytes, decode them, parse them.
`json.loads` needs the result annotation (`dict[str, str]` above), and the decoded string must be UTF-8, since `bytes.decode` lowers the `utf-8` encoding only.

The compiled program runs `curl` as a child process.
That means the request is made by `curl`, not by pyscriptc: the TLS stack, the proxy handling, the certificate store and the redirect policy are `curl`'s, and the version on the host decides the behavior.

## Caveats

- `curl` must be on `PATH` at run time.
  A binary compiled on a machine with `curl` fails on a machine without it.
- POSIX only.
  The `subprocess` runtime uses `fork()` (`src/pyscriptc/runtime/ps_sub.c:85`), so this path does not work on Windows.
- The binary must be the libpython-linked shape.
  `subprocess.check_output` is an island call, so the link adds the embedded CPython (`ps_island.c`) and `-lpython3.14`.
  A binary that only needs the small runtime cannot make this call.
- No streaming.
  The whole body arrives as one `bytes` value, so the response is held in memory and there is no incremental read.
- The island follows the interpreter the binary links, not the CPython version the script was written against.
  That is divergence 15 in the [divergence list](../reference/divergences.md).

## What replaces it

A native HTTP client is planned and this bridge is the interim path.
The design survey is in the developer notes (`.agents/docs/spec/http-seam-design.md`): a statically linked client, plaintext first, TLS bound to mbedTLS through the FFI manifest, owned by a separate `pyscriptc-http` adapter package.
The epic is [#219](https://github.com/sigilco/pyscriptc/issues/219) and the native client task is [#60](https://github.com/sigilco/pyscriptc/issues/60).

When the native client ships, this page keeps its escape-hatch note: `curl` remains available for the cases the native client does not cover, such as an unusual TLS configuration.

## Keeping the example honest

The example on this page is compiled and run by the test suite against a local HTTP listener, so it cannot rot without the suite failing.
