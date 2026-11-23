/* SPDX-License-Identifier: Apache-2.0 */
/* The asyncio stream surface (issue #1281): open_connection and the
   StreamReader / StreamWriter pair.

   One stream record per `asyncio.open_connection(host, port)` call; the
   pair CPython answers is two handles over the same connected socket, so
   one record backs both halves and the reader and the writer see the
   same descriptor. The value the compiled program holds is the record's
   address, the same opaque-pointer shape the socket handle (#1270) uses,
   so the emitted IR never sees the layout.

   The reads are cooperative: a read with nothing buffered asks the
   socket for what it has, and when the socket has none the reader parks
   the running task on the descriptor (ps_loop_wait_fd) and suspends.
   The loop wakes the task when the socket becomes readable, the await
   resumes and the read polls again. The socket itself is never read in
   blocking mode, so one slow peer does not stop the other tasks.

   The buffer is the reader's own: `read(n)` takes what the buffer holds
   and waits for more only while the buffer is short; `readline()` reads
   up to and including the first newline and the bytes past it stay
   buffered for the next read; `readexactly(n)` takes exactly n bytes and
   raises RuntimeError when the peer closes first (CPython raises
   IncompleteReadError there, whose message carries the partial bytes).

   `drain()` is the writer's flush barrier: the writer hands the socket
   the bytes it holds and returns when the socket took them all. The
   socket send is blocking (the socket surface has no non-blocking mode),
   so a full socket buffer parks the whole loop for the send, the same
   seam `socket.sendall` has.

   The server half (`asyncio.start_server`, the issue's second slice) is
   a listen record: bind + listen at `start_server`, a non-blocking
   accept the serve loop parks on (`ps_loop_wait_fd` again), and the
   connected peer served through the same PsStream record the client
   pair uses, so the callback's (reader, writer) handle the exact
   surface slice 1 lowers. The accept itself never blocks the loop:
   the socket is non-blocking (O_NONBLOCK on the listen fd) and the
   serve loop parks the task on the listen descriptor between peers.

   Host-only: a WASI target has no socket story, so the stub half raises
   the runtime error a WASI program would hit at the first call, the
   same shape ps_sock.c's stubs use; the wasm link refuses the socket
   symbols before that. A mobile build excludes the whole file
   (MOBILE_EXCLUDED_SOURCES), like ps_sock.c itself.
*/

#include "ps_rt.h"

#include <stddef.h>

#if !defined(__wasi__) && !defined(_WIN32)

#include <errno.h>
#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <unistd.h>

/* The socket record of ps_sock.c is opaque here, so the descriptor and
   the connect come through the accessors the socket file owns. */
extern int64_t ps_sock_fd(void *box);
extern void *ps_sock_new(void);
extern void ps_sock_connect(void *box, int64_t n, const char *host, int64_t port);
extern void ps_sock_close(void *box);
extern void ps_sock_bind(void *box, int64_t n, const char *host, int64_t port);
extern void ps_sock_listen(void *box, int64_t backlog);
extern void *ps_sock_accept(void *box);

typedef struct PsStream {
    /* The connected socket: the reader and the writer share it. */
    void *sock;
    /* The read buffer: the bytes the socket handed over that no read has
       taken yet (pos reads, len ends). */
    char *buf;
    int64_t len;
    int64_t cap;
    int64_t pos;
    /* The pending read request: the mode (0 read(n), 1 readline,
       2 readexactly, 3 read(-1)) and the n it carries. The park call
       answers whether the request is satisfiable right now; the read
       call the lowerer emits after the park takes the request out. */
    int32_t want_mode;
    int64_t want_n;
    /* The bytes the writer has not handed to the socket yet. */
    char *out;
    int64_t out_len;
    int64_t out_cap;
    int64_t out_pos;
    /* Set once the peer closed the socket (a read saw the end of file). */
    int32_t eof;
    /* Set once the writer closed the socket. */
    int32_t closed;
} PsStream;

static void ps_stream_fail(const char *what) {
    ps_raise_cstr(PS_OSERROR, what);
}

static PsStream *ps_stream_of(void *box) {
    if (box == NULL) {
        ps_raise_cstr(PS_RUNTIME, "stream is not initialized");
    }
    return (PsStream *)box;
}

