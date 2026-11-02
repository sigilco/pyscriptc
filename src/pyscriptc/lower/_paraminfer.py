"""Body-usage inference for an unannotated parameter (issue #1002).

The checkers answer `Any` for an unannotated parameter: pyrefly and mypy
both need an annotation or a literal same-file call site to name a
parameter's type, and yt-dlp annotates almost nothing. The body, though,
uses the parameter, and a use is a fact: `url.upper()` is a `str`,
`info_dict.get(k)` is a `dict`, `only_once or other` is a `bool`.

The rule is the compiler's own soundness rule, applied to the body
instead of the call sites: a parameter's type is proved when every use
in the body agrees on one concrete type the lowered subset can express.
A use the reader cannot type, a use that agrees on two types, and a
parameter the body never touches all leave the parameter fenced
(`PS1001`): the inference never guesses.

Two kinds of evidence, with different weights:

* A *method call* on the parameter names the receiver's type outright.
  In the lowered subset a `str` has no subclass, so `x.upper()` proves
  `x` is a `str` on its own.
* A *contextual* use (a comparison against a literal, a binary operation
  with a literal, a boolean context) only suggests a type: the same
  expression is legal on several types. One such use proves nothing; two
  agreeing ones do.

A method name that several container types share (`count`, `index`,
`copy`, `pop`, ...) names no single type, so it is no evidence at all.
"""

from __future__ import annotations

import ast

# The IR kinds a proved parameter type may carry.
_SCALARS = frozenset({"str", "int", "float", "bool", "bytes"})

# The methods whose receiver type the use names outright. A name several
# container types share is deliberately absent: it names no single type.
_STR_METHODS = frozenset(
    {
        "capitalize",
        "casefold",
        "center",
        "encode",
        "endswith",
        "expandtabs",
        "find",
        "format",
        "format_map",
        "isalnum",
        "isalpha",
        "isascii",
        "isdecimal",
        "isdigit",
        "isidentifier",
        "islower",
        "isnumeric",
        "isprintable",
        "isspace",
        "istitle",
        "isupper",
        "join",
        "ljust",
        "lower",
        "lstrip",
        "partition",
        "removeprefix",
        "removesuffix",
        "replace",
        "rfind",
        "rindex",
        "rjust",
        "rpartition",
        "rsplit",
        "rstrip",
        "split",
        "splitlines",
        "startswith",
        "strip",
        "swapcase",
        "title",
        "upper",
        "zfill",
    }
)
_LIST_METHODS = frozenset({"append", "extend", "insert", "reverse", "sort"})
_DICT_METHODS = frozenset({"get", "items", "keys", "setdefault", "values"})
_SET_METHODS = frozenset(
    {"add", "discard", "difference", "intersection", "isdisjoint", "issubset", "issuperset", "union"}
)

# A conversion call is not evidence about its argument: `int(x)` accepts a
# `str`, a `float` and a `bytes`, and `str(x)` accepts anything at all. The
# call names the *result* type, not the argument's, so a converter leaves
# the argument's type unproved.


def _method_kind(name: str) -> str | None:
    """The receiver type a method call names, or None when it names none."""
    if name in _STR_METHODS:
        return "str"
    if name in _LIST_METHODS:
        return "list"
    if name in _DICT_METHODS:
        return "dict"
    if name in _SET_METHODS:
        return "set"
    return None


class _Candidates:
    """The candidate types one parameter has, and whether they conflict."""

    def __init__(self) -> None:
        # The kinds a definite use named.
        self.strong: set[str] = set()
        # The kinds a contextual use suggested.
        self.weak: set[str] = set()

    def add_strong(self, kind: str) -> None:
        self.strong.add(kind)

    def add_weak(self, kind: str) -> None:
        self.weak.add(kind)

    def proved(self) -> str | None:
        """The one type every use agrees on, or None.

        A definite use names the type outright, so it wins; a contextual
        use only agrees with itself, so two agreeing contextual uses are
        the weakest proof the scan accepts.
        """
        if len(self.strong) > 1:
            return None
        if len(self.strong) == 1:
            kind = next(iter(self.strong))
            # A definite use and a contextual use that disagree: the
            # contextual one is the weaker fact, but a disagreement is a
            # disagreement, so the parameter keeps its fence.
            if self.weak - {kind}:
                return None
            return kind
        if len(self.weak) != 1:
            return None
        kind = next(iter(self.weak))
        return kind if self.weak_count >= 2 else None

    weak_count: int = 0


