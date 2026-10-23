"""Self-host fence census totals."""

from __future__ import annotations

from pathlib import Path

from benchmarks.compat2.census_self import build_self_census

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "pyscriptc"


def test_self_census_ballpark() -> None:
    census = build_self_census(SRC)
    # doctor.py is the 28th module; log.py, telemetry.py, debug.py and rtcache.py each added a module.
    # T2-5 static folds: restlower/_folds.py adds a module (+15 fences of
    # fold-boundary docstrings and helpers, self-host only).
    # config.py (config surface: PEP 723, pyscriptc.toml/pyproject tables) adds 1 module.
    # coro.py (generator frames) adds one more.
    # stubs.py + sigregistry.py (stub reader, signature registry, not lowered): +2 modules.
    # imports.py (per-import lane classifier for the adoption ladder) adds one more.
    # deps.py (PyPI resolver, wheel cache) adds 1 module and about 125 fences, with its cli.py hook.
    # Script stdlib families (#188): +3 modules (restlower _ospath, _subprocess, _itertools) and +127 self-host fences.
    # restlower/_threading.py (native threading locks, #190) adds 1 module and 13 fences.
    # restlower/_asyncio.py (minimal async/await, #193) adds 1 module and 23 fences.
    # ffi_manifest.py (typed extern manifest parser, F0) adds 1 module and 77 fences.
    # restlower/_tomllib.py (tomllib.loads, #62) adds 1 module and 3 fences.
    # restlower/_yaml.py (yaml.safe_load, #254) adds 1 module and 5 fences.
    # Checker bridge (#87): factsbridge.py adds 1 module.
    # emitter.py (the emitter seam, #273) adds one more.
    # IR verifier (#272): irverify.py and llvverify.py add 2 modules.
    # Reducer (#275): reduce.py adds 1 module.
    # dwarf.py (the DWARF metadata, #280) adds one more.
    # Emitter split (#262): llvm.py becomes a shim and the emitter moves into
    # the emit/ package: 13 modules (emit/_ir.py, emit/llvm/_driver.py and the
    # eight area mixins, plus the two package inits) replace the one file.
    # zigshim.py (#291, the `pip install ziglang` PATH shim) adds 1 module.
    # restlower/_dotenv.py (dotenv_values / load_dotenv, #253) adds 1 module and 5 fences.
    # restlower/_yaml.py (yaml.safe_load, #254) adds 1 module and 5 fences.
    # restlower/_urllib.py (urlopen / Request, #225 #226) adds 1 module and 12 fences.
    # Project mode (#150): project.py adds 1 module.
    # restlower/_jwt.py and restlower/_orjson.py (the jwt and orjson packs,
    # #61 and #153) add 2 modules.
    # restlower/_queue.py (queue.Queue / LifoQueue / SimpleQueue, #172
    # Stage C) adds 1 module.
    # mobile.py and emitlib.py (#356, --emit lib and the mobile triples)
    # add 2 modules.
    # restlower/_asyncio_sync.py (the asyncio Lock, Event, Semaphore, Queue
    # and to_thread, #180 Stage D3) adds 1 module.
    assert census["modules"] == 94
    # E1 items 2+7a: print support added 3 self-host fences, then the
    # list.append flip removed 20 fenced call sites; headroom 14k for wave-C lowers.
    # telemetry.py added 36 fences, log.py 20, debug.py 6 union-type
    # fences (`X | None` parameters); R1–R5 batch un-fenced 10 self-host
    # call sites (list/dict/str methods).
    # PyrightFactsAdapter (typefacts.py) added 139 fences: subprocess/threads/
    # JSON-RPC stdio plumbing has no scriptc-subset equivalent; the `--facts`
    # CLI lane adds 21 more (same plumbing family).
    # Pyrefly/TSP adapter plumbing + deprecation markers added more.
    # Checker resolver (sysconfig/which/env probing) in check.py adds 12 more.
    # Telemetry v1 events + CLI --fail-on/--print/passthrough add 14 more.
    # doctor.py, env defaults and --emit asm/obj add 1 module and 34 fences.
    # PyreflyTspFactsAdapter (typefacts.py) adds 23 more.
    # v0.3.1: mypy/pyright/pyright-typeserver adapters removed from typefacts.py (-1343 to 1242).
    # v0.4.0: restlower.py split into a package of handler modules (+19 modules, +140 fences).
    # Telemetry privacy pass: random install id, IP-discard properties, opt-in switch (+12 fences).
    # rtcache.py (runtime object cache: threads, hashing, subprocess) adds 1 module and 33 fences.
    # First-run fix: node_text/op_text diagnostics helpers and CLI --run entry handling (+7 fences).
    # Optional widening (Stage U): total stays 1434; 2 typefacts `X | Y | None` fences
    # move from union-types (11 to 9) into optional-types (0 to 2, PS1102).
    # T2-3 parameter forms normalize into the named-parameter machinery (+1
    # _classes fence for the kwarg-dict binding path).
    # Optional widening (Stage U): total stays 1434; 2 typefacts `X | Y | None` fences
    # move from union-types (11 to 9) into optional-types (0 to 2, PS1102).
    # T2-3 parameter forms normalize into the named-parameter machinery (+1
    # _classes fence for the kwarg-dict binding path).
    # T2-5 static folds: restlower/_folds.py adds +15 fences (fold-boundary
    # docstrings and helpers, self-host only).
    # T2-2 ABC/Protocol erasure: +5 fences in _classes.py (the erased-base
    # and decorator spellings are self-host fenced branches).
    # config.py adds 37 self-host fences (tomllib parsing, dataclass, os/pathlib use).
    # coro.py (generator frame pass) adds 45 fences (dict-shaped IR annotations).
    # stubs.py + sigregistry.py (stub reader, signature registry, not lowered): +2 modules, +77 fences.
    # imports.py adds 1 module and 48 fences; the `pyscriptc imports` subcommand registration adds 16 fences in cli.py.
    # Program directories (R2): the import graph walk, init-owner map, cycle fence, `__main__` guard branch,
    # entry inference and `--program` plumbing add 16 self-host fences (package.py, lower/, cli.py).
    # runcache.py (binary cache for `pyscriptc run`) adds 1 module; the `run` subcommand adds fences in cli.py.
    # PEP 723 dependency lanes, stub-path and ffi keys add 17 self-host fences (config.py, cli.py).
    # E2 M2-M5 (exception hierarchy, object surface, ZeroDivisionError variants): +23 fences (log, telemetry, llvm).
    # math atan2/gcd/isclose handlers: +2 self-host fences in lower/_calls.py.
    # Checker bridge (#87): factsbridge.py adds 1 module and 40 self-host fences
    # (the mypy adapter, the facts-to-IR translator and the bridge consumer).
    # --sanitize (#271): the flag parsing and the sanitize argv plumbing add 14
    # fences in cli.py and 2 in llvm.py.
    # --timings (#277): the stage-table refactor adds 9 fences in cli.py and debug.py.
    # Emitter seam (#273): emitter.py adds 4 self-host fences (the dict-shaped
    # IR annotations on the module and function builders).
    # IR verifier (#272): irverify.py and llvverify.py add 2 modules and 42 self-host fences.
    # Reducer (#275): reduce.py adds 43 self-host fences; the `reduce`
    # subcommand adds 29 more in cli.py.
    # DWARF metadata (#280): dwarf.py adds a module with 10 self-host fences
    # (the metadata records), and the `--debug-info` plumbing adds 5 in
    # cli.py and 11 in llvm.py.
    # Emitter split (#262): the moved emitter methods carry the same fences.
    # The new module docstrings and import surfaces add 30 more across
    # emit/_ir.py, emit/llvm/_driver.py and the mixins.
    # restlower/_dotenv.py (dotenv_values / load_dotenv, #253) adds 5 more.
    # restlower/_yaml.py (yaml.safe_load, #254) adds 5 more.
    # restlower/_urllib.py (urlopen / Request, #225 #226) adds 12 more.
    # Checker bridge follow-ups (#258): the pyrefly lane and the package-tree
    # per-file bridges add 31 more in factsbridge.py.
    # Generator protocol (#166): close/throw/send/next-default surface in
    # coro.py, lower/_calls.py, lower/_classes.py and restlower/_iterators.py
    # add 11 self-host fences (new lowering code and its annotations).
    # Telemetry v2 (#257): the PSxxxx histogram, the binary size and the
    # run.finished event add 10 self-host fences in telemetry.py and cli.py.
    # Project mode (#150): project.py adds about 107 self-host fences (the
    # pyproject discovery, the entry-point reader and the entry wrapper).
    # restlower/_jwt.py (the jwt pack, #61) adds 16 fences; the orjson pack
    # (#153) adds 8 more (the handler and the emit branch).
    # FFI F1a (#218): the --ffi manifest wiring in stdlib_queue.py, lower/ and
    # cli.py adds 21 self-host fences (the manifest install, the FFI binding
    # clauses and the CLI flag plumbing). The module count is unchanged.
    # @contextmanager (#167): the manager class, the decorator recognition in
    # lower/_stdlib.py and lower/_classes.py add 5 self-host fences.
    # yield inside try (#164): the re-entrant try in the coro guard transform
    # adds 5 self-host fences in coro.py (the body re-raise, the handler
    # save/restore and the per-part guards).
    # restlower/_queue.py (queue.Queue, LifoQueue and SimpleQueue, #172) adds
    # 1 module and 15 self-host fences (the lowerer and its annotations).
    # FFI F1b (#224): the handle ownership checks in lower/ and the manifest
    # handle helpers add 14 self-host fences (the consume, borrow, free and
    # scope-exit clauses). The module count is unchanged.
    # Task lifetime (#179, stage D1): the create_task lowering in
    # restlower/_asyncio.py, the task-await marker and the task method
    # dispatch add 10 self-host fences; the emitter's none-field fix removes
    # one in emit/llvm/_stmts.py.
    # FFI F2 (#220): the link-step wiring in cli.py, emit/llvm/_driver.py and
    # ffi_manifest.py adds 11 self-host fences (the manifest unit split, the
    # PS4009 pre-build check and the link inputs). The module count is unchanged.
    # The wasm thread refusal (#172 Stage F) adds 6 self-host fences in
    # emit/llvm/_driver.py (the symbol table and the f-string match) and 1
    # in cli.py (the gate call after the emit).
    # generator perf receipt (#169): the env narrowing in the emitter adds
    # 1 self-host fence in emit/llvm/_stmts.py (the IR walk that collects
    # the slots a try body touches).
    # FFI F1c (#236): the typed container checks in ffi_manifest.py and the
    # dict-elem loop guard in lower/_stmt.py add 26 self-host fences. The
    # module count is unchanged.
    # mobile.py and emitlib.py (#356): the mobile registry, the library build
    # and the target-aware fences add 59 self-host fences.
    # Timeouts and cancellation (#179, stage D2): the wait_for lowering, the
    # deadline expansion in coro.py and the cancel/cancelled method dispatch
    # add 3 self-host fences; the asyncio.CancelledError class alias adds
    # none (the tables are data).
    # FFI F3 (#66): the checker stub-root plumbing in check.py adds 6 self-host
    # fences over master's 2818 (the `Sequence[Path] | None` annotations and
    # the mypy `mypy_path` line). The empty-tuple default the first cut used
    # would have dropped 25 fences instead: an empty tuple is not a lowered
    # constant, so the lowerer abandons the whole function body and the
    # fences inside it never reach the census. The `None` default keeps them.
    # Toolchain driver resolution (prefer zig with clang fallback): +5
    # self-host fences (cli.py +1, doctor.py +2, llvverify.py +2). The
    # emit/llvm/_driver.py lines carry no annotations the census counts.
    # Windows target compilation: +0 fences (the runtime C and the ps_path
    # surface are not self-hosted by the compiler; the `.exe` suffix branch
    # is plain Python with no annotations).
    # wasm32-wasi target (#312): the emitter's entry-symbol and `%.*s`
    # truncation, the wasm gate wiring and wasm_adapter.py add 13 self-host
    # fences.
    # wasm32-wasi-p2 (the WASI 0.2 component lane): the component wrapper and
    # the adapter module add 42 more.
    # D1/O1 (#432): the dynamic attribute protocol is refused by design, so
    # the `__del__` finalizer in typefacts.py (the pyrefly TSP adapter) is
    # now a PS4203 fence in the `refused-finalizer` family: +1 fence, the
    # module count is unchanged.
    assert census["fences"] == 2914
    # 22,164 lines after the `--facts` lane landed in typefacts.py.
    # IR verifier (#272): the two verifier modules add about 900 lines.
    # Project mode (#150): project.py adds about 240 lines.
    # yield inside try (#164): the re-entrant try in the coro guard transform
    # adds about 150 lines in coro.py. The asyncio primitives (#180 D3) add
    # about 400 in ps_async.c and restlower/_asyncio_sync.py.
    assert 11_000 <= census["lines"] <= 40_500
    assert sum(p["fences"] for p in census["packages"]) == 2914