/* The reader's buffer grows to hold `want` bytes. */
static void ps_stream_reserve(PsStream *st, int64_t want) {
    if (want <= st->cap) {
        return;
    }
    int64_t cap = st->cap == 0 ? 256 : st->cap;
    while (cap < want) {
        cap *= 2;
    }
    char *grown = realloc(st->buf, (size_t)cap);
    if (grown == NULL) {
        abort();
    }
    st->buf = grown;
    st->cap = cap;
}

/* The bytes the reader holds that no read has taken yet. */
static int64_t ps_stream_buffered(PsStream *st) {
    return st->len - st->pos;
}

/* Ask the socket for whatever it has, without blocking: the reader reads
   only the bytes the socket already holds. Returns the byte count (0 at
   end of file), or -1 when the socket has nothing right now (the caller
   parks on the descriptor and polls again). */
static int64_t ps_stream_poll(PsStream *st) {
    ps_stream_reserve(st, st->cap == 0 ? 256 : st->cap);
    for (;;) {
        ssize_t got =
            recv(ps_sock_fd(st->sock), st->buf + st->len, (size_t)(st->cap - st->len), MSG_DONTWAIT);
        if (got < 0) {
            if (errno == EINTR) {
                continue;
            }
            if (errno == EAGAIN || errno == EWOULDBLOCK) {
                return -1;
            }
            ps_stream_fail("recv failed");
        }
        if (got == 0) {
            st->eof = 1;
            return 0;
        }
        st->len += (int64_t)got;
        return (int64_t)got;
    }
}

/* Park the running task on the descriptor: the loop wakes the task when
   the socket becomes readable and the read polls again on the resume. */
static void ps_stream_wait_readable(PsStream *st) {
    ps_loop_wait_fd((int)ps_sock_fd(st->sock));
}

/* Pull one non-blocking recv into the buffer: bytes counted (0 at end
   of file) or -1 when the socket had nothing. */
static int64_t ps_stream_pull(PsStream *st) {
    return ps_stream_poll(st);
}

/* Whether the buffered bytes satisfy the pending request. Read(n) wants
   n bytes (or any at all when the end of file is in); readline wants a
   newline or the end of file; readexactly wants n and the end of file
   is the error the read raises; read(-1) wants one poll's worth. */
static int32_t ps_stream_satisfies(PsStream *st) {
    int64_t held = ps_stream_buffered(st);
    switch (st->want_mode) {
    case 1: /* readline */
        if (st->eof) {
            return 1;
        }
        for (int64_t i = 0; i < held; i++) {
            if (st->buf[st->pos + i] == '\n') {
                return 1;
            }
        }
        return 0;
    case 2: /* readexactly */
        if (st->eof) {
            return 1;
        }
        return held >= st->want_n ? 1 : 0;
    case 3: /* read(-1) */
        return st->eof ? 1 : (held > 0 ? 1 : 0);
    default: /* read(n) */
        if (st->eof || st->want_n <= 0) {
            return 1;
        }
        return held > 0 ? 1 : 0;
    }
}

/* The read park (the PRIM marker's stream_read op): 1 when the pending
   request can be served right now (the buffer holds enough, or the end
   of file is in), 0 when the task parked on the descriptor and the frame
   has to suspend. The loop wakes the task when the socket is readable
   and the resume parks again at the same point. */
int64_t ps_stream_park(void *box) {
    PsStream *st = ps_stream_of(box);
    if (ps_stream_satisfies(st)) {
        return 1;
    }
    if (ps_stream_pull(st) > 0 || st->eof) {
        return ps_stream_satisfies(st) ? 1 : 0;
    }
    ps_stream_wait_readable(st);
    return 0;
}

/* The pending request the lowerer records before the park: the mode and
   the n it carries. The read call that follows the park takes the
   request out of the record and serves it. */
void ps_stream_want(void *box, int64_t mode, int64_t n) {
    PsStream *st = ps_stream_of(box);
    st->want_mode = (int32_t)mode;
    st->want_n = n;
}

