# Loose typing and default inference in pyscriptc (issue #1283)

Status: design note, 2026-11-18. Not a decision. The decision row proposed at the end is what turns it into one.

This note answers two related questions raised by the wider-gains goal for typed Python code: (1) should there be an explicit opt-in flag that admits loosely typed codebases, and (2) can the annotations-required rule (PS1001) be relaxed to inference by default so the `--infer` flag can be dropped. It compares what mypy, Pyright, mypyc, Cython, Codon, Nuitka, Vercel scriptc, Static Hermes and Sorbet do, and it reads every claim against the compat matrix from the 2026-11-18 nine-tree report (`/tmp/compat_matrix.csv`, `/tmp/pyscriptc_native_compat_report.md`).

The short answer: the compiler already has the opt-in mechanism (`--infer`, package-wide call-site pins from `pyscriptc infer`), and the right move is not a second "loose mode" but a staged default-on rollout of the existing pins with diagnostics, an opt-out flag, and a per-module pragma. A Codon-style whole-program Hindley-Milner inference arm is rejected: it would move the semantics away from "the written annotation is the lowering input" and it would not close the fences that actually dominate the matrix (PS2002 class-base refusals are 40 to 64 percent of fence mass in every tree, which no typing flag touches).

## Current behaviour

What exists today, from the CLI (`src/pyscriptc/cli/_main.py`):

- Default: annotations are required. A parameter, return or local without one fences PS1001, and every later read of the name fences PS2002 because of it (`docs/guide/python-subset.md`, "the annotations are the input the compiler works from"). The checker preflight (pyrefly by default) checks the annotations first, then the lowerer reads them.
- `--bridge mypy|pyrefly`: feeds the checker's per-module inferred-type map to the compiler so unannotated code compiles. Per-module facts; no package agreement requirement.
- `--infer` on `compile`: runs the `pyscriptc infer` engine (`src/pyscriptc/inferrunner.py`) before lowering. It drives the checker over the whole package tree, reduces call-site argument types to pins, and pins a parameter only when every call site in the tree agrees and the type is one the subset can express. A pinned copy of each module is then re-checked so derived return and local types agree with what the lowerer applies. The runner does up to four pyrefly passes to a fixpoint, bounded so a non-converging tree cannot hang.
- `pyscriptc infer` standalone: runs the same engine and prints a report (`--json`) or writes a sidecar of inferred annotations (`--write-annotations`) the user can apply.

The pins are a package-wide fact, deliberately stricter than a per-module bridge: one call site that disagrees with all the others leaves the parameter unpinned and the PS1001 fence stands. That is the soundness rule this note keeps everywhere below.

## What the fence data says inference can and cannot fix

The compat matrix (nine trees, 17,997 fence events) splits into three families:

- PS2002 mass ("this class base is not lowered", closed-table refusals): 264 to 5,921 events per tree, 38 to 64 percent of each tree's fences. No typing flag changes these; they are the cross-runtime class wall (report chapter 5, change N1).
- The annotation family (PS1001 plus PS1103/1104/1105/1106): roughly 3,089 fences in SQLAlchemy, 1,058 in pydantic, 540 in Piccolo, 411 in asyncpg. This is what inference targets: unannotated locals (PS1001), generic aliases (PS1103), structural protocols (PS1104), forward references (PS1105), re-exported `Self`/`Unpack` (PS1106).
- Everything else (PS1203 headers, PS2003 kwargs, PS2001 surfaces): orthogonal to typing.

So the honest ceiling for this work is the second family, named rows: `sqlalchemy` (PS1001 1,327, PS1106 700), `pydantic` (PS1106 396, PS1103 269), `piccolo` (PS1001 300), `asyncpg` (PS1001 121), `psycopg` (PS1001 85), `fastapi` and `starlette` (PS1106 85 and 112). The `pg0` row (6 PS1001) is already near zero and `sqlmodel` shows the mixed picture (31 PS1103 generic-alias fences that inference alone does not fix). Named rows that inference will not move: every row's PS2002 and PS2003 columns.

The `--bridge`/`--infer` behaviour matters most for trees where the annotation family dominates the movable mass: sqlalchemy first, then piccolo and asyncpg where annotations are a larger share of fences than in pydantic.

## How peers handle the annotations-required question

