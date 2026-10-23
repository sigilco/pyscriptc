/* The F1b handle fixture: a tiny resource with an open/use/close lifecycle.
 *
 * The adapter thunk owns the C struct and the free, exactly like the sqlite
 * pilot's ps_ffi_sqlite.c would. The compiler only knows the manifest
 * signatures: a handle crosses as an opaque pointer, and the consuming call
 * is the one that releases it.
 *
 * The record is heap allocated, so a leak shows up under
 * --sanitize=address and a double free shows up under the same build. The
 * counters let the fixture script assert the lifecycle from Python.
 */

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

/* The runtime ABI this fixture needs, spelled out so the file compiles
   without the runtime's include path. PsBuf is { i64 len, char *data } and
   ps_buf_copy is the runtime's copy helper; the fixture links against the
   runtime, so the symbol resolves there. */
typedef struct PsBuf {
  int64_t len;
  char *data;
} PsBuf;

void ps_buf_copy(PsBuf *out, const char *p, int64_t n);

/* The opaque handle. The Python side never sees this layout. */
typedef struct FfxResource {
  int64_t magic;
  int64_t uses;
  int64_t closed;
} FfxResource;

#define FFX_MAGIC 0x46465800LL

static int64_t ffx_open_count = 0;
static int64_t ffx_close_count = 0;
static int64_t ffx_use_count = 0;

int64_t ps_ffi_ffx_open_count(void) { return ffx_open_count; }
int64_t ps_ffi_ffx_close_count(void) { return ffx_close_count; }
int64_t ps_ffi_ffx_use_count(void) { return ffx_use_count; }

/* open(name: str) -> Resource */
void *ps_ffi_ffx_open(int64_t n, const char *p) {
  FfxResource *res = (FfxResource *)calloc(1, sizeof(FfxResource));
  if (res == NULL) {
    return NULL;
  }
  res->magic = FFX_MAGIC;
  ffx_open_count += 1;
  (void)n;
  (void)p;
  return res;
}

/* use(res: Resource, n: int) -> int: counts a use, refuses a dead handle. */
int64_t ps_ffi_ffx_use(void *handle, int64_t n) {
  FfxResource *res = (FfxResource *)handle;
  if (res == NULL || res->magic != FFX_MAGIC) {
    return -1;
  }
  res->uses += 1;
  ffx_use_count += 1;
  return res->uses * n;
}

/* close(res: Resource) -> None: the consuming call, the one that frees. */
void ps_ffi_ffx_close(void *handle) {
  FfxResource *res = (FfxResource *)handle;
  if (res == NULL) {
    return;
  }
  if (res->magic != FFX_MAGIC) {
    /* A double close would be a use after free. */
    return;
  }
  res->magic = 0;
  res->closed = 1;
  ffx_close_count += 1;
  free(res);
}

/* echo(s: str) -> str: the str round trip through the (len, ptr) ABI. */
void ps_ffi_ffx_echo(int64_t n, const char *p, PsBuf *out) { ps_buf_copy(out, p, n); }

/* bounce(b: bytes) -> bytes: the bytes round trip, NUL bytes included. */
void ps_ffi_ffx_bounce(int64_t n, const char *p, PsBuf *out) { ps_buf_copy(out, p, n); }

/* take(res: Resource) -> Resource: returns the handle, transferring
   ownership to the caller. The compiler must not free it twice. */
void *ps_ffi_ffx_take(void *handle) { return handle; }