/* Hand `n` buffered bytes to `out` and drop them from the buffer. */
static void ps_stream_take(PsStream *st, int64_t n, PsBuf *out) {
    if (n > ps_stream_buffered(st)) {
        n = ps_stream_buffered(st);
    }
    ps_buf_copy(out, st->buf + st->pos, n);
    st->pos += n;
    if (st->pos == st->len) {
        st->pos = 0;
        st->len = 0;
    }
}

/* `asyncio.open_connection(host, port)`: connect a fresh socket and
   build the record both handles share. */
void *ps_stream_open(int64_t n, const char *host, int64_t port) {
    void *sock = ps_sock_new();
    ps_sock_connect(sock, n, host, port);
    PsStream *st = malloc(sizeof(PsStream));
    if (st == NULL) {
        abort();
    }
    st->sock = sock;
    st->buf = NULL;
    st->len = 0;
    st->cap = 0;
    st->pos = 0;
    st->want_mode = 0;
    st->want_n = 0;
    st->out = NULL;
    st->out_len = 0;
    st->out_cap = 0;
    st->out_pos = 0;
    st->eof = 0;
    st->closed = 0;
    return st;
}

/* `read(n)`: the bytes the buffer holds, up to n. The park before this
   call waited until the socket had something, so the read never blocks.
   A negative n drains everything until the peer closes, the way
   CPython's `read(-1)` does. */
void ps_stream_read(void *box, int64_t n, PsBuf *out) {
    PsStream *st = ps_stream_of(box);
    if (n < 0) {
        /* Read(-1): everything the peer sent, up to the close. The park
           already waited for the first byte; the rest arrive with the
           close, so the read takes what the buffer holds and the peer's
           close ends it. */
        while (!st->eof && ps_stream_pull(st) > 0) {
            /* Keep pulling until the socket has nothing or the peer closed. */
        }
        ps_stream_take(st, ps_stream_buffered(st), out);
        return;
    }
    if (ps_stream_buffered(st) == 0 && !st->eof) {
        /* The park said there was something; pull it in if the buffer is
           still empty (the bytes may have arrived between the park and
           the read). */
        ps_stream_pull(st);
    }
    ps_stream_take(st, n, out);
}

/* `readline()`: the bytes up to and including the newline. The bytes
   past the newline stay in the buffer for the next read. The park before
   this call waited for a newline or the end of file. */
void ps_stream_readline(void *box, PsBuf *out) {
    PsStream *st = ps_stream_of(box);
    if (ps_stream_buffered(st) == 0 && !st->eof) {
        ps_stream_pull(st);
    }
    int64_t held = ps_stream_buffered(st);
    for (int64_t i = 0; i < held; i++) {
        if (st->buf[st->pos + i] == '\n') {
            ps_stream_take(st, i + 1, out);
            return;
        }
    }
    /* End of file: the line is whatever the buffer holds. */
    ps_stream_take(st, held, out);
}

/* `readexactly(n)`: exactly n bytes. The park before this call waited
   until the buffer held n bytes or the peer closed. The peer closing
   first raises RuntimeError (CPython raises IncompleteReadError there,
   whose message carries the partial bytes). */
void ps_stream_readexactly(void *box, int64_t n, PsBuf *out) {
    PsStream *st = ps_stream_of(box);
    if (n < 0) {
        ps_raise_code(PS_VALUE);
    }
    while (ps_stream_buffered(st) < n && !st->eof) {
        ps_stream_pull(st);
    }
    if (ps_stream_buffered(st) < n) {
        ps_raise_cstr(PS_RUNTIME, "incomplete read: the peer closed before the bytes arrived");
    }
    ps_stream_take(st, n, out);
}

/* --- the writer --------------------------------------------------------- */

/* The writer's out buffer grows to hold `want` bytes. */
static void ps_stream_reserve_out(PsStream *st, int64_t want) {
    if (want <= st->out_cap) {
        return;
    }
    int64_t cap = st->out_cap == 0 ? 256 : st->out_cap;
    while (cap < want) {
        cap *= 2;
    }
    char *grown = realloc(st->out, (size_t)cap);
    if (grown == NULL) {
        abort();
    }
    st->out = grown;
    st->out_cap = cap;
}

/* `write(data)`: the bytes ride the writer's buffer and go out on the
   next drain, the way CPython's transport buffer takes them. The call
   answers None, so the ABI's tail is a void. */
