# pyscriptc priority backlog: the email-ai competitive gaps, ranked

Replaces the "Suggested priority" section of `.agents/probes/email-ai-vs-scriptc.md`.
Ranking rule: (port cost removed) / (effort and risk), in pyscriptc's own vocabulary.

Vocabulary used: the fence code and family, the existing issue number, the milestone
(`v0.10.0` / `v0.11.0` / `Future`), the board `Effort` vocabulary (S/M/L/XL), the board
`Track` vocabulary (V1 oracle, V2 compiler, V3 corpus, V4 CLI DX, V5 harness).

Status comes from the verification matrix (`/tmp/emailai-gaps/verification-matrix.md`),
measured on master `a876d9e7` (pyscriptc 0.10.0b1).

## Ranked backlog

| rank | gap | fence code / family | status | issue | milestone | effort | track | why this over the item above/below |
|---|---|---|---|---|---|---|---|---|
| 1 | **subprocess timeout + parent watchdog** (`subprocess.run(timeout=)`, `input=`, `os.kill`, `signal`) | PS2003 (call-arity), PS2001 (stdlib:os), PS2002 (import-table) | STILL TRUE | #1223 | v0.10.0 | M | V2 compiler | smallest diff that removes the single most common real-world fence: `timeout=` is the one keyword every CLI sets on every child process, and today the only native answer is to hang. Below it only because #1223 already exists and its scope is the keyword, not the watchdog primitive (`os.kill`/`signal` are a second, unfiled chunk) |
| 2 | **stdio line reader** (`sys.stdin.readline`, `sys.stdout.flush`) | PS2001 (stdlib:sys) | STILL TRUE | #1225 (overlaps #478) | v0.10.0 | S | V2 compiler | an MCP stdio server is a line-oriented JSON-RPC loop; `os.read(0, n)` already works so this is a thin buffered layer over an existing runtime primitive, not new machinery. Cheapest item on the list, so it sits directly under the timeout keyword: both are one-day-ish diffs on the same subprocess/IO theme |
| 3 | **tagged dynamic JSON value** (`dict[str, object]`, `Any`, heterogeneous `json.loads`/`dumps`) | PS1104 (dynamic-annotation), PS1107, PS2001 (json.loads does not lower X) | STILL TRUE | #1226 | v0.11.0 | XL | V2 compiler | the largest port cost removed of anything here (the spike hand-rolled a ~340 LOC JSON reader and writer because of it), but it is XL and touches the type system, so it cannot be first. It ranks above everything below because nothing else in the list removes a whole class of rewrites |
| 4 | **`email` in the closed import table** | PS2002 (import-table) | STILL TRUE | #1228 | v0.11.0 | L | V2 compiler | a table row plus a runtime helper, and it unlocks the one place Python's stdlib beats the TS ecosystem outright (MIME). Below the JSON value because a MIME parser without a dynamic value still needs the port to hand-roll its own message model |
| 5 | **streaming read** (`file.read(n)`, `hashlib` object form) | PS2003 (call-arity) | STILL TRUE | #1227 | v0.10.0 | S | V2 compiler | one-argument `read` on a binary file; the runtime already has the primitive (whole-file read lowers), so this is an arity row, not a runtime. It ranks above the HTTP work because it is S and it blocks the SHA-256-over-archive path in the same installer flow as the archive extract |
| 6 | **archive extract** (`tarfile.open`/`getnames`/`extractall`, `zipfile` equivalent) | PS2001 (stdlib:tarfile) | STILL TRUE (module imports clean, call path does not) | new | v0.11.0 | M | V2 compiler | `import tarfile` and `import zipfile` already compile clean (they are table rows), so only the call surface is missing; that is a smaller job than the HTTP stack below. It ranks below the streaming read because both serve the same installer flow and the read is smaller |
| 7 | **HTTP/TLS/socket surface** (`socket`, `ssl`, `http.server`) | PS2001 (stdlib:socket), PS2002 (import-table) | STILL TRUE | #219 (epic), #227, #229 | Future | XL | V2 compiler | the strategic unlock (in-process IMAP, a real HTTP server), but XL with an open design question (#227) and it duplicates three existing issues, so it stays parked rather than being re-filed |
| 8 | **async subprocess** (`asyncio.create_subprocess_exec`, `Process`, `PIPE`, `communicate`) | PS1106 (unlowered-type-name), PS2002 (name-resolution) | STILL TRUE | #1224 (overlaps #179) | Future | L | V2 compiler | a coroutine engine already lowers, so the adapter is the missing piece, but the async surface (#179) is deliberately Future and this depends on it. Above #9 only because it is the async twin of rank 1 and a real CLI consumer wants both |
| 9 | **multi-arm unions + `module.Class` annotation form** | PS1101 (unions), PS1106 (unlowered-type-name) | STILL TRUE | #413 covers unions; the annotation form is unfiled | v0.11.0 | L | V2 compiler | #413 is already scheduled in v0.11.0 for unions and forward references, so the union half needs no new ticket; only the `module.Class` annotation form (`def use(t: mod.Thing) -> str:` still fences even though `mod.Thing(...)` construction lowers) is unfiled, and it is a small addition to #413's scope rather than a new effort |
| 10 | **return-type covariance for nominal subclasses** | PS1003 (type mismatch) | STILL TRUE | new | v0.11.0 | L | V2 compiler | a real port cost (every trait in a Rust CLI becomes tag dispatch) but it is a type-system change with a narrow safe carve-out, and it ranks below the union work because #413 already builds the erasure machinery it needs |
| 11 | **`os` script-surface keywords** (`os.makedirs(exist_ok=)`, `os.environ`, `os.pipe`, `os.dup2`, `os.kill`) | PS2003 (keyword args), PS2001 (stdlib:os) | PARTIAL | #503 covers `makedirs(exist_ok=)` and `environ.get`; `os.pipe`/`dup2`/`kill` unfiled | v0.11.0 | M | V2 compiler | #503 already carries `makedirs` and `environ.get`, so only the pipe/dup2/kill trio is new; it ranks low because the fd workarounds (`os.read`/`os.write`, `/bin/sh -c` redirect) exist and only bite programs that must talk to a child's stdin |
| 12 | **`str.find(start)`, starred expressions, `print(<class>)`, `except <specific>`** | PS2001 (call of attribute), PS2002 (starred expression / except names a lowered exception), PS2003 (print) | STILL TRUE | new | Future | M | V2 compiler | four small but individually cheap fences; each has a one-line workaround (copy the attribute to a local, build the list with append, wrap in `str(...)`, catch `ValueError`), so they cost a consumer very little. They rank last because the workaround tax is low, not because the work is |
| 13 | **native link audit** (fail loudly when a produced binary links host libpython) | not a fence (toolchain) | PARTIAL | new | v0.10.0 | S | V4 CLI DX | the native tier already refuses to produce a binary that would silently demote, so the remaining gap is a post-link audit, which is S. It ranks low on cost removed but it is cheap and it is distribution credibility, so it is worth pulling into v0.10.0 rather than the Future |

