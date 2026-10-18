/* io.StringIO, io.BytesIO, and builtin open.

Memory buffers start at position 0, so read() returns the initial value.
File writes are flushed so a later open sees them. OS errors are RuntimeError.
*/

#include "ps_rt.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct PsIO {
    int32_t kind;
    int32_t closed;
    PsGrow mem;
    int64_t pos;
    FILE *fp;
} PsIO;

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

PsIO *ps_io_open(int64_t n, const char *path, int64_t mode) {
    const char *how = NULL;
    int kind = 3;
    char *text;
    FILE *fp;
    PsIO *io;
    if (mode == 1) {
        how = "r";
    } else if (mode == 2) {
        how = "w";
    } else if (mode == 3) {
        how = "rb";
        kind = 4;
    } else if (mode == 4) {
        how = "wb";
        kind = 4;
    } else if (mode == 5) {
        how = "a";
    } else if (mode == 6) {
        how = "ab";
        kind = 4;
    } else {
        ps_raise_cstr(PS_VALUE, "open mode is not lowered");
    }
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
    return io;
}

static int ps_can_read(PsIO *io) {
    if (io->kind == 1 || io->kind == 2) {
        return 1;
    }
    return io->fp != NULL;
}

void ps_io_read(PsIO *io, PsBuf *out) {
    ps_open_check(io);
    if (!ps_can_read(io)) {
        ps_raise_cstr(PS_VALUE, "read failed");
    }
    if (io->fp != NULL) {
        long size;
        char *buf;
        if (fseek(io->fp, 0, SEEK_END) != 0) {
            ps_raise_cstr(PS_RUNTIME, "read failed");
        }
        size = ftell(io->fp);
        if (size < 0 || fseek(io->fp, 0, SEEK_SET) != 0) {
            ps_raise_cstr(PS_RUNTIME, "read failed");
        }
        buf = malloc((size_t)size + 1);
        if (buf == NULL) {
            abort();
        }
        if (size > 0 && fread(buf, 1, (size_t)size, io->fp) != (size_t)size) {
            free(buf);
            ps_raise_cstr(PS_RUNTIME, "read failed");
        }
        buf[size] = '\0';
        out->len = size;
        out->data = buf;
        return;
    }
    if (io->pos > io->mem.len) {
        io->pos = io->mem.len;
    }
    ps_buf_copy(out, io->mem.data == NULL ? "" : io->mem.data + io->pos, io->mem.len - io->pos);
    io->pos = io->mem.len;
}

int64_t ps_io_write(PsIO *io, int64_t n, const char *s) {
    ps_open_check(io);
    if (n < 0) {
        ps_raise_code(PS_VALUE);
    }
    if (io->fp != NULL) {
        if (n > 0 && fwrite(s, 1, (size_t)n, io->fp) != (size_t)n) {
            ps_raise_cstr(PS_RUNTIME, "write failed");
        }
        if (fflush(io->fp) != 0) {
            ps_raise_cstr(PS_RUNTIME, "write failed");
        }
        return n;
    }
    if (io->pos > io->mem.len) {
        while (io->mem.len < io->pos) {
            ps_grow_byte(&io->mem, '\0');
        }
    }
    if (io->pos == io->mem.len) {
        ps_grow_add(&io->mem, s, n);
        io->pos = io->mem.len;
        return n;
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
    return n;
}

void ps_io_readline(PsIO *io, PsBuf *out) {
    /* `for line in f` / f.readline(): bytes through the next '\n' (kept),
       an empty buffer at EOF. */
    PsGrow line;
    ps_open_check(io);
    if (!ps_can_read(io)) {
        ps_raise_cstr(PS_VALUE, "readline is lowered for readable streams");
    }
    ps_grow_init(&line);
    if (io->fp != NULL) {
        int ch;
        for (;;) {
            ch = fgetc(io->fp);
            if (ch == EOF) {
                break;
            }
            ps_grow_byte(&line, (unsigned char)ch);
            if (ch == '\n') {
                break;
            }
        }
        if (ferror(io->fp)) {
            free(line.data);
            ps_raise_cstr(PS_RUNTIME, "readline failed");
        }
        ps_grow_finish(&line, out);
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

int64_t ps_stdout_write(int64_t n, const char *s) {
    if (n < 0) {
        ps_raise_code(PS_VALUE);
    }
    if (n > 0 && fwrite(s, 1, (size_t)n, stdout) != (size_t)n) {
        ps_raise_cstr(PS_RUNTIME, "write failed");
    }
    return n;
}

int64_t ps_stderr_write(int64_t n, const char *s) {
    if (n < 0) {
        ps_raise_code(PS_VALUE);
    }
    if (n > 0 && fwrite(s, 1, (size_t)n, stderr) != (size_t)n) {
        ps_raise_cstr(PS_RUNTIME, "write failed");
    }
    return n;
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
}