void ps_stream_write_buf(void *box, int64_t n, const char *s) {
    PsStream *st = ps_stream_of(box);
    if (st->closed) {
        ps_stream_fail("write on a closed stream");
    }
    ps_stream_reserve_out(st, st->out_len + n);
    if (n > 0) {
        memcpy(st->out + st->out_len, s, (size_t)n);
        st->out_len += n;
    }
}

/* Hand the writer's buffer to the socket: the bytes go out until the
   socket took them all (the sendall loop). */
static void ps_stream_flush(PsStream *st) {
    while (st->out_pos < st->out_len) {
        ssize_t put = send(ps_sock_fd(st->sock), st->out + st->out_pos, (size_t)(st->out_len - st->out_pos), 0);
        if (put < 0) {
            if (errno == EINTR) {
                continue;
            }
            ps_stream_fail("send failed");
        }
        st->out_pos += (int64_t)put;
    }
    st->out_pos = 0;
    st->out_len = 0;
}

/* `drain()`: the writer's flush barrier. The bytes leave the writer's
   buffer and the call returns when the socket took them all. */
void ps_stream_drain(void *box) {
    ps_stream_flush(ps_stream_of(box));
}

/* `close()`: the writer flushes what it holds and closes the socket.
   A second close is a no-op, the way CPython's transport close is. */
void ps_stream_close(void *box) {
    PsStream *st = ps_stream_of(box);
    if (st->closed) {
        return;
    }
    ps_stream_flush(st);
    st->closed = 1;
    ps_sock_close(st->sock);
}

/* `wait_closed()`: the close is done once the flush came back, so the
   wait is the flush. */
void ps_stream_wait_closed(void *box) {
    ps_stream_flush(ps_stream_of(box));
}

/* --- the server (issue #1281, slice 2) ---------------------------------- */

/* One listen record per `asyncio.start_server(cb, host, port)` call. The
   callback is compiled code, so the record holds its callable pair as an
   opaque pointer: the serve loop reads the pair back and calls it
   env-first (emit_value_call's convention), and the runtime never
   dereferences it. */
typedef struct PsServer {
    void *sock;      /* the listening socket (ps_sock.c's record) */
    void *callback;  /* the callable pair {impl, env} the serve loop calls */
    int32_t closed;
    /* The task parked in serve_forever, so `close` wakes it the way
       CPython's Server.close cancels the serve_forever future. */
    void *waiter;
    /* The peer the last accept produced: the stream record the callback's
       (reader, writer) pair points at. */
    void *accepted;
} PsServer;

static PsServer *ps_server_of(void *box) {
    if (box == NULL) {
        ps_raise_cstr(PS_RUNTIME, "server is not initialized");
    }
    return (PsServer *)box;
}

/* `asyncio.start_server(cb, host, port)`: bind and listen. */
void *ps_server_open(int64_t n, const char *host, int64_t port) {
    void *sock = ps_sock_new();
    ps_sock_setsockopt_reuseaddr(sock);
    ps_sock_bind(sock, n, host, port);
    ps_sock_listen(sock, 128);
    /* The accept is cooperative: the listen fd goes non-blocking, the
       park asks it for a peer, and the serve loop parks on the
       descriptor between peers. The peers themselves keep the blocking
       shape the stream surface rides (the reads poll with MSG_DONTWAIT,
       the sends block the loop the same seam sendall has). */
    int fd = (int)ps_sock_fd(sock);
    int flags = fcntl(fd, F_GETFL, 0);
    if (flags >= 0) {
        fcntl(fd, F_SETFL, flags | O_NONBLOCK);
    }
    PsServer *srv = malloc(sizeof(PsServer));
    if (srv == NULL) {
        abort();
    }
    srv->sock = sock;
    srv->callback = NULL;
    srv->closed = 0;
    srv->waiter = NULL;
    srv->accepted = NULL;
    return srv;
}

/* Store the callback's callable pair: the compiled code built it, the
   runtime only holds the pointer. */
void ps_server_set_callback(void *box, void *pair) {
    PsServer *srv = ps_server_of(box);
    srv->callback = pair;
}

