#ifndef PYSCRIPTC_PS_TLS_H
#define PYSCRIPTC_PS_TLS_H

/* The transport contract every byte of the native HTTP client travels on.

   `ps_http.c` owns the HTTP layer: it writes the request, parses the status
   line, the headers and the body. Everything under it is a transport: four
   calls (connect, read, write, close) plus the verify-hostname flag. The
   plaintext TCP backend in `ps_http.c` is the only backend today; a TLS
   backend (#235) implements the same four calls, so the HTTP layer never
   learns whether the bytes travel in the clear or under TLS.

   The read and write calls take the time left on the request deadline in
   seconds, not an absolute deadline: a backend that blocks must unblock
   within that time and report the timeout through the return code.
*/

#include <stdint.h>

/* The transport: connect to host:port, read and write bytes on the stream,
   then close it. `connect` returns a handle (>= 0) or a negative code;
   `read` returns the byte count, 0 at end of stream, or a negative code;
   `write` returns the byte count written or a negative code; `close` drops
   the handle and whatever the backend holds for it. */
typedef struct PsHttpTransport {
    int64_t (*connect)(const char *host, int port, double timeout);
    int64_t (*read)(int64_t handle, char *buf, int64_t len, double timeout);
    int64_t (*write)(int64_t handle, const char *buf, int64_t len, double timeout);
    void (*close)(int64_t handle);
    /* Whether the backend verifies the peer certificate against the host
       name. On by default: a backend that cannot verify says so here. */
    int verify_hostname;
} PsHttpTransport;

/* The negative codes a transport read or write returns. */
#define PS_HTTP_IO_ERROR (-1)
#define PS_HTTP_IO_TIMEOUT (-2)

#endif /* PYSCRIPTC_PS_TLS_H */
