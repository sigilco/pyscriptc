/* io.StringIO, io.BytesIO, and builtin open.

Memory buffers start at position 0, so read() returns the initial value.
File writes are flushed so a later open sees them. OS errors are RuntimeError.
*/

#include "ps_rt.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

typedef struct PsIO {
    int32_t kind;
    int32_t closed;
    PsGrow mem;
    int64_t pos;
    FILE *fp;
    /* Set for tempfile.NamedTemporaryFile(delete=True): unlinked on close. */
    char *tmp_path;
    /* The fopen mode letters ("r", "rb", ...): text reads use it to decide
       whether universal-newline translation applies. */
    const char *how;
    /* The byte offset of the file position: the reads and the writes keep
       it, so a `read(n)` after a `readline()` continues where the last
       read stopped (CPython's position semantics). */
    int64_t tell;
    /* The text codec of a file handle: 0 UTF-8 (the default, PEP 686), 1
       ASCII, 2 Latin-1. The file bytes are decoded to UTF-8 str bytes on
       the way up and str bytes are encoded on the way down. */
    int32_t enc;
} PsIO;

/* The mode word `open` hands over: the low nibble is the mode code and the
   next nibble the text codec (`encoding=`). */
#define PS_ENC_SHIFT 4
#define PS_ENC_ASCII 1
#define PS_ENC_LATIN1 2

static PsIO *ps_blank(int kind) {
    PsIO *io = calloc(1, sizeof(PsIO));
    if (io == NULL) {
        abort();
    }
    io->kind = kind;
    ps_grow_init(&io->mem);
    return io;
}

static void ps_open_check(PsIO *io) {
    if (io == NULL || io->closed) {
        ps_raise_cstr(PS_VALUE, "I/O operation on closed file");
    }
}

PsIO *ps_io_string(int64_t n, const char *s) {
    PsIO *io = ps_blank(1);
    if (n > 0) {
        ps_grow_add(&io->mem, s, n);
    }
    return io;
}

PsIO *ps_io_bytes(int64_t n, const char *s) {
    PsIO *io = ps_blank(2);
    if (n > 0) {
        ps_grow_add(&io->mem, s, n);
    }
    return io;
}

static const char *ps_open_how(int64_t mode) {
    if (mode == 1) {
        return "r";
    }
    if (mode == 2) {
        return "w";
    }
    if (mode == 3) {
        return "rb";
    }
    if (mode == 4) {
        return "wb";
    }
    if (mode == 5) {
        return "a";
    }
    if (mode == 6) {
        return "ab";
    }
    if (mode == 7) {
        return "w+";
    }
    if (mode == 8) {
        return "w+b";
    }
    return NULL;
}

static int32_t ps_open_kind(int64_t mode) { return mode == 3 || mode == 4 || mode == 6 || mode == 8 ? 4 : 3; }

/* tempfile.NamedTemporaryFile hands over an fd that mkstemp created. The
   path is unlinked on close unless `keep` is set (delete=False). */
PsIO *ps_io_take_fd(int fd, const char *path, int64_t mode, int64_t keep) {
    const char *how = ps_open_how(mode);
    FILE *fp;
    PsIO *io;
    if (how == NULL) {
        close(fd);
        ps_raise_cstr(PS_VALUE, "open mode is not lowered");
    }
    fp = fdopen(fd, how);
    if (fp == NULL) {
        close(fd);
        ps_raise_cstr(PS_RUNTIME, "open failed");
    }
    io = ps_blank(ps_open_kind(mode));
    io->fp = fp;
    io->how = ps_open_how(mode);
    if (!keep) {
        size_t len = strlen(path);
        io->tmp_path = malloc(len + 1);
        if (io->tmp_path == NULL) {
            abort();
        }
        memcpy(io->tmp_path, path, len + 1);
    }
    return io;
}

