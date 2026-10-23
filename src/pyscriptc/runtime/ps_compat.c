/* The implementations of the ps_compat.h shims for the targets that lack the
   native call: Windows (no setenv, no mkstemp, no sigsetjmp) and WASI (no
   mkstemp). On every other target each shim is a macro onto the native call
   and this file is empty, so the runtime keeps one object per platform. */

#if defined(_WIN32) || defined(__wasi__)

#include <errno.h>
#include <stdlib.h>
#include <string.h>

#if defined(_WIN32)

#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef _NO_W32_PSEUDO_MODIFIERS
#define _NO_W32_PSEUDO_MODIFIERS
#endif
#include <windows.h>

#include <fcntl.h>
#include <sys/stat.h>

int ps_win_setenv(const char *name, const char *value, int overwrite) {
    if (!overwrite && getenv(name) != NULL) {
        return 0;
    }
    if (SetEnvironmentVariableA(name, value) == 0) {
        return -1;
    }
    return 0;
}

int ps_mkstemp(char *template_path) {
    /* mkstemp with the Xs replaced in place, same contract as POSIX. The
       alphabet and the retry loop match the POSIX implementations: 62
       characters, 100 tries, mode 0600. _open with _O_EXCL gives the
       unique-file guarantee; _O_TEMPORARY is not used because the caller
       decides when the file goes. */
    static const char alphabet[] =
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
    char *xs;
    int attempt;
    int fd;
    if (strlen(template_path) < 6) {
        return -1;
    }
    xs = template_path + strlen(template_path) - 6;
    for (attempt = 0; attempt < 100; attempt++) {
        int i;
        for (i = 0; i < 6; i++) {
            xs[i] = alphabet[rand() % 62];
        }
        fd = _open(template_path, _O_CREAT | _O_EXCL | _O_RDWR | _O_BINARY, _S_IREAD | _S_IWRITE);
        if (fd >= 0) {
            return fd;
        }
        if (errno != EEXIST) {
            return -1;
        }
    }
    return -1;
}

#else /* __wasi__ */

#include <fcntl.h>
#include <unistd.h>

int ps_mkstemp(char *template_path) {
    /* wasi-libc declares no mkstemp (TODO(zig-0.18): still true upstream).
       open with O_CREAT|O_EXCL on a randomized suffix gives the same
       unique-file guarantee; mode 0600 matches musl's mkstemp. */
    static const char alphabet[] =
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
    char *xs;
    int attempt;
    int fd;
    if (strlen(template_path) < 6) {
        return -1;
    }
    xs = template_path + strlen(template_path) - 6;
    for (attempt = 0; attempt < 100; attempt++) {
        int i;
        for (i = 0; i < 6; i++) {
            xs[i] = alphabet[rand() % 62];
        }
        fd = open(template_path, O_CREAT | O_EXCL | O_RDWR, 0600);
        if (fd >= 0) {
            return fd;
        }
        if (errno != EEXIST) {
            return -1;
        }
    }
    return -1;
}

#endif

#else

/* ISO C requires a translation unit to hold at least one declaration. */
typedef int ps_compat_unused_t;

#endif
