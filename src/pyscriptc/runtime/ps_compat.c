/* The Windows implementations of the ps_compat.h shims.

   Compiled only for _WIN32; on POSIX every shim is a macro onto the native
   call and this file is empty, so the runtime keeps exactly one object per
   platform. */

#ifdef _WIN32

#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef _NO_W32_PSEUDO_MODIFIERS
#define _NO_W32_PSEUDO_MODIFIERS
#endif
#include <windows.h>

#include <errno.h>
#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "ps_compat.h"

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

#else

/* ISO C requires a translation unit to hold at least one declaration. */
typedef int ps_compat_unused_t;

#endif
