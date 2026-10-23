/* Load libmix.so by its C ABI. No Python headers: this file is linked into
   the small runtime, and only a call to ps_mix_* opens the library.

   PYSCRIPTC_MIX_SO is the absolute path of that .so. A PyInit_* extension
   cannot be opened this way; see ps_island_mix_crc32. */

#include "ps_rt.h"

#include <dlfcn.h>
#include <pthread.h>
#include <stdlib.h>

typedef int64_t (*ps_mix_crc_fn)(const char *, int64_t);
typedef int64_t (*ps_mix_crc_n_fn)(const char *, int64_t, int64_t);

static void *ps_mix_lib;
static ps_mix_crc_fn ps_mix_crc;
static ps_mix_crc_n_fn ps_mix_crc_n;
/* A second thread can reach the mix seam, so the dlopen runs once. */
static pthread_once_t ps_mix_once = PTHREAD_ONCE_INIT;

static void ps_mix_load(void) {
    const char *path;
    path = getenv("PYSCRIPTC_MIX_SO");
    if (path == NULL || path[0] == '\0') {
        ps_raise_cstr(PS_RUNTIME, "PYSCRIPTC_MIX_SO is unset");
    }
    ps_mix_lib = dlopen(path, RTLD_NOW);
    if (ps_mix_lib == NULL) {
        ps_raise_cstr(PS_RUNTIME, dlerror());
    }
    ps_mix_crc = (ps_mix_crc_fn)dlsym(ps_mix_lib, "mix_crc32");
    ps_mix_crc_n = (ps_mix_crc_n_fn)dlsym(ps_mix_lib, "mix_crc32_n");
    if (ps_mix_crc == NULL || ps_mix_crc_n == NULL) {
        ps_raise_cstr(PS_RUNTIME, "libmix.so is missing mix_crc32");
    }
}

int64_t ps_mix_crc32(int64_t n, const char *data) {
    pthread_once(&ps_mix_once, ps_mix_load);
    return ps_mix_crc(data, n);
}

int64_t ps_mix_crc32_n(int64_t n, const char *data, int64_t times) {
    pthread_once(&ps_mix_once, ps_mix_load);
    return ps_mix_crc_n(data, n, times);
}
