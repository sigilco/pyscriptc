---
title: "The Python subset"
description: "What pyscriptc compiles, how to write code inside the subset, and where to check the live support status."
---

pyscriptc compiles the typed subset of Python 3.14.
Code inside the subset becomes a native binary with no interpreter inside.
Code outside it either stops the build with a `PSxxxx` code that names the exact line, or runs through embedded CPython (fallback mode) when you enable it.
The compiler never guesses.

Check whether your script is inside the subset before compiling:

```bash
pyscriptc coverage script.py
```

`coverage` needs no C toolchain and reports, per line, what would stop the build.
`pyscriptc explain PSxxxx` looks up any code it prints and shows the smallest rewrite that clears it.

## How to write code that compiles

Annotate every variable and function parameter.
The annotations are the input the compiler works from: a type-checker preflight ([pyrefly](https://pyrefly.org/)) checks them first, then the compiler uses them to build the binary.

```python
def sum_squares(xs: list[int]) -> int:
    total: int = 0
    for x in xs:
        total = total + x * x
    return total


def find(xs: list[str], t: str) -> str | None:
    for x in xs:
        if x == t:
            return x
    return None


def main() -> None:
    counts: dict[str, int] = {"a": 1, "b": 2}
    best: int | None = None
    for v in counts.values():
        if best is None or v > best:
            best = v
    print(sum_squares([1, 2, 3]), best, find(["x"], "x"))
```

The rules that matter most:

- Assign to annotated names.
`counts: dict[str, int] = {}` compiles; `counts = {}` stops with `PS1001`, and every later read of the name stops with `PS2002` because of it.
- Narrow optionals with `is None`.
`best: int | None = None` followed by `if best is None or v > best:` compiles, including inside loops.
- Return `T | None` instead of a sentinel value, then narrow at the call site.
- Type function values with `Callable`: `c: Callable[[int], int] = lambda v: v + 1` compiles and calls through `c` are direct native calls.
- Write generic helpers in the PEP 484 shape: `T = TypeVar("T")` at module level, then `def first(xs: list[T]) -> T`.
The helper is specialized per call-site type, including over your own classes.
- Use `if TYPE_CHECKING:` for imports that only feed annotations (for example `collections.abc` shapes or third-party types).
The branch body is skipped by the compiler and the checker still checks it.
- Prefer `from __future__ import annotations` for self-referencing classes, so annotations resolve without forward-reference refusals.
- Read schema-like data into annotated locals: `d: dict[str, int] = json.loads(s)`, then pull typed fields out of `d`.

`typing` and `typing_extensions` are annotation-only modules.
Importing them is free, but a typing name used as a value (`cast(...)`, `get_type_hints(...)`, `NewType(...)`) stops the build with `PS2002`.
There is no `typing` module object in the binary.

## Annotations and idioms

| Write | What happens |
| --- | --- |
| `list[int]`, `dict[str, int]`, `set[int]`, `tuple[int, str]`, `tuple[int, ...]` | compiles; the annotation fixes the element layout |
| `T \| None`, `Optional[T]` | compiles as a tagged optional; `is None` narrowing drives the branches |
| `A \| B` (neither arm `None`), `Union[A, B, None]` | unsupported (`PS1101` / `PS1102`) |
| `Final`, `Final[int]`, `ClassVar[T]` | the inner annotation compiles; the checker enforces no reassignment |
| `Annotated[int, "..."]` | the first argument compiles, the rest is dropped |
| `Callable[[int], int]` | compiles over functions and lambdas; bare `Callable` is unsupported (`PS1104`) |
| `Iterator[int]`, `Iterable[int]`, `Generator[...]`, `Sequence[int]` | compiles (`Sequence[T]` reads as `list[T]`); spell the names under `if TYPE_CHECKING:` from `collections.abc` |
| `TypeVar("T")` generic functions | compiles, specialized per call site |
| PEP 695 `def f[T]`, bound or constrained `TypeVar`, `ParamSpec` | unsupported (`PS1106` and friends) |
| `Protocol`, `ABC`, `@abstractmethod`, `@overload` | the annotation is checked, then dropped; no runtime enforcement (divergence 14) |
| `NamedTuple`, `TypedDict`, `NewType`, `Literal[...]`, `Self` | unsupported; use a `@dataclass`, a plain `dict[K, V]`, an alias, a plain `str`, or the class name |
| `Any`, `object` as an annotation | unsupported (`PS1104`) |
| `async def` and `await` | compiles |
| `dataclasses.dataclass` with no options | compiles as a native record; `frozen=True` and other decorators are unsupported (`PS1201`) |
| `cast(t, v)`, `get_type_hints`, `__annotations__` reads | unsupported (`PS2002`); write the type on an annotated local instead |
| `isinstance(v, list[int])` | unsupported (`PS2001`); runtime checks take scalar and class names only |
| quoted forward references (`"Node"`) | unsupported (`PS1105`); define the class first and write the name unquoted |

Constructs with runtime meaning beyond their annotation (pydantic models, `attrs` validators, anything that reads annotations at run time) are not typing failures: run them through embedded CPython (fallback mode), see [Libraries and fallback mode](libraries.md).

For background on the typing system itself, the upstream references are the best source and pyscriptc does not re-explain them:

- [The typing module](https://docs.python.org/3/library/typing.html) and [Type hints cheat sheet](https://mypy.readthedocs.io/en/stable/cheat_sheet_py3.html) from the mypy docs
- [PEP 484](https://peps.python.org/pep-0484/) (type hints), [PEP 526](https://peps.python.org/pep-0526/) (variable annotations), [PEP 544](https://peps.python.org/pep-0544/) (protocols), [PEP 570](https://peps.python.org/pep-0570/) (positional-only parameters), [PEP 585](https://peps.python.org/pep-0585/) (builtin generics), [PEP 604](https://peps.python.org/pep-0604/) (`X | Y` unions), [PEP 647](https://peps.python.org/pep-0647/) (type guards), [PEP 695](https://peps.python.org/pep-0695/) (type parameter syntax)

## What stays unsupported

- Runtime reflection: `inspect.signature`, `getattr` dispatch, `**kwargs` forwarding, annotation reads.
- Typing names used as values: `cast`, `NewType`, `get_type_hints`, `Annotated` outside an annotation.
- Dynamic object model features: `issubclass`, `type(v)`, `vars`, `__import__`.
- Anything not covered above stops the build with a named `PSxxxx` code, never a guess.
The full divergence list is in [Divergences from CPython](../reference/divergences.md).

## Where the live status lives

Per-construct, per-builtin, per-module and per-library support status is tracked on the [compat site]({{PYSC_COMPAT_URL}}), fed by the [nightly CI workflow](https://github.com/sigilco/pyscriptc/actions/workflows/nightly.yml), so it cannot go stale the way a page would.
This page states the rules and the known gaps only.

## Scripts as a target

pyscriptc aims at real CLI and automation scripts, and is measured against third-party scripts vendored from public repositories (`benchmarks/real_scripts/`).
A script that stops with a `PSxxxx` code is treated as a signal about the compiler, so corpus scripts are never edited to make them compile.

Common failure modes on real scripts:

- An assignment without an annotation (`files = os.listdir(d)`) stops with `PS1001`, and every later read of the name stops with `PS2002`; the first one is the cause and the rest follow from it.
- A keyword argument to a stdlib call (`shutil.copy(src, dst, follow_symlinks=False)`) stops with `PS2003`.
- A module outside the closed import table (`platform`, `calendar`, `zipfile`, `tarfile`, `sqlite3`) stops with `PS2002` on the import.
Run the module through embedded CPython (fallback mode) instead, see [Libraries and fallback mode](libraries.md).

Related commands:

```bash
pyscriptc assess script.py        # what compiles, what does not, and why
pyscriptc imports script.py       # how each imported module is classified
pyscriptc verify script.py --call main   # run the binary and CPython side by side and diff the output
```
