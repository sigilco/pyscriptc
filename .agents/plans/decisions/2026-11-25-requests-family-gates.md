# Recorded decision: the requests-family graduation gates are measured, and none of them is PS1101 or an import cycle (#1186, item 2)

Status: Proposed 2026-11-25

Refs: #1186, #1301 (item 1, merged), #413/#1264 (union erasure, merged 2026-11-15), #414/#1267 (reach-based pruning, merged), #598 (cycle false-positive fix, v0.7.0), #428 (the epic), the assessment comment on #1186 (2026-11-18).

## Decision

Both measured graduation gates for the rest of the requests family belong to the milestone that already carries the work that unblocks them, **v0.11.0**, and neither is a new gate:

1. **The PS1101 union gate is closed, not scheduled.** #413 retired the code: `PS1101` is no longer in the diagnostics registry (`src/pyscriptc/diagnostics.py`), `pyscriptc explain PS1101` answers "unknown diagnostic code", and a multi-arm union annotation now erases to its first arm with the later arms fenced at the use site as PS1003. Re-measured on the merged pins: the PS1101 fence count is **0** on requests, urllib3, charset_normalizer and idna. There is nothing left to assign under PS1101.
2. **There is no PS2002 import-cycle gate left to schedule.** `PS2002: circular import` (`src/pyscriptc/package.py` cycle refusal) fires **0** times across all four trees. #598 fixed the false-positive cycle on submodule relative imports in v0.7.0, and #414's reach-based pruning stops unreached dependency modules from being root fences at all. The issue's framing ("module-init-order vs import cycles gate urllib3 and requests") describes the pre-#414 contract.
3. **The measured graduation gates that remain, ranked by fences on the 2026-11-25 census**, are families the v0.11.0 theme ("library depth: the typing surface lowers") already covers or that already have open tickets, so each takes its milestone from its own ticket, not from this decision. No milestone is changed here; nothing new is opened by this decision; the existing tickets are the home of the work.

## The measurement (2026-11-25, master at 52f2bfeb + the #1301 pins, the regenerated census snapshot)

`pyscriptc coverage` over each pinned tree (`uv run python -m benchmarks.compat2.census --json data/benchmarks/census-snapshot.json`; the snapshot in this PR is regenerated from 2026-11-25, over the #414 pruning contract):

| package | version | defs | fenced defs | native defs | fences | top root families |
| --- | --- | --- | --- | --- | --- | --- |
| certifi | 2026.7.22 | 5 | 0 | 5 | 0 | (none) - graduated |
| idna | 3.20 | 56 | 35 | 21 | 140 | keyword-args 47, name-resolution 31, type-mismatch 14 |
| charset_normalizer | 3.5.2 | 148 | 52 | 96 | 195 | name-resolution 42, construct-other 27, type-mismatch 20 |
| urllib3 | 2.8.0 | 617 | 222 | 395 | 582 | name-resolution 97, unlowered-type-name 74, construct-other 56 |
| requests | 2.34.2 | 319 | 123 | 196 | 356 | name-resolution 45, unlowered-type-name 42, construct-other 23 |

Deltas this PR records against the issue's numbers (measured on the 2026-11-17 pins, pre-#414): `import-table` root fences on requests were 26, now 0; on urllib3 43, now 0; on charset_normalizer 5, now 0; on idna 2, now 0. Total root fences dropped requests 388→356 and urllib3 631→582. `circular import` was already 0 then and is 0 now.

## Where the remaining fences actually live (evidence for point 3)

- **`name-resolution` ("X is not a local or parameter") is the largest family on all four trees** (requests 45, urllib3 97, charset_normalizer 42, idna 31). The bulk is plain function locals the inference cannot prove (`fetch.py` in urllib3's emscripten contrib alone carries dozens) plus cross-module constants. #1339 (non-local captures, the largest census shape) is the open ticket that names this shape; the assignment-lowering work in #1370's vein covers the local half.
- **`unlowered-type-name` (PS1106) is the live remnant of the typing theme.** The unions themselves now erase; what fences is a *type alias* the module top level binds as a value-shaped statement. urllib3's 74 PS1106 fences are 44 alias annotations (`_TYPE_TIMEOUT` 8, `_TYPE_FIELD_VALUE` 7, `_TYPE_BODY` 6, ...) plus first-party class annotations (`BaseHTTPResponse`, `Url`) - the #1338 shape - and `Self`/`ssl.SSLContext`. requests' 42 are mostly same-package class annotations (`CookieJar` 8, `PreparedRequest` 6, `Response` 3). The typing alias *annotation* form lowers (#413, `X: TypeAlias = T`), but urllib3 spells its aliases as plain module assignments (`_TYPE_BODY = typing.Union[...]`), and the `typing.Union` right-hand side is a typing name used as a value (PS2002 `typing-value`, 10 fences on urllib3). Two smaller open shapes ride along: `typing.NamedTuple` bases (#1369, v0.11.0) and first-party class annotations (#1338).
- **`keyword-args` (47) and `call-arity` (10) gate idna**: `idna.check_hyphen_ok(label: str, *, ...)`. Idna's closed signatures in `package_spec.json` (6 of them) only need the keyword-args surface to score.
- **`construct-other`** is a mix of known tickets: multiple inheritance (v0.11.0 #732's class-body/base work), walrus, raise/except of lowered exceptions, subscript `del`, unpacking.

## Consequences

- The compat2 corpus carries the three new pins as family rows (21 v1 + 21 v2 visible pins, 147 visible probes), and the census snapshot, `compat.json`, `ladder.json` and `support.json` are regenerated under the post-#414 contract in the same PR as this decision. The regeneration is the receipt: it moves the import-table family from 109 fences across 12 packages (rank 7 in the old ranking) to 0.
- Follow-up work is named, not scheduled: the PS1106 alias/annotation remainder and the PS2002 typing-value remainder belong to the typing-surface tickets (#1338, #1369) that already exist; the keyword-args gate for idna is its own lowering ticket if the owner wants to pull it forward - pulling a ticket forward is a milestone change, never a doc edit.
- No frozen gate file is touched by this decision. `outcomes.py`, `score.py`, `coverage_map.py`, `cache.py`, `calibrate.py`, `CALIBRATION.json`, `TRIAGE.md`, `probes/*.jsonl` and `TIR_HASHES.json` are untouched; the census snapshot is a regenerated artifact with a `generated` date, not a frozen gate.

## What reopens this decision

A re-measurement that shows a `circular import` fence or a PS1101 fence on any pinned tree, or the owner wanting the keyword-args/idna gate pulled into a different release than its ticket's milestone.
