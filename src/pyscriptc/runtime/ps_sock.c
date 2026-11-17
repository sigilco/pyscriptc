/* The blocking TCP socket surface (#1270): the closed client and server
   subset over the host's BSD socket API.

   One socket record per `socket.socket(AF_INET, SOCK_STREAM)` call; the
   value the compiled program holds is this struct's address, the same
   opaque-pointer shape every runtime handle (deque, queue, selector,
   archive) uses, so the emitted IR never sees the layout.

   The surface is the loopback TCP echo shape: `bind(addr, port)`,
   `listen()`, `accept()`, `connect(addr, port)`, `recv(n)`, `recv_into`
   over `recv`, `send`, `sendall`, `close`, and the context-manager form
   the lowering lowers as close-on-both-paths. `SO_REUSEADDR` through
   `setsockopt` is the one option the subset reads; it is what a
   bind-listen-close-bind loop needs between runs.

   `recv(n)` returns the bytes one `recv(2)` handed back (short reads are
   CPython's short reads); `sendall` loops until the buffer is out, the
   way CPython's `socket.socket.sendall` does, and `send` answers what one
   `send(2)` accepted. `accept` answers the (conn, address) pair as the
   (int fd, str "addr:port") pair CPython's tuple carries on IPv4; the
   lowering unpacks it into the synthetic tuple the way `os.pipe` does.

   `socket.socketpair` is outside the subset (AF_UNIX is IPv4 loopback and
   named hosts only). Non-blocking mode, timeouts and `select` on sockets
   stay fenced upstream (divergence 29 / the #229 notes name the fences).

   Host-only: a WASI target has no socket story, so the stub half raises
   the runtime error a WASI program would hit at the first call, the same
   shape ps_select.c and ps_sub.c stubs use; a mobile build excludes the
   whole file (MOBILE_EXCLUDED_SOURCES).
*/

#include "ps_rt.h"

#include <stddef.h>

#if !defined(__wasi__) && !defined(_WIN32)

#include <arpa/inet.h>
#include <errno.h>
#include <netinet/in.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <unistd.h>

typedef struct PsSock {
    int fd;
    /* The address pair the last accept or getsockname filled: the host text
       and the port, the two halves of CPython's (host, port) pair. Accept
       writes the connected peer's, getsockname the bound one; the lowering
       reads the pair back in the same call sequence that filled it. */
    char peer_host[64];
    int64_t peer_port;
} PsSock;

static PsSock *ps_sock_of(void *box) {
    if (box == NULL) {
        ps_raise_cstr(PS_RUNTIME, "socket is not initialized");
    }
    return (PsSock *)box;
}

static void ps_sock_fail(const char *what) {
    ps_raise_cstr(PS_OSERROR, what);
}

void *ps_sock_new(void) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) {
        ps_sock_fail("socket failed");
    }
    PsSock *sock = malloc(sizeof(PsSock));
    if (sock == NULL) {
        abort();
    }
    sock->fd = fd;
    return sock;
}

void ps_sock_setsockopt_reuseaddr(void *box) {
    PsSock *sock = ps_sock_of(box);
    int one = 1;
    if (setsockopt(sock->fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one) != 0) {
        ps_sock_fail("setsockopt failed");
    }
}

static void ps_sock_fill_addr(const char *host, int64_t port, struct sockaddr_in *addr) {
    memset(addr, 0, sizeof *addr);
    addr->sin_family = AF_INET;
    addr->sin_port = htons((uint16_t)port);
    if (host == NULL || strcmp(host, "") == 0 || strcmp(host, "0.0.0.0") == 0) {
        addr->sin_addr.s_addr = htonl(INADDR_ANY);
        return;
    }
    if (inet_pton(AF_INET, host, &addr->sin_addr) != 1) {
        ps_sock_fail("address is not an IPv4 literal");
    }
}

void ps_sock_bind(void *box, int64_t n, const char *host, int64_t port) {
    PsSock *sock = ps_sock_of(box);
    struct sockaddr_in addr;
    ps_sock_fill_addr(host, port, &addr);
    if (bind(sock->fd, (struct sockaddr *)&addr, sizeof addr) != 0) {
        ps_sock_fail("bind failed");
    }
}

