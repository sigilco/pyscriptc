# PYSC_MIR escape hatch

How the opt-in MIR lane is switched, what "per function" means for it, and how to compare it against the direct emitter.
User-facing differences and failures are in `docs/guide/debugging.md` ("The `PYSC_MIR` backend lane").

## The switch

- `PYSC_MIR` is read once per compile by `miremit.mir_opt_in()`: on for any value other than empty or `"0"`.
- `cli/_main.py` swaps `emit_llvm` for `emit_llvm_mir` at the `emit_llvm` stage. `--emit binding` keeps `emit_llvm` regardless.
- `scripts/runtime_ab.py` sets `PYSC_MIR=0` on its direct lane so a parent shell's opt-in cannot leak into the baseline. Do the same in any harness that compiles both lanes.

## Per function

There is no per-function knob. With the lane on, every function `mirlower.supported_fn` accepts is lowered to MIR and spliced into the direct emitter's module text (`MirEmitter._splice`), and every other function keeps the direct body. So "flipping one function" means:

- To see which functions take the lane: `mirlower.lower_module(module).skipped` lists the ones that do not; `uv run python scripts/mir_coverage.py --json` gives per-script in-slice names (and, once landed, an out-of-slice reason per function).
- To compare one function across lanes: compile with `--emit llvm` with and without `PYSC_MIR=1` and diff that function's `define` block. `tests/test_mir_differential.py` does this for every in-slice corpus program and leaf fixture and names the diverging function.
- To keep one function on the direct path while the rest take the lane: move it out of the slice (it is a pure predicate on the IR), or compile the module without `PYSC_MIR`. Adding a per-function allow or deny list is a design change to `miremit`, owned by the MIR lane, not a doc fix.
- Runtime equivalence, not only text: `uv run python scripts/runtime_ab.py [prog.py]` builds both binaries and diffs stdout, stderr and the exit code.

## Known gap

`emit_llvm_mir` accepts `wasm=` but does not pass it to `MirEmitter`, so the direct `Emitter` inside the lane is built with `wasm=False` and spells the entry point `@main` instead of `@__main_argc_argv`. A `wasm32` compile with `PYSC_MIR` set therefore fails at the link. Reproduce: `emit_llvm(m, standalone=True, wasm=True) != emit_llvm_mir(m, standalone=True, wasm=True)` on any leaf fixture. The fix belongs in `miremit.py`.
