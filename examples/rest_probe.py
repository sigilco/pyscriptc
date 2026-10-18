"""Clocks, containers, paths, object helpers, and the island.

`probe` returns 0 when the lowered calls match CPython.
"""

import collections
import ctypes
import datetime
import fnmatch
import functools
import inspect
import io
import itertools
import logging
import operator
import os
import pathlib
import posixpath
import socket
import ssl
import stat
import subprocess
import sys
import threading
import time
import warnings
from collections import deque
from contextlib import nullcontext, suppress


def add(a: int, b: int) -> int:
    return a + b


def probe() -> int:
    if datetime.date(1970, 1, 1).toordinal() != 719163:
        return 1
    if datetime.date(1, 1, 1).toordinal() != 1:
        return 2
    if datetime.date(2020, 1, 2).isoformat() != "2020-01-02":
        return 3
    parsed: datetime.date = datetime.date.fromisoformat("2020-01-02")
    if parsed.day != 2:
        return 4
    epoch: datetime.date = datetime.date.fromordinal(719163)
    if epoch.isoformat() != "1970-01-01":
        return 5
    if datetime.date(1970, 1, 1).weekday() != 3:
        return 6
    back: datetime.date = datetime.date(2020, 1, 1) + datetime.timedelta(0, -1)
    if back.isoformat() != "2019-12-31":
        return 7
    stay: datetime.date = datetime.date(2020, 1, 1) + datetime.timedelta(0, 86399)
    if stay.isoformat() != "2020-01-01":
        return 8
    step: datetime.timedelta = datetime.timedelta(1, 2, 3)
    if step.days != 1:
        return 9
    if step.seconds != 2:
        return 10
    if step.microseconds != 3:
        return 11
    if step.total_seconds() != 86402.000003:
        return 12
    flipped: datetime.timedelta = -step
    if flipped.days != -2:
        return 13
    if flipped.seconds != 86397:
        return 14
    if flipped.microseconds != 999997:
        return 15
    doubled: datetime.timedelta = step + step
    if doubled.days != 2:
        return 16
    if doubled.seconds != 4:
        return 17
    if datetime.datetime(2020, 1, 2, 3, 4, 5).isoformat() != "2020-01-02T03:04:05":
        return 18
    span: datetime.timedelta = datetime.date(2020, 1, 3) - datetime.date(2020, 1, 1)
    if span.days != 2:
        return 19
    gap: datetime.timedelta = datetime.datetime(2020, 1, 2, 3, 4, 5) - datetime.datetime(2020, 1, 1, 3, 4, 5)
    if gap.days != 1:
        return 20
    moved: datetime.datetime = datetime.datetime(2020, 1, 2, 3, 4, 5) + datetime.timedelta(0, 82800)
    if moved.isoformat() != "2020-01-03T02:04:05":
        return 21
    leap: datetime.date = datetime.date(2020, 2, 29)
    if leap.day != 29:
        return 22
    if datetime.date.today().year < 2020:
        return 23
    if datetime.datetime.now().year < 2020:
        return 24
    left: datetime.date = datetime.date(2020, 1, 1)
    right: datetime.date = datetime.date(2020, 1, 2)
    if left == right:
        return 25
    if not (left < right):
        return 26
    if datetime.time(1, 2, 3).isoformat() != "01:02:03":
        return 27
    when: datetime.datetime = datetime.datetime.fromisoformat("2020-01-02T03:04:05")
    if when.hour != 3:
        return 28
    if time.time() <= 1700000000.0:
        return 29
    if time.monotonic() < 0.0:
        return 30
    time.sleep(0)
    clock: time.struct_time = time.gmtime(0)
    if clock.tm_year != 1970:
        return 31
    if time.strftime("%Y-%m-%d", clock) != "1970-01-01":
        return 32
    xs: deque[int] = deque([1, 2])
    xs.appendleft(0)
    if xs.pop() != 2:
        return 33
    if xs.popleft() != 0:
        return 34
    if len(xs) != 1:
        return 35
    od: dict[str, int] = collections.OrderedDict()  # type: ignore[assignment]
    od["a"] = 3
    if od["a"] != 3:
        return 36
    if functools.reduce(add, [1, 2, 3]) != 6:
        return 37
    if functools.reduce(add, [1, 2, 3], 4) != 10:
        return 38
    total: int = 0
    for rep in itertools.repeat(2, 3):
        total = total + rep
    if total != 6:
        return 39
    chained: int = 0
    for piece in itertools.chain([1, 2], [3]):
        chained = chained + piece
    if chained != 6:
        return 40
    if operator.add(2, 3) != 5:
        return 41
    if operator.floordiv(-3, 2) != -2:
        return 42
    if operator.concat("a", "b") != "ab":
        return 43
    if not operator.eq(1, 1):
        return 44
    if operator.neg(-3) != 3:
        return 45
    with suppress(ValueError):
        raise ValueError("x")
    kept: int = 0
    with nullcontext():
        kept = kept + 1
    if kept != 1:
        return 46
    text_out: io.StringIO = open("/tmp/pyscriptc-rest.txt", "w")  # type: ignore[assignment]
    wrote: int = text_out.write("hi")
    text_out.close()
    if wrote != 2:
        return 47
    text_in: io.StringIO = open("/tmp/pyscriptc-rest.txt", "r")  # type: ignore[assignment]  # noqa: UP015
    body: str = text_in.read()
    text_in.close()
    if body != "hi":
        return 48
    bin_out: io.BytesIO = open("/tmp/pyscriptc-rest.bin", "wb")  # type: ignore[assignment]
    bin_wrote: int = bin_out.write(b"hi")
    bin_out.close()
    if bin_wrote != 2:
        return 49
    bin_in: io.BytesIO = open("/tmp/pyscriptc-rest.bin", "rb")  # type: ignore[assignment]
    blob: bytes = bin_in.read()
    bin_in.close()
    if blob != b"hi":
        return 50
    info: os.stat_result = os.stat("/tmp/pyscriptc-rest.txt")
    if info.st_size != 2:
        return 51
    if not stat.S_ISREG(info.st_mode):
        return 52
    path: pathlib.Path = pathlib.Path("/tmp/pyscriptc-rest.txt")
    if path.name != "pyscriptc-rest.txt":
        return 53
    if not path.is_file():
        return 54
    if path.read_text() != "hi":
        return 55
    parent: pathlib.Path = path.parent
    if parent.as_posix() != "/tmp":
        return 56
    rel: pathlib.Path = pathlib.Path("b.txt")
    rel_parent: pathlib.Path = rel.parent
    if rel_parent.as_posix() != ".":
        return 57
    named: pathlib.Path = pathlib.Path("a.tar.gz")
    if named.suffix != ".gz":
        return 58
    if named.stem != "a.tar":
        return 59
    if posixpath.join("/a", "/b") != "/b":
        return 60
    if posixpath.basename("/a/b") != "b":
        return 61
    if posixpath.splitext("a.tar.gz")[1] != ".gz":
        return 62
    if posixpath.normpath("/a/../b//c") != "/b/c":
        return 63
    if not posixpath.isabs("/a"):
        return 64
    if posixpath.isabs("a"):
        return 65
    nested: str = "/tmp/pyscriptc-rest-dir/nested"
    if not posixpath.isdir(nested):
        os.makedirs(nested)
    if not posixpath.isdir(nested):
        return 66
    if not stat.S_ISDIR(os.stat(nested).st_mode):
        return 67
    src: str = "/tmp/pyscriptc-rest-src.txt"
    dst: str = "/tmp/pyscriptc-rest-dst.txt"
    if posixpath.exists(dst):
        os.remove(dst)
    src_out: io.StringIO = open(src, "w")  # type: ignore[assignment]
    src_out.write("z")
    src_out.close()
    os.rename(src, dst)
    if not posixpath.exists(dst):
        return 68
    os.remove(dst)
    if posixpath.exists(dst):
        return 69
    env_path: str | None = os.getenv("PATH")
    if len(env_path or "") == 0:
        return 70
    entries: list[str] = os.listdir("/tmp")
    if len(entries) == 0:
        return 71
    if not fnmatch.fnmatch("Foo.txt", "*.txt"):
        return 72
    if fnmatch.fnmatch("Foo.txt", "*.TXT"):
        return 73
    matched: list[str] = fnmatch.filter(["a.txt", "b.dat"], "*.txt")
    if matched[0] != "a.txt":
        return 74
    if len(matched) != 1:
        return 75
    if abs(-3) != 3:
        return 76
    if min(3, 1) != 1:
        return 77
    if max(3, 1) != 3:
        return 78
    if sum([3, 1, 2]) != 6:
        return 79
    ordered: list[int] = sorted([3, 1, 2])
    if ordered[0] != 1:
        return 80
    if ordered[2] != 3:
        return 81
    if ord("A") != 65:
        return 82
    if chr(65) != "A":
        return 83
    if round(2.5) != 2:
        return 84
    if round(1.25, 1) != 1.2:
        return 85
    if not isinstance(1, int):
        return 86
    if isinstance(1, str):
        return 87
    if not isinstance(True, int):
        return 88
    point: datetime.date = datetime.date(2020, 1, 2)
    if not isinstance(point, datetime.date):
        return 89
    if getattr(point, "year") != 2020:  # noqa: B009
        return 90
    if not hasattr(point, "month"):
        return 91
    if hasattr(point, "nope"):
        return 92
    if sys.platform != "linux":
        return 93
    if sys.maxsize <= 0:
        return 94
    version: str = sys.version
    if not version.startswith("3.14"):
        return 95
    executable: str = sys.executable
    if len(executable) == 0:
        return 96
    if socket.AF_INET != 2:
        return 97
    openssl: str = ssl.OPENSSL_VERSION
    if not openssl.startswith("OpenSSL"):
        return 98
    if not ssl.HAS_SNI:
        return 99
    if ctypes.sizeof(ctypes.c_int) != 4:
        return 100
    host: str = socket.gethostname()
    if len(host) == 0:
        return 101
    if threading.active_count() < 1:
        return 102
    label: str = threading.current_thread().name
    if len(label) == 0:
        return 103
    echoed: bytes = subprocess.check_output(["/bin/echo", "hi"])
    if echoed != b"hi\n":
        return 104
    doc: str = inspect.cleandoc("  hi\n  there")
    if doc != "hi\nthere":
        return 105
    logging.info("rest")
    warnings.warn("rest")  # noqa: B028
    mem: io.StringIO = io.StringIO("ab")
    if mem.read() != "ab":
        return 106
    empty: io.StringIO = io.StringIO("")
    if empty.write("z") != 1:
        return 107
    if empty.getvalue() != "z":
        return 108
    raw: io.BytesIO = io.BytesIO(b"ab")
    if raw.read() != b"ab":
        return 109
    return 0


def iostream() -> int:
    n: int = sys.stdout.write("probe-out\n")
    if n != 10:
        return 1
    m: int = sys.stderr.write("probe-err\n")
    if m != 10:
        return 2
    print("print-err", file=sys.stderr)
    print("print-out", file=sys.stdout)
    return 0
