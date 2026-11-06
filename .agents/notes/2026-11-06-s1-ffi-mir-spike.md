# S1 spike: can the FFI data plane build on the MIR-default compiler?

Date: 2026-11-06. Task: cloud-handoff S1 (epic #817). Informs whether
[#240](https://github.com/sigilco/pyscriptc/issues/240) stays in v0.9.0.
No compiler code changed.

## Verdict

**Yes.** A `--ffi` program compiles, links and runs with `PYSC_MIR` unset
(direct emitter, today's default) and with `PYSC_MIR=1` (MIR strangler
lane). LLVM text is byte-identical. The FFI call itself is **not** in the
MIR leaf vocabulary; the function that contains it is skipped and rides
the direct emitter through the existing splice. That is enough for a
MIR-default compiler that still keeps the dict-IR direct path as the
out-of-slice fallback (the v0.9.0 shape: MIR default, dict IR dropped
only at v0.10.0).

**#240 recommendation: keep in v0.9.0.** The second-manifest work is an
outside-of-compiler adapter (`objects=` vs `source=` already exist on
the F2 link seam). MIR-default does not regress that seam.

## Probe

Existing F2 smoke, not a new adapter: `packaging/zig/ffiprobe/`
(`ffi.toml` + `ffi.c` exporting `ffi_add3`, Python `ffiprobe.add3`).

Program (same shape as `tests/test_ffi_link.py`):

```python
import ffiprobe


def total() -> int:
    return ffiprobe.add3(20, 21, 22)
```

Host: Linux x86_64, clang present. Command:

```
uv run python -m pyscriptc compile USE.py --ffi packaging/zig/ffiprobe/ffi.toml \
  --skip-check --call total --args '[]' --run -o OUT.bin
```

| Lane | env | compile | stdout | exit |
| --- | --- | --- | --- | --- |
| Direct | `PYSC_MIR` unset | ok | `63` | 0 |
| MIR | `PYSC_MIR=1` | ok | `63` | 0 |

`--emit llvm` on both lanes: `cmp` reports identical files. A mixed
module (FFI `total` plus in-slice `leaf(a, b) -> a + b`) also emits
byte-identical LLVM: `leaf` is in the MIR function list, `total` is in
`skipped`.

Pinned by `tests/test_ffi_mir_spike.py`.

## What works

- Manifest install, F1 lowering of a scalar ABI call (`kind == "call"`,
  `symbol == "ffi_add3"`), F2 `[[source]]` compile-and-link of `ffi.c`.
- Dual-lane CLI compile and run; dual-lane LLVM identity.
- Strangler: `mirlower.supported_expr` admits only
  `const`/`name`/`binary`/`compare`/`unary`. An FFI call is out of slice,
  so `supported_fn` is false for the caller and `emit_llvm_mir` leaves
  that `define` block on the direct path.
- Link flags and units are post-emit (`link_binary`). Switching the
  emit lane does not change `compile_units` / `link_units` / `libs`.

## What does not

- MIR does not lower ABI/FFI calls, handles, `bytes`, or consumed
  ownership. Those stay on the direct emitter.
- This spike did not land a zlib or libm adapter (that is #240 itself)
  and did not run the musl cross matrix (needs zig; sqlite pilot already
  covers the musl compile gate for F3).
- If the direct emitter is deleted before MIR grows a `call`/ABI op,
  the FFI data plane stops building. That is a v0.10.0 sequencing
  constraint, not a v0.9.0 blocker.

## Why keep #240

Issue #240's acceptance is: adapter outside `src/pyscriptc/`, compiler
diff zero, differential against CPython `zlib` or `math`, host +
`x86_64-linux-musl`. The compiler-side F2 split (`path` vs `objects`,
`libs`) already exists. The S1 question was whether MIR-as-default
breaks that plane. It does not, while the strangler fallback remains.

Slip only if v0.9.0 were to drop the direct path; the recorded milestone
shape does not.
