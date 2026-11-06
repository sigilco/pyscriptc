# MIR lane escape-hatch contract

The MIR emitter is a strangler lane. The default stays the direct LLVM path until the owner signs the flip (draft PR #937). This note names the env values and the one-line default change; it does not change behavior.

## Env names

| Name | Value | Meaning |
| --- | --- | --- |
| `PYSC_MIR` (on) | `1` | Force the MIR lane. Any nonempty value other than `0` is also on. |
| `PYSC_MIR` (off) | `0` | Force the direct emitter. After the flip this is the escape hatch. |
| inverted default | unset or empty | Follows `MIR_DEFAULT` in `src/pyscriptc/miremit.py`. Today that flag is `False` (MIR off). The flip is `MIR_DEFAULT = True`, so unset means MIR on and `PYSC_MIR=0` remains the off switch. |

Constants: `MIR_ENV`, `MIR_ON`, `MIR_OFF`, `MIR_DEFAULT`. Predicate: `mir_opt_in()`.

## Where the default is read

The default is read in one place: `mir_opt_in()` in `src/pyscriptc/miremit.py`. Other code must call that function, not re-parse the env.

Known call sites of `mir_opt_in()`:

- `src/pyscriptc/cli/_main.py` (the emit driver: picks `emit_llvm_mir` when the predicate is true)

Known writers of `PYSC_MIR` that must not invent a second default:

- `scripts/runtime_ab.py` (`lane_env`) sets `PYSC_MIR=0` or `1` so a parent opt-in cannot leak
- tests that opt into the lane (`tests/test_mir_hashseed.py`) set `PYSC_MIR=1`

## Flip

After the owner signs the decision, the behavior change is one assignment: `MIR_DEFAULT = True` in `miremit.py`. Tests in `tests/test_mir_escape_hatch.py` pin today's default and both switches; they will need the default assertion inverted in the same PR as the flip.
