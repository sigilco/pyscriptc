"""Table-driven differential tests for the lowered stdlib call table (issue #586).

Every fixed-signature row in the call table is exercised once: `MATH_FUNCTIONS`,
`WAVE_FUNCTIONS` and `REST_FIXED` plus the codec rows whose signature the
lowerer builds from the call (`SPECIAL_FUNCTIONS`). Each form gets a
representative argument set, and the forms are batched into a handful of tiny
programs so the module stays cheap under `-n 2`.

For each batch the same source is compiled to a native binary and run under
CPython 3.14; stdout and the exit status are compared. Each probe emits exactly
one stdout line, so a difference maps back to its table row. A batch whose only
differences are covered by a tracked divergence reference (a numbered ledger
entry or a tracking issue) is reported as an `xfail` naming that reference. Any
unrecorded difference fails with the probe key and both outputs. The coverage
test refuses an inventory row without a probe, so a new row cannot slip in
untested.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from pyscriptc.stdlib_queue import MATH_FUNCTIONS, SPECIAL_FUNCTIONS, WAVE_FUNCTIONS
from pyscriptc.stdmore import REST_FIXED

ROOT = Path(__file__).resolve().parents[1]
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}

# Divergence references for the rows this sweep currently records. Each one is
# a real difference from CPython 3.14 tracked as an issue; the entry is what
# makes the batch an xfail instead of a failure, and it must be removed when
# the row is fixed (the batch then passes on its own).
MIRRORED_DIVERGENCE = "issue #712 (unicodedata.mirrored return kind)"
PLATFORM_DIVERGENCE = "issue #713 (platform.platform output)"
REALPATH_DIVERGENCE = "issue #714 (os.path.realpath on a missing target)"


@dataclass(frozen=True)
class Probe:
    module: str
    member: str
    group: str
    lines: tuple[str, ...]
    divergence: str | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.module, self.member)


def _p(module: str, member: str, group: str, *lines: str, divergence: str | None = None) -> Probe:
    return Probe(module, member, group, tuple(lines), divergence)


PROBES: list[Probe] = [
    # math
    _p("math", "sqrt", "math", "print(math.sqrt(2.0))"),
    _p("math", "fabs", "math", "print(math.fabs(-3.5))"),
    _p("math", "sin", "math", "print(math.sin(0.5))"),
    _p("math", "cos", "math", "print(math.cos(0.5))"),
    _p("math", "tan", "math", "print(math.tan(0.5))"),
    _p("math", "exp", "math", "print(math.exp(0.5))"),
    _p("math", "log", "math", "print(math.log(2.0))"),
    _p("math", "log2", "math", "print(math.log2(10.0))"),
    _p("math", "log10", "math", "print(math.log10(50.0))"),
    _p("math", "hypot", "math", "print(math.hypot(2.0, 3.0))"),
    _p("math", "pow", "math", "print(math.pow(2.0, 0.5))"),
    _p("math", "atan2", "math", "print(math.atan2(1.0, 1.0))"),
    _p("math", "floor", "math", "print(math.floor(2.7))"),
    _p("math", "ceil", "math", "print(math.ceil(2.1))"),
    _p("math", "trunc", "math", "print(math.trunc(2.9))"),
    _p("math", "isnan", "math", "print(math.isnan(math.nan))"),
    _p("math", "isinf", "math", "print(math.isinf(math.inf))"),
    _p("math", "isfinite", "math", "print(math.isfinite(0.5))"),
    # re: wave rows plus the four special-cased calls
    _p("re", "escape", "re", "print(re.escape('a+b.c'))"),
    _p("re", "findall", "re", "print(len(re.findall('[0-9]+', 'a1b22')))"),
    _p("re", "sub", "re", "print(re.sub('[0-9]+', '#', 'a1b22'))"),
    _p("re", "split", "re", "print(len(re.split('[,;]', 'a,b;c')))"),
    _p("re", "compile", "re", "print(re.compile('[a-z]+').pattern)"),
    _p(
        "re",
        "search",
        "re",
        "m_search: re.Match | None = re.search('[0-9]+', 'a1b22')",
        "if m_search is not None:",
        "    print(m_search.group(0))",
    ),
    _p(
        "re",
        "match",
        "re",
        "m_match: re.Match | None = re.match('[a-z]+', 'abc')",
        "if m_match is not None:",
        "    print(m_match.group(0))",
    ),
    _p(
        "re",
        "fullmatch",
        "re",
        "m_full: re.Match | None = re.fullmatch('[a-z]+', 'abc')",
        "if m_full is not None:",
        "    print(m_full.group(0))",
    ),
    _p("re", "subn", "re", "print(re.subn('[0-9]+', '#', 'a1b22'))"),
    # base64
    _p("base64", "b64encode", "codec", "print(base64.b64encode(b'hello'))"),
    _p("base64", "standard_b64encode", "codec", "print(base64.standard_b64encode(b'hello'))"),
    _p("base64", "urlsafe_b64encode", "codec", "print(base64.urlsafe_b64encode(b'\\xfb\\xff'))"),
    _p("base64", "b64decode", "codec", "print(base64.b64decode(b'aGVsbG8='))"),
    _p("base64", "standard_b64decode", "codec", "print(base64.standard_b64decode(b'aGVsbG8='))"),
    _p("base64", "urlsafe_b64decode", "codec", "print(base64.urlsafe_b64decode(b'-_8='))"),
    # hashlib
    _p("hashlib", "md5", "codec", "print(hashlib.md5(b'abc').hexdigest())"),
    _p("hashlib", "sha1", "codec", "print(hashlib.sha1(b'abc').hexdigest())"),
    _p("hashlib", "sha256", "codec", "print(hashlib.sha256(b'abc').hexdigest())"),
    _p("hashlib", "sha384", "codec", "print(hashlib.sha384(b'abc').hexdigest())"),
    _p("hashlib", "sha512", "codec", "print(hashlib.sha512(b'abc').hexdigest())"),
    # urllib.parse
    _p("urllib.parse", "quote", "url", "print(urllib.parse.quote('a b/c'))"),
    _p("urllib.parse", "quote_plus", "url", "print(urllib.parse.quote_plus('a b/c'))"),
    _p("urllib.parse", "unquote", "url", "print(urllib.parse.unquote('a%20b'))"),
    _p("urllib.parse", "unquote_plus", "url", "print(urllib.parse.unquote_plus('a+b'))"),
    _p("urllib.parse", "urlencode", "url", "print(urllib.parse.urlencode({'a': 'b c', 'd': 'e'}))"),
    _p("urllib.parse", "parse_qs", "url", "print(len(urllib.parse.parse_qs('a=1&b=2')))"),
    _p("urllib.parse", "urlsplit", "url", "print(urllib.parse.urlsplit('http://h/a?q=1').path)"),
    # datetime
    _p("datetime", "date.today", "datetime", "print(datetime.date.today().year > 2020)"),
    _p(
        "datetime",
        "date.fromisoformat",
        "datetime",
        "print(datetime.date.fromisoformat('2020-01-02').isoformat())",
    ),
    _p("datetime", "date.fromordinal", "datetime", "print(datetime.date.fromordinal(737000).isoformat())"),
    _p("datetime", "date.fromtimestamp", "datetime", "print(datetime.date.fromtimestamp(0.0).isoformat())"),
    _p("datetime", "datetime.now", "datetime", "print(datetime.datetime.now().year > 2020)"),
    _p(
        "datetime",
        "datetime.fromisoformat",
        "datetime",
        "print(datetime.datetime.fromisoformat('2020-01-02T03:04:05').isoformat())",
    ),
    _p(
        "datetime",
        "datetime.fromtimestamp",
        "datetime",
        "print(datetime.datetime.fromtimestamp(0.0).isoformat())",
    ),
    _p(
        "datetime",
        "datetime.strptime",
        "datetime",
        "print(datetime.datetime.strptime('2020-01-02 03:04:05', '%Y-%m-%d %H:%M:%S').isoformat())",
    ),
    _p(
        "datetime",
        "datetime.combine",
        "datetime",
        "d_comb: datetime.date = datetime.date(2020, 1, 2)",
        "t_comb: datetime.time = datetime.time(3, 4, 5)",
        "print(datetime.datetime.combine(d_comb, t_comb).isoformat())",
    ),
    # time and calendar (calendar rows ride along for a current-interpreter read)
    _p("time", "time", "datetime", "print(time.time() > 0.0)"),
    _p("time", "monotonic", "datetime", "print(time.monotonic() > 0.0)"),
    _p("time", "strftime", "datetime", "print(time.strftime('%Y-%m-%d', time.gmtime(0)))"),
    _p("time", "mktime", "datetime", "print(time.mktime(time.gmtime(0)) > 0.0)"),
    _p("calendar", "isleap", "datetime", "print(calendar.isleap(2024))"),
    _p("calendar", "leapdays", "datetime", "print(calendar.leapdays(2000, 2024))"),
    _p("calendar", "weekday", "datetime", "print(calendar.weekday(2024, 1, 1))"),
    # pathlib, posixpath, stat, fnmatch
    _p("pathlib", "Path.cwd", "paths", "print(Path.cwd().is_absolute())"),
    _p("pathlib", "Path.home", "paths", "print(Path.home().is_absolute())"),
    _p("posixpath", "basename", "paths", "print(os.path.basename('/a/b.txt'))"),
    _p("posixpath", "dirname", "paths", "print(os.path.dirname('/a/b.txt'))"),
    _p("posixpath", "split", "paths", "print(len(os.path.split('/a/b.txt')))"),
    _p("posixpath", "splitext", "paths", "print(os.path.splitext('/a/b.txt')[1])"),
    _p("posixpath", "normpath", "paths", "print(os.path.normpath('/a/./b/../c'))"),
    _p("posixpath", "isabs", "paths", "print(os.path.isabs('/a'))"),
    _p("posixpath", "abspath", "paths", "print(os.path.isabs(os.path.abspath('rel/x')))"),
    _p("posixpath", "exists", "paths", "print(os.path.exists(path_file))"),
    _p("posixpath", "isfile", "paths", "print(os.path.isfile(path_file))"),
    _p("posixpath", "isdir", "paths", "print(os.path.isdir(path_dir))"),
    _p("posixpath", "expanduser", "paths", "print(os.path.expanduser('~').startswith('/'))"),
    _p("posixpath", "expandvars", "paths", "print(os.path.expandvars('$HOME/x').startswith('/'))"),
    _p(
        "posixpath",
        "realpath",
        "paths_divergent",
        "print(os.path.basename(os.path.realpath('a/../b')) == 'b')",
        divergence=REALPATH_DIVERGENCE,
    ),
    _p("posixpath", "getmtime", "paths", "print(os.path.getmtime(path_file) > 0.0)"),
    _p("posixpath", "getsize", "paths", "print(os.path.getsize(path_file))"),
    _p("posixpath", "getatime", "paths", "print(os.path.getatime(path_file) > 0.0)"),
    _p("posixpath", "islink", "paths", "print(os.path.islink(path_file))"),
    _p("posixpath", "splitdrive", "paths", "print(os.path.splitdrive('/a/b')[1] == '/a/b')"),
    _p("stat", "S_ISREG", "paths", "print(stat.S_ISREG(0o100644))"),
    _p("stat", "S_ISDIR", "paths", "print(stat.S_ISDIR(0o040755))"),
    _p("stat", "S_ISLNK", "paths", "print(stat.S_ISLNK(0o120777))"),
    _p("fnmatch", "fnmatch", "paths", "print(fnmatch.fnmatch('a.txt', '*.txt'))"),
    _p("fnmatch", "filter", "paths", "print(len(fnmatch.filter(['a.py', 'b.txt', 'c.py'], '*.py')))"),
    # os
    _p("os", "getcwd", "os", "print(os.getcwd().startswith('/'))"),
    _p("os", "listdir", "os", "print(len(os.listdir(os_dir)) >= 1)"),
    _p("os", "getenv", "os", "print(os.getenv('PYSC_DIFFTAB_UNSET') is None)"),
    _p(
        "os",
        "remove",
        "os",
        "p_remove: str = os.path.join(os_dir, 'remove.txt')",
        "with open(p_remove, 'w') as h_remove:",
        "    h_remove.write('x')",
        "os.remove(p_remove)",
        "print(not os.path.exists(p_remove))",
    ),
    _p(
        "os",
        "rename",
        "os",
        "p_ren_a: str = os.path.join(os_dir, 'ren_a.txt')",
        "with open(p_ren_a, 'w') as h_ren:",
        "    h_ren.write('x')",
        "p_ren_b: str = os.path.join(os_dir, 'ren_b.txt')",
        "os.rename(p_ren_a, p_ren_b)",
        "print(os.path.exists(p_ren_b))",
    ),
    _p(
        "os",
        "makedirs",
        "os",
        "p_made: str = os.path.join(os_dir, 'made', 'deep')",
        "os.makedirs(p_made)",
        "print(os.path.isdir(p_made))",
    ),
    _p(
        "os",
        "stat",
        "os",
        "st_file: os.stat_result = os.stat(os_file)",
        "print(st_file.st_size)",
    ),
    _p("os", "access", "os", "print(os.access(os_file, os.R_OK))"),
    _p("os", "getpid", "os", "print(os.getpid() > 0)"),
    _p(
        "os",
        "chdir",
        "os",
        "os.chdir(os_dir)",
        "print(os.getcwd().endswith(os.path.basename(os_dir)))",
        "os.chdir(os_cwd)",
    ),
    _p("os", "chmod", "os", "os.chmod(os_file, 0o644)", "print(os.path.exists(os_file))"),
    _p(
        "os",
        "mkdir",
        "os",
        "p_mkdir: str = os.path.join(os_dir, 'mkdir_one')",
        "os.mkdir(p_mkdir)",
        "print(os.path.isdir(p_mkdir))",
    ),
    _p(
        "os",
        "replace",
        "os",
        "p_rep_a: str = os.path.join(os_dir, 'rep_a.txt')",
        "with open(p_rep_a, 'w') as h_rep:",
        "    h_rep.write('x')",
        "p_rep_b: str = os.path.join(os_dir, 'rep_b.txt')",
        "os.replace(p_rep_a, p_rep_b)",
        "print(os.path.exists(p_rep_b))",
    ),
    _p(
        "os",
        "unlink",
        "os",
        "p_unlink: str = os.path.join(os_dir, 'unlink.txt')",
        "with open(p_unlink, 'w') as h_unlink:",
        "    h_unlink.write('x')",
        "os.unlink(p_unlink)",
        "print(not os.path.exists(p_unlink))",
    ),
    _p(
        "os",
        "close",
        "os",
        "fd_close: int = os.open(os_file, os.O_RDONLY)",
        "os.close(fd_close)",
        "print(True)",
    ),
    _p(
        "os",
        "write",
        "os",
        "fd_write: int = os.open(os_file, os.O_WRONLY)",
        "print(os.write(fd_write, b'ab') == 2)",
        "os.close(fd_write)",
    ),
    _p(
        "os",
        "fstat",
        "os",
        "fd_stat: int = os.open(os_file, os.O_RDONLY)",
        "st_fd: os.stat_result = os.fstat(fd_stat)",
        "print(st_fd.st_size == 3)",
        "os.close(fd_stat)",
    ),
    _p(
        "os",
        "lstat",
        "os",
        "st_link: os.stat_result = os.lstat(os_file)",
        "print(st_link.st_size == 3)",
    ),
    _p("os", "utime", "os", "os.utime(os_file)", "print(True)"),
    # shutil and tempfile
    _p(
        "shutil",
        "copyfile",
        "files",
        "shutil.copyfile(files_src, files_copyfile)",
        "print(os.path.exists(files_copyfile))",
    ),
    _p("shutil", "rmtree", "files", "shutil.rmtree(files_rmtree)", "print(not os.path.exists(files_rmtree))"),
    _p(
        "shutil",
        "which",
        "files",
        "which_path: str | None = shutil.which('sh')",
        "if which_path is not None:",
        "    print(which_path.endswith('sh'))",
    ),
    _p("shutil", "copy", "files", "shutil.copy(files_src, files_copy)", "print(os.path.exists(files_copy))"),
    _p("shutil", "copy2", "files", "shutil.copy2(files_src, files_copy2)", "print(os.path.exists(files_copy2))"),
    _p(
        "shutil",
        "copytree",
        "files",
        "shutil.copytree(files_tree_src, files_tree_dst)",
        "print(os.path.isdir(files_tree_dst))",
    ),
    _p(
        "shutil",
        "move",
        "files",
        "shutil.move(files_move_src, files_move_dst)",
        "print(os.path.exists(files_move_dst))",
    ),
    _p("shutil", "get_terminal_size", "files", "print(shutil.get_terminal_size().columns >= 0)"),
    _p("tempfile", "gettempdir", "files", "print(tempfile.gettempdir().startswith('/'))"),
    _p("tempfile", "gettempprefix", "files", "print(tempfile.gettempprefix())"),
    _p("tempfile", "mkdtemp", "files", "print(os.path.isdir(tempfile.mkdtemp()))"),
    # textwrap, unicodedata, inspect, warnings
    _p("textwrap", "dedent", "text", "print(repr(textwrap.dedent('  a\\n    b\\n')))"),
    _p("textwrap", "indent", "text", "print(repr(textwrap.indent('a\\nb\\n', '> ')))"),
    _p("unicodedata", "category", "text", "print(unicodedata.category('a'))"),
    _p("unicodedata", "combining", "text", "print(unicodedata.combining('a'))"),
    _p(
        "unicodedata",
        "normalize",
        "text",
        "print(unicodedata.normalize('NFC', 'caf\\u00e9') == 'caf\\u00e9')",
    ),
    _p("unicodedata", "is_normalized", "text", "print(unicodedata.is_normalized('NFC', 'caf\\u00e9'))"),
    _p("unicodedata", "bidirectional", "text", "print(unicodedata.bidirectional('a'))"),
    _p("unicodedata", "east_asian_width", "text", "print(unicodedata.east_asian_width('a'))"),
    _p("unicodedata", "mirrored", "text", "print(unicodedata.mirrored('('))", divergence=MIRRORED_DIVERGENCE),
    _p("unicodedata", "decimal", "text", "print(unicodedata.decimal('7'))"),
    _p("unicodedata", "digit", "text", "print(unicodedata.digit('7'))"),
    _p("unicodedata", "numeric", "text", "print(unicodedata.numeric('7'))"),
    _p("unicodedata", "decomposition", "text", "print(len(unicodedata.decomposition('a')) == 0)"),
    _p("unicodedata", "lookup", "text", "print(unicodedata.lookup('LATIN SMALL LETTER A'))"),
    _p("inspect", "cleandoc", "text", "print(repr(inspect.cleandoc('  a\\n    b')))"),
    _p("warnings", "warn", "text", "warnings.warn('dt')", "print(True)"),
    # platform, sys, socket, threading, subprocess, logging, zipfile, tarfile
    _p("platform", "system", "host", "print(platform.system())"),
    _p("platform", "machine", "host", "print(platform.machine())"),
    # The binary pins the declared target patch (3.14.9) while CI's oracle can
    # be a different 3.14 patch, so compare the target line, not the patch.
    _p(
        "platform",
        "python_version",
        "host",
        "print(platform.python_version().startswith('3.14.'))",
    ),
    _p("platform", "platform", "host", "print(platform.platform())", divergence=PLATFORM_DIVERGENCE),
    _p("sys", "stdout.write", "host", "print(sys.stdout.write('dt') == 2)"),
    _p("sys", "stderr.write", "host", "print(sys.stderr.write('dt') == 2)"),
    _p("socket", "gethostname", "host", "print(len(socket.gethostname()) > 0)"),
    _p("threading", "active_count", "host", "print(threading.active_count() >= 1)"),
    _p("subprocess", "check_output", "host", "print(subprocess.check_output(['echo', 'hi']))"),
    _p(
        "logging",
        "getLogger",
        "host",
        "log_probe: logging.Logger = logging.getLogger('dt')",
        "log_probe.info('dt')",
        "print(True)",
    ),
    _p("logging", "debug", "host", "print(logging.debug('dt') is None)"),
    _p("logging", "info", "host", "print(logging.info('dt') is None)"),
    _p("logging", "warning", "host", "print(logging.warning('dt') is None)"),
    _p("logging", "error", "host", "print(logging.error('dt') is None)"),
    _p("logging", "critical", "host", "print(logging.critical('dt') is None)"),
    _p("zipfile", "is_zipfile", "host", "print(zipfile.is_zipfile(host_file))"),
    _p("tarfile", "is_tarfile", "host", "print(tarfile.is_tarfile(host_file))"),
    # json and struct
    _p("json", "dumps", "data", "print(json.dumps({'a': 1, 'b': 2}))"),
    _p("json", "loads", "data", "v_json: dict[str, int] = json.loads('{\"a\": 7}')", "print(v_json['a'])"),
    _p("struct", "pack", "data", "print(struct.pack('>i', 42))"),
    _p("struct", "unpack", "data", "print(struct.unpack('>i', b'\\x00\\x00\\x00*')[0])"),
]

GROUP_IMPORTS: dict[str, tuple[str, ...]] = {
    "math": ("import math",),
    "re": ("import re",),
    "codec": ("import base64", "import hashlib"),
    "url": ("import urllib.parse",),
    "datetime": ("import calendar", "import datetime", "import time"),
    "paths": (
        "import fnmatch",
        "import os",
        "import stat",
        "import tempfile",
        "from pathlib import Path",
    ),
    "paths_divergent": ("import os",),
    "os": ("import os", "import tempfile"),
    "files": ("import os", "import shutil", "import tempfile"),
    "text": ("import inspect", "import textwrap", "import unicodedata", "import warnings"),
    "host": (
        "import logging",
        "import os",
        "import platform",
        "import socket",
        "import subprocess",
        "import sys",
        "import tarfile",
        "import tempfile",
        "import threading",
        "import zipfile",
    ),
    "data": ("import json", "import struct"),
}

GROUP_SETUP: dict[str, tuple[str, ...]] = {
    "paths": (
        "path_dir: str = tempfile.mkdtemp()",
        "path_file: str = os.path.join(path_dir, 'a.txt')",
        "with open(path_file, 'w') as h_paths:",
        "    h_paths.write('abc')",
    ),
    "os": (
        "os_dir: str = tempfile.mkdtemp()",
        "os_cwd: str = os.getcwd()",
        "os_file: str = os.path.join(os_dir, 'a.txt')",
        "with open(os_file, 'w') as h_os:",
        "    h_os.write('abc')",
    ),
    "files": (
        "files_dir: str = tempfile.mkdtemp()",
        "files_src: str = os.path.join(files_dir, 'src.txt')",
        "with open(files_src, 'w') as h_files:",
        "    h_files.write('abc')",
        "files_copyfile: str = os.path.join(files_dir, 'copyfile.txt')",
        "files_copy: str = os.path.join(files_dir, 'copy.txt')",
        "files_copy2: str = os.path.join(files_dir, 'copy2.txt')",
        "files_rmtree: str = os.path.join(files_dir, 'rmtree')",
        "os.mkdir(files_rmtree)",
        "files_tree_src: str = os.path.join(files_dir, 'tree')",
        "os.mkdir(files_tree_src)",
        "files_tree_dst: str = os.path.join(files_dir, 'tree2')",
        "files_move_src: str = os.path.join(files_dir, 'move.txt')",
        "with open(files_move_src, 'w') as h_move:",
        "    h_move.write('x')",
        "files_move_dst: str = os.path.join(files_dir, 'move2.txt')",
    ),
    "host": (
        "host_dir: str = tempfile.mkdtemp()",
        "host_file: str = os.path.join(host_dir, 'a.txt')",
        "with open(host_file, 'w') as h_host:",
        "    h_host.write('abc')",
    ),
}


def _inventory() -> set[tuple[str, str]]:
    rows: set[tuple[str, str]] = {("math", member) for member in MATH_FUNCTIONS}
    for module, members in WAVE_FUNCTIONS.items():
        rows.update((module, member) for member in members)
    for module, members in REST_FIXED.items():
        rows.update((module, member) for member in members)
    for module, members in SPECIAL_FUNCTIONS.items():
        rows.update((module, member) for member in members)
    return rows


def test_every_table_row_has_a_probe() -> None:
    inventory = _inventory()
    probed = {probe.key for probe in PROBES}
    missing = sorted(inventory - probed)
    extra = sorted(probed - inventory)
    assert not missing, f"call-table rows without a differential probe: {missing}"
    assert not extra, f"probes without a call-table row: {extra}"
    assert len(inventory) == 153
    assert len(PROBES) == 153


def _group_source(group: str) -> str:
    lines = list(GROUP_IMPORTS[group])
    lines.extend(["", "def run() -> None:"])
    body = list(GROUP_SETUP.get(group, ()))
    for probe in PROBES:
        if probe.group == group:
            body.extend(probe.lines)
    lines.extend(f"    {line}" for line in body)
    return "\n".join(lines) + "\n"


ORACLE_SCRIPT = """
import importlib.util
import sys

