# SPDX-License-Identifier: Apache-2.0
"""Per-target probe receipts: the measured answer to "does pyscriptc run here?".

The harness compiles a small, toolchain-independent probe set for every
target in the tier-A matrix, runs each binary on the runner that can run
it (native, `qemu-<arch>-static`, `wasmtime`), and byte-compares the
output against the pinned CPython 3.14 oracle. It writes one receipt
document per target.

Design: .agents/docs/benchmarks/target-receipts.md
"""
