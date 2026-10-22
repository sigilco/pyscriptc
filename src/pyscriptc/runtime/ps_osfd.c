/* os file descriptors: open, close, read, write, fstat, lstat, utime.

The flag argument uses the CPython `os.O_*` numbers, so `os.O_RDONLY` in
Python and the C `O_RDONLY` agree on the values the compiler passes. The
subset keeps the flags that matter to scripts: read, write, append, create
and truncate.
*/

#include "ps_rt.h"

#include <errno.h>
#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <utime.h>
#include <unistd.h>

typedef struct {
    int64_t mode;
    int64_t size;
    double mtime;
} PsFdStat;

static void ps_fd_fail(const char *what) { ps_raise_cstr(PS_RUNTIME, what); }

int64_t ps_os_open(int64_t n, const char *s, int64_t flags, int64_t mode) {
    char *text = malloc((size_t)n + 1);
    if (text == NULL) {
        abort();
    }
    memcpy(text, s, (size_t)n);
    text[n] = '\0';
    int fd = open(text, (int)flags, (mode_t)mode);
    free(text);
    if (fd < 0) {
        ps_fd_fail("open failed");
    }
    return (int64_t)fd;
}

void ps_os_close(int64_t fd) {
    if (close((int)fd) != 0) {
        ps_fd_fail("close failed");
    }
}

void ps_os_read(int64_t fd, int64_t size, PsBuf *out) {
    if (size < 0) {
        ps_raise_code(PS_VALUE);
    }
    char *buf = malloc((size_t)size + 1);
    if (buf == NULL) {
        abort();
    }
    ssize_t got = read((int)fd, buf, (size_t)size);
    if (got < 0) {
        free(buf);
        ps_fd_fail("read failed");
    }
    ps_buf_copy(out, buf, (int64_t)got);
    free(buf);
}

int64_t ps_os_write(int64_t fd, int64_t n, const char *s) {
    ssize_t put = write((int)fd, s, (size_t)n);
    if (put < 0) {
        ps_fd_fail("write failed");
    }
    return (int64_t)put;
}

void ps_os_fstat(int64_t fd, PsFdStat *out) {
    struct stat st;
    if (fstat((int)fd, &st) != 0) {
        ps_fd_fail("fstat failed");
    }
    out->mode = (int64_t)st.st_mode;
    out->size = (int64_t)st.st_size;
    out->mtime = (double)st.st_mtime;
}

void ps_os_lstat(int64_t n, const char *s, PsFdStat *out) {
    char *text = malloc((size_t)n + 1);
    if (text == NULL) {
        abort();
    }
    memcpy(text, s, (size_t)n);
    text[n] = '\0';
    struct stat st;
    int rc = lstat(text, &st);
    free(text);
    if (rc != 0) {
        ps_fd_fail("lstat failed");
    }
    out->mode = (int64_t)st.st_mode;
    out->size = (int64_t)st.st_size;
    out->mtime = (double)st.st_mtime;
}

/* utime: the `now` form touches both timestamps with the current clock. The
   explicit times form is not in the subset; the compiler fences it. */
void ps_os_utime(int64_t n, const char *s) {
    char *text = malloc((size_t)n + 1);
    if (text == NULL) {
        abort();
    }
    memcpy(text, s, (size_t)n);
    text[n] = '\0';
    int rc = utime(text, NULL);
    free(text);
    if (rc != 0) {
        ps_fd_fail("utime failed");
    }
}
