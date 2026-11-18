# The typing quality lane: docstring types, strictness levels, and the annotation surface (follow-up to #1283, #1302)

Status: design note, 2026-11-18, session rsC. Research note, not a decision. The decisions.md row proposed at the end is what turns it into one.

Owner direction (2026-11-17): typing is a QUALITY lane, not an ADMISSION lane. Inference is on by default, the `--infer` option is dropped and no `--no-infer` flag is added, because the references do not offer one. Library blocks go away as a failure category: a codebase fails to lower only for language-wise walls (PSxxxx), never "<library> can't compile". This note takes three follow-up questions from that direction: (1) can docstring types (Sphinx, Google, numpy style) be an inference input the way Vercel scriptc uses JSDoc, (2) should pyscriptc adopt graduated strictness levels like Sorbet, Pyright and tsc, and (3) pragma versus decorator as the annotation surface. It extends `.agents/docs/design/loose-typing-and-default-inference.md` (default-on pins) and `.agents/docs/design/island-promotion-and-annotation-surface.md` (the shadow module, `@island`, `@specialize`).

## Question

Three sub-questions, one per part: (1) do any of mypy, pyright, ty or peers take types from docstrings for checking, and should pyscriptc read docstring types as an inference input, with what precedence and risk? (2) should pyscriptc adopt graduated strictness levels, spelled how, mapped to which PEPs and diagnostics, and how do levels work when typing is a quality lane that never gates admission? (3) is a comment directive or a decorator the right annotation surface, what lands in v0.11.0, and what happens to the annotation-surface issue cluster #1306 to #1309?

## Evidence

### Docstring types: what every peer actually does

