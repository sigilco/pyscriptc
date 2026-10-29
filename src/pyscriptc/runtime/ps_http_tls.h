/* The optional TLS transport seam between ps_http.c and the pyscriptc-http
   FFI adapter (issue #235).

   ps_http.c speaks plaintext HTTP/1.1 over blocking BSD sockets and links
   into every binary on every host target. The adapter ships mbedTLS
   statically and binds this seam through its FFI manifest: when the
   manifest is on the compile, the adapter's strong definitions of these
   symbols win over the runtime's weak ones and https:// works; when it is
   not, the weak definitions stay and urlopen fails closed with an
   actionable message.

   The seam is a transport, not a request: the HTTP parsing (status line,
   headers, content-length, chunked decoding, redirects) stays in ps_http.c,
   where it already is, and the adapter only moves bytes over a verified TLS
   session. So the seam is four calls plus the error text:

     ps_http_tls_open   connect, handshake with verification REQUIRED
                        (hostname through sni, roots from ca_paths), and
                        hand back a session handle
     ps_http_tls_write  write the request bytes
     ps_http_tls_read   read response bytes (0 at end of stream)
     ps_http_tls_close  close the session and the socket
     ps_http_tls_last_error  the reason of the last failure

   A read or a write that fails returns -1 and the reason comes from
   ps_http_tls_last_error. There is no insecure mode: verification is
   required, the hostname is checked against the certificate, and a
   handshake that does not verify fails the call.

   Why weak definitions and not weak references: the macOS linker refuses
   an undefined weak symbol, so a weak *reference* to a symbol the adapter
   does not provide fails the link on Darwin. A weak *definition* links
   everywhere, and the adapter's strong definition overrides it (the
   standard ELF and Mach-O rule).
*/

#ifndef PS_HTTP_TLS_H
#define PS_HTTP_TLS_H

#include "ps_rt.h"

#if !defined(__wasi__) && !defined(_WIN32)

#include <stdint.h>

/* The provider marker: the adapter defines this as a strong int, so a
   binary that carries the adapter reports 1 and a binary without it
   reports the runtime's weak 0.

   The weak attribute lives on the declarations, and a definition inherits
   the attribute of a prior declaration. So the adapter's translation unit
   defines PS_HTTP_TLS_PROVIDER before including this header and gets
   strong declarations, while the runtime's ps_http.c (which includes it
   without the macro) gets the weak ones. */
#ifdef PS_HTTP_TLS_PROVIDER
#define PS_HTTP_TLS_WEAK
#else
#define PS_HTTP_TLS_WEAK __attribute__((weak))
#endif

PS_HTTP_TLS_WEAK int ps_http_tls_provider(void);
PS_HTTP_TLS_WEAK int ps_http_tls_open(
    const char *host,
    int port,
    const char *sni,
    const char *const *ca_paths,
    int ca_count,
    double timeout,
    void **out_session
);
PS_HTTP_TLS_WEAK int64_t ps_http_tls_write(void *session, const char *buf, int64_t len);
PS_HTTP_TLS_WEAK int64_t ps_http_tls_read(void *session, char *buf, int64_t len);
PS_HTTP_TLS_WEAK void ps_http_tls_close(void *session);
PS_HTTP_TLS_WEAK const char *ps_http_tls_last_error(void);

#endif /* !__wasi__ && !_WIN32 */

#endif /* PS_HTTP_TLS_H */
