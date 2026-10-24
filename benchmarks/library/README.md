# Library receipt

The library receipt (issue #409): a program that depends on a pure-Python
PyPI package runs the real deps lane. certifi resolves from a committed
pylock, compiles from the unpacked wheel source, stages `cacert.pem`
beside the binary for `importlib.resources`, and its stdout is diffed
against CPython on the same machine. Opt-in: nothing in CI's default
path or `benchmarks.baseline.run --check` reads this package.

```bash
uv run python -m benchmarks.library.run
uv run python -m benchmarks.library.run --json library.json
```

The runner touches the network only when the deps cache is cold; the
compile needs zig or clang. The pytest form of the same receipt is
`tests/test_deps_receipt.py`, gated on `PYSC_NETWORK_TESTS=1` and run by
the `network-receipt` CI job. The details (measured numbers, gotchas,
scope) live in `.agents/docs/benchmarks/library-receipt.md`.
