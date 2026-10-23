#pragma once

#include <stdint.h>

/* C ABI of the manifest-built smoke unit. The manifest compiler is the
   static-link path (F2), so the symbols resolve at link time, not through
   dlopen like the mix.c pair. */

int64_t ffi_add3(int64_t a, int64_t b, int64_t c);
