/* The fake in-process TLS backend for the https:// transport test.

A real TLS backend (#235) implements the four calls of the transport
contract in ps_tls.h and returns them from ps_tls_backend(). This one
returns a transport that connects to a plaintext HTTP listener over the
loopback, reads and writes it like a TLS stream would, and reports
hostname verification on, so the https:// request in the test proves the
request travels through the transport interface, not that TLS is real.

The handle is the socket fd, and the read wraps select with the per-call
timeout the way the plaintext backend does, so a hung listener times out
instead of hanging the test.
*/

#include "ps_rt.h"
#include "ps_tls.h"

#include <arpa/inet.h>
#include <errno.h>
#include <netdb.h>
#include <stdio.h>
#include <string.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

static int64_t fake_tls_connect(const char *host, int port, double timeout) {
    (void)timeout;
    char port_text[8];
    struct addrinfo hints;
    struct addrinfo *res = NULL;
    snprintf(port_text, sizeof port_text, "%d", port);
    memset(&hints, 0, sizeof hints);
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    int gai = getaddrinfo(host, port_text, &hints, &res);
    if (gai != 0 || res == NULL) {
        return PS_HTTP_IO_ERROR;
    }
    int fd = socket(res->ai_family, res->ai_socktype, res->ai_protocol);
    if (fd >= 0 && connect(fd, res->ai_addr, res->ai_addrlen) != 0) {
        close(fd);
        fd = -1;
    }
    freeaddrinfo(res);
    if (fd < 0) {
        return PS_HTTP_IO_ERROR;
    }
    return (int64_t)fd;
}

static int64_t fake_tls_read(int64_t handle, char *buf, int64_t len, double timeout) {
    int fd = (int)handle;
    struct timeval tv;
    tv.tv_sec = (time_t)timeout;
    tv.tv_usec = (suseconds_t)((timeout - (double)tv.tv_sec) * 1e6);
    fd_set rf;
    FD_ZERO(&rf);
    FD_SET(fd, &rf);
    int n = select(fd + 1, &rf, NULL, NULL, &tv);
    if (n <= 0) {
        return n == 0 ? PS_HTTP_IO_TIMEOUT : PS_HTTP_IO_ERROR;
    }
    ssize_t got = read(fd, buf, (size_t)len);
    if (got <= 0) {
        return PS_HTTP_IO_ERROR;
    }
    return (int64_t)got;
}

static int64_t fake_tls_write(int64_t handle, const char *buf, int64_t len, double timeout) {
    (void)timeout;
    ssize_t wrote = write((int)handle, buf, (size_t)len);
    if (wrote <= 0) {
        return PS_HTTP_IO_ERROR;
    }
    return (int64_t)wrote;
}

static void fake_tls_close(int64_t handle) { close((int)handle); }

static const PsHttpTransport FAKE_TLS_TRANSPORT = {
    fake_tls_connect,
    fake_tls_read,
    fake_tls_write,
    fake_tls_close,
    /* Verification on: a backend that would report it off is refused by
       the client, and the test relies on the refusal being reachable. */
    1,
};

const PsHttpTransport *ps_tls_backend(void) { return &FAKE_TLS_TRANSPORT; }
