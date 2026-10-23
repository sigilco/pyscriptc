"""The LLVM emitter: the composed `Emitter` class and the toolchain entry points.

`Emitter` is the mixin composition of the areas under `emit/llvm/`. The
driver owns the two things that are not emitter areas: the public entry
points (`emit_llvm`, `standalone_entry`) and the toolchain calls that turn
emitted text into a binary (`link_binary`, `compile_unit`).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from pyscriptc import rtcache, zigshim
from pyscriptc.dwarf import DwarfEmitter
from pyscriptc.emit.llvm._abi import AbiMixin
from pyscriptc.emit.llvm._calls import CallsMixin
from pyscriptc.emit.llvm._core import CoreMixin
from pyscriptc.emit.llvm._exprs import ExprsMixin
from pyscriptc.emit.llvm._funcs import FuncsMixin
from pyscriptc.emit.llvm._print import PrintMixin
from pyscriptc.emit.llvm._stdlib import StdlibMixin
from pyscriptc.emit.llvm._stmts import StmtsMixin
from pyscriptc.errors import IRError
from pyscriptc.ir import assert_same_cpython


def _cc_argv(cc: str) -> list[str]:
    """The cc prefix argv, with `zig` resolved through the ziglang shim.

    `pip install ziglang` ships the zig binary without a `zig` console
    script, so a wheel-only install has nothing on PATH. When the name does
    not resolve and the ziglang package does, the shim installed by
    `pyscriptc.zigshim` is used directly.
    """
    argv = cc.split()
    if argv and argv[0] == "zig" and shutil.which("zig") is None:
        resolved = zigshim.resolve_zig()
        if resolved is not None:
            argv[0] = resolved
            if len(argv) < 2 or argv[1] != "cc":
                argv.insert(1, "cc")
    return argv


class Emitter(
    AbiMixin,
    CallsMixin,
    CoreMixin,
    ExprsMixin,
    FuncsMixin,
    PrintMixin,
    StdlibMixin,
    StmtsMixin,
):
    """Emits the textual LLVM IR of one module.

    The areas are mixins, one per concern, so the file that owns a piece of
    emission is the file that owns it. The composition order does not matter:
    no area calls `super()`, they all go through `self`.
    """


def emit_llvm(
    module: dict[str, Any],
    call: str | None = None,
    args: list[Any] | None = None,
    standalone: bool = False,
    debug_info: bool = False,
    library: bool = False,
) -> str:
    """With `standalone` and no `call`, main does nothing: module level code is not lowered.

    `library` is the `--emit lib` shape: no `main`, an exported
    `pyscriptc_main` that initializes the runtime and runs the module entry,
    and default visibility on the exported symbols while everything else
    stays hidden.
    """
    assert_same_cpython(module)
    if module["fences"]:
        raise IRError("refusing to emit LLVM while fences are present")
    if not module["functions"]:
        raise IRError("module has no functions to emit")
    dwarf = DwarfEmitter(module["sourceFile"]) if debug_info else None
    return Emitter(module, dwarf).emit(call, [] if args is None else args, standalone, library=library)


def sanitize_flags(sanitize: list[str] | None) -> list[str]:
    """`-fsanitize=a,b` argv fragment, or [] when no sanitizers are requested."""
    if not sanitize:
        return []
    return ["-fsanitize=" + ",".join(sanitize)]


# The runtime's thread primitives are pthreads. A wasm target has no
# working pthread_create: wasi-libc stubs it to return EAGAIN, and its
# pthread_cond_wait traps. The link would succeed and the program would fail
# at run time with a libc error that names neither the target nor the reason.
# The refusal names both, at link time, before the sysroot is touched.
_WASM_TARGETS = ("wasm32",)
# The symbols that need a second thread. `ps_lock_*` is not here: a lock on
# the one thread a WASI program has is a working mutex.
_THREAD_SYMBOLS = (
    "ps_thread_start2",
    "ps_thread_new",
    "ps_queue_new",
    "ps_cond_init",
    "ps_sem_new",
    "ps_event_new",
)


def refuse_unsupported_target(text: str, target: str | None) -> None:
    """Refuse a target that cannot run the runtime's thread primitives.

    The check is on the emitted text, not on the source: the runtime links
    every `.c` file, but the thread primitives only reach the binary when
    the program uses them. A program that never starts a thread links fine
    for wasm; a program that does gets the refusal instead of a libc error
    at run time.
    """
    if target is None:
        return
    if not target.startswith(_WASM_TARGETS):
        return
    # The IR names a global as `@symbol(`, both in its declaration and in
    # every call. Anchoring on that form keeps a colliding name (say
    # `not_ps_thread_start2`) from reading as a use of the runtime symbol.
    used = [symbol for symbol in _THREAD_SYMBOLS if f"@{symbol}(" in text]
    if not used:
        return
    raise IRError(
        f"the {target} target has no working pthreads, so the runtime's "
        f"thread primitives ({', '.join(used)}) cannot be used there; "
        "threads are a native-only surface for now (issue #172)"
    )


def link_binary(
    llvm_path: Path,
    output: Path,
    driver: str = "clang",
    target: str | None = None,
    record: list[list[str]] | None = None,
    link_driver: str | None = None,
    sanitize: list[str] | None = None,
    timings: dict[str, float] | None = None,
    debug_info: bool = False,
    compile_units: list[Path] | None = None,
    link_units: list[Path] | None = None,
    link_flags: list[str] | None = None,
    include_dirs: list[Path] | None = None,
) -> None:
    """Link `.ll` text plus the C runtime into `output`.

    Two-stage: `.ll -> .o`, then objects + runtime `*.c` link. Required for
    `zig cc` on macOS (a `.ll` on the link line writes an empty binary there)
    and keeps per-target artifacts clean. `driver` is `clang` or `zig`
    (`zig cc`); `target` is a zig-style triple and requires the zig driver —
    it bundles the per-target sysroot that `clang --target` does not.
    `sanitize` is a `-fsanitize` name list (ASan+UBSan diagnostics for the
    runtime and the emitted code); it applies to every compile and the link,
    and the runtime object cache keys on it.
    `debug_info` asks cc for `-g` so the DWARF metadata in the `.ll` survives
    into the object file. On Darwin the linker moves the DWARF out of the
    executable into a `.dSYM` bundle, so the link step runs `dsymutil` to
    produce it; on ELF the sections stay in the binary.
    When `timings` is a dict, per-step cc/link wall milliseconds are added
    into it (`cc_compile`, `cc_link`).

    The `--ffi` manifest units join the same link shape as the runtime
    (`.agents/docs/ffi/ffi-manifest.md` section 4): `compile_units` are C
    sources the target cc compiles in the per-unit `.o` loop, `link_units`
    are prebuilt archives and objects that go straight on the link line, and
    `link_flags` are the per-manifest `-L`/`-l` arguments. Every one of them
    is optional, so a build with no manifest produces exactly the link line
    it produced before the FFI gate.
    `include_dirs` are added as `-I` to the manifest unit compiles only, so a
    vendored header next to the manifest resolves without touching the
    runtime's own include surface.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    if driver == "zig":
        # `pip install ziglang` alone satisfies this: the wheel ships the zig
        # binary but only exposes `python-zig`, so pyscriptc installs a `zig`
        # PATH shim pointing at `python -m ziglang`. A stale shim (its venv
        # is gone) is not trusted and gets rebuilt or rejected here.
        zigshim.ensure_on_path()
    if target is not None and driver != "zig":
        raise IRError("--target requires --driver zig (bundled sysroot per target)")
    cc = "zig cc" if driver == "zig" else driver
    runtime = runtime_dir()
    text = llvm_path.read_text(encoding="utf-8")
    # The refusal comes before the sysroot is touched: a program that
    # cannot run threads on the target should not reach the compiler at all.
    refuse_unsupported_target(text, target)
    sources = [str(path) for path in sorted(runtime.glob("*.c"))]
    target_flags = ["-target", target] if target is not None else []
    san_flags = sanitize_flags(sanitize)
    dbg_flags = ["-g"] if debug_info else []
    tail = [*san_flags, "-lm", "-o", str(output)]
    if "ps_island_" in text:
        if target is not None:
            raise IRError(
                "island fallback embeds the host CPython; "
                "cross-compiling it needs a cross-built libpython (explain-only)"
            )
        import sysconfig

        include = sysconfig.get_path("include")
        libdir = sysconfig.get_config_var("LIBDIR")
        version = sysconfig.get_config_var("VERSION")
        target_flags.extend(["-I", include])
        tail = [f"-L{libdir}", f"-lpython{version}", f"-Wl,-rpath,{libdir}", *tail]
    else:
        sources = [source for source in sources if not source.endswith("ps_island.c")]
    # The `--ffi` manifest units join the same per-unit compile loop as the
    # runtime: a `.c` unit compiles like a runtime file, an archive goes
    # straight on the link line (`.agents/docs/ffi/ffi-manifest.md` section 4).
    # A missing unit is refused before the compiler runs, so the failure names
    # the manifest that declared it instead of surfacing as a cc error.
    _require_units(compile_units, link_units)
    ffi_sources = [str(unit) for unit in compile_units or []]
    ffi_archives = [str(unit) for unit in link_units or []]
    sources.extend(ffi_sources)
    # zig cannot compile C sources inside a link invocation for Mach-O, so
    # every unit goes to .o first and the link line carries objects only.
    use_cache = rtcache.prepare()
    ll_obj = output.with_name(f"{output.name}.{llvm_path.stem}.o")
    ll_kind = ["-x", "ir"] if driver == "clang" else []
    ll_argv = _cc_argv(cc)
    ll_argv = [
        *cc.split(),
        "-O2",
        *dbg_flags,
        *san_flags,
        *ll_kind,
        "-c",
        str(llvm_path),
        "-o",
        str(ll_obj),
        *target_flags,
    ]
    jobs: list[tuple[list[str], Path | None]] = [(ll_argv, None)]
    objects = [ll_obj]
    hits = 0
    # A manifest unit compiles against the manifest's own directory (vendored
    # headers) and against the runtime directory (ps_rt.h, the contract the
    # thunk is written against). The runtime units gain nothing.
    ffi_include_flags = ["-I", str(runtime)]
    for directory in include_dirs or []:
        ffi_include_flags.extend(["-I", str(directory)])
    # The manifest units carry the manifest's own include dirs; the runtime
    # units keep the flags they always had, so a manifest-free build compiles
    # the runtime exactly as it did before the FFI gate.
    manifest_sources = frozenset(ffi_sources)
    for source in sources:
        src = Path(source)
        flags = ["-O2", *san_flags, *target_flags]
        extra_dirs: tuple[Path, ...] = ()
        if source in manifest_sources:
            flags.extend(ffi_include_flags)
            extra_dirs = (runtime, *(include_dirs or []))
        if use_cache:
            final = rtcache.object_path(src, runtime, cc.split(), flags, extra_dirs)
            objects.append(final)
            if final.exists():
                hits += 1
                continue
            tmp = rtcache.temp_for(final)
            jobs.append((_cc_argv(cc) + [*flags, "-c", source, "-o", str(tmp)], final))
        else:
            obj = output.with_name(f"{output.name}.{src.stem}.o")
            objects.append(obj)
            jobs.append((_cc_argv(cc) + [*flags, "-c", source, "-o", str(obj)], None))
    if record is not None:
        state = f"on dir={rtcache.cache_dir()}" if use_cache else "off"
        record.append(["#", f"rtcache {state} hit={hits} miss={len(jobs) - 1}"])
        record.extend([str(arg) for arg in argv] for argv, _ in jobs)
    cc_started = time.perf_counter()
    proc = rtcache.run_jobs(jobs)
    if timings is not None:
        timings["cc_compile"] = round((time.perf_counter() - cc_started) * 1000.0, 3)
    ok = proc is None
    if ok:
        # no -O on the link line: it is a compile flag and zig's Mach-O
        # driver crashes on it (0.16.0)
        link_cc = ("zig cc" if link_driver == "zig" else link_driver) if link_driver else cc
        argv = [
            *_cc_argv(link_cc),
            *objects,
            *ffi_archives,
            *target_flags,
            *(link_flags or []),
            *tail,
        ]
        if record is not None:
            record.append([str(arg) for arg in argv])
        link_started = time.perf_counter()
        proc = subprocess.run(argv, capture_output=True, text=True)
        if timings is not None:
            timings["cc_link"] = round((time.perf_counter() - link_started) * 1000.0, 3)
    if proc is not None and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise IRError(f"{cc} failed to assemble LLVM IR: {detail}")
    if ok and debug_info:
        _write_debug_symbols(output, record)


