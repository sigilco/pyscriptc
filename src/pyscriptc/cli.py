"""Command line for the spike compiler."""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from pyscriptc.check import CheckFailed, run_checker
from pyscriptc.coverage import build_report, render_text
from pyscriptc.diagnostics import explain
from pyscriptc.errors import FitError, IRError, SpikeError, VersionError
from pyscriptc.ir import validate_module
from pyscriptc.llvm import emit_llvm, link_binary
from pyscriptc.lower import lower_file, render_fences
from pyscriptc.package import lower_directory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pyscriptc",
        description="Lower a typed Python subset to IR and LLVM.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    compile_parser = sub.add_parser("compile", help="lower a source file")
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
    compile_parser.add_argument("--skip-check", action="store_true")
    compile_parser.add_argument("--checker", default="mypy")
    coverage_parser = sub.add_parser("coverage", help="report every fence in a file or package tree")
    coverage_parser.add_argument("source")
    coverage_parser.add_argument("--json", action="store_true", help="print the report as JSON")
    explain_parser = sub.add_parser("explain", help="describe a diagnostic code")
    explain_parser.add_argument("code")
    try:
        args = parser.parse_args(argv)
        if args.cmd == "compile":
            return compile_cmd(args)
        if args.cmd == "coverage":
            return coverage_cmd(args)
        if args.cmd == "explain":
            return explain_cmd(args)
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
    print(json.dumps(report, indent=2) if args.json else render_text(report))
    return 0


def explain_cmd(args: argparse.Namespace) -> int:
    text = explain(args.code)
    if text is None:
        print(f"unknown diagnostic code: {args.code}", file=sys.stderr)
        return 2
    print(text)
    return 0


def compile_cmd(args: argparse.Namespace) -> int:
    source_path = Path(args.source)
    if source_path.is_dir():
        module, source = lower_directory(source_path)
    elif source_path.is_file():
        module, source = lower_file(str(source_path))
    else:
        raise IRError(f"source not found: {source_path}")
    if args.run:
        args.emit = "bin"
    if args.emit == "bin" and not args.call:
        raise IRError("--emit bin requires --call")
    for note in module.pop("_notes", []):
        print(note, file=sys.stderr)
    if not args.skip_check:
        run_checker(source_path, args.checker)
    validate_module(module)
    ir_path = _output_path(args, source_path, ".ir.json")
    ir_path.parent.mkdir(parents=True, exist_ok=True)
    ir_path.write_text(json.dumps(module, indent=2) + "\n", encoding="utf-8")
    if module["fences"]:
        print(render_fences(module, source), file=sys.stderr)
        return 3
    if args.emit == "ir":
        print(ir_path)
        return 0
    call_args = _parse_args(args.args) if args.call else None
    llvm_text = emit_llvm(module, args.call, call_args)
    ll_path = _output_path(args, source_path, ".ll")
    ll_path.write_text(llvm_text, encoding="utf-8")
    if args.emit == "llvm":
        print(ll_path)
        return 0
    binary = Path(args.output) if args.output else source_path.with_suffix("")
    link_binary(ll_path, binary)
    if not args.run:
        print(binary)
        return 0
    completed = subprocess.run([str(binary)])
    if completed.returncode < 0:
        return 128 + (-completed.returncode)
    return completed.returncode


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