PsIO *ps_io_open(int64_t n, const char *path, int64_t mode) {
    int32_t enc = (int32_t)(mode >> PS_ENC_SHIFT);
    const char *how;
    mode &= (1 << PS_ENC_SHIFT) - 1;
    how = ps_open_how(mode);
    int kind;
    char *text;
    FILE *fp;
    PsIO *io;
    if (how == NULL) {
        ps_raise_cstr(PS_VALUE, "open mode is not lowered");
    }
    kind = ps_open_kind(mode);
    text = malloc((size_t)n + 1);
    if (text == NULL) {
        abort();
    }
    if (n > 0) {
        memcpy(text, path, (size_t)n);
    }
    text[n] = '\0';
    fp = fopen(text, how);
    free(text);
    if (fp == NULL) {
        ps_raise_cstr(PS_RUNTIME, "open failed");
    }
    io = ps_blank(kind);
    io->fp = fp;
    io->how = how;
    io->enc = enc;
    return io;
}

static int ps_can_read(PsIO *io) {
    if (io->kind == 1 || io->kind == 2) {
        return 1;
    }
    return io->fp != NULL;
}

/* The mode letters the handle opened with ("r", "rb", ...). A handle with
   no `how` (StringIO, a taken fd) is text by construction. */
static const char *ps_io_mode_of(PsIO *io) {
    return io->how != NULL ? io->how : "r";
}

/* The text decode on the way up: a text file handle's bytes become the
   UTF-8 bytes a str holds. UTF-8 (the default) validates strictly and
   raises UnicodeDecodeError on a bad sequence, as CPython's strict errors
   do; ASCII rejects a byte above 0x7f; Latin-1 lifts each byte to its code
   point. A binary handle and a memory handle pass through. */
static void ps_io_decode(PsIO *io, PsBuf *buf) {
    if (io->fp == NULL || strchr(ps_io_mode_of(io), 'b') != NULL || buf->len == 0) {
        return;
    }
    if (io->enc == PS_ENC_LATIN1) {
        PsBuf lifted;
        ps_latin1_decode(buf->len, buf->data, &lifted);
        free(buf->data);
        *buf = lifted;
        return;
    }
    if (io->enc == PS_ENC_ASCII) {
        for (int64_t i = 0; i < buf->len; i++) {
            if ((unsigned char)buf->data[i] >= 0x80) {
                char msg[160];
                snprintf(msg, sizeof msg, "'ascii' codec can't decode byte 0x%02x in position %lld: ordinal not in range(128)",
                         (unsigned)(unsigned char)buf->data[i], (long long)i);
                ps_raise_cstr(PS_UNICODEDECODE, msg);
            }
        }
        return;
    }
    ps_utf8_require(buf->len, buf->data, NULL);
}

/* The text encode on the way down for a non-UTF-8 handle: the str bytes
   (UTF-8) become one byte per code point. `limit` is 0x80 for ASCII and
   0x100 for Latin-1; a code point at or above it is UnicodeEncodeError. */
static void ps_io_encode(int64_t n, const char *s, unsigned int limit, PsBuf *out) {
    PsGrow grow;
    int64_t at = 0;
    int64_t pos = 0;
    ps_grow_init(&grow);
    while (at < n) {
        unsigned char c = (unsigned char)s[at];
        unsigned int cp = c;
        int w = 1;
        if (c >= 0xF0) {
            w = 4;
            cp = c & 0x07;
        } else if (c >= 0xE0) {
            w = 3;
            cp = c & 0x0F;
        } else if (c >= 0xC0) {
            w = 2;
            cp = c & 0x1F;
        }
        for (int k = 1; k < w && at + k < n; k++) {
            cp = (cp << 6) | ((unsigned char)s[at + k] & 0x3F);
        }
        if (cp >= limit) {
            char msg[160];
            free(grow.data);
            snprintf(msg, sizeof msg, "'%s' codec can't encode character '\\u%04x' in position %lld: ordinal not in range(%u)",
                     limit == 0x80 ? "ascii" : "latin-1", cp, (long long)pos, limit);
            ps_raise_cstr(PS_UNICODEENCODE, msg);
        }
        ps_grow_byte(&grow, (unsigned char)cp);
        at += w;
        pos++;
    }
    ps_grow_finish(&grow, out);
}

