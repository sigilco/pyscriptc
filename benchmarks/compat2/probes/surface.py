"""Probe generation for one module (or builtins): runs in its own process, prints JSON lines."""

from __future__ import annotations

import ast
import builtins
import contextlib
import enum
import importlib
import inspect
import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

from benchmarks.compat2.outcomes import WEIGHTS, Probe

from . import typeshed
from .engine import Built, build, raises_probe, run_forked, scalar_params

# These are neither imported nor called: they open browsers, install packages, or run on import.
# Everything else runs in a forked child of a Linux container with a scratch working directory.
UNSAFE_MODULES = frozenset({"antigravity", "ensurepip"})
# GUI toolkits cannot be constructed without a display, so their names cannot be oracle-checked at all.
# They are reported as excluded in the manifest instead of becoming thousands of mention rows.
V1_SURFACE: dict[str, list[str]] = {}
for _qual in json.loads(
    (Path(__file__).resolve().parents[2] / "compat" / "denominators.json").read_text(encoding="utf-8")
)["stdlib_surface"]:
    _mod, _, _name = _qual.rpartition(".")
    V1_SURFACE.setdefault(_mod, []).append(_name)
GUI_MODULES = frozenset({"idlelib", "tkinter", "turtle", "turtledemo"})
UNSAFE_NAMES: frozenset[str] = frozenset()
# `kill` and `killpg` must never get pids 3 or 7: those can be live processes of the generator itself, which
# made generation crash or differ between runs. Pid 0 and signal 0 only probe the child's own process group.
SELF_ONLY = [["0", "0"]]
CALL_OVERRIDES: dict[str, list[list[str]]] = {
    "time.sleep": [["0"]],
    "exit": [["3"]],
    "quit": [["3"]],
    "os.kill": SELF_ONLY,
    "os.killpg": SELF_ONLY,
    "posix.kill": SELF_ONLY,
    "posix.killpg": SELF_ONLY,
}
EXC_ARGS: list[list[str]] = [
    [],
    ["'x'"],
    ["'x'", "[ValueError('y')]"],
    ["'utf-8'", "b'a'", "0", "1", "'x'"],
    ["'utf-8'", "'a'", "0", "1", "'x'"],
    ["'a'", "0", "1", "'x'"],
    ["1", "'x'"],
]
BUILTIN_RECEIVERS: dict[str, tuple[str, str]] = {
    "str": ("str", "'hello world'"),
    "bytes": ("bytes", "b'hello world'"),
    "list": ("list[int]", "[3, 1, 2]"),
    "dict": ("dict[str, int]", "{'a': 1, 'b': 2}"),
    "set": ("set[int]", "{3, 1, 2}"),
    "tuple": ("tuple[int, int, int]", "(3, 1, 2)"),
    "int": ("int", "7"),
    "float": ("float", "2.5"),
}


def emit(probe: Probe, fold: str, reason: str = "") -> None:
    meta = {"id": probe.id, "area": probe.area, "category": probe.category, "kind": probe.kind, "fold": fold}
    if reason:
        meta["reason"] = reason
    print(json.dumps({"probe": probe.to_json(), "meta": meta}), flush=True)


class Ctx:
    def __init__(self, module: str) -> None:
        self.module = module
        self.is_builtins = module == "builtins"
        self.area = "builtins" if self.is_builtins else "surface"
        self.prefix = "builtins" if self.is_builtins else f"stdlib.{module}"
        self.header = [] if self.is_builtins else [f"import {module}"]
        self.stub = typeshed.load(module)

    def stub_of(self, obj: object) -> typeshed.Stub | None:
        """The stub that defines `obj`, which differs from the module's own stub for re-exports."""
        home = getattr(obj, "__module__", None)
        found = typeshed.load(home) if isinstance(home, str) else None
        return found or self.stub

    def find_function(self, obj: object, name: str) -> list[ast.FunctionDef] | None:
        for stub in (self.stub_of(obj), self.stub):
            for key in (getattr(obj, "__name__", name), name):
                if stub and key in stub.functions:
                    return stub.functions[key]
        return None

    def headers_for(self, *texts: str) -> list[str]:
        extra = [
            f"import {m}" for m in ("io",) if any(f"{m}." in t for t in texts) and f"import {m}" not in self.header
        ]
        return [*self.header, *extra]

    def qual(self, name: str) -> str:
        return name if self.is_builtins else f"{self.module}.{name}"

    def mention(self, pid: str, category: str, expr: str, reason: str) -> None:
        source = (
            "\n".join(self.header)
            + ("\n\n" if self.header else "")
            + f"def probe() -> int:\n    {expr}\n    return 1\n"
        )
        probe = Probe(pid, self.area, category, "mention", source, weight=WEIGHTS[category])  # type: ignore[arg-type]
        emit(probe, "mention", reason)

    def finish(self, pid: str, category: str, built: Built, extra_reason: str = "") -> bool:
        if built.kind == "fail":
            return False
        probe = Probe(
            pid,
            self.area,  # type: ignore[arg-type]
            category,  # type: ignore[arg-type]
            built.kind,  # type: ignore[arg-type]
            built.source,
            "probe",
            tuple(built.args),
            built.expect_exc,
            WEIGHTS[category],
        )
        emit(probe, built.fold, extra_reason)
        return True


