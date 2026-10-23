# Compiler debug mode

For agents and contributors debugging lowering decisions: what the compiler
saw, what it decided, and how long each stage took.

## Switches

- `pyscriptc compile FILE --debug` — compact per-stage timing table on stderr,
  plus the IR structural verification.
- `--timings` — that stage table on its own, without the other debug topics.
- `--debug=WHAT` — comma list of topics: `stages`, `fences`, `cc`, `paths`,
  `verify`, `all`.
- `PYSC_DEBUG=1` — same as bare `--debug`; `PYSC_DEBUG=fences,cc` selects topics.
  `--debug` wins over the env var when both are set.
- `--debug-dir DIR` — writes artifacts to DIR and enables debug. See below.

## stderr topics

- `stages` — `parse` / `lower` / `check` / `validate` / `verify` / `emit_llvm` /
  `llvmverify` / `link` / `run` in ms plus a total. `parse` is the part of
  lowering that went into the Python parser; `cc_compile` and `cc_link` break
  the `link` stage into its object compiles and the final link.
- `fences` — the fence table: count, then `file:line:col CODE message` per fence
  (the same dicts that `module["fences"]` carries; no extra computation).
- `cc` — every clang/zig command line `link_binary` runs (the `.ll`/`.c -> .o`
  compiles, then the link line).
- `paths` — where `ir`, `ll`, and `bin` were written.
- `verify` — the verdicts of the two verifiers (see below). Part of bare
  `--debug`; `--debug=stages` alone skips it.

## Verification

Two read-only checks run when the `verify` topic is on (bare `--debug`,
`--debug=all`, or any `--debug-dir`):

- IR structural check (`irverify.py`): after `validate`, the verifier walks the
  module and reports type inconsistencies (operands that disagree, a compare
  that does not yield bool, a non-bool condition, a returned value that does
  not match the function), undefined names (a `name` that is not a parameter,
  local or env slot, a callee that names no function or class of the module)
  and terminator problems (a statement after a `return`/`raise`, a value-return
  function with a path that reaches the end). A violation fails the compile
  with its own `PS01xx` code (`pyscriptc explain PS0101` and friends describe
  each); the compile would have produced a wrong binary or a cc error instead.
- LLVM module verification (`llvverify.py`): after the `.ll` is written,
  `opt -verify` runs over it when LLVM's `opt` is on PATH, and the C driver's
  own module verifier runs otherwise (`clang -c` / `zig cc -c` verify the
  module before codegen). A rejected module fails the compile with `PS0104`
  and the verifier's message.

## `--debug-dir` artifacts

| File | Contents |
| --- | --- |
| `ir.json` | The lowered module, identical to the `<out>.ir.json` next to the output. |
| `out.ll` | The emitted LLVM text (present for `--emit llvm` and `--emit bin`). |
| `cc.sh` | The exact cc invocations, one per line (only when linking ran). |
| `summary.json` | Stage timings (`timings` maps every stage and sub-step to ms), fence dicts, cc commands, output paths, verification verdicts (`verify.ir` list, `verify.llvm` object), final status (`ok` / `fences` / `error`). |

Artifacts are written in a `finally`, so a fenced or failed compile still
leaves `ir.json` and `summary.json` for inspection.

## When to use it

- A fence you did not expect: `--debug=fences` shows code + span without the
  caret rendering; `summary.json` has the machine-readable dicts.
- "What did lowering produce": read `ir.json` (the schema is `ir.py`).
- "What did the backend emit": `out.ll`.
- A link failure or a cross-compile question: `cc.sh` replays the driver calls.
- A slow compile: the stage table splits lower/check/emit/link time.
- "Did the backend emit valid LLVM": `summary.json` carries the verifier
  verdict, and `out.ll` is the module it judged.

Debug mode never mutates the module or the emitted text, and `--emit
ir`/`llvm`/`bin` outputs stay byte-identical to a plain run. The two
verifiers are the exception: they read the module and the emitted text, and
they fail the compile when they find a problem.

## Reducer: `pyscriptc reduce`

Delta-debugging for failing sources. Given a file that reproduces a bug
(a fence you did not expect, a lowering crash, a wrong binary), `reduce`
mechanically deletes what the bug does not need and writes the smallest
variant it found to `FILE.min.py` beside the input.

```
pyscriptc reduce FILE --fence PS1104        # still fences with PS1104
pyscriptc reduce FILE --crash               # still raises a non-fence error
pyscriptc reduce FILE --predicate CMD       # CMD exits 0 on the candidate file
```

- `--predicate CMD` runs `CMD` (a shell command) after every removal. The
  candidate's path arrives in `$PSC_SOURCE`; exit 0 keeps the removal.
  `--timeout S` caps each run (default 120).
- `--fence CODE` is shorthand for "lowering the candidate still reports at
  least one fence with code CODE".
- `--crash` is shorthand for "lowering the candidate still raises a non-fence
  error" (any exception `lower_source` raises that is not a fence).
- `--print` prints the reduced source instead of writing `FILE.min.py`;
  `--json` prints the removal log (`step`, `kind`, `detail`, `kept`, `reason`)
  and writes nothing. Exit code is 0 when the final variant still satisfies
  the predicate, 1 otherwise.

The loop is AST-level and monotone. Each pass collects every statement in the
module (module level, function bodies, class bodies, branches), skips the ones
whose names are still read later, re-lowers with `lower_source`, and keeps a
removal only when the predicate still holds. No LLVM, no link, no C toolchain:
a pass is fast.

The input file is never modified. `--steps N` caps removal attempts.

Typical flow after a bad fence in a large script:

```
pyscriptc reduce big.py --fence PS1104 --json > reduction.json
cat big.min.py
```

`reduction.json` shows which removals were kept, so a fence report can carry
the minimal reproducer instead of a 400-line file.

## wasm targets

With `--target wasm32-wasi-musl`, the `cc` table shows the wasm specifics:

- Every unit (the `.ll` and each runtime `.c`) carries the
  `-mllvm -wasm-enable-sjlj` flag set plus `-mllvm -wasm-use-legacy-eh=false`
  and the `+exception-handling` target feature: `try`/`except` lowers through
  wasm exception handling via setjmp/longjmp.
- The compile list includes `runtime/wasm/rt_wasm.c`, the vendored wasi-libc
  setjmp runtime (zig ships the source but never builds it).
- The `-mconsole` flag that a Windows target inserts into the link line is
  absent on wasm.
- On `wasm32-wasi-p2` there is one more line after the link: the
  `wasm-tools component new` invocation with the pinned preview1 adapter.
  Its output replaces the core module, so the `.wasm` in the dump is the
  component, and the `--debug=cc` command list is the whole story of how it
  got there.
