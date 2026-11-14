# External-consumer fence probes: email-ai CLI rewrite (C14)

Findings from an attempted **rewrite of a real 7.2k-LOC Rust CLI** (`email-ai`: IMAP email management, MCP server, workflow runner — see `../email-ai`) to pyscriptc, on 2026-11-12, master `ec024c72` (v0.10.0b1). Format follows [fence-repro-probes.md](fence-repro-probes.md) (C13).

Each probe is a minimal reproduction. Verify with `pyscriptc compile --emit bin --call main -o /tmp/x.bin probe.py` (or `pyscriptc coverage probe.py` for the fence list).

**Summary:** 5 of 8 CLI-critical subsystems compile fully native today (subprocess, json, hashlib one-shot, tomllib, argparse, dataclasses). The blocking gaps are concentrated in **streaming I/O** (file.read(n), sys.stdin/stdout), **async subprocess** (needed for timeouts on external binaries), **heterogeneous dicts** (`dict[str, object]` for JSON-RPC), and four missing stdlib modules (`email`, `http.server`, raw `socket`, `tarfile`/`zipfile` extraction).

---

## Works today (verified on master)

These compiled **fully native** (no libpython link for the pure-Python paths, or via the sqlite FFI adapter):

| Capability | Probe | Verdict |
|---|---|---|
| `subprocess.check_output`, `.run(capture_output=True)` | `g11`-adjacent, sync | native, no fences |
| `subprocess.check_output` + `json.loads` into `list[dict[str, str]]` → dataclass | `email_probe2.py` | native binary, **653K**, runs |
| `hashlib.sha256(data)` one-shot | `g6b` variant without `read(n)` | native |
| `tomllib.loads(s)` | `email_probe2.py` | native (`tomllib.load(f)` fences, `loads` works) |
| `os.read`/`os.write` on fds 0/1 | `stdio_b/d.py` | native, round-trips JSON-RPC line |
| SQLite via `--ffi` manifest (`pyscriptc_sqlite`) | `sqlite_test.py` | **1.9M static Mach-O**, links only libSystem, full CRUD works |
| `asyncio.run`, `async def`, `await` (pure compute) | `async_probe3.py` | native, 0 fences |
| `urllib.request.urlopen(...).read()` | `g11_urllib_download.py` | native, 0 fences |
| `os` path/env, `dataclasses`, `argparse`, `sys.argv`, `len()` | various | native |
| macOS keychain via `subprocess.check_output(["security", ...])` | `keychain_probe.py` | **native, 0 fences** — no keyring module needed |

Distribution facts measured:
- Native-tier binary: **606K–1.9M**, links `/usr/lib/libSystem.B.dylib` only.
- Any island demotion silently links `libpython3.14.dylib` from the *host* uv install (non-portable) — check `otool -L` after every build.
- `--onefile --embed-python=bundle`: self-contained **23M bundle dir** (includes 18M libpython). Works, but is an island, not native tier.
- `--driver zig` fails `--onefile` with `libcompiler_rt.a` not found; `--driver clang` works.

---

## Gaps (reproduces on master `ec024c72`)

### 1. `subprocess.run(..., timeout=N)` — PS2003

```python
import subprocess

def main() -> None:
    out: bytes = subprocess.run(["echo", "x"], capture_output=True, timeout=30).stdout
    print(out.decode())
```

Fence: `PS2003: subprocess.run keyword timeout is not lowered`.
**Why it matters:** email-ai shells out to himalaya/notmuch with a 30s timeout (`adapter/himalaya.rs`). Without `timeout=` there is no way to bound a hung subprocess in the native tier.
Desired: lower the `timeout` keyword (threading-based watchdog or `poll()` loop).

### 2. `asyncio.create_subprocess_exec` — PS1106/PS2002

```python
import asyncio

async def run() -> str:
    proc: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
        "echo", "hi", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out: bytes
    err: bytes
    out, err = await proc.communicate()
    return out.decode().strip()
```

Fence: `PS1106: the annotation asyncio.subprocess.Process names a type that is not lowered` (plus cascading PS2002 on `proc`).
**Why it matters:** the async twin of gap 1 — this is the idiomatic bounded-subprocess path for a workflow runner. `async def`/`await` on pure compute lowers fine, so the coroutine engine works; only the subprocess adapter is missing.
Desired: lower `asyncio.subprocess.Process` + `create_subprocess_exec`/`PIPE`/`communicate()`.

### 3. `sys.stdin.readline` / `sys.stdout.flush` — PS2001

```python
import sys

def main() -> None:
    line: str = sys.stdin.readline()
    sys.stdout.write(line + "\n")
    sys.stdout.flush()
```

