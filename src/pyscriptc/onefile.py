"""The onefile output of `--embed-python bundle`: one executable, extracted on first run.

Everything here is boxed-only code (the ledger counts this module whole; see
`.agents/docs/boxed-cost.md` and `AGENTS.md`).

A `--embed-python=bundle` program ships as a relocatable directory (`name/bin/name`
plus `name/lib/`). That shape loses the `files_n` dimension of the yt-dlp
composite against the PyInstaller onefile baseline (a 153 file tree against one
file), so the composite's onefile lever is a single executable:

- the stub: a small C `main` that finds the payload appended to its own file,
  inflates it on the first run into the cache (`$PYSC_ONEFILE_CACHE`, or
  `$XDG_CACHE_HOME/pyscriptc/onefile/<digest>`, or
  `~/.cache/pyscriptc/onefile/<digest>`) and execs the extracted `bin/<name>`
  with the process argv;
- the payload: a zlib-compressed archive of the bundle directory, appended
  after the stub with the footer the stub reads (payload sha256 hex, a
  newline, the payload length as little-endian uint64, the `PS1FILE\\n`
  magic).

The cache directory is the payload's sha256, so a rebuild with any byte changed
extracts beside the old tree instead of over it, and a run that races another
first run loses the atomic rename and execs the winner's directory.

The stub links zlib and nothing else: `tar` would be a runtime dependency the
payload's host may not have, and the archive format is ours, so the stub reads
it directly. The archive is a flat record stream: a path length, a mode, a
size, the path, then the bytes. Paths are relative and never absolute, and a
path with a `..` component is refused by the stub before it is written.
"""

from __future__ import annotations

import hashlib
import os
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path

MAGIC = b"PS1FILE\n"
FOOTER = len(MAGIC) + 8 + 65  # magic, payload length (LE uint64), 64 hex chars + newline


class OnefileError(Exception):
    """A user-facing onefile build failure (no traceback)."""


def cache_root() -> Path:
    """The extraction cache root: `$PYSC_ONEFILE_CACHE`, else the XDG/home cache."""
    override = os.environ.get("PYSC_ONEFILE_CACHE")
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "pyscriptc" / "onefile"
    return Path.home() / ".cache" / "pyscriptc" / "onefile"