/* The callback's callable pair, the value the serve loop calls. */
void *ps_server_callback(void *box) {
    PsServer *srv = ps_server_of(box);
    return srv->callback;
}

/* The accept park (the PRIM marker's server_accept op): 1 when a peer
   is connected and the record is built, 0 when the task parked on the
   listen descriptor and the frame has to suspend. The resume parks
   again at the same point, so the serve loop's re-entry finds the peer
   waiting. The connected peer builds a fresh PsStream record, the same
   shape `open_connection` builds, so the callback's (reader, writer)
   handle the exact slice-1 surface. The peer's stream rides out of the
   record's `accepted` slot, the way the queue's taken element does. */
int64_t ps_server_park(void *box) {
    extern void *ps_loop_current_task(void);
    PsServer *srv = ps_server_of(box);
    if (srv->closed) {
        /* The server was closed: CPython's `Server.close` cancels the
           serve_forever future, so the await raises CancelledError. */
        ps_raise_code(PS_CANCELLED);
    }
    for (;;) {
        int fd = (int)ps_sock_fd(srv->sock);
        struct sockaddr_storage peer;
        socklen_t peer_len = sizeof peer;
        int conn = accept(fd, (struct sockaddr *)&peer, &peer_len);
        if (conn >= 0) {
            PsStream *st = malloc(sizeof(PsStream));
            if (st == NULL) {
                abort();
            }
            /* The accepted connection rides a socket record of its own:
               the stream surface drives reads and writes through
               ps_sock.c's fd accessor, so the conn fd is wrapped the
               same way a connected client is. */
            st->sock = ps_sock_wrap(conn);
            st->buf = NULL;
            st->len = 0;
            st->cap = 0;
            st->pos = 0;
            st->want_mode = 0;
            st->want_n = 0;
            st->out = NULL;
            st->out_len = 0;
            st->out_cap = 0;
            st->out_pos = 0;
            st->eof = 0;
            st->closed = 0;
            srv->accepted = st;
            return 1;
        }
        if (errno == EINTR) {
            continue;
        }
        if (errno == EAGAIN || errno == EWOULDBLOCK) {
            srv->waiter = ps_loop_current_task();
            ps_loop_wait_fd(fd);
            return 0;
        }
        ps_stream_fail("accept failed");
    }
}

/* The parked serve_forever's waiter row, dropped so a later close is a
   plain no-op: the park cleared it, the loop's fire dropped the row, and
   the re-entry cleared `waiter` before the next park. */
static void ps_server_wake_waiter(PsServer *srv) {
    extern void ps_loop_wake_task(void *task);
    void *waiter = srv->waiter;
    srv->waiter = NULL;
    if (waiter == NULL) {
        return;
    }
    ps_loop_wake_task(waiter);
}

/* The accepted peer's stream, the (reader, writer) pair's record. Read
   right after a park that answered 1, the same order the queue's taken
   element rides. */
void *ps_server_accepted(void *box) {
    PsServer *srv = ps_server_of(box);
    void *st = srv->accepted;
    srv->accepted = NULL;
    return st;
}

/* `server.close()`: stop listening and drop the socket, and cancel the
   serve_forever the way CPython's Server.close cancels the future: the
   parked task is woken, its fd row is dropped and its next park raises
   CancelledError. A second close is a no-op, the way CPython's is. */
void ps_server_close(void *box) {
    extern void ps_loop_drop_fd_wait(int fd);
    PsServer *srv = ps_server_of(box);
    if (srv->closed) {
        return;
    }
    int fd = (int)ps_sock_fd(srv->sock);
    srv->closed = 1;
    ps_loop_drop_fd_wait(fd);
    ps_sock_close(srv->sock);
    ps_server_wake_waiter(srv);
}

/* `server.is_serving()`: CPython answers True while the server takes
   peers; the closed flag is that answer here. */
int64_t ps_server_is_serving(void *box) {
    PsServer *srv = ps_server_of(box);
    return srv->closed ? 0 : 1;
}

/* The closed flag on its own: the serve loop's stop test reads it after
   every peer, the way CPython's serve_forever future is cancelled by
   `Server.close`. */
int64_t ps_server_closed(void *box) {
    PsServer *srv = ps_server_of(box);
    return srv->closed ? 1 : 0;
}

