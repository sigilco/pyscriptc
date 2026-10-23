---
title: Trying the beta
description: Install the pyscriptc beta, what is in scope, how to report, and how to roll back.
---

A beta is an early, opt-in build of the next release. It exists so testers can break the next version before it becomes the stable tag. It never changes what a default install gets: `pip install pyscriptc` and `uv tool install pyscriptc` stay on the latest stable, because pip, uv and PyPI treat a beta as a pre-release and skip it unless asked.

Betas are tagged `v0.6.0-beta.1`, `v0.6.0-beta.2`, and so on. The GitHub release carries the same title, is marked as a pre-release (never Latest), and the wheel published to PyPI is the PEP 440 form of the same version, `0.6.0b1`.

## Install the beta

Compiling to a binary needs a C toolchain on PATH (`clang`, or `zig` for cross-compilation); see [Install and toolchain](install.md). Install the beta in a scratch environment: it replaces any pyscriptc already in that environment.

```sh
# uv tool (isolated tool slot)
uv tool install --prerelease allow pyscriptc==0.6.0b1

# pip (inside an activated virtualenv)
pip install --pre 'pyscriptc==0.6.0b1'
```

Prebuilt binaries ship on the [release page](https://github.com/sigilco/pyscriptc/releases) (macOS arm64 and x86_64, Linux x86_64 and aarch64, Windows x86_64 experimental). Each release carries a `sha256sums.txt`; verify the binary you downloaded against it before running it.

## What is in scope

Every compiler surface: the language subset (including whatever the release notes call out as new, such as project mode, async or FFI), `pyscriptc compile`, `run`, `doctor`, the emitted binaries, and the wheel install path. The beta is not the place to evaluate the compat numbers; those are measured separately on the [compat page](https://pyscriptc.pages.dev/compat/).

## How to report

Open an issue with the `beta-feedback` template: it asks for the version, the install channel and the smallest script that shows the problem. Attach `pyscriptc compile FILE --debug` output when a compile stops at a fence.

## Roll back to stable

Reinstall the stable channel over the beta; the stable version always wins its slot back:

```sh
uv tool install pyscriptc        # or: pip install pyscriptc
pyscriptc --version              # should print a plain X.Y.Z
```

If you installed a binary instead, replace it with the stable release binary, or delete it and use the install above.