STUB_SOURCE = r"""
/* The onefile stub: a POSIX C main that finds a zlib-compressed archive
   appended to its own file (payload bytes, then the footer: payload sha256
   hex, a newline, the payload length as little-endian uint64, the PS1FILE\n
   magic), inflates it into the cache directory named by the payload digest on
   the first run and execs the extracted binary with the process argv.

   The archive is a flat record stream: u32 path length, u32 mode, u64 size,
   the path bytes, then `size` bytes of content. A zero path length ends the
   stream. Paths are relative; a path that is absolute or holds a `..`
   component is refused before anything is written. */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include <limits.h>
#include <errno.h>
#include <zlib.h>

#define PS_TAIL 8
#define PS_FOOTER (PS_TAIL + 8 + 64 + 1)

/* The inflated stream: zlib in, files out. */
static z_stream ps_zs;
static FILE *ps_payload;
static long long ps_left;
static int ps_eof;

/* The inflate input buffer is static: next_in keeps pointing into it across
   iterations, so a local would hand the zlib state a dead stack frame. */
static unsigned char ps_in[1 << 16];

static int ps_fill(unsigned char *buf, unsigned len) {
    if (ps_eof) return -1;
    ps_zs.next_out = buf;
    ps_zs.avail_out = len;
    while (ps_zs.avail_out > 0) {
        if (ps_zs.avail_in == 0) {
            if (ps_left <= 0) { ps_eof = 1; return -1; }
            size_t chunk = ps_left < (long long)sizeof ps_in ? (size_t)ps_left : sizeof ps_in;
            size_t got = fread(ps_in, 1, chunk, ps_payload);
            if (got == 0) { ps_eof = 1; return -1; }
            ps_left -= (long long)got;
            ps_zs.next_in = ps_in;
            ps_zs.avail_in = (unsigned)got;
        }
        int rc = inflate(&ps_zs, Z_NO_FLUSH);
        if (rc == Z_STREAM_END) { ps_eof = 1; break; }
        if (rc != Z_OK && rc != Z_BUF_ERROR) {
            fprintf(stderr, "onefile: the payload does not inflate (%d)\n", rc);
            return -1;
        }
        if (rc == Z_BUF_ERROR && ps_zs.avail_in == 0 && ps_left <= 0) { ps_eof = 1; break; }
    }
    return (int)(len - ps_zs.avail_out);
}

/* Read exactly `len` bytes of the inflated stream. */
static int ps_read(void *dst, size_t len) {
    unsigned char *out = dst;
    size_t got = 0;
    while (got < len) {
        int n = ps_fill(out + got, (unsigned)(len - got));
        if (n <= 0) return -1;
        got += (size_t)n;
    }
    return 0;
}

static int ps_read_u32(unsigned *out) {
    unsigned char b[4];
    if (ps_read(b, 4) != 0) return -1;
    *out = (unsigned)b[0] | ((unsigned)b[1] << 8) | ((unsigned)b[2] << 16) | ((unsigned)b[3] << 24);
    return 0;
}

static int ps_read_u64(unsigned long long *out) {
    unsigned char b[8];
    if (ps_read(b, 8) != 0) return -1;
    unsigned long long v = 0;
    for (int i = 7; i >= 0; i--) v = (v << 8) | b[i];
    *out = v;
    return 0;
}

/* Create every parent directory of `path`. */
static int ps_mkdirs(const char *path) {
    char buf[PATH_MAX];
    size_t n = strlen(path);
    if (n >= sizeof buf) return -1;
    memcpy(buf, path, n + 1);
    for (size_t i = 1; i < n; i++) {
        if (buf[i] != '/') continue;
        buf[i] = '\0';
        if (mkdir(buf, 0755) != 0 && errno != EEXIST) return -1;
        buf[i] = '/';
    }
    return 0;
}

/* A path is safe when it is relative and holds no `..` component. */
static int ps_safe_path(const char *path) {
    if (path[0] == '/' || path[0] == '\0') return 0;
    const char *p = path;
    while (*p) {
        if (p[0] == '.' && p[1] == '.' && (p[2] == '/' || p[2] == '\0')) return 0;
        p++;
    }
    return 1;
}

static int ps_unpack(const char *dir) {
    for (;;) {
        unsigned path_n = 0;
        if (ps_read_u32(&path_n) != 0) break;
        if (path_n == 0) break;
        unsigned mode = 0;
        unsigned long long size = 0;
        if (ps_read_u32(&mode) != 0 || ps_read_u64(&size) != 0) {
            fprintf(stderr, "onefile: truncated payload header\n");
            return -1;
        }
        if (path_n >= 4096) {
            fprintf(stderr, "onefile: a payload path is too long\n");
            return -1;
        }
        char rel[4096];
        if (ps_read(rel, path_n) != 0) {
            fprintf(stderr, "onefile: truncated payload path\n");
            return -1;
        }
        rel[path_n] = '\0';
        if (!ps_safe_path(rel)) {
            fprintf(stderr, "onefile: refusing the payload path %s\n", rel);
            return -1;
        }
        char target[PATH_MAX];
        if (snprintf(target, sizeof target, "%s/%s", dir, rel) >= (int)sizeof target) {
            fprintf(stderr, "onefile: a payload path is too long\n");
            return -1;
        }
        if ((mode & 0170000) == 0040000) {
            /* A directory record: the mode's file-type bits say so. Every
               parent comes along inside the record list, but an empty
               directory (the lib-dynload landmark) has no file under it, so
               the record itself is the only thing that creates it. */
            if (mkdir(target, mode & 07777) != 0 && errno != EEXIST) {
                fprintf(stderr, "onefile: cannot create %s: %s\n", target, strerror(errno));
                return -1;
            }
            continue;
        }
        if (ps_mkdirs(target) != 0) {
            fprintf(stderr, "onefile: cannot create %s: %s\n", target, strerror(errno));
            return -1;
        }
        if ((mode & 0170000) == 0120000) {
            /* A symlink record: the body is the target path. */
            char link[4096];
            if (size >= sizeof link) {
                fprintf(stderr, "onefile: a symlink target is too long\n");
                return -1;
            }
            if (ps_read(link, (size_t)size) != 0) {
                fprintf(stderr, "onefile: truncated symlink target\n");
                return -1;
            }
            link[size] = '\0';
            if (symlink(link, target) != 0 && errno != EEXIST) {
                fprintf(stderr, "onefile: cannot link %s: %s\n", target, strerror(errno));
                return -1;
            }
            continue;
        }
        FILE *out = fopen(target, "wb");
        if (!out) {
            fprintf(stderr, "onefile: cannot write %s: %s\n", target, strerror(errno));
            return -1;
        }
        unsigned char buf[1 << 16];
        unsigned long long left = size;
        while (left > 0) {
            size_t chunk = left < (unsigned long long)sizeof buf ? (size_t)left : sizeof buf;
            if (ps_read(buf, chunk) != 0) {
                fprintf(stderr, "onefile: truncated payload body\n");
                fclose(out);
                return -1;
            }
            if (fwrite(buf, 1, chunk, out) != chunk) {
                fprintf(stderr, "onefile: cannot write %s\n", target);
                fclose(out);
                return -1;
            }
            left -= (unsigned long long)chunk;
        }
        fclose(out);
        chmod(target, mode & 0777);
    }
    return 0;
}

int main(int argc, char **argv) {
    char self[PATH_MAX];
    ssize_t n = readlink("/proc/self/exe", self, sizeof self - 1);
    if (n <= 0) {
        fprintf(stderr, "onefile: cannot read /proc/self/exe\n");
        return 2;
    }
    self[n] = '\0';
    struct stat sst;
    if (stat(self, &sst) != 0) { perror(self); return 2; }
    long long file_n = (long long)sst.st_size;
    FILE *f = fopen(self, "rb");
    if (!f) { perror(self); return 2; }
    unsigned char tail[PS_TAIL];
    if (file_n < PS_FOOTER || fseek(f, -PS_TAIL, SEEK_END) != 0 ||
        fread(tail, 1, PS_TAIL, f) != PS_TAIL || memcmp(tail, "PS1FILE\n", PS_TAIL) != 0) {
        fprintf(stderr, "onefile: no payload found in %s\n", self);
        return 2;
    }
    fseek(f, -(long long)(PS_TAIL + 8), SEEK_END);
    unsigned char szbuf[8];
    if (fread(szbuf, 1, 8, f) != 8) { fclose(f); return 2; }
    long long payload_n = 0;
    for (int i = 7; i >= 0; i--) payload_n = (payload_n << 8) | szbuf[i];
    if (payload_n <= 0 || payload_n > file_n - PS_FOOTER) {
        fprintf(stderr, "onefile: corrupt payload length in %s\n", self);
        return 2;
    }
    char hex[65];
    if (fseek(f, -(long long)PS_FOOTER, SEEK_END) != 0 || fread(hex, 1, 64, f) != 64) {
        fclose(f);
        return 2;
    }
    hex[64] = '\0';
    long long payload_at = file_n - PS_FOOTER - payload_n;

    /* The cache root: PYSC_ONEFILE_CACHE, else XDG_CACHE_HOME, else HOME. */
    const char *cache = getenv("PYSC_ONEFILE_CACHE");
    char home[PATH_MAX];
    if (cache && cache[0]) {
        snprintf(home, sizeof home, "%s", cache);
    } else {
        const char *xdg = getenv("XDG_CACHE_HOME");
        if (xdg && xdg[0]) {
            snprintf(home, sizeof home, "%s/pyscriptc/onefile", xdg);
        } else {
            const char *h = getenv("HOME");
            if (!h || !h[0]) {
                fprintf(stderr, "onefile: no HOME and no PYSC_ONEFILE_CACHE\n");
                return 2;
            }
            snprintf(home, sizeof home, "%s/.cache/pyscriptc/onefile", h);
        }
    }
    char target[PATH_MAX];
    snprintf(target, sizeof target, "%s/%s", home, hex);
    struct stat st;
    if (!(stat(target, &st) == 0 && S_ISDIR(st.st_mode))) {
        /* Extract into a private staging directory and rename it into place:
           a run that races another first run loses the rename and execs the
           winner's tree, whatever it is - both hold the same digest. */
        if (ps_mkdirs(home) != 0 || (mkdir(home, 0755) != 0 && errno != EEXIST)) {
            perror("onefile: the cache root");
            return 2;
        }
        char tmpl[PATH_MAX];
        snprintf(tmpl, sizeof tmpl, "%s/.extract-XXXXXX", home);
        char *dir = mkdtemp(tmpl);
        if (!dir) { perror("onefile: mkdtemp"); return 2; }
        if (fseek(f, payload_at, SEEK_SET) != 0) { perror(self); return 2; }
        ps_payload = f;
        ps_left = payload_n;
        ps_zs.zalloc = Z_NULL;
        ps_zs.zfree = Z_NULL;
        ps_zs.opaque = Z_NULL;
        if (inflateInit(&ps_zs) != Z_OK) {
            fprintf(stderr, "onefile: cannot start the inflater\n");
            return 2;
        }
        int failed = ps_unpack(dir);
        inflateEnd(&ps_zs);
        fclose(f);
        if (failed != 0) return 2;
        if (rename(dir, target) != 0 && errno != ENOTEMPTY && errno != EEXIST) {
            perror("onefile: rename");
            return 2;
        }
    } else {
        fclose(f);
    }
    char exe[PATH_MAX];
    snprintf(exe, sizeof exe, "%s/bin/%s", target, "@@ENTRY@@");
    char **newargv = calloc((size_t)argc + 1, sizeof *newargv);
    if (!newargv) { fprintf(stderr, "onefile: out of memory\n"); return 2; }
    newargv[0] = exe;
    for (int i = 1; i < argc; i++) newargv[i] = argv[i];
    execv(exe, newargv);
    fprintf(stderr, "onefile: exec %s: %s\n", exe, strerror(errno));
    return 2;
}
"""