def runtime_dir() -> Path:
    """The C runtime directory the emitter links beside every program.

    The runtime lives next to the package root, not next to the emitter
    modules, so the package can grow without moving the data files.
    """
    return Path(__file__).resolve().parents[2] / "runtime"


def _require_units(compile_units: list[Path] | None, link_units: list[Path] | None) -> None:
    """Refuse a declared unit that is not on disk, before the compiler runs.

    A missing file would otherwise surface as a cc error halfway through the
    build, which names the compiler and not the manifest that declared it.
    """
    for unit in [*(compile_units or []), *(link_units or [])]:
        if not unit.exists():
            raise IRError(f"the declared unit {unit} does not exist")


def _write_debug_symbols(output: Path, record: list[list[str]] | None) -> None:
    """Move the DWARF out of the linker into a `.dSYM` bundle on Darwin.

    The Mach-O linker does not keep the debug sections in the executable, so
    `lldb` finds the line table in the companion `.dSYM`. On ELF the sections
    stay in the binary and there is nothing to do.
    """
    if sys.platform != "darwin":
        return
    bundle = output.with_name(f"{output.name}.dSYM")
    argv = ["xcrun", "dsymutil", str(output), "-o", str(bundle)]
    if record is not None:
        record.append(argv)
    proc = subprocess.run(argv, capture_output=True, text=True)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise IRError(f"dsymutil failed to write the debug symbols: {detail}")


