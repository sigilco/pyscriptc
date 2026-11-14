# email-ai gap list verification on master a876d9e7 (pyscriptc 0.10.0b1, 2026-11-14)

Method: every probe in `.agents/probes/email-ai/` run with `uv run pyscriptc coverage`;
claims the probes do not cover verified with throwaway probes under `/tmp/emailai-gaps/probes/`
and by reading `src/pyscriptc/`. Compile probes run on macOS arm64 host with zig 0.16.0
and Apple clang 21.0.0.

## A. Type-system ceilings

| gap id | gap claim (short) | status on master | exact fence / evidence | probe or source path | existing issue | notes |
|---|---|---|---|---|---|---|
| A1 | no heterogeneous/dynamic JSON value; `dict[str, object]` fences PS1104/PS1107; json.loads/dumps require exactly-matching monomorphic type | STILL TRUE | `PS1104: object is a dynamic or structural type and is not lowered`; `json.loads` result must be a builtin container whose shape matches exactly - nested `dict[str, dict[str,str]]` works, mixed shape fails at runtime with `ValueError: JSON string expected`; dataclass result refuses with `PS2001: json.loads does not lower Envelope` | `g4_dict_object.py`, `/tmp/.../t_json_dataclass.py`, `/tmp/.../t_jsonhetero2.py` | #1226 | the runtime ValueError variants are PARTIAL vs the old claim: nested containers of the same type work, so "monomorphic only" is accurate but the runtime error story is worse than the doc says |
| A2 | no subtype polymorphism (PS1003) | STILL TRUE | `PS1003: return is Sub, annotation says Base` on return of subclass where base is annotated | `/tmp/.../t_polymorph.py` | #413 (unions/generics/forward-refs, v0.11.0) is adjacent but not the same; no issue tracks PS1003 covariance specifically | a return-type-covariance carve-out for nominal subclasses would be the minimal fix; see report |
| A3 | no multi-arm unions (PS1101); only `X | None` lowers | STILL TRUE | `PS1101: union types are not in the IR` on `str \| list[str]`; `str \| None` compiles clean | `/tmp/.../t_union.py`, `/tmp/.../t_optional.py` | #413 | #413 is v0.11.0 (typing surface lowers) and covers unions |
| A4 | annotation burden total: missing return annotation fences the whole function (PS1001); `module.Class` annotation form fails cross-module; plain classes need @dataclass + declared fields; @property unfloored | PARTIAL (3 of 4 FIXED) | missing return annotation now **compiles clean** (`t_noann.py`: `def helper(): return 1` lowers); `module.Class` annotation form STILL fences `PS1106: the annotation mod.Thing names a type that is not lowered` (y1 package probe) even though `import mod as m` + `m.Thing(...)` construction lowers and runs (x2/x3 probes); plain-class `self.x = ...` still fences `PS2001: AppConfig.bin_dir is not a field` (t_plainclass); `@property` now **compiles clean** (t_property2 runs, prints 1) | `t_noann.py`, `y1/`, `x2`, `x3`, `t_plainclass.py`, `t_property2.py` | #413 for the typing part | cross-module class resolution was fixed by `efc81a7b fix(lower): pkg class export on module.Class calls, isinstance bare-name ...` - calls work; only the *annotation form* `mod.Thing` still fences PS1106 |
| A5 | `from __future__ import annotations` silently demotes to island even when all imports are native | FIXED | identical trivial file with/without the import both compile native: `requiresLibpython: false, islandImports: []`, binary links only libSystem, prints ok. Source confirms `__future__` imports are accepted for known names (`_stdlib_bind.py:132-141` PS2002 only for non-type future imports) | `t_future.py` | - | claim is now wrong; verify which commit fixed it if needed |

## B. Stdlib surface

