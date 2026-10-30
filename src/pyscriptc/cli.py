"""Command line for the spike compiler."""

from __future__ import annotations

import argparse
import ast
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

# boxed:begin
from pyscriptc import embed, runcache, sitedeps, stdlibzip

# boxed:end
from pyscriptc.config import Config, apply_to_args, entry_points, load_config
from pyscriptc.errors import CheckFailed, FitError, IRError, SpikeError, VersionError
from pyscriptc.ffi_manifest import FfiManifest
from pyscriptc.log import enable_debug, get_logger, tail

log = get_logger()

_VERIFY_DEFAULT_TIMEOUT = 60.0
_REDUCE_DEFAULT_TIMEOUT = 120.0


class _LazyTelemetry:
    def __getattr__(self, name: str) -> Any:
        from pyscriptc import telemetry as _t

        globals()["telemetry"] = _t
        return getattr(_t, name)


telemetry: Any = _LazyTelemetry()


def _load_compiler() -> None:
    """Load compiler modules for compilation, verification and analysis commands."""
    global assess, deps, reduce, verify, zigshim
    global run_checker
    global build_report, family_of, render_text
    global Debug, resolve
    global explain
    global doctor_cmd
    global compile_unit, emit_llvm, link_binary, refuse_unsupported_target, standalone_entry
    global build_library
    global FfiManifestError, load_manifests
    global build_import_report, export_reach, render_import_text
    global validate_module, verify_module, LLVMVerifyError, verify_module_text
    global lower_file, render_fences
    global ANDROID_PAGE_FLAGS, MobileRefusal, is_mobile_target, require_mobile_target
    global lower_directory
    # boxed:begin
    global island_policy
    global boxed_definitions
    # boxed:end

    if "lower_file" in globals():
        return

    from pyscriptc import (
        assess as _assess,
    )
    from pyscriptc import (
        deps as _deps,
    )
    from pyscriptc import (
        reduce as _reduce,
    )
    from pyscriptc import (
        verify as _verify,
    )
    from pyscriptc import (
        zigshim as _zigshim,
    )

    # boxed:begin
    from pyscriptc.boxed import boxed_definitions as _boxed_definitions
    from pyscriptc.check import run_checker as _run_checker
    from pyscriptc.coverage import (
        build_report as _build_report,
    )
    from pyscriptc.coverage import (
        family_of as _family_of,
    )
    from pyscriptc.coverage import (
        render_text as _render_text,
    )
    from pyscriptc.debug import Debug as _Debug
    from pyscriptc.debug import resolve as _resolve
    from pyscriptc.diagnostics import explain as _explain
    from pyscriptc.doctor import doctor_cmd as _doctor_cmd
    from pyscriptc.emit.llvm import (
        compile_unit as _compile_unit,
    )
    from pyscriptc.emit.llvm import (
        emit_llvm as _emit_llvm,
    )
    from pyscriptc.emit.llvm import (
        link_binary as _link_binary,
    )
    from pyscriptc.emit.llvm import (
        refuse_unsupported_target as _refuse_unsupported_target,
    )
    from pyscriptc.emit.llvm import (
        standalone_entry as _standalone_entry,
    )
    from pyscriptc.emitlib import build_library as _build_library
    from pyscriptc.ffi_manifest import (
        FfiManifestError as _FfiManifestError,
    )
    from pyscriptc.ffi_manifest import (
        load_manifests as _load_manifests,
    )
    from pyscriptc.imports import (
        build_report as _build_import_report,
    )
    from pyscriptc.imports import (
        export_reach as _export_reach,
    )
    from pyscriptc.imports import (
        render_text as _render_import_text,
    )
    from pyscriptc.ir import validate_module as _validate_module
    from pyscriptc.irverify import verify_module as _verify_module
    from pyscriptc.llvverify import (
        LLVMVerifyError as _LLVMVerifyError,
    )
    from pyscriptc.llvverify import (
        verify_module_text as _verify_module_text,
    )
    from pyscriptc.lower import lower_file as _lower_file
    from pyscriptc.lower import render_fences as _render_fences
    from pyscriptc.mobile import (
        ANDROID_PAGE_FLAGS as _ANDROID_PAGE_FLAGS,
    )
    from pyscriptc.mobile import (
        MobileRefusal as _MobileRefusal,
    )
    from pyscriptc.mobile import (
        is_mobile_target as _is_mobile_target,
    )
    from pyscriptc.mobile import (
        require_mobile_target as _require_mobile_target,
    )
    from pyscriptc.package import lower_directory as _lower_directory
    from pyscriptc.stdlib_queue import island_policy as _island_policy

    # boxed:end

    assess = _assess
    deps = _deps
    reduce = _reduce
    verify = _verify
    zigshim = _zigshim
    run_checker = _run_checker
    build_report = _build_report
    family_of = _family_of
    render_text = _render_text
    Debug = _Debug
    resolve = _resolve
    explain = _explain
    doctor_cmd = _doctor_cmd
    compile_unit = _compile_unit
    emit_llvm = _emit_llvm
    link_binary = _link_binary
    refuse_unsupported_target = _refuse_unsupported_target
    standalone_entry = _standalone_entry
    build_library = _build_library
    FfiManifestError = _FfiManifestError
    load_manifests = _load_manifests
    build_import_report = _build_import_report
    export_reach = _export_reach
    render_import_text = _render_import_text
    validate_module = _validate_module
    verify_module = _verify_module
    LLVMVerifyError = _LLVMVerifyError
    verify_module_text = _verify_module_text
    lower_file = _lower_file
    render_fences = _render_fences
    ANDROID_PAGE_FLAGS = _ANDROID_PAGE_FLAGS
    MobileRefusal = _MobileRefusal
    is_mobile_target = _is_mobile_target
    require_mobile_target = _require_mobile_target
    lower_directory = _lower_directory
    # boxed:begin
    island_policy = _island_policy
    boxed_definitions = _boxed_definitions
    # boxed:end