/* The listening socket handle (CPython's `server.sockets[0]`): the record
   the server owns is the socket record, so the handle is that pointer. */
void *ps_server_socket(void *box) {
    PsServer *srv = ps_server_of(box);
    return srv->sock;
}

/* The bound port (CPython's `server.sockets[0].getsockname()[1]`): the
   lowering reads the port the kernel assigned when the program bound 0.
   The read goes through the listen socket's own getsockname, so the
   record's pair is filled here, not at accept time. */
int64_t ps_server_port(void *box) {
    PsServer *srv = ps_server_of(box);
    ps_sock_getsockname(srv->sock);
    return ps_sock_peer_port(srv->sock);
}

#else /* __wasi__ || _WIN32 */

/* No sockets on a target without them: the records exist so the link
   stays whole, and every call raises the runtime error a WASI program
   would hit at the first socket call anyway (the wasm link refuses the
   socket symbols before that, so these are the Windows-cross path). */

void *ps_stream_open(int64_t n, const char *host, int64_t port) {
    (void)n;
    (void)host;
    (void)port;
    ps_raise_cstr(PS_RUNTIME, "streams need the BSD socket API, which this target has no story for");
    return NULL;
}

void ps_stream_read(void *box, int64_t n, PsBuf *out) {
    (void)box;
    (void)n;
    (void)out;
    ps_raise_cstr(PS_RUNTIME, "streams need the BSD socket API, which this target has no story for");
}

void ps_stream_readline(void *box, PsBuf *out) {
    (void)box;
    (void)out;
    ps_raise_cstr(PS_RUNTIME, "streams need the BSD socket API, which this target has no story for");
}

void ps_stream_readexactly(void *box, int64_t n, PsBuf *out) {
    (void)box;
    (void)n;
    (void)out;
    ps_raise_cstr(PS_RUNTIME, "streams need the BSD socket API, which this target has no story for");
}

void ps_stream_write_buf(void *box, int64_t n, const char *s) {
    (void)box;
    (void)n;
    (void)s;
    ps_raise_cstr(PS_RUNTIME, "streams need the BSD socket API, which this target has no story for");
}

void ps_stream_drain(void *box) {
    (void)box;
    ps_raise_cstr(PS_RUNTIME, "streams need the BSD socket API, which this target has no story for");
}

void ps_stream_close(void *box) {
    (void)box;
}

void ps_stream_wait_closed(void *box) {
    (void)box;
    ps_raise_cstr(PS_RUNTIME, "streams need the BSD socket API, which this target has no story for");
}

int64_t ps_stream_park(void *box) {
    (void)box;
    ps_raise_cstr(PS_RUNTIME, "streams need the BSD socket API, which this target has no story for");
    return 0;
}

void ps_stream_want(void *box, int64_t mode, int64_t n) {
    (void)box;
    (void)mode;
    (void)n;
}

void *ps_server_open(int64_t n, const char *host, int64_t port) {
    (void)n;
    (void)host;
    (void)port;
    ps_raise_cstr(PS_RUNTIME, "servers need the BSD socket API, which this target has no story for");
    return NULL;
}

void ps_server_set_callback(void *box, void *pair) {
    (void)box;
    (void)pair;
}

void *ps_server_callback(void *box) {
    (void)box;
    ps_raise_cstr(PS_RUNTIME, "servers need the BSD socket API, which this target has no story for");
    return NULL;
}

int64_t ps_server_park(void *box) {
    (void)box;
    ps_raise_cstr(PS_RUNTIME, "servers need the BSD socket API, which this target has no story for");
    return 0;
}

void *ps_server_accepted(void *box) {
    (void)box;
    ps_raise_cstr(PS_RUNTIME, "servers need the BSD socket API, which this target has no story for");
    return NULL;
}

void ps_server_close(void *box) {
    (void)box;
}

int64_t ps_server_is_serving(void *box) {
    (void)box;
    return 0;
}

int64_t ps_server_closed(void *box) {
    (void)box;
    return 0;
}

void *ps_server_socket(void *box) {
    (void)box;
    return NULL;
}

int64_t ps_server_port(void *box) {
    (void)box;
    return 0;
}

#endif /* __wasi__ || _WIN32 */