| gap id | gap claim (short) | status on master | exact fence / evidence | probe or source path | existing issue | notes |
|---|---|---|---|---|---|---|
| B6 | `email` module absent (PS2002) | STILL TRUE | `PS2002: email is not in the closed import table`; `PS2002: email.message is not in the closed import table` | `g7_email_mime.py` | #1228 | - |
| B7 | no socket/TLS client; `socket`/`http.server` fenced | STILL TRUE | `PS2002: http.server is not in the closed import table`; `PS2001: socket.socket is not in the lowered subset of socket`. `socket` and `ssl` ARE in the closed import table (stdlib_queue.py:57-59) but no usable surface is lowered; `_selectors.py:7` says "`socket` handle is #229" | `g10_http_server.py`, `g10b_socket.py`, stdlib_queue.py:57-59 | #219 (epic: outbound client, TLS, inbound server, fastapi port), #227 (design note inbound server), #229 (ps_httpd.c runtime) | the three HTTP issues cover it; B7 is a duplicate of #219+#229 |
| B8 | asyncio subprocess dead (PS1106) + `subprocess.run(timeout=)`/`input=` unfloored (PS2003) | STILL TRUE | `PS2003: subprocess.run keyword timeout is not lowered`; `PS2003: subprocess.run keyword input is not lowered`; `PS1106: the annotation asyncio.subprocess.Process names a type that is not lowered` (unannotated form fences PS1001 first). Pure `asyncio.run`/`await asyncio.sleep(0)` compiles clean; `asyncio.create_task(coro())` fences `PS2002: asyncio.create_task takes a coroutine, not another value`; `asyncio.wait_for` untested in isolation (nested def in resumable fences first, PS2002) | `g1_subprocess_timeout.py`, `g2_async_subprocess.py`, `t_sub_in.py`, `t_async.py`, `t_asyncio2.py` | #1223 (timeout), #1224 (async subprocess), #179 (asyncio run/create_task/gather/sleep/wait_for/timeout/cancellation, Future) | parent-side watchdog alternative also missing: `os.kill` fences `PS2001: os.kill is not in the lowered subset of os`; `signal` is not in the closed import table; `time.monotonic` lowers fine |
| B9 | `sys.stdin.readline`/`sys.stdout.flush` unfloored (PS2001) | STILL TRUE | `PS2001: sys.stdin.readline is not in the lowered subset of sys`; `PS2001: sys.stdout.flush is not in the lowered subset of sys` | `g3_sys_stdio.py` | #1225 | #478 (S6 UTF-8 I/O) is the v0.11.0 umbrella; overlap noted in report |
| B10 | scattered unfloored builtins: `file.read(n)`, `str.find(start)`, starred exprs, `**kwargs`, chained attribute calls, `print(<class>)`, `except <specific>` (only ValueError-class), `os.pipe`/`dup2`, `os.makedirs(exist_ok=)`, `shutil`/`base64` imports, `tomllib.load(file)` (only `.loads(bytes)`) | PARTIAL (3 of 12 FIXED) | STILL TRUE: `file.read(n)` → `PS2003: BytesIO.read expects 0 argument(s)` (g9, g6b); `str.find(start)` → `PS2001: call of attribute is not lowered (s.find)`; starred exprs → `PS2002: starred expression is not lowered`; `print(<class instance>)` → `PS2003: print is lowered for str, int, ... not class`; `except json.JSONDecodeError` → `PS2002: except names a lowered exception`; `os.pipe` → `PS2001: os.pipe is not in the lowered subset of os`; `os.dup2` → `PS2001: os.dup2 is not in the lowered subset of os`; `os.makedirs(exist_ok=)` → `PS2003: keyword arguments are not lowered`; `tomllib.load(f)` → `PS2001: tomllib.load is not in the lowered subset of tomllib`. FIXED: `**kwargs` in calls compiles clean (t_kwargs); chained attribute calls `result.stdout.rstrip().decode()` compile clean (t_chain); `import base64` + `b64encode` compiles clean (t_base64); `import shutil` + `shutil.which` compiles clean (t_shutil2) | g9, g6b, t_strfind, t_starred2, t_printclass, t_except, t_pipe, t_dup2, t_makedirs, t_tomllib_load, t_kwargs, t_chain, t_base64, t_shutil2 | #1227 (file.read(n)); others unfiled | `zero-arg hashlib.sha256()` still fences `PS2003: hashlib.sha256 expects 1 argument(s)` (g6) - not in the B10 list verbatim but in the probe README |
| B11 | `argparse` absent from the closed table | FIXED | `argparse` is in the closed table (stdlib_queue.py:78); a realistic parser with `ArgumentParser(prog=, description=)`, `add_argument("--count", type=int, default=1)`, `parse_args()` and `args.count` compiles **0 fences** (t_argparse) | t_argparse.py | #510 (CLOSED 2026, v0.8.0) | #510 tracked exactly this and closed it |

## C. Toolchain & distribution

