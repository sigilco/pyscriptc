"""Checker preflight.

The checker is a subprocess. Its AST is not an input to lowering. A bad
annotation fails the build before LLVM emission.

The default checker is `pyrefly` (resolved by `_resolve_pyrefly_binary`: the
running interpreter's install, then `PYREFLY_BIN`, then PATH): a 34 MB
MIT-licensed binary, fast enough to run per compile. It
runs against a copy of mypy's bundled typeshed patched the same way the
mypy plugin realigns signatures (`range(...) -> list[int]`,
`frozenset[T]` subclasses `set[T]`), plus the vendored `_pyright_stubs/`
on `search-path` so `struct.unpack` and `functools.partial` keep
yielding `Any`. Two check kinds that reject idioms the subset defines —
`unbound-name` (bare `x: int` declarations are zero-initialized) and
`bad-context-manager` (`__exit__` keeps the lowered signature) — are
silenced in the generated `pyrefly.toml`; nothing else is suppressed.

Deprecated: `--checker mypy` and `--checker pyright` still work but emit
DeprecationWarning. The mypy plugin below realigns a few typeshed
signatures with the lowered contract: `struct.unpack` yields a
homogeneous `list`, not `tuple[Any, ...]`; `frozenset(...)` is the set
list, so `set[T]` annotation sites accept it. The pyright lane reuses
`_pyright_stubs/` through a generated `pyrightconfig.json` `stubPath`.
Keep them for sanity-diffing against pyrefly (`benchmarks/factsdiff.py`)
and as the escape hatch for the two builtins divergences.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import warnings
from collections.abc import Callable
from pathlib import Path


class CheckFailed(Exception):
    def __init__(self, output: str) -> None:
        super().__init__(output)
        self.output = output


def plugin(version: str) -> type:
    """mypy plugin entry point (deprecated mypy lane only).

    mypy is imported here, not at module import, so `pyscriptc.check` works
    with mypy absent (install the `oracle-mypy` extra for the mypy lane).
    """
    return _subset_plugin_class()


def _subset_plugin_class() -> type:
    try:
        from mypy.plugin import FunctionContext, Plugin
        from mypy.types import AnyType, Instance, Type, TypeOfAny
    except ImportError as exc:
        raise CheckFailed("mypy is not installed; install the extra: pip install 'pyscriptc[oracle-mypy]'") from exc

    def unpack_type(ctx: FunctionContext) -> Type:
        # struct.unpack lowers to a homogeneous list; `Any` keeps both
        # `list[int]` and the historical `tuple[int, ...]` annotations clean.
        return AnyType(TypeOfAny.explicit)

    def frozenset_type(ctx: FunctionContext) -> Type:
        ret = ctx.default_return_type
        args = ret.args if isinstance(ret, Instance) and ret.args else [AnyType(TypeOfAny.explicit)]
        return ctx.api.named_generic_type("builtins.set", list(args))

    def range_type(ctx: FunctionContext) -> Type:
        # R24: range(...) materializes as list[int]; sizing and indexing line
        # up with CPython's range under the eager model.
        return ctx.api.named_generic_type("builtins.list", [ctx.api.named_type("builtins.int")])

    def partial_type(ctx: FunctionContext) -> Type:
        # functools.partial returns a Callable whose exact parameter shift the
        # subset does not model; Any keeps the value assignable to Callable.
        return AnyType(TypeOfAny.explicit)

    hooks: dict[str, Callable[[FunctionContext], Type]] = {
        "struct.unpack": unpack_type,
        "_struct.unpack": unpack_type,
        "functools.partial": partial_type,
        "frozenset": frozenset_type,
        "builtins.frozenset": frozenset_type,
        "range": range_type,
        "builtins.range": range_type,
    }

    class _SubsetPlugin(Plugin):
        def get_function_hook(self, fullname: str) -> Callable[[FunctionContext], Type] | None:
            return hooks.get(fullname)

    return _SubsetPlugin


_DEPRECATED_CHECKERS = {
    "mypy": "--checker mypy is deprecated; pyrefly is the default preflight. "
    "mypy remains as the coverage escape hatch (range/frozenset builtins realignments).",
    "pyright": "--checker pyright is deprecated; pyrefly is the default preflight.",
}


def run_checker(path: Path, checker: str = "pyrefly") -> None:
    if checker in _DEPRECATED_CHECKERS:
        warnings.warn(_DEPRECATED_CHECKERS[checker], DeprecationWarning, stacklevel=2)
    if checker == "pyright":
        _run_pyright(path)
        return
    if checker == "pyrefly":
        _run_pyrefly(path)
        return
    if checker != "mypy":
        raise CheckFailed(f"checker {checker!r} is not wired; the preflight uses pyrefly")
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
        output = "mypy is not installed; install the extra: pip install 'pyscriptc[oracle-mypy]'"
    raise CheckFailed(output)


_PYRIGHT_STUBS = Path(__file__).with_name("_pyright_stubs")


def _patched_typeshed(dest: Path) -> Path:
    """Copy mypy's bundled typeshed and realign builtins with the lowered contract.

    `frozenset(...)` is the set list (`frozenset[T]` subclasses `set[T]`),
    and `range(...)` materializes `list[int]` (`range.__new__` yields a
    list). Everything else stays stock mypy-typeshed — which, unlike
    pyrefly's bundled snapshot, already types `str.split*` as `list[str]`
    rather than `list[LiteralString]`.
    """
    import mypy  # local import: only needed on the pyrefly lane

    shutil.copytree(Path(mypy.__file__).resolve().parent / "typeshed", dest)
    builtins = dest / "stdlib" / "builtins.pyi"
    text = builtins.read_text(encoding="utf-8")
    text = text.replace("class frozenset(AbstractSet[_T_co]):", "class frozenset(set[_T_co]):", 1)
    start = text.index("class range(")
    end = text.index("\nclass ", start)
    block = text[start:end].replace("-> Self:", "-> list[int]:")
    builtins.write_text(text[:start] + block + text[end:], encoding="utf-8")
    return dest


def _resolve_tool_argv(name: str, env_var: str, fallback: list[str]) -> list[str]:
    """argv that launches a checker binary, preferring the running interpreter's install.

    Order: `env_var` (shlex-split, an explicit override), then the scripts dir of
    the running interpreter, then the user scheme's scripts dir, then `name` on
    PATH, then `fallback`. The result is an argv list, so `fallback` may be multi-token
    (`[sys.executable, "-m", "pyrefly"]`).
    """
    return _resolve_tool(name, env_var, fallback)[0]


def _resolve_tool(name: str, env_var: str, fallback: list[str]) -> tuple[list[str], str]:
    """Like `_resolve_tool_argv` but also names the winning source.

    Source is one of `env`, `interpreter`, `user-scheme`, `path`, `-m-fallback`.
    """
    override = os.environ.get(env_var)
    if override:
        return shlex.split(override), "env"
    exe = sysconfig.get_config_var("EXE") or ""
    scripts_dirs = [("interpreter", sysconfig.get_path("scripts"))]
    try:
        scripts_dirs.append(("user-scheme", sysconfig.get_path("scripts", sysconfig.get_preferred_scheme("user"))))
    except KeyError:
        pass
    for source, scripts in scripts_dirs:
        candidate = Path(scripts) / f"{name}{exe}"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return [str(candidate)], source
    found = shutil.which(name)
    if found:
        return [found], "path"
    return list(fallback), "-m-fallback"


def _resolve_pyrefly_binary() -> list[str]:
    return _resolve_pyrefly()[0]


def _resolve_pyrefly() -> tuple[list[str], str]:
    return _resolve_tool("pyrefly", "PYREFLY_BIN", [sys.executable, "-m", "pyrefly"])


def _run_pyrefly(path: Path) -> None:
    argv = _resolve_pyrefly_binary()
    source = path.resolve()
    with tempfile.TemporaryDirectory(prefix="pyscriptc-pyrefly-") as tmp:
        typeshed = _patched_typeshed(Path(tmp) / "typeshed")
        search = [str(_PYRIGHT_STUBS)]
        if source.is_dir():
            # A package's own dir resolves `from .x import ...`-adjacent
            # names; its parent resolves `import <pkg>.x`.
            search += [str(source), str(source.parent)]
        else:
            search.append(str(source.parent))
        config = Path(tmp) / "pyrefly.toml"
        config.write_text(
            f'typeshed-path = "{typeshed}"\n'
            f"search-path = [{', '.join(json.dumps(s) for s in search)}]\n\n"
            "[errors]\n"
            "# The subset deliberately diverges from typeshed here: bare\n"
            "# `x: int` declarations are zero-initialized (unbound-name),\n"
            "# and __exit__ signatures keep the lowered shape\n"
            "# (bad-context-manager).\n"
            'unbound-name = "ignore"\n'
            'bad-context-manager = "ignore"\n',
            encoding="utf-8",
        )
        try:
            proc = subprocess.run(
                [
                    *argv,
                    "check",
                    "-c",
                    str(config),
                    "--python-interpreter-path",
                    sys.executable,
                    str(source),
                ],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise CheckFailed(
                f"pyrefly is not installed (command {argv[0]!r}); set PYREFLY_BIN or put pyrefly on PATH"
            ) from exc
    if proc.returncode == 0:
        return
    # Diagnostics go to stdout; stderr carries WARN/INFO progress lines.
    output = proc.stdout.strip() or proc.stderr.strip() or f"pyrefly exited {proc.returncode}"
    raise CheckFailed(output)


def _run_pyright(path: Path) -> None:
    argv = _resolve_tool_argv("pyright", "PYRIGHT_BIN", ["pyright"])
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
                    *argv,
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
                f"pyright is not installed (command {argv[0]!r}); set PYRIGHT_BIN or put pyright on PATH"
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
