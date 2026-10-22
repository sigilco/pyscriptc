# ABI and backwards-compat strategy

Where pyscriptc stands versus `vercel-labs/scriptc`, and what to promise.

## What our ABI actually is

pyscriptc has three distinct compat surfaces, not one:

1. **`.ll` ↔ `runtime/ps_*.c` layout contract.** The emitted LLVM names `%ps.str
   = type { i64, ptr }` and calls `ps_*` functions with C-ABI signatures;
   `ps_rt.h` fixes `PsBuf` at 16 bytes, `PsSeq` field order (len, elem, data,
   then appended `cap`), `PsMap`, `PsType`, `PsArg`. This is the real ABI —
   the same boundary scriptc calls its runtime ABI.
2. **IR JSON schema.** `irVersion: 1`, `subset: "gates-v1"`, plus the `cpython`
   stamp (`assert_same_cpython` refuses emission under a different interpreter
   version). `--emit ir` artifacts and `IR_HASHES.json` live here.
3. **External C ABI.** `PYSCRIPTC_MIX_SO` — a frozen two-symbol surface
   (`mix_crc32`, `mix_crc32_n`) dlopened by `ps_mix.c`. This is the only ABI a
   third party could ever build against today.

Key structural fact: a compile is atomic. `.ll` text, runtime `.c`, and the
linker invocation all come from one checkout in one `link_binary` call — a
stale `.ll`/runtime pair can only arise from a *committed fixture*
(`packaging/zig/*.ll`) or a saved `--emit llvm` artifact, never from a fresh
compile. Cross-compilation changed nothing here: the `.ll` is
target-agnostic, all ABI decisions still live in the same link step.

## How scriptc does it

scriptc stamps an ABI integer into generated binaries and runtime packs
(`packages/compiler/src/backend/runtime-abi.ts`,
`scripts/verify-linux-abi.mjs`) and bumps it every release — currently ABI 22
at v0.3.5. There is no backwards compat: a binary and a runtime pack must
come from the same release, full stop. That works for them because the
runtime is shipped as a prebuilt artifact consumers link against, and the
TS codegen churns every release anyway.

## Recommendation

Adopt the epoch model, but bump on *contract change*, not on release:

1. **Single `PS_ABI` integer**, defined once in `ps_rt.h` and stamped into
   emitted `.ll` (as a module flag or first-line comment) by `link_binary`'s
   upstream emitter. The link step refuses a mismatch. Bump it only when the
   `.ll`↔runtime contract changes: struct layout, a `ps_*` signature, the
   entry-point shape (the synthesized module-level entry just changed it once).
   Written rule: any change to a `ps_*` signature or a `%ps.*` struct layout
   in `ps_rt.h` requires a `PS_ABI` bump or an explicit no-bump note in the
   PR description. Status: `PS_ABI` stamping is unimplemented today. This is
   a proposal; nothing in `link_binary` defines, stamps or checks it yet.
2. **Append-only struct discipline, already documented in `ps_rt.h`, becomes
   the compat rule:** new fields go at the tail (`PsSeq.cap` precedent), new
   functions are additive, existing signatures are frozen. Same rule as
   stable C ABIs — no layout reshuffle inside an epoch. Enforcement: prefer
   a CI check over a review-rule-only policy. A grep-level check suffices:
   `ps_rt.h` structs only grow, and the `ps_mix.c` dlsym set never shrinks.
3. **`PS_ABI` is a third, independent version axis.** It never enters IR
   hashing (see `docs/ir-canonicity.md`), stays out of `irVersion`, and
   stays out of `canonical-schema/2`. `irVersion` keeps doing its own job:
   it guards the IR JSON schema, which changes for its own reasons (new
   node kinds, new fields) and should stay on its own counter. `cpython`
   stamping already enforces interpreter-parity — no change needed.
4. **`mix_crc32` stays frozen forever** — it is the precedent for the phase-F
   static FFI (`--ffi` manifest à la `scriptc build --ffi`), with a caveat:
   it becomes precedent for FFI manifests only after per-manifest ABI
   pinning is confirmed in the epic-F plan. When FFI lands,
   each manifest pins its own ABI; the core `PS_ABI` is not its concern.
5. **Do not promise cross-version `.ll` compat.** Saved `--emit llvm`
   artifacts and committed fixtures are same-epoch only; regenerating
   (`packaging/zig/emit.sh`) is the documented answer, same as
   `IR_HASHES.json` regen. This is scriptc's model: cheap, honest, and the
   only wrong-use mode (stale artifact) fails loudly at link time instead of
   silently misreading a struct.

Net: same fail-closed posture as scriptc, tuned to our compile model —
epochs on real contract changes rather than per-release bumps, because our
runtime is stable C and compile is atomic.

## Exception codes (E2 decision, 2026-10-21)

Exception class codes cross the `.ll` ↔ `runtime` boundary — emitted IR carries
them as `i32` literals — so they are part of surface 1 above. The E2 renumber
(M2, commit ba701bf) assigns the 67 builtin classes the dense range 1 to 67
plus the three `OSError` aliases, moves `USER_EXC_BASE` to 256, and leaves
72 to 255 as headroom for future builtins so user classes never renumber
across releases. This was an epoch-level renumber absorbed inside E2 before
any cross-binary `.ll` consumer depended on the old 1-to-7 values, per the
"do not promise cross-version `.ll` compat" rule above; it is recorded here
because the design (`docs/e2-exception-model.md`) names this document as the
owner of the decision.

## Sources

- `src/pyscriptc/runtime/ps_rt.h` (struct layout + append-only comment),
  `.agents/docs/invariants.md` (C ABI string/aggregate rules),
  `src/pyscriptc/ir.py` (`IR_VERSION`, `SUBSET`, `assert_same_cpython`),
  `src/pyscriptc/runtime/ps_mix.c`, `.agents/plans/2026-10-14-waves-after-scorer-v2.md` (phase F FFI).
- scriptc: `vercel-labs/scriptc` `packages/compiler/src/backend/runtime-abi.ts`,
  `scripts/verify-linux-abi.mjs` (ABI 22 at v0.3.5).
