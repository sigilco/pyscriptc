<p align="center"><img src="docs/assets/logo-512.png" width="270" alt="pyscriptc"></p>

<h1 align="center">pyscriptc</h1>

<p align="center">Compile annotated Python to a native binary.</p>

<p align="center">
<a href="https://pypi.org/project/pyscriptc/"><img alt="PyPI" src="https://img.shields.io/pypi/v/pyscriptc"></a>
<a href="https://pypi.org/project/pyscriptc/"><img alt="PyPI downloads per month" src="https://img.shields.io/pypi/dm/pyscriptc"></a>
<a href="https://github.com/sigilco/pyscriptc/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/sigilco/pyscriptc/actions/workflows/ci.yml/badge.svg"></a>
<a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-Apache--2.0-blue"></a>
</p>

pyscriptc compiles typed Python to native executables. It uses Python's inferred types and type hints to generate machine code from supported code.

pyscriptc is experimental, aims for CPython compatibility, and supports a subset of Python, its builtins and standard library, and selected PyPI packages. Code outside the subset stops the build with a diagnostic code (`PSxxxx`) that names the exact line, or runs through embedded CPython (fallback mode) when you opt in with `--island` or `--fallback=cpython`. Fallback mode links against the host CPython: it is not bundled, and a Python installation is expected on the machine.

## Installation

macOS and Linux:

```bash
curl -LsSf https://raw.githubusercontent.com/sigilco/pyscriptc/master/docs/install.sh | sh
```

Windows:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/sigilco/pyscriptc/master/docs/install.ps1 | iex"
```

The script checks the prerequisites, installs what it can, installs pyscriptc and runs `pyscriptc doctor`. It never uses `sudo` or an admin shell.

### Install with your coding agent

Paste this into your coding agent:

```text
Install pyscriptc from https://pyscriptc.pages.dev/llms.txt, run `pyscriptc doctor`,
then compile and run a hello world with it.
```

<details>
<summary>Install with uv</summary>

```bash
uv tool install pyscriptc
```

Building a binary needs a clang (or `zig`) on your PATH; `pyscriptc doctor` checks. See [Getting started](https://pyscriptc.pages.dev/getting-started/) for the full toolchain setup.

</details>

Windows: the `pyscriptc` binary runs there (experimental) and `coverage` works. Emitted programs are experimental cross-compilation only: `pyscriptc compile FILE --target x86_64-windows-gnu --driver zig` builds a Windows binary from Linux or macOS, but no CI job runs the output and Windows semantics are unverified. Native Windows compiles are not supported yet.

## Documentation

[Documentation site](https://pyscriptc.pages.dev/), including the [CLI reference](https://pyscriptc.pages.dev/reference/cli/) and the [Python subset](https://pyscriptc.pages.dev/guide/python-subset/).

For agents: [llms.txt](https://pyscriptc.pages.dev/llms.txt) and [llms-full.txt](https://pyscriptc.pages.dev/llms-full.txt).

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

The practical summary and the fenced constructs: [The Python subset](https://pyscriptc.pages.dev/guide/python-subset/).

## Using PyPI packages

We keep track of the share of the top 100 PyPI libraries that compiles. Mind that the compiler requires typed code. Per-module and per-library support status is tracked on the [compat site](https://pyscriptc.pages.dev/compat/), fed by the nightly CI workflow, so it cannot go stale the way a page would.

## Contributing

See [Contributing](https://pyscriptc.pages.dev/project/contributing/) for setup and conventions.

## License

Apache License 2.0. See [LICENSE](LICENSE).
