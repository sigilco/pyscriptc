---
title: Debugging and logs
description: Stage timings, refusal tables, IR dumps, and audit logs.
---

## Compiler debug mode

`pyscriptc compile FILE --debug` prints a stage table to stderr: parse, check, lower, emit, link, each with wall time.
Narrow it with `--debug=stages,fences,cc,paths` or set `PYSC_DEBUG=1`.
`--timings` prints the same table on its own.

```bash
pyscriptc compile app.py --debug=fences   # every unsupported construct with its span
pyscriptc compile app.py --debug-dir out/ # dumps ir.json, out.ll, cc.sh, summary.json
```

`--debug-dir` never changes the emitted IR; it only writes the intermediates next to it, so it is safe to diff two runs.

The full reference for the debug switches, the stderr topics and the `--debug-dir` artifacts lives in [`.agents/docs/debug.md`](https://github.com/sigilco/pyscriptc/blob/master/.agents/docs/debug.md) in the repo; `pyscriptc compile --help` lists the flags themselves.

## The `PYSC_MIR` backend lane

`PYSC_MIR=1 pyscriptc compile FILE` routes the functions the new MIR backend covers through it instead of the default LLVM emitter. It is an escape hatch for comparing the two backends, not a feature switch: the lane is experimental, off by default, and any value other than empty or `0` turns it on for the whole compile.
The lane is held to byte-identical output, so on a native target a program behaves exactly as without it. Only scalar leaf functions (`int`, `float` and `bool` parameters, locals and returns, with no calls, containers, strings or closures) take the lane; every other function and the module's entry point still come from the default emitter.
What differs or fails with `PYSC_MIR` set:

- A `wasm32` target (`--target wasm32-wasi-musl`, `wasm32-wasi-p2`) emits the program entry point as `@main` instead of `@__main_argc_argv`, so the wasm link fails. Unset `PYSC_MIR` for wasm builds.
- `--emit binding` ignores the variable and always uses the default emitter.
- Any other difference from the default output is a bug: report it with both `.ll` files (`--emit llvm` with and without `PYSC_MIR=1`).

## Reducer

`pyscriptc reduce FILE --fence PS1104` shrinks a failing source to the smallest variant that still reproduces, and writes it to `FILE.min.py`. Use it when a `PSxxxx` code or a crash shows up in a file far bigger than the bug.

```bash
pyscriptc reduce big.py --fence PS1104 --json   # the removal log
pyscriptc reduce big.py --crash                 # still raises an error that is not a PSxxxx code
pyscriptc reduce big.py --predicate 'pytest -x "$PSC_SOURCE"'  # your own check
```

The loop is AST-level and monotone: each pass drops one statement the bug does not need, re-checks, and keeps the removal only when the check still holds. No C toolchain is needed.

## Audit logs

Every invocation appends JSONL records to `~/.cache/pyscriptc/log/`.
INFO records are always on; `-v` / `--verbose` or `PYSC_DEBUG=1` adds DEBUG records for each pipeline stage.

```bash
pyscriptc logs   # print the log path and its last 50 lines
```

Attach the log tail (or a `--debug-dir` dump) to a bug report and we can usually reproduce without your source.
The logging directives are in [`.agents/docs/pyscriptc-debug.md`](https://github.com/sigilco/pyscriptc/blob/master/.agents/docs/pyscriptc-debug.md).