/* CPython's universal newlines: in text mode a `\r\n` pair and a lone `\r`
   read as `\n` (the default `newline=None`); `\n` passes through. Applied
   to `read` and `readline` on every text-mode handle; binary mode is
   untouched. `newline=` is fenced on `open`, so the default is the whole
   story. */
static void ps_universal_newlines(char *buf, int64_t size, int64_t *out_len) {
    int64_t r = 0;
    int64_t w = 0;
    while (r < size) {
        char c = buf[r];
        if (c == '\r') {
            buf[w++] = '\n';
            r++;
            if (r < size && buf[r] == '\n') {
                r++;
            }
        } else {
            buf[w++] = c;
            r++;
        }
    }
    *out_len = w;
}

void ps_io_read(PsIO *io, PsBuf *out) {
    ps_open_check(io);
    if (!ps_can_read(io)) {
        ps_raise_cstr(PS_VALUE, "read failed");
    }
    if (io->fp != NULL) {
        long size;
        long left;
        char *buf;
        if (fseek(io->fp, 0, SEEK_END) != 0) {
            ps_raise_cstr(PS_RUNTIME, "read failed");
        }
        size = ftell(io->fp);
        if (size < 0 || fseek(io->fp, io->tell, SEEK_SET) != 0) {
            ps_raise_cstr(PS_RUNTIME, "read failed");
        }
        left = size - (long)io->tell;
        if (left < 0) {
            left = 0;
        }
        buf = malloc((size_t)left + 1);
        if (buf == NULL) {
            abort();
        }
        if (left > 0 && fread(buf, 1, (size_t)left, io->fp) != (size_t)left) {
            free(buf);
            ps_raise_cstr(PS_RUNTIME, "read failed");
        }
        buf[left] = '\0';
        io->tell += left;
        /* Text mode translates: a text handle's `how` never contains 'b'. */
        if (left > 0 && !strchr(ps_io_mode_of(io), 'b')) {
            int64_t shrunk = left;
            ps_universal_newlines(buf, left, &shrunk);
            buf[shrunk] = '\0';
            out->len = shrunk;
        } else {
            out->len = left;
        }
        out->data = buf;
        ps_io_decode(io, out);
        return;
    }
    if (io->pos > io->mem.len) {
        io->pos = io->mem.len;
    }
    ps_buf_copy(out, io->mem.data == NULL ? "" : io->mem.data + io->pos, io->mem.len - io->pos);
    io->pos = io->mem.len;
}

/* `read(n)`: at most `n` items from the current position. A text handle
   counts code points (CPython's `read(n)` on a text stream returns n
   characters), a binary handle counts bytes. The read stops short at EOF
   and returns an empty buffer there, like CPython. A negative `n` reads
   the whole rest of the stream, as CPython's `read(-1)` does. */