def __getattr__(name: str) -> Any:
    if name.startswith("__"):
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    _load_compiler()
    if name in globals():
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pyscriptc",
        description="Compile a typed Python subset to native code through LLVM.",
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-v", "--verbose", action="store_true", help="enable DEBUG audit records")
    sub = parser.add_subparsers(dest="cmd", required=True)
    compile_parser = sub.add_parser("compile", help="compile a source file", parents=[common])
    compile_parser.add_argument("source", help="Python file or package directory to compile")
    compile_parser.add_argument(
        "--program",
        action="store_true",
        help="treat the directory as a program (`python -m pkg` parity): `__main__.py` is the "
        "entry point even when the directory has an `__init__.py`, and its modules are "
        "namespaced under the directory name",
    )
    compile_parser.add_argument(
        "--emit",
        choices=("ir", "llvm", "asm", "obj", "bin", "lib", "binding"),
        default="llvm",
        help="output artifact: ir (JSON IR), llvm (textual LLVM), asm, obj, bin, lib or binding; default llvm",
    )
    compile_parser.add_argument(
        "--out-dir",
        metavar="DIR",
        help="write artifacts to DIR/<source name> instead of beside the source (useful for directory compiles)",
    )
    compile_parser.add_argument("-o", dest="output", metavar="PATH", help="output path; defaults to the source stem")
    compile_parser.add_argument(
        "--ffi",
        action="append",
        metavar="MANIFEST",
        default=[],
        help="bind a typed extern manifest (TOML) and link its C sources; repeatable. "
        "Also read from the `ffi` key of pyscriptc.toml / PEP 723 metadata",
    )
    # boxed:begin
    compile_parser.add_argument(
        "--fallback",
        choices=("cpython",),
        default=None,
        metavar="TIER",
        help="route a function pyscriptc cannot compile to embedded CPython (fallback mode); "
        "the only tier is cpython. Needs libpython and the packages on the host",
    )
    compile_parser.add_argument(
        "--island",
        action="store_true",
        help="alias of --fallback=cpython: route every import outside the closed table and "
        "every function pyscriptc cannot compile to embedded CPython (fallback mode)",
    )
    compile_parser.add_argument(
        "--embed-python",
        choices=("host", "bundle", "auto"),
        default=None,
        metavar="MODE",
        help="libpython embedded CPython links: host (the compiler interpreter), bundle (the pinned "
        "runtime pack, relocatable output) or auto (host when this interpreter has a shared "
        "libpython, bundle otherwise; the default). Also read from the `embed` key of "
        "[tool.pyscriptc.island]",
    )
    compile_parser.add_argument(
        "--embed-stdlib",
        choices=stdlibzip.STDLIB_MODES,
        default=None,
        metavar="TRIM",
        help="stdlib trim of a bundle: safe (the default; everything except test, idlelib, "
        "tkinter, turtledemo, ensurepip, pydoc_data, __phello__) or minimal (an opt-in frozen "
        "closure plus allowlist). Also read from the `stdlib` key of [tool.pyscriptc.island]",
    )
    # boxed:end
    compile_parser.add_argument("--call", metavar="FUNC", help="function the generated main calls")
    compile_parser.add_argument(
        "--args",
        metavar="ARGS",
        default="[]",
        help="Python literal list of arguments for --call, parsed with ast.literal_eval",
    )
    compile_parser.add_argument(
        "--run",
        action="store_true",
        help="compile a binary and run it; arguments after `--` go to the binary",
    )
    compile_parser.add_argument(
        "--driver",
        choices=("clang", "zig"),
        default=None,
        help="C toolchain driver; zig means `zig cc` (preferred, required for --target); "
        "env PYSC_CC, defaults to zig if found, else clang",
    )
    compile_parser.add_argument(
        "--target",
        metavar="TRIPLE",
        help="cross-compilation triple for `zig cc`, e.g. aarch64-linux-gnu.2.39 (env PYSC_TARGET)",
    )
    compile_parser.add_argument(
        "--linker",
        choices=("clang", "zig"),
        default=None,
        help="driver for the final link step only; env PYSC_LINKER, defaults to --driver",
    )
    compile_parser.add_argument(
        "--sanitize",
        metavar="LIST",
        default=None,
        help="comma list of sanitizers for the C runtime and the emitted code, e.g. "
        "address,undefined (env PYSC_SANITIZE); needs a binary, so --emit bin only",
    )
    compile_parser.add_argument(
        "--debug-info",
        action="store_true",
        help="emit DWARF debug metadata (source file and line per function and "
        "statement) so lldb/gdb can step in the Python source (env PYSC_DEBUG_INFO)",
    )
    compile_parser.add_argument(
        "--print",
        dest="print_what",
        choices=("diagnostics",),
        help="print JSON instead of compiling: diagnostics is the coverage report; exit 3 on a refusal",
    )
    compile_parser.add_argument("--skip-check", action="store_true")
    compile_parser.add_argument(
        "--checker",
        choices=("pyrefly", "mypy", "pyright"),
        default="pyrefly",
        help="type-checker preflight; mypy and pyright are deprecated",
    )
    compile_parser.add_argument(
        "--bridge",
        choices=("mypy", "pyrefly"),
        default=None,
        help="feed the checker's inferred-type map to the compiler so unannotated "
        "code compiles; needs mypy installed, or pyrefly for --bridge pyrefly",
    )
    compile_parser.add_argument(
        "--debug",
        nargs="?",
        const="",
        default=None,
        metavar="WHAT",
        help="print a compact stage table to stderr; WHAT is a comma list of "
        "stages,fences,cc,paths,all (also via PYSC_DEBUG)",
    )
    compile_parser.add_argument(
        "--debug-dir",
        metavar="DIR",
        help="write debug artifacts (ir.json, out.ll, cc.sh, summary.json) to DIR",
    )
    compile_parser.add_argument(
        "--timings",
        action="store_true",
        help="print per-stage wall times (parse, check, lower, emit, cc/link) to stderr",
    )
    run_parser = sub.add_parser(
        "run", help="compile (cached by content hash) and run; arguments after `--` go to the program", parents=[common]
    )
    run_parser.add_argument("source", help="Python file or package directory to compile and run")
    run_parser.add_argument("--program", action="store_true", help="treat the directory as a program (see compile)")
    # boxed:begin
    run_parser.add_argument(
        "--fallback",
        choices=("cpython",),
        default=None,
        metavar="TIER",
        help="route a function pyscriptc cannot compile to embedded CPython (fallback mode); the only tier is cpython",
    )
    run_parser.add_argument(
        "--island",
        action="store_true",
        help="alias of --fallback=cpython",
    )
    run_parser.add_argument(
        "--embed-python",
        choices=("host", "bundle", "auto"),
        default=None,
        metavar="MODE",
        help="libpython embedded CPython links (see compile); the run cache keys on it",
    )
    # boxed:end
    # boxed:begin
    run_parser.add_argument(
        "--report",
        nargs="?",
        const=True,
        default=None,
        metavar="FILE.json",
        help="print or write the executed plane map after the run (default: silent)",
    )
    # boxed:end
    run_parser.add_argument("--call", metavar="FUNC", help="function the generated main calls")
    run_parser.add_argument("--args", metavar="ARGS", default="[]", help="Python literal list of arguments for --call")
    run_parser.add_argument(
        "--driver", choices=("clang", "zig"), default=None, metavar="DRIVER", help="C toolchain driver (see compile)"
    )
    run_parser.add_argument("--target", metavar="TRIPLE", help="cross-compilation triple for `zig cc` (see compile)")
    run_parser.add_argument(
        "--linker", choices=("clang", "zig"), default=None, metavar="DRIVER", help="link driver (see compile)"
    )
    run_parser.add_argument(
        "--sanitize",
        metavar="LIST",
        default=None,
        help="comma list of sanitizers for the C runtime and the emitted code (env PYSC_SANITIZE)",
    )
    run_parser.add_argument("--skip-check", action="store_true", help="skip the type-checker preflight")
    run_parser.add_argument(
        "--checker",
        choices=("pyrefly", "mypy", "pyright"),
        default="pyrefly",
        metavar="CHECKER",
        help="type-checker preflight",
    )
    run_parser.add_argument("--no-cache", action="store_true", help="always recompile (env PYSC_NO_RUN_CACHE=1)")
    run_parser.add_argument(
        "--ffi",
        action="append",
        metavar="MANIFEST",
        default=[],
        help="bind a typed extern manifest (TOML) and link its C sources; repeatable",
    )
    verify_parser = sub.add_parser(
        "verify",
        help="compile, run under CPython 3.14 and natively, and diff stdout, stderr and the exit code",
        parents=[common],
    )
    verify_parser.add_argument("source", help="Python file or package directory to compile")
    verify_parser.add_argument("--call", metavar="FUNC", help="function to call on both sides, not the program entry")
    verify_parser.add_argument("--args", metavar="ARGS", default="[]", help="Python literal args list for --call")
    verify_parser.add_argument(
        "--input",
        metavar="FILE",
        help="feed FILE's bytes on stdin to both runs (default: an empty stdin, so input() sees EOF)",
    )
    verify_parser.add_argument("--json", action="store_true", help="print the receipt as JSON instead of text")
    verify_parser.add_argument(
        "--timeout",
        type=float,
        default=_VERIFY_DEFAULT_TIMEOUT,
        metavar="S",
        help=f"seconds each run may take before it is a divergence (default {_VERIFY_DEFAULT_TIMEOUT:g})",
    )
    verify_parser.add_argument("--program", action="store_true", help="treat the directory as a program (see compile)")
    verify_parser.add_argument(
        "--driver", choices=("clang", "zig"), default=None, metavar="DRIVER", help="C toolchain driver (see compile)"
    )
    verify_parser.add_argument("--target", metavar="TRIPLE", help="cross-compilation triple for `zig cc` (see compile)")
    verify_parser.add_argument(
        "--linker", choices=("clang", "zig"), default=None, metavar="DRIVER", help="link driver (see compile)"
    )
    verify_parser.add_argument("--skip-check", action="store_true", help="skip the type-checker preflight")
    verify_parser.add_argument(
        "--checker",
        choices=("pyrefly", "mypy", "pyright"),
        default="pyrefly",
        metavar="CHECKER",
        help="type-checker preflight",
    )
    verify_parser.add_argument("--no-cache", action="store_true", help="always recompile (env PYSC_NO_RUN_CACHE=1)")
    verify_parser.add_argument(
        "--ffi",
        action="append",
        metavar="MANIFEST",
        default=[],
        help="bind a typed extern manifest (TOML) and link its C sources; repeatable",
    )
    verify_parser.set_defaults(sanitize=None, debug_info=False)
    assess_parser = sub.add_parser(
        "assess",
        help="assess any script, library or app for native gains and fallback routes",
        parents=[common],
    )
    assess_parser.add_argument("source", help="script, package directory or app")
    assess_parser.add_argument("--json", action="store_true", help="print the assessment report as JSON")
    assess_parser.add_argument(
        "--all",
        action="store_true",
        help="print the full report without capping refusals (default top-3 per root cause)",
    )
    assess_parser.add_argument(
        "--bench",
        action="store_true",
        help="time CPython against native binary on compilable entry points and print ratio",
    )
    assess_parser.add_argument(
        "--verify",
        action="store_true",
        help="run differential verify on compilable entry points and show receipt inline",
    )
    assess_parser.add_argument("--call", metavar="FUNC", help="function to call on entry points, not the program entry")
    assess_parser.add_argument("--args", metavar="ARGS", default="[]", help="Python literal args list for --call")
    assess_parser.add_argument("--input", metavar="FILE", help="feed FILE's bytes on stdin to verify and bench")
    assess_parser.add_argument(
        "--timeout",
        type=float,
        default=_VERIFY_DEFAULT_TIMEOUT,
        metavar="S",
        help=f"seconds each run may take (default {_VERIFY_DEFAULT_TIMEOUT:g})",
    )
    assess_parser.add_argument("--program", action="store_true", help="treat the directory as a program")
    assess_parser.add_argument(
        "--driver", choices=("clang", "zig"), default=None, metavar="DRIVER", help="C toolchain driver (see compile)"
    )
    assess_parser.add_argument("--target", metavar="TRIPLE", help="cross-compilation triple for `zig cc` (see compile)")
    assess_parser.add_argument(
        "--linker", choices=("clang", "zig"), default=None, metavar="DRIVER", help="link driver (see compile)"
    )
    assess_parser.add_argument("--skip-check", action="store_true", help="skip the type-checker preflight")
    assess_parser.add_argument(
        "--checker",
        choices=("pyrefly", "mypy", "pyright"),
        default="pyrefly",
        metavar="CHECKER",
        help="type-checker preflight",
    )
    assess_parser.add_argument("--no-cache", action="store_true", help="always recompile (env PYSC_NO_RUN_CACHE=1)")
    assess_parser.add_argument(
        "--ffi",
        action="append",
        metavar="MANIFEST",
        default=[],
        help="bind a typed extern manifest (TOML) and link its C sources; repeatable",
    )
    assess_parser.set_defaults(sanitize=None, debug_info=False)
    coverage_parser = sub.add_parser(
        "coverage", help="report everything a file or package tree cannot compile", parents=[common]
    )
    coverage_parser.add_argument("source", help="Python file or package directory to report on")
    coverage_parser.add_argument("--json", action="store_true", help="print the report as JSON")
    coverage_parser.add_argument(
        "--fail-on",
        choices=("blockers", "any"),
        help="exit 1 when root refusals (blockers) or any refusal (any) exist; default always exits 0",
    )
    config_parser = sub.add_parser("config", help="show the resolved config for a source", parents=[common])
    config_parser.add_argument("source", help="Python file, pyproject.toml or directory")
    config_parser.add_argument("--json", action="store_true", help="print the resolved config as JSON")
    explain_parser = sub.add_parser("explain", help="describe a diagnostic code", parents=[common])
    explain_parser.add_argument("code", help="a PSxxxx diagnostic code, for example PS2001")
    imports_parser = sub.add_parser(
        "imports", help="classify every import of a file or package tree into a compat lane", parents=[common]
    )
    imports_parser.add_argument("source", help="Python file or package directory to classify")
    imports_parser.add_argument("--json", action="store_true", help="print the report as JSON")
    imports_parser.add_argument(
        "--reach",
        metavar="FILE",
        help="append the embedded-CPython reach share to a JSON file (the compat ledger reads it)",
    )
    reduce_parser = sub.add_parser(
        "reduce", help="delta-debug a source to a minimal reproducer of a refusal or crash", parents=[common]
    )
    reduce_parser.add_argument("source", help="the Python file to shrink")
    group = reduce_parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--predicate",
        metavar="CMD",
        help="shell command run on each candidate file; it exits 0 when the candidate still "
        "reproduces (the candidate path arrives in $PSC_SOURCE)",
    )
    group.add_argument("--fence", metavar="CODE", help="shorthand: compiling still reports a refusal with this code")
    group.add_argument("--crash", action="store_true", help="shorthand: compiling still raises a non-refusal error")
    reduce_parser.add_argument(
        "--timeout",
        type=float,
        default=_REDUCE_DEFAULT_TIMEOUT,
        help=f"seconds per --predicate command (default {_REDUCE_DEFAULT_TIMEOUT:g})",
    )
    reduce_parser.add_argument("--steps", type=int, default=10_000, help="cap on removal attempts (default 10000)")
    reduce_parser.add_argument(
        "--print", action="store_true", help="print the reduced source instead of writing FILE.min.py"
    )
    reduce_parser.add_argument("--json", action="store_true", help="print the removal log as JSON")
    build_parser = sub.add_parser(
        "build", help="project mode: one binary per [project.scripts] entry point", parents=[common]
    )
    build_parser.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="the project directory (with a pyproject.toml); default .",
    )
    build_parser.add_argument("--script", help="build only this console script; default every script")
    build_parser.add_argument("--out-dir", metavar="DIR", help="write the binaries to DIR instead of <project>/build")
    build_parser.add_argument(
        "--driver", choices=("clang", "zig"), default=None, metavar="DRIVER", help="C toolchain driver (see compile)"
    )
    build_parser.add_argument("--target", metavar="TRIPLE", help="cross-compilation triple for `zig cc` (see compile)")
    build_parser.add_argument(
        "--linker", choices=("clang", "zig"), default=None, metavar="DRIVER", help="link driver (see compile)"
    )
    build_parser.add_argument("--skip-check", action="store_true", help="skip the type-checker preflight")
    build_parser.add_argument(
        "--checker",
        choices=("pyrefly", "mypy", "pyright"),
        default="pyrefly",
        metavar="CHECKER",
        help="type-checker preflight",
    )
    sub.add_parser("logs", help="print the audit-log path and its last lines", parents=[common])
    telemetry_parser = sub.add_parser("telemetry", help="usage metrics opt-out")
    telemetry_parser.add_argument("action", choices=("disable", "enable", "status", "reset-id"), help="what to do")
    doctor_parser = sub.add_parser("doctor", help="report toolchain, cache and install shape", parents=[common])
    doctor_parser.add_argument("--json", action="store_true", help="print the report as JSON")
    deps_parser = sub.add_parser(
        "deps",
        help="resolve PyPI dependencies (PEP 723 or pyproject) and cache pure-Python wheels",
        parents=[common],
    )
    deps_parser.add_argument(
        "source", help="a .py script with a `# /// script` block, a pyproject.toml or its directory"
    )
    deps_parser.add_argument("--json", action="store_true", help="print the report as JSON")
    deps_parser.add_argument("--frozen", action="store_true", help="use the existing lock, never resolve")
    deps_parser.add_argument("--upgrade", action="store_true", help="re-resolve to the newest allowed versions")
    deps_parser.add_argument("--offline", action="store_true", help="no network: uv offline, cached wheels only")
    deps_parser.add_argument("--no-download", action="store_true", help="classify from wheel tags only")
    deps_parser.add_argument(
        "--cache-dir", metavar="DIR", help="cache root (env PYSC_CACHE_DIR, default ~/.cache/pyscriptc)"
    )
    # boxed:begin
    embed_parser = sub.add_parser(
        "embed",
        help="manage the pinned CPython runtime pack for --embed-python=bundle",
        parents=[common],
    )
    embed_sub = embed_parser.add_subparsers(dest="embed_cmd", required=True)
    fetch_parser = embed_sub.add_parser("fetch", help="download, verify and unpack the runtime pack")
    fetch_parser.add_argument(
        "--platform", metavar="PLATFORM", default=None, help="pack platform (default: this machine)"
    )
    fetch_parser.add_argument("--force", action="store_true", help="re-download even when the pack is cached")
    # boxed:end
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        raw = list(sys.argv[1:] if argv is None else argv)
        run_argv: list[str] = []
        if "--" in raw:
            split = raw.index("--")
            raw, run_argv = raw[:split], raw[split + 1 :]
        # boxed:begin
        # Fix #622: `run --report` must not swallow the source positional.
        # If `--report` is followed by a token that is the only positional
        # argument on the command line, that token is `source` and `--report` is bare.
        if "run" in raw and "--report" in raw:
            run_idx = raw.index("run")
            rep_idx = raw.index("--report")
            if rep_idx > run_idx and rep_idx + 1 < len(raw) and not raw[rep_idx + 1].startswith("-"):
                one_arg_flags = {
                    opt
                    for act in parser._subparsers._group_actions[0].choices["run"]._actions
                    for opt in act.option_strings
                    if act.nargs is None and not isinstance(act, (argparse._StoreTrueAction, argparse._HelpAction))
                }
                positionals: list[str] = []
                i = run_idx + 1
                while i < len(raw):
                    token = raw[i]
                    if token == "--report":
                        i += 1
                        continue
                    if token.startswith("-"):
                        if "=" in token:
                            i += 1
                        elif token in one_arg_flags:
                            i += 2
                        else:
                            i += 1
                    else:
                        positionals.append(token)
                        i += 1
                if len(positionals) == 1:
                    target_pos = positionals[0]
                    raw.remove("--report")
                    target_idx = raw.index(target_pos)
                    raw.insert(target_idx + 1, "--report")
        # boxed:end
        args = parser.parse_args(raw)
        args.run_argv = run_argv
        if run_argv and not (args.cmd in ("verify", "run", "assess") or (args.cmd == "compile" and args.run)):
            parser.error("arguments after `--` need `run`, `compile --run`, `verify` or `assess`")
        if getattr(args, "verbose", False):
            enable_debug()
        log.info("cli.invoke", cmd=args.cmd)
        if args.cmd != "telemetry":
            telemetry.maybe_print_notice()
            telemetry.track("cli.invoked", {"subcommand": args.cmd, **telemetry.base_properties()})
        if args.cmd == "compile":
            _load_compiler()
            return compile_cmd(args)
        if args.cmd == "run":
            return run_cmd(args)
        if args.cmd == "verify":
            _load_compiler()
            return verify_cmd(args)
        if args.cmd == "assess":
            _load_compiler()
            return assess_cmd(args)
        if args.cmd == "coverage":
            _load_compiler()
            return coverage_cmd(args)
        if args.cmd == "imports":
            _load_compiler()
            return imports_cmd(args)
        if args.cmd == "config":
            return config_cmd(args)
        if args.cmd == "reduce":
            _load_compiler()
            return reduce_cmd(args)
        if args.cmd == "build":
            _load_compiler()
            return build_cmd(args)
        if args.cmd == "explain":
            _load_compiler()
            return explain_cmd(args)
        if args.cmd == "logs":
            return logs_cmd(args)
        if args.cmd == "doctor":
            _load_compiler()
            return doctor_cmd(args.json)
        if args.cmd == "deps":
            _load_compiler()
            return deps.deps_cmd(args)
        if args.cmd == "telemetry":
            return telemetry.telemetry_cmd(args.action)
        # boxed:begin
        if args.cmd == "embed":
            return embed_cmd(args)
        # boxed:end
    except CheckFailed as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (VersionError, FitError) as exc:
        print(str(exc), file=sys.stderr)
        return 4
    except SpikeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    # boxed:begin
    except (embed.EmbedError, sitedeps.SiteError) as exc:
        # Issues #661 and #662: a bundle compile that cannot get its pack (or
        # that carries a native dependency) is a user-facing refusal, not a
        # crash: one line naming `pyscriptc embed fetch`, no traceback.
        print(str(exc), file=sys.stderr)
        return 1
    # boxed:end
    return 2


