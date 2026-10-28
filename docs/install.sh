#!/bin/sh
# docs/install.sh: check the pyscriptc prerequisites, install what is missing,
# then install pyscriptc and run `pyscriptc doctor`.
#
# Safe to run any number of times: every step skips its work when it is
# already done. The script never runs sudo; for anything it will not install
# itself it prints the exact command to run as your normal user, and exits
# non-zero so the caller can decide what to do.
#
# Usage:
#   sh docs/install.sh           check prerequisites, print commands for anything missing
#   sh docs/install.sh --yes     also install uv and zig where it can, without asking
#
# With --yes the script may install:
#   - uv, via its official installer (https://astral.sh/uv/install.sh)
#   - zig, via mise when mise is on PATH, else via the platform package
#     manager (brew, apt, dnf or pacman) if that is on PATH
# It still never runs sudo: if the package manager needs root it prints the
# command and exits.

set -eu

ASSUME_YES=0
for arg in "$@"; do
    case "$arg" in
        --yes) ASSUME_YES=1 ;;
        -h|--help)
            sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "install.sh: unknown option: $arg (known: --yes, --help)" >&2
            exit 2
            ;;
    esac
done

say() { printf '%s\n' "$*"; }
err() { printf '%s\n' "$*" >&2; }

have() { command -v "$1" >/dev/null 2>&1; }

# Run a command only when we can see its result without prompting.
run_quiet() {
    say "+ $*"
    "$@" >/dev/null 2>&1
}

# --- C compiler or zig -------------------------------------------------------

install_zig_with_mise() {
    if ! have mise; then
        return 1
    fi
    say "+ mise use -g zig"
    mise use -g zig >/dev/null 2>&1
}

install_zig_with_pm() {
    if have brew; then
        say "+ brew install zig"
        brew install zig >/dev/null 2>&1
    elif have apt-get; then
        # apt needs root; refuse to guess and hand the caller the command.
        err "install zig with: sudo apt install zig"
        return 1
    elif have dnf; then
        err "install zig with: sudo dnf install zig"
        return 1
    elif have pacman; then
        err "install zig with: sudo pacman -S zig"
        return 1
    else
        return 1
    fi
}

ensure_toolchain() {
    if have zig || have clang; then
        return 0
    fi
    err "no C compiler found (need zig or clang on PATH)."
    if [ "$ASSUME_YES" -eq 1 ]; then
        if install_zig_with_mise; then
            return 0
        fi
        if install_zig_with_pm && (have zig || have clang); then
            return 0
        fi
        err "could not install zig automatically; install clang or zig and re-run."
    else
        err "re-run with --yes to install zig automatically (mise, or brew if present), or install one of:"
        err "  macOS:      xcode-select --install   (or: brew install zig)"
        err "  Debian/Ubuntu: sudo apt install clang"
        err "  Fedora:        sudo dnf install clang"
        err "  Arch:          sudo pacman -S clang"
        err "  any (mise):    mise use -g zig"
        err "  Windows:       winget install zig.zig"
    fi
    return 1
}

# --- uv ----------------------------------------------------------------------

install_uv() {
    if [ "$ASSUME_YES" -eq 1 ]; then
        say "+ curl -fsSL https://astral.sh/uv/install.sh | sh"
        curl -fsSL https://astral.sh/uv/install.sh | sh
        # The installer puts uv in ~/.local/bin (or $UV_INSTALL_DIR); make it
        # visible for the rest of this script without assuming a login shell.
        UV_BIN="$HOME/.local/bin"
        if [ -x "$UV_BIN/uv" ]; then
            PATH="$UV_BIN:$PATH"
            export PATH
        fi
    else
        err "uv is not installed. Install it with:"
        err "  curl -fsSL https://astral.sh/uv/install.sh | sh"
        err "or re-run this script with --yes to do that step automatically."
        return 1
    fi
}

ensure_uv() {
    if have uv; then
        return 0
    fi
    install_uv
}

# --- main --------------------------------------------------------------------

missing=0

ensure_toolchain || missing=1
ensure_uv || missing=1

if [ "$missing" -ne 0 ]; then
    err "prerequisites are missing; fix the items above and re-run."
    exit 1
fi

say "+ uv tool install pyscriptc"
uv tool install pyscriptc

say "+ pyscriptc doctor"
uv tool run pyscriptc doctor

say "done. Try: pyscriptc run hello.py"