def try_calls(
    ctx: Ctx,
    callee: str,
    attempts: list[tuple[list[str], dict[str, str]]],
    setup: list[str] | None = None,
    receiver: str = "",
) -> Built:
    """First candidate that runs and folds wins. Else a raises probe if candidates agree on an exception."""
    setup = setup or []
    raised: Counter[str] = Counter()
    first_raise: dict[str, tuple[list[tuple[str, str]], list[object], str]] = {}
    last = Built("fail", reason="no-candidates")
    for positional, keywords in attempts:
        params, args, text = scalar_params(positional, keywords)
        expr = f"{callee}({text})"
        built = build(ctx.headers_for(expr, *setup), params, args, setup, expr, receiver)
        if built.kind == "value":
            return built
        last = built
        if built.fold == "raises":
            raised[built.reason] += 1
            first_raise.setdefault(built.reason, (params, args, expr))
    if raised:
        exc = raised.most_common(1)[0][0]
        params, args, expr = first_raise[exc]
        rebuilt = raises_probe(ctx.headers_for(expr, *setup), params, args, setup, expr, exc)
        if rebuilt.kind == "raises":
            return rebuilt
    return last


def attempts_for(
    qual: str, sigs: list[typeshed.Sig], stub: typeshed.Stub | None, optional_first: bool = False
) -> list[tuple[list[str], dict[str, str]]]:
    """Typed candidates first, then blind ones, since stubs can disagree with the runtime on arity."""
    if qual in CALL_OVERRIDES:
        return [(list(a), {}) for a in CALL_OVERRIDES[qual]]
    typed = typeshed.candidate_calls(sigs, stub, optional_first=optional_first)
    seen = {repr(call) for call in typed}
    return [*typed, *(call for call in typeshed.generic_calls() if repr(call) not in seen)]


def sigs_of(defs: list[ast.FunctionDef] | None, method: bool) -> list[typeshed.Sig]:
    return [typeshed.signature(d, method) for d in defs or []]


def classify(obj: object) -> str:
    if inspect.ismodule(obj):
        return "module"
    if inspect.isclass(obj):
        return "exception" if issubclass(obj, BaseException) else "class"
    if callable(obj):
        return "function"
    return "constant"


def public_names(mod: object) -> list[str]:
    if mod.__name__ == "builtins":
        return sorted(n for n in dir(mod) if not n.startswith("_"))
    declared = getattr(mod, "__all__", None)
    if declared is not None:
        return sorted({n for n in declared if isinstance(n, str) and not n.startswith("_") and hasattr(mod, n)})
    out = []
    for name in dir(mod):
        if name.startswith("_"):
            continue
        obj = getattr(mod, name)
        origin = getattr(obj, "__module__", None)
        if inspect.ismodule(obj) or origin is None or origin == mod.__name__ or not callable(obj):
            out.append(name)
    return sorted(out)


def probe_function(ctx: Ctx, name: str, obj: object) -> None:
    pid, qual = f"{ctx.prefix}.{name}", ctx.qual(name)
    stub = ctx.stub_of(obj)
    defs = ctx.find_function(obj, name)
    built = try_calls(ctx, qual, attempts_for(qual, sigs_of(defs, False), stub))
    if not ctx.finish(pid, "function", built):
        ctx.mention(pid, "function", qual, f"no oracle-checked call: {built.reason}")


