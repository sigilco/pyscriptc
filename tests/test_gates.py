"""Gates: imports, containers, classes, and the first queued stdlib module."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}

QUEUE = (
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
