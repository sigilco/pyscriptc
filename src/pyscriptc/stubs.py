"""Stub discovery and loading: where a module's declared signatures come from.

Partial slice of the stubs/typeshed design. Nothing in
lowering or the checker reads this module yet; `pyscriptc.sigregistry` exposes
it for the lowering integration that lands after the serialized `lower/` PRs.

Roots are searched in a fixed order, mirroring PEP 561's resolution order for
the parts the partial slice covers:

1. `explicit`: user stub paths (`stub-path` in `pyscriptc.toml`, later a flag).
2. `beside`: a `.pyi` next to the module (`util.pyi` beside `util.py`, or
   `pkg/__init__.pyi`), the same directories `check.py` puts on pyrefly's
   search path.
3. `stubs-dir`: a `stubs/` directory in the source directory.
4. `typeshed`: the vendored `_typeshed/stdlib` snapshot the checker already
   uses, limited to an allowlist (default: the closed import table) and to
   the modules `stdlib/VERSIONS` lists for the target version.

A stdlib module name (any top-level name in `stdlib/VERSIONS`) resolves only
from the typeshed root: a user `json.pyi` cannot retype `json.loads` for
lowering. Third-party stub packages in site-packages (`<pkg>-stubs`, inline
`py.typed`) are the full-scope lane in `2026-10-21-typeshed-third-party-stubs.md`
(W2) and are not searched here.

Stub files are read with `ast` only to find declarations; annotation text is
turned into `TypeFacts` by the same grammar the pyrefly facts adapter uses for
hover payloads (`typefacts._parse_type_text`), so stub-declared and
checker-reported signatures share one vocabulary.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from pyscriptc.stdlib_queue import QUEUE
from pyscriptc.typefacts import _MISSING as _MISSING_DEFAULT
from pyscriptc.typefacts import (
    CallableFacts,
    ParameterFacts,
    StaticValue,
    TypeFacts,
    _default_static_value,
    _parse_type_text,
)

TYPESHED_STDLIB = Path(__file__).with_name("_typeshed") / "stdlib"

# Partial scope: the stdlib modules the closed import table lowers, plus the
# package roots their dotted names need. Re-export targets outside the
# allowlist (`_hashlib`, `json.decoder`) are still followed, never served.
STDLIB_SUBSET: frozenset[str] = frozenset(QUEUE) | {name.split(".")[0] for name in QUEUE}

ROOT_KINDS = ("explicit", "beside", "stubs-dir", "typeshed")

Version = tuple[int, int]


@dataclass(frozen=True)
class StubRoot:
    """One directory that may hold `.pyi` files, with its precedence kind."""

    kind: str
    path: Path
    allow: frozenset[str] | None = None

    def find(self, module: str, version: Version) -> Path | None:
        if self.allow is not None and module not in self.allow:
            return None
        if self.kind == "typeshed" and not stdlib_available(module, version, self.path):
            return None
        return _find_pyi(self.path, module)


def _find_pyi(base: Path, module: str) -> Path | None:
    parts = module.split(".")
    if not all(part.isidentifier() for part in parts):
        return None
    package = base.joinpath(*parts) / "__init__.pyi"
    if package.is_file():
        return package
    single = base.joinpath(*parts[:-1], parts[-1] + ".pyi")
    if single.is_file():
        return single
    return None


@cache
def _versions(stdlib: Path) -> dict[str, tuple[Version, Version | None]]:
    table: dict[str, tuple[Version, Version | None]] = {}
    path = stdlib / "VERSIONS"
    if not path.is_file():
        return table
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        name, _, span = line.partition(":")
        first, _, last = span.strip().partition("-")
        table[name.strip()] = (_version(first), _version(last) if last.strip() else None)
    return table


def _version(text: str) -> Version:
    major, _, minor = text.strip().partition(".")
    return int(major), int(minor or 0)


def stdlib_available(module: str, version: Version, stdlib: Path = TYPESHED_STDLIB) -> bool:
    """True when `module` and each parent package exist for `version` per `stdlib/VERSIONS`."""
    table = _versions(stdlib)
    parts = module.split(".")
    if parts[0] not in table:
        return False
    for end in range(1, len(parts) + 1):
        span = table.get(".".join(parts[:end]))
        if span is not None and not (span[0] <= version and (span[1] is None or version <= span[1])):
            return False
    return True


def is_stdlib(module: str, stdlib: Path = TYPESHED_STDLIB) -> bool:
    return module.split(".")[0] in _versions(stdlib)


def discover_roots(
    source: Path | None = None,
    stub_paths: Sequence[Path] = (),
    *,
    stdlib_allow: Iterable[str] | None = STDLIB_SUBSET,
    typeshed: Path | None = TYPESHED_STDLIB,
) -> tuple[StubRoot, ...]:
    """Roots for `source` (a file or a package directory) in precedence order.

    `stdlib_allow=None` serves the whole vendored stdlib (full scope).
    `typeshed=None` drops the bundled root (tests, or a user who opts out).
    """
    roots: list[StubRoot] = [StubRoot("explicit", Path(p).resolve()) for p in stub_paths]
    if source is not None:
        source = source.resolve()
        bases = [source, source.parent] if source.is_dir() else [source.parent]
        roots += [StubRoot("beside", base) for base in bases]
        stubs_dir = bases[0] / "stubs"
        if stubs_dir.is_dir():
            roots.append(StubRoot("stubs-dir", stubs_dir))
    if typeshed is not None and typeshed.is_dir():
        allow = frozenset(stdlib_allow) if stdlib_allow is not None else None
        roots.append(StubRoot("typeshed", typeshed, allow))
    return tuple(roots)


def find_stub(
    module: str, roots: Sequence[StubRoot], version: Version | None = None, *, internal: bool = False
) -> tuple[StubRoot, Path] | None:
    """First root that holds a `.pyi` for `module`.

    Stdlib names only resolve from typeshed roots. `internal=True` lifts the
    typeshed allowlist; the registry uses it to follow re-exports.
    """
    version = version or sys.version_info[:2]
    stdlib = is_stdlib(module)
    for root in roots:
        if stdlib != (root.kind == "typeshed"):
            continue
        probe = StubRoot(root.kind, root.path) if internal else root
        found = probe.find(module, version)
        if found is not None:
            return root, found
    return None


@dataclass(frozen=True)
class StubSignature:
    """Declared signature(s) of one function or method in a stub.

    `overloads` holds every `@overload` variant in declaration order, or the
    single signature. `method` means the receiver was dropped. `ignored`
    means a `# type: ignore` sits on the `def` line, so the declaration is
    not trusted for lowering.
    """

    module: str
    qualname: str
    overloads: tuple[CallableFacts, ...]
    origin: str
    path: Path
    line: int
    method: bool = False
    ignored: bool = False

    @property
    def callable(self) -> CallableFacts | None:
        return self.overloads[0] if len(self.overloads) == 1 else None


@dataclass(frozen=True)
class StubModule:
    """Declarations read from one `.pyi` file.

    `reexports` maps a local name to `(module, name)` for explicit re-exports
    (`from m import x as x`, relative forms resolved); `imports` holds every
    `from` import, public or not, for resolving annotation names; `aliases`
    maps a `TypeAlias` name to its source text; `star` lists `from m import *`
    sources. `ignored` is a file-level `# type: ignore`;
    `incomplete` is a module `__getattr__`, which types every unknown name
    as its return type (normally `Any`).
    """

    name: str
    path: Path
    origin: str
    functions: dict[str, StubSignature] = field(default_factory=dict)
    classes: frozenset[str] = frozenset()
    variables: dict[str, TypeFacts] = field(default_factory=dict)
    reexports: dict[str, tuple[str, str]] = field(default_factory=dict)
    imports: dict[str, tuple[str, str]] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)
    star: tuple[str, ...] = ()
    ignored: bool = False
    incomplete: bool = False


def load_stub(
    module: str, path: Path, origin: str, version: Version | None = None, platform: str | None = None
) -> StubModule:
    """Parse one `.pyi` into a `StubModule` for the target version and platform."""
    version = version or sys.version_info[:2]
    platform = platform or sys.platform
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path), type_comments=True)
    ignores = {item.lineno for item in tree.type_ignores}
    first = tree.body[0].lineno if tree.body else len(text.splitlines()) + 1
    reader = _Reader(module, path, origin, ignores, version, platform, path.name == "__init__.pyi")
    reader.visit_body(_flatten(tree.body, version, platform), prefix="")
    return StubModule(
        name=module,
        path=path,
        origin=origin,
        functions=reader.finish(),
        classes=frozenset(reader.classes),
        variables=reader.variables,
        reexports=reader.reexports,
        imports=reader.imports,
        aliases=reader.aliases,
        star=tuple(reader.star),
        ignored=any(line < first for line in ignores),
        incomplete="__getattr__" in reader.groups,
    )


def _flatten(body: list[ast.stmt], version: Version, platform: str) -> list[ast.stmt]:
    """Inline `if sys.version_info ...` / `if sys.platform ...` branches for the target."""
    out: list[ast.stmt] = []
    for stmt in body:
        if isinstance(stmt, ast.If):
            verdict = _eval_test(stmt.test, version, platform)
            if verdict is True:
                out += _flatten(stmt.body, version, platform)
            elif verdict is False:
                out += _flatten(stmt.orelse, version, platform)
            else:
                out += _flatten(stmt.body, version, platform) + _flatten(stmt.orelse, version, platform)
        else:
            out.append(stmt)
    return out


_COMPARE = {
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
}


def _eval_test(test: ast.expr, version: Version, platform: str) -> bool | None:
    if isinstance(test, ast.BoolOp):
        values = [_eval_test(value, version, platform) for value in test.values]
        decisive = isinstance(test.op, ast.Or)  # the value that settles the whole expression
        if decisive in values:
            return decisive
        if None in values:
            return None
        return not decisive
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        inner = _eval_test(test.operand, version, platform)
        return None if inner is None else not inner
    if isinstance(test, ast.Compare) and len(test.ops) == 1:
        left, op, right = ast.unparse(test.left), type(test.ops[0]), test.comparators[0]
        if left == "sys.version_info" and isinstance(right, ast.Tuple) and op in _COMPARE:
            try:
                bound = tuple(ast.literal_eval(right))
            except ValueError:
                return None
            return _COMPARE[op](version[: len(bound)], bound)
        if left == "sys.platform" and isinstance(right, ast.Constant) and op in (ast.Eq, ast.NotEq):
            return _COMPARE[op](platform, right.value)
    if isinstance(test, ast.Call) and ast.unparse(test.func) == "sys.platform.startswith" and test.args:
        arg = test.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return platform.startswith(arg.value)
    return None


def _decorator_names(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    return {ast.unparse(d).rsplit(".", 1)[-1] for d in fn.decorator_list}


class _Reader:
    def __init__(
        self,
        module: str,
        path: Path,
        origin: str,
        ignores: set[int],
        version: Version,
        platform: str,
        is_package: bool,
    ) -> None:
        self.module = module
        self.path = path
        self.origin = origin
        self.ignores = ignores
        self.version = version
        self.platform = platform
        self.package = module if is_package else module.rpartition(".")[0]
        # name -> [(signature, line, is_overload, ignored, is_method)]
        self.groups: dict[str, list[tuple[CallableFacts, int, bool, bool, bool]]] = {}
        self.classes: set[str] = set()
        self.variables: dict[str, TypeFacts] = {}
        self.reexports: dict[str, tuple[str, str]] = {}
        self.imports: dict[str, tuple[str, str]] = {}
        self.aliases: dict[str, str] = {}
        self.star: list[str] = []

    def visit_body(self, body: list[ast.stmt], prefix: str) -> None:
        for stmt in body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._function(stmt, prefix)
            elif isinstance(stmt, ast.ClassDef):
                qual = prefix + stmt.name
                self.classes.add(qual)
                self.visit_body(_flatten(stmt.body, self.version, self.platform), prefix=qual + ".")
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and not prefix:
                if ast.unparse(stmt.annotation).rsplit(".", 1)[-1] == "TypeAlias" and stmt.value is not None:
                    self.aliases[stmt.target.id] = ast.unparse(stmt.value)
                else:
                    self.variables[stmt.target.id] = _facts(stmt.annotation)
            elif isinstance(stmt, ast.TypeAlias) and isinstance(stmt.name, ast.Name) and not prefix:
                self.aliases[stmt.name.id] = ast.unparse(stmt.value)
            elif isinstance(stmt, ast.Assign) and not prefix:
                self._alias(stmt)
            elif isinstance(stmt, ast.ImportFrom) and not prefix:
                self._import_from(stmt)

    def _function(self, fn: ast.FunctionDef | ast.AsyncFunctionDef, prefix: str) -> None:
        decorators = _decorator_names(fn)
        method = bool(prefix) and "staticmethod" not in decorators
        owner = prefix[:-1].rsplit(".", 1)[-1] if prefix else None
        facts = _signature(fn, drop_first=method, owner=owner)
        name = prefix + fn.name
        overload = "overload" in decorators
        group = self.groups.setdefault(name, [])
        if group and not overload and not group[-1][2]:
            group.clear()  # a later plain redefinition replaces the earlier one
        ignored = any(line in self.ignores for line in _def_lines(fn))
        group.append((facts, fn.lineno, overload, ignored, method))

    def _alias(self, stmt: ast.Assign) -> None:
        if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
            return
        target = stmt.targets[0].id
        if isinstance(stmt.value, ast.Name) and stmt.value.id in self.groups:
            self.groups[target] = list(self.groups[stmt.value.id])
        elif isinstance(stmt.value, ast.Name) and stmt.value.id in self.reexports:
            self.reexports[target] = self.reexports[stmt.value.id]
        elif isinstance(stmt.value, ast.Attribute) and isinstance(stmt.value.value, ast.Name):
            # `randint = _inst.randint`: a bound method of a module-level instance.
            owner = self.variables.get(stmt.value.value.id)
            method = f"{owner.kind}.{stmt.value.attr}" if owner is not None else ""
            if method in self.groups:
                self.groups[target] = list(self.groups[method])

    def _import_from(self, stmt: ast.ImportFrom) -> None:
        source = stmt.module or ""
        if stmt.level:
            base = self.package.split(".") if self.package else []
            if stmt.level > 1:
                base = base[: len(base) - (stmt.level - 1)]
            source = ".".join(part for part in [*base, source] if part)
        for alias in stmt.names:
            if alias.name == "*":
                self.star.append(source)
                continue
            self.imports[alias.asname or alias.name] = (source, alias.name)
            if alias.asname == alias.name:
                self.reexports[alias.name] = (source, alias.name)
            elif alias.asname is not None and not alias.asname.startswith("_"):
                # `from _hashlib import openssl_sha256 as sha256`: typeshed's
                # rule is that `as` renames are public in stdlib stubs.
                self.reexports[alias.asname] = (source, alias.name)

    def finish(self) -> dict[str, StubSignature]:
        out: dict[str, StubSignature] = {}
        for name, group in self.groups.items():
            variants = [item for item in group if item[2]] or group[-1:]
            out[name] = StubSignature(
                module=self.module,
                qualname=name,
                overloads=tuple(item[0] for item in variants),
                origin=self.origin,
                path=self.path,
                line=variants[0][1],
                method=variants[0][4],
                ignored=any(item[3] for item in variants),
            )
        return out


def _def_lines(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> range:
    """Lines of the `def` header, where a `# type: ignore` applies to the declaration."""
    end = fn.body[0].lineno if fn.body else fn.lineno
    return range(fn.lineno, end + 1)


def _facts(node: ast.expr | None, owner: str | None = None) -> TypeFacts:
    """Annotation node to TypeFacts; a missing annotation is implicit `Any`."""
    if node is None:
        return TypeFacts(kind="any", runtime_repr=None)
    text = ast.unparse(node)
    if owner is not None and text in {"Self", "typing.Self", "typing_extensions.Self"}:
        return TypeFacts(kind=owner, runtime_repr=text)
    return _parse_type_text(text) or TypeFacts(kind="unknown", runtime_repr=text)


def _signature(fn: ast.FunctionDef | ast.AsyncFunctionDef, *, drop_first: bool, owner: str | None) -> CallableFacts:
    args = fn.args
    positional = [*args.posonlyargs, *args.args]
    defaults: list[ast.expr | None] = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    posonly = len(args.posonlyargs)
    if drop_first and positional:  # `self` or `cls`
        positional, defaults = positional[1:], defaults[1:]
        posonly = max(posonly - 1, 0)
    if not args.posonlyargs:
        # Pre-PEP 570 stub convention: leading `__name` parameters are positional-only.
        while posonly < len(positional) and _dunder_positional(positional[posonly].arg):
            posonly += 1
    params: list[ParameterFacts] = []
    static: dict[str, StaticValue] = {}
    for arg, default in zip(positional, defaults, strict=True):
        has_default = default is not None
        params.append(
            ParameterFacts(
                name=arg.arg,
                kind="positional_optional" if has_default else "positional",
                type=_facts(arg.annotation, owner),
                has_default=has_default,
            )
        )
        _record_default(static, arg.arg, default)
    if args.vararg is not None:
        params.append(ParameterFacts(name=args.vararg.arg, kind="varargs", type=_facts(args.vararg.annotation, owner)))
    for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
        has_default = default is not None
        params.append(
            ParameterFacts(
                name=arg.arg,
                kind="keyword_optional" if has_default else "keyword_only",
                type=_facts(arg.annotation, owner),
                has_default=has_default,
            )
        )
        _record_default(static, arg.arg, default)
    if args.kwarg is not None:
        params.append(ParameterFacts(name=args.kwarg.arg, kind="kwargs", type=_facts(args.kwarg.annotation, owner)))
    return CallableFacts(
        parameters=tuple(params),
        return_type=_facts(fn.returns, owner),
        positional_only=posonly,
        keyword_only=frozenset(arg.arg for arg in args.kwonlyargs),
        defaults=static,
    )


def _dunder_positional(name: str) -> bool:
    return name.startswith("__") and not name.endswith("__")


def _record_default(out: dict[str, StaticValue], name: str, default: ast.expr | None) -> None:
    """Keep literal defaults (`flags: int = 0`); stub `...` defaults stay unknown."""
    if default is None:
        return
    value = _default_static_value(ast.unparse(default))
    if value is not _MISSING_DEFAULT and value is not ...:
        out[name] = value
