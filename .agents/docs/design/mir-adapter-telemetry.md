# MIR adapter fallback telemetry (B11)

Design note only. Nothing here is implemented; it fixes what to record when a function leaves the MIR lane, so the implementation is a small, reviewable PR once the adapter exists.

## The event

Today, with `PYSC_MIR` set, `mirlower.lower_module` lowers every function `supported_fn` accepts and puts the rest in `MirModule.skipped`; `MirEmitter._splice` keeps the direct emitter's body for those. Once the lane is the default, those functions go through the typed-HIR adapter (typed `_irnode` HIR handed to the direct emitter) instead. That hand-off is the event: one per function, at the `emit_llvm` stage, only on the MIR lane.

Each record carries three fields and nothing else:

- `function`: the IR function name (the same string `MirModule.skipped` holds, e.g. `main`, `Point.__init__`, `__entry__`).
- `module`: the IR module's `sourceFile`, the path as the compile received it (the IR carries no dotted module name today). The log may hold paths; the remote telemetry may not, see below.
- `reason`: the innermost node the slice predicates reject, in the vocabulary `scripts/mir_coverage.py` already prints (`expr:call`, `stmt:for`, `return:str`, `local:list`, `fn:closure env`, see `.agents/docs/compiler-progress.md`). One source of truth: the reason function moves next to `supported_fn` in `mirlower.py` (lane A's file, so it lands with them) and the script imports it, rather than keeping a copy.

Entry points (`__entry__`) and functions with no body worth lowering are recorded like any other: filtering is a reader's job, and dropping them would make the counts disagree with `skipped`.

## Where it surfaces

- `--debug` stages table: one summary line under the `emit_llvm` row, `[debug] mir  lowered N  adapter M`, plus with `--debug=fences` (or a new `mir` topic) one line per adapter function, `[debug] mir.adapter module.fn  reason`, sorted by module then function. `--debug-dir` writes the same list into `summary.json` as `mirAdapter: [{module, function, reason}]`, next to `stageStats`. Precedent: `DebugSession.set_binding` already records emitted and skipped names with a reason for `--emit binding`; the MIR record is the same shape.
- JSONL audit log (`src/pyscriptc/log.py`): one INFO event per compile, `emit.mir`, with `lowered=N adapter=M` and a `reasons` histogram (`{"expr:call": 4, ...}`); per-function `emit.mir.adapter` records (`module`, `function`, `reason`) at DEBUG only (`PYSC_DEBUG=1` or `-v`). The log is local, so the `sourceFile` path is allowed there.
- Not PostHog. `telemetry.py` never sends definition names; if the remote event ever gets MIR data it is the two counts and the reason histogram, which carry only the fixed reason vocabulary, the same rule as the PSxxxx code histogram.

## Cost

- Compute: the verdict is already computed (`supported_fn` runs once per function in `lower_module`). The reason walk runs only for rejected functions and stops at the first rejected node, so it is at most one extra predicate pass over a function that is out of the slice. On the visible corpus (31 functions, 28 out of slice) the whole `scripts/mir_coverage.py` run, lowering included, takes about 0.3s.
- Default compile: zero. The direct lane never imports `miremit`; the record is built inside `emit_llvm_mir`, only when a `DebugSession` or the logger wants it. The INFO summary is one dict per compile.
- Log volume: one INFO line per compile; DEBUG adds one line per adapter function (tens per program, low thousands for the self-host compile), within the existing 10 MiB daily cap.
- Output stability: no change to the emitted `.ll`, the IR or TIR hashes, or `module_hash`; the record is a side channel.

## Open questions

- Whether `reason` should keep the op (`expr:binary & int`) or collapse to the node kind for the INFO histogram. Keeping the op is more useful for picking the next slice; the DEBUG records keep it either way.
- Whether a function the adapter also fails on (a direct emitter refusal) gets a second event or rides the existing fence and diagnostic path. Proposal: the existing path, since it already stops the compile with a code.
