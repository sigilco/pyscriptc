"""LLVM verification of the emitted module.

The emitter writes LLVM text that the driver compiles. When the emitter drifts
(missing terminator, undefined value, wrong operand type), the failure lands
in `cc` with a message about a line in a generated file, which is expensive to
attribute. Running the LLVM verifier over the emitted text names the problem
while the `.ll` is still the thing under inspection.

The verifier is `opt -verify` when LLVM's `opt` is on PATH, and the C driver's
own module verifier otherwise (`clang -c` on a `.ll` runs the same verifier
before codegen). Both are read-only: they parse and check, they do not emit a
binary the user might mistake for a build product.

"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from pyscriptc.errors import SpikeError

CODE_LLVM_VERIFY = "PS0104"


class LLVMVerifyError(SpikeError):
    """The emitted LLVM module did not verify. `code` is stable."""

    def __init__(self, message: str) -> None:
        super().__init__(CODE_LLVM_VERIFY, message)


@dataclass(frozen=True)
class VerifyResult:
    """The outcome of one verification run."""

    tool: str
    ok: bool
    message: str
    skipped: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "tool": self.tool,
            "ok": self.ok,
            "skipped": self.skipped,
            "message": self.message,
        }


def _opt_command(ll_path: Path, opt: str) -> list[str]:
    return [opt, "-verify", "-disable-output", str(ll_path)]


def _driver_command(ll_path: Path, driver: str, target: str | None) -> list[str]:
    """The driver's own module check: `clang -c` on a `.ll` verifies the module."""
    cc = "zig cc" if driver == "zig" else driver
    argv = [*cc.split(), "-c", "-x", "ir", str(ll_path), "-o", os.devnull]
    if target is not None:
        argv.extend(["-target", target])
    return argv


def verify_module_text(
    ll_path: Path,
    driver: str = "clang",
    target: str | None = None,
    opt: str | None = None,
) -> VerifyResult:
    """Verify the LLVM module in `ll_path`.

    Returns the result instead of raising, so the caller can record it in the
    debug artifacts. A failure is reported, not swallowed.
    """
    if opt is None:
        opt = shutil.which("opt")
    if opt is not None:
        argv = _opt_command(ll_path, opt)
        tool = f"opt -verify ({Path(opt).name})"
    else:
        argv = _driver_command(ll_path, driver, target)
        tool = f"{driver} module verifier"
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return VerifyResult(tool=tool, ok=False, message=f"could not run {tool}: {exc}", skipped=True)
    if proc.returncode == 0:
        return VerifyResult(tool=tool, ok=True, message="")
    message = (proc.stderr or proc.stdout).strip()
    return VerifyResult(tool=tool, ok=False, message=message)


def verify_or_raise(
    ll_path: Path,
    driver: str = "clang",
    target: str | None = None,
    opt: str | None = None,
) -> VerifyResult:
    """Verify the LLVM module and raise `LLVMVerifyError` when it does not."""
    result = verify_module_text(ll_path, driver=driver, target=target, opt=opt)
    if not result.ok and not result.skipped:
        raise LLVMVerifyError(f"the emitted LLVM module did not verify: {result.message}")
    return result
