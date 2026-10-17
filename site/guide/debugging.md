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

## Audit logs

Every invocation appends JSONL records to `~/.cache/pyscriptc/log/`.
INFO records are always on; `-v` / `--verbose` or `PYSC_DEBUG=1` adds DEBUG records for each pipeline stage.

```bash
pyscriptc logs            # tail the newest log
pyscriptc logs --follow   # stream while compiling elsewhere
```

Attach the log tail (or a `--debug-dir` dump) to a bug report and we can usually reproduce without your source.
