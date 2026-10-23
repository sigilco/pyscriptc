#include "ffi.h"

/* The manifest-built unit of the zig cross matrix (issue #220). One small
   self-contained C file: `zig cc -c` cross-compiles it for every tier-A
   triple, and the manifest declares it as a `[[source]]` so the linker
   compiles it with the target cc and links it statically. */

int64_t ffi_add3(int64_t a, int64_t b, int64_t c) {
    return a + b + c;
}
