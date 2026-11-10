"""The `zig` extra: the declared wheel, and the hints that name it."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _extras() -> dict[str, list[str]]:
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return cfg["project"]["optional-dependencies"]


def test_the_zig_extra_declares_the_ziglang_wheel() -> None:
    """`pyscriptc[zig]` must pull the wheel that carries the zig binary."""
    extras = _extras()
    assert "zig" in extras, f"the zig extra is missing: {sorted(extras)}"
    requirements = extras["zig"]
    assert any(req.startswith("ziglang") for req in requirements), requirements


def test_the_zig_extra_pins_the_ci_verified_range() -> None:
    """The pin is the range CI proves (packaging/TOOLCHAIN.md)."""
    requirement = next(req for req in _extras()["zig"] if req.startswith("ziglang"))
    match = re.fullmatch(r"ziglang(>=[\d.]+,<[\d.]+)", requirement)
    assert match, f"the ziglang pin must be a range, got {requirement!r}"
    lower, upper = match.group(1).split(",")
    low_minor = int(lower.split(".")[1])
    high_minor = int(upper.split(".")[1])
    assert (low_minor, high_minor) == (16, 18), f"the CI-verified range moved: {requirement!r}"


def test_the_install_hint_names_the_extra() -> None:
    """The missing-toolchain diagnostic must offer the one-command install."""
    from pyscriptc import zigshim

    hint = zigshim.install_hint()
    assert "pyscriptc[zig]" in hint, hint
    assert "ziglang" in hint, hint


def test_the_missing_toolchain_error_names_the_extra() -> None:
    """The compiler's own refusal names the extra, not only the wheel."""
    from pyscriptc.cli import _main

    source = Path(_main.__file__).read_text(encoding="utf-8")
    assert "zigshim.install_hint()" in source
    # The hint is the only place the extra can be named from the CLI, so the
    # refusal has to route through it.
    assert "pyscriptc[zig]" in zigshim_install_hint()


def zigshim_install_hint() -> str:
    from pyscriptc import zigshim

    return zigshim.install_hint()