def exception_attempts(ctx: Ctx, cls: type) -> list[list[str]]:
    out: list[list[str]] = []
    methods = stub_methods(ctx, cls)
    for key in ("__init__", "__new__"):
        for call in typeshed.candidate_calls(sigs_of(methods.get(key), True), ctx.stub_of(cls), 3):
            out.append(call[0])
    return [*out, *EXC_ARGS]


def probe_exception(ctx: Ctx, name: str, cls: type) -> None:
    pid, qual = f"{ctx.prefix}.{name}", ctx.qual(name)
    for args in exception_attempts(ctx, cls):
        params, values, text = scalar_params(args, {})
        header = ctx.headers_for(text)
        source = (
            "\n".join(header)
            + ("\n\n" if header else "")
            + f"def probe({', '.join(f'{n}: {t}' for n, t in params)}) -> int:\n"
            f"    try:\n        raise {qual}({text})\n    except {qual}:\n        return 1\n    return 0\n"
        )
        status, value = run_forked(lambda s=source, v=values: _run(s, v))
        if status == "ok" and value == 1:
            probe = Probe(pid, ctx.area, "exception", "value", source, "probe", tuple(values), "", WEIGHTS["exception"])  # type: ignore[arg-type]
            emit(probe, "raise-except")
            return
    ctx.mention(pid, "exception", qual, "no constructible argument list")


def _run(source: str, args: list[object]) -> object:
    namespace: dict[str, object] = {}
    exec(compile(source, "<probe>", "exec"), namespace)
    return namespace["probe"](*args)  # type: ignore[operator]


def probe_constant(ctx: Ctx, name: str) -> None:
    pid, qual = f"{ctx.prefix}.{name}", ctx.qual(name)
    built = build(ctx.header, [], [], [], qual)
    if not ctx.finish(pid, "constant", built):
        ctx.mention(pid, "constant", qual, f"no oracle-checked value: {built.reason}")


def probe_submodule(ctx: Ctx, name: str, target: str) -> None:
    pid = f"{ctx.prefix}.{name}"
    source = f"import {target}\n\ndef probe() -> int:\n    return 1\n"
    emit(Probe(pid, ctx.area, "module", "value", source, weight=WEIGHTS["module"]), "import")  # type: ignore[arg-type]


def probe_class(ctx: Ctx, name: str, cls: type) -> Built | None:
    pid, qual = f"{ctx.prefix}.{name}", ctx.qual(name)
    methods = stub_methods(ctx, cls)
    ctor_sigs = sigs_of(methods.get("__init__") or methods.get("__new__"), True)
    attempts = attempts_for(qual, ctor_sigs, ctx.stub_of(cls))
    built = try_calls(ctx, qual, attempts)
    if not ctx.finish(pid, "class", built):
        ctx.mention(pid, "class", qual, f"no oracle-checked construction: {built.reason}")
        return None
    return built


def constructor_setup(ctx: Ctx, name: str, cls: type) -> list[str] | None:
    """A setup line that builds a receiver `o`, or None when the class cannot be constructed here."""
    qual = ctx.qual(name)
    if issubclass(cls, enum.Enum) and len(cls) > 0:
        return [f"o: {qual} = {qual}.{next(iter(cls)).name}"]
    methods = stub_methods(ctx, cls)
    sigs = sigs_of(methods.get("__init__") or methods.get("__new__"), True)
    for positional, keywords in attempts_for(qual, sigs, ctx.stub_of(cls), optional_first=True):
        text = ", ".join([*positional, *(f"{k}={v}" for k, v in keywords.items())])
        built = build(ctx.headers_for(text), [], [], [], f"{qual}({text})")
        if built.kind == "value" and built.ann:
            return [f"o: {built.ann} = {qual}({text})"]
    return None


def stub_methods(ctx: Ctx, cls: type) -> dict[str, list[ast.FunctionDef]]:
    """Stub methods of a class and of its bases that live in the same stub, nearest class first."""
    table: dict[str, list[ast.FunctionDef]] = {}
    for base in cls.__mro__:
        if base is object:
            continue
        node = None
        for stub in (ctx.stub_of(base), ctx.stub):
            node = stub.classes.get(base.__name__) if stub else None
            if node is not None:
                break
        if node is not None:
            for key, defs in typeshed.class_methods(node).items():
                table.setdefault(key, defs)
    return table


