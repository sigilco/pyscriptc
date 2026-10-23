"""The mobile triples: what they are, what they refuse, and what they exclude.

A mobile triple is a target a native app links against: the iOS device, the
iOS simulator and Android, all aarch64. Two rules follow from the shape of
those platforms and they live here so every call site agrees:

- An executable build is refused. An iOS or Android app has no use for a
  standalone binary, and the linker shapes differ enough that a binary that
  links on a desktop says nothing about the library that will actually ship.
  The refusal carries the stable code PS4101.
- The host-only runtime surface is excluded. `ps_sub.c` forks and execs,
  `ps_osfd.c` opens raw file descriptors, and neither exists on a phone.
  The list is a build-time exclusion, not a runtime check, so the symbols are
  absent from the archive instead of failing at the first call.

Everything else about the mobile build (the archive, the header, the module
map) lives in `pyscriptc.emitlib`.
"""

from __future__ import annotations

from pathlib import Path

from pyscriptc.errors import SpikeError

# The stable diagnostic code for "this target has no executable build".
# Registered in `pyscriptc.diagnostics`, so `pyscriptc explain PS4101` works.
MOBILE_REFUSAL_CODE = "PS4101"

# The three triples `--emit lib` accepts. The values are the triples `zig cc`
# and `clang` understand; the Apple ones carry the minimum OS version, which
# is what lands in `LC_BUILD_VERSION`.
MOBILE_TARGETS: dict[str, str] = {
    "aarch64-apple-ios": "aarch64-apple-ios15.0",
    "aarch64-apple-ios-simulator": "aarch64-apple-ios15.0-simulator",
    "aarch64-linux-android": "aarch64-linux-android",
}

# The runtime files a mobile build does not carry. `ps_sub.c` forks and execs,
# `ps_osfd.c` opens raw file descriptors: neither has a meaning inside an app
# sandbox, and both pull in headers a phone SDK does not ship.
MOBILE_EXCLUDED_SOURCES: tuple[str, ...] = ("ps_osfd.c", "ps_sub.c")


class MobileRefusal(SpikeError):
    """A mobile target was asked for something it does not have.

    `code` is the stable code callers match on; the message names the fix.
    """

    def __init__(self, message: str, code: str = MOBILE_REFUSAL_CODE) -> None:
        super().__init__(code, message)


def normalize_target(target: str | None) -> str | None:
    """The canonical triple for `target`, or None when there is no target.

    A triple without a minimum OS version is the mobile alias
    (`aarch64-apple-ios`), so `--target aarch64-apple-ios` and
    `--target aarch64-apple-ios15.0` mean the same build.
    """
    if target is None:
        return None
    return MOBILE_TARGETS.get(target, target)


def is_mobile_target(target: str | None) -> bool:
    """True when `target` is one of the three mobile triples."""
    if target is None:
        return False
    normalized = normalize_target(target)
    return normalized in set(MOBILE_TARGETS.values())


def mobile_target_family(target: str | None) -> str:
    """The platform family of a mobile target: `ios` or `android`.

    Empty for a non-mobile target.
    """
    normalized = normalize_target(target)
    if normalized is None:
        return ""
    if normalized.startswith("aarch64-apple-ios"):
        return "ios"
    if normalized.startswith("aarch64-linux-android"):
        return "android"
    return ""


def require_mobile_target(target: str | None, what: str) -> str:
    """The normalized triple, or a refusal naming the three supported ones.

    `what` names the operation that needs a mobile target, so the message
    says which one failed.
    """
    if not is_mobile_target(target):
        raise MobileRefusal(
            f"{what} needs a mobile target: one of "
            f"{', '.join(sorted(MOBILE_TARGETS))} (got {target!r})"
        )
    assert target is not None
    return normalize_target(target) or target


def excluded_sources(runtime: Path) -> list[Path]:
    """The runtime files a mobile build leaves out."""
    out: list[Path] = []
    for name in MOBILE_EXCLUDED_SOURCES:
        path = runtime / name
        if path.is_file():
            out.append(path)
    return out