void ps_io_read_n(PsIO *io, int64_t n, PsBuf *out) {
    ps_open_check(io);
    if (!ps_can_read(io)) {
        ps_raise_cstr(PS_VALUE, "read failed");
    }
    if (n < 0) {
        ps_io_read(io, out);
        return;
    }
    if (io->fp != NULL) {
        /* Binary mode: bytes straight from the stream. The buffer grows as
           the stream gives, so a large `n` does not reserve the whole
           budget up front. */
        if (strchr(ps_io_mode_of(io), 'b')) {
            PsGrow grow;
            int64_t got = 0;
            ps_grow_init(&grow);
            while (got < n) {
                int64_t room = n - got;
                char chunk[4096];
                size_t want = room < (int64_t)sizeof(chunk) ? (size_t)room : sizeof(chunk);
                size_t read_now = fread(chunk, 1, want, io->fp);
                if (read_now == 0) {
                    break;
                }
                ps_grow_add(&grow, chunk, (int64_t)read_now);
                got += (int64_t)read_now;
            }
            if (ferror(io->fp)) {
                free(grow.data);
                ps_raise_cstr(PS_RUNTIME, "read failed");
            }
            ps_grow_finish(&grow, out);
            io->tell += got;
            return;
        }
        /* Text mode: the budget counts code points the way this str model
           counts them (a continuation byte 10xxxxxx never starts one), so
           the read stops on the `n`-th code point and keeps whole
           sequences. Universal newlines translate inside the budget, the
           way CPython counts the translated characters. */
        {
            PsGrow grow;
            int64_t chars = 0;
            int64_t bytes = 0;
            ps_grow_init(&grow);
            while (chars < n) {
                int ch = fgetc(io->fp);
                if (ch == EOF) {
                    break;
                }
                bytes++;
                if (ch == '\r') {
                    /* Universal newlines: a `\r\n` pair reads as one `\n`. */
                    int next = fgetc(io->fp);
                    if (next != '\n' && next != EOF) {
                        ungetc(next, io->fp);
                    } else if (next == '\n') {
                        bytes++;
                    }
                    ps_grow_byte(&grow, '\n');
                    chars++;
                    continue;
                }
                ps_grow_byte(&grow, (unsigned char)ch);
                if (io->enc != 0) {
                    /* ASCII and Latin-1: one byte is one code point. */
                    chars++;
                    continue;
                }
                if (((unsigned char)ch & 0xC0) != 0x80) {
                    chars++;
                    if (chars >= n) {
                        /* The continuation bytes of this sequence belong to
                           the code point the budget just counted, so they
                           ride out with it. */
                        for (;;) {
                            int cont = fgetc(io->fp);
                            if (cont == EOF) {
                                break;
                            }
                            if (((unsigned char)cont & 0xC0) != 0x80) {
                                ungetc(cont, io->fp);
                                break;
                            }
                            bytes++;
                            ps_grow_byte(&grow, (unsigned char)cont);
                        }
                        break;
                    }
                }
            }
            if (ferror(io->fp)) {
                free(grow.data);
                ps_raise_cstr(PS_RUNTIME, "read failed");
            }
            ps_grow_finish(&grow, out);
            io->tell += bytes;
            ps_io_decode(io, out);
            return;
        }
    }
    /* A memory handle (BytesIO, StringIO): the budget counts the stored
       bytes for a binary handle and the code points for a text one, the
       way CPython's StringIO counts characters. */
    if (io->pos > io->mem.len) {
        io->pos = io->mem.len;
    }
    if (io->kind == 2 || n == 0) {
        int64_t left = io->mem.len - io->pos;
        int64_t take = n < left ? n : left;
        ps_buf_copy(out, io->mem.data == NULL ? "" : io->mem.data + io->pos, take);
        io->pos += take;
        return;
    }
    {
        int64_t chars = 0;
        int64_t at = io->pos;
        while (at < io->mem.len) {
            unsigned char ch = (unsigned char)io->mem.data[at];
            at++;
            if ((ch & 0xC0) != 0x80) {
                chars++;
                if (chars >= n) {
                    /* The continuation bytes of this sequence ride out with
                       the code point the budget just counted. */
                    while (at < io->mem.len && ((unsigned char)io->mem.data[at] & 0xC0) == 0x80) {
                        at++;
                    }
                    break;
                }
            }
        }
        ps_buf_copy(out, io->mem.data == NULL ? "" : io->mem.data + io->pos, at - io->pos);
        io->pos = at;
    }
}

/* The code points in `n` bytes of UTF-8: what a text `write` returns (the
   length of the str, not its byte count). */
static int64_t ps_cp_count(int64_t n, const char *s) {
    int64_t count = 0;
    for (int64_t i = 0; i < n; i++) {
        if (((unsigned char)s[i] & 0xC0) != 0x80) {
            count++;
        }
    }
    return count;
}

/* A text handle (a file opened without `b`, or a StringIO) returns the
   code points written; a binary handle returns bytes. */
static int64_t ps_io_written(PsIO *io, int64_t n, const char *s) {
    if (io->kind == 2 || (io->fp != NULL && strchr(ps_io_mode_of(io), 'b') != NULL)) {
        return n;
    }
    return ps_cp_count(n, s);
}

