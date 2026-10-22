<p align="center"><img src="public/assets/logo-512.png" width="270" alt="pyscriptc"></p>

<h1 align="center">pyscriptc</h1>

<p align="center">Compile annotated Python to a native binary. No CPython at runtime.</p>

<p align="center">
<a href="https://pypi.org/project/pyscriptc/"><img alt="PyPI" src="https://img.shields.io/pypi/v/pyscriptc"></a>
<a href="https://pypi.org/project/pyscriptc/"><img alt="PyPI downloads per month" src="https://img.shields.io/pypi/dm/pyscriptc"></a>
<a href="https://github.com/sigilco/pyscriptc/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/sigilco/pyscriptc/actions/workflows/ci.yml/badge.svg"></a>
<a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-Apache--2.0-blue"></a>
</p>

pyscriptc compiles typed Python to native executables. It uses Python's inferred types and type hints to generate machine code from supported code. Static output runs without a Python runtime.

pyscriptc is experimental, aims for CPython compatibility, and supports a subset of Python, its builtins and standard library, and selected PyPI packages. For any code that's not supported, it can run it in CPython runtime. The CPython runtime is not bundled and is expected to be available at the host.

## Installation

```bash
uv tool install pyscriptc
```

Building a binary needs a clang (or `zig`) on your PATH; `pyscriptc doctor` checks. See [getting started](https://pyscriptc.pages.dev/guide/getting-started/) for other install methods and toolchain setup.

## Documentation

[Documentation site](https://pyscriptc.pages.dev/), including the [CLI reference](https://pyscriptc.pages.dev/guide/cli/) and the [Python subset](https://pyscriptc.pages.dev/guide/language/).

## Hello world

Create `hello.py`:

```python
def main() -> None:
    print("hello from a native binary")


if __name__ == "__main__":
    main()
```

```bash
pyscriptc run hello.py
pyscriptc compile hello.py --emit bin -o hello && ./hello
```

## Benchmarks

| Group | Time | Peak memory |
| --- | --- | --- |
| Kernels (sum-squares, floor-sum, horner, repeat-len) | 23.9x faster | 5.8x smaller |
| Scripting | 22.2x faster | 6.0x smaller |
| Stdlib calls | 14.4x faster | 5.2x smaller |

Compiled binary versus the same call under CPython 3.15.0, geometric mean per workload group, higher is better. Reference recorded 2026-10-15 on one Linux x86_64 host (8 CPUs, 5 runs per sample).

Source: [benchmarks/baseline/reference.json](https://github.com/sigilco/pyscriptc/blob/master/benchmarks/baseline/reference.json). Fresh numbers are in the job summary of the latest [nightly run](https://github.com/sigilco/pyscriptc/actions/workflows/nightly.yml). Reproduce with `uv run python -m benchmarks.baseline.run --check`.

## Using the stdlib

The practical summary and the fenced constructs: [The Python subset](https://pyscriptc.pages.dev/guide/language/).

## Using PyPI packages

We keep track of the share of the top 100 PyPI libraries that compiles. Mind that the compiler requires typed code. The per-library status lives in [docs/supported.md](https://github.com/sigilco/pyscriptc/blob/master/docs/supported.md) and the generated detail in [.agents/docs/compat-top100.md](https://github.com/sigilco/pyscriptc/blob/master/.agents/docs/compat-top100.md) (moving to the compat site, see [The Python subset](https://pyscriptc.pages.dev/guide/language/)).

## Contributing

See [Contributing](https://pyscriptc.pages.dev/guide/contributing/) for setup and conventions.

## License

Apache License 2.0. See [LICENSE](LICENSE).