def probe_methods(ctx: Ctx, name: str, cls: type, setup: list[str] | None) -> None:
    table = stub_methods(ctx, cls)
    for meth in sorted(n for n in dir(cls) if not n.startswith("_")):
        attr = inspect.getattr_static(cls, meth, None)
        member = getattr(cls, meth, None)
        pid = f"{ctx.prefix}.{name}.{meth}"
        defs = table.get(meth)
        static = bool(defs) and typeshed.is_static_or_class(defs[0])
        is_callable = callable(member) or isinstance(attr, classmethod | staticmethod)
        category = "method" if is_callable else "constant"
        qual = f"{ctx.qual(name)}.{meth}"
        if not is_callable:
            built = build(ctx.headers_for(*(setup or [])), [], [], setup or [], f"o.{meth}" if setup else qual)
            if not ctx.finish(pid, category, built):
                ctx.mention(pid, category, qual, f"no oracle-checked attribute: {built.reason}")
            continue
        if setup is None and not static:
            ctx.mention(pid, category, qual, "class has no constructible receiver")
            continue
        callee = qual if static else f"o.{meth}"
        attempts = attempts_for(qual, sigs_of(defs, True), ctx.stub_of(cls))
        built = try_calls(ctx, callee, attempts, [] if static else setup, "" if static else "o")
        if not ctx.finish(pid, category, built):
            ctx.mention(pid, category, qual, f"no oracle-checked call: {built.reason}")


def run_module(module: str) -> None:
    ctx = Ctx(module)
    try:
        with contextlib.redirect_stdout(open(os.devnull, "w")):  # noqa: SIM115
            mod = importlib.import_module(module)
    except BaseException as exc:  # noqa: BLE001
        stub_only(ctx, f"import failed: {type(exc).__name__}")
        return
    names = sorted({*public_names(mod), *(n for n in V1_SURFACE.get(module, ()) if hasattr(mod, n))})
    if module in GUI_MODULES:
        print(json.dumps({"excluded": {"module": module, "names": len(names), "reason": "gui toolkit"}}), flush=True)
        return
    unsafe_module = module in UNSAFE_MODULES
    for name in names:
        obj = getattr(mod, name)
        category = classify(obj)
        pid = f"{ctx.prefix}.{name}"
        qual = ctx.qual(name)
        if category == "module":
            probe_submodule(ctx, name, getattr(obj, "__name__", qual))
            continue
        if module == "antigravity" or unsafe_module and category != "module" or pid in UNSAFE_NAMES:
            ctx.mention(pid, category, qual, "unsafe to execute at generation time")
            continue
        if category == "function":
            probe_function(ctx, name, obj)
        elif category == "exception":
            probe_exception(ctx, name, obj)
        elif category == "constant":
            probe_constant(ctx, name)
        else:
            probe_class(ctx, name, obj)  # type: ignore[arg-type]
            if not (ctx.is_builtins and name in BUILTIN_RECEIVERS):
                setup = constructor_setup(ctx, name, obj)  # type: ignore[arg-type]
                probe_methods(ctx, name, obj, setup)  # type: ignore[arg-type]
    if ctx.is_builtins:
        probe_builtin_methods(ctx)


def stub_only(ctx: Ctx, reason: str) -> None:
    """The module cannot be imported here, so names come from its typeshed stub and cannot be oracle-checked."""
    if ctx.stub is None:
        return
    for name, category in sorted(ctx.stub.names.items()):
        if not name.startswith("_"):
            ctx.mention(f"{ctx.prefix}.{name}", category, ctx.qual(name), reason)


def probe_builtin_methods(ctx: Ctx) -> None:
    for tname, (ann, literal) in BUILTIN_RECEIVERS.items():
        probe_methods(ctx, tname, getattr(builtins, tname), [f"o: {ann} = {literal}"])


def main(argv: list[str]) -> int:
    sys.setrecursionlimit(2000)
    os.chdir(tempfile.mkdtemp(prefix="ps-probe-"))
    run_module(argv[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
