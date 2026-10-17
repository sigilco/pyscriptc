# pyscriptc debug logging

Structured audit logging lives in `src/pyscriptc/log.py` — a tiny
structlog-shaped, zero-dependency JSONL layer (the zero-dep rule rules out
structlog itself; the API is the same shape).

- Audit file: `~/.cache/pyscriptc/log/YYYYMMDD.jsonl` — one JSON record per
  line: `{ts, level, event, pysc, py, pid, ...kv}`. Rotates by day, capped
  near 10 MiB (oldest lines dropped). `PYSC_LOG_DIR` overrides the directory
  (use it in tests and repro scripts).
- Level: INFO by default. DEBUG via `PYSC_DEBUG=1` or the `--debug` flag on
  any subcommand.
- Support surface: `pyscriptc logs` prints the file path and its last ~50
  lines — that output is what users paste into issues.

Rules for agents:

- Never log source contents or file contents — only paths, event names,
  codes, counts, and exit codes. The audit file is local/user-facing, not
  telemetry, so paths are fine.
- Logging never raises: writes are swallowed on OSError. Keep it that way.
- Emit structlog-style events: `log.info("compile.start", source=...)`.
- Wire new CLI subcommands through `cli.invoke` automatically; add stage
  events (`compile.start`, `check.fences`, `emit.ir`, `emit.llvm`,
  `link.binary`, `run.start`, `run.exit`, `coverage.report`,
  `explain.lookup`) where the flow actually does the work.
