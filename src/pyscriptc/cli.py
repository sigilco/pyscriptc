"""Command line for the spike compiler."""

from __future__ import annotations

import argparse
import ast
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from pyscriptc import deps, runcache, telemetry
from pyscriptc.check import CheckFailed, run_checker
from pyscriptc.config import apply_to_args, load_config
from pyscriptc.coverage import build_report, family_of, render_text
from pyscriptc.debug import Debug, resolve
from pyscriptc.diagnostics import explain
from pyscriptc.doctor import doctor_cmd
from pyscriptc.errors import FitError, IRError, SpikeError, VersionError
from pyscriptc.imports import build_report as build_import_report
from pyscriptc.imports import render_text as render_import_text
from pyscriptc.ir import validate_module
from pyscriptc.llvm import compile_unit, emit_llvm, link_binary, standalone_entry
from pyscriptc.log import enable_debug, get_logger, tail
from pyscriptc.lower import lower_file, render_fences
from pyscriptc.package import lower_directory

log = get_logger()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pyscriptc",
        description="Lower a typed Python subset to IR and LLVM.",
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-v", "--verbose", action="store_true", help="enable DEBUG audit records")
    sub = parser.add_subparsers(dest="cmd", required=True)
    compile_parser = sub.add_parser("compile", help="lower a source file", parents=[common])
    compile_parser.add_argument("source")
    compile_parser.add_argument(
        "--program",
        action="store_true",
        help="treat the directory as a program (`python -m pkg` parity): `__main__.py` is the "
        "entry point even when the directory has an `__init__.py`, and its modules are "
        "namespaced under the directory name",
    )
    compile_parser.add_argument("--emit", choices=("ir", "llvm", "asm", "obj", "bin"), default="llvm")
    compile_parser.add_argument(
        "--out-dir",
        metavar="DIR",
        help="write artifacts to DIR/<source name> instead of beside the source (useful for directory compiles)",
    )
    compile_parser.add_argument("-o", dest="output")
    compile_parser.add_argument("--call", help="function the generated main calls")
    compile_parser.add_argument(
        "--args",
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
        "--print",
        dest="print_what",
        choices=("diagnostics",),
        help="print JSON instead of compiling: diagnostics is the coverage report; exit 3 when fenced",
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
        choices=("mypy",),
        default=None,
        help="checker bridge: feed the checker's inferred-type map into lowering so "
        "unannotated locals, parameters and returns stop fencing. Off by default; "
        "needs mypy installed",
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
    run_parser.add_argument("source")
    run_parser.add_argument("--program", action="store_true", help="treat the directory as a program (see compile)")
    run_parser.add_argument("--call", help="function the generated main calls")
    run_parser.add_argument("--args", default="[]", help="Python literal list of arguments for --call")
    run_parser.add_argument("--driver", choices=("clang", "zig"), default=None)
    run_parser.add_argument("--target", help="cross-compilation triple for `zig cc`")
    run_parser.add_argument("--linker", choices=("clang", "zig"), default=None)
    run_parser.add_argument(
        "--sanitize",
        metavar="LIST",
        default=None,
        help="comma list of sanitizers for the C runtime and the emitted code (env PYSC_SANITIZE)",
    )
    run_parser.add_argument("--skip-check", action="store_true")
    run_parser.add_argument("--checker", choices=("pyrefly", "mypy", "pyright"), default="pyrefly")
    run_parser.add_argument("--no-cache", action="store_true", help="always recompile (env PYSC_NO_RUN_CACHE=1)")
    coverage_parser = sub.add_parser("coverage", help="report every fence in a file or package tree", parents=[common])
    coverage_parser.add_argument("source")
    coverage_parser.add_argument("--json", action="store_true", help="print the report as JSON")
    coverage_parser.add_argument(
        "--fail-on",
        choices=("blockers", "any"),
        help="exit 1 when root fences (blockers) or any fence exist; default always exits 0",
    )
    config_parser = sub.add_parser("config", help="show the resolved config for a source", parents=[common])
    config_parser.add_argument("source")
    config_parser.add_argument("--json", action="store_true", help="print the resolved config as JSON")
    explain_parser = sub.add_parser("explain", help="describe a diagnostic code", parents=[common])
    explain_parser.add_argument("code")
    imports_parser = sub.add_parser(
        "imports", help="classify every import of a file or package tree into a compat lane", parents=[common]
    )
    imports_parser.add_argument("source")
    imports_parser.add_argument("--json", action="store_true", help="print the report as JSON")
    sub.add_parser("logs", help="print the audit-log path and its last lines", parents=[common])
    telemetry_parser = sub.add_parser("telemetry", help="usage metrics opt-out")
    telemetry_parser.add_argument("action", choices=("disable", "enable", "status", "reset-id"))
    doctor_parser = sub.add_parser("doctor", help="report toolchain, cache and install shape", parents=[common])
    doctor_parser.add_argument("--json", action="store_true", help="print the report as JSON")
    deps.register(sub, common)
    try:
        raw = list(sys.argv[1:] if argv is None else argv)
        run_argv: list[str] = []
        if "--" in raw:
            split = raw.index("--")
            raw, run_argv = raw[:split], raw[split + 1 :]
        args = parser.parse_args(raw)
        args.run_argv = run_argv
        if run_argv and not (args.cmd == "run" or (args.cmd == "compile" and args.run)):
            parser.error("arguments after `--` need `run` or `compile --run`")
        if getattr(args, "verbose", False):
            enable_debug()
        log.info("cli.invoke", cmd=args.cmd)
        if args.cmd != "telemetry":
            telemetry.maybe_print_notice()
            telemetry.track("cli.invoked", {"subcommand": args.cmd, **telemetry.base_properties()})
        if args.cmd == "compile":
            return compile_cmd(args)
        if args.cmd == "run":
            return run_cmd(args)
        if args.cmd == "coverage":
            return coverage_cmd(args)
        if args.cmd == "imports":
            return imports_cmd(args)
        if args.cmd == "config":
            return config_cmd(args)
        if args.cmd == "explain":
            return explain_cmd(args)
        if args.cmd == "logs":
            return logs_cmd(args)
        if args.cmd == "doctor":
            return doctor_cmd(args.json)
        if args.cmd == "deps":
            return deps.deps_cmd(args)
        if args.cmd == "telemetry":
            return telemetry.telemetry_cmd(args.action)
    except CheckFailed as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (VersionError, FitError) as exc:
        print(str(exc), file=sys.stderr)
        return 4
    except SpikeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 2


def run_cmd(args: argparse.Namespace) -> int:
    """`pyscriptc run FILE [-- args]`: compile once per content hash, then run the cached binary."""
    _apply_env_defaults(args)
    source = Path(args.source)
    if not source.exists():
        raise IRError(f"source not found: {source}")
    use_cache = runcache.enabled() and not args.no_cache
    key = runcache.key_for(
        source,
        load_config(source).origin,
        [
            f"program={args.program}",
            f"call={args.call}",
            f"args={args.args if args.call else ''}",
            f"driver={args.driver}",
            f"target={args.target}",
            f"linker={args.linker}",
            f"sanitize={args.sanitize}",
            f"skip_check={args.skip_check}",
            f"checker={args.checker}",
        ],
    )
    name = (source.resolve().name if source.is_dir() else source.stem) or "app"
    binary = runcache.binary_path(key, name)
    if use_cache and binary.is_file():
        log.info("run.cache_hit", key=key, binary=str(binary))
        return _exec_binary(binary, args.run_argv)
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
                return _exec_binary(binary, args.run_argv)
        return _exec_binary(build / name, args.run_argv)
    finally:
        shutil.rmtree(build, ignore_errors=True)


def _build_for_run(args: argparse.Namespace, output: Path) -> int:
    compile_args = argparse.Namespace(
        source=args.source,
        program=args.program,
        emit="bin",
        out_dir=None,
        output=str(output),
        call=args.call,
        args=args.args,
        run=False,
        driver=args.driver,
        target=args.target,
        linker=args.linker,
        sanitize=args.sanitize,
        print_what=None,
        skip_check=args.skip_check,
        checker=args.checker,
        debug=None,
        debug_dir=None,
        run_argv=[],
    )
    # compile prints the binary path on success; keep stdout clean for the program
    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        return compile_cmd(compile_args)
    finally:
        sys.stdout = real_stdout


def _exec_binary(binary: Path, argv: list[str]) -> int:
    completed = subprocess.run([str(binary.absolute()), *argv])
    code = 128 + (-completed.returncode) if completed.returncode < 0 else completed.returncode
    log.info("run.exit", binary=str(binary), code=code)
    return code


def coverage_cmd(args: argparse.Namespace) -> int:
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
    report = build_import_report(Path(args.source))
    log.info("imports.report", source=args.source)
    print(json.dumps(report, indent=2) if args.json else render_import_text(report))
    return 0


def config_cmd(args: argparse.Namespace) -> int:
    config = load_config(Path(args.source))
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
    return 0


def explain_cmd(args: argparse.Namespace) -> int:
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


def compile_cmd(args: argparse.Namespace) -> int:
    started = time.monotonic()
    _apply_env_defaults(args)
    state: dict[str, Any] = {
        "outcome": None,
        "stage": "lower",
        "fence_count": 0,
        "fence_families": {},
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
                checker=None if args.skip_check else args.checker,
                emit=args.emit,
                driver=args.driver,
                skip_check=bool(args.skip_check),
            ),
        )


