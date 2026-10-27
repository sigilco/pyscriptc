# Writing idiomatic native Python for pyscriptc

Agent-facing companion of the docs guide: `docs/guide/typing.md` (the per-construct table with probed fences).
Read this before writing or editing Python that must compile with pyscriptc, and keep it current when diagnostics or erasures change (the PR checklist "docs updated if API/ABI/behavior changed" covers it).

## The rule set

1. Annotations drive lowering; they are not stripped.
Types are erased at compile time (no type objects, no annotation tables in the binary), but every annotation is read by the checker and the lowerer and decides the emitted code.
A missing annotation is a fence (`PS1001`), not an inferred `Any`.
2. `typing` and `typing_extensions` are annotation-only modules.
Importing them is free and they never appear in the binary.
Never use a typing name as a value: `cast(...)`, `get_type_hints(...)`, `NewType(...)`, `Annotated[...]` outside an annotation all fence with `PS2002`.
Frame it exactly that way in answers and commits: typing is how to lower, not a module that fails.
3. Annotate every binding before use.
`counts: dict[str, int] = {}` lowers, `counts = {}` fences `PS1001`.
A local first bound to a value cannot be widened later; declare the optional up front: `p: Path | None = None`.
4. Optionals are single-arm only.
`T | None`, `Optional[T]`, `Union[T, None]` lower to a tagged optional.
`A | B` fences `PS1101`, `A | B | None` fences `PS1102`; split the function per arm or pick one arm.
5. Narrow with `is None` / `is not None`, and combine with `and`, `or`, early returns and loops; it all lowers.
`isinstance` narrowing does not: `isinstance` takes scalar and class names only, never generics (`isinstance(v, list[int])` fences `PS2001`).
6. Iteration shapes are erased to list forms.
`Iterator[int]`, `Iterable[int]`, `Sequence[int]`, `Generator[int, None, None]` all lower in annotations.
Spell them from `typing` (or under `if TYPE_CHECKING:`); a plain `import collections.abc` is outside the closed import table.
7. Generic helpers use the PEP 484 spelling.
`T = TypeVar("T")` at module level plus `def first(xs: list[T]) -> T` monomorphizes per call site, over scalars and user classes alike.
PEP 695 `def f[T](...)` fences (`PS1106`).
`ParamSpec`, bound and constrained `TypeVar`s fence.
8. Abstract and structural bases erase.
`class Shape(ABC)`, `@abstractmethod`, `Protocol` bases, `@overload` stubs: the class lowers as a plain class, stub bodies are skipped, there is no runtime enforcement (divergence 14), the preflight checker is the guard.
Member reads through a protocol-typed name do not lower; read through a concrete-typed local.
`NamedTuple` and `TypedDict` class bases fence (`PS2002`); use `@dataclasses.dataclass` or a plain `dict[K, V]`.
9. `Literal` and `Self` fence.
Use a plain `str` parameter for modes and the class name (quoted only inside its own body before definition is fine for classes, but prefer `from __future__ import annotations`) for fluent returns.
`Final`, `ClassVar`, `Annotated` unwrap to their inner annotation and lower.
10. `TYPE_CHECKING` bodies never lower and their imports stay erased; use the guard for annotation-only imports.
`from __future__ import annotations` is accepted and avoids forward-reference fences (`PS1105`) for self-referencing classes.
11. Runtime introspection is island material, not a typing failure.
`get_type_hints`, `__annotations__` reads, `inspect.signature`, `**kwargs` forwarding fence with `PS2002` naming the island lane; when a script needs them, say "this routes to the island under `--fallback=cpython`", not "typing is unsupported".
12. When a fence appears, the first fence is the cause and the rest are cascades; fix the first, then re-run `pyscriptc coverage FILE`.
`pyscriptc explain <CODE>` names the typed idiom where one exists.

## Where each fact lives (do not duplicate)

- Per-construct detail with probed fences: `docs/guide/typing.md`.
- Live per-module/per-builtin status: the compat site data (`compat/data/*.json`), never markdown tables.
- Deliberate runtime differences: `docs/reference/divergences.md` (typing-relevant: 14).
- Gap ledger: `compat/data/what_will_fail.json` (generated, never hand-edited).
- Probe definitions: `scripts/support_data.toml` (the `typing` row); the drift tests fail when the compiler moves under them.