def embed_cmd(args: argparse.Namespace) -> int:
    """`pyscriptc embed fetch`: download, verify and unpack the pinned runtime pack."""
    if args.embed_cmd != "fetch":
        raise IRError(f"unknown embed command {args.embed_cmd!r} (known: fetch)")
    pack = embed.lock_pack(getattr(args, "platform", None))
    directory = embed.pack_dir(pack)
    if embed.pack_is_ready(pack) and not args.force:
        print(f"runtime pack already present: {directory}")
        return 0
    if embed.pack_is_ready(pack) and args.force:
        print(f"re-fetching runtime pack {pack.pack_id}", file=sys.stderr)
    else:
        print(
            f"fetching runtime pack {pack.pack_id} ({pack.cpython}, {pack.release})",
            file=sys.stderr,
        )
    embed.fetch(pack, force=args.force)
    print(f"runtime pack ready: {directory}")
    return 0


def run_cmd(args: argparse.Namespace) -> int:
    """`pyscriptc run FILE [-- args]`: compile once per content hash, then run the cached binary."""
    started = time.monotonic()
    _apply_env_defaults(args)
    # boxed:begin
    # A bundle link writes a directory (`name/bin/name` plus `name/lib/`), not
    # a single file, so it does not fit the run cache's one-binary-per-key
    # shape. The compile command is the way to make a bundle.
    if getattr(args, "embed_python", None) == "bundle":
        raise IRError(
            "--embed-python=bundle writes a relocatable directory and does not fit the run cache; "
            "use `pyscriptc compile --embed-python=bundle -o OUT/name` and run OUT/name/bin/name"
        )
    # boxed:end
    source = Path(args.source)
    if not source.exists():
        raise IRError(f"source not found: {source}")
    run_env = _run_environment(source)
    use_cache = runcache.enabled() and not args.no_cache
    key = _run_key(args)
    name = (source.resolve().name if source.is_dir() else source.stem) or "app"
    binary = runcache.binary_path(key, name)
    if use_cache and binary.is_file():
        log.info("run.cache_hit", key=key, binary=str(binary))
        code = _exec_binary(binary, args.run_argv, env=run_env)
        _track_run(
            args,
            code,
            duration_ms=int((time.monotonic() - started) * 1000),
            cache_hit=True,
            binary_bytes=_binary_size(binary),
        )
        # boxed:begin
        if getattr(args, "report", None):
            _handle_run_report(args.report, binary, source)
        # boxed:end
        return code
    build = runcache.cache_dir() / f".build-{os.getpid()}-{key}"
    shutil.rmtree(build, ignore_errors=True)
    build.mkdir(parents=True, exist_ok=True)
    try:
        code = _build_for_run(args, build / name)
        if code != 0:
            return code
        if use_cache:
            try:
                os.replace(build, binary.parent)
                log.info("run.cache_store", key=key, binary=str(binary))
            except OSError:
                # another run stored the same content first; build stays ours to run and clean
                if not binary.is_file():
                    raise
            else:
                code = _exec_binary(binary, args.run_argv, env=run_env)
                _track_run(
                    args,
                    code,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    cache_hit=False,
                    binary_bytes=_binary_size(binary),
                )
                # boxed:begin
                if getattr(args, "report", None):
                    _handle_run_report(args.report, binary, source)
                # boxed:end
                return code
        code = _exec_binary(build / name, args.run_argv, env=run_env)
        _track_run(
            args,
            code,
            duration_ms=int((time.monotonic() - started) * 1000),
            cache_hit=False,
            binary_bytes=_binary_size(build / name),
        )
        # boxed:begin
        if getattr(args, "report", None):
            _handle_run_report(args.report, build / name, source)
        # boxed:end
        return code
    finally:
        shutil.rmtree(build, ignore_errors=True)


