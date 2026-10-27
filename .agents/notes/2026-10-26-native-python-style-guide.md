# Native Python style guide: assessment

Date: 2026-10-26. Scope: the owner request "assess whether we should write a guideline / style guide for writing idiomatic native Python" (humans on the docs site, agents through `.md` files and `llms.txt`), plus two side questions: how much of the typing surface can be adopted long term, and where the "typing is not supported" framing should be corrected.

Companion of the typed-subset positioning (`.agents/plans/2026-10-25-typed-subset-positioning.md`): that plan fixes the scope (typed subset, island floor, native ceiling, introspection discipline), this note fixes the per-construct contract and the guide that carries it.

## Findings

### The framing problem is real but narrower than the request assumed

There is no page, table row or diagnostic that says "typing is not supported". The wrong framing lives in four places, all of them data or prose:

1. `scripts/support_data.toml` (the `typing` row note): "annotation-only: imports are free, values are checked, there is no runtime surface".
2. The generated rows that note feeds: `compat/data/support.json` and `compat/data/compat.json` (`stdlib.typing`, status `none`, so the compat site rendered `typing` as fully unsupported).
3. `src/pyscriptc/imports.py` (the `pyscriptc imports` verdict reason): "annotation-only module; the checker keeps it and the lowering erases it" (accurate but reads like a limitation).
4. The diagnostics wording: `PS1104` said the dynamic types "describe types the subset cannot represent natively", and the alias fences said "is not a value type in the IR", which reads as "this typing form does not lower" instead of "this form has no slot in the IR".

Everything else is already framed correctly: `src/pyscriptc/reflection.py` names `typing.get_type_hints` as runtime reflection and points at the island lane, `src/pyscriptc/imports.py` classifies `typing` as a native lane import, `docs/guide/island-floor.md` explains the `TypeVar` demotion refusal, and the compiler actually erases the import and lowers the annotations.

The important correction is the concept, not the strings: annotations are erased at compile time, but the type information is the input that decides the emitted code (`Optional[int]` is the instruction to build a tagged optional). A typing construct that has no IR slot is not "typing is unsupported", it is "this annotation has no representation yet". Constructs with runtime meaning beyond the annotation (`Annotated` metadata consumed at run time, `get_type_hints`, `TypedDict` validation) are runtime introspection, which the positioning plan (decision 7) already assigns to the island lane.

### Per-construct probes (native tier, `uv run pyscriptc compile`)

Each row was probed on 2026-10-26 with a tiny script through the compiler (native tier) and, where the construct is a runtime one, with `--fallback=cpython`.

| Construct | Result today | Fence / note |
| --- | --- | --- |
| `list[int]`, `dict[str, int]`, `set[int]`, `tuple[int, str]` | lowered | element layout from the generics |
| `T \| None`, `Optional[T]`, `Union[T, None]` | lowered | tagged optional, `is None` narrowing lowers |
| `A \| B` (two arms) | fenced | `PS1101` (no tagged union in the IR) |
| `Union[A, B, None]` | fenced | `PS1102` |
| `Final`, `Final[int]` | unwrapped and lowered | checker enforces no reassignment |
| `Annotated[int, "mm"]` | first argument lowered, rest erased | module-level `Id = Annotated[int, "pk"]` fences `PS2002` (value) |
| `Callable[[int], int]` | lowered (functions and lambdas) | bare `Callable` fences `PS1104` |
| `Iterator[int]`, `Iterable[int]`, `Sequence[int]`, `Generator[int, None, None]` | lowered | erased to the list / iterator shape |
| `Iterator` from `collections.abc` | lowered only under `if TYPE_CHECKING:` | plain `import collections.abc` is outside the closed table (`PS2002`) |
| `TypeVar("T")` + `def f(xs: list[T]) -> T` | lowered, monomorphized | also over user classes |
| PEP 695 `def f[T]` / `class C[T]` | fenced | `PS1106` |
| `TypeVar` with `bound=` or constraints | fenced | `PS1106` |
| `ParamSpec` | fenced | `PS2002` (value) plus `PS1001` (Callable form) |
| `Protocol` | base and stub members erase; a protocol-typed parameter accepts a class whose bases name the protocol | member read through the protocol type fences `PS2001`; `isinstance` on a protocol fences |
| `ABC`, `ABCMeta`, `@abstractmethod` | erased | divergence 14 (no runtime enforcement) |
| `@overload` | erased, only the implementation lowers | divergence 14; pyrefly checks the set |
| `NamedTuple` | fenced | `PS2002` (class base) |
| `TypedDict` | fenced | `PS2002` (class base); the PEP 692 `Unpack[TypedDict]` idiom named by diagnostics is not implemented yet |
| `NewType` | fenced | `PS2002` (value) |
| `Self` | fenced | `PS1103` |
| `ClassVar[T]` | unwrapped like `Final` | field rules unchanged |
| `Literal[...]` | fenced | `PS1103` |
| `TYPE_CHECKING` guard | folded, body skipped | imports under the guard stay erased |
| `cast(t, v)` | fenced | `PS2002` (value) |
| `Any`, `object` | fenced | `PS1104` |
| `from __future__ import annotations` | accepted | avoids forward-reference fences for self-referencing classes |
| quoted annotations | fenced | `PS1105` (a self-reference by plain name needs no quotes) |
| `get_type_hints`, `__annotations__` | fenced | `PS2002`, names the island lane; under `--fallback=cpython` the definition runs island-side |
| `isinstance(v, list[int])` | fenced | `PS2001` |
| `async def` / `await` | lowered | coroutine lowering |
| `Awaitable[int]` | fenced | `PS1103` |
| `@dataclasses.dataclass` | lowered | native record |
| `@dataclasses.dataclass(frozen=True)` | fenced | `PS1201`; island under the fallback tier |