Pyright: docstring-derived types exist but never reach checking. `docStringUtils.ts` parses reST, Google and Epytext formats and the parsed text is used "only for signature help"; Eric Traut declined stub generation from them in issue 432 (2019): "I find that they are frequently inaccurate and incomplete. Misleading and incorrect types are arguably worse than no types" (https://github.com/microsoft/pyright/issues/432). numpydoc is not parsed at all; issue 10995 (2025-10) was closed by routing it to Pylance, "We don't plan to back port the restructured text parser to Pyright" (https://github.com/microsoft/pyright/issues/10995). Pyright's checking input is PEP 484 annotations and PEP 561 stubs, full stop.

Mypy: the docstring machinery lives in `mypy/stubdoc.py` and `stubgenc.py`, used by `stubgen` to draft stubs, primarily for C extension modules where runtime introspection is the only source (`DocstringSignatureGenerator` reads Sphinx-style signatures and Google/Numpy-style `name : type` property lines, https://github.com/python/mypy/blob/52de0c73/mypy/stubdoc.py, https://mypy.readthedocs.io/en/stable/stubgen.html). The checker itself never consults docstrings: an unannotated def has signature `None` and the bridge measured exactly this (`.agents/docs/design/checker-bridge.md`: "Parameter types: mypy never infers unannotated parameter types; they stay Any").

Ty (Astral): no docstring typing anywhere in its docs; its adoption model is gradual `Unknown` plus per-rule severities (https://docs.astral.sh/ty/coming-from-mypy-or-pyright/).

The wider ecosystem treats docstring types as a stub-generation and documentation problem, not a checking input: docstub (scientific Python, NumPyDoc to `.pyi`, https://docstub.readthedocs.io/stable/), Griffe/mkdocstrings (parse the styles for docs rendering, https://mkdocstrings.github.io/griffe/reference/docstrings/), typewriter/typeright (insert annotations *into source* from docstrings, dmypy suggestions, then let the checker verify, https://github.com/sambvfx/typewriter). numpydoc issue 196 states the direction plainly: annotations are preferable because "type annotations, unlike docstrings types, can be programmatically checked" (https://github.com/numpy/numpydoc/issues/196).

Why the scriptc comparison fails structurally. JSDoc in the TypeScript world is a first-class gradual-typing surface: `checkJs` plus JSDoc gives every value a checked type and tsc verifies the JSDoc types themselves (`@param {number} x` is parsed by the same type system), so scriptc lowers off verified types. Python's equivalent verified surface is PEP 484 annotations plus PEP 561 stubs. Sphinx/Google/numpydoc types are prose-adjacent conventions with three competing grammars, unresolved names needing scope heuristics (Griffe resolves them against the parent scope; pyright never built it), and no verification anywhere in the toolchain. Adopting them as a lowering input would mean pyscriptc trusts types no checker has checked, which is a parity risk in the exact sense the 2026-10-25 decision guards: the written annotation is the lowering input because the checker preflight proved it (`.agents/docs/design/checker-bridge.md`, "the bridge only answers, it never forces").

Matrix read: the annotation family this would target (PS1001 locals 300 in piccolo, 121 in asyncpg, 85 in psycopg; PS1106 re-exports in sqlalchemy and pydantic) is already the target of the default-on pin engine from the loose-typing note, which derives types the checker *did* prove from call sites. Docstring types would add a second, weaker source under the same rows, and the fence data gives no row the pins do not already cover better.

### Strictness levels: the peer shapes

Sorbet: five per-file sigils `# typed: ignore/false/true/strict/strong`, file-level default `false`, a `--typed` CLI force for adoption experiments, and Stripe/Shopify ratchet adoption file by file (https://sorbet.org/docs/static, https://shopify.engineering/adopting-sorbet). Levels change which errors are reported; they never change runtime semantics.

Pyright: four named modes `off/basic/standard/strict` (`typeCheckingMode`, default `standard`), a large table of per-rule severities each defaulted per mode, per-file comments (`# pyright: strict, reportPrivateUsage=false`), and config `strict`/`basic` path lists with globs; crucially the file comment may only *raise* severity over config, never lower it, so a strict config ratchet holds (https://github.com/microsoft/pyright/blob/main/docs/configuration.md, https://github.com/microsoft/pyright/blob/main/docs/comments.md, https://github.com/microsoft/pyright/issues/4321).

Mypy: flag bundles, not named levels; `--strict` composes `--check-untyped-defs`, `--disallow-untyped-defs` and friends; per-module sections in the config file (https://mypy.readthedocs.io/en/stable/command_line.html).

Ty: no preset at all; every rule has its own `error/warn/ignore` severity, per-file overrides and suppression comments, and strictness is assembled by config (https://docs.astral.sh/ty/coming-from-mypy-or-pyright/). tsc: per-flag `strict` family (`strictNullChecks`, `noImplicitAny`, ...), `tsconfig` globs per directory.

The two orthogonal axes across all of them: (a) which *rules* fire (missing annotations, unknown types, incomplete generics) and (b) how *inference itself* behaves (pyright's `strictListInference` family, mypy's `--check-untyped-defs`, ty making them default). pyscriptc's inference behaviour is already fixed by the pin engine's soundness rule (tree-wide agreement, never overrides annotations), so levels here can only be axis (a): which diagnostics get reported and at what severity in the quality report.

PEP mapping for the levels: PEP 484 (function annotations) and PEP 526 (variable annotations) are the base contract the lowerer consumes; PEP 561 (py.typed stubs) is what the checker preflight resolves third-party types through; PEP 585 (builtin generics), PEP 604 (`X | Y`), PEP 695 (type parameter syntax) and PEP 647 (TypeGuard) are the construct forms the annotation-family fences police (PS1103 generic aliases, PS1106 re-exported forms, per `docs/guide/python-subset.md` construct table). A strictness ladder maps onto these as reporting emphasis, not new semantics.

### Annotation surface: directive versus decorator

The rsB note already landed the shape: an inert `pyscriptc._annotations` shadow module carrying `@island` (deliberate demotion, the React Compiler `"use no memo"` analog) and `@specialize` (asserts existing monomorphization), with per-module lane selection in pyproject, never in code (`.agents/docs/design/island-promotion-and-annotation-surface.md`, part 2 verdict). React Compiler's own surface splits the same way: per-function directive `"use memo"` in `annotation` compilationMode, directory scoping via Babel overrides, and the out-directive beats the mode (https://react.dev/learn/react-compiler/incremental-adoption, https://react.dev/reference/react-compiler/compilationMode).

Concrete comparison. Analysis level: a pragma is naturally per-module (parse-time, one scan of the header comments, matches the existing `# pyscriptc:` per-module pragma sketched in the loose-typing note); a decorator is naturally per-definition (it attaches to the exact function node the lowerer visits, which is what `@island` demotion needs). Stacking and composition: decorators compose with the language (`@island` under `@functools.lru_cache` is just source), pragmas do not conflict because they are file-scoped. Ease of static analysis: both are trivial for pyscriptc (the pragma is a comment scan before checking; the decorator is one name match on the decorator list). Inertness: a decorator needs the shadow module import to be a no-op without pyscriptc (the Cython.Shadow pattern, proven in the rsB note); a pragma is inert by construction but invisible to every other tool. CPython/PyPy runnability: pragma wins on zero import cost; decorator wins on IDE visibility, go-to-definition and the ability of other linters to see and check the marker. Per-function versus per-module need: pyscriptc genuinely needs both granularities, and the existing config surface (`[tool.pyscriptc.imports]` lanes, `[tool.pyscriptc]` in `src/pyscriptc/config.py`) already covers the per-module case in pyproject form.

## Options

### Option D: docstring types as an inference input

D1. Read docstring types as a pin source with precedence annotations > checker pins > docstrings. Rejected: it puts unverified prose-derived types into lowering, the thing no peer checker does (pyright's Traut quote, mypy confining them to stubgen), it builds three docstring grammars beside the checker bridge, and every row it targets is already the pin engine's target with checker-proved types. Misleading types are worse than no types precisely because a wrong pin is a wrong binary.

D2. Read docstring types only as *report suggestions* in the `pyscriptc infer` coverage report: "parameter `x` of `foo()` is unannotated; its docstring says `x : int`, consider annotating". Nothing reaches lowering. Cost is one best-effort parser over three formats with graceful absence.

D3. Off-the-shelf annotation insertion: recommend or integrate a typewriter/docstub-style tool that *writes* PEP 484 annotations into the source from docstrings, after which the existing pipeline (checker preflight, pins) verifies them. Zero compiler code; user-owned ratchet.

### Option S: graduated strictness levels as a quality lane

S1. One global flag (`--strict`). Rejected: no ratchet, no per-file adoption, and the loose-typing note already established the per-module pragma shape.

S2. Pyright shape: named levels `off/basic/standard/strict`, pyproject `[tool.pyscriptc.typing]` default plus per-glob lists, and a per-file `# pyscriptc: typed=strict` pragma that may only raise, never lower, the file's level. Levels drive only the quality report (severity and which PSxxxx diagnostics are surfaced in `assess`/`infer` output); compilation output for a given source is identical at every level.

S3. Sorbet shape only: per-file sigils without config globs. Rejected as the sole mechanism: existing users configure per-package in pyproject already, and pyright's combination is the field's converged answer (Sorbet for per-file, mypy/pyright for project config).

### Option A: annotation surface, directive versus decorator

A1. Pragma only (`# pyscriptc: ...` for everything including `@island`'s job). Rejected: per-definition demotion in a pragma means naming functions in file headers, which drifts from the code and loses IDE checking; the React `"use no memo"` analog works per-function because the directive sits *inside* the function, which Python comments do awkwardly and decorators do natively.

A2. Decorators only. Rejected for per-module concerns: module-wide lane selection as decorators on every def is noise, and pyproject already owns per-module policy.

A3. Both, split by granularity, matching the rsB verdict: decorators (`@island`, `@specialize`) for per-definition, compiler-checked markers in the shadow module; pragmas for per-module typing mode (`# pyscriptc: typed=strict`, and the existing no-infer opt-out sketch); pyproject globs for project defaults. The lowerer treats decorators as named, checked constructs (fence with a code if misused, per A0 rule b of the rsB note) and pragmas as config.

## Recommendation

### Docstring types (Option D)

Do now: nothing in the compiler. Gate on evidence: D2, the report-suggestion lane, only if the default-on pin rollout's coverage report shows real PS1001 mass in docstring-heavy trees (numpy/scipy-style scientific code) that the pins leave unpinned. Reject: D1, docstring types as a lowering input, permanently, with the Traut rationale recorded in the note. D3 costs nothing and can be mentioned in `docs/guide/python-subset.md` alongside the annotation guidance if the typing guide gains an adoption section; not required for v0.11.0.

Precedence stays exactly as the loose-typing note set it: written annotation, then checker-proven pin, then the fence. There is no third source.

### Strictness levels (Option S2)

Do now, staged, all quality-lane by construction:

1. Define the four levels in the typing config (`[tool.pyscriptc.typing] mode = "standard"`, default `standard`) as a table of which existing PSxxxx diagnostics surface in the quality report and at what severity: `off` suppresses typing reports, `basic` reports missing annotations (PS1001 family) as warnings, `standard` reports the annotation family as warnings plus coverage numbers, `strict` adds the construct-form family (PS1103/1104/1105/1106) and reports everything as errors in `assess` output. Levels never change what compiles, what lowers, or exit codes of `compile`.
2. Per-file pragma `# pyscriptc: typed=strict` that may only raise the level over config (the pyright ratchet rule, issue 4321), and per-glob lists (`typing.strict = ["src/**"]`) in pyproject. Both, because the two existing mechanisms (config lanes in pyproject, per-module pragma in the loose-typing note) already prove each half.
3. The `pyscriptc infer` coverage report (stage 1 of the loose-typing rollout) and `pyscriptc assess` are the consumers: levels configure their output, and the Sorbet-style ratchet story is "raise globs file by file, the report shows the split".

Map documented in the typing guide's construct-table area: `off/basic/standard/strict` against the PEP 484/526 base contract, PEP 561 stub resolution, and the PEP 585/604/647/695 construct forms, so a level's meaning is "which PEP-construct reports fire", never new syntax support. PEP 695 and ParamSpec support itself remains PS1106/PS2003 fence territory regardless of level; a strict level reports the fence more loudly, it does not lower it.

Gate on evidence: a fifth level (`strong`, Sorbet-style, meaning "no gradual escape: no Any-sourced pins accepted") only if users ask for an audit mode after the default-on rollout; it has no lowering meaning either. Reject: any level that changes admission, including resurrecting an annotations-required admission mode as a "strict" compile behaviour.

### Annotation surface (Option A3)

Do now: confirm the rsB four-PR order and add the pragma to the third and fourth PRs: (1) the inert `pyscriptc._annotations` shadow module; (2) `@island` demotion notes; (3) `--fallback-island-imports` plus the per-module lane table, which gains the `# pyscriptc: typed=...` pragma parse; (4) `@specialize` plus the specialization report, which gains the strictness level wiring from Option S2 step 1 if it fits, else levels are a fifth small PR.

Rules that keep the surface coherent: decorators are for what the compiler *does* to a definition (route it, monomorphize it); pragmas are for how the *quality lane reports* a module; pyproject is for project defaults and package lanes. Nothing runtime-visible is ever added to either surface (the shadow module stays inert; pragmas are comments). The `--infer` drop and the no-`--no-infer` owner direction are untouched by all of this: the pragma family carries typing *report* mode, never inference opt-out.

### What happens to #1306 to #1309

No GitHub access from this host, so this is a disposition to verify when the laptop files/moves them: the cluster is the annotation-surface follow-up from PR 1302. Keep the decorator issues (#1306-#1307, `@island` notes and the shadow module) scoped per-definition exactly as written. Amend whichever issue proposes module-wide typing behaviour to use the pragma plus pyproject globs from Option S2 rather than a decorator, and whichever proposes a pragma for per-definition routing to the decorator instead. Nothing in the cluster is closed by this note; each closes with its own PR per the rsB ordering. If the cluster does not yet exist as four issues, file it as: shadow module, `@island` notes, lane table plus pragma, `@specialize` plus report and levels.

### Proposed decisions.md row (not appended)

| Date | Decision | Why (one line) | Rejected alternatives | Detail |
| --- | --- | --- | --- | --- |
| 2026-11-18 | Typing stays a quality lane: docstring types are never a lowering input (report suggestions at most, gated), strictness is four report-only levels (`off/basic/standard/strict`) driven by pyproject globs plus a raise-only per-file pragma, and the annotation surface stays split (decorators per definition, pragma per module) | No peer checker feeds docstring types into checking and unverified types in lowering would break the annotation-is-the-input contract, while report-only levels give the Sorbet/Pyright adoption ratchet with zero admission coupling | Docstring pins with precedence under checker pins (unverified input, Traut: misleading types are worse than none), a single global `--strict` flag, pragma-only or decorator-only surfaces, any level that changes what compiles | `.agents/docs/design/typing-quality-lane.md` |

## Issues to file

- Strictness levels: `[tool.pyscriptc.typing] mode` plus per-glob lists plus the raise-only `# pyscriptc: typed=` pragma, driving only `assess`/`infer` report output (docs + config + report wiring, docs-only adjacent, milestone v0.11.0).
- The typing guide paragraph mapping levels to PEP 484/526/561 and the PS1103/1104/1105/1106 construct forms, per the documentation policy (update in the same PR as the levels land).
- Gate ticket, not scheduled: docstring report suggestions in `pyscriptc infer`, opened with the numpy/scipy-style tree evidence requirement attached, blocked until the pin rollout's coverage report shows unpinned PS1001 mass in docstring-heavy trees.
- Verify the #1306 to #1309 cluster dispositions above when GitHub access is available (laptop task).

## Related

- `.agents/docs/design/loose-typing-and-default-inference.md`: the default-on pin engine, the per-module pragma sketch, and the staged rollout this note's levels ride on.
- `.agents/docs/design/island-promotion-and-annotation-surface.md`: the shadow module, `@island`, `@specialize`, and the pyproject-only per-module rule this note's surface split confirms.
- `.agents/docs/design/checker-bridge.md`: the bridge-only-answers rule and the measured mypy limits that anchor the docstring rejection.
- `docs/guide/python-subset.md`: the construct table and PSxxxx codes the levels report on.
- Sources: https://github.com/microsoft/pyright/issues/432, https://github.com/microsoft/pyright/issues/10995, https://github.com/microsoft/pyright/blob/main/docs/configuration.md, https://github.com/microsoft/pyright/blob/main/docs/comments.md, https://github.com/python/mypy/blob/52de0c73/mypy/stubdoc.py, https://mypy.readthedocs.io/en/stable/stubgen.html, https://docs.astral.sh/ty/coming-from-mypy-or-pyright/, https://sorbet.org/docs/static, https://shopify.engineering/adopting-sorbet, https://react.dev/learn/react-compiler/incremental-adoption, https://docstub.readthedocs.io/stable/, https://github.com/sambvfx/typewriter, https://github.com/numpy/numpydoc/issues/196, https://mkdocstrings.github.io/griffe/reference/docstrings/
