"""The mobile triples: what they are, what they refuse, and what they exclude.

A mobile triple is a target a native app links against: the iOS device, the
iOS simulator and Android, all aarch64. Three rules follow from the shape of
those platforms and they live here so every call site agrees:

- An executable build is refused. An iOS or Android app has no use for a
  standalone binary, and the linker shapes differ enough that a binary that
  links on a desktop says nothing about the library that will actually ship.
  The refusal carries the stable code PS4101.
- The driver is clang with an explicit sysroot, never zig. Zig bundles libc
  for its tier-A desktop targets, but not bionic and not the Apple SDKs, so
  the iOS path reads the sysroot from `xcrun --show-sdk-path` and the Android
  path from the NDK the user points at. The spike (#353/#354) confirms which
  of these needs tuning; the shape is fixed.
- The host-only runtime surface is excluded. `ps_sub.c` forks and execs,
  `ps_osfd.c` opens raw file descriptors, and neither exists in an app
  sandbox. The exclusion is at build time, so the symbols are absent from the
  archive instead of failing at the first call.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pyscriptc.errors import SpikeError

# The stable diagnostic code for "this target has no executable build".
# Registered in `pyscriptc.diagnostics`, so `pyscriptc explain PS4101` works.
MOBILE_REFUSAL_CODE = "PS4101"

# The Android API level the NDK sysroot targets. 24 is the modern minSdk
# floor: over 96 percent device coverage, matching the AndroidX default.
ANDROID_API_DEFAULT = 24

# The runtime files a mobile build does not carry. `ps_sub.c` forks and execs,
# `ps_osfd.c` opens raw file descriptors: neither has a meaning inside an app
# sandbox, and both pull in headers a phone SDK does not ship.
MOBILE_EXCLUDED_SOURCES: tuple[str, ...] = ("ps_osfd.c", "ps_sub.c")

# The 16 KB page-alignment link flags Google Play requires of any shared
# object built for Android 15+ (the 2025-11-01 rule). A static archive has no
# ELF program headers of its own, so it inherits the consumer's alignment;
# these flags belong on the shared-object outputs a consumer links the
# archive into.
ANDROID_PAGE_FLAGS: tuple[str, ...] = (
    "-Wl,-z,max-page-size=16384",
    "-Wl,-z,common-page-size=16384",
)


class MobileRefusal(SpikeError):
    """A mobile target was asked for something it does not have.

    `code` is the stable code callers match on; the message names the fix.
    """

    def __init__(self, message: str, code: str = MOBILE_REFUSAL_CODE) -> None:
        super().__init__(code, message)


@dataclass(frozen=True)
class MobileTarget:
    """One mobile triple and the toolchain facts a build needs for it."""

    # The triple as the user writes it and as cc sees it.
    name: str
    cc_target: str
    # `ios` or `android`: picks the archive tool and the sysroot source.
    family: str


MOBILE_TARGETS: dict[str, MobileTarget] = {
    "aarch64-apple-ios": MobileTarget(
        "aarch64-apple-ios",
        "aarch64-apple-ios15.0",
        "ios",
    ),
    "aarch64-apple-ios-simulator": MobileTarget(
        "aarch64-apple-ios-simulator",
        "aarch64-apple-ios15.0-simulator",
        "ios",
    ),
    "aarch64-linux-android": MobileTarget(
        "aarch64-linux-android",
        "aarch64-linux-android",
        "android",
    ),
}


def normalize_target(target: str | None) -> str | None:
    """The canonical triple for `target`, or None when there is no target."""
    if target is None:
        return None
    if target in MOBILE_TARGETS:
        return MOBILE_TARGETS[target].name
    return target


def is_mobile_target(target: str | None) -> bool:
    """True when `target` is one of the three mobile triples."""
    return target is not None and target in MOBILE_TARGETS


def mobile_target(target: str) -> MobileTarget:
    """The resolved target record for a mobile triple.

    A triple carrying its own minimum version (`aarch64-apple-ios15.0`) still
    resolves when it names a supported platform; the version the compiler
    stamps is our choice, not the user's spelling.
    """
    if target in MOBILE_TARGETS:
        return MOBILE_TARGETS[target]
    for known in MOBILE_TARGETS.values():
        if target == known.cc_target or target.startswith(known.name.split("-ios")[0] + "-ios"):
            return known
    raise MobileRefusal(f"{target!r} is not a mobile target: one of {', '.join(sorted(MOBILE_TARGETS))}")


def require_mobile_target(target: str | None, what: str) -> MobileTarget:
    """The resolved target, or a refusal naming the three supported ones.

    `what` names the operation that needs a mobile target, so the message
    says which one failed.
    """
    if not is_mobile_target(target):
        raise MobileRefusal(
            f"{what} needs a mobile target: one of {', '.join(sorted(MOBILE_TARGETS))} (got {target!r})"
        )
    assert target is not None
    return mobile_target(target)


def excluded_sources(runtime: Path) -> list[Path]:
    """The runtime files a mobile build leaves out."""
    out: list[Path] = []
    for name in MOBILE_EXCLUDED_SOURCES:
        path = runtime / name
        if path.is_file():
            out.append(path)
    return out


# Lowered surfaces whose runtime lives in a file the mobile archive excludes.
# The fence carries the same code the desktop build would give for a broken
# surface use, but the message says what the mobile target is missing, so the
# failure lands at compile time instead of as an undefined symbol at link.
MOBILE_FENCE_HINTS: dict[str, str] = {
    "subprocess": (
        "subprocess spawns processes, and a mobile sandbox has no fork/exec: the mobile archive leaves ps_sub.c out"
    ),
    "osfd": ("raw file descriptors have no meaning inside an app sandbox: the mobile archive leaves ps_osfd.c out"),
}


# The runtime symbols the excluded files define. A lowered call that reaches
# one of them cannot link into a mobile archive, so the lowering fences it.
MOBILE_EXCLUDED_SYMBOLS: dict[str, str] = {
    "ps_sub_run": "subprocess",
    "ps_sub_call": "subprocess",
    "ps_sub_check_call": "subprocess",
    "ps_os_open": "osfd",
    "ps_os_close": "osfd",
    "ps_os_read": "osfd",
    "ps_os_write": "osfd",
    "ps_os_fstat": "osfd",
    "ps_os_lstat": "osfd",
    "ps_os_utime": "osfd",
}


def mobile_fence_hint(text: str) -> str | None:
    """The mobile sentence for a lowered surface, or None when it is not one.

    Matched on the surface key (`subprocess`, `osfd`), not on fence messages,
    so wording changes cannot silently unhook it.
    """
    if "subprocess" in text.lower():
        return MOBILE_FENCE_HINTS["subprocess"]
    if "osfd" in text.lower():
        return MOBILE_FENCE_HINTS["osfd"]
    return None