spec = importlib.util.spec_from_file_location("probe", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.run()
"""


def _compile_and_run(
    group: str, tmp_path: Path
) -> tuple[subprocess.CompletedProcess[str], subprocess.CompletedProcess[str]]:
    source = tmp_path / f"probe_{group}.py"
    source.write_text(_group_source(group), encoding="utf-8")
    binary = tmp_path / group
    compiled = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(source),
            "--emit",
            "bin",
            "--call",
            "run",
            "-o",
            str(binary),
        ],
        cwd=ROOT,
        env=ENV,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert compiled.returncode == 0, f"compile failed for group {group}:\n{compiled.stderr}"
    native = subprocess.run(
        [str(binary)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    oracle = subprocess.run(
        [sys.executable, "-c", ORACLE_SCRIPT, str(source)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    return native, oracle


GROUPS = sorted(GROUP_IMPORTS)


@pytest.mark.parametrize("group", GROUPS)
def test_call_table_group(group: str, tmp_path: Path) -> None:
    probes = [probe for probe in PROBES if probe.group == group]
    native, oracle = _compile_and_run(group, tmp_path)
    if native.returncode != oracle.returncode:
        if all(probe.divergence is not None for probe in probes):
            refs = sorted({probe.divergence for probe in probes})
            pytest.xfail(
                f"{group}: recorded divergence, exit status native={native.returncode} "
                f"cpython={oracle.returncode}: {', '.join(refs)}"
            )
        pytest.fail(
            f"{group}: exit status differs: native={native.returncode} cpython={oracle.returncode}\n"
            f"native stdout:\n{native.stdout}\nnative stderr:\n{native.stderr}\n"
            f"cpython stdout:\n{oracle.stdout}\ncpython stderr:\n{oracle.stderr}"
        )
    native_lines = native.stdout.splitlines()
    oracle_lines = oracle.stdout.splitlines()
    differences: list[tuple[Probe, str]] = []
    for index, probe in enumerate(probes):
        got = native_lines[index] if index < len(native_lines) else "<missing>"
        want = oracle_lines[index] if index < len(oracle_lines) else "<missing>"
        if got != want:
            differences.append((probe, f"{probe.module}.{probe.member}: native={got!r} cpython={want!r}"))
    if len(native_lines) != len(oracle_lines):
        differences.append(
            (
                probes[-1],
                f"line count: native={len(native_lines)} cpython={len(oracle_lines)}",
            )
        )
    unknown = [message for probe, message in differences if probe.divergence is None]
    if unknown:
        pytest.fail(f"{group}: unrecorded divergence from CPython 3.14:\n" + "\n".join(unknown))
    known = [f"{probe.divergence} ({message})" for probe, message in differences]
    if known:
        pytest.xfail(f"{group}: recorded divergence: " + "; ".join(known))