Items dropped from the original gap list because they no longer hold: B11 `argparse`
(#510 closed it), C12 `--onefile` zig on macOS arm64 (works now), A5
`from __future__ import annotations` demotion (does not demote), A4's "missing return
annotation fences the function" and "`@property` unfloored" (both compile clean now),
B10's `**kwargs`, chained attribute calls, `shutil` and `base64` imports (all compile
clean now). C14 (no packaging/ecosystem story) is a strategy item, not a fence: it stays
out of this table and rides on #1209 (pyscriptc-arrow, the first published pack).

## Milestone placement

- **v0.10.0** (ecosystem and embed breadth, the current minor): ranks 1, 2, 5, 13.
  These are the S/M items that remove a fence a consumer hits in the first hour.
- **v0.11.0** (library depth, the typing surface lowers): ranks 3, 4, 6, 9, 10, 11.
  #413 already sits here; the dynamic JSON value and the typing work belong to the
  same milestone theme.
- **Future** (unplanned): ranks 7, 8, 12. #219, #227, #229 and #179 already live there.

A milestone change is a milestone change: set it on the issue, never as a doc edit.

## Measured receipts for the top three

The release policy requires a measured receipt for a minor release: a census delta, a
boxed ledger window, or a perf number with a baseline.

1. **#1223 subprocess timeout (rank 1).** Receipt: a compat2 census delta on the
   PS2003 call-arity family (the `subprocess.run` keyword rows) plus a differential
   probe that runs a child that sleeps past the timeout and byte-compares stdout,
   stderr and the exit code against CPython 3.14 (`subprocess.TimeoutExpired` and the
   killed child). Second receipt, if the watchdog lands: the baseline perf gate
   (`mise run bench:baseline`) shows no regression against the recorded baseline for
   the subprocess workload, because the watchdog adds a thread or a poll loop.
2. **#1225 stdio line reader (rank 2).** Receipt: a census delta on the PS2001
   `stdlib:sys` family (the `sys.stdin`/`sys.stdout` rows) plus a differential probe
   that reads a fixed JSON-RPC line from stdin and writes it back, byte-compared
   against CPython 3.14. The measured number to record is the fence count for the
   `stdlib:sys` family before and after, from `pyscriptc infer --json` (per-family
   fence deltas, #1171) or the compat2 census.
3. **#1226 tagged dynamic JSON value (rank 3).** Receipt: a census delta on the
   PS1104 dynamic-annotation family and the PS1101 union family over the compat
   corpus (both are large families: `PS1104` and `PS1107` on rich alone are 138 and
   136 fences), recorded as the delta in the PR without touching the frozen gate
   files. Second receipt: a differential probe that round-trips a heterogeneous
   JSON document (scalar, list, nested object, null) through `json.loads` and
   `json.dumps` and byte-compares the output against CPython 3.14.
