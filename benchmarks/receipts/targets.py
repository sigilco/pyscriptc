# SPDX-License-Identifier: Apache-2.0
"""The target matrix the receipt harness measures.

One row per target in the tier-A cross matrix `packaging/zig/smoke.sh`
builds. A row is a triple, the operating system and architecture it is
for, and the runner that can execute it on the build host.

The riscv64 rows are compile-only by decision (the scope line of issue
#469): the receipt records them as `built: true, ran: false`, with the
reason spelled out. Nothing here grows the riscv64 support claim.

Island rows stay out by design: a cross-compiled binary cannot run island
calls (the island tier embeds the host libpython), so no probe in the
receipt set uses the island fallback.
"""

from __future__ import annotations

import platform
from dataclasses import dataclass


@dataclass(frozen=True)
class Target:
    """One row of the cross matrix."""

    name: str
    triple: str
    # The operating system the row is for: "linux", "macos" or "wasi".
    system: str
    # The architecture the row is for: "x86_64", "aarch64" or "riscv64".
    arch: str
    # The runner that can execute a build of this row on a build host that
    # is not the row's own system. "qemu-aarch64-static" for a foreign
    # Linux arch, "wasmtime" for WASI. Empty when no such runner exists.
    emulated_runner: str = ""
    # Why the row cannot run, when it cannot. Empty for a runnable row.
    # A compile-only row keeps its reason here, so the receipt can state
    # it instead of showing a bare false.
    compile_only_reason: str = ""
    # The row is compile-only by decision, whatever the host can run.
    # The riscv64 rows carry this: issue #469 keeps them exactly as they
    # are today, so the receipt never runs them.
    compile_only_by_decision: bool = False

    @property
    def is_wasm(self) -> bool:
        """Whether the row is a WASI target."""

        return self.system == "wasi"


# The reason the riscv64 rows carry. The scope line of issue #469: the
# riscv64 rows stay exactly what they are today, so the receipt records
# them as built but not run, with the reason spelled out.
RISCV_REASON = "compile-only by decision (#469): the riscv64 rows stay exactly what they are today"

# The matrix. The order is the one the smoke script uses, so the two read
# the same way.
TARGETS: tuple[Target, ...] = (
    Target("x86_64-linux-gnu", "x86_64-linux-gnu.2.39", "linux", "x86_64"),
    Target("aarch64-linux-gnu", "aarch64-linux-gnu.2.39", "linux", "aarch64"),
    Target("riscv64-linux-gnu", "riscv64-linux-gnu.2.39", "linux", "riscv64", "", RISCV_REASON),
    Target("x86_64-linux-musl", "x86_64-linux-musl", "linux", "x86_64"),
    Target("aarch64-linux-musl", "aarch64-linux-musl", "linux", "aarch64", "qemu-aarch64-static"),
    Target(
        "riscv64-linux-musl",
        "riscv64-linux-musl",
        "linux",
        "riscv64",
        "qemu-riscv64-static",
        RISCV_REASON,
        compile_only_by_decision=True,
    ),
    Target("aarch64-macos", "aarch64-macos", "macos", "aarch64"),
    Target("x86_64-macos", "x86_64-macos", "macos", "x86_64"),
    Target("wasm32-wasi", "wasm32-wasi-musl", "wasi", "wasm32", "wasmtime"),
)


def by_name(name: str) -> Target:
    """The target with this name."""

    for target in TARGETS:
        if target.name == name:
            return target
    raise KeyError(name)


def names() -> list[str]:
    """Every target name, in matrix order."""

    return [target.name for target in TARGETS]


def host_arch() -> str:
    """The build host's architecture, in the matrix's vocabulary."""

    machine = platform.machine()
    if machine in ("x86_64", "amd64"):
        return "x86_64"
    if machine in ("aarch64", "arm64"):
        return "aarch64"
    if machine.startswith("riscv64"):
        return "riscv64"
    return machine


def host_system() -> str:
    """The build host's operating system, in the matrix's vocabulary."""

    if platform.system() == "Darwin":
        return "macos"
    if platform.system() == "Linux":
        return "linux"
    return platform.system().lower()


def runner_for(target: Target) -> str:
    """The runner that executes a build of this target on this host.

    A build for the host's own operating system and architecture runs
    natively. A foreign Linux architecture runs under a user-mode qemu,
    and a WASI build runs under wasmtime. Everything else is compile-only.

    The returned string is the runner executable name, or "" when the row
    has no runner on this host.
    """

    if target.compile_only_by_decision:
        return ""
    if target.system == host_system() and target.arch == host_arch():
        return "native"
    if target.system == "linux" and target.emulated_runner:
        return target.emulated_runner
    if target.system == "wasi":
        return target.emulated_runner
    return ""
