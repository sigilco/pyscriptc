# pyscriptc vs scriptc — what would make pyscriptc win the email-ai port

**Context:** email-ai's ~7.1k LOC Rust CLI was spike-ported to both compilers (2026-11-14, branches `spike/scriptc-cli` / `spike/pyscriptc-cli` in espetro/email-ai; full findings in `/tmp/py-spike.md` on the VPS and `spikes/pyscriptc-cli/REPORT.md`). This document is the competitive gap list: what pyscriptc lacks today, and what it would take for pyscriptc — not scriptc — to be the right choice for this class of program (subprocess-heavy CLI + MCP stdio server + JSON contract + native binary distribution).

## Scoreboard

| | pyscriptc | scriptc 0.4.12 |
|---|---|---|
| Slice compiled | 0 fences / 75 defs, all native | 99–100% static, zero island |
| Binary | **620 KiB ELF** | 2.9–3.6 MB ELF |
| Linkage | libc/libm only, no libpython | libc/libm only |
| JSON ergonomics | **monomorphic only** → ~340 LOC hand-rolled codec | real `JSON.parse`/typed values |
| Polymorphism | **none** (PS1003) → tag dispatch | classes, structural typing |
| Effort to parity (this CLI) | ~19–28 days excl. Apple Mail; 35–50 incl. | ~1.5–2 weeks |
| MCP stdio | works via `os.read`/`os.write` workaround | `@chttp/mcp` published, zero code |
| Distribution | `--onefile` zig driver broken on macOS; silent libpython footgun | npm packages + runtime packs |

pyscriptc's wins today: **binary size (5× smaller)**, **self-owned roadmap** (every gap is fixable in-repo with probes already filed), and the **FFI vendored-amalgamation pattern** (`pyscriptc-sqlite`) which is genuinely elegant. Everything else lost on effort.

## Gap list, grouped by what it costs

### A. Type-system ceilings (the real port cost)

1. **No heterogeneous/dynamic JSON value.** `json.loads`/`dumps` require an exactly-matching monomorphic type; `dict[str, object]`/`Any` fence (PS1104/PS1107/PS2001). Real programs (himalaya envelopes, MCP messages, config) are heterogeneous. *Needed:* a tagged-dynamic value type that lowers natively — scriptc already solved this; it is the single highest-leverage fix.
2. **No subtype polymorphism** (PS1003). Traits/`AsyncBackend`-style seams must become tag dispatch. *Needed:* return-type covariance at least for dataclass/nominal subtypes, or a blessed union-dispatch pattern.
3. **No multi-arm unions** (PS1101). Only `X | None` lowers. *Needed:* `str | list[str]`-class unions or a sum type.
4. **Annotation burden is total.** Missing return annotation → PS1001 fences the whole function; `module.Class` annotation form fails cross-module (only `from pkg.mod import Class` resolves); plain classes need `@dataclass` + declared fields; `@property` unfloored. *Needed:* graceful degradation (fence the expression, not the function) and the `module.Class` form.
5. **`from __future__ import annotations` silently demotes to island** even when all imports are native — a footgun that will bite every real codebase. *Needed:* fix or warn loudly.

### B. Stdlib surface (closed import table + lowered subset)

6. **`email` module absent** (PS2002) — blocks Apple Mail `.emlx` MIME parsing; Python's biggest natural advantage over TS is exactly this.
7. **No socket/TLS client** — `socket`/`http.server` fenced; `ps_tls_backend()` hook exists but links nothing. Needed for any in-process IMAP/HTTPS. The transport contract is already clean; it needs an mbedTLS or SecureTransport backend via the FFI manifest pattern `pyscriptc-sqlite` proves.
8. **`asyncio` subprocess dead** (PS1106) + **`subprocess.run(timeout=)`/`input=` unfloored** (PS2003) — the Rust CLI sets timeouts on every himalaya call; without them a hung child hangs the CLI. *Needed:* timeout kwarg or `os.waitpid`-with-deadline FFI; `signal`/`os.kill` for the parent-side watchdog alternative.
9. **`sys.stdin.readline`/`sys.stdout.flush` unfloored** (PS2001). `os.read`/`os.write` works but pushes framing/buffering onto every caller. *Needed:* lower the stdio conveniences or document the fd pattern as first-class.
10. **Scattered unfloored builtins:** `file.read(n)`, `str.find(start)`, starred exprs, `**kwargs`, chained attribute calls, `print(<class>)`, `except <specific>` (only `ValueError`-class), `os.pipe`/`dup2`, `os.makedirs(exist_ok=)`, `shutil`/`base64` imports, `tomllib.load(file)` (only `.loads(bytes)`).
11. **`argparse` absent from the closed table** — every CLI needs hand-rolled parsing; `@cutils/cli` gives scriptc this for free.

### C. Toolchain & distribution

12. **`--onefile` zig driver broken on macOS arm64** (compiler_rt archive error); clang works. *Needed:* fix zig path or make the failure actionable.
13. **Silent `libpython` linkage on any island demotion** — binary looks fine, `otool -L` reveals host-dylib dependence, non-portable. *Needed:* build-time link audit that fails loudly (or `--static` guarantee).
14. **No packaging/ecosystem story.** scriptc ships runtime packs + npm distribution; pyscriptc has FFI manifests (good) but no equivalent of "published library that just compiles" for CLI parsing, MCP, SQLite, TOML. The email-ai spike hand-rolled JSON because nothing in the pyscriptc ecosystem provided it.

## What would make pyscriptc *win* (not just tie)

Ties get you parity; wins need a reason to pick Python over TypeScript for systems CLIs:

1. **The dynamic JSON value (A1)** — without it every port pays a ~340 LOC hand-rolled codec and a permanent fuzzing burden. With it, pyscriptc's Python ergonomics (dataclasses, `email` stdlib, `tomllib`) become genuinely nicer than TS for this workload.
2. **TLS + `email` + `http.server` in the closed table (B6–B7)** — unlocks in-process IMAP and MIME, the two places Python's stdlib beats the TS ecosystem. Combined with A1, a Python port could be *shorter* than the TS one.
3. **Subprocess timeout + async story (B8)** — subprocess is the CLI's entire I/O model; unfloored `timeout=` is the most common real-world fence.
4. **Binary-size story as a feature** — 620K vs 2.9MB is real for a privacy CLI shipped as a sidecar. If `--onefile` produced verified-static macOS arm64 binaries reliably, that's a headline.
5. **Fuzzed stdlib codecs** — if pyscriptc ships a *tested* JSON/TOML/MIME layer (rather than every consumer hand-rolling), it beats scriptc's "hope the npm package lowers cleanly" model for correctness-critical parsing.

## Suggested priority for pyscriptc agents

1. Dynamic/dynamic-ish JSON value type (A1) — unblocks the largest port cost.
2. `subprocess.run(timeout=)` + `sys.stdin`/`sys.stdout` lowers (B8, B9) — smallest diffs, biggest daily win.
3. `email` + `socket`/TLS backend via FFI (B6, B7) — the strategic unlock.
4. Subtype-polymorphism or union story (A2, A3) — second-largest structural cost.
5. `--onefile` macOS fix + link audit (C12, C13) — distribution credibility.
6. `argparse` + `shutil`/`base64`/`time`/`signal` in closed table (B10, B11) — breadth.

Repro probes for every diagnostic above are in `email-ai/` next to this file, and the spike's full per-construct table is in `spikes/pyscriptc-cli/REPORT.md` on branch `spike/pyscriptc-cli` (espetro/email-ai).
