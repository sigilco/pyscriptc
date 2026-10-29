"""Closed import table, in the order the stdlib subset is lowered.

Every name in `QUEUE` has a lowered surface. Importing a module that is not
listed is refused. `typing` is intentionally absent; it stays in the checker.
A name added to `QUEUE` and left out of `LOWERED` imports cleanly and fences
with PS2101 on use.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from collections.abc import Iterator as _Iterator
from contextlib import contextmanager

from pyscriptc.ffi_manifest import FfiManifest
from pyscriptc.sigrec import (
    Sig,
    T,
    optional,
    sig_for,
    t_class,
    t_dict,
    t_leaf,
    t_list,
)

# Iterative order from the top-100 import scan. Do not reorder casually:
# tests lock this sequence.
QUEUE: tuple[str, ...] = (
    "math",
    "re",
    "json",
    "urllib.parse",
    "base64",
    "hashlib",
    "struct",
    "datetime",
    "time",
    "collections",
    "functools",
    "itertools",
    "operator",
    "contextlib",
    "os",
    "posixpath",
    "pathlib",
    "stat",
    "fnmatch",
    "warnings",
    "io",
    "sys",
    "importlib",
    "inspect",
    "threading",
    "logging",
    "asyncio",
    "subprocess",
    "socket",
    "ssl",
    "ctypes",
    "string",
    "bisect",
    "errno",
    "importlib.resources",
    "hmac",
    "unicodedata",
    "tempfile",
    "atexit",
    "shutil",
    "enum",
    "dataclasses",
    "random",
    "csv",
    "textwrap",
    "copy",
    "argparse",
    "tomllib",
    "dotenv",
    "yaml",
    "urllib.request",
    "urllib.error",
    "queue",
    "platform",
    "calendar",
    "zipfile",
    "tarfile",
    "uuid",
    "binascii",
    "shlex",
    "secrets",
    "selectors",