Trimmed command outputs:

```
$ uv run pyscriptc compile /tmp/probes/opt.py --run --call main
1
$ uv run pyscriptc coverage /tmp/probes/union2.py
/tmp/probes/union2.py:3:10: PS1101: union types are not in the IR
$ uv run pyscriptc coverage /tmp/probes/lit.py
/tmp/probes/lit.py:5:13: PS1103: Literal is not a value type in the IR
$ uv run pyscriptc coverage /tmp/probes/getth.py
/tmp/probes/getth.py:7:16: PS2002: runtime reflection (typing.get_type_hints) resolves a function's or class's
annotations to types at run time, and native code keeps no annotation table and no type objects; this code
routes to the island lane (requires libpython); typed idiom: pass the concrete types explicitly where they
are needed
$ uv run pyscriptc compile /tmp/probes/gt_fb2.py --fallback=cpython --run --call main
2
```

### Long-term adoption ceiling

Typing features split into three classes, and only the third one is hard.

1. Erase-and-lower (already native, or a small extension away): `Final`, `ClassVar`, `Annotated` (metadata ignored), `Required`/`NotRequired` unwrapping, `TypeAlias` and PEP 695 `type X = ...` as an alias, PEP 695 type parameters (`def f[T]`, `class C[T]`) as monomorphization, `Literal` as a compile-time constant, `NewType` as an alias, `Self` as the owner class, `TypedDict` as a `dict[K, V]` shape, `NamedTuple` as a tuple shape.
Effort: S to M each, mostly in the annotation resolver and the IR, not in the emitter.
Release tier: v0.7.0 for the alias and type-parameter forms (they unblock real code in the corpus), v0.8.0 for the rest.

2. Needs a runtime representation (island, or a documented compile-time-only subset): `Annotated` metadata that a library consumes at run time (pydantic validators, FastAPI dependency injection), `get_type_hints`, `__annotations__` reads, `TypedDict` validation at run time, `Literal` narrowing driven by a runtime value.
These are T1 by the positioning plan: they run on the island, and the native lane uses an explicit substitute (pass the concrete types, or a compile-time descriptor per framework, which is a Future option, not a v0.8.0 promise).
Effort: the island path exists; the "compile-time-only subset" needs a written contract (which is what the guide provides).

