# Per-target probe receipts

The measured answer to "does pyscriptc run on this target?". One receipt
per target in the tier-A cross matrix: the probe programs pyscriptc
compiles for that target, the bytes the binary produced when it ran, and
the verdict against the pinned CPython 3.14 oracle.

This is issue #469, R1 of the plan
`.agents/plans/2026-10-24-receipts-and-dx.md`. The plan's "Why" is the
gap it closes: `packaging/zig/smoke.sh` proves `hello.ll` plus the
runtime builds on seven targets and *runs* on three, but there is no
per-target number anywhere. The public `/compat` page gives one number
for CPython; cross targets have nothing. The receipt turns platform
support into a measurement.

## Where it lives

| path | role |
| --- | --- |
| `benchmarks/receipts/` | the harness: the target matrix, the probe set, the oracle resolver, the runner |
| `benchmarks/receipts/probes/` | the probe programs and their expected-output files |
| `data/compat/receipts.json` | the committed receipt document the page reads |
| `scripts/build_receipts_json.py` | the builder that copies a run into the data tree, with a validator and a `--check` mode |
| `apps/web/src/pages/receipts/` | the static per-target table page |
| `.github/workflows/nightly.yml` | the `target-receipts` job |

```bash
uv run python -m benchmarks.receipts.run --outdir nightly/receipts
uv run python -m benchmarks.receipts.run --target wasm32-wasi --strict
uv run python scripts/build_receipts_json.py --from nightly/receipts/receipts.json
```

## The probe set

Five programs under `benchmarks/receipts/probes/`, each with its expected
output committed next to it:

| probe | what it covers |
| --- | --- |
| `int_arith` | the arithmetic operators, floor division, modulo, `divmod`, rounding |
| `str_ops` | the string methods, the slices, the joins, the f-strings |
| `list_dict` | the list, dict and tuple literals, the comprehensions, the dict operations |
| `control_flow` | the loops, the conditionals, the typed functions, the recursion, `try`/`except` |
| `float_math` | the float arithmetic, the comparisons, the `math` helpers |

The set is toolchain-independent by construction. Every value is a literal
or a computed number: no island fallback, no `dlopen`/mix, no threads, no
files, no clock, no randomness, no locale. A program whose output depends
on the host is not a receipt, so nothing in the set reads the environment
or the filesystem.

The set is small on purpose. Every probe is compiled once per target and
run once per target, so the cost is the compile, not the probe count.

## The target matrix

The rows are the targets `packaging/zig/smoke.sh` builds:

