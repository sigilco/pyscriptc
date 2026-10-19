"""Checker preflight.

mypy is a subprocess. Its AST is not an input to lowering. A bad annotation
fails the build before LLVM emission.

The plugin below realigns a few typeshed signatures with the lowered
contract: `struct.unpack` yields a homogeneous `list`, not `tuple[Any, ...]`;
`frozenset(...)` is the set list, so `set[T]` annotation sites accept it.

`--checker pyright` swaps the preflight for a pyright subprocess
(`PYRIGHT_BIN`, fallback `pyright` on PATH). Pyright has no plugin API,
so the realignments above are applied as vendored stubs in
`_pyright_stubs/` (`struct.pyi` + `functools.pyi` patched to yield
`Any` like the hooks) bound through a generated `pyrightconfig.json`
whose `stubPath` points at that directory. Known residual divergences
versus mypy: `builtins` cannot be partially stubbed, so `range(...)` at
`list[int]` sites and `frozenset(...)` at `set[T]` sites are errors under
pyright that mypy's hooks accept. Those two idioms stay documented
divergences; nothing else is suppressed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from mypy.plugin import FunctionContext, Plugin
from mypy.types import AnyType, Instance, Type, TypeOfAny


class CheckFailed(Exception):
    def __init__(self, output: str) -> None:
        super().__init__(output)
        self.output = output


def _unpack_type(ctx: FunctionContext) -> Type:
    # struct.unpack lowers to a homogeneous list; `Any` keeps both `list[int]`
    # and the historical `tuple[int, ...]` annotations checking clean.
    return AnyType(TypeOfAny.explicit)


def _frozenset_type(ctx: FunctionContext) -> Type:
    ret = ctx.default_return_type
    args = ret.args if isinstance(ret, Instance) and ret.args else [AnyType(TypeOfAny.explicit)]
    return ctx.api.named_generic_type("builtins.set", list(args))


def _range_type(ctx: FunctionContext) -> Type:
    # R24: range(...) materializes as list[int]; sizing and indexing line
    # up with CPython's range under the eager model.
    return ctx.api.named_generic_type("builtins.list", [ctx.api.named_type("builtins.int")])


def _partial_type(ctx: FunctionContext) -> Type:
    # functools.partial returns a Callable whose exact parameter shift the
    # subset does not model; Any keeps the value assignable to Callable.
    return AnyType(TypeOfAny.explicit)


_HOOKS: dict[str, Callable[[FunctionContext], Type]] = {
    "struct.unpack": _unpack_type,
    "_struct.unpack": _unpack_type,
    "functools.partial": _partial_type,
    "frozenset": _frozenset_type,
    "builtins.frozenset": _frozenset_type,
    "range": _range_type,
    "builtins.range": _range_type,
}


class _SubsetPlugin(Plugin):
    def get_function_hook(self, fullname: str) -> Callable[[FunctionContext], Type] | None:
        return _HOOKS.get(fullname)


def plugin(version: str) -> type[Plugin]:
    return _SubsetPlugin


def run_checker(path: Path, checker: str = "mypy") -> None:
    if checker == "pyright":
        _run_pyright(path)
        return
    if checker == "pyrefly":
        _run_pyrefly(path)
        return
    if checker != "mypy":
        raise CheckFailed(f"checker {checker!r} is not wired; the spike preflight uses mypy")
    with tempfile.TemporaryDirectory(prefix="pyscriptc-mypy-") as cache:
        config = Path(cache) / "mypy.ini"
        config.write_text("[mypy]\nplugins = pyscriptc.check\n", encoding="utf-8")
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "mypy",
                "--show-error-codes",
                "--hide-error-context",
                "--no-error-summary",
                "--follow-imports=silent",
                "--config-file",
                str(config),
                "--cache-dir",
                cache,
                "--no-incremental",
                str(path),
            ],
            capture_output=True,
            text=True,
        )
    if proc.returncode == 0:
        return
    output = (proc.stdout or proc.stderr).strip()
    if not output:
        output = proc.stderr.strip() or f"mypy exited {proc.returncode}"
    if "No module named mypy" in output:
        output = "mypy is not installed for this interpreter"
    raise CheckFailed(output)


_PYRIGHT_STUBS = Path(__file__).with_name("_pyright_stubs")


def _run_pyrefly(path: Path) -> None:
    binary = os.environ.get("PYREFLY_BIN", "pyrefly")
    try:
        proc = subprocess.run(
            [binary, "check", str(path.resolve())],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise CheckFailed(
            f"pyrefly is not installed (binary {binary!r}); set PYREFLY_BIN or put pyrefly on PATH"
        ) from exc
    if proc.returncode == 0:
        return
    output = (proc.stderr or proc.stdout).strip() or f"pyrefly exited {proc.returncode}"
    raise CheckFailed(output)


def _run_pyright(path: Path) -> None:
    binary = os.environ.get("PYRIGHT_BIN", "pyright")
    source = path.resolve()
    with tempfile.TemporaryDirectory(prefix="pyscriptc-pyright-") as tmp:
        config = Path(tmp) / "pyrightconfig.json"
        config.write_text(
            json.dumps(
                {
                    "stubPath": str(_PYRIGHT_STUBS),
                    "executionEnvironments": [{"root": str(source.parent)}],
                }
            ),
            encoding="utf-8",
        )
        try:
            proc = subprocess.run(
                [
                    binary,
                    "--outputjson",
                    "--pythonversion",
                    "3.13",
                    "--project",
                    tmp,
                    str(source),
                ],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise CheckFailed(
                f"pyright is not installed (binary {binary!r}); set PYRIGHT_BIN or put pyright on PATH"
            ) from exc
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        output = (proc.stderr or proc.stdout).strip() or f"pyright exited {proc.returncode}"
        raise CheckFailed(output) from None
    summary = report.get("summary") or {}
    errors = summary.get("errorCount", 0)
    if errors == 0:
        return
    lines = [
        _format_diagnostic(diag) for diag in report.get("generalDiagnostics", []) if diag.get("severity") == "error"
    ]
    output = "\n".join(line for line in lines if line) or f"pyright reported {errors} error(s)"
    raise CheckFailed(output)


def _format_diagnostic(diag: object) -> str:
    if not isinstance(diag, dict):
        return ""
    start = diag.get("range", {}).get("start", {})
    line = start.get("line", 0) + 1
    message = str(diag.get("message", "")).splitlines()[0]
    rule = diag.get("rule")
    suffix = f" [{rule}]" if rule else ""
    return f"{diag.get('file', '')}:{line}: error: {message}{suffix}"
