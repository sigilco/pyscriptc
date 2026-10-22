<!-- Generated: edit README.tmpl.md, then run `uv run python scripts/render_readme.py`. -->
<p align="center"><img src="public/assets/logo-512.png" width="320" alt="pyscriptc"></p>

<h1 align="center">pyscriptc</h1>

<p align="center">Compile annotated Python to a native binary. No CPython at runtime.</p>

<p align="center">
<a href="https://pypi.org/project/pyscriptc/"><img alt="PyPI" src="https://img.shields.io/pypi/v/pyscriptc"></a>
<a href="https://pypi.org/project/pyscriptc/"><img alt="PyPI downloads per month" src="https://img.shields.io/pypi/dm/pyscriptc"></a>
<a href="https://github.com/sigilco/pyscriptc/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/sigilco/pyscriptc/actions/workflows/ci.yml/badge.svg"></a>
<a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-Apache--2.0-blue"></a>
</p>

pyscriptc compiles an annotated subset of Python to a native binary. Parsing is CPython's own `ast` module, and a type checker runs as a preflight. Anything the subset does not prove stops the compile with a named diagnostic (a fence), it never guesses. The resulting binary carries no interpreter and no Python dependency.

## Installation

```bash
uv tool install pyscriptc
```

Building a binary needs a clang (or `zig`) on your PATH; `pyscriptc doctor` checks. See [getting started](https://pyscriptc.pages.dev/guide/getting-started/) for other install methods and toolchain setup.

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

Compiled binary versus the same call under CPython 3.15.0, geometric mean per workload group, higher is better. Reference recorded 2026-10-15 on one Linux x86_64 host (8 CPUs, 5 runs per sample).

| Group | Time | Peak memory |
| --- | --- | --- |
| Kernels (sum-squares, floor-sum, horner, repeat-len) | 23.9x faster | 5.8x smaller |
| Scripting | 22.2x faster | 6.0x smaller |
| Stdlib calls | 14.4x faster | 5.2x smaller |

Source: [benchmarks/baseline/reference.json](https://github.com/sigilco/pyscriptc/blob/master/benchmarks/baseline/reference.json). Fresh numbers are in the job summary of the latest [nightly run](https://github.com/sigilco/pyscriptc/actions/workflows/nightly.yml). Reproduce with `uv run python -m benchmarks.baseline.run --check`.

## Using the stdlib

Builtins: 25 🟢, 22 🟡, 23 🔴. Stdlib: 0 🟢, 48 🟡, 2 🔴. 🟢 fully supported, 🟡 partial, 🔴 not supported. The practical summary and the fenced constructs: [The Python subset](https://pyscriptc.pages.dev/guide/language/). Full tables, module by module: [docs/supported.md](https://github.com/sigilco/pyscriptc/blob/master/docs/supported.md).

## Using PyPI packages

Declare dependencies in a PEP 723 `# /// script` block or `pyproject.toml`, then run `pyscriptc deps script.py`.

| Wheel lane | Status | Note |
| --- | --- | --- |
| pure (`none-any`), for example six, attrs, idna | 🟡 | resolved, locked and cached by `pyscriptc deps`, then compiled from source |
| native (ABI or platform wheels), for example orjson, numpy | 🔴 | refused by default: a native wheel needs a C ABI extern manifest |

Top 100 PyPI libraries: [docs/compat-top100.md](https://github.com/sigilco/pyscriptc/blob/master/docs/compat-top100.md).

## Documentation

[Documentation site](https://pyscriptc.pages.dev/), including the [CLI reference](https://pyscriptc.pages.dev/guide/cli/) and the [Python subset](https://pyscriptc.pages.dev/guide/language/).

## Contributing

See [Contributing](https://pyscriptc.pages.dev/guide/contributing/) for setup and conventions.

## License

Apache License 2.0. See [LICENSE](LICENSE).