void ps_sock_listen(void *box, int64_t backlog) {
    PsSock *sock = ps_sock_of(box);
    if (backlog <= 0) {
        backlog = SOMAXCONN;
    }
    if (listen(sock->fd, (int)backlog) != 0) {
        ps_sock_fail("listen failed");
    }
}

/* accept: the conn socket, CPython's first half of the (conn, address)
   pair. The peer address lands in the record, and the lowering reads it
   back with ps_sock_peer_host and ps_sock_peer_port (the pair's second
   half, the (host, port) pair). */
void *ps_sock_accept(void *box) {
    PsSock *sock = ps_sock_of(box);
    struct sockaddr_in peer;
    socklen_t peer_len = sizeof peer;
    int conn = accept(sock->fd, (struct sockaddr *)&peer, &peer_len);
    if (conn < 0) {
        ps_sock_fail("accept failed");
    }
    char ip[INET_ADDRSTRLEN];
    if (inet_ntop(AF_INET, &peer.sin_addr, ip, sizeof ip) == NULL) {
        close(conn);
        ps_sock_fail("accept failed");
    }
    size_t wrote = strlen(ip);
    if (wrote >= sizeof sock->peer_host) {
        close(conn);
        ps_sock_fail("accept failed");
    }
    memcpy(sock->peer_host, ip, wrote + 1);
    sock->peer_port = (int64_t)ntohs(peer.sin_port);
    PsSock *conn_sock = malloc(sizeof(PsSock));
    if (conn_sock == NULL) {
        abort();
    }
    conn_sock->fd = conn;
    conn_sock->peer_host[0] = '\0';
    conn_sock->peer_port = 0;
    return conn_sock;
}


/* getsockname: the bound address of the socket, CPython's (host, port)
   pair. The host text and the port land in the record, the way the accept
   peer address does, so the lowering reads them back with the same two
   helpers. */
void ps_sock_getsockname(void *box) {
    PsSock *sock = ps_sock_of(box);
    struct sockaddr_in addr;
    socklen_t addr_len = sizeof addr;
    if (getsockname(sock->fd, (struct sockaddr *)&addr, &addr_len) != 0) {
        ps_sock_fail("getsockname failed");
    }
    char ip[INET_ADDRSTRLEN];
    if (inet_ntop(AF_INET, &addr.sin_addr, ip, sizeof ip) == NULL) {
        ps_sock_fail("getsockname failed");
    }
    size_t wrote = strlen(ip);
    if (wrote >= sizeof sock->peer_host) {
        ps_sock_fail("getsockname failed");
    }
    memcpy(sock->peer_host, ip, wrote + 1);
    sock->peer_port = (int64_t)ntohs(addr.sin_port);
}

/* The peer host text of the last accept, CPython's address pair's first
   half. */
void ps_sock_peer_host(void *box, PsBuf *out) {
    PsSock *sock = ps_sock_of(box);
    ps_buf_view(out, sock->peer_host, (int64_t)strlen(sock->peer_host));
}

/* The peer port of the last accept, the pair's second half. */
int64_t ps_sock_peer_port(void *box) {
    PsSock *sock = ps_sock_of(box);
    return sock->peer_port;
}

void ps_sock_connect(void *box, int64_t n, const char *host, int64_t port) {
    PsSock *sock = ps_sock_of(box);
    struct sockaddr_in addr;
    ps_sock_fill_addr(host, port, &addr);
    if (connect(sock->fd, (struct sockaddr *)&addr, sizeof addr) != 0) {
        ps_sock_fail("connect failed");
    }
}

void ps_sock_recv(void *box, int64_t size, PsBuf *out) {
    PsSock *sock = ps_sock_of(box);
    if (size < 0) {
        ps_raise_code(PS_VALUE);
    }
    char *buf = malloc((size_t)size + 1);
    if (buf == NULL) {
        abort();
    }
    ssize_t got = recv(sock->fd, buf, (size_t)size, 0);
    if (got < 0) {
        free(buf);
        ps_sock_fail("recv failed");
    }
    ps_buf_copy(out, buf, (int64_t)got);
    free(buf);
}

int64_t ps_sock_send(void *box, int64_t n, const char *s) {
    PsSock *sock = ps_sock_of(box);
    ssize_t put = send(sock->fd, s, (size_t)n, 0);
    if (put < 0) {
        ps_sock_fail("send failed");
    }
    return (int64_t)put;
}