# boxed:begin
def _handle_run_report(report_target: Any, binary: Path, source_path: Path) -> None:
    report_file = binary.parent / f"{binary.name}.report.json"
    if report_file.is_file():
        report_data = json.loads(report_file.read_text(encoding="utf-8"))
    else:
        ir_file = binary.parent / f"{binary.name}.ir.json"
        from pyscriptc import boxed as _boxed

        config = load_config(source_path)
        if ir_file.is_file():
            mod = json.loads(ir_file.read_text(encoding="utf-8"))
            plane_map = mod.get("fallback")
            if plane_map is None:
                all_names = [f["name"] for f in mod.get("functions", [])]
                plane_map = _boxed.plane_map([], [{"name": n} for n in dict.fromkeys(all_names)], mod.get("entry"))
            requires_lp = bool(
                mod.get("islandSource")
                or (plane_map and any(p == _boxed.ISLAND for p in plane_map.get("functions", {}).values()))
                or (plane_map and any(p == _boxed.ISLAND for p in plane_map.get("classes", {}).values()))
                or (plane_map and plane_map.get("moduleTopLevel") == _boxed.ISLAND)
                or config.island_imports()
            )
        else:
            plane_map = _boxed.plane_map([], [], None)
            requires_lp = bool(config.island_imports())
        report_data = {
            "target": str(source_path),
            "planeMap": plane_map,
            "fallback": plane_map,
            "requiresLibpython": requires_lp,
            "islandImports": list(config.island_imports()),
        }

    rendered = json.dumps(report_data, indent=2) + "\n"
    if isinstance(report_target, str):
        Path(report_target).write_text(rendered, encoding="utf-8")
    else:
        sys.stderr.write(rendered)


# boxed:end


def _run_key(args: argparse.Namespace) -> str:
    """The content-hash key of the binary `run` and `verify` share.

    Everything that changes the linked output is a part; argv and stdin do not
    change the binary, so they stay out. The source file's own name is a part
    too: sibling scripts in one directory share the content tree, so without it
    two different programs would claim the same cache entry.
    """
    source = Path(args.source)
    name = (source.resolve().name if source.is_dir() else source.stem) or "app"
    ffi_paths = [str(p) for p in getattr(args, "ffi", None) or []]
    ffi_paths.extend(str(p) for p in load_config(source).ffi_manifests)
    return runcache.key_for(
        source,
        load_config(source).origin,
        [
            f"source={name}",
            f"program={args.program}",
            f"call={args.call}",
            f"args={args.args if args.call else ''}",
            f"driver={args.driver}",
            f"target={args.target}",
            f"linker={args.linker}",
            f"sanitize={args.sanitize}",
            f"debug_info={getattr(args, 'debug_info', False)}",
            f"skip_check={args.skip_check}",
            f"checker={args.checker}",
            f"ffi={','.join(ffi_paths)}",
            # boxed:begin
            f"fallback={getattr(args, 'fallback', None)}",
            f"island={getattr(args, 'island', False)}",
            f"embed_python={getattr(args, 'embed_python', None)}",
            # boxed:end
        ],
    )


def _binary_size(binary: Path) -> int | None:
    """Size of the linked binary in bytes, or None when it is missing."""
    try:
        return binary.stat().st_size
    except OSError:
        return None


def _track_run(
    args: argparse.Namespace,
    code: int,
    *,
    duration_ms: int,
    cache_hit: bool,
    binary_bytes: int | None = None,
) -> None:
    """Send the `run.finished` event: outcome, duration, toolchain flags, binary size only."""
    telemetry.track(
        "run.finished",
        telemetry.run_properties(
            duration_ms=duration_ms,
            exit_code=code,
            cache_hit=cache_hit,
            binary_bytes=binary_bytes,
            driver=args.driver,
            target=args.target,
            skip_check=bool(args.skip_check),
        ),
    )


def _build_for_run(args: argparse.Namespace, output: Path) -> int:
    _load_compiler()
    compile_args = argparse.Namespace(
        source=args.source,
        program=args.program,
        emit="bin",
        out_dir=None,
        output=str(output),
        call=args.call,
        args=args.args,
        run=False,
        # boxed:begin
        fallback=getattr(args, "fallback", None),
        island=getattr(args, "island", False),
        embed_python=getattr(args, "embed_python", None),
        # boxed:end
        driver=args.driver,
        target=args.target,
        linker=args.linker,
        sanitize=args.sanitize,
        debug_info=getattr(args, "debug_info", False),
        print_what=None,
        skip_check=args.skip_check,
        checker=args.checker,
        debug=None,
        debug_dir=None,
        run_argv=[],
    )
    compile_args.ffi = [*getattr(args, "ffi", [])]
    compile_args.ffi.extend(str(p) for p in load_config(Path(args.source)).ffi_manifests)
    # compile prints the binary path on success; keep stdout clean for the program
    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        return compile_cmd(compile_args)
    finally:
        sys.stdout = real_stdout