def _scan(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, _Candidates]:
    """The candidate types each parameter of `fn` has, by body use."""
    args = fn.args
    params = [*args.posonlyargs, *args.args, *args.kwonlyargs]
    names = {arg.arg for arg in params}
    if not names:
        return {}
    found: dict[str, _Candidates] = {name: _Candidates() for name in names}
    weak_counts: dict[str, int] = dict.fromkeys(names, 0)

    def strong(name: str, kind: str) -> None:
        cand = found.get(name)
        if cand is not None:
            cand.add_strong(kind)

    def weak(name: str, kind: str) -> None:
        cand = found.get(name)
        if cand is not None:
            cand.add_weak(kind)
            weak_counts[name] += 1

    def literal_kind(node: ast.expr) -> str | None:
        if not isinstance(node, ast.Constant):
            return None
        value = node.value
        if isinstance(value, bool):
            return "bool"
        if isinstance(value, int):
            return "int"
        if isinstance(value, float):
            return "float"
        if isinstance(value, str):
            return "str"
        if isinstance(value, bytes):
            return "bytes"
        return None

    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                kind = _method_kind(func.attr)
                if kind is not None:
                    strong(func.value.id, kind)
            if isinstance(func, ast.Name):
                # A conversion call names the result type, not the
                # argument's, so it is no evidence about the argument.
                continue
        elif isinstance(node, ast.BinOp):
            for side, other in ((node.left, node.right), (node.right, node.left)):
                if not isinstance(side, ast.Name):
                    continue
                kind = literal_kind(other)
                if kind is not None:
                    weak(side.id, kind)
        elif isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.Not) and isinstance(node.operand, ast.Name):
                weak(node.operand.id, "bool")
        elif isinstance(node, ast.Compare):
            # A comparison against a literal suggests the operand's type.
            # Every operand of the comparison is compared against the
            # literals of the same comparison, so a name compared to both
            # "a" and 5 sees two types and stays unproved.
            kinds = {kind for kind in (literal_kind(c) for c in node.comparators) if kind is not None}
            if not kinds:
                continue
            if isinstance(node.left, ast.Name):
                for kind in kinds:
                    weak(node.left.id, kind)
            for comparator in node.comparators:
                if isinstance(comparator, ast.Name):
                    for kind in kinds:
                        weak(comparator.id, kind)
        elif isinstance(node, ast.BoolOp):
            for value in node.values:
                if isinstance(value, ast.Name):
                    weak(value.id, "bool")
        elif isinstance(node, ast.If) and isinstance(node.test, ast.Name):
            weak(node.test.id, "bool")
        elif isinstance(node, ast.While) and isinstance(node.test, ast.Name):
            weak(node.test.id, "bool")
        elif isinstance(node, ast.Assert) and isinstance(node.test, ast.Name):
            weak(node.test.id, "bool")
    for name, cand in found.items():
        cand.weak_count = weak_counts[name]
    return found


def prove_params(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, str]:
    """The parameters of `fn` whose body proves a type, by parameter name.

    The returned map names only parameters the body proves: a parameter the
    body never touches, a parameter whose uses disagree, and a parameter
    whose only evidence is a single contextual use are absent, so the
    caller keeps its fence.
    """
    proved: dict[str, str] = {}
    for name, cand in _scan(fn).items():
        kind = cand.proved()
        if kind is not None and kind in _SCALARS:
            proved[name] = kind
    return proved
