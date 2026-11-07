"""Checker preflight.

The checker is a subprocess. Its AST is not an input to lowering. A bad
annotation fails the build before LLVM emission.

The default checker is `pyrefly` (resolved by `_resolve_pyrefly_binary`: the
running interpreter's install, then `PYREFLY_BIN`, then PATH): a 34 MB
MIT-licensed binary, fast enough to run per compile. It
runs against a copy of the vendored `_typeshed/` stdlib stubs patched the same
way the mypy plugin realigns signatures (`range(...) -> list[int]`,
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

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import warnings
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from pyscriptc.errors import CheckFailed


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


def run_checker(
    path: Path,
    checker: str = "pyrefly",
    extra_paths: Sequence[Path] | None = None,
    # boxed:begin
    plane_map: dict[str, Any] | None = None,
    fallback: bool = False,
    program_dir: Path | None = None,
    # boxed:end
) -> None:
    """Run the checker over `path`.

    `extra_paths` are roots the checker searches besides the source's own
    directory: the `--ffi` manifest's directory, where the adapter's `.pyi`
    lives. A manifest module the checker cannot import is a false
    `missing-import` error, so the manifest directory rides the same search
    path the stub roots use (issue #66).

    The default is `None`, not `()`: an empty-tuple default is not a
    lowered constant, so the lowerer abandons the whole function body and
    the fences inside it never reach the census (issue #66).
    """
    paths: list[Path] = [Path(p) for p in _active_site_packages()]
    if extra_paths is not None:
        paths = list(extra_paths) + paths
    if checker in _DEPRECATED_CHECKERS:
        warnings.warn(_DEPRECATED_CHECKERS[checker], DeprecationWarning, 2)
    if checker == "pyright":
        # boxed:begin
        _run_pyright(path, paths, plane_map=plane_map, fallback=fallback, program_dir=program_dir)
        # boxed:end
        return
    if checker == "pyrefly":
        # boxed:begin
        _run_pyrefly(path, paths, plane_map=plane_map, fallback=fallback, program_dir=program_dir)
        # boxed:end
        return
    if checker != "mypy":
        raise CheckFailed(f"checker {checker!r} is not wired; the preflight uses pyrefly")
    cache = tempfile.mkdtemp()
    try:
        config = Path(cache) / "mypy.ini"
        # The manifest's directory carries the adapter's `.pyi`, so mypy
        # finds the module the same way pyrefly and pyright do (issue #66).
        mypy_path = ", ".join(str(root) for root in paths)
        config.write_text(
            f"[mypy]\nplugins = pyscriptc.check\nmypy_path = {mypy_path}\n",
            "utf-8",
        )
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
    finally:
        shutil.rmtree(cache)
    if proc.returncode == 0:
        return
    output: str = (proc.stdout or proc.stderr).strip()
    if not output:
        output = proc.stderr.strip() or f"mypy exited {proc.returncode}"
    if "No module named mypy" in output:
        output = "mypy is not installed; install the extra: pip install 'pyscriptc[oracle-mypy]'"
    raise CheckFailed(output)


_PYRIGHT_STUBS = Path(__file__).with_name("_pyright_stubs")


_VENDORED_TYPESHED = Path(__file__).with_name("_typeshed")

_BUILTINS_FROZENSET = "class frozenset(AbstractSet[_T_co]):"


def _patched_typeshed(dest: Path) -> Path:
    """Copy the vendored typeshed stdlib and realign builtins with the lowered contract.

    `frozenset(...)` is the set list (`frozenset[T]` subclasses `set[T]`),
    and `range(...)` materializes `list[int]` (`range.__new__` yields a
    list). Everything else stays stock typeshed. The snapshot in
    `_typeshed/` is a pristine copy of mypy 2.4's bundled stdlib stubs: it
    types `str.split*` as `list[str]` where pyrefly's own bundled snapshot
    says `list[LiteralString]`, and it removes the dependency on mypy being
    installed. A `search-path` `builtins.pyi` override does not work:
    pyrefly then loads two `builtins` modules and `list[int]` stops matching
    `list[int]`, so the whole tree is handed over via `typeshed-path`.
    """
    shutil.copytree(_VENDORED_TYPESHED, dest)
    builtins = dest / "stdlib" / "builtins.pyi"
    text = builtins.read_text(encoding="utf-8")
    if _BUILTINS_FROZENSET not in text or "class range(" not in text:
        raise CheckFailed("vendored typeshed builtins.pyi no longer matches the subset patch anchors")
    text = text.replace(_BUILTINS_FROZENSET, "class frozenset(set[_T_co]):", 1)
    start: int = text.index("class range(")
    end: int = text.index("\nclass ", start)
    block: str = text[start:end].replace("-> Self:", "-> list[int]:")
    builtins.write_text(text[:start] + block + text[end:], "utf-8")
    return dest


# The patched tree is identical for every compile, so it is built once per
# content digest under the user cache dir instead of being copied into a
# fresh tempdir each time (about 160 ms per compile). The staging dir is
# renamed into place, so concurrent compiles never see a partial tree.
# `PYSC_NO_TYPESHED_CACHE=1` bypasses the cache (the patched copy then
# lives next to the checker config, as before).
_TYPESHED_COMPLETE = ".pysc-complete"

_TYPESHED_PATCH_KEY_PARTS = (
    "v1",
    _BUILTINS_FROZENSET,
    "class frozenset(set[_T_co]):",
    "-> list[int]:",
)


def _typeshed_cache_enabled() -> bool:
    return os.environ.get("PYSC_NO_TYPESHED_CACHE", "") not in ("1", "true", "yes")


def _typeshed_cache_root() -> Path:
    override = os.environ.get("PYSC_TYPESHED_CACHE_DIR")
    if override:
        return Path(override)
    return Path.home() / ".cache" / "pyscriptc" / "typeshed"


def _typeshed_digest() -> str:
    """Content hash of the vendored tree plus the builtins patch literals."""
    digest = hashlib.sha256()
    for part in _TYPESHED_PATCH_KEY_PARTS:
        digest.update(part.encode())
        digest.update(b"\0")
    for path in sorted(_VENDORED_TYPESHED.rglob("*")):
        if not path.is_file():
            continue
        digest.update(str(path.relative_to(_VENDORED_TYPESHED)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:32]


def _typeshed_entry_valid(root: Path) -> bool:
    """True for a complete, still-patched cache entry."""
    builtins = root / "stdlib" / "builtins.pyi"
    if not (root / _TYPESHED_COMPLETE).is_file() or not builtins.is_file():
        return False
    try:
        text = builtins.read_text(encoding="utf-8")
    except OSError:
        return False
    return "class frozenset(set[_T_co]):" in text and "-> list[int]:" in text


def _patched_typeshed_cached(fallback: Path) -> Path:
    """The patched typeshed, content-addressed under the user cache dir.

    On a hit the entry path is handed to pyrefly directly; nothing is
    copied. On a miss the tree is built in a staging dir and renamed into
    place. An entry without its completion marker, or one whose
    `builtins.pyi` lost the patch, is treated as corrupt and rebuilt.
    Anything unusable (cache off, read-only home, a lost rename race)
    falls back to a private copy at `fallback`.
    """
    if not _typeshed_cache_enabled():
        return _patched_typeshed(fallback)
    try:
        digest = _typeshed_digest()
        root = _typeshed_cache_root()
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        return _patched_typeshed(fallback)
    cached = root / f"typeshed-{digest}"
    if _typeshed_entry_valid(cached):
        return cached
    stage: Path | None = None
    try:
        stage = Path(tempfile.mkdtemp(prefix=".typeshed-", dir=root))
        build = stage / "tree"
        _patched_typeshed(build)
        (build / _TYPESHED_COMPLETE).write_text(digest + "\n", "utf-8")
        if cached.exists() and not _typeshed_entry_valid(cached):
            shutil.rmtree(cached, ignore_errors=True)
        os.replace(build, cached)
        return cached
    except OSError:
        # A parallel compile won the rename race: its entry is valid.
        if _typeshed_entry_valid(cached):
            return cached
        return _patched_typeshed(fallback)
    finally:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)


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
    exe: str = sysconfig.get_config_var("EXE") or ""
    scripts_dirs: list[tuple[str, str]] = [("interpreter", sysconfig.get_path("scripts"))]
    try:
        scripts_dirs.append(("user-scheme", sysconfig.get_path("scripts", sysconfig.get_preferred_scheme("user"))))
    except KeyError:
        pass
    for source, scripts in scripts_dirs:
        candidate = Path(scripts) / f"{name}{exe}"
        if candidate.is_file() and os.access(str(candidate), os.X_OK):
            return [str(candidate)], source
    found = shutil.which(name)
    if found:
        return [found], "path"
    return list(fallback), "-m-fallback"


def _resolve_pyrefly_binary() -> list[str]:
    return _resolve_pyrefly()[0]


def _resolve_pyrefly() -> tuple[list[str], str]:
    return _resolve_tool("pyrefly", "PYREFLY_BIN", [sys.executable, "-m", "pyrefly"])


# PEP 765 makes a `return`, `break` or `continue` in a `finally` block an
# error in Python 3.15. The subset targets the 3.12 window
# (`.agents/docs/release/python-version-window.md`), where the same code is legal and the
# compiler reproduces CPython's "the finally block replaces the in-flight
# outcome" behavior, so pyrefly's diagnostics for it do not apply.
_PEP765_MARKERS: set[str] = {"in a `finally` block will silence exceptions"}


def _is_pep765(error: dict[str, Any]) -> bool:
    """True for the PEP 765 `finally`-block diagnostics, false otherwise."""
    name: str = ""
    if "name" in error:
        name = error["name"]
    if name != "invalid-syntax":
        return False
    description: str = ""
    if "description" in error:
        description = error["description"]
    for marker in _PEP765_MARKERS:
        if marker in description:
            return True
    return False


def _pyrefly_errors(stdout: str) -> list[dict[str, Any]] | None:
    """The parsed pyrefly diagnostics, or None when the output is not JSON."""
    text = stdout.strip()
    if not text.startswith("{"):
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    errors = data.get("errors")
    if not isinstance(errors, list):
        return None
    return [err for err in errors if isinstance(err, dict)]


def _format_pyrefly_error(error: dict[str, Any]) -> str:
    """One diagnostic in the shape the CLI prints."""
    path: str = ""
    if "path" in error:
        path = error["path"]
    line: int = 0
    if "line" in error:
        line = error["line"]
    rule: str = ""
    if "name" in error:
        rule = error["name"]
    description: str = ""
    if "description" in error:
        description = error["description"]
    if rule:
        return f"{path}:{line}: error: {description} [{rule}]"
    return f"{path}:{line}: error: {description}"


def _active_interpreter() -> str:
    venv = os.environ.get("VIRTUAL_ENV")
    if venv:
        for candidate in (
            str(Path(venv) / "bin" / "python"),
            str(Path(venv) / "bin" / "python3"),
            str(Path(venv) / "Scripts" / "python.exe"),
            str(Path(venv) / "python.exe"),
        ):
            if Path(candidate).is_file() and os.access(candidate, os.X_OK):
                return candidate
    return sys.executable


def _active_site_packages() -> list[str]:
    roots: list[str] = []
    pythonpath = os.environ.get("PYTHONPATH")
    if pythonpath:
        for p in pythonpath.split(os.pathsep):
            if p and Path(p).is_dir() and p not in roots:
                roots.append(p)
    venv = os.environ.get("VIRTUAL_ENV")
    if not venv:
        return roots
    for sp_candidate in (
        str(Path(venv) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"),
        str(Path(venv) / "Lib" / "site-packages"),
    ):
        if Path(sp_candidate).is_dir() and sp_candidate not in roots:
            roots.append(sp_candidate)
    return roots


def _run_pyrefly(
    path: Path,
    extra_paths: Sequence[Path] | None = None,
    # boxed:begin
    plane_map: dict[str, Any] | None = None,
    fallback: bool = False,
    program_dir: Path | None = None,
    # boxed:end
) -> None:
    argv = _resolve_pyrefly_binary()
    source = path.resolve()
    roots: list[Path] = [Path(p) for p in _active_site_packages()]
    if extra_paths is not None:
        roots = list(extra_paths) + roots
    with tempfile.TemporaryDirectory(None, "pyscriptc-pyrefly-") as tmp:
        typeshed = _patched_typeshed_cached(Path(tmp) / "typeshed")
        search = [str(_PYRIGHT_STUBS)]
        if source.is_dir():
            # A package's own dir resolves `from .x import ...`-adjacent
            # names; its parent resolves `import pkg.x`.
            search += [str(source), str(source.parent)]
        else:
            search.append(str(source.parent))
        # boxed:begin
        if fallback:
            if program_dir is None:
                p_env = os.environ.get("PYSC_PROGRAM_DIR")
                prog_path = Path(p_env).resolve() if p_env else (source if source.is_dir() else source.parent)
            else:
                prog_path = Path(program_dir).resolve()
            if str(prog_path) not in search:
                search.append(str(prog_path))
            pythonpath = os.environ.get("PYTHONPATH")
            if pythonpath:
                for p in pythonpath.split(os.pathsep):
                    if p:
                        resolved_p = str(Path(p).resolve())
                        if Path(resolved_p).is_dir() and resolved_p not in search:
                            search.append(resolved_p)
        # boxed:end
        search += [str(root) for root in roots if str(root) not in search]
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
            "utf-8",
        )
        try:
            proc = subprocess.run(
                [
                    *argv,
                    "check",
                    "-c",
                    str(config),
                    "--python-interpreter-path",
                    _active_interpreter(),
                    "--output-format",
                    "json",
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
    errors: list[dict[str, Any]] | None = _pyrefly_errors(proc.stdout)
    if errors is not None:
        kept: list[dict[str, Any]] = []
        for err in errors:
            if not _is_pep765(err):
                kept.append(err)
        if not kept:
            return
        # boxed:begin
        if fallback and plane_map is not None:
            from pyscriptc import boxed as _boxed

            suppressed: list[dict[str, Any]] = []
            remaining: list[dict[str, Any]] = []
            for err in kept:
                if _boxed.is_island_diagnostic(err, source, plane_map, Path(tmp)):
                    suppressed.append(err)
                else:
                    remaining.append(err)
            if suppressed:
                count: int = len(suppressed)
                s: str = "s" if count != 1 else ""
                print(f"note: suppressed {count} checker diagnostic{s} routed to the island", file=sys.stderr)
                for err in suppressed[:3]:
                    print(f"note: {_format_pyrefly_error(err)}", file=sys.stderr)
            if not remaining:
                return
            kept = remaining
        # boxed:end
        raise CheckFailed("\n".join(_format_pyrefly_error(err) for err in kept))
    # Diagnostics go to stdout; stderr carries WARN/INFO progress lines.
    output: str = proc.stdout.strip() or proc.stderr.strip() or f"pyrefly exited {proc.returncode}"
    if "usage: pyscriptc" in output:
        # A self-compiled pyscriptc has no Python interpreter to run
        # `python -m pyrefly`: the fallback launched the compiler itself, and
        # the compiler printed its own usage. Name the remedy instead.
        raise CheckFailed(
            "pyrefly is not installed for this compiler; the checker is an external tool. "
            "Install it (pip install pyrefly) or point PYREFLY_BIN at a pyrefly binary, "
            "or compile with --skip-check"
        )
    raise CheckFailed(output)


def _run_pyright(
    path: Path,
    extra_paths: Sequence[Path] | None = None,
    # boxed:begin
    plane_map: dict[str, Any] | None = None,
    fallback: bool = False,
    program_dir: Path | None = None,
    # boxed:end
) -> None:
    argv = _resolve_tool_argv("pyright", "PYRIGHT_BIN", ["pyright"])
    source = path.resolve()
    roots: list[str] = _active_site_packages()
    if extra_paths is not None:
        extra_strs: list[str] = []
        for i in range(len(extra_paths)):
            extra_strs.append(str(extra_paths[i]))
        roots = extra_strs + roots
    # boxed:begin
    if fallback:
        prog_path: Path = source
        if program_dir is not None:
            prog_path = program_dir.resolve()
        else:
            p_env: str = os.environ.get("PYSC_PROGRAM_DIR") or ""
            if p_env:
                prog_path = Path(p_env).resolve()
            elif not source.is_dir():
                prog_path = source.parent
        prog_dir: str = str(prog_path)
        if prog_dir not in roots:
            roots.append(prog_dir)
        pythonpath: str = os.environ.get("PYTHONPATH") or ""
        if pythonpath:
            for p in pythonpath.split(os.pathsep):
                if p:
                    resolved_p: Path = Path(p).resolve()
                    if resolved_p.is_dir():
                        resolved_s: str = str(resolved_p)
                        if resolved_s not in roots:
                            roots.append(resolved_s)
    # boxed:end
    with tempfile.TemporaryDirectory(None, "pyscriptc-pyright-") as tmp:
        config = Path(tmp) / "pyrightconfig.json"
        config.write_text(
            json.dumps(
                {
                    "stubPath": str(_PYRIGHT_STUBS),
                    "extraPaths": roots,
                    "executionEnvironments": [{"root": str(source.parent)}],
                }
            ),
            "utf-8",
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
                f"pyright is not installed (command {argv[0]!r}); "
                "install the extra: pip install 'pyscriptc[oracle-pyright]', or set PYRIGHT_BIN / put pyright on PATH"
            ) from exc
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        output = (proc.stderr or proc.stdout).strip() or f"pyright exited {proc.returncode}"
        raise CheckFailed(output) from None
    summary: dict[str, Any] = {}
    if "summary" in report:
        summary = report["summary"]
    errors: int = 0
    if "errorCount" in summary:
        errors = summary["errorCount"]
    if errors == 0:
        return
    diagnostics: list[dict[str, Any]] = []
    if "generalDiagnostics" in report:
        diagnostics = report["generalDiagnostics"]
    lines: list[str] = []
    for diag in diagnostics:
        severity: str = ""
        if "severity" in diag:
            severity = diag["severity"]
        if severity == "error":
            lines.append(_format_diagnostic(diag))
    non_empty: list[str] = []
    for line in lines:
        if line:
            non_empty.append(line)
    output: str = "\n".join(non_empty) or f"pyright reported {errors} error(s)"
    raise CheckFailed(output)


def _format_diagnostic(diag: dict[str, Any]) -> str:
    rng: dict[str, Any] = {}
    if "range" in diag:
        rng = diag["range"]
    start: dict[str, Any] = {}
    if "start" in rng:
        start = rng["start"]
    line: int = 0
    if "line" in start:
        line = start["line"]
    line += 1
    message: str = ""
    if "message" in diag:
        message = diag["message"]
    message_lines: list[str] = message.splitlines()
    if message_lines:
        message = message_lines[0]
    else:
        message = ""
    rule: str = ""
    if "rule" in diag:
        rule = diag["rule"]
    suffix: str = ""
    if rule:
        suffix = f" [{rule}]"
    file: str = ""
    if "file" in diag:
        file = diag["file"]
    return f"{file}:{line}: error: {message}{suffix}"
