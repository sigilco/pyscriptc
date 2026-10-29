/* pyscriptc-http: the TLS transport for the native HTTP client (issue #235).

   `ps_http.c` speaks plaintext HTTP/1.1 over blocking BSD sockets and links
   into every binary on every host target. This adapter adds the https half:
   the vendored mbedTLS is linked statically through the FFI manifest and the
   transport seam in `runtime/ps_http_tls.h` is implemented here.

   The seam is a transport, not a request: the HTTP parsing (status line,
   headers, content-length, chunked decoding, redirects) stays in ps_http.c,
   so the plaintext and the https paths share it. Four calls move bytes:

     ps_http_tls_open   connect, handshake with verification REQUIRED, hand
                        back the session pointer
     ps_http_tls_write  write request bytes
     ps_http_tls_read   read response bytes (0 at end of stream)
     ps_http_tls_close  close the session and the socket

   Verification is on and there is no insecure fallback: a handshake that
   fails to verify fails the open, and the runtime raises URLError (OSError)
   with the reason. There is no "trust anything" mode and no way to skip the
   hostname check: the SNI server name is also the name the certificate is
   verified against.

   The CA bundle is chosen by the runtime, not by this file: the runtime
   looks up the platform trust store (the macOS `/etc/ssl/cert.pem` bundle,
   the Linux `/etc/ssl/certs` directory and its known bundle files) and
   passes the paths here. When no root can be found the open fails; it
   never falls back to an empty trust store.

   The parsed CA chain is cached across calls keyed on the bundle path, so
   a program that makes several https requests parses the bundle once.
   The cache is not thread-safe; the seam is called from one thread at a
   time, like every other urlopen call (the runtime's urlopen is not
   reentrant across threads yet).
*/

/* The provider of the seam: the definitions below must not be weak. */
/* The provider of the seam: the definitions below are strong, so they
   override the runtime's weak ones when the adapter is linked. */
#define PS_HTTP_TLS_PROVIDER 1

#include "ps_rt.h"
#include "ps_http_tls.h"

#if !defined(__wasi__) && !defined(_WIN32)

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <netdb.h>
#include <unistd.h>

#include "mbedtls/ctr_drbg.h"
#include "mbedtls/entropy.h"
#include "mbedtls/error.h"
#include "mbedtls/net_sockets.h"
#include "mbedtls/ssl.h"
#include "mbedtls/x509_crt.h"

/* The provider marker the runtime reads (see ps_http_tls.h). */
int ps_http_tls_provider(void) { return 1; }

/* The failure codes the seam returns; the runtime maps them onto raises
   whose message carries the text from ps_http_tls_last_error. */
#define PS_TLS_ERR_CONNECT 1
#define PS_TLS_ERR_CA 2
#define PS_TLS_ERR_HANDSHAKE 3
#define PS_TLS_ERR_IO 4
#define PS_TLS_ERR_INTERNAL 5

static char ps_tls_error[256];

static void ps_tls_set_error(const char *detail) {
    snprintf(ps_tls_error, sizeof ps_tls_error, "%s", detail != NULL ? detail : "TLS failure");
}

static void ps_tls_set_mbedtls_error(int code, const char *what) {
    char text[160];
    mbedtls_strerror(code, text, sizeof text);
    char message[256];
    snprintf(message, sizeof message, "%s (%s)", what, text);
    ps_tls_set_error(message);
}

/* One open TLS session: the mbedTLS contexts plus the socket. The caller
   sees an opaque pointer; the fields never cross the boundary. */
typedef struct {
    int fd;
    mbedtls_ssl_context ssl;
    mbedtls_ssl_config conf;
    mbedtls_ctr_drbg_context drbg;
    mbedtls_entropy_context entropy;
} PsTlsSession;

/* The CA roots, parsed once and reused. Keyed on the bundle path: a new
   path re-parses. Not freed at exit (the process exit reclaims it), so a
   program that closes its last https session still holds the chain; that
   is the same lifetime the runtime's own caches use. */
static mbedtls_x509_crt ps_tls_chain;
static int ps_tls_chain_loaded;
static char ps_tls_chain_path[1024];

static int ps_tls_load_roots(const char *const *paths, int count) {
    if (count <= 0 || paths == NULL) {
        ps_tls_set_error("no CA bundle was found on this system for certificate verification");
        return PS_TLS_ERR_CA;
    }
    if (ps_tls_chain_loaded) {
        for (int i = 0; i < count; i++) {
            if (paths[i] != NULL && strcmp(ps_tls_chain_path, paths[i]) == 0) {
                return 0;
            }
        }
    }
    mbedtls_x509_crt_free(&ps_tls_chain);
    mbedtls_x509_crt_init(&ps_tls_chain);
    for (int i = 0; i < count; i++) {
        if (paths[i] == NULL) {
            continue;
        }
        int rc = mbedtls_x509_crt_parse_file(&ps_tls_chain, paths[i]);
        if (rc == 0) {
            ps_tls_chain_loaded = 1;
            snprintf(ps_tls_chain_path, sizeof ps_tls_chain_path, "%s", paths[i]);
            return 0;
        }
        ps_tls_set_mbedtls_error(rc, "the CA bundle could not be read");
    }
    return PS_TLS_ERR_CA;
}

