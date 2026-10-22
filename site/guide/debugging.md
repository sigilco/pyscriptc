---
title: Debugging and logs
description: Stage timings, fence tables, IR dumps, and audit logs.
---

## Compiler debug mode

`pyscriptc compile FILE --debug` prints a stage table to stderr: parse, check, lower, emit, link, each with wall time.
Narrow it with `--debug=stages,fences,cc,paths` or set `PYSC_DEBUG=1`.

```bash
pyscriptc compile app.py --debug=fences   # every fence with its span
pyscriptc compile app.py --debug-dir out/ # dumps ir.json, out.ll, cc.sh, summary.json
```

`--debug-dir` never changes the emitted IR; it only writes the intermediates next to it, so it is safe to diff two runs.

The full reference for the debug switches, the stderr topics and the `--debug-dir` artifacts lives in [`.agents/docs/debug.md`](https://github.com/sigilco/pyscriptc/blob/master/.agents/docs/debug.md) in the repo; `pyscriptc compile --help` lists the flags themselves.

## Reducer

`pyscriptc reduce FILE --fence PS1104` shrinks a failing source to the smallest variant that still reproduces, and writes it to `FILE.min.py`. Use it when a fence or a crash shows up in a file far bigger than the bug.

```bash
pyscriptc reduce big.py --fence PS1104 --json   # the removal log
pyscriptc reduce big.py --crash                 # still raises a non-fence error
pyscriptc reduce big.py --predicate 'pytest -x "$PSC_SOURCE"'  # your own check
```

The loop is AST-level and monotone: each pass drops one statement the bug does not need, re-checks, and keeps the removal only when the check still holds. No C toolchain is needed.

## Audit logs

Every invocation appends JSONL records to `~/.cache/pyscriptc/log/`.
INFO records are always on; `-v` / `--verbose` or `PYSC_DEBUG=1` adds DEBUG records for each pipeline stage.

```bash
pyscriptc logs            # tail the newest log
pyscriptc logs --follow   # stream while compiling elsewhere
```

Attach the log tail (or a `--debug-dir` dump) to a bug report and we can usually reproduce without your source.