| gap id | gap claim (short) | status on master | exact fence / evidence | probe or source path | existing issue | notes |
|---|---|---|---|---|---|---|
| C12 | `--onefile` zig driver broken on macOS arm64 (compiler_rt archive error); clang works | FIXED | `uv run pyscriptc compile top1.py --emit bin --call main --driver zig --onefile --embed-python=bundle` succeeds on arm64 darwin, zig 0.16.0: produces a 12.5M `.onefile` bundle dir + relocatable binary that runs and links only libz/libSystem | manual: `/tmp/.../top1zb.onefile` | #1145 is about `run` on bundle dirs, adjacent | `--onefile` now refuses a plain non-embed compile with `PS0001: --onefile needs --embed-python=bundle` - actionable. A bare zig non-onefile compile also works (`top1z` 671.7K Mach-O). The compiler_rt failure mode no longer reproduces |
| C13 | silent `libpython` linkage on any island demotion - binary looks fine, otool -L reveals host-dylib dependence | PARTIAL | the *native* tier cannot silently link libpython anymore: island-eligible code without `--island`/`--fallback` fails the compile with its fence (e.g. `import email` → PS2002, exit non-zero-ish diagnostics, no binary). With `--island` the island binary on this host links only libSystem (671.7K) - but that is because `islandImports` was empty for the probes that built; the embed modes (`host` default = host shared libpython when present) are the real surface and were not exercised end-to-end here. No build-time link audit was observed | manual: `t_island.py`, `t_island2.bin`, `_main.py:2111-2128` (`requiresLibpython` reported in `<out>.report.json`) | none | the old "looks fine but otool shows libpython" footgun: the compile refuses to produce the binary at all in the native tier now, which is the fix. What is missing: a post-link audit that fails loudly if the produced binary links host libpython when the user asked for native. UNVERIFIED in full: the `--embed-python=host` path on this machine did not trigger because every island probe resolved `islandImports: []` |
| C14 | no packaging/ecosystem story (no published packs equivalent to scriptc runtime packs / npm) | STILL TRUE | nothing in-tree ships a published library a consumer can `--ffi`-reference or import for JSON/MCP/SQLite; the FFI manifest pattern exists (`--ffi` docs, pyscriptc-sqlite precedent) but there is no pack registry | repo structure; spike report §7 | #1209 (pyscriptc-arrow adapter, Future) is the first concrete pack proposal | this is a strategy item, not a fence; the report treats it as such |

## "What would make it win" list

| # | claim | status |
|---|---|---|
| 1 | dynamic JSON value (A1) | = A1, STILL TRUE, #1226 |
| 2 | TLS + `email` + `http.server` in the closed table | STILL TRUE: #1228, #219/#229 |
| 3 | subprocess timeout + async story | STILL TRUE: #1223, #1224, #179 |
| 4 | binary-size story: `--onefile` verified-static macOS arm64 | PARTIAL: `--onefile --embed-python=bundle` works on zig now (C12 FIXED) but it is an island bundle (12.5M), not a 620K native binary; a *native* onefile is not a thing. C13's link audit is the open piece |
| 5 | fuzzed stdlib codecs (tested JSON/TOML/MIME layer) | UNVERIFIABLE as a product claim; there is no shipped MIME layer; `tomllib.loads` lowers and runs; `json` lowers for monomorphic shapes. No public test-evidence artifact to cite either way |

## Additional probe results not in the gap doc (context)

- `tarfile.open` STILL fences `PS2001: tarfile.open is not in the lowered subset of tarfile` (g5, t_wavetry) even though `import tarfile` and `import zipfile` both compile clean (in the closed table). The spike report claimed "gap appears closed" because its probe only imported the modules. The open-with-context-manager + `getnames` path is not lowered. PARTIAL at module level, STILL TRUE at the call.
- `os.environ` mapping access fences `PS2001: os.environ is not in the lowered subset of os`; `os.getenv(k, default)` lowers clean.
- `zipfile` import compiles clean (t_zip); no call-surface probe done.
- `asyncio.create_task` refuses `PS2002: asyncio.create_task takes a coroutine, not another value` for `create_task(asyncio.sleep(0))` - a coroutine IS what was passed; this looks like a real checker bug, separate from #179's scope. Filed as part of B8's evidence.

## Issues filed from this matrix (2026-11-14)

| issue | gap | milestone | labels | board (Horizon / Effort / Track) |
|---|---|---|---|---|
| #1241 | archive extract (tarfile/zipfile call surface) | v0.11.0 | compat-gap | Later / M / V2 compiler |
| #1242 | `module.Class` annotation form (PS1106) | v0.11.0 | compat-gap, lowering | Next / S / V2 compiler |
| #1243 | return-type covariance for nominal subclasses (PS1003) | v0.11.0 | compat-gap, lowering | Later / L / V2 compiler |
| #1244 | `os.kill` + the `signal` import (parent watchdog) | v0.10.0 | compat-gap | Current / M / V2 compiler |
| #1245 | `os.pipe` / `os.dup2` (fd control) | v0.11.0 | compat-gap | Next / M / V2 compiler |
| #1246 | `str.find(start)`, starred exprs, `print(<class>)`, `except <specific>` | Future | compat-gap, lowering | Later / M / V2 compiler |
| #1247 | post-link audit for host libpython linkage | v0.10.0 | compat-gap, tooling | Current / S / V4 CLI DX |
| #1248 | `hashlib` hash-object surface (zero-arg `sha256()`, `update()`) | v0.10.0 | compat-gap | Current / S / V2 compiler |

Not filed, with the existing issue that covers the gap: B7 socket/TLS/http.server
(#219, #227, #229), A3 multi-arm unions (#413), B9 stdio (#1225, overlapping #478),
B8 async subprocess (#1224, overlapping #179), B10 `os.makedirs(exist_ok=)` and
`os.environ.get` (#503), A1 dynamic JSON (#1226), B6 `email` (#1228),
B8 `timeout=` (#1223), B10 `file.read(n)` (#1227).