3. Hard, and honestly out of reach for the native lane soon: multi-arm unions (`A | B`) and tagged unions in the IR (the IR has no union node), generic classes with runtime type parameters, `ParamSpec` (needs a callable shape in the IR), `Self` in a return position with subclass-precise typing (needs a self-type in the IR), `Literal` in a switch (needs a compile-time dispatch decision over a runtime value).
Effort: L to XL each, each one an IR change with a canonicity and IR-hash consequence.
Release tier: Future (the `Future` milestone), with the union work tracked by the erased-annotation ticket (#412) and the `collections.abc`/TypeVar work by #413.

A pydantic-style or FastAPI-style framework never becomes native under this ceiling, and the plan already says so (decision 7): those packages are island material.

### Decision

Write the guide, in two places, with one source of truth for the facts.

- Human page: `docs/guide/typing.md`, titled "Typing and idioms", in the Guide section of the docs site (it is a how-to, not a divergence list, so it belongs under `docs/guide/`, not `docs/reference/`).
- Agent page: `.agents/docs/python-style.md` under `.agents/docs/`, the rule set an agent reads before writing Python for the native tier, linked from `AGENTS.md` (one line, so the file does not grow).
- Machine source of truth: the `typing` row of `scripts/support_data.toml`, with one `ok` probe per construct that lowers and one `no` probe per construct that fences, each with the expected code. `scripts/gen_support_table.py` and `scripts/build_compat_json.py` consume it, and `tests/test_support_docs.py` fails when the compiler moves under it, so the table cannot rot.
- `llms.txt` picks the guide up for free: docmd's bundled `llms` plugin writes `llms.txt` and `llms-full.txt` for every page under `docs/`, and `scripts/gen_llms_txt.py` only appends the public data files on top. A new page under `docs/guide/` is listed without touching that script. `tests/test_llms_txt.py` checks that every page appears (it skips when the site is not built).

How it stays living:

1. The PR checklist rule ("docs updated if API/ABI/behavior changed") already covers it: a change to a typing diagnostic, an erasure or a new fence on a typing construct is a behavior change, and the construct table moves in the same PR. `AGENTS.md` now says so explicitly for typing.
2. The probe row is the mechanical half: a construct that starts or stops lowering flips the `support_data.toml` row, the drift test fails, and the guide row follows from the same probe.
3. The doc-cadence inventory (`.agents/docs/doc-cadence.md`) lists the page, so the release gate knows it is per-version documentation and not a nightly metric.

Repo doc policy respected: the page states what the compiler does with each construct and links every refusal to its `PSxxxx` code and to the numbered divergences (14 for the ABC/Protocol/overload erasure), instead of listing features that work. Live per-module status stays on the compat site.

### What changed in this work

- `docs/guide/typing.md` (new): the construct table, the idioms that lower, the constructs that stay off the native tier, and the freshness rules.
- `.agents/docs/python-style.md` (new) and one line in `AGENTS.md`.
- `scripts/support_data.toml`: the `typing` row now carries the measured probes (16 `ok`, 18 `no`) and the note "annotation-only: imports are free, annotations are the lowering input, erased before emission; typing names used as values fence".
- `compat/data/support.json` and `compat/data/compat.json`: regenerated from the source above, never hand-edited. The `typing` row moves from `none` to `partial` with its measured fence codes.
- `src/pyscriptc/diagnostics.py` (`PS1104`), `src/pyscriptc/lower/_classes.py` (the alias fences), `src/pyscriptc/lower/_stmt.py` (the PEP 695 `type` statement fence), `src/pyscriptc/imports.py` (the `typing` verdict reason) and `src/pyscriptc/coverage.py` (the `typing-value` family): wording now says "not a lowered type in the IR" and "typing names steer the lowering", instead of implying the module is unsupported.
- `docs/guide/support-model.md`, `docs/guide/island-floor.md`, `docs/reference/first-class-scripts.md`, `docs/index.md`: the pointer lines and the corrected `TypeVar` demotion sentence.
- `docs/.docmd/docmd.config.js`: the page in the site navigation.

### Proposed issues (not filed here)

1. "typing: lower PEP 695 type aliases and type parameters" (v0.7.0, M): `type X = ...`, `def f[T]`, `class C[T]`. Unblocks the corpus scripts that use the PEP 695 spellings; the PEP 484 `TypeVar` form already lowers.
2. "typing: lower `Literal`, `Self`, `NewType` and `TypeAlias` as compile-time forms" (v0.8.0, M): each is an alias or a constant over an already lowered type.
3. "typing: `TypedDict` and `NamedTuple` as native record shapes" (v0.8.0, L): `dict[K, V]` and tuple shapes; also the precondition for the PEP 692 `Unpack[TypedDict]` kwargs idiom the diagnostics already name.
4. "typing: erase `Annotated` metadata into a compile-time descriptor" (Future, L): the escape for pydantic-style consumers, per positioning decision 7.1; island until then.
5. "IR: tagged unions for multi-arm annotations" (Future, XL): `PS1101` and `PS1102` are IR work, not typing work. Related: #412 (erased annotations).
6. "typing: `Self` return positions and `ParamSpec`" (Future, L): each needs a new IR shape.
7. "docs: generate the typing construct table from the probe row" (v0.8.0, S): the table is written by hand today; a generator that renders the `ok`/`no` rows into the page would remove the last manual sync step.

## Reproduction

Every claim in the table comes from `uv run pyscriptc compile FILE --run --call main` and `uv run pyscriptc coverage FILE` on 2026-10-26, against the worktree at `~/.worktrees/pyscriptc-styleguide` (branch `work/styleguide`, base `origin/master` at `10926443`).
The same probes live in `scripts/support_data.toml` (the `typing` row) and are re-checked by `tests/test_support_docs.py`.