/* The BIO: the socket is blocking with the deadline carried by the socket
   timeouts, so the callbacks are plain read/write with the mbedTLS retry
   codes for EINTR. */
static int ps_tls_send(void *ctx, const unsigned char *buf, size_t len) {
    int fd = *(int *)ctx;
    ssize_t wrote;
    do {
        wrote = write(fd, buf, len);
    } while (wrote < 0 && errno == EINTR);
    if (wrote < 0) {
        return MBEDTLS_ERR_NET_SEND_FAILED;
    }
    return (int)wrote;
}

static int ps_tls_recv(void *ctx, unsigned char *buf, size_t len) {
    int fd = *(int *)ctx;
    ssize_t got;
    do {
        got = read(fd, buf, len);
    } while (got < 0 && errno == EINTR);
    if (got < 0) {
        return MBEDTLS_ERR_NET_RECV_FAILED;
    }
    if (got == 0) {
        return MBEDTLS_ERR_SSL_PEER_CLOSE_NOTIFY;
    }
    return (int)got;
}

/* Connect to host:port, the same rules as the plaintext client: the first
   address that accepts wins, the timeout lands in the socket's own
   send/receive timeouts. */
static int ps_tls_connect(const char *host, int port, double timeout) {
    char port_text[8];
    struct addrinfo hints;
    struct addrinfo *res = NULL;
    snprintf(port_text, sizeof port_text, "%d", port);
    memset(&hints, 0, sizeof hints);
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    int gai = getaddrinfo(host, port_text, &hints, &res);
    if (gai != 0 || res == NULL) {
        ps_tls_set_error(gai == 0 ? "no address for host" : gai_strerror(gai));
        return -1;
    }
    int fd = -1;
    for (struct addrinfo *ai = res; ai != NULL; ai = ai->ai_next) {
        fd = socket(ai->ai_family, ai->ai_socktype, ai->ai_protocol);
        if (fd < 0) {
            continue;
        }
        if (connect(fd, ai->ai_addr, ai->ai_addrlen) == 0) {
            break;
        }
        close(fd);
        fd = -1;
    }
    freeaddrinfo(res);
    if (fd < 0) {
        ps_tls_set_error("connection refused");
        return -1;
    }
    if (timeout > 0) {
        struct timeval tv;
        tv.tv_sec = (time_t)timeout;
        tv.tv_usec = (suseconds_t)((timeout - (double)tv.tv_sec) * 1e6);
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
        setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof tv);
    }
    return fd;
}

int ps_http_tls_open(
    const char *host,
    int port,
    const char *sni,
    const char *const *ca_paths,
    int ca_count,
    double timeout,
    void **out_session
) {
    *out_session = NULL;
    int rc = ps_tls_load_roots(ca_paths, ca_count);
    if (rc != 0) {
        return rc;
    }
    int fd = ps_tls_connect(host, port, timeout);
    if (fd < 0) {
        return PS_TLS_ERR_CONNECT;
    }
    PsTlsSession *session = ps_alloc((int64_t)sizeof *session);
    session->fd = fd;
    mbedtls_ssl_init(&session->ssl);
    mbedtls_ssl_config_init(&session->conf);
    mbedtls_ctr_drbg_init(&session->drbg);
    mbedtls_entropy_init(&session->entropy);

    const char *personalization = "pyscriptc-http";
    rc = mbedtls_ctr_drbg_seed(
        &session->drbg,
        mbedtls_entropy_func,
        &session->entropy,
        (const unsigned char *)personalization,
        strlen(personalization)
    );
    if (rc != 0) {
        ps_tls_set_mbedtls_error(rc, "the TLS random generator failed");
        goto fail;
    }
    rc = mbedtls_ssl_config_defaults(
        &session->conf,
        MBEDTLS_SSL_TRANSPORT_STREAM,
        MBEDTLS_SSL_IS_CLIENT,
        MBEDTLS_SSL_PRESET_DEFAULT
    );
    if (rc != 0) {
        ps_tls_set_mbedtls_error(rc, "the TLS configuration failed");
        goto fail;
    }
    mbedtls_ssl_conf_rng(&session->conf, mbedtls_ctr_drbg_random, &session->drbg);
    /* Verification is required: a certificate that does not chain to a
       root in the bundle, or a hostname that does not match, fails the
       handshake. */
    mbedtls_ssl_conf_ca_chain(&session->conf, &ps_tls_chain, NULL);
    mbedtls_ssl_conf_authmode(&session->conf, MBEDTLS_SSL_VERIFY_REQUIRED);
    rc = mbedtls_ssl_setup(&session->ssl, &session->conf);
    if (rc != 0) {
        ps_tls_set_mbedtls_error(rc, "the TLS session could not be set up");
        goto fail;
    }
    if (sni != NULL && sni[0] != '\0') {
        /* The SNI server name and the hostname check are the same string:
           the certificate is verified against it, so a certificate for
           another host fails the handshake. */
        rc = mbedtls_ssl_set_hostname(&session->ssl, sni);
        if (rc != 0) {
            ps_tls_set_mbedtls_error(rc, "the server name could not be set");
            goto fail;
        }
    }
    mbedtls_ssl_set_bio(&session->ssl, &session->fd, ps_tls_send, ps_tls_recv, NULL);
    rc = mbedtls_ssl_handshake(&session->ssl);
    if (rc != 0) {
        if (rc == MBEDTLS_ERR_X509_CERT_VERIFY_FAILED) {
            uint32_t flags = mbedtls_ssl_get_verify_result(&session->ssl);
            char detail[256];
            char flags_text[128];
            /* The verify flags are mbedTLS's bitmask; the common ones get
               names, the rest fall back to the number. */
            if (flags & MBEDTLS_X509_BADCERT_NOT_TRUSTED) {
                snprintf(flags_text, sizeof flags_text, "the certificate is not trusted");
            } else if (flags & MBEDTLS_X509_BADCERT_CN_MISMATCH) {
                snprintf(flags_text, sizeof flags_text, "the hostname does not match the certificate");
            } else if (flags & MBEDTLS_X509_BADCERT_EXPIRED) {
                snprintf(flags_text, sizeof flags_text, "the certificate has expired");
            } else {
                snprintf(flags_text, sizeof flags_text, "verification flags 0x%lx", (unsigned long)flags);
            }
            snprintf(
                detail,
                sizeof detail,
                "the certificate of %s could not be verified: %s",
                host,
                flags_text
            );
            ps_tls_set_error(detail);
        } else {
            ps_tls_set_mbedtls_error(rc, "the TLS handshake failed");
        }
        goto fail;
    }
    *out_session = session;
    return 0;

fail:
    mbedtls_ssl_free(&session->ssl);
    mbedtls_ssl_config_free(&session->conf);
    mbedtls_ctr_drbg_free(&session->drbg);
    mbedtls_entropy_free(&session->entropy);
    close(fd);
    return PS_TLS_ERR_HANDSHAKE;
}

