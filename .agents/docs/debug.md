# Compiler debug mode

For agents and contributors debugging lowering decisions: what the compiler
saw, what it decided, and how long each stage took.

## Switches

- `pyscriptc compile FILE --debug` — compact per-stage timing table on stderr.
- `--debug=WHAT` — comma list of topics: `stages`, `fences`, `cc`, `paths`, `all`.
- `PYSC_DEBUG=1` — same as bare `--debug`; `PYSC_DEBUG=fences,cc` selects topics.
  `--debug` wins over the env var when both are set.
- `--debug-dir DIR` — writes artifacts to DIR and enables debug. See below.

## stderr topics

- `stages` — `lower` / `check` / `validate` / `emit_llvm` / `link` / `run` in ms
  plus a total.
- `fences` — the fence table: count, then `file:line:col CODE message` per fence
  (the same dicts that `module["fences"]` carries; no extra computation).
- `cc` — every clang/zig command line `link_binary` runs (the `.ll`/`.c -> .o`
  compiles, then the link line).
- `paths` — where `ir`, `ll`, and `bin` were written.

## `--debug-dir` artifacts

| File | Contents |
| --- | --- |
| `ir.json` | The lowered module, identical to the `<out>.ir.json` next to the output. |
| `out.ll` | The emitted LLVM text (present for `--emit llvm` and `--emit bin`). |
| `cc.sh` | The exact cc invocations, one per line (only when linking ran). |
| `summary.json` | Stage timings, fence dicts, cc commands, output paths, final status (`ok` / `fences` / `error`). |

Artifacts are written in a `finally`, so a fenced or failed compile still
leaves `ir.json` and `summary.json` for inspection.

## When to use it

- A fence you did not expect: `--debug=fences` shows code + span without the
  caret rendering; `summary.json` has the machine-readable dicts.
- "What did lowering produce": read `ir.json` (the schema is `ir.py`).
- "What did the backend emit": `out.ll`.
- A link failure or a cross-compile question: `cc.sh` replays the driver calls.
- A slow compile: the stage table splits lower/check/emit/link time.

Debug mode is observational only — it never mutates the module or the emitted
text, and `--emit ir`/`llvm`/`bin` outputs stay byte-identical to a plain run.
