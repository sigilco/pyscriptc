"""Nuitka entry point for the release binary.

The default preflight is pyrefly, a native executable bundled under bin/ (see
`_point_pyrefly_at_bundle`). The release binary ships without mypy (an optional extra): mypy's
mypyc-compiled extension segfaults at import inside a Nuitka onefile on x86_64 Linux.
This shim points `sys.executable` and `PYREFLY_BIN` at the binary and its bundle,
then hands off to the normal CLI. It keeps src/ untouched.
"""

from __future__ import annotations

import sys


def _load_cli():
    """Import the CLI, tolerating errno and os constants that this OS lacks.

    TEMPORARY, remove after the A0 fix lands (see packaging/TOOLCHAIN.md, "Known issues"):
    stdmore.py reads every errno name at import time and macOS has no `errno.EADV` or
    `os.CLONE_FILES`, which kill the binary before it parses its arguments. Inert on a tree where the import works.
    The stand-in value is -1, so a script that names such a constant on macOS gets a wrong
    number until the real fix is in.
    """
    import importlib
    import re

    for _ in range(1000):
        try:
            from pyscriptc.cli import main as cli_main
        except AttributeError as exc:
            hit = re.search(r"module '(\w+)' has no attribute '(\w+)'", str(exc))
            if hit is None:
                raise
            for name in [m for m in list(sys.modules) if m == "pyscriptc" or m.startswith("pyscriptc.")]:
                del sys.modules[name]
            setattr(importlib.import_module(hit.group(1)), hit.group(2), -1)
        else:
            return cli_main
    raise RuntimeError("too many missing platform constants")


def _point_executable_at_binary() -> None:
    """Make `sys.executable` the release binary.

    Under onefile Nuitka reports `<extract dir>/python`, which does not exist, so a
    subprocess spawning `sys.executable` cannot start. `sys.argv[0]` is the path the user ran; resolve it
    through PATH when it was a bare command name.
    """
    import os
    import shutil

    argv0 = sys.argv[0]
    found = argv0 if os.sep in argv0 else shutil.which(argv0)
    if found and os.path.isfile(found):
        sys.executable = os.path.abspath(found)


def _point_pyrefly_at_bundle() -> None:
    """Use the pyrefly executable bundled under bin/ unless PYREFLY_BIN already overrides it."""
    import os
    import stat

    if os.environ.get("PYREFLY_BIN"):
        return
    name = "pyrefly.exe" if os.name == "nt" else "pyrefly"
    bundled = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin", name)
    if os.path.isfile(bundled):
        mode = os.stat(bundled).st_mode
        if not mode & stat.S_IXUSR:
            os.chmod(bundled, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        os.environ["PYREFLY_BIN"] = bundled


def main() -> int:
    _point_executable_at_binary()
    _point_pyrefly_at_bundle()
    cli_main = _load_cli()

    return cli_main()


if __name__ == "__main__":
    sys.exit(main())