def compile_unit(
    llvm_path: Path,
    output: Path,
    kind: str,
    driver: str = "clang",
    target: str | None = None,
    record: list[list[str]] | None = None,
    timings: dict[str, float] | None = None,
    debug_info: bool = False,
) -> None:
    """Compile the module's `.ll` alone to assembly (`asm`) or an object (`obj`)."""
    if driver == "zig":
        zigshim.ensure_on_path()
    if target is not None and driver != "zig":
        raise IRError("--target requires --driver zig (bundled sysroot per target)")
    output.parent.mkdir(parents=True, exist_ok=True)
    cc = "zig cc" if driver == "zig" else driver
    mode = "-S" if kind == "asm" else "-c"
    ir_kind = ["-x", "ir"] if driver == "clang" else []
    target_flags = ["-target", target] if target is not None else []
    dbg_flags = ["-g"] if debug_info else []
    argv = _cc_argv(cc)
    argv = [*argv, "-O2", *dbg_flags, *ir_kind, mode, str(llvm_path), "-o", str(output), *target_flags]
    if record is not None:
        record.append(argv)
    started = time.perf_counter()
    proc = subprocess.run(argv, capture_output=True, text=True)
    if timings is not None:
        timings["cc_compile"] = round((time.perf_counter() - started) * 1000.0, 3)
    if proc.returncode != 0:
        raise IRError(f"{cc} failed to emit {kind}: {(proc.stderr or proc.stdout).strip()}")
