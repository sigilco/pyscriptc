"""The static-library build: toolchain, objects, archive, header, module map.

`link_binary` turns emitted LLVM plus the C runtime into a program. This
module stops one step earlier and ships the same objects as a static archive:

- every unit (the module's `.ll` and each runtime `.c`) compiles to an object
  through the same `rtcache` path the binary build uses, with the visibility
  flags on the line, so the archive hides what an app should not see;
- the objects are archived with `libtool -static` (Apple) or `llvm-ar`
  (Android), giving `libpyscriptc.a`;
- `pyscriptc.h` declares the C entry points, and `module.modulemap` lets
  Swift import the header as a clang module.

The mobile driver is clang, never zig: zig bundles libc for its desktop
targets but neither bionic nor the Apple SDKs. The iOS sysroots come from
`xcrun --show-sdk-path`; the Android sysroot comes from the NDK the user
points at (`PYSC_ANDROID_NDK`). The spike questions that remain (bionic
coverage, `zig cc` with an NDK sysroot) are tracked in #353/#354; the shape
here does not depend on their answers.

There is no constructor-dependent initialization: an archive member whose
only contribution is an initializer is dropped by ld64 when nothing pulls it
in, so `pyscriptc_init()` (runtime, C) and `pyscriptc_main()` (module, LLVM)
are the explicit, documented entry points.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from pyscriptc import rtcache
from pyscriptc.emit.llvm._driver import runtime_dir, sanitize_flags
from pyscriptc.errors import IRError
from pyscriptc.mobile import ANDROID_API_DEFAULT, MobileTarget, excluded_sources

# The archive name and the header name are the surface an app links against.
LIB_NAME = "libpyscriptc.a"
HEADER_NAME = "pyscriptc.h"
MODULEMAP_NAME = "module.modulemap"


@dataclass(frozen=True)
class MobileToolchain:
    """The cc invocation and the archive tool one mobile target needs."""

    # The compiler: an argv prefix ("clang", or the NDK clang path).
    cc: tuple[str, ...]
    # Flags every compile carries: the target triple and the sysroot.
    target_flags: tuple[str, ...]
    # The archiver: `libtool -static` (the archive path follows -o) or
    # `llvm-ar rcs` (the archive path is the first operand).
    archive: tuple[str, ...]
    archive_output_style: str  # "o" or "positional"


def _xcrun_sdk(sdk: str) -> str:
    """The path of one Xcode SDK, or an IRError naming the missing piece."""
    proc = subprocess.run(["xcrun", "--sdk", sdk, "--show-sdk-path"], capture_output=True, text=True)
    path = proc.stdout.strip()
    if proc.returncode != 0 or not path:
        raise IRError(
            f"the {sdk} SDK was not found (xcrun --sdk {sdk} --show-sdk-path failed); "
            "install Xcode and run xcode-select -s on it"
        )
    return path


def _ndk_sysroot() -> tuple[Path, tuple[str, ...]]:
    """The NDK sysroot and the clang prefix that targets it.

    `PYSC_ANDROID_NDK` names the NDK root (`.../ndk/26.1.10909125`). The
    unified sysroot is `toolchains/llvm/prebuilt/<host-tag>/sysroot` and the
    driver is the clang in the same prebuilt tree.
    """
    root = os.environ.get("PYSC_ANDROID_NDK", "").strip()
    if not root:
        raise IRError(
            "--emit lib for aarch64-linux-android needs the Android NDK: set "
            "PYSC_ANDROID_NDK to the NDK root (the directory with "
            "toolchains/llvm/prebuilt). zig cc does not bundle bionic, so the "
            "Android objects always compile through the NDK clang."
        )
    _android_api()
    ndk = Path(root)
    host_tag = "darwin-x86_64" if os.uname().sysname == "Darwin" else "linux-x86_64"
    prebuilt = ndk / "toolchains" / "llvm" / "prebuilt" / host_tag
    sysroot = prebuilt / "sysroot"
    clang = prebuilt / "bin" / "clang"
    if not sysroot.is_dir() or not clang.is_file():
        raise IRError(
            f"PYSC_ANDROID_NDK={root} has no NDK clang/sysroot at {prebuilt}; "
            "point it at the NDK root (the directory that contains "
            "toolchains/llvm/prebuilt)"
        )
    return sysroot, (str(clang),)


def _android_api() -> int:
    """The Android API level the sysroot targets; 24 unless PYSC_ANDROID_API says otherwise."""
    raw = os.environ.get("PYSC_ANDROID_API", "").strip()
    if not raw:
        return ANDROID_API_DEFAULT
    try:
        api = int(raw)
    except ValueError as exc:
        raise IRError(f"PYSC_ANDROID_API must be an int, got {raw!r}") from exc
    if api < 21:
        raise IRError(f"PYSC_ANDROID_API={api} is below the 64-bit floor (21)")
    return api


def toolchain_for(target: MobileTarget) -> MobileToolchain:
    """The toolchain one mobile target compiles with.

    Apple targets compile through the host clang with the Xcode SDK sysroot
    (`xcrun`); the device and the simulator are separate SDKs, so the target
    record decides which one is read. The Android target compiles through the
    NDK clang with the unified sysroot, at the API level `PYSC_ANDROID_API`
    names (24 by default).
    """
    if target.family == "ios":
        sdk_name = "iphonesimulator" if target.cc_target.endswith("-simulator") else "iphoneos"
        sysroot = _xcrun_sdk(sdk_name)
        return MobileToolchain(
            cc=("clang",),
            target_flags=("-target", target.cc_target, "-isysroot", sysroot),
            archive=("libtool", "-static"),
            archive_output_style="o",
        )
    sysroot, cc = _ndk_sysroot()
    api = _android_api()
    return MobileToolchain(
        cc=cc,
        target_flags=(
            f"--target={target.cc_target}{api}",
            f"--sysroot={sysroot}",
        ),
        archive=("llvm-ar", "rcs"),
        archive_output_style="positional",
    )


def library_flags(sanitize: list[str] | None = None) -> list[str]:
    """The cc flags every mobile object carries: optimization and visibility.

    `-fvisibility=hidden` keeps every symbol out of the export table unless
    its definition marks it visible, which is what makes the archive linkable
    into an app without leaking the whole runtime namespace. It has to happen
    at compile time: a consumer cannot hide symbols in a finished archive.
    """
    return ["-O2", "-fvisibility=hidden", *sanitize_flags(sanitize)]


def runtime_sources(target: MobileTarget) -> list[Path]:
    """The runtime `.c` files a mobile build compiles, sorted, minus the excluded.

    `ps_island.c` is excluded like every non-island build (it embeds the host
    CPython); `ps_sub.c` and `ps_osfd.c` are excluded because a mobile target
    has no fork/exec or raw-descriptor surface.
    """
    runtime = runtime_dir()
    excluded = set(excluded_sources(runtime))
    return sorted(path for path in runtime.glob("*.c") if path not in excluded and path.name != "ps_island.c")


def build_library(
    llvm_path: Path,
    output: Path,
    target: MobileTarget,
    driver: str = "clang",
    sanitize: list[str] | None = None,
    record: list[list[str]] | None = None,
    timings: dict[str, float] | None = None,
    debug_info: bool = False,
    use_cache: bool | None = None,
    toolchain: MobileToolchain | None = None,
) -> dict[str, Path]:
    """Compile `.ll` plus the runtime into `output` (a directory) of artifacts.

    Writes `libpyscriptc.a`, `pyscriptc.h` and `module.modulemap` into
    `output` and returns their paths. The runtime objects go through
    `rtcache`, keyed on the same inputs the binary build uses plus the
    library's visibility flags, so a stale cached object cannot mis-link a
    mobile build. `use_cache=False` forces a rebuild (tests and debugging).
    """
    if driver == "zig":
        raise IRError(
            "--emit lib compiles through clang with an explicit sysroot "
            "(Xcode SDK or NDK); zig bundles neither bionic nor the Apple SDKs"
        )
    output.mkdir(parents=True, exist_ok=True)
    archive = output / LIB_NAME
    header = output / HEADER_NAME
    modulemap = output / MODULEMAP_NAME
    chain = toolchain if toolchain is not None else toolchain_for(target)
    flags = [*chain.target_flags, *library_flags(sanitize)]
    dbg_flags = ["-g"] if debug_info else []
    cc_argv = [*chain.cc, *flags, *dbg_flags]

    jobs: list[tuple[list[str], Path | None]] = []
    objects: list[Path] = []
    ll_obj = output / f"{llvm_path.stem}.o"
    ll_kind = ["-x", "ir"]
    jobs.append(([*cc_argv, *ll_kind, "-c", str(llvm_path), "-o", str(ll_obj)], None))
    objects.append(ll_obj)

    if use_cache is None:
        use_cache = rtcache.prepare()
    runtime = runtime_dir()
    hits = 0
    for source in runtime_sources(target):
        if use_cache:
            final = rtcache.object_path(source, runtime, chain.cc, cc_argv)
            objects.append(final)
            if final.exists():
                hits += 1
                continue
            tmp = rtcache.temp_for(final)
            jobs.append(([*cc_argv, "-c", str(source), "-o", str(tmp)], final))
        else:
            obj = output / f"{source.stem}.o"
            objects.append(obj)
            jobs.append(([*cc_argv, "-c", str(source), "-o", str(obj)], None))
    if record is not None:
        state = f"on dir={rtcache.cache_dir()}" if use_cache else "off"
        record.append(["#", f"rtcache {state} hit={hits} miss={len(jobs) - 1}"])
        record.extend([str(arg) for arg in argv] for argv, _ in jobs)
    cc_started = time.perf_counter()
    proc = rtcache.run_jobs(jobs)
    if timings is not None:
        timings["cc_compile"] = round((time.perf_counter() - cc_started) * 1000.0, 3)
    if proc is not None and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise IRError(f"{chain.cc[0]} failed to build a mobile object: {detail}")

    if chain.archive_output_style == "o":
        argv = [*chain.archive, "-o", str(archive), *[str(obj) for obj in objects]]
    else:
        argv = [*chain.archive, str(archive), *[str(obj) for obj in objects]]
    if record is not None:
        record.append([str(arg) for arg in argv])
    arch_started = time.perf_counter()
    proc = subprocess.run(argv, capture_output=True, text=True)
    if timings is not None:
        timings["archive"] = round((time.perf_counter() - arch_started) * 1000.0, 3)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise IRError(f"archiving the mobile library failed: {detail}")

    header.write_text(render_header(), encoding="utf-8")
    modulemap.write_text(render_modulemap(), encoding="utf-8")
    return {"archive": archive, "header": header, "modulemap": modulemap}


def render_header() -> str:
    """The `pyscriptc.h` a mobile app includes.

    The header is generated, not vendored, so it always matches the compiler
    that wrote the archive beside it. `pyscriptc_init` is the runtime entry
    the archive carries; `pyscriptc_main` is the module entry the emitted
    code defines (it calls `pyscriptc_init` first). Everything else in the
    archive is hidden.
    """
    return """/* Generated by pyscriptc. The archive beside this header is a static
   library: call pyscriptc_main() to run the module's entry (it initializes
   the runtime first), then call the exported functions rung B generates.
   Every other symbol is hidden. */
#ifndef PYSCRIPTC_H
#define PYSCRIPTC_H

#ifdef __cplusplus
extern "C" {
#endif

/* Runtime init: the per-process state the runtime needs before its first
   call (the argv list is empty in a library). Idempotent; pyscriptc_main
   calls it, so a host only calls it directly when it drives the runtime
   without the module entry. */
__attribute__((visibility("default"))) int pyscriptc_init(void);

/* The module entry: runtime init, the exception table, the module globals,
   then the module's own entry (a script body or the --call function).
   Returns 0 on success. */
__attribute__((visibility("default"))) int pyscriptc_main(void);

#ifdef __cplusplus
}
#endif

#endif /* PYSCRIPTC_H */
"""


def render_modulemap() -> str:
    """The clang module map that lets Swift `import pyscriptc`."""
    return f"""module pyscriptc {{
    header "{HEADER_NAME}"
    export *
}}
"""
