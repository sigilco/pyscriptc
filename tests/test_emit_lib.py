"""`--emit lib`: the mobile static-library mode.

Two layers. The CLI layer runs the real compiler and needs no cross
toolchain: the refusals, the flag plumbing, the header/modulemap text and
the emitted IR shape. The archive layer needs a cc with the right sysroot,
so it runs only where one exists (macOS with Xcode: both Apple triples
compile, archive and link-check for real), and it fakes the compiler
everywhere else to pin the argv shapes, the rtcache keys and the exclusion
list.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from tests.test_gates import ROOT, write_case

from pyscriptc.emit.llvm._driver import runtime_dir
from pyscriptc.emitlib import (
    HEADER_NAME,
    LIB_NAME,
    MODULEMAP_NAME,
    MobileToolchain,
    build_library,
    render_header,
    render_modulemap,
    runtime_sources,
    toolchain_for,
)
from pyscriptc.errors import SpikeError
from pyscriptc.mobile import (
    ANDROID_PAGE_FLAGS,
    MOBILE_EXCLUDED_SOURCES,
    is_mobile_target,
    mobile_target,
)

TRIPLES = (
    "aarch64-apple-ios",
    "aarch64-apple-ios-simulator",
    "aarch64-linux-android",
)
SIM = "aarch64-apple-ios-simulator"
DEVICE = "aarch64-apple-ios"
ANDROID = "aarch64-linux-android"
DESKTOP = "aarch64-linux-gnu"

HELLO = "def main() -> None:\n    print(str(1 + 1))\n\n\nmain()\n"


def _sdk_present(sdk: str) -> bool:
    proc = subprocess.run(["xcrun", "--sdk", sdk, "--show-sdk-path"], capture_output=True, text=True)
    return proc.returncode == 0 and bool(proc.stdout.strip())


def _sdk_path(sdk: str) -> str:
    return subprocess.run(["xcrun", "--sdk", sdk, "--show-sdk-path"], capture_output=True, text=True).stdout.strip()


needs_xcode = pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("clang") is None,
    reason="needs macOS with Xcode clang",
)


@pytest.fixture
def no_ndk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYSC_ANDROID_NDK", raising=False)
    monkeypatch.delenv("PYSC_ANDROID_API", raising=False)


@pytest.fixture
def isolated_rt_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "rt"
    monkeypatch.setenv("PYSC_RT_CACHE_DIR", str(root))
    monkeypatch.delenv("PYSC_NO_RT_CACHE", raising=False)
    return root


def compile_cli(
    tmp_path: Path, source_path: Path, *flags: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    from tests.test_gates import ENV

    return subprocess.run(
        [sys.executable, "-m", "pyscriptc", "compile", str(source_path), *flags],
        cwd=ROOT,
        env={**ENV, **(env or {})},
        capture_output=True,
        text=True,
    )


# --- the refusals (no toolchain needed) ---


@pytest.mark.parametrize("triple", TRIPLES)
def test_emit_bin_refuses_mobile_triples(tmp_path: Path, triple: str) -> None:
    path = write_case(tmp_path, "hello.py", HELLO)
    got = compile_cli(tmp_path, path, "--emit", "bin", "--target", triple, "--skip-check")
    assert got.returncode == 1, got.stderr
    assert got.stderr.startswith("PS4101:")
    assert "--emit lib" in got.stderr


def test_run_refuses_mobile_triples(tmp_path: Path) -> None:
    path = write_case(tmp_path, "hello.py", HELLO)
    got = compile_cli(tmp_path, path, "--run", "--target", ANDROID, "--skip-check")
    assert got.returncode == 1, got.stderr
    assert "PS4101" in got.stderr


def test_emit_lib_refuses_desktop_triple(tmp_path: Path) -> None:
    path = write_case(tmp_path, "hello.py", HELLO)
    got = compile_cli(tmp_path, path, "--emit", "lib", "--target", DESKTOP, "--skip-check")
    assert got.returncode == 1, got.stderr
    assert "PS4101" in got.stderr


def test_emit_lib_refuses_missing_target(tmp_path: Path) -> None:
    path = write_case(tmp_path, "hello.py", HELLO)
    got = compile_cli(tmp_path, path, "--emit", "lib", "--skip-check")
    assert got.returncode == 1, got.stderr
    assert "PS4101" in got.stderr
    # the message names all three triples
    for triple in TRIPLES:
        assert triple in got.stderr


def test_refusal_precedes_lowering(tmp_path: Path) -> None:
    """The refusal fires before lowering, so a fenced script still gets PS4101."""
    fenced = "from typing import Any\n\n\ndef f(x: Any) -> int:\n    return 1\n"
    path = write_case(tmp_path, "fenced.py", fenced)
    got = compile_cli(tmp_path, path, "--emit", "bin", "--target", SIM, "--skip-check")
    assert got.returncode == 1, got.stderr
    assert "PS4101" in got.stderr


def test_explain_documents_the_refusal() -> None:
    from pyscriptc.diagnostics import DIAGNOSTICS

    assert "PS4101" in DIAGNOSTICS


def test_zig_driver_is_refused_for_lib(tmp_path: Path, no_ndk) -> None:
    path = write_case(tmp_path, "hello.py", HELLO)
    got = compile_cli(tmp_path, path, "--emit", "lib", "--target", SIM, "--driver", "zig", "--skip-check")
    assert got.returncode == 1, got.stderr
    assert "sysroot" in got.stderr


def test_android_without_ndk_names_the_env_var(tmp_path: Path, no_ndk) -> None:
    path = write_case(tmp_path, "hello.py", HELLO)
    got = compile_cli(tmp_path, path, "--emit", "lib", "--target", ANDROID, "--skip-check")
    assert got.returncode == 1, got.stderr
    assert "PYSC_ANDROID_NDK" in got.stderr


def test_android_api_must_be_an_int(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYSC_ANDROID_NDK", "/nonexistent-ndk")
    monkeypatch.setenv("PYSC_ANDROID_API", "not-a-number")
    with pytest.raises(SpikeError, match="PYSC_ANDROID_API"):
        toolchain_for(mobile_target(ANDROID))


def test_android_api_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYSC_ANDROID_NDK", "/nonexistent-ndk")
    monkeypatch.setenv("PYSC_ANDROID_API", "19")
    with pytest.raises(SpikeError, match="21"):
        toolchain_for(mobile_target(ANDROID))


def test_android_page_flags_are_the_play_requirement() -> None:
    """The 16 KB alignment pair a shared-object link needs on Android 15+."""
    assert ANDROID_PAGE_FLAGS == (
        "-Wl,-z,max-page-size=16384",
        "-Wl,-z,common-page-size=16384",
    )


def _fake_ndk(root: Path) -> Path:
    """An NDK root with a real sysroot layout and a clang that just records argv.

    The clang shim drops `--sysroot` so the host SDK supplies the headers;
    the test asserts argv shape and the post-build note, not Android codegen.
    """
    prebuilt = root / "ndk" / "toolchains" / "llvm" / "prebuilt" / "darwin-x86_64"
    (prebuilt / "sysroot" / "usr").mkdir(parents=True)
    real_cc = shutil.which("cc") or shutil.which("clang")
    assert real_cc, "no host cc for the fake NDK"
    (prebuilt / "bin").mkdir(parents=True, exist_ok=True)
    shim = "\n".join(
        [
            "#!/usr/bin/env python3",
            "import os, subprocess, sys",
            "args = sys.argv[1:]",
            "clean = []",
            "skip = False",
            "for arg in args:",
            "    if skip:",
            "        skip = False",
            "        continue",
            "    if arg == '--sysroot':",
            "        skip = True",
            "        continue",
            "    if arg.startswith('--sysroot='):",
            "        continue",
            "    if arg.startswith('--target='):",
            "        continue",
            "    clean.append(arg)",
            f"os.execv({real_cc!r}, [{real_cc!r}, *clean])",
        ]
    )
    clang = prebuilt / "bin" / "clang"
    clang.write_text(shim + "\n", encoding="utf-8")
    clang.chmod(0o755)
    ar = prebuilt / "bin" / "llvm-ar"
    ar.write_text('#!/bin/sh\nexec ar "$@"\n', encoding="utf-8")
    ar.chmod(0o755)
    return root / "ndk"


def test_android_build_prints_the_page_flag_note(tmp_path: Path) -> None:
    path = write_case(tmp_path, "hello.py", HELLO)
    out = tmp_path / "lib"
    got = compile_cli(
        tmp_path,
        path,
        "--emit",
        "lib",
        "--target",
        ANDROID,
        "--skip-check",
        "-o",
        str(out),
        env={"PYSC_ANDROID_NDK": str(_fake_ndk(tmp_path))},
    )
    assert got.returncode == 0, got.stderr
    assert "-Wl,-z,max-page-size=16384" in got.stdout


# --- the pure surfaces: header, modulemap, registry, exclusions ---


def test_header_documents_the_init_entry() -> None:
    text = render_header()
    assert "pyscriptc_init" in text
    assert "pyscriptc_main" in text
    assert text.count('visibility("default")') == 2


def test_modulemap_wraps_the_header() -> None:
    text = render_modulemap()
    assert "module pyscriptc {" in text
    assert HEADER_NAME in text


def test_mobile_registry_resolves_the_three_triples() -> None:
    for triple in TRIPLES:
        assert is_mobile_target(triple)
        assert mobile_target(triple).name == triple
    assert not is_mobile_target(DESKTOP)
    assert not is_mobile_target(None)
    assert not is_mobile_target("x86_64-apple-macos")


def test_runtime_sources_exclude_the_mobile_hostile_files() -> None:
    runtime = runtime_dir()
    names = [path.name for path in runtime_sources(mobile_target(SIM))]
    for excluded in MOBILE_EXCLUDED_SOURCES:
        assert excluded not in names
    assert "ps_island.c" not in names
    assert "ps_rt.c" in names
    # excluded for mobile only, not deleted
    assert (runtime / "ps_sub.c").is_file()
    assert (runtime / "ps_osfd.c").is_file()


# --- the emitted IR shape (in-process emitter, no cross toolchain) ---


def _lower_library(tmp_path: Path, source: str, name: str = "mod.py") -> str:
    """Lower `source` and emit its library-shaped LLVM text."""
    from pyscriptc.emit.llvm import emit_llvm
    from pyscriptc.lower import lower_source

    path = write_case(tmp_path, name, source)
    module = lower_source(source, str(path))
    return emit_llvm(module, None, [], library=True)


def test_library_ll_defines_pyscriptc_main_and_hides_the_rest(tmp_path: Path) -> None:
    ll = _lower_library(tmp_path, HELLO)
    assert "define i32 @main(" not in ll
    assert "define i32 @pyscriptc_main()" in ll
    assert "declare i32 @pyscriptc_init()" in ll
    assert "define hidden void @ps.user.main(" in ll


def test_noop_module_still_gets_the_entry(tmp_path: Path) -> None:
    """A definition-only module defines pyscriptc_main anyway: the ABI is unconditional."""
    ll = _lower_library(tmp_path, "def add(a: int, b: int) -> int:\n    return a + b\n")
    assert "define i32 @pyscriptc_main()" in ll
    assert "define hidden i64 @add(" in ll


def test_default_llvm_emission_is_untouched(tmp_path: Path) -> None:
    """The default emission has no library markers: no main, no hidden, no init call."""
    from pyscriptc.emit.llvm import emit_llvm
    from pyscriptc.lower import lower_source

    source = "def add(a: int, b: int) -> int:\n    return a + b\n"
    path = write_case(tmp_path, "plain.py", source)
    module = lower_source(source, str(path))
    ll = emit_llvm(module)
    assert "define i64 @add(" in ll
    assert "hidden" not in ll
    assert "pyscriptc" not in ll


def test_plain_llvm_emission_is_untouched(tmp_path: Path) -> None:
    """The default `--emit llvm` output has no main and no visibility markers."""
    path = write_case(tmp_path, "mod.py", "def add(a: int, b: int) -> int:\n    return a + b\n")
    got = compile_cli(tmp_path, path, "--emit", "llvm", "--skip-check")
    assert got.returncode == 0, got.stderr
    ll = tmp_path.joinpath("mod.ll").read_text(encoding="utf-8")
    assert "define i64 @add(" in ll
    assert "hidden" not in ll


# --- the archive: fake-cc argv pinning (no cross toolchain needed) ---


@pytest.fixture
def fake_cc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake cc on PATH that records its argv and emits a real host object.

    The stub shells out to the host cc for the object bytes (a one-line C
    file compiled once), so `ar` accepts the members: the shapes are pinned,
    the bytes are not target code. Skipped when the host has no cc, because
    the fixture cannot produce archive members without one.
    """
    if shutil.which("cc") is None:
        pytest.skip("needs a host cc for the stub's object bytes")
    log = tmp_path / "fakecc.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    seed = tmp_path / "seed.c"
    seed.write_text("int pyscriptc_seed(void) { return 1; }\n", encoding="utf-8")
    seed_obj = tmp_path / "seed.o"
    subprocess.run(["cc", "-c", str(seed), "-o", str(seed_obj)], check=True)
    script = bin_dir / "fakecc"
    script.write_text(
        "#!/bin/sh\n"
        'out=""\n'
        'prev=""\n'
        'for arg in "$@"; do\n'
        '  [ "$prev" = "-o" ] && out="$arg"\n'
        '  prev="$arg"\n'
        "done\n"
        f"cp '{seed_obj}' \"$out\"\n"
        'echo "$@" >> "$PYSC_FAKE_CC_LOG"\n',
        encoding="utf-8",
    )
    script.chmod(0o755)

    monkeypatch.setenv("PYSC_FAKE_CC_LOG", str(log))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    chain = MobileToolchain(
        cc=("fakecc",),
        target_flags=("-target", "aarch64-fake"),
        archive=("ar", "rcs"),
        archive_output_style="positional",
    )
    monkeypatch.setattr("pyscriptc.emitlib.toolchain_for", lambda target: chain)
    return log


