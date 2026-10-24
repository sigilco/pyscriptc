# Library receipt

The library receipt: what runs, how to run it, and what the numbers were
on 2026-10-24.

## Where it lives

`benchmarks/library/`, a self-contained package next to `benchmarks/net/`.
The receipt is issue #409, the v0.7.0 exit criterion: a program that
depends on a pure-Python PyPI package compiles through the normal
`pyscriptc compile` path (deps-resolved, not vendored) and its output
matches CPython on the same machine. The target is `certifi`; the strong
receipt from #137 (`colorama`/`shellingham`) is deferred to the
library-depth minor by recorded decision 2026-10-23.

```bash
uv run python -m benchmarks.library.run
uv run python -m benchmarks.library.run --json library.json
```

The differential test `tests/test_deps_receipt.py` runs the same steps
inside pytest. It is skipped by default and gated exactly like the one
PyPI-touching test in `tests/test_deps.py`:

```bash
PYSC_NETWORK_TESTS=1 uv run pytest -q tests/test_deps_receipt.py
```

CI runs it on the `network-receipt` job (ubuntu, clang). Nothing on the
default path reaches PyPI through this package and nothing reads
`benchmarks.baseline.run --check` state, so the frozen baseline gates are
untouched.

## The run, step by step

One PEP 723 script, `benchmarks/library/certifi_receipt.py`, with a
`[dependencies]` block naming `certifi` and nothing else. The committed
`pylock.certifi_receipt.toml` pins the resolution (certifi 2026.10.23,
wheel sha256 `e9b54de7...`), so a repeat run resolves without uv; the
content-addressed deps cache absorbs the wheel download after the first
run.

The compiler lowers certifi from the unpacked wheel's source root (the
one `deps` materialised), prunes what the import graph never reaches
(`certifi.__main__`, `certifi.tests`), and stages the wheel's data file
under `<binary>-data/certifi/cacert.pem` so `importlib.resources`
resolves there the way it does in CPython. `where()` reads that staged
path; `contents()` reads the PEM through the same handle.

The CPython reference is the same script with the unpacked wheel source
root on `PYTHONPATH` (the deps lane's own copy, not site-packages). The
runner requires both stdout runs to agree line for line and both side's
exit codes to be 0.

## The measured receipt (2026-10-24, macOS arm64, CPython 3.14.9)

certifi 2026.10.23, wheel `certifi-2026.10.23-py3-none-any.whl`, lane
`pure`, reason `none-any wheel, no extension modules`. Fences: 0 (from
the IR written beside the binary). Compiled binary: ~580 KB, runtime
well under the 60 s run timeout. Output, both sides:

```
235863
b'\n# Is' b'----\n'
cacert.pem
True
```

The four lines are the PEM byte length, the head and tail slices, the
tail of `where()` after `certifi/`, and the staged path shape as a
boolean. Raw `where()` paths differ between the lanes by construction
(the CPython side reads the cache root, the compiled side the staged
tree), so the receipt asserts on the shape, never the host prefix.

## Gotchas the receipt hit

- `str.encode` lowers the `'utf-8'` encoding only (PS2003). The first
  draft used `encode("ascii")` and the compile refused it. The PEM is
  ASCII, so `'utf-8'` produces identical bytes on both sides.
- A wheel's `tests` package and `__main__` are pruned before lowering:
  their fences do not block the compile, and certifi 2026.10.23 (which
  still ships its test suite inside the wheel) compiles with zero
  operations left to fence.
- The `deps` text report calls the wheel's imports "unknown" even though
  the wheel publishes `top_level.txt`: the local cache unpack predates
  the `.imports` recording and `.complete` short-circuits a re-unpack.
  Does not block the compile path; tracked in #562.

## What is not covered

- Timing. The net receipt times its rows against CPython; this receipt
  only proves the correctness half (lane, fences, matching output).
- TLS behaviour of the binary. `cacert.pem` staged and readable is not
  the same as a verification policy wired to it.
- A second package. certifi only. The other pure-lane pilots (idna) and
  the strong receipt from #137 stay where the recorded decisions put
  them: the library-depth minor.
- No compiler or deps-lane change is carried by this receipt: everything
  it exercises ships in the track's other issues (#398, #408, #539,
  #550).
