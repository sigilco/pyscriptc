---
name: Beta feedback
about: Report what you hit while testing a pyscriptc beta (vX.Y.Z-beta.N)
labels: beta
---

The type is set by the label, so the title needs no "Beta:" prefix.

## Beta version and channel

`pyscriptc --version` output, plus how you installed it: `uv tool install --prerelease allow`, `pip install --pre`, or the release binary (name the platform).

## What you tried

Which compiler surface: compile and run, project mode, async, FFI, threads, or something else.

## What happened

The smallest script that shows the problem, plus the exact command and the full output. If a compile stopped at a fence, attach `pyscriptc compile FILE --debug` output.

## Expected (CPython behavior)

If this is a wrong-output or wrong-exit bug: what CPython 3.14 prints or returns for the same script.
