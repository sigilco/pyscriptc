"""Command line for the spike compiler."""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from pyscriptc import telemetry
from pyscriptc.check import CheckFailed, run_checker
from pyscriptc.coverage import build_report, family_of, render_text
from pyscriptc.debug import Debug, resolve
from pyscriptc.diagnostics import explain
from pyscriptc.errors import FitError, IRError, SpikeError, VersionError
from pyscriptc.ir import validate_module
from pyscriptc.llvm import emit_llvm, link_binary
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
    compile_parser.add_argument("--emit", choices=("ir", "llvm", "bin"), default="llvm")
    compile_parser.add_argument("-o", dest="output")
    compile_parser.add_argument("--call", help="function the generated main calls")
    compile_parser.add_argument(
        "--args",
        default="[]",
        help="Python literal list of arguments for --call, parsed with ast.literal_eval",
    )
    compile_parser.add_argument("--run", action="store_true", help="compile a binary and run it")
    compile_parser.add_argument(
        "--driver",
        choices=("clang", "zig"),
        default="clang",
        help="C toolchain driver; zig means `zig cc` (required for --target)",
    )
    compile_parser.add_argument(
        "--target",
        help="cross-compilation triple for `zig cc`, e.g. aarch64-linux-gnu.2.39",
    )
    compile_parser.add_argument("--skip-check", action="store_true")
    compile_parser.add_argument(
        "--checker",
        choices=("pyrefly", "mypy", "pyright"),
        default="pyrefly",
        help="type-checker preflight; mypy and pyright are deprecated",
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
    coverage_parser = sub.add_parser("coverage", help="report every fence in a file or package tree", parents=[common])
    coverage_parser.add_argument("source")
    coverage_parser.add_argument("--json", action="store_true", help="print the report as JSON")
    explain_parser = sub.add_parser("explain", help="describe a diagnostic code", parents=[common])
    explain_parser.add_argument("code")
    sub.add_parser("logs", help="print the audit-log path and its last lines", parents=[common])
    telemetry_parser = sub.add_parser("telemetry", help="anonymous usage metrics opt-out")
    telemetry_parser.add_argument("action", choices=("disable", "enable", "status"))
    try:
        args = parser.parse_args(argv)
        if getattr(args, "verbose", False):
            enable_debug()
        log.info("cli.invoke", cmd=args.cmd)
        telemetry.maybe_print_notice()
        telemetry.track("cli.invoked", {"subcommand": args.cmd, **telemetry.base_properties()})
        if args.cmd == "compile":
            return compile_cmd(args)
        if args.cmd == "coverage":
            return coverage_cmd(args)
        if args.cmd == "explain":
            return explain_cmd(args)
        if args.cmd == "logs":
            return logs_cmd(args)
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


def coverage_cmd(args: argparse.Namespace) -> int:
    report = build_report(Path(args.source))
    log.info("coverage.report", source=args.source)
    print(json.dumps(report, indent=2) if args.json else render_text(report))
    telemetry.track("coverage.reported", telemetry.coverage_properties(report["summary"]))
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
    state: dict[str, Any] = {
        "outcome": None,
        "stage": "lower",
        "fence_count": 0,
        "fence_families": {},
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
                checker=None if args.skip_check else args.checker,
                emit=args.emit,
                driver=args.driver,
                binary_bytes=state["binary_bytes"],
                skip_check=bool(args.skip_check),
            ),
        )


def _compile_cmd(args: argparse.Namespace, state: dict[str, Any]) -> int:
    try:
        dbg = resolve(args.debug, args.debug_dir)
    except ValueError as exc:
        raise IRError(str(exc)) from exc
    if dbg is None:
        dbg = Debug(set(), None)
    source_path = Path(args.source)
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
        dbg.begin("lower")
        if source_path.is_dir():
            module, source = lower_directory(source_path)
        elif source_path.is_file():
            module, source = lower_file(str(source_path))
        else:
            raise IRError(f"source not found: {source_path}")
        dbg.end("lower")
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
        binary = Path(args.output) if args.output else source_path.with_suffix("")
        state["stage"] = "link"
        dbg.begin("link")
        link_binary(ll_path, binary, driver=args.driver, target=args.target, record=dbg.commands)
        dbg.end("link")
        outputs["bin"] = str(binary)
        log.info("link.binary", output=str(binary), driver=args.driver, target=args.target)
        try:
            state["binary_bytes"] = binary.stat().st_size
        except OSError:
            pass
        if not args.run:
            print(binary)
            return 0
        state["stage"] = "run"
        log.info("run.start", binary=str(binary))
        dbg.begin("run")
        completed = subprocess.run([str(binary.absolute())])
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


def _output_path(args: argparse.Namespace, source: Path, suffix: str) -> Path:
    if args.output and args.emit == "ir" and suffix == ".ir.json":
        return Path(args.output)
    if args.output and args.emit == "llvm" and suffix == ".ll":
        return Path(args.output)
    if args.output and suffix == ".ll":
        return Path(str(args.output) + ".ll")
    if args.output and suffix == ".ir.json":
        return Path(str(args.output) + ".ir.json")
    return source.with_suffix(suffix)
