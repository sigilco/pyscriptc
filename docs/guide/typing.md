---
title: Typing and idioms
description: How to write idiomatic typed Python for the native tier, construct by construct, with what the compiler does with each annotation.
---

pyscriptc compiles the typed subset of Python 3.14.
Annotations are not decorations the compiler throws away: they are the input that drives lowering.
Types are erased at compile time in the sense that no type objects, no annotation tables and no `typing` runtime surface exist in the binary, but the information itself is read, checked by the preflight type-checker ([pyrefly](https://pyrefly.org/)), and used to choose layouts, call forms and fences.
This page says, per construct, what the compiler does with it today, and links every refusal to its numbered divergence or its `PSxxxx` code so you can write code that lowers the first time.

The stance is the typed-subset positioning (`.agents/plans/2026-10-25-typed-subset-positioning.md`): island is the floor, native is the ceiling, and the guide is the part in between.
What works is not listed as a promise; each rule below states what the compiler does, and anything not covered follows the general contract: a named `PSxxxx` fence, never a guess.
Live per-module status is measured nightly on the [compat site]({{PYSC_COMPAT_URL}}).

## The mental model

- Annotations are compile-time facts.
`def f(s: str) -> int` fixes the parameter as an i64-backed `str` record and the return as an `int`, and the lowering plans from exactly those facts.
- `typing` and `typing_extensions` are annotation-only modules.
An import of them is free: the checker keeps the names and the lowering erases the import.
There is no `typing` module object in the binary, so a typing name used as a value (`cast(...)`, `get_type_hints(...)`) is a fence (`PS2002`), not a missing library.
- Types describe how to lower, not what ships.
`Optional[int]` is not "unsupported typing": it is the instruction to build an i64 plus a None tag, and to fence every path that forgets the tag.
- A construct with runtime meaning beyond its annotation (an `Annotated` metadata consumer, a `TypedDict` validated at run time, a pydantic model) is not a typing failure.
It is a runtime-introspection feature, and it belongs to the island lane or to an explicit compile-time substitute, per the introspection decision 7 of the positioning plan.

## Construct table

Every row was probed through `pyscriptc compile` on the day this table was written.
"Erased" means the annotation is read for lowering and no runtime trace remains.
"Lowered" means the construct produces native code.

| Construct | What the compiler does today | Where it goes |
| --- | --- | --- |
| `list[int]`, `dict[str, int]`, `set[int]`, `tuple[int, str]`, `tuple[int, ...]` | lowered; the generics fix the element layout | native |
| `T \| None`, `Optional[T]`, `Union[T, None]` (one arm) | lowered: a tagged optional; `is None` narrowing drives branches | native |
| `A \| B` (two arms, neither None) | fenced `PS1101`: the IR has no tagged union yet | fence, erased-annotation work tracked in #412 |
| `Union[A, B, None]` | fenced `PS1102`: a multi-arm union is not in the IR | fence |
| `Final`, `Final[int]` | unwrapped: the annotation inside lowers; the checker enforces no reassignment | native (erased) |
| `Annotated[int, "..."]` | the first argument is lowered, the rest is erased; at module level an alias assignment `Id = Annotated[int, "pk"]` fences `PS2002` because it is a value | native (erased) |
| `Callable[[int], int]` | lowered over functions and lambdas typed with it | native |
| bare `Callable` (no parameter list) | fenced `PS1104` | fence |
| `Iterator[int]`, `Iterable[int]`, `Sequence[int]`, `Generator[int, None, None]` | lowered: `Iterator`/`Iterable`/`Generator` map to the iterator shape, `Sequence[T]` reads as `list[T]` | native (erased to list) |
| `Iterator` spelled from `collections.abc` | lowered when the name arrives through `if TYPE_CHECKING:`; a plain `import collections.abc` is outside the closed table (`PS2002`, island at import level) | native (erased) with the TYPE_CHECKING spelling |
| `TypeVar("T")` + `def f(xs: list[T]) -> T` (PEP 484 form) | lowered: monomorphized per call-site instantiation | native |
| PEP 695 `def f[T]` and `class C[T]` | fenced `PS1106`: the parameter `T` is not a lowered type name yet | fence |
| `TypeVar("T", bound=...)`, constrained `TypeVar` | fenced: bound and constraint forms do not lower | fence |
| `ParamSpec` | fenced: the `P = ParamSpec("P")` statement is a value fence (`PS2002`), the `Callable[P, ...]` annotation is not a lowered form | fence |
| `Protocol` | base and declaration-only members erase; a parameter annotated with a protocol accepts any class whose definer names the protocol in its bases; member reads on the protocol type fence (`PS2001`), `isinstance` on a protocol fences | native (erased), reads fence |
| `ABC`, `ABCMeta`, `@abstractmethod` | erased: plain classes and methods, no runtime enforcement, declaration-only bodies are skipped | native (erased), divergence 14 |
| `@overload` | erased: only the implementation lowers; pyrefly checks the overload set | native (erased), divergence 14 |
| `NamedTuple` | fenced `PS2002` (class base not lowered): write a `@dataclass` or a plain class | fence |
| `TypedDict` | fenced `PS2002` (class base not lowered): a plain `dict[K, V]` annotation is the native form; the `**kwargs: Unpack[TypedDict]` idiom named by diagnostics is future work for the island lane | fence |
| `NewType("Id", int)` | fenced `PS2002` (value at module level): alias the base type instead | fence |
| `Self` | fenced `PS1103`: return the class name (quoted if it is not yet defined) | fence |
| `ClassVar[T]` | unwrapped like `Final`; the field itself follows the class rules (annotation-only, assigned in `__init__`) | native (erased) |
| `Literal["a", "b"]` | fenced `PS1103`: literals are not value types in the IR; a plain `str` parameter plus a checker-tight call site is the typed form today | fence |
| `TYPE_CHECKING` guard | folded: the branch body is skipped at lowering and the checker still sees it; imports under the guard are kept as erased bindings | native (folded) |
| `cast(t, v)` | fenced `PS2002`: `cast` is a value; write the type on an annotated local instead | fence |
| `Any`, `object` as annotations | fenced `PS1104`: no dynamic slot in the IR | fence |
| `from __future__ import annotations` | accepted; the file is parsed and annotations resolve as written | native |
| quoted annotations (`"Node"`, `"Node \| None"`) | fenced `PS1105` unless they name a form the lowering resolves (self-reference through a plain class name does not need quotes); define the class first and write the name unquoted | fence |
| `typing.get_type_hints`, `__annotations__` reads | fenced `PS2002` naming the island lane: native code keeps no annotation table | island only |
| `isinstance(v, list[int])` | fenced `PS2001`: runtime checks take scalar and class names only | fence |
| `issubclass`, `type(v)`, `vars`, `__import__` | fenced: the dynamic object model is refused by design | fence (T0/T1 in refused-features.md) |
| `async def` functions and `await` | lowered through the coroutine lowering | native |
| `Awaitable[int]` as an annotation | fenced `PS1103` | fence |
| `dataclasses.dataclass` (no options) | lowered: a native record with fields from the annotations | native |
| `@dataclasses.dataclass(frozen=True)` and other decorators | fenced `PS1201`; under the fallback tier the class routes to the island | fence / island |

## Idioms that lower

Write the annotated form; each idiom below is verified by the probes in `scripts/support_data.toml` (the `typing` row) and the example programs.

- Annotate before use.
`counts: dict[str, int] = {}` lowers; `counts = {}` fences `PS1001` and every later read fences with it.
- Narrow optionals with `is None`, and combine the guard with the work:
`best: int | None = None` then `if best is None or v > best:` lowers, including in loops.
- Return optionals instead of sentinels: `def find(xs: list[str], t: str) -> str | None` with a `None` return, then narrow at the call site.
- Type function values with `Callable`: `c: Callable[[int], int] = lambda v: v + 1` lowers and the call through `c` is a direct native call.
- Keep generic helpers in the PEP 484 shape: `T = TypeVar("T")` at module level, then `def first(xs: list[T]) -> T`.
It monomorphizes per instantiation, including over user classes.
- Put abstract bases under the erasure contract: `class Shape(ABC)` with `@abstractmethod` stub bodies, implementations in the subclass.
No runtime enforcement exists (divergence 14), so the checker is the guard.
- Use `if TYPE_CHECKING:` for imports that only feed annotations (`collections.abc` shapes, third-party types).
The body never lowers, the checker still checks it, and the import stays free.
- Prefer `from __future__ import annotations` for self-referencing classes; the annotations then resolve without forward-reference fences.
- Read schema-like data with annotated locals: `d: dict[str, int] = json.loads(s)` and typed field pulls, instead of any runtime annotation introspection.

## What stays off the native tier

- Runtime reflection: `get_type_hints`, `__annotations__` reads, `inspect.signature`, `**kwargs` forwarding.
The fences name the typed idiom where one exists (PEP 692 `Unpack[TypedDict]` for keyword forwarding is the planned shape; today the island lane runs the dynamic form).
- Typing names as values: `cast`, `NewType`, `get_type_hints`, `Annotated` outside an annotation.
- Structural classes that need member dispatch on the protocol type: the annotation accepts the value, a member read on the protocol-typed name does not lower; read through a concrete-typed local instead.
- Constructors with runtime metadata needs (pydantic models, attrs validators, `NamedTuple` unpacking).
They are island material, not typing failures.

## How this page stays current

- Every fenced row above is or will be probed in `scripts/support_data.toml` (the `typing` row), so the drift tests (`tests/test_support_docs.py`) fail when the compiler changes and the table goes stale.
- The PR template rule "docs updated if API/ABI/behavior changed" covers this page: a diagnostic change, a new erasure or a new fence on a typing construct is a behavior change and must update the construct table in the same PR.
- Deliberate runtime differences stay in [Divergences from CPython](../reference/divergences.md) (for typing specifically: 14), and the gap ledger stays in [`data/compat/what_will_fail.json`]({{PYSC_COMPAT_URL}}/data/what_will_fail.json).