One sentence each, with sources. The pattern across every checker and every compiler is the same: refuse-by-default is nowhere; the axis is which body gets checked and whether missing annotations are an error, an inferred type, or an explicit dynamic.

Mypy: gradual typing by design. Untyped function bodies are not checked by default; `--check-untyped-defs` checks bodies without demanding annotations; `--disallow-untyped-defs` demands annotations; `--strict` bundles the demanding flags. https://mypy.readthedocs.io/en/stable/command_line.html#untyped-definitions-and-calls and https://mypy.readthedocs.io/en/stable/getting_started.html

Pyright: four named strictness levels (`off`, `basic`, `standard`, `strict`) with a big table of per-rule severities, plus strict-vs-loose inference knobs (`strictListInference` and friends default false). https://github.com/microsoft/pyright/blob/main/docs/configuration.md and the cross-checker framing in https://docs.astral.sh/ty/coming-from-mypy-or-pyright/

mypyc: the closest compiler peer. It compiles with mypy's inference; "variable type annotations are not needed for optimal performance", but parameter and return annotations are effectively required for specialized types, `Any` and other erased types fall back to generic CPython-style operations (the loose-but-slow lane), and it enforces non-erased annotations at runtime with TypeError. https://mypyc.readthedocs.io/en/stable/introduction.html and https://mypyc.readthedocs.io/en/stable/using_type_annotations.html

Cython: three modes layered. Pure-Python mode reads PEP 484 annotations as types and can turn that off per function or block (`annotation_typing` directive), plain `.pyx` infers locals and temporaries with "safe inference" by default (no C integer inference through arithmetic, to avoid overflow) and `infer_types` enables the aggressive version. Loose code always compiles as Python objects; annotations buy speed, never admission. https://docs.cython.org/en/latest/src/tutorial/pure.html and https://docs.cython.org/en/latest/src/userguide/language_basics.html#type-inference

Codon: the outlier. A modified Hindley-Milner checker infers types with annotations "often optional", monomorphizes unannotated functions per call site, and its semantics deliberately deviate from CPython (`int` is i64, no runtime reflection), so it is a different language that borrows Python syntax. https://docs.exaloop.io/language/overview/ and https://docs.exaloop.io/developers/compilation/ and the CC'23 paper https://cap.csail.mit.edu/sites/default/files/research-pdfs/Codon-%20A%20Compiler%20for%20High-Performance%20Pythonic%20Applications%20and%20DSLs.pdf