def _resolve_default_driver(target: str | None = None) -> str | None:
    """Resolve the default C driver when not explicitly specified via --driver or PYSC_CC.

    Rules:
    - If target is set (or targets Windows) -> prefer zig.
    - If sys.platform == "win32" -> prefer zig.
    - If shutil.which("zig") is found on PATH -> prefer zig.
    - Otherwise, fall back to clang on macOS/Linux if found on PATH.
    - If neither is found, return None.
    """
    if target is not None or sys.platform == "win32":
        return "zig"
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
    if args.driver is None:
        args.driver = _resolve_default_driver(args.target)
    if getattr(args, "sanitize", None) is None:
        args.sanitize = os.environ.get("PYSC_SANITIZE") or None


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
    if args.emit != "bin":
        raise IRError(f"--sanitize needs a linked binary, so it works with --emit bin only (got --emit {args.emit})")
    if args.target is not None:
        raise IRError("--sanitize cannot cross-compile: the sanitizer runtime must match the host")
    return names


def _require_toolchain(args: argparse.Namespace) -> None:
    """Fail before any work when the requested output needs a C driver that is not on PATH."""
    if not (args.run or args.emit in ("asm", "obj", "bin")):
        return
    driver = args.driver
    if driver is None:
        raise IRError(
            "no C toolchain found on PATH (neither zig nor clang), and compiling to a binary needs a C toolchain. "
            "Install zig (`brew install zig`, `winget install zig`, or `pip install ziglang`) or clang. "
            "Run `pyscriptc doctor` to see what was found and the README for install commands. "
            "`--emit ir` and `--emit llvm` need no toolchain."
        )
    if shutil.which(driver) is None:
        hint = ""
        if driver == "zig":
            hint = " (install via `brew install zig`, `winget install zig`, or `pip install ziglang`)"
        raise IRError(
            f"{driver} not found on PATH, and compiling to a binary needs a C toolchain "
            f"(clang or zig; select with --driver or PYSC_CC){hint}. Run `pyscriptc doctor` to see what was found "
            f"and the README for install commands. `--emit ir` and `--emit llvm` need no toolchain."
        )


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
    _require_toolchain(args)
    sanitize = _require_sanitize(args)
    config = load_config(source_path)
    for note in apply_to_args(config, args):
        print(note, file=sys.stderr)
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
        if source_path.is_dir():
            module, source = lower_directory(source_path, program_mode=args.program, bridge=bridge)
        elif source_path.is_file():
            module, source = lower_file(str(source_path), bridge=bridge, timings=dbg.timings_map)
        else:
            raise IRError(f"source not found: {source_path}")
        dbg.end("lower")
        dbg.exclude("lower", "parse")
        if args.run:
            args.emit = "bin"
        for note in module.pop("_notes", []):
            print(note, file=sys.stderr)
        state["stage"] = "check"
        if not args.skip_check:
            dbg.begin("check")
            run_checker(source_path, args.checker)
            dbg.end("check")
        state["stage"] = "validate"
        dbg.begin("validate")
        validate_module(module)
        dbg.end("validate")
        ir_path = _output_path(args, source_path, ".ir.json")
        ir_path.parent.mkdir(parents=True, exist_ok=True)
        ir_path.write_text(json.dumps(module, indent=2) + "\n", encoding="utf-8")
        outputs["ir"] = str(ir_path)
        if module["fences"]:
            state["fence_count"] = len(module["fences"])
            state["fence_families"] = _fence_families(module["fences"])
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
        call_args = _parse_args(args.args) if args.call else None
        if args.run and not args.call and standalone_entry(module) is None:
            print(
                'note: nothing to run: the file has no module-level script and no `if __name__ == "__main__":` '
                "body, so the binary exits 0 without output. Use --call FUNC [--args '[...]'] to run a function.",
                file=sys.stderr,
            )
        state["stage"] = "emit"
        dbg.begin("emit_llvm")
        llvm_text = emit_llvm(module, args.call, call_args, standalone=args.emit == "bin")
        dbg.end("emit_llvm")
        ll_path = _output_path(args, source_path, ".ll")
        ll_path.write_text(llvm_text, encoding="utf-8")
        outputs["ll"] = str(ll_path)
        log.info("emit.llvm", output=str(ll_path))
        if args.emit == "llvm":
            print(ll_path)
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
            )
            outputs[args.emit] = str(unit_path)
            print(unit_path)
            return 0
        binary = Path(args.output) if args.output else _artifact_base(args, source_path)
        state["stage"] = "link"
        dbg.begin("link")
        link_binary(
            ll_path,
            binary,
            driver=args.driver,
            target=args.target,
            record=dbg.commands,
            link_driver=args.linker,
            sanitize=sanitize,
            timings=dbg.timings_map,
        )
        dbg.end("link")
        outputs["bin"] = str(binary)
        log.info("link.binary", output=str(binary), driver=args.driver, target=args.target)
        if not args.run:
            print(binary)
            return 0
        state["stage"] = "run"
        log.info("run.start", binary=str(binary))
        dbg.begin("run")
        completed = subprocess.run([str(binary.absolute()), *args.run_argv])
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


def _compile_outcome(exc: BaseException, stage: str) -> str:
    """Map a compile failure onto the telemetry outcome vocabulary."""
    if isinstance(exc, CheckFailed):
        return "check-failed"
    if stage == "link":
        return "build-error"
    if isinstance(exc, SpikeError) or stage in ("lower", "validate", "emit"):
        return "lower-error"
    return "build-error"


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
    if args.output:
        return Path(str(args.output) + suffix)
    if args.out_dir:
        return Path(args.out_dir) / f"{source.stem}{suffix}"
    return source.with_suffix(suffix)
