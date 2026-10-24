---
title: pyscriptc
description: Compile annotated Python to a native binary.
---

# pyscriptc

Compile annotated Python to a native binary.

[Quickstart](/getting-started/) · [GitHub](https://github.com/sigilco/pyscriptc) · [PyPI](https://pypi.org/project/pyscriptc/)

## What it does

- **Native output**: a Python file lowers to a typed IR, then to LLVM, then to a standalone binary. The result has no interpreter inside.
- **Honest fences**: anything the subset does not prove is a fence with a diagnostic code (`PSxxxx`), not a guess.
- **CPython's own parser**: the front end is the `ast` module, so what parses is what CPython parses.
- **Cross-compile**: `zig cc` as the driver emits binaries for other targets via `--target`.
- **Zero runtime dependencies**: the CLI itself is stdlib-only and ships the C runtime it links.

## Try it now

```bash
uv tool install pyscriptc
pyscriptc compile examples/sum_squares.py --run --call sum_squares --args '[10]' -o /tmp/sum_squares
```

That prints `285`, the same value CPython returns, from a native binary.

## Read next

- [Quickstart](/getting-started/): install, compile, run.
- [Install and toolchain](getting-started/install/): clang, zig, doctor.
- [The Python subset](/guide/support-model/): what lowers, what fences.
- [Mobile apps](/guide/mobile/): a static library for iOS and Android.
- [CLI](/reference/cli/): every subcommand and flag.
- [Stability and contracts](/reference/stability/): frozen tiers, driver API, and internal surfaces.
- [Examples](/examples/): programs that compile today.