| target | triple | runner | when it runs |
| --- | --- | --- | --- |
| `x86_64-linux-gnu` | `x86_64-linux-gnu.2.39` | native | on an x86_64 Linux host; compile-only elsewhere |
| `aarch64-linux-gnu` | `aarch64-linux-gnu.2.39` | none | compile-only: the gnu triple needs a sysroot, not just a runner |
| `riscv64-linux-gnu` | `riscv64-linux-gnu.2.39` | none | compile-only by decision (#469) |
| `x86_64-linux-musl` | `x86_64-linux-musl` | native | on an x86_64 Linux host; compile-only elsewhere |
| `aarch64-linux-musl` | `aarch64-linux-musl` | `qemu-aarch64-static` | on a Linux host with qemu-user-static; compile-only without it |
| `riscv64-linux-musl` | `riscv64-linux-musl` | none | compile-only by decision (#469) |
| `aarch64-macos` | `aarch64-macos` | native | on an aarch64 macOS host; compile-only elsewhere |
| `x86_64-macos` | `x86_64-macos` | native | on an x86_64 macOS host; compile-only elsewhere |
| `wasm32-wasi` | `wasm32-wasi-musl` | `wasmtime` | on any host with wasmtime; compile-only without it |

The runner is a property of the build host, not of the target: a row that
matches the host's own system and architecture runs natively, a foreign
Linux architecture runs under a user-mode qemu, and a WASI build runs
under wasmtime. The receipt names the runner it used and the reason a row
could not run, so the page shows the host's posture rather than a fixed
claim per target.

The riscv64 rows stay compile-only. That is the scope line of #469, not
an omission: the receipt records them as `built: true, ran: false`, with
the reason in the receipt and on the page. Nothing here grows the riscv64
support claim.

## Island rows stay out by design

A cross-compiled binary cannot run island calls: the island tier embeds
the host libpython, and a binary built for another target has no such
library. The receipt therefore covers the non-island subset only, and the
page says so in the same words.

## The measurement

For each target and each probe:

1. The compiler builds the program: `pyscriptc compile PROBE --emit bin
   --skip-check --driver zig --target TRIPLE`.
2. The binary runs through the runner for that target: natively, under
   `qemu-<arch>-static`, or under `wasmtime`.
3. The output is compared byte for byte against the bytes the pinned
   CPython 3.14 oracle produced for the same program.

The comparison is on untrimmed bytes, so a trailing space, a missing
newline or a NUL truncation is a difference. The oracle is the pinned
CPython 3.14 the rest of the repo uses; the resolver is the same one
`benchmarks/probe_lane/lane.py` has, so the two lanes cannot drift on
what "the oracle" means.

The posture is the one the smoke script already has:

| outcome | meaning |
| --- | --- |
| `built: false` | the compiler refused or failed the program; the fence code or the last error line is in `detail` |
| `built: true, ran: false` | the row is compile-only on this host; the reason is in `detail` |
| `ran: true, match: false` | the binary produced different bytes; the first differing byte is in `detail` |
| `ran: true, match: true` | the bytes are identical |

A missing runner is a skip with a reason, never a failure and never a
verdict. A wrong output is a finding, and `--strict` turns it into a
non-zero exit.

## The receipt document

`data/compat/receipts.json` is the contract between the measurement and
the page. The builder copies a run into it and refuses to write a
document the page cannot render.

| key | type | meaning |
| --- | --- | --- |
| `schema` | string | schema version, currently `1` |
| `generated` | string | UTC date of the measurement run |
| `commit` | string | git SHA of the tree that produced the file |
| `oracle` | string | the pinned CPython 3.14 interpreter's version string (never a local path) |
| `host` | object | the build host and the zig version that built the rows |
| `targets` | array | one receipt per target |

A target receipt:

| key | type | meaning |
| --- | --- | --- |
| `target` | string | the row name |
| `triple` | string | the zig triple the row was built for |
| `system` | string | `linux`, `macos` or `wasi` |
| `arch` | string | the architecture the row is for |
| `runner` | string | the runner executable, or empty |
| `method` | string | `native`, `qemu`, `wasmtime` or `compile-only` |
| `skip_reason` | string | why the row could not run, when it could not |
| `toolchain` | string | the zig version that built the row |
| `built` | int | how many probes built |
| `ran` | int | how many probes ran |
| `matched` | int | how many probes matched the oracle |
| `total` | int | how many probes the target was measured with |
| `probes` | array | one row per probe: `id`, `built`, `ran`, `match`, `detail` |

The harness also writes one JSON file per target next to the combined
document, so a consumer that wants a single target does not have to walk
the whole matrix.

## The page

`apps/web/src/pages/receipts/` is a static page, a sibling of `/compat`.
It reads `data/compat/receipts.json` at build time and bakes the numbers
in, so the page cannot claim more than the receipts say. The table is
one column per target and one row per probe, with the verdict, the
matched count and the method. A target that could not run shows the
reason, never a bare false.

The page is built with base `/receipts`, so the deploy tree serves it at
`/receipts/` next to the landing page, `/docs`, `/compat` and `/compare`.

## The nightly job

`.github/workflows/nightly.yml` has a `target-receipts` job. It installs
zig, wasmtime and qemu-user-static, runs the harness, and uploads the
receipt JSON as an artifact.

The job is report-only: the step is `continue-on-error`, the job feeds no
`report-failure` need and opens no `nightly-failure` issue. A target the
runner cannot execute is a skip with a reason; only a wrong output is a
finding, and the artifact carries it.

The job does not push to master. The committed `data/compat/receipts.json`
is refreshed by the same bot-PR path the compat snapshot uses, so master
stays PR-only.

## What the receipt found

The harness found a real wasm32 bug the moment it ran: the emitted
`malloc` declaration used a 64-bit `size_t`, but wasi-libc's `size_t` is
32 bits. The call was a signature mismatch, so every string concat
trapped with `signature_mismatch:malloc` at run time. The native IR is
byte-identical after the fix; the wasm32 lane now runs and matches.

The bug was invisible to the existing lanes because the wasm smoke
program never concatenated two strings.

## What the receipt does not cover

- Island rows, by design (see above).
- The riscv64 rows, by decision (see above).
- The macOS foreign-architecture rows: no runner can execute a binary
  built for another macOS architecture.
- The gnu triples: the smoke script compiles them but has no runner for
  them either.
- Timing. The receipt measures correctness, not speed. The perf gate is
  `benchmarks/baseline/`.
- Windows. The runtime has POSIX gaps there (see `packaging/zig/smoke.sh`),
  and no Windows target is in the tier-A matrix.