int64_t ps_io_write(PsIO *io, int64_t n, const char *s) {
    ps_open_check(io);
    if (n < 0) {
        ps_raise_code(PS_VALUE);
    }
    if (io->fp != NULL) {
        PsBuf coded;
        int64_t m = n;
        const char *bytes = s;
        coded.data = NULL;
        if (io->enc != 0 && strchr(ps_io_mode_of(io), 'b') == NULL) {
            /* The text encode on the way down: ASCII refuses a code point
               above 0x7f, Latin-1 above 0xff, both with UnicodeEncodeError. */
            ps_io_encode(n, s, io->enc == PS_ENC_LATIN1 ? 0x100u : 0x80u, &coded);
            bytes = coded.data;
            m = coded.len;
        }
        if (m > 0 && fwrite(bytes, 1, (size_t)m, io->fp) != (size_t)m) {
            free(coded.data);
            ps_raise_cstr(PS_RUNTIME, "write failed");
        }
        free(coded.data);
        if (fflush(io->fp) != 0) {
            ps_raise_cstr(PS_RUNTIME, "write failed");
        }
        io->tell += m;
        return ps_io_written(io, n, s);
    }
    if (io->pos > io->mem.len) {
        while (io->mem.len < io->pos) {
            ps_grow_byte(&io->mem, '\0');
        }
    }
    if (io->pos == io->mem.len) {
        ps_grow_add(&io->mem, s, n);
        io->pos = io->mem.len;
        return ps_io_written(io, n, s);
    }
    if (io->mem.cap < io->pos + n + 1) {
        ps_grow_add(&io->mem, "", 0);
        while (io->mem.cap < io->pos + n + 1) {
            ps_grow_byte(&io->mem, '\0');
            io->mem.len -= 1;
        }
    }
    if (n > 0) {
        memcpy(io->mem.data + io->pos, s, (size_t)n);
    }
    if (io->pos + n > io->mem.len) {
        io->mem.len = io->pos + n;
        io->mem.data[io->mem.len] = '\0';
    }
    io->pos += n;
    return ps_io_written(io, n, s);
}

void ps_io_readline(PsIO *io, PsBuf *out) {
    /* `for line in f` / f.readline(): bytes through the next '\n' (kept),
       an empty buffer at EOF. Text mode translates `\r\n` and `\r` to `\n`
       like CPython's universal newlines. */
    PsGrow line;
    ps_open_check(io);
    if (!ps_can_read(io)) {
        ps_raise_cstr(PS_VALUE, "readline is lowered for readable streams");
    }
    ps_grow_init(&line);
    if (io->fp != NULL) {
        int ch;
        int text = !strchr(ps_io_mode_of(io), 'b');
        int64_t bytes = 0;
        for (;;) {
            ch = fgetc(io->fp);
            if (ch == EOF) {
                break;
            }
            bytes++;
            if (text && ch == '\r') {
                ps_grow_byte(&line, '\n');
                int next = fgetc(io->fp);
                if (next != '\n' && next != EOF) {
                    ungetc(next, io->fp);
                } else if (next == '\n') {
                    bytes++;
                }
            } else {
                ps_grow_byte(&line, (unsigned char)ch);
            }
            if (ch == '\n' || (text && ch == '\r')) {
                break;
            }
        }
        if (ferror(io->fp)) {
            free(line.data);
            ps_raise_cstr(PS_RUNTIME, "readline failed");
        }
        ps_grow_finish(&line, out);
        io->tell += bytes;
        ps_io_decode(io, out);
        return;
    }
    if (io->pos > io->mem.len) {
        io->pos = io->mem.len;
    }
    while (io->pos < io->mem.len) {
        unsigned char ch = (unsigned char)io->mem.data[io->pos];
        io->pos += 1;
        ps_grow_byte(&line, ch);
        if (ch == '\n') {
            break;
        }
    }
    ps_grow_finish(&line, out);
}