def _log_calls(log: Path) -> list[list[str]]:
    return [shlex.split(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_fake_build_pins_argv_and_artifacts(tmp_path: Path, fake_cc: Path, isolated_rt_cache: Path) -> None:
    path = write_case(tmp_path, "hello.py", HELLO)
    got = compile_cli(tmp_path, path, "--emit", "llvm", "--skip-check")
    assert got.returncode == 0, got.stderr
    ll = tmp_path / "hello.ll"

    out = tmp_path / "libout"
    artifacts = build_library(ll, out, mobile_target(SIM), use_cache=False)
    assert artifacts["archive"].name == LIB_NAME
    assert artifacts["archive"].is_file()
    assert artifacts["header"].is_file()
    assert artifacts["modulemap"].is_file()
    members = subprocess.run(["ar", "t", str(artifacts["archive"])], capture_output=True, text=True)
    names = members.stdout.split()
    assert "hello.o" in names
    for excluded in ("ps_sub.o", "ps_osfd.o", "ps_island.o"):
        assert excluded not in names
    # one compile job per runtime source plus the module unit
    compiles = [argv for argv in _log_calls(fake_cc) if "-c" in argv]
    assert len(compiles) == len(runtime_sources(mobile_target(SIM))) + 1
    for argv in compiles:
        assert "-fvisibility=hidden" in argv
        assert "-target" in argv


def test_fake_build_uses_the_rtcache(tmp_path: Path, fake_cc: Path, isolated_rt_cache: Path) -> None:
    path = write_case(tmp_path, "hello.py", HELLO)
    got = compile_cli(tmp_path, path, "--emit", "llvm", "--skip-check")
    assert got.returncode == 0, got.stderr
    ll = tmp_path / "hello.ll"

    build_library(ll, tmp_path / "one", mobile_target(SIM))
    cold = [argv for argv in _log_calls(fake_cc) if "-c" in argv]
    assert len(cold) > 1

    build_library(ll, tmp_path / "two", mobile_target(SIM))
    warm = [argv for argv in _log_calls(fake_cc) if "-c" in argv][len(cold) :]
    # only the module unit compiles again; the runtime objects came from the cache
    assert len(warm) == 1
    assert str(ll) in warm[0]


def test_rtcache_key_covers_the_library_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A stale runtime object (built without the visibility flag) is not reused."""
    from pyscriptc import rtcache

    monkeypatch.setenv("PYSC_RT_CACHE_DIR", str(tmp_path / "rt"))
    monkeypatch.delenv("PYSC_NO_RT_CACHE", raising=False)
    runtime = runtime_dir()
    source = runtime / "ps_argv.c"
    cc = ("clang",)
    plain = rtcache.object_path(source, runtime, list(cc), ["-O2", "-target", "x"])
    library = rtcache.object_path(source, runtime, list(cc), ["-O2", "-fvisibility=hidden", "-target", "x"])
    assert plain != library
    same = rtcache.object_path(source, runtime, list(cc), ["-O2", "-fvisibility=hidden", "-target", "x"])
    assert library == same


# --- the archive: real toolchain (macOS with Xcode) ---


@needs_xcode
@pytest.mark.skipif(not _sdk_present("iphonesimulator"), reason="simulator SDK missing")
def test_simulator_archive_builds_and_link_checks(tmp_path: Path, isolated_rt_cache: Path) -> None:
    path = write_case(tmp_path, "hello.py", HELLO)
    out = tmp_path / "lib"
    got = compile_cli(tmp_path, path, "--emit", "lib", "--target", SIM, "--skip-check", "-o", str(out))
    assert got.returncode == 0, got.stderr
    assert (out / LIB_NAME).is_file()
    assert (out / HEADER_NAME).is_file()
    assert (out / MODULEMAP_NAME).is_file()

    main_c = tmp_path / "main.c"
    main_c.write_text(
        "extern int pyscriptc_main(void);\nint main(void) { return pyscriptc_main(); }\n",
        encoding="utf-8",
    )
    sdk = subprocess.run(
        ["xcrun", "--sdk", "iphonesimulator", "--show-sdk-path"], capture_output=True, text=True
    ).stdout.strip()
    main_o = tmp_path / "main.o"
    subprocess.run(
        [
            "clang",
            "-target",
            "aarch64-apple-ios15.0-simulator",
            "-isysroot",
            sdk,
            "-c",
            str(main_c),
            "-o",
            str(main_o),
        ],
        check=True,
    )
    app = tmp_path / "app"
    linked = subprocess.run(
        [
            "clang",
            "-target",
            "aarch64-apple-ios15.0-simulator",
            "-isysroot",
            sdk,
            str(main_o),
            str(out / LIB_NAME),
            "-o",
            str(app),
        ],
        capture_output=True,
        text=True,
    )
    assert linked.returncode == 0, linked.stderr
    arch = subprocess.run(["lipo", "-info", str(app)], capture_output=True, text=True)
    assert "arm64" in arch.stdout


@needs_xcode
@pytest.mark.skipif(not _sdk_present("iphoneos"), reason="device SDK missing")
def test_device_archive_builds(tmp_path: Path, isolated_rt_cache: Path) -> None:
    path = write_case(tmp_path, "hello.py", HELLO)
    out = tmp_path / "lib"
    got = compile_cli(tmp_path, path, "--emit", "lib", "--target", DEVICE, "--skip-check", "-o", str(out))
    assert got.returncode == 0, got.stderr
    members = subprocess.run(["ar", "t", str(out / LIB_NAME)], capture_output=True, text=True)
    names = members.stdout.split()
    assert "hello.o" in names
    for excluded in ("ps_sub.o", "ps_osfd.o", "ps_island.o"):
        assert excluded not in names


@needs_xcode
@pytest.mark.skipif(not _sdk_present("iphonesimulator"), reason="simulator SDK missing")
def test_archive_exports_only_the_documented_entry(tmp_path: Path, isolated_rt_cache: Path) -> None:
    """The exported surface is pyscriptc_init plus pyscriptc_main, nothing else.

    Checked on a linked dylib: a raw Mach-O archive still lists private-extern
    symbols in `nm -gU`, they only disappear when the member is linked.
    """
    path = write_case(tmp_path, "hello.py", HELLO)
    out = tmp_path / "lib"
    got = compile_cli(tmp_path, path, "--emit", "lib", "--target", SIM, "--skip-check", "-o", str(out))
    assert got.returncode == 0, got.stderr
    dylib = tmp_path / "libhello.dylib"
    linked = subprocess.run(
        [
            "xcrun",
            "-sdk",
            "iphonesimulator",
            "clang",
            "-target",
            "aarch64-apple-ios15.0-simulator",
            "-isysroot",
            _sdk_path("iphonesimulator"),
            "-shared",
            "-o",
            str(dylib),
            "-Wl,-all_load",
            str(out / LIB_NAME),
        ],
        capture_output=True,
        text=True,
    )
    assert linked.returncode == 0, linked.stderr
    nm = subprocess.run(["nm", "-gU", str(dylib)], capture_output=True, text=True)
    assert nm.returncode == 0
    exported = {line.split()[-1].lstrip("_") for line in nm.stdout.splitlines() if line.strip()}
    assert exported == {"pyscriptc_init", "pyscriptc_main"}