Fences: `PS2001: sys.stdin.readline is not in the lowered subset of sys`, same for `sys.stdout.flush`.
**Why it matters:** this is the **MCP stdio transport loop** — a line-oriented JSON-RPC server on stdin/stdout. `os.read(0, N)`/`os.write(1, ...)` works as a workaround (verified: `stdio_b.py` round-trips a JSON-RPC message natively), so the fix is a thin buffered line-reader over the existing fd surface, plus an unbuffered-write helper or `os.write(1, ...)` convention documented as the flush story.
Desired: lower `sys.stdin.readline` and `sys.stdout.flush` (or `sys.stdout.buffer.write`).

### 4. `dict[str, object]` / heterogeneous dicts — PS1104

```python
import json

def main() -> None:
    msg: dict[str, object] = json.loads('{"id":1,"method":"x"}')
    print(msg)
```

Fence: `PS1104: object is a dynamic or structural type and is not lowered` (cascading PS2002).
**Why it matters:** JSON-RPC messages are heterogeneous by nature (`"id"` is int *or* string, `params` is dict *or* list). Without `dict[str, object]` there is no native-tier representation for a decoded JSON-RPC message. `json.loads` into `dict[str, str]` works, so the missing piece is a tagged-dynamic value type (scriptc solved this exact problem with "checked dynamic slots").
Desired: lower `object` as a tagged dynamic slot, at least for `json.loads` results.

### 5. `file.read(n)` with a size argument — PS2003 (BytesIO arity)

```python
def main() -> None:
    f = open("/etc/hosts", "rb")
    data: bytes = f.read(100)   # fences
    # f.read()               # works — whole file only
```

Fence: `PS2003: BytesIO.read expects 0 argument(s)`.
**Why it matters:** chunked reading is how `email-ai setup` computes SHA-256 over a downloaded archive (GB-scale, cannot `read()` whole). `hashlib` itself is fine one-shot — this fence is the only blocker for streaming hashing.
Desired: lower the one-arg `read(size)` on binary files (and `hashlib` hash-object `.update()`/`.hexdigest()` as method calls — currently `h = hashlib.sha256()` zero-arg construction fences too, PS2003, see `g6`).

### 6. `email` / `email.message` module absent from closed table — PS2002

```python
import email
from email.message import Message
```

Fence: `PS2002: email is not in the closed import table`.
**Why it matters:** email-ai's Apple Mail adapter parses `.emlx` MIME (RFC822 with a length prefix). Without stdlib `email` the whole adapter has no parser. This is the single largest functional gap for this consumer.
Desired: add a subset `email` module (parser + `Message.get` header access at minimum).

### 7. `http.server` / raw `socket` — PS2002 (both absent)

```python
from http.server import HTTPServer, BaseHTTPRequestHandler   # PS2002: not in closed table
# or
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)        # PS2001: socket.socket not lowered
```

**Why it matters:** the OAuth2 login flow needs a localhost loopback HTTP listener on :8085 to catch the provider redirect. `urllib.request` (client) lowers fine; there is no server story in the native tier.
Desired: either a minimal `http.server` subset or a lowered `socket` surface sufficient for bind/listen/accept/recv/send. (`docs/spec/http-seam-design.md` may already cover this — this probe shows the user-facing surface is not there yet.)

### 8. `tarfile`/`zipfile` — PS2001 (partial/incomplete surface)

```python
import tarfile

def main() -> None:
    with tarfile.open("/tmp/x.tar.gz", "r:gz") as t:
        names: list[str] = t.getnames()
    print(names)
```

Fence: `PS2001: tarfile.open is not in the lowered subset of tarfile` (both modules are in QUEUE but this call path is unlowered).
**Why it matters:** `email-ai setup` extracts the pinned himalaya `.tar.gz` (POSIX) / `.zip` (Windows) release asset.
Desired: lower `tarfile.open` + `extractall`/`getnames` and the `zipfile` equivalent.

---

## Non-blockers worth knowing

- `tomllib.load(f)` fences but `tomllib.loads(raw.decode())` lowers — one-line workaround, maybe worth aliasing.
- `from __future__ import annotations` silently forces an island/libpython demotion even when every import is native (verified: identical file with/without it flips `otool -L`). The docs recommend it for self-referencing classes; either lower it or fence it loudly.
- `os.getenv` missing-key raises `KeyError` (documented divergence; two-arg form fences).
- Workflow-YAML: `yaml` is in QUEUE but not exercised in these probes.

## Consumer context (why these specific gaps)

email-ai's Rust CLI is 7.2k LOC of: subprocess orchestration (himalaya/notmuch), JSON-RPC MCP stdio server (~200 LOC, hand-rolled), YAML workflow runner, SQLite Apple Mail adapter, keychain, OAuth loopback, self-installer (download + SHA-256 + archive extraction). No Rust-specific library dependencies — every one maps to a Python stdlib module **except** the eight gaps above. An otherwise-complete port stalls on streaming I/O + async subprocess + heterogeneous JSON + MIME.

Probe sources: `/tmp/psc-gaps/g1..g11` (session scratch), main binaries verified at `/tmp/psc-probe/`.