def _compile_stub(entry: str, workdir: Path) -> Path:
    """Compile the stub `main` with the host cc; returns the stub binary.

    `cc` (not the program's own link driver) is enough: the stub is plain
    POSIX C with no pyscriptc runtime in it, and it has to run on the target
    host before anything else does. The stub links zlib, so the build host
    needs zlib's development files (`libz-dev` on Debian, `zlib-devel` on
    Fedora); the runtime needs only `libz.so.1`, which every glibc host and
    every slim container image carries.
    """
    import shutil as _shutil

    cc = _shutil.which("cc") or _shutil.which("gcc") or _shutil.which("clang")
    if cc is None:
        raise OnefileError("onefile needs a C compiler (cc, gcc or clang) on PATH to build the stub")
    source = workdir / "onefile_stub.c"
    source.write_text(STUB_SOURCE.replace("@@ENTRY@@", entry), encoding="utf-8")
    stub = workdir / "onefile_stub"
    done = subprocess.run(
        [cc, "-O2", "-o", str(stub), str(source), "-lz"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if done.returncode != 0:
        message = done.stderr.strip()
        if "zlib.h" in message or "cannot find -lz" in message:
            raise OnefileError(
                "the onefile stub needs zlib's development files (libz-dev on Debian, zlib-devel on Fedora): " + message
            )
        raise OnefileError(f"the onefile stub failed to compile: {message}")
    return stub


def _archive(bundle_dir: Path) -> bytes:
    """The archive of a bundle directory: the flat record stream, compressed.

    A directory record carries the directory's mode with the execute bits
    set (the stub reads that as "a directory"); a file record carries its
    mode. The paths are relative to the bundle directory, so the extracted
    tree is the bundle directory's own shape.
    """
    records = bytearray()
    for path in sorted(bundle_dir.rglob("*")):
        rel = str(path.relative_to(bundle_dir)).encode("utf-8")
        if path.is_dir():
            records += struct.pack("<IIQ", len(rel), 0o040000 | 0o755, 0)
            records += rel
            continue
        if path.is_symlink():
            # A symlink travels as a symlink record (the body is the target
            # path): the bundle's own symlinks are the libpython SONAME link,
            # whose target travels beside it, and a plain file would not be
            # found by the loader.
            body = os.readlink(path).encode("utf-8")
            mode = 0o120000 | 0o777
        else:
            body = path.read_bytes()
            mode = 0o100000 | (path.stat().st_mode & 0o777)
        records += struct.pack("<IIQ", len(rel), mode, len(body))
        records += rel
        records += body
    records += struct.pack("<I", 0)  # the end-of-stream marker
    return zlib.compress(bytes(records), 9)


def build(bundle_dir: Path, entry: str, output: Path, workdir: Path | None = None) -> Path:
    """Assemble the single executable of a bundle directory.

    The bundle directory (the `stage_bundle` output, `name/bin/name` plus
    `name/lib/`) is archived, compressed, hashed and appended after the
    compiled stub with the footer the stub reads. `entry` is the binary name
    inside `bin/` the stub execs. Returns the single-file path; the bundle
    directory stays on disk for whoever still wants the unpacked shape.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    work = Path(workdir) if workdir is not None else Path(tempfile.mkdtemp(prefix="pyscriptc-onefile-"))
    work.mkdir(parents=True, exist_ok=True)
    payload = _archive(bundle_dir)
    digest = hashlib.sha256(payload).hexdigest().encode("ascii")
    stub = _compile_stub(entry, work)
    with open(output, "wb") as out:
        out.write(stub.read_bytes())
        out.write(payload)
        out.write(digest)
        out.write(b"\n")
        out.write(struct.pack("<Q", len(payload)))
        out.write(MAGIC)
    output.chmod(0o755)
    if workdir is None:
        import shutil as _shutil

        _shutil.rmtree(work, ignore_errors=True)
    return output