void ps_io_close(PsIO *io) {
    if (io->closed) {
        return;
    }
    if (io->tmp_path != NULL) {
        unlink(io->tmp_path);
        free(io->tmp_path);
        io->tmp_path = NULL;
    }
    if (io->fp != NULL && fclose(io->fp) != 0) {
        io->fp = NULL;
        io->closed = 1;
        ps_raise_cstr(PS_RUNTIME, "close failed");
    }
    io->fp = NULL;
    io->closed = 1;
}

void ps_io_value(PsIO *io, PsBuf *out) {
    ps_open_check(io);
    if (io->fp != NULL) {
        ps_raise_cstr(PS_VALUE, "getvalue is lowered for StringIO and BytesIO");
    }
    ps_buf_copy(out, io->mem.data == NULL ? "" : io->mem.data, io->mem.len);
}

/* `sys.stdout.write`/`sys.stderr.write` return the chars written, like CPython. */

/* The stderr stream for `print(file=sys.stderr)`. On Darwin `stderr` is a
macro for `__stderrp`, so the emitted code cannot name the symbol; the
runtime hands the FILE* over instead. */
void *ps_stderr_stream(void) { return stderr; }

void *ps_stdout_stream(void) { return stdout; }

/* Write len bytes to a stream: the size_t arguments of fwrite stay in C, so
   a wasm32 target (32-bit size_t) never sees an i64 call signature. */
void ps_stream_write(void *stream, const char *data, int64_t len) { fwrite(data, 1, (size_t)len, (FILE *)stream); }

int64_t ps_stdout_write(int64_t n, const char *s) {
    if (n < 0) {
        ps_raise_code(PS_VALUE);
    }
    if (n > 0 && fwrite(s, 1, (size_t)n, stdout) != (size_t)n) {
        ps_raise_cstr(PS_RUNTIME, "write failed");
    }
    return ps_cp_count(n, s);
}

int64_t ps_stderr_write(int64_t n, const char *s) {
    if (n < 0) {
        ps_raise_code(PS_VALUE);
    }
    if (n > 0 && fwrite(s, 1, (size_t)n, stderr) != (size_t)n) {
        ps_raise_cstr(PS_RUNTIME, "write failed");
    }
    return ps_cp_count(n, s);
}

/* `sys.stdin.readline()` reads one line, like CPython. On POSIX CPython
opens `sys.stdin` with newline="\n", so a line ends at a "\n" and the
"\n" stays in the returned string; a "\r" is an ordinary character. The
line that ends at EOF without a newline comes back whole, and a read at
EOF returns the empty string. */
void ps_stdin_readline(PsBuf *out) {
    PsGrow grow;
    ps_grow_init(&grow);
    int ch = fgetc(stdin);
    while (ch != EOF) {
        ps_grow_byte(&grow, (unsigned char)ch);
        if (ch == '\n') {
            break;
        }
        ch = fgetc(stdin);
    }
    ps_grow_finish(&grow, out);
    ps_utf8_require(out->len, out->data, NULL);
}

/* `sys.stdout.flush()` and `sys.stderr.flush()` push the buffered bytes out
to the peer. The writes go through stdio, so a flush is what makes a
line-oriented peer see the response. */
void ps_stdout_flush(void) {
    fflush(stdout);
}

void ps_stderr_flush(void) {
    fflush(stderr);
}

/* `input()` reads one stdin line and drops the trailing newline. A first EOF
is RuntimeError standing in for EOFError, which the exception table lacks. */
void ps_input_line(PsBuf *out) {
    PsGrow grow;
    ps_grow_init(&grow);
    int ch = fgetc(stdin);
    int got = 0;
    while (ch != EOF && ch != '\n') {
        ps_grow_byte(&grow, (unsigned char)ch);
        got = 1;
        ch = fgetc(stdin);
    }
    if (ch == EOF && !got) {
        ps_raise_cstr(PS_RUNTIME, "EOF when reading a line");
    }
    ps_grow_finish(&grow, out);
    ps_utf8_require(out->len, out->data, NULL);
}
