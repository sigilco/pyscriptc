#pragma once

#include <stdint.h>

/* C ABI of the sample library. A Rust cdylib with the same two symbols
   is the same kind of file. A PyPI extension module is not: its entry
   point is PyInit_*, and it speaks PyObject. */

int64_t mix_crc32(const char *data, int64_t n);
int64_t mix_crc32_n(const char *data, int64_t n, int64_t times);