void ps_sock_sendall(void *box, int64_t n, const char *s) {
    PsSock *sock = ps_sock_of(box);
    int64_t done = 0;
    while (done < n) {
        ssize_t put = send(sock->fd, s + done, (size_t)(n - done), 0);
        if (put < 0) {
            if (errno == EINTR) {
                continue;
            }
            ps_sock_fail("sendall failed");
        }
        done += (int64_t)put;
    }
}

void ps_sock_close(void *box) {
    PsSock *sock = ps_sock_of(box);
    if (sock->fd >= 0) {
        close(sock->fd);
        sock->fd = -1;
    }
}

int64_t ps_sock_fileno(void *box) {
    PsSock *sock = ps_sock_of(box);
    return (int64_t)sock->fd;
}

/* The descriptor the socket record owns. The stream surface (ps_stream.c)
   rides this socket, and the loop waits on the descriptor, so the stream
   reads it through this accessor instead of knowing the socket layout. */
int64_t ps_sock_fd(void *box) {
    PsSock *sock = ps_sock_of(box);
    return (int64_t)sock->fd;
}

#else /* __wasi__ || _WIN32 */

/* No sockets on a target without them: the records exist so the link
   stays whole, and every call raises the runtime error a WASI program
   would hit at the first socket call anyway. */

void *ps_sock_new(void) {
    ps_raise_cstr(PS_RUNTIME, "sockets need the BSD socket API, which this target has no story for");
    return NULL;
}

void ps_sock_setsockopt_reuseaddr(void *box) {
    (void)box;
    ps_raise_cstr(PS_RUNTIME, "sockets need the BSD socket API, which this target has no story for");
}

void ps_sock_bind(void *box, int64_t n, const char *host, int64_t port) {
    (void)box;
    (void)n;
    (void)host;
    (void)port;
    ps_raise_cstr(PS_RUNTIME, "sockets need the BSD socket API, which this target has no story for");
}

void ps_sock_listen(void *box, int64_t backlog) {
    (void)box;
    (void)backlog;
    ps_raise_cstr(PS_RUNTIME, "sockets need the BSD socket API, which this target has no story for");
}

void *ps_sock_accept(void *box) {
    (void)box;
    ps_raise_cstr(PS_RUNTIME, "sockets need the BSD socket API, which this target has no story for");
    return NULL;
}


void ps_sock_getsockname(void *box) {
    (void)box;
    ps_raise_cstr(PS_RUNTIME, "sockets need the BSD socket API, which this target has no story for");
}

void ps_sock_peer_host(void *box, PsBuf *out) {
    (void)box;
    (void)out;
    ps_raise_cstr(PS_RUNTIME, "sockets need the BSD socket API, which this target has no story for");
}

int64_t ps_sock_peer_port(void *box) {
    (void)box;
    ps_raise_cstr(PS_RUNTIME, "sockets need the BSD socket API, which this target has no story for");
    return 0;
}

void ps_sock_connect(void *box, int64_t n, const char *host, int64_t port) {
    (void)box;
    (void)n;
    (void)host;
    (void)port;
    ps_raise_cstr(PS_RUNTIME, "sockets need the BSD socket API, which this target has no story for");
}

void ps_sock_recv(void *box, int64_t size, PsBuf *out) {
    (void)box;
    (void)size;
    (void)out;
    ps_raise_cstr(PS_RUNTIME, "sockets need the BSD socket API, which this target has no story for");
}

int64_t ps_sock_send(void *box, int64_t n, const char *s) {
    (void)box;
    (void)n;
    (void)s;
    ps_raise_cstr(PS_RUNTIME, "sockets need the BSD socket API, which this target has no story for");
    return 0;
}

void ps_sock_sendall(void *box, int64_t n, const char *s) {
    (void)box;
    (void)n;
    (void)s;
    ps_raise_cstr(PS_RUNTIME, "sockets need the BSD socket API, which this target has no story for");
}

void ps_sock_close(void *box) {
    (void)box;
}

int64_t ps_sock_fileno(void *box) {
    (void)box;
    return -1;
}

int64_t ps_sock_fd(void *box) {
    (void)box;
    return -1;
}

#endif /* __wasi__ || _WIN32 */
