# Essence compile-rate assessment (C11)

Headline: 45/45 (100%) of Python-compatible essence translations compile and run today under dummy boundaries, far above the 10-20% floor (5-9 of 45). The strict native-seam rate is 27/45 (60%): 18 rows have no native seam in the subset and were exercised through the sanctioned dummies instead.

The assessed corpus is a private third-party script catalogue. This document
records the methodology and the aggregate findings only; the per-script
catalogue is deliberately not published.

## Method

Each of the 45 scripts in the catalogue was reduced to a minimal Python essence: same seam shape, dummy boundaries, `def main() -> int` wrapper (module-level statements do not lower; E1 pending), compiled with `pyscriptc compile --emit bin --call main --skip-check`, then executed. Boundaries: `/tmp` for FS and sqlite, `https://example.com` via `curl` for HTTP, `date`/`ffmpeg`/`uv`/`python3` for subprocess tools, env vars for service credentials. Essence sources stayed in /tmp; the integration is not published either.

Native seams that exist: FS (`open`, `os`, `posixpath`, `pathlib`, `shutil`, `tempfile`), subprocess (`subprocess.check_output`, island), env reads (`os.getenv`), plus `json`/`hashlib`/`base64`/`re`/`time`/`datetime`. Native seams that do not exist: `sqlite3`, any HTTP client (`urllib.request`, `http.client`, `requests`), websockets, `random`, `secrets`, `posix`, `sys.argv`/`argparse`, `print`. Every missing module above was verified to fence with PS2002, not assumed.

## Results

Totals: COMPILED+RAN 45, COMPILED 0, FENCE 0, SEAM 0, SKIP 0. Wall time for the full 45-file matrix was about 5 minutes.

## Rate vs floor

Overall compile+run rate under the sanctioned dummy boundaries: 100% (45/45), floor 10-20% holds with margin. Native-clean subset (no Net, no DB, no external-service call, only real FS/spawn/pure logic): 18/45 = 40%. Strict view (natural stdlib port where Net and DB rows become SEAM): 27/45 = 60%.

## Missing seams, ranked by rows blocked

Vendor names are generalized; the counts are what drive prioritization.

1. `sqlite3` (DB): 13 rows shimmed through `python3 -c` children. Maps to epic F (FFI; `_sqlite3` is a C extension) or Wave C stdlib if a pure subset lands first.
2. Service-client SDKs (secret-manager x9, object-storage x10, billing x1, tracing x1, distinct rows 18): env reads are native, the calls themselves were dummied with `date`. Maps to whatever HTTP client lands (same blocker as next row).
3. HTTP client (Net=fetch/http): 5 rows shimmed through `curl`. Maps to F FFI/socket work plus Wave C `urllib.request` surface. Websocket/SSE blocks 0 catalogue rows today.
4. `print`: pervasive. Every real script writes to stdout; all essences return int instead. Blocks faithful ports of all 45; likely a Wave C builtin row.
5. `sys.argv`/argument parsing: avoided in every essence (inputs hardcoded). Blocks the media-platform CLI-shaped scripts; ~10 catalogue rows realistically.
6. Module-level statements (E1): structural gate on all 45 - every essence needed a `def main()` wrapper. This is the single largest fidelity gap for unmodified ports.
7. `random`, `secrets`, `posix`: verified PS2002 but 0 catalogue rows need them.

## Fences hit while authoring (real findings, not blockers to the tally)

- `list.append` is not lowered: PS2001 "call of attribute is not lowered" (worked around with accumulation).
- `int(bytes)` is not lowered: PS2003 (hit decoding `check_output`; worked around with `len`/byte compare).
- `os.getenv(missing)` raises `KeyError` at runtime, so env audit essences need `try/except KeyError`.
- Every local needs annotation-before-use (PS1001); `x = call()` without `x: T` fences.
- `os.stat` result annotation is `Stat` in the lowering tables; unclear surface name - avoided via `posixpath`/`os.remove`.
- `dict.values()`/`dict.items()` are not lowered (documented); `.keys()` only.

## Example essences

Net via the curl shim:

```python
import subprocess


def main() -> int:
    body: bytes = subprocess.check_output(["curl", "-sS", "https://example.com"])
    return len(body)
```

DB via the python3-sqlite shim: a real sqlite file under /tmp, exercised through a `check_output(["python3", "-c", code])` child that creates the table, inserts, commits, and prints the row count.

FS+spawn: `subprocess.check_output(["ffmpeg", "-version"])` returns bytes, proving the island subprocess path end to end.

## Cheapest unblocks next, by seam

Seam-level ranking (the per-script ordering is not published):

1. `print`: every real script writes to stdout. One builtin unblocks the most faithful ports in the set.
2. `sys.argv`: an argv seam unblocks the media-platform CLI rows in one change.
3. `list.append` lowering plus `print`: makes already-running scripts faithful. With E1 they go verbatim.

If a seam-level win is preferred over script-level wins: `sqlite3` is the single largest unlock (13 rows), and it is also the hardest - C extension, so epic F territory. The HTTP client is second (5 direct rows, plus all 18 service-SDK rows that today ride on `date`/`curl` dummies).