int64_t ps_http_tls_write(void *handle, const char *buf, int64_t len) {
    PsTlsSession *session = (PsTlsSession *)handle;
    if (session == NULL || len < 0) {
        return -1;
    }
    int wrote = mbedtls_ssl_write(&session->ssl, (const unsigned char *)buf, (size_t)len);
    if (wrote < 0) {
        ps_tls_set_mbedtls_error(wrote, "the TLS write failed");
        return -1;
    }
    return (int64_t)wrote;
}

int64_t ps_http_tls_read(void *handle, char *buf, int64_t len) {
    PsTlsSession *session = (PsTlsSession *)handle;
    if (session == NULL || len < 0) {
        return -1;
    }
    int got = mbedtls_ssl_read(&session->ssl, (unsigned char *)buf, (size_t)len);
    if (got == MBEDTLS_ERR_SSL_PEER_CLOSE_NOTIFY || got == 0) {
        /* The peer closed the session cleanly: the stream is at its end.
           The close_notify that follows is not sent back, so the next
           read also reports 0. */
        return 0;
    }
    if (got < 0) {
        ps_tls_set_mbedtls_error(got, "the TLS read failed");
        return -1;
    }
    return (int64_t)got;
}

void ps_http_tls_close(void *handle) {
    PsTlsSession *session = (PsTlsSession *)handle;
    if (session == NULL) {
        return;
    }
    /* The close_notify is best effort: a peer that went away mid-response
       still gets its socket closed. */
    (void)mbedtls_ssl_close_notify(&session->ssl);
    mbedtls_ssl_free(&session->ssl);
    mbedtls_ssl_config_free(&session->conf);
    mbedtls_ctr_drbg_free(&session->drbg);
    mbedtls_entropy_free(&session->entropy);
    close(session->fd);
    /* The session memory is owned by the runtime's allocator; nothing to
       free here. */
}

const char *ps_http_tls_last_error(void) { return ps_tls_error; }

#else /* __wasi__ || _WIN32 */

/* The unsupported-target stubs: the adapter is not built for a target
   without BSD sockets (the same rule ps_http.c follows; the manifest's
   units would fail to compile there, so the manifest is host-only until
   winsock lands). */

int ps_http_tls_open(
    const char *host,
    int port,
    const char *sni,
    const char *const *ca_paths,
    int ca_count,
    double timeout,
    void **out_session
) {
    (void)host;
    (void)port;
    (void)sni;
    (void)ca_paths;
    (void)ca_count;
    (void)timeout;
    (void)out_session;
    return PS_TLS_ERR_HANDSHAKE;
}

int64_t ps_http_tls_write(void *session, const char *buf, int64_t len) {
    (void)session;
    (void)buf;
    (void)len;
    return -1;
}

int64_t ps_http_tls_read(void *session, char *buf, int64_t len) {
    (void)session;
    (void)buf;
    (void)len;
    return -1;
}

void ps_http_tls_close(void *session) { (void)session; }

const char *ps_http_tls_last_error(void) { return "TLS is not supported on this target"; }

#endif /* __wasi__ || _WIN32 */
