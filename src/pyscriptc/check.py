"""Checker preflight.

mypy is a subprocess. Its AST is not an input to lowering. A bad annotation
fails the build before LLVM emission.

The plugin below realigns a few typeshed signatures with the lowered
contract: `struct.unpack` yields a homogeneous `list`, not `tuple[Any, ...]`;
`frozenset(...)` is the set list, so `set[T]` annotation sites accept it.
"""

from __future__ import annotations

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