def _run_environment(source_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    prog_dir = str((source_path if source_path.is_dir() else source_path.parent).resolve())
    env.setdefault("PYSC_PROGRAM_DIR", prog_dir)
    if "VIRTUAL_ENV" not in env and sys.prefix != sys.base_prefix:
        env["VIRTUAL_ENV"] = sys.prefix
    return env


def _exec_binary(binary: Path, argv: list[str], env: dict[str, str] | None = None) -> int:
    completed = subprocess.run([str(binary.absolute()), *argv], env=env)
    code = 128 + (-completed.returncode) if completed.returncode < 0 else completed.returncode
    log.info("run.exit", binary=str(binary), code=code)
    return code


def verify_cmd(args: argparse.Namespace) -> int:
    """`pyscriptc verify FILE`: run under the CPython 3.14 oracle and natively, then diff.

    A differential smoke test, not a proof: it covers the paths the argv and
    stdin exercised. Exit 0 when every check matches, 1 on a divergence, 3 when
    the compiler fences the program, 4 when no pinned oracle is available.
    """
    _load_compiler()
    started = time.monotonic()
    _apply_env_defaults(args)
    source = Path(args.source)
    if not source.exists():
        raise IRError(f"source not found: {source}")
    if not source.is_file():
        raise IRError("verify takes one file; run it per file, or `pyscriptc coverage DIR` for a package tree")
    if not (args.timeout > 0):
        raise IRError("--timeout must be a positive number of seconds")
    if args.input is not None and not Path(args.input).exists():
        raise IRError(f"--input file not found: {args.input}")
    stdin = Path(args.input).read_bytes() if args.input is not None else b""
    oracle = verify.find_oracle()
    call_args = _parse_args(args.args) if args.call else []
    name = source.stem or "app"
    key = _run_key(args)
    with tempfile.TemporaryDirectory(prefix="pyscriptc-verify-") as scratch_name:
        scratch = Path(scratch_name)
        compile_started = time.monotonic()
        binary, cache_hit, code, compile_log = _verify_binary(args, name, key, scratch)
        compile_ms = int((time.monotonic() - compile_started) * 1000)
        if code != 0:
            failure = verify.failure_receipt(str(source), oracle, code, compile_log, timeout_seconds=args.timeout)
            print(json.dumps(failure, indent=2) if args.json else verify.render_failure(failure))
            log.info("verify.refused", source=str(source), exit_code=code)
            return code
        resolved = source.resolve()
        receipt = verify.measure(
            resolved,
            oracle,
            binary,
            argv=list(args.run_argv),
            stdin=stdin,
            stdin_path=args.input,
            call=args.call,
            call_args=call_args,
            timeout=args.timeout,
            scratch=scratch,
            cache_hit=cache_hit,
            compile_ms=compile_ms,
            started=started,
        )
    print(json.dumps(receipt.as_dict(), indent=2) if args.json else verify.render_text(receipt))
    log.info("verify.finished", source=str(source), match=receipt.match, cache_hit=cache_hit)
    telemetry.track(
        "verify.finished",
        {
            "duration_ms": receipt.duration_ms,
            "compile_ms": compile_ms,
            "match": receipt.match,
            "cache_hit": cache_hit,
            "check_count": len(receipt.checks),
            "diverged": [check["name"] for check in receipt.checks if not check["match"]],
            "call": args.call is not None,
            "timeout": args.timeout,
        },
    )
    return 0 if receipt.match else 1


def assess_cmd(args: argparse.Namespace) -> int:
    """`pyscriptc assess PATH`: throw pyscriptc at any script, library or app."""
    _load_compiler()
    _apply_env_defaults(args)
    source = Path(args.source)
    if not source.exists():
        raise IRError(f"source not found: {source}")
    call_args = _parse_args(args.args) if args.call else []
    report = assess.assess(
        source,
        bench=args.bench,
        verify_diff=args.verify,
        run_argv=args.run_argv,
        stdin_path=args.input,
        call=args.call,
        call_args=call_args,
        timeout=args.timeout,
        compile_args=args,
    )
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(assess.render_text(report, show_all=args.all))

    if args.verify and report.get("verify"):
        for rec in report["verify"]:
            if rec.get("match") is False or "error" in rec:
                return 1
    return 0


def _verify_binary(args: argparse.Namespace, name: str, key: str, scratch: Path) -> tuple[Path, bool, int, str]:
    """The binary to verify, its cache state, and the compile's exit code and log.

    The build goes through `_build_for_run`, so verify compiles exactly what
    `run` compiles and shares its content-hash cache. The compiler's own stderr
    is captured: a fence report belongs in the receipt, not above it.
    """
    use_cache = runcache.enabled() and not args.no_cache
    cached = runcache.binary_path(key, name)
    if use_cache and cached.is_file():
        log.info("verify.cache_hit", key=key, binary=str(cached))
        return cached, True, 0, ""
    build = scratch / "build"
    build.mkdir(parents=True, exist_ok=True)
    output = io.StringIO()
    try:
        with contextlib.redirect_stderr(output):
            code = _build_for_run(args, build / name)
    except BaseException:
        sys.stderr.write(output.getvalue())
        raise
    if code != 0:
        return build / name, False, code, output.getvalue()
    if not use_cache:
        return build / name, False, 0, output.getvalue()
    try:
        cached.parent.mkdir(parents=True, exist_ok=True)
        os.replace(build, cached.parent)
    except OSError:
        # another store owns the entry (or left a partial one); a fresh build
        # in the scratch directory is still a valid binary to verify
        log.info("verify.cache_store_skipped", key=key)
        if cached.is_file():
            return cached, False, 0, output.getvalue()
        return build / name, False, 0, output.getvalue()
    return cached, False, 0, output.getvalue()


def coverage_cmd(args: argparse.Namespace) -> int:
    _load_compiler()
    report = build_report(Path(args.source))
    log.info("coverage.report", source=args.source)
    print(json.dumps(report, indent=2) if args.json else render_text(report))
    summary = report["summary"]
    telemetry.track("coverage.reported", telemetry.coverage_properties(summary))
    mode = args.fail_on
    if mode is None:
        config = load_config(Path(args.source))
        mode = config.fail_on
    if mode == "any" and summary["fences"]:
        return 1
    if mode == "blockers" and summary["rootFences"]:
        return 1
    return 0


def imports_cmd(args: argparse.Namespace) -> int:
    _load_compiler()
    source = Path(args.source)
    config = load_config(source)
    report = build_import_report(source, policy=config.imports)
    log.info("imports.report", source=args.source)
    print(json.dumps(report, indent=2) if args.json else render_import_text(report))
    if args.reach:
        payload = export_reach(report, Path(args.reach))
        share = payload["boxed_only_reach"][-1]
        log.info("imports.reach", path=args.reach, reach=share)
        print(f"island reach {share:.4f} appended to {args.reach}", file=sys.stderr)
    return 0


def config_cmd(args: argparse.Namespace) -> int:
    config = load_config(Path(args.source))
    scripts = _scripts_of(Path(args.source))
    data = {
        "source": config.source,
        "origin": str(config.origin) if config.origin else None,
        "entry": config.entry,
        "src": list(config.src),
        "checker": config.checker,
        "driver": config.driver,
        "target": config.target,
        "fail-on": config.fail_on,
        "requires-python": config.requires_python,
        "dependencies": list(config.dependencies),
        "stub-path": list(config.stub_path),
        "ffi": list(config.ffi),
        "dependency-lanes": config.dependency_lanes(),
        "imports": {name: config.lane_for(name)[0] for name in sorted(config.imports)},
        "scripts": scripts,
    }
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    print(f"source: {data['source']}")
    if data["origin"]:
        print(f"origin: {data['origin']}")
    for key in ("entry", "checker", "driver", "target", "fail-on", "requires-python"):
        if data[key] is not None:
            print(f"{key}: {data[key]}")
    if data["src"]:
        print(f"src: {', '.join(data['src'])}")
    for key in ("stub-path", "ffi"):
        if data[key]:
            print(f"{key}: {', '.join(data[key])}")
    if data["dependencies"]:
        print(f"dependencies: {', '.join(data['dependencies'])}")
        for req, lane in data["dependency-lanes"].items():
            print(f"  {req}: {lane}")
    for name, lane in data["imports"].items():
        print(f"import {name}: {lane}")
    if data["scripts"]:
        print("scripts:")
        for name, target in data["scripts"].items():
            print(f"  {name}: {target}")
    return 0


def _scripts_of(source: Path) -> dict[str, str]:
    """The console scripts of the pyproject above `source`, if any."""

    from pyscriptc import project

    try:
        pyproject = project.discover_pyproject(source)
    except project.ProjectError:
        return {}
    try:
        return entry_points(pyproject)
    except Exception:
        return {}


def _reduce_predicate(args: argparse.Namespace) -> reduce.Predicate:
    """The predicate the reducer checks on every candidate."""
    if args.predicate:
        source = Path(args.source)
        return reduce.CommandPredicate(
            args.predicate,
            directory=source.parent if source.parent.is_dir() else Path.cwd(),
            suffix=source.suffix or ".py",
            timeout=args.timeout,
        )
    if args.fence:
        return reduce.FencePredicate(args.fence)
    return reduce.CrashPredicate()


def reduce_cmd(args: argparse.Namespace) -> int:
    """`pyscriptc reduce FILE`: shrink FILE to a minimal reproducer."""
    _load_compiler()
    predicate = _reduce_predicate(args)
    out_path = Path(args.source).with_name(f"{Path(args.source).stem}.min.py")
    target = out_path if not args.print and not args.json else None
    result = reduce.reduce_file(args.source, predicate, output=target, max_steps=args.steps)
    kept = sum(1 for step in result.steps if step.kept)
    log.info(
        "reduce.done",
        source=args.source,
        candidates=result.candidates,
        kept=kept,
        verdict=str(result.verdict),
    )
    telemetry.track(
        "reduce.finished",
        {
            "candidates": result.candidates,
            "steps": len(result.steps),
            "kept": kept,
            "changed": result.changed,
            "mode": "predicate" if args.predicate else "fence" if args.fence else "crash",
        },
    )
    if args.json:
        print(json.dumps(result.as_dict(), indent=2))
    elif args.print:
        print(result.source, end="")
    else:
        print(f"predicate: {result.predicate}")
        print(f"{result.candidates} candidates, {len(result.steps)} steps, {kept} kept")
        print(f"wrote {out_path} ({'changed' if result.changed else 'no reduction found'})")
        print(f"verdict: {result.verdict}")
    return 0 if result.verdict.holds else 1


def explain_cmd(args: argparse.Namespace) -> int:
    _load_compiler()
    text = explain(args.code)
    log.info("explain.lookup", code=args.code, known=text is not None)
    if text is None:
        print(f"unknown diagnostic code: {args.code}", file=sys.stderr)
        return 2
    print(text)
    return 0


def logs_cmd(args: argparse.Namespace) -> int:
    tail()
    return 0


def build_cmd(args: argparse.Namespace) -> int:
    """`pyscriptc build [DIR]`: one binary per [project.scripts] entry point."""

    from pyscriptc import project

    started = time.monotonic()
    _apply_env_defaults(args)
    if args.target is not None and args.driver != "zig":
        raise IRError("--target requires --driver zig (bundled sysroot per target)")
    directory = Path(args.directory)
    pyproject = project.discover_pyproject(directory)
    scripts = entry_points(pyproject)
    if not scripts:
        print(f"{pyproject}: no [project.scripts] table, nothing to build", file=sys.stderr)
        return 1
    names = sorted(scripts) if args.script is None else [args.script]
    for name in names:
        if name not in scripts:
            known = ", ".join(sorted(scripts))
            print(f"{pyproject}: no console script {name!r} (known: {known})", file=sys.stderr)
            return 2
    state: dict[str, Any] = {"outcome": None, "stage": "lower", "fence_count": 0, "fence_families": {}}
    try:
        out_dir = Path(args.out_dir) if args.out_dir else pyproject.parent / "build"
        out_dir.mkdir(parents=True, exist_ok=True)
        built: list[Path] = []
        for name in names:
            module_ir, _ = project.lower_script(pyproject, name)
            if module_ir["fences"]:
                state["fence_count"] = len(module_ir["fences"])
                state["fence_families"] = _fence_families(module_ir["fences"])
                print(render_fences(module_ir, module_ir["sourceFile"]), file=sys.stderr)
                state["outcome"] = "fences"
                return 3
            state["stage"] = "check"
            if not args.skip_check:
                run_checker(Path(module_ir["sourceFile"]), args.checker)
            state["stage"] = "emit"
            llvm_text = emit_llvm(module_ir, None, [], standalone=True)
            with tempfile.TemporaryDirectory(prefix="pyscriptc-build-") as scratch:
                ll_path = Path(scratch) / f"{name}.ll"
                ll_path.write_text(llvm_text, encoding="utf-8")
                state["stage"] = "link"
                # The link step writes its object files next to the output, so
                # it runs in the scratch directory and the binary moves.
                staged = Path(scratch) / name
                link_binary(
                    ll_path,
                    staged,
                    driver=args.driver or "clang",
                    target=args.target,
                    link_driver=args.linker,
                )
                binary = out_dir / name
                shutil.move(str(staged), str(binary))
            built.append(binary)
            print(binary)
        state["outcome"] = "ok"
        log.info(
            "build.finished",
            scripts=len(built),
            out_dir=str(out_dir),
            ms=int((time.monotonic() - started) * 1000),
        )
        return 0
    except BaseException as exc:
        if state["outcome"] is None:
            state["outcome"] = _compile_outcome(exc, state["stage"])
        raise


def compile_cmd(args: argparse.Namespace) -> int:
    _load_compiler()
    started = time.monotonic()
    _compile_env_defaults(args)
    state: dict[str, Any] = {
        "outcome": None,
        "stage": "lower",
        "fence_count": 0,
        "fence_families": {},
        "fence_codes": {},
        "binary_bytes": None,
    }
    try:
        return _compile_cmd(args, state)
    except BaseException as exc:
        if state["outcome"] is None:
            state["outcome"] = _compile_outcome(exc, state["stage"])
        raise
    finally:
        telemetry.track(
            "compile.finished",
            telemetry.compile_properties(
                duration_ms=int((time.monotonic() - started) * 1000),
                outcome=state["outcome"] or "ok",
                fence_count=state["fence_count"],
                fence_families=state["fence_families"],
                fence_codes=state["fence_codes"],
                binary_bytes=state["binary_bytes"],
                checker=None if args.skip_check else args.checker,
                emit=args.emit,
                driver=args.driver,
                skip_check=bool(args.skip_check),
            ),
        )


def _resolve_default_driver(target: str | None = None, sanitize: list[str] | None = None) -> str | None:
    """Resolve the default C driver when not explicitly specified via --driver or PYSC_CC.

    Rules:
    - If target is set (or targets Windows) -> prefer zig.
    - If sys.platform == "win32" -> prefer zig.
    - If a sanitizer is requested -> prefer clang: `zig cc` compiles with
      `-fsanitize=` but ships no linkable sanitizer runtime, so the link
      fails with `undefined symbol: __asan_init`.
    - If shutil.which("zig") is found on PATH -> prefer zig.
    - Otherwise, fall back to clang on macOS/Linux if found on PATH.
    - If neither is found, return None.
    """
    if target is not None or sys.platform == "win32":
        return "zig"
    if sanitize:
        if shutil.which("clang") is not None:
            return "clang"
        return None
    if shutil.which("zig") is not None:
        return "zig"
    if shutil.which("clang") is not None:
        return "clang"
    return None


def _apply_env_defaults(args: argparse.Namespace) -> None:
    """Fill unset toolchain flags from PYSC_CC / PYSC_LINKER / PYSC_TARGET; explicit flags win."""
    for attr, var in (("driver", "PYSC_CC"), ("linker", "PYSC_LINKER")):
        if getattr(args, attr) is None:
            value = os.environ.get(var) or None
            if value is not None and value not in ("clang", "zig"):
                raise IRError(f"{var} must be clang or zig, got {value!r}")
            setattr(args, attr, value)
    if args.target is None:
        args.target = os.environ.get("PYSC_TARGET") or None
    if getattr(args, "sanitize", None) is None:
        args.sanitize = os.environ.get("PYSC_SANITIZE") or None
    if args.driver is None:
        args.driver = _resolve_default_driver(args.target, _sanitize_list(args.sanitize))
    if not getattr(args, "debug_info", False):
        args.debug_info = os.environ.get("PYSC_DEBUG_INFO", "") not in ("", "0", "false", "no")


def _compile_env_defaults(args: argparse.Namespace) -> None:
    """The compile-time flag defaults: env first, then the mode-specific ones.

    `--emit lib` compiles through clang with an explicit sysroot (Xcode SDK
    or NDK); zig bundles neither bionic nor the Apple SDKs, so the library
    mode defaults the driver to clang. An explicit `--driver zig` is left in
    place so `build_library` refuses it with the message that says why.
    """
    explicit_driver = args.driver is not None
    _apply_env_defaults(args)
    if args.emit == "lib" and not explicit_driver:
        args.driver = "clang"
        args.linker = "clang"


def _sanitize_list(value: str | None) -> list[str]:
    """Split a `--sanitize` list into names, rejecting empty or malformed entries."""
    if not value:
        return []
    names = [part.strip() for part in value.split(",")]
    if any(not name for name in names):
        raise IRError(f"--sanitize needs a comma separated list of sanitizer names, got {value!r}")
    seen: list[str] = []
    for name in names:
        if name not in seen:
            seen.append(name)
    return seen


def _require_sanitize(args: argparse.Namespace) -> list[str]:
    """Resolve `--sanitize` and refuse the combinations that cannot honor it."""
    names = _sanitize_list(getattr(args, "sanitize", None))
    if not names:
        return []
    if args.emit not in ("bin", "binding"):
        raise IRError(
            "--sanitize needs a linked binary or shared library, "
            f"so it works with --emit bin/binding only (got --emit {args.emit})"
        )
    if args.target is not None:
        raise IRError("--sanitize cannot cross-compile: the sanitizer runtime must match the host")
    return names


def _require_debug_info(args: argparse.Namespace) -> bool:
    """Resolve `--debug-info` and refuse the combinations that cannot honor it.

    The DWARF metadata lands in the emitted LLVM, so every emit mode can
    carry it. A cross-compiled binary would need the DWARF of the target
    toolchain, which the host `cc` does not produce, so that is refused.
    """
    if not getattr(args, "debug_info", False):
        return False
    if args.target is not None:
        raise IRError("--debug-info cannot cross-compile: the debug info must match the target toolchain")
    return True


def _library_dir(args: argparse.Namespace, source: Path) -> Path:
    """The directory a library build writes into.

    `-o DIR` names the directory; without it the build lands in
    `<stem>lib` beside the source (or under --out-dir), so it never
    collides with the `--emit bin` artifact of the same stem.
    """
    if args.output:
        return Path(args.output)
    base = (Path(args.out_dir) / source.stem) if args.out_dir else source.with_suffix("")
    return base.with_name(base.name + "lib")


def _require_mobile_refusals(args: argparse.Namespace) -> None:
    """Refuse the mobile-target shapes that have no meaning before any work runs.

    An executable build on a mobile triple is the main one: a phone app links
    a static library, not a standalone binary, so `--emit bin` (and `--run`,
    which implies it) points at `--emit lib` with the stable PS4101 code.
    The other direction refuses too: `--emit lib` on a desktop triple has no
    consumer story yet (a desktop app links a binary or a shared object, and
    the desktop cross matrix already has `--emit obj`), so the mode names the
    three triples it works for.
    """
    if args.emit == "lib":
        require_mobile_target(args.target, "--emit lib")
    if not is_mobile_target(args.target):
        return
    if args.emit == "bin" or args.run:
        raise MobileRefusal(
            f"{args.target} has no executable build: a mobile app links a static library. Use --emit lib."
        )


def _require_toolchain(args: argparse.Namespace) -> None:
    """Fail before any work when the requested output needs a C driver that is not on PATH."""
    _load_compiler()
    if not (args.run or args.emit in ("asm", "obj", "bin", "lib", "binding")):
        return
    if args.emit == "lib":
        # The library mode resolves its own toolchain (the Xcode SDK clang or
        # the NDK clang), and the resolution errors name what is missing.
        return
    driver = args.driver
    if driver is None:
        raise IRError(
            "no C toolchain found on PATH (neither zig nor clang), and compiling to a binary needs a C toolchain. "
            f"Install one of: {zigshim.install_hint()}, or clang (xcode-select --install, apt install clang, ...). "
            "Run `pyscriptc doctor` to see what was found and the README for install commands. "
            "`--emit ir` and `--emit llvm` need no toolchain."
        )
    if driver == "zig":
        # `pip install ziglang` alone satisfies this: the wheel ships the zig
        # binary but only exposes `python-zig`, so pyscriptc installs a `zig`
        # PATH shim pointing at `python -m ziglang`. A stale shim (its venv
        # is gone) is not trusted and gets rebuilt or rejected here.
        zigshim.ensure_on_path()
    if shutil.which(driver) is None:
        hint = (
            zigshim.install_hint()
            if driver == "zig"
            else ("install clang (xcode-select --install, apt install clang, ...) or pick zig with --driver zig")
        )
        raise IRError(
            f"{driver} not found on PATH, and compiling to a binary needs a C toolchain "
            f"(clang or zig; select with --driver or PYSC_CC). {hint} "
            f"Run `pyscriptc doctor` to see what was found. "
            f"`--emit ir` and `--emit llvm` need no toolchain."
        )


def _load_ffi(args: argparse.Namespace, config: Config) -> list[FfiManifest]:
    """The `--ffi` manifests of this compile, CLI first then the config file.

    The manifests are validated before lowering starts, so a bad pin or a
    duplicate module claim fails with its `PS4xxx` code and exit 1 (the
    `SpikeError` handler) instead of a link error later. A manifest that
    declares a `[[source]]` unit that is not on disk is refused here too:
    the compiler must not start a build that cannot finish.
    """
    paths = [Path(item) for item in getattr(args, "ffi", None) or []]
    paths.extend(config.ffi_manifests)
    if not paths:
        return []
    manifests = load_manifests(paths)
    for manifest in manifests:
        _require_manifest_units(manifest)
    return manifests


def _require_manifest_units(manifest: FfiManifest) -> None:
    """Refuse a manifest whose declared unit is missing, before the build starts.

    The linker checks the same paths, but the manifest is what names the
    file, so the error points at the manifest and not at a cc invocation.
    """
    for unit in manifest.unit_paths():
        if not unit.exists():
            raise FfiManifestError(
                "PS4009",
                f"the manifest declares the unit {unit}, which does not exist",
                path=manifest.path,
            )


def _manifest_stub_roots(manifests: list[FfiManifest]) -> list[Path]:
    """The directories the checker searches for a manifest module's stub.

    An adapter ships its surface as a `.pyi` beside the manifest (the
    sqlite adapter's `pyscriptc_sqlite.pyi`). The checker has to find it,
    or the import is a false `missing-import` (issue #66). The root is the
    directory that holds the stub file, not the manifest's own directory:
    a manifest may sit in the adapter's root while the stub sits in a
    subdirectory.
    """
    roots: list[Path] = []
    for manifest in manifests:
        # Absolute: the checker runs with its own working directory, so a
        # relative root would resolve against the wrong place.
        base = manifest.path.parent.resolve()
        roots.append(base)
        module = manifest.module.replace(".", "/")
        for candidate in (base / f"{module}.pyi", base / module / "__init__.pyi"):
            if candidate.is_file():
                roots.append(candidate.parent)
                break
    return roots


def _compile_cmd(args: argparse.Namespace, state: dict[str, Any]) -> int:
    try:
        dbg = resolve(args.debug, args.debug_dir, getattr(args, "timings", False))
    except ValueError as exc:
        raise IRError(str(exc)) from exc
    if dbg is None:
        dbg = Debug(set(), None)
    source_path = Path(args.source)
    if args.print_what == "diagnostics":
        report = build_report(source_path)
        print(json.dumps(report, indent=2))
        return 3 if report["fences"] else 0
    # The mobile refusal is a property of the request, not of the host: it
    # fires before the toolchain check, so a mobile triple reports PS4101
    # even on a machine without zig or clang.
    _require_mobile_refusals(args)
    _require_toolchain(args)
    sanitize = _require_sanitize(args)
    debug_info = _require_debug_info(args)
    config = load_config(source_path)
    for note in apply_to_args(config, args):
        print(note, file=sys.stderr)
    manifests = _load_ffi(args, config)
    log.info(
        "compile.start",
        source=str(source_path),
        emit=args.emit,
        run=args.run,
        driver=args.driver,
        target=args.target,
        checker=None if args.skip_check else args.checker,
    )
    module: dict[str, Any] | None = None
    llvm_text: str | None = None
    outputs: dict[str, str] = {}
    status = "ok"
    try:
        bridge = None
        if getattr(args, "bridge", None):
            from pyscriptc.factsbridge import CheckerBridge

            dbg.begin("bridge")
            bridge = CheckerBridge.for_path(source_path, args.bridge)
            dbg.end("bridge")
        dbg.begin("lower")
        # boxed:begin
        island_names = set(config.island_imports())
        fallback = (
            getattr(args, "fallback", None) == "cpython"
            or bool(getattr(args, "island", False))
            or config.fallback_definitions()
        )
        island_default = fallback or config.wildcard_island()
        island_ctx = island_policy(island_names, island_default)
        # boxed:end
        dep_roots, checker_roots, dep_lanes = _dependency_roots(source_path, config, island_default=island_default)
        binary = Path(args.output) if args.output else _artifact_base(args, source_path)
        if ((args.target and "windows" in args.target) or sys.platform == "win32") and not binary.suffix:
            binary = binary.with_suffix(".exe")
        data_root = _stage_dependency_data(source_path, config, args, binary, island_default=island_default)
        # boxed:begin
        from pyscriptc import package as _package

        staged_data: list[tuple[Path, bytes]] = []
        if source_path.is_dir() and (fallback or args.program):
            # Issue #665: a program-island compile stages the program
            # directory's non-Python files beside the binary (under the same
            # relative paths), so a relocated run resolves sibling data files
            # against the copy instead of the compile-time tree. Non-directory
            # sources keep today's behavior (divergence 46).
            staged_data = _collect_program_data(source_path)
        _package._STAGED_DATA_TARGET = (
            binary.parent / "pyscriptc-data" / source_path.resolve().name if staged_data and fallback else None
        )
        # boxed:end
        try:
            # boxed:begin
            with island_ctx, boxed_definitions(fallback):
                # boxed:end
                if source_path.is_dir():
                    module, source = lower_directory(
                        source_path,
                        program_mode=args.program,
                        bridge=bridge,
                        ffi=manifests,
                        target=args.target,
                        dep_roots=dep_roots,
                        dep_lane_report=dep_lanes,
                        resource_root=str(data_root) if data_root else "",
                        config=config,
                        staged_data=staged_data,
                    )
                elif source_path.is_file():
                    module, source = lower_file(
                        str(source_path),
                        bridge=bridge,
                        timings=dbg.timings_map,
                        ffi=manifests,
                        target=args.target,
                        dep_roots=dep_roots,
                        dep_lane_report=dep_lanes,
                        resource_root=str(data_root) if data_root else "",
                        config=config,
                    )
                else:
                    raise IRError(f"source not found: {source_path}")
        # boxed:begin
        finally:
            _package._STAGED_DATA_TARGET = None
        # boxed:end
        dbg.end("lower")
        dbg.exclude("lower", "parse")
        if args.run:
            args.emit = "bin"
        for note in module.pop("_notes", []):
            print(note, file=sys.stderr)
        # boxed:begin
        fallback_map = module.get("fallback")
        from pyscriptc import boxed as _boxed

        if fallback_map is None:
            all_names = [record["name"] for record in module.get("functions", [])]
            fallback_map = _boxed.plane_map(
                [],
                [{"name": n} for n in dict.fromkeys(all_names)],
                module.get("entry"),
            )
        # boxed:end
        state["stage"] = "check"
        if not args.skip_check:
            dbg.begin("check")
            run_checker(
                source_path,
                args.checker,
                [*_manifest_stub_roots(manifests), *checker_roots],
                # boxed:begin
                plane_map=fallback_map,
                fallback=fallback,
                # boxed:end
            )
            dbg.end("check")
        state["stage"] = "validate"
        dbg.begin("validate")
        validate_module(module)
        dbg.end("validate")
        if dbg.wants("verify"):
            state["stage"] = "verify"
            dbg.begin("verify")
            _verify_ir(module, dbg)
            dbg.end("verify")
        ir_path = _output_path(args, source_path, ".ir.json")
        ir_path.parent.mkdir(parents=True, exist_ok=True)
        ir_path.write_text(json.dumps(module, indent=2) + "\n", encoding="utf-8")
        outputs["ir"] = str(ir_path)
        if module["fences"]:
            state["fence_count"] = len(module["fences"])
            state["fence_families"] = _fence_families(module["fences"])
            state["fence_codes"] = _histogram(module["fences"], "code")
            log.info(
                "check.fences",
                count=len(module["fences"]),
                codes=[fence["code"] for fence in module["fences"]],
            )
            print(render_fences(module, source), file=sys.stderr)
            status = "fences"
            state["outcome"] = "fences"
            return 3
        log.info("emit.ir", output=str(ir_path))
        if args.emit == "ir":
            print(ir_path)
            return 0
        # boxed:begin
        requires_lp = bool(
            module.get("islandSource")
            or (fallback_map and any(p == _boxed.ISLAND for p in fallback_map.get("functions", {}).values()))
            or (fallback_map and any(p == _boxed.ISLAND for p in fallback_map.get("classes", {}).values()))
            or (fallback_map and fallback_map.get("moduleTopLevel") == _boxed.ISLAND)
            or config.island_imports()
        )
        report_data = {
            "target": str(source_path),
            "planeMap": fallback_map,
            "fallback": fallback_map,
            "requiresLibpython": requires_lp,
            "islandImports": list(config.island_imports()),
        }
        report_path = _output_path(args, source_path, ".report.json")
        report_path.write_text(json.dumps(report_data, indent=2) + "\n", encoding="utf-8")
        # boxed:end
        call_args = _parse_args(args.args) if args.call else None
        if args.run and not args.call and standalone_entry(module) is None:
            print(
                'note: nothing to run: the file has no module-level script and no `if __name__ == "__main__":` '
                "body, so the binary exits 0 without output. Use --call FUNC [--args '[...]'] to run a function.",
                file=sys.stderr,
            )
        state["stage"] = "emit"
        dbg.begin("emit_llvm")
        llvm_text = emit_llvm(
            module,
            args.call,
            call_args,
            standalone=args.emit == "bin",
            debug_info=debug_info,
            library=args.emit == "lib",
            binding=args.emit == "binding",
            wasm=bool(args.target and args.target.startswith("wasm32")),
        )
        dbg.end("emit_llvm")
        # The wasm thread refusal fires before the link, so a program that
        # cannot run threads on the target never reaches the compiler.
        refuse_unsupported_target(llvm_text, args.target)
        if args.emit == "lib":
            # The library bundle carries its own .ll: the archive's module
            # member is <stem>.o, so the stem must not be the -o directory.
            ll_path = _library_dir(args, source_path) / f"{source_path.stem}.ll"
        else:
            ll_path = _output_path(args, source_path, ".ll")
        ll_path.parent.mkdir(parents=True, exist_ok=True)
        ll_path.write_text(llvm_text, encoding="utf-8")
        outputs["ll"] = str(ll_path)
        log.info("emit.llvm", output=str(ll_path))
        if dbg.wants("verify"):
            state["stage"] = "llvmverify"
            dbg.begin("llvmverify")
            _verify_llvm(ll_path, dbg, driver=args.driver or "clang", target=args.target)
            dbg.end("llvmverify")
        if args.emit == "llvm":
            print(ll_path)
            return 0
        if args.emit == "lib":
            state["stage"] = "archive"
            dbg.begin("archive")
            mobile = require_mobile_target(args.target, "--emit lib")
            artifacts = build_library(
                ll_path,
                _library_dir(args, source_path),
                target=mobile,
                driver=args.driver or "clang",
                sanitize=sanitize,
                record=dbg.commands,
                timings=dbg.timings_map,
                debug_info=debug_info,
            )
            dbg.end("archive")
            outputs["lib"] = str(artifacts["archive"])
            outputs["header"] = str(artifacts["header"])
            outputs["modulemap"] = str(artifacts["modulemap"])
            log.info("archive.library", output=str(artifacts["archive"]), target=args.target)
            print(artifacts["archive"])
            if mobile.family == "android":
                # The archive has no program headers; the 16 KB alignment is
                # the consumer's link-time decision, so say it where the
                # build finishes.
                print(
                    "note: link the archive into a shared object with "
                    + " ".join(ANDROID_PAGE_FLAGS)
                    + " (Google Play 16 KB page rule)"
                )
            return 0
        if args.emit == "binding":
            from pyscriptc import binding

            state["stage"] = "binding"
            dbg.begin("binding")
            base = Path(args.output) if args.output else _artifact_base(args, source_path)
            stem = base.stem.removesuffix("_binding")
            shlib_name = binding.shared_library_name(stem, target=args.target)
            shlib_path = base.parent / shlib_name
            binding_path = base.parent / f"{stem}_binding.py"

            # The exported calls need an exception guard at the C ABI edge: a
            # raise unwinds with longjmp, and a ctypes caller leaves no handler
            # on the far side, so an unguarded call would report and exit the
            # host process. The shim is a C unit of the same link, generated
            # from the same function list the loader module is built from.
            emitted, skipped = binding.analyze_binding(module)
            dbg.set_binding(emitted, skipped)
            shim_path = base.parent / f"{stem}_binding_shim.c"
            shim_path.parent.mkdir(parents=True, exist_ok=True)
            shim_path.write_text(binding.render_binding_shim(stem, emitted), encoding="utf-8")

            link_binary(
                ll_path,
                shlib_path,
                driver=args.driver or "clang",
                target=args.target,
                record=dbg.commands,
                link_driver=args.linker,
                sanitize=sanitize,
                timings=dbg.timings_map,
                debug_info=debug_info,
                compile_units=[
                    *(unit for manifest in manifests for unit in manifest.compile_units()),
                    shim_path,
                ],
                link_units=[unit for manifest in manifests for unit in manifest.link_units()],
                link_flags=[flag for manifest in manifests for flag in manifest.link_flags()],
                include_dirs=[manifest.path.parent for manifest in manifests if manifest.sources],
                shared=True,
            )

            binding_code = binding.render_binding_module(stem, shlib_name, emitted, skipped)
            binding_path.parent.mkdir(parents=True, exist_ok=True)
            binding_path.write_text(binding_code, encoding="utf-8")

            outputs["shared"] = str(shlib_path)
            outputs["binding"] = str(binding_path)
            log.info(
                "emit.binding",
                output=str(binding_path),
                shared=str(shlib_path),
                emitted=len(emitted),
                skipped=len(skipped),
            )
            dbg.end("binding")
            print(binding_path)
            return 0
        if args.emit in ("asm", "obj"):
            suffix = ".s" if args.emit == "asm" else ".o"
            unit_path = _output_path(args, source_path, suffix)
            state["stage"] = "link"
            compile_unit(
                ll_path,
                unit_path,
                args.emit,
                driver=args.driver,
                target=args.target,
                record=dbg.commands,
                timings=dbg.timings_map,
                debug_info=debug_info,
            )
            outputs[args.emit] = str(unit_path)
            print(unit_path)
            return 0
        state["stage"] = "link"
        dbg.begin("link")
        link_binary(
            ll_path,
            binary,
            driver=args.driver or "clang",
            target=args.target,
            record=dbg.commands,
            link_driver=args.linker,
            sanitize=sanitize,
            timings=dbg.timings_map,
            debug_info=debug_info,
            compile_units=[unit for manifest in manifests for unit in manifest.compile_units()],
            link_units=[unit for manifest in manifests for unit in manifest.link_units()],
            link_flags=[flag for manifest in manifests for flag in manifest.link_flags()],
            include_dirs=[manifest.path.parent for manifest in manifests if manifest.sources],
            embed_mode=getattr(args, "embed_python", None),
        )
        dbg.end("link")
        outputs["bin"] = str(binary)
        # boxed:begin
        # A bundle link writes a relocatable directory: the binary under
        # `bin/`, the runtime under `lib/` (the relative rpath points there).
        # The stdlib travels as a safe-trimmed zip of .pyc bodies (issue #659),
        # and the compile first manifest-checks every stdlib import of the
        # program against the zip the trim would produce, so a trimmed module
        # fails the build (PS4301), never the run.
        resolved_embed = getattr(args, "embed_python", None)
        if resolved_embed != "host" and embed.resolve_mode(resolved_embed) == "bundle":
            resolved_embed = "bundle"
        # The direct third-party import roots of the program, for the bundle's
        # site-packages tree (issue #660). A native-backed distribution among
        # them refuses the compile (PS4302) before anything is staged.
        dep_roots = sitedeps.third_party_roots(source_path) if resolved_embed == "bundle" else []
        if resolved_embed == "bundle":
            sitedeps.check_pure(dep_roots)
            pack = embed.lock_pack()
            pack_directory = embed.ensure_pack(pack)
            embed_version = embed.python_version()
            trim = getattr(args, "embed_stdlib", None) or stdlibzip.DEFAULT_MODE
            carried = stdlibzip.kept_module_names(
                stdlibzip.stdlib_sources(pack_directory / "lib" / f"python{embed_version}", trim)
            )
            stdlibzip.manifest_check(carried, _stdlib_imports(source_path), trim)
        if resolved_embed == "bundle":
            pack = embed.lock_pack()
            staged = embed.stage_bundle(
                binary,
                embed.pack_dir(pack),
                embed.python_version(),
                binary,
                stdlib_mode=getattr(args, "embed_stdlib", None) or stdlibzip.DEFAULT_MODE,
                dependency_roots=dep_roots,
            )
            outputs["bin"] = str(staged)
            binary = staged
        # boxed:end
        state["binary_bytes"] = binary.stat().st_size if binary.is_file() else None
        log.info("link.binary", output=str(binary), driver=args.driver, target=args.target)
        if not args.run:
            print(binary)
            return 0
        state["stage"] = "run"
        log.info("run.start", binary=str(binary))
        dbg.begin("run")
        run_env = _run_environment(source_path)
        completed = subprocess.run([str(binary.absolute()), *args.run_argv], env=run_env)
        dbg.end("run")
        code = 128 + (-completed.returncode) if completed.returncode < 0 else completed.returncode
        log.info("run.exit", binary=str(binary), code=code)
        return code
    except Exception as exc:
        status = "error"
        state["outcome"] = _compile_outcome(exc, state["stage"])
        raise
    finally:
        report = dbg.render(module, outputs)
        if report:
            print(report, file=sys.stderr)
        dbg.write_dir(module, llvm_text, outputs, status)


def _stdlib_imports(source_path: Path) -> list[str]:
    """The absolute stdlib import names of the program, for the bundle manifest check.

    Relative imports stay inside the program tree and third-party imports go
    through the site-packages bundle (issue #660), so only stdlib roots reach
    the check. `sys.stdlib_module_names` of the running interpreter answers
    it: the compiler and the pack share the 3.14 stdlib.
    """

    from pyscriptc.imports import collect_imports

    names: set[str] = set()
    for site in collect_imports(source_path):
        if site["level"] > 0 or not site["name"]:
            continue
        if site["name"].split(".")[0] in sys.stdlib_module_names:
            names.add(site["name"])
    return sorted(names)


def _dependency_roots(
    source_path: Path, config: Config, island_default: bool = False
) -> tuple[list[Path], list[Path], dict[str, dict[str, str]]]:
    """The dependency source roots the compile may import (issue #398).

    The deps report's `sourceRoots`, one directory per pure-Python
    dependency, become extra module roots of the compile. The `[imports]`
    lane table decides per import name: `compile` takes the dependency from
    the deps cache, and `replace`, `extern`, `island` and `refuse` behave
    exactly as they do today (the dependency contributes no root, so the
    import fences or binds on its own lane).

    The second value is `checker_roots`: all unpacked source roots from the
    deps cache (including island packages), so the preflight checker can
    resolve their modules without requiring host environment installation.

    The third value is the lane report the compiler fences against: every
    locked dependency keyed by its recorded import names (issue #408; the
    distribution-shaped module name when the wheel records none), with its
    lane and the lane hint, so a refused import names the distribution and
    what would unblock it.

    A source with no declared dependencies has no roots and no resolver
    call, so a compile that declares nothing never touches the network.
    """

    if not config.dependencies:
        return [], [], {}
    report = deps.run_deps(source_path)
    roots: list[Path] = []
    checker_roots: list[Path] = []
    lanes: dict[str, dict[str, str]] = {}
    for package in report["packages"]:
        distribution = package["name"]
        module = distribution.lower().replace("-", "_").replace(".", "_")
        # The lane is the deps report's (pure, native, sdist from the lock
        # and the wheel tags); the `[imports]` table can only *keep* a
        # dependency off the compile lane, not put a native one on it.
        lane = package["lane"]
        hint = package.get("hint", "")
        policy = _dependency_lane(distribution, config, island_default=island_default)
        if policy != "compile":
            lane = policy
            hint = deps.POLICY_HINTS.get(policy, hint)
        entry = {
            "distribution": distribution,
            "lane": lane,
            "hint": hint,
        }
        root = package.get("source_root")
        if root:
            checker_roots.append(Path(root))
        names = tuple(package.get("import_names") or ())
        if not names:
            entry["importNames"] = deps.UNKNOWN_IMPORT_NAMES
            lanes.setdefault(module, entry)
            if root and lane == "pure":
                roots.append(Path(root))
            continue
        if len(names) > 1:
            entry["provides"] = ", ".join(sorted(names))
        for import_name in names:
            lanes.setdefault(import_name.lower(), entry)
        if root and lane == "pure":
            roots.append(Path(root))
    return roots, checker_roots, lanes


# boxed:begin
def _collect_program_data(source_path: Path) -> list[tuple[Path, bytes]]:
    """The program directory's non-Python files, program-relative (issue #665).

    A program-island compile carries the code in the binary, so a relocated
    run has no source tree: only the sibling data files the program opens
    (`Path(__file__).parent / "data.json"` and friends) need a home beside
    the binary. Everything is read at compile time, so the staged copy is
    the exact bytes the compile saw. `__pycache__` and dot directories are
    skipped, as the module discovery skips them.
    """

    root = source_path.resolve()
    files: list[tuple[Path, bytes]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
            continue
        if path.is_dir() or path.suffix == ".py":
            continue
        try:
            files.append((relative, path.read_bytes()))
        except OSError:
            continue
    return files


# boxed:end


def _stage_dependency_data(
    source_path: Path, config: Config, args: argparse.Namespace, binary: Path, island_default: bool = False
) -> Path | None:
    """Stage the pure wheels' data files beside the emitted binary (issue #410).

    Only the compile lanes (`bin` and the `--run` that implies it) need the
    staged tree: the binary's `importlib.resources` searches the compile-time
    path, and the staged root sits next to the binary under the package's
    dotted path. Every other emit (`ir`, `llvm`, `asm`, `obj`, `lib`) lowers
    nothing that reads files beside the artifact, so staging would only
    leave an unused directory; the search-path constant is empty there and
    the behavior stays exactly as before.
    """
    _load_compiler()

    from pyscriptc.deps import data_files, stage_data_files

    if not config.dependencies:
        return None
    if getattr(args, "emit", "") not in ("bin", "llvm") and not getattr(args, "run", False):
        return None
    report = deps.run_deps(source_path)
    collected: list[tuple[str, list[tuple[str, bytes]]]] = []
    for package in report["packages"]:
        policy = _dependency_lane(package["name"], config, island_default=island_default)
        if policy != "compile" or package["lane"] != "pure":
            continue
        root = package.get("source_root")
        wheel = package.get("wheel")
        sha = package.get("sha256")
        if not root or not wheel or not sha:
            continue
        blob = deps.cache_root() / "blobs" / "sha256" / sha[:2] / f"{sha}.whl"
        if not blob.is_file():
            continue
        files = data_files(blob.read_bytes())
        if files:
            collected.append((package["name"], files))
    return stage_data_files(collected, binary)


def _dependency_lane(distribution: str, config: Config, island_default: bool = False) -> str:
    """The `[imports]` policy lane of one distribution, `compile` by default."""
    from pyscriptc.config import requirement_name

    name = requirement_name(distribution)
    module = name.lower().replace("-", "_").replace(".", "_")
    for key in (name, name.lower(), module, distribution):
        if key in config.imports:
            policy = config.imports[key]
            # boxed:begin
            if policy == "compile" and island_default:
                return "island"
            # boxed:end
            return policy
    # boxed:begin
    if island_default:
        return "island"
    # boxed:end
    star = config.imports.get("*")
    if star not in (None, "compile"):
        return star
    return "compile"


def _compile_outcome(exc: BaseException, stage: str) -> str:
    """Map a compile failure onto the telemetry outcome vocabulary."""
    if isinstance(exc, CheckFailed):
        return "check-failed"
    if stage == "link":
        return "build-error"
    if isinstance(exc, SpikeError) or stage in ("lower", "validate", "emit"):
        return "lower-error"
    return "build-error"


def _verify_ir(module: dict[str, Any], dbg: Debug) -> None:
    """Run the structural IR verifier and record (or raise) its verdict.

    Debug only: a violation means lowering produced an IR the emitter would
    misread, so fail the compile with the verifier's own diagnostic instead
    of letting the drift surface as a wrong binary or a cc error.
    """

    errors = verify_module(module)
    dbg.ir_verifications.extend([{"code": error.code, "message": error.message} for error in errors])
    if errors:
        raise errors[0]


def _verify_llvm(ll_path: Path, dbg: Debug, driver: str, target: str | None) -> None:
    """Verify the emitted LLVM module and record (or raise) its verdict."""

    result = verify_module_text(ll_path, driver=driver, target=target)
    dbg.llvm_verification = result.as_dict()
    if not result.ok and not result.skipped:
        raise LLVMVerifyError(f"the emitted LLVM module did not verify: {result.message}")


def _histogram(fences: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for fence in fences:
        counts[fence.get(key, "")] = counts.get(fence.get(key, ""), 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _fence_families(fences: list[dict[str, Any]]) -> dict[str, int]:
    """Count fences per diagnostic family; `stdlib:<module>` collapses to `stdlib`."""
    counts: dict[str, int] = {}
    for fence in fences:
        family = family_of(fence.get("code", ""), fence.get("message", ""))
        if family.startswith("stdlib:"):
            family = "stdlib"
        counts[family] = counts.get(family, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _parse_args(text: str) -> list[Any]:
    try:
        value = ast.literal_eval(text)
    except (SyntaxError, ValueError) as exc:
        raise IRError(f"--args is not a Python literal: {exc}") from exc
    if not isinstance(value, list):
        raise IRError("--args must be a list literal")
    return list(value)


def _artifact_base(args: argparse.Namespace, source: Path) -> Path:
    """Extensionless artifact path: beside the source, or under --out-dir."""
    if args.out_dir:
        return Path(args.out_dir) / source.stem
    return source.with_suffix("")


def _output_path(args: argparse.Namespace, source: Path, suffix: str) -> Path:
    if args.output and args.emit == "ir" and suffix == ".ir.json":
        return Path(args.output)
    if args.output and args.emit == "llvm" and suffix == ".ll":
        return Path(args.output)
    if args.output and args.emit == "asm" and suffix == ".s":
        return Path(args.output)
    if args.output and args.emit == "obj" and suffix == ".o":
        return Path(args.output)
    if args.output and args.emit == "binding":
        base = Path(args.output)
        stem = base.stem.removesuffix("_binding")
        return base.parent / f"{stem}{suffix}"
    if args.output:
        return Path(str(args.output) + suffix)
    if args.out_dir:
        return Path(args.out_dir) / f"{source.stem}{suffix}"
    return source.with_suffix(suffix)