Nuitka: not in this axis at all. It compiles full CPython semantics to C and treats type hints as "just hints, not reliable" (Kay Hayen, 2018, https://nuitka.net/posts/nuitka-this-week-1.html); the 2.5 dual-types work derives likely runtime types for optimization only, never for admission (https://nuitka.net/posts/nuitka-release-25.html).

Vercel scriptc (the structural role model, TypeScript not Python): the TS compiler type-checks the program with the user's `tsconfig.json`, and scriptc uses the checked types and narrowing to lower. "No annotations or language extensions are required" is not inference by scriptc: it reuses the existing checker contract, and untyped JS is admitted only through `checkJs`/JSDoc so every value still has a checked type. This is exactly the pyscriptc shape: keep the annotations-required contract, get coverage from the checker's own inference arm. https://scriptc.dev/docs and https://scriptc.dev/docs/how-it-works

Static Hermes: typed and untyped JS coexist by rule, not by inference across the boundary. Untyped functions are called without typechecks; typed code infers inside itself (declarations from initializers, `-typed` infers `var x = [1,2,3]` as `number[]`), and cross-boundary calls insert checked casts. The maintainer explicitly declines to infer-and-enforce types onto untyped code: "We can't be sure of the intent of a programmer who thought they were writing untyped JS". https://github.com/facebook/hermes/issues/1349 and https://cdn.jsdelivr.net/gh/facebook/hermes@static_h/doc/TypedLanguage.md

Sorbet: the per-file precedent for the staged rollout. Five `# typed:` sigils from `ignore` to `strong`, file-level default `false`, `--typed` to force a level for adoption experiments, and Stripe/Shopify ratchet stories built on the granularity. https://sorbet.org/docs/static and https://shopify.engineering/adopting-sorbet

The lesson for pyscriptc: every peer that stays compatible with its dynamic host (mypyc, Cython, Nuitka, scriptc, Static Hermes) treats loose code as a quality lane, not an admission lane: the loose parts run slower, untyped, or behind checked casts, and none of them makes missing annotations a compile error, but none of them lets the lowerer guess across an untyped boundary either. Codon is the only one that infers its way to admission, and it pays by leaving CPython semantics.

## Option A: an explicit opt-in loose-typing flag

What it is: a flag (say `--loose-typing`, or a pyproject `[tool.pyscriptc.typing] loose = true`) that admits a codebase with unannotated parameters, locals and attributes by running the pin engine and routing what stays unpinned to a defined lane instead of fencing PS1001.

What peers do: nothing ships a single "loose mode" flag; mypy/Pyright express it as flag bundles and named levels (URLs above), Sorbet as per-file sigils. The loose lane in compilers is always defined semantics: mypyc's erased types, Cython's Python-object fallback, Static Hermes's untyped functions.

The key design question is what happens to an unpinned construct inside the flag. Two choices:

- Demote to the island (definition granularity, the assess demotion hints the report's N1 names). This keeps every emitted native definition sound: the native part only ever contains code the pins proved. Cost rides the existing island machinery, but it is boxed code and the boxed ledger is a live concern (code 5.4 percent, drop-flag TRUE as of 2026-11-09 with an acknowledged owner decision).
- Demote to a dynamic tagged value in native code (an `object`-shaped slot). This avoids island growth but reintroduces per-operation dynamic dispatch in emitted code, which is exactly the "guarded speculation" shape the 2026-10-25 typed-subset decision dropped, and it degrades silently: a `dict` read that the pins did not prove becomes interpreter-shaped work inside a "native" function.

Peers argue for the first. mypyc's erased types keep CPython-generic operations and say so; Static Hermes crosses to untyped with a checked cast; nobody fakes dynamism inside sound native code.

Effect on the compat matrix: the annotation-family columns of `sqlalchemy`, `pydantic`, `piccolo`, `asyncpg` (the rows named above) collapse from fences to island-routed definitions, so the fence counts fall but the native share does not rise unless the pins cover the definitions. It is an honest-mode win (compile succeeds, mixed speed), not a native-share win.

Cost: the flag and config plumbing is small; the honest cost is the island-side definition routing, which overlaps N1 (cross-runtime class interop, "Large" in the report's ranking). If N1 lands first, this option becomes mostly a policy default rather than new lowering work.

Maintenance: a second admission mode must keep its soundness rule documented (what is pinned, what is demoted) and every divergence it introduces needs a ledger entry per the differential probe rule.

Risk to the CPython/PyPy-compatible-source invariant: low for the source (the source still runs on CPython; nothing is added to it), medium for the contract: a binary where half the definitions are island must be clearly reported as such, or users read "compiled" where they got interpreter speed. The `assess` plane map already exists to say it.

ROI verdict: gate on evidence. Not a standalone do-now: its value is conditional on N1's demotion machinery and it is dominated by Option B's cheaper shape. Do it only if, after B lands, the `pyscriptc infer` report shows a large population of definitions the pins cannot prove but users still want admitted.

First PR-sized step: none as a flag. The evidence-gathering step is the one under Option B: ship the `pyscriptc infer` coverage report so the pin-rate per tree is a number, not a guess.

## Option B: inference by default, `--infer` dropped

What it is: make the pins run by default on every compile, so an unannotated parameter whose call sites all agree lowers without `--infer`, and `--no-infer` (plus a per-module pragma) opts out. `--infer` remains as a deprecated alias for one release.

What peers do: scriptc is the direct precedent (checked types from the user's checker are the lowering input, no flag to opt into types at all, https://scriptc.dev/docs/how-it-works); mypyc never had an inference flag because mypy's inference is the pipeline (https://mypyc.readthedocs.io/en/stable/introduction.html); Codon infers by default but pays with semantics (https://docs.exaloop.io/language/overview/). Among checkers, Pyright's default is `standard` strictness and its loose-inference knobs default loose (https://github.com/microsoft/pyright/blob/main/docs/configuration.md): defaults are set for the common user, strictness is opt-in. That is the direction this option moves pyscriptc: today the default is the strictest admission rule and the helpful arm is opt-in.

Why the pins are safe to default on. The existing soundness rule is the whole argument: a parameter is pinned only when every call site in the tree proves the same concrete type the subset can express (`src/pyscriptc/inferrunner.py`). Default-on cannot lower a function the annotation path would have lowered differently, because the pins never override a written annotation; they only fill the PS1001 gap. The failure mode of a wrong default is the PS1001 fence standing (safe) or, in the worst case, a pin that a later call site violates, which is a runtime TypeError-shaped divergence and must be a named ledger entry if it is ever observed. Static Hermes makes the same trade the other direction (untyped callers of typed functions get checked casts, https://cdn.jsdelivr.net/gh/facebook/hermes@static_h/doc/TypedLanguage.md); the pin engine's tree-wide agreement is a stronger guard than that.

Default-on risks, named:

- Compile-time cost. The engine starts one checker server, opens every module, and runs up to four fixpoint passes (`inferrunner.py`). On a tree like sqlalchemy (9,021 imports, per the matrix) this is real build time added to every compile, including compiles that needed no inference at all. Mitigation: skip the engine entirely when the tree has no unpinned parameters (the common case for pyscriptc-native code, and cheap to detect in one parse pass), and cache facts keyed on tree content hash the way the compat2 cache does.
- Non-determinism of pin sets. The pins depend on which modules are in the tree and which call sites exist, so adding a caller can change an unannotated callee's lowered type. That is a recompilation-contract problem (two builds of the same file can differ by tree) and it must be surfaced: the compile should record the pins it applied in the summary/debug output, and a pin-driven lowering change should be visible in `--debug` stages. Cython had a real bug class from inference depending on set ordering (changelog 2026, issue #8002, https://docs.cython.org/en/latest/src/changes.html); a deterministic pin reduction (sort everything) is mandatory from day one.
- Wider acceptance surface means wider blame surface. A wrong pin is a wrong binary. The report-and-diagnose path (the coverage report below) is not optional; it ships with the default.
- Checker-bridge coupling. The pins come from pyrefly today. Default-on makes pyrefly a hard dependency of every compile unless `--no-infer` or the bridge already handles absence; the degraded path must be the fence, never a guessed pin.

Effect on the compat matrix: directly closes the PS1001 columns of the named rows (`sqlalchemy` 1,327, `piccolo` 300, `asyncpg` 121, `psycopg` 85, `pg0` 6) where the call sites agree, and it chips the derived PS2002s that follow from a PS1001 ("every later read of the name stops with PS2002 because of it", `docs/guide/python-subset.md`). It does not close PS1103/1104/1105/1106 (those are annotation-form refusals, N4's territory) and it does not touch PS2002 class-base mass.

Cost: small to medium. The engine and flag exist; the work is the skip-fast-path, the caching, the deterministic reduction audit, the diagnostics, and the docs (the typing guide's "annotations are required" paragraph becomes "annotations or provable call-site types", and the construct table gains a PS1001 row change per the documentation policy).

Maintenance: one more thing every compile does; the skip path must be tested so the common annotated case never pays.

Risk to the invariant: low for source compatibility (source is unchanged; anything that compiled before compiles the same, because pins never override annotations). Low for CPython/PyPy runnability, which is the differentiator vs Codon and which scriptc demonstrates on the TS side. Medium for build determinism (mitigated as above).

ROI verdict: do now, staged (below). It is the highest-leverage move in this note: it converts the existing opt-in engine into the default with guardrails, and its ceiling is a measured chunk of the annotation family.

First PR-sized step: the diagnostics half, before any default flips. Ship a `pyscriptc infer` coverage report (pin rate per module, unpinned-parameter count, per-tree totals) as the measurement instrument, so the staged rollout's each stage is a number. Then flip the default behind `--no-infer` with the skip-fast-path and the pin record in `summary.json`. Two small PRs, each independently revertable.

## Option C: whole-program HM inference (Codon-style)

What it is: a real inference arm in the compiler that types unannotated code from usage (Hindley-Milner or a bidirectional subset) instead of from call-site pins, so `def f(x): return x + 1` lowers natively with no annotation and no caller.

What peers do: Codon exactly this, with semantics that leave CPython (https://docs.exaloop.io/developers/compilation/). Nobody with a CPython-parity contract does it: mypyc leans on mypy's gradual checker, scriptc leans on tsc, Static Hermes refuses the idea explicitly for untyped code (https://github.com/facebook/hermes/issues/1349), Nuitka calls hints unreliable (https://nuitka.net/posts/nuitka-this-week-1.html).

Why it is rejected here, on the note's own framing from the JIT discussion (`.agents/docs/design/jit-and-codon-annotations.md`): the 2026-10-25 decision's soundness guarantee is "refusal-by-default"; HM admission replaces "the written annotation proves the type" with "the inferred type proves the type", and inferred types on dynamic Python are exactly where intent is unknowable (Static Hermes's maintainer says this in one sentence). It also builds a second type system beside the checker bridge, which is a permanent maintenance burden in a project where the checker bridge is the contract. And its win overlaps B's: the pins already admit the common unannotated-parameter case, and mypy/Pyright's own data shows the rest of a typical unannotated function is where checking stops paying.

Cost: large and permanent (an inference engine plus unification over the IR). Risk: it moves emitted semantics per call site, the same contract objection as the JIT, and the 3,089-fence annotation family does not need it to fall (B plus N4 covers it).

ROI verdict: reject. Revisit only if the pins measurably fail to admit the shapes real codebases need (the B rollout's coverage report is the instrument that would show it).

First PR-sized step: none.

## The per-module pragma and the staged rollout

Sorbet is the precedent for the staging shape (file-level sigils, https://sorbet.org/docs/static): adoption moves file by file, and the strict level is visible in the file itself. The pragma for pyscriptc:

- `# pyscriptc: infer` at the top of a module pins that module's unannotated parameters regardless of the default (useful before the default flips, and after it, to force inference in a `--no-infer` build).
- `# pyscriptc: no-infer` opts one module out of the pins; its PS1001 fences stand as today.
- pyproject `[tool.pyscriptc.typing]` carries the project-level default and a per-glob override, mirroring mypy's per-module overrides (https://mypy.readthedocs.io/en/stable/config_file.html) and Pyright's `strict` path list.

Staged rollout, each stage a separate PR with its own revert:

1. Diagnostics only. The `pyscriptc infer` coverage report ships (pin rate per module and per tree). No behavior change. Nothing can break.
2. Default on, opt-out available. Compiles run the pins by default with the skip-fast-path (no unpinned parameters means no engine run) and fact caching. `--no-infer` and the pragma exist. The fence is still the output for everything the pins cannot prove, so the worst regression is compile time, which the report from stage 1 measures per tree.
3. Per-module pragma governance. Teams adopt the pragma or pyproject overrides to keep their critical modules annotation-explicit. This is the Sorbet ratchet: the default is loose, the ratchet makes it strict where it matters, and `pyscriptc assess` reports the split.

The loose-typing flag of Option A, if it ever happens, is stage 4: it needs N1's demotion machinery and the pin-rate evidence from stages 1-2.

## The proposed decisions.md row (not appended)

| Date | Decision | Why (one line) | Rejected alternatives | Detail |
| --- | --- | --- | --- | --- |
| 2026-11-18 | Default the call-site pins on (`--infer` becomes the default, `--no-infer` and a per-module pragma opt out) behind a diagnostics-first staged rollout; no loose-typing admission flag and no HM inference arm in v0.11.0 (#1283) | The pin engine exists, its tree-wide agreement rule never overrides a written annotation, and the fence data shows the annotation family is the movable mass, while admission by inference is the Codon trade the 2026-10-25 decision already refused | A `--loose-typing` flag demoting unpinned definitions to island (gated on N1 evidence), a Codon-style Hindley-Milner inference arm (contract risk, second type system), keeping `--infer` opt-in forever (the annotation family stays fenced for the common user) | `.agents/docs/design/loose-typing-and-default-inference.md` |

## Related

- `docs/guide/python-subset.md`: the per-construct typing table and the "annotations are required" paragraph this work rewrites.
- `docs/reference/divergences.md`: the parity contract; any pin-driven divergence gets a ledger entry in the same PR.
- `.agents/docs/design/jit-and-codon-annotations.md`: the Codon comparison this note extends, and the 2026-10-25 typed-subset decision both notes lean on.
- `src/pyscriptc/inferrunner.py` and `src/pyscriptc/cli/_main.py`: the engine and the `--infer`/`--bridge` wiring this note proposes to default.
- The nine-tree report and `compat_matrix.csv` (2026-11-18): the fence data every ROI verdict above reads.
