"""The static-library build: objects, archive, header, module map.

`link_binary` turns emitted LLVM plus the C runtime into a program. This
module stops one step earlier and ships the same objects as a static archive:

- every unit (the module's `.ll` and each runtime `.c`) compiles to an object
  through the same `rtcache` path the binary build uses, with the visibility
  flags on the line, so the archive hides what an app should not see;
- the objects are archived with `libtool -static` (Apple) or `llvm-ar`
  (Android), giving `libpyscriptc.a`;
- `pyscriptc.h` declares `pyscriptc_init()` plus the entry functions, and
  `module.modulemap` lets Swift import the header as a clang module.

There is no constructor-dependent initialization: an archive member whose
only contribution is an initializer is dropped by ld64 when nothing pulls it
in, so `pyscriptc_init()` is the documented, explicit entry point that runs
the module's globals and registers its exception classes.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

from pyscriptc import rtcache
from pyscriptc.errors import IRError
from pyscriptc.mobile import excluded_sources, mobile_target_family, normalize_target
from pyscriptc.emit.llvm._driver import runtime_dir, sanitize_flags

# The archive name and the header name are the surface an app links against.
LIB_NAME = "libpyscriptc.a"
HEADER_NAME = "pyscriptc.h"
MODULEMAP_NAME = "module.modulemap"

# The exported C ABI of a library build. The archive hides everything else
# behind `-fvisibility=hidden`; these are the symbols a host may reference.
# `pyscriptc_init` is the module's own entry; the rest is the runtime surface
# the exported Python functions call through.
EXPORTED_RUNTIME_SYMBOLS: tuple[str, ...] = (
    "ps_alloc",
    "ps_free",
    "ps_report_current",
    "ps_exc_register",
    "ps_exc_code",
    "ps_argv_init",
)

# The 16 KB page-alignment link flags Google Play requires of any shared
# object built for Android 15+ (2025-11-01 rule). A static archive carries no
# ELF program headers, so it inherits the consumer's alignment; the flags
# exist for the shared-object outputs a host may link the archive into.
ANDROID_PAGE_FLAGS: tuple[str, ...] = (
    "-Wl,-z,max-page-size=16384",
    "-Wl,-z,common-page-size=16384",
)


def library_flags(target: str, sanitize: list[str] | None = None) -> list[str]:
    """The cc flags a mobile object carries: target, visibility, sanitizers.

    `-fvisibility=hidden` keeps every symbol out of the export table unless a
    header marks it visible, which is what makes the archive linkable into an
    app without polluting its symbol namespace. It has to happen at compile
    time: a consumer cannot hide symbols in a finished archive.
    """
    normalized = normalize_target(target) or target
    return [
        "-O2",
        "-fvisibility=hidden",
        *sanitize_flags(sanitize),
        "-target",
        normalized,
    ]


def runtime_sources(target: str) -> list[Path]:
    """The runtime `.c` files a mobile build compiles, sorted, minus the excluded."""
    runtime = runtime_dir()
    excluded = set(excluded_sources(runtime))
    return sorted(path for path in runtime.glob("*.c") if path not in excluded and path.name != "ps_island.c")


def archive_tool(target: str) -> list[str]:
    """The archive command for `target`: `libtool -static` on Apple, `llvm-ar` otherwise."""
    if mobile_target_family(target) == "ios":
        return ["libtool", "-static"]
    return ["llvm-ar", "rcs"]


def build_library(
    llvm_path: Path,
    output: Path,
    target: str,
    entry: str | None = None,
    driver: str = "zig",
    sanitize: list[str] | None = None,
    record: list[list[str]] | None = None,
    timings: dict[str, float] | None = None,
    debug_info: bool = False,
) -> dict[str, Path]:
    """Compile `.ll` plus the runtime into `output` (an archive) plus its headers.

    Returns the artifact paths: the archive, the header and the module map.
    The runtime objects go through `rtcache`, keyed on the same flags the
    binary build uses plus the library flags, so a stale cached object cannot
    mis-link a mobile build.
    """
    from pyscriptc.mobile import is_mobile_target

    if not is_mobile_target(target):
        raise IRError(
            f"--emit lib needs a mobile target: one of aarch64-apple-ios, "
            f"aarch64-apple-ios-simulator, aarch64-linux-android (got {target!r})"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    archive = output / LIB_NAME
    header = output / HEADER_NAME
    modulemap = output / MODULEMAP_NAME

    cc = "zig cc" if driver == "zig" else driver
    cc_argv = cc.split()
    flags = library_flags(target, sanitize)
    dbg_flags = ["-g"] if debug_info else []
    obj_dir = output / "obj"
    obj_dir.mkdir(parents=True, exist_ok=True)
    jobs: list[tuple[list[str], Path | None]] = []
    objects: list[Path] = []

    ll_obj = obj_dir / f"{llvm_path.stem}.o"
    ll_kind = ["-x", "ir"] if driver == "clang" else []
    jobs.append(
        (
            [*cc_argv, *flags, *dbg_flags, *ll_kind, "-c", str(llvm_path), "-o", str(ll_obj)],
            None,
        )
    )
    objects.append(ll_obj)

    use_cache = rtcache.prepare()
    runtime = runtime_dir()
    hits = 0
    for source in runtime_sources(target):
        if use_cache:
            final = rtcache.object_path(source, runtime, cc_argv, flags + dbg_flags)
            objects.append(final)
            if final.exists():
                hits += 1
                continue
            tmp = rtcache.temp_for(final)
            jobs.append(([*cc_argv, *flags, *dbg_flags, "-c", str(source), "-o", str(tmp)], final))
        else:
            obj = obj_dir / f"{source.stem}.o"
            objects.append(obj)
            jobs.append(([*cc_argv, *flags, *dbg_flags, "-c", str(source), "-o", str(obj)], None))
    if record is not None:
        state = f"on dir={rtcache.cache_dir()}" if use_cache else "off"
        record.append(["#", f"rtcache {state} hit={hits} miss={len(jobs) - 1}"])
        record.extend(argv for argv, _ in jobs)
    cc_started = time.perf_counter()
    proc = rtcache.run_jobs(jobs)
    if timings is not None:
        timings["cc_compile"] = round((time.perf_counter() - cc_started) * 1000.0, 3)
    if proc is not None and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise IRError(f"{cc} failed to build a mobile object: {detail}")

    argv = [*archive_tool(target), *objects, "-o", str(archive)]
    if mobile_target_family(target) == "android":
        # llvm-ar has no -o; its output path is the last argument.
        argv = [*archive_tool(target), str(archive), *objects]
    if record is not None:
        record.append(argv)
    arch_started = time.perf_counter()
    proc = subprocess.run(argv, capture_output=True, text=True)
    if timings is not None:
        timings["archive"] = round((time.perf_counter() - arch_started) * 1000.0, 3)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise IRError(f"archiving the mobile library failed: {detail}")

    header.write_text(render_header(entry), encoding="utf-8")
    modulemap.write_text(render_modulemap(), encoding="utf-8")
    return {"archive": archive, "header": header, "modulemap": modulemap}


def render_header(entry: str | None = None) -> str:
    """The `pyscriptc.h` a mobile app includes: the init entry and the runtime types.

    The header is generated, not vendored, so it always matches the compiler
    that wrote the archive beside it.
    """
    lines = [
        "/* Generated by pyscriptc. The archive beside this header is a static",
        "   library: call pyscriptc_init() once before anything else, then call",
        "   the entry function. Every other symbol is hidden. */",
        "#ifndef PYSSCRIPTC_H",
        "#define PYSSCRIPTC_H",
        "",
        "#ifdef __cplusplus",
        'extern "C" {',
        "#endif",
        "",
        "/* Initialize the runtime: the exception table and the module globals.",
        "   Call once per process (or once per thread once rung B lands), before",
        "   the first entry call. Idempotent. */",
        "__attribute__((visibility(\"default\"))) int pyscriptc_init(void);",
        "",
    ]
    if entry:
        lines.extend(
            [
                f"/* The module entry ({entry}). Returns 0 on success, non-zero with a",
                "   message on stderr when a Python exception escapes. */",
                '__attribute__((visibility("default"))) int pyscriptc_main(void);',
                "",
            ]
        )
    lines.extend(
        [
            "#ifdef __cplusplus",
            "}",
            "#endif",
            "",
            "#endif /* PYSSCRIPTC_H */",
            "",
        ]
    )
    return "\n".join(lines)


def render_modulemap() -> str:
    """The clang module map that lets Swift `import pyscriptc`."""
    return f"""module pyscriptc {{
    header "{HEADER_NAME}"
    export *
}}
"""


def android_shared_flags() -> list[str]:
    """The link flags any Android shared object carries (Play's 16 KB rule)."""
    return list(ANDROID_PAGE_FLAGS)
