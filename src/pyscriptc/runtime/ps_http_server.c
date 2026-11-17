/* SPDX-License-Identifier: Apache-2.0 */
/* The http.server substrate (#1270, slice 2): the request-line and header
   parsing, the connection ownership and the response writer.

   The closed surface is the one CPython's `http.server` writes down for a
   small handler: `HTTPServer`, a `BaseHTTPRequestHandler` subclass with
   `do_GET` and `do_POST`, `send_response`, `send_header`, `end_headers`,
   `wfile.write`, `rfile.read(n)`, `serve_forever` and `shutdown`.

   The split between this file and the lowering is deliberate. This file
   owns the bytes and the shapes: it binds and listens on the socket, it
   accepts a connection, it parses the request line and the header block,
   it reads the body, and it writes the status line, the header block and
   the body back. The request-line and header parsing stays small and
   explicit here, the way the design note asks.

   The dispatch stays out of this file. The runtime answers the method
   name of the request it just parsed (`GET`, `POST`), and the lowering
   turns that name into the user's `do_GET` / `do_POST` call through the
   ordinary class-method lowering. No new dispatch mechanism is introduced
   and the handler stays a lowered class, not a runtime record.

   The handler talks to this file through two records. The request record
   holds the parsed method, the path, the header block and the body; the
   response record holds the status line and the header block the handler
   wrote, plus the body bytes. Both are opaque pointers at the ABI: the
   emitted IR never sees the layout.

   The server loop is single-threaded and blocking, the way CPython's
   `HTTPServer.serve_forever` is on the default `socketserver` mixin (the
   threading mixin is fenced, see divergence 79). `shutdown()` stops the
   loop: it sets the stop flag and wakes the accept with a self-pipe, so
   a `shutdown()` from another thread lands without waiting for a
   connection.

   Host-only: a WASI target has no socket story, so the stub half raises
   the runtime error a WASI program would hit at the first call, the same
   shape ps_sock.c uses; a mobile build excludes the whole file
   (MOBILE_EXCLUDED_SOURCES).
*/

#include "ps_rt.h"

#include <stddef.h>

#if !defined(__wasi__) && !defined(_WIN32)

#include <arpa/inet.h>
#include <errno.h>
#include <netinet/in.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

/* The request record: what the runtime parsed off the wire. The handler
   reads it through the ps_httpreq_* helpers, never directly. */
typedef struct PsHttpReq {
    int fd;
    char method[16];
    char path[2048];
    /* The header block, one "Name: value\n" row per header, the same
       shape the urllib client's response keeps. The lookup is
       case-insensitive, the way CPython's email.Message.get is. */
    char *headers;
    int64_t headers_len;
    char *body;
    int64_t body_len;
} PsHttpReq;

/* The response record: the status line and the header block the handler
   wrote, plus the body bytes it wrote. */
typedef struct PsHttpResp {
    int64_t status;
    char *headers;
    int64_t headers_len;
    char *body;
    int64_t body_len;
    int64_t body_cap;
} PsHttpResp;

typedef struct PsHttpServer {
    int fd;
    /* The bound address: the host text and the port, the two halves of
       CPython's (host, port) pair. The lowering reads the pair back with
       the same two helpers the socket subset uses. */
    char host[64];
    int64_t port;
    /* The stop flag and the self-pipe: shutdown() sets the flag and
       writes a byte to the pipe, so a blocked accept returns. */
    volatile int stopped;
    int wake[2];
} PsHttpServer;

/* The pair's impl is env-first: `impl(env)`. */
typedef void (*ps_call_fn)(void *env);

static void ps_http_fail(const char *what) { ps_raise_cstr(PS_OSERROR, what); }

static PsHttpServer *ps_srv_of(void *box) {
    if (box == NULL) {
        ps_raise_cstr(PS_RUNTIME, "the http server is not initialized");
    }
    return (PsHttpServer *)box;
}

/* ----- the request record ----- */

static void ps_req_of(void *box, PsHttpReq **out) {
    if (box == NULL) {
        ps_raise_cstr(PS_RUNTIME, "the http request is not initialized");
    }
    *out = (PsHttpReq *)box;
}

/* The method name of the request: "GET", "POST" or whatever the request
   line said. The lowering reads it to pick the handler method. */
void ps_httpreq_method(void *box, PsBuf *out) {
    PsHttpReq *req;
    ps_req_of(box, &req);
    ps_buf_view(out, req->method, (int64_t)strlen(req->method));
}

/* The request path: the target of the request line, the way CPython's
   `self.path` reads it (the query string rides along). */
void ps_httpreq_path(void *box, PsBuf *out) {
    PsHttpReq *req;
    ps_req_of(box, &req);
    ps_buf_view(out, req->path, (int64_t)strlen(req->path));
}

/* The body of the request: the bytes the Content-Length named. */
void ps_httpreq_body(void *box, PsBuf *out) {
    PsHttpReq *req;
    ps_req_of(box, &req);
    ps_buf_copy(out, req->body, req->body_len);
}

/* The value of one header, looked up case-insensitively. The answer is
   empty when the header is absent, the way an absent header reads on the
   wire. */
void ps_httpreq_header(void *box, int64_t n, const char *name, PsBuf *out) {
    PsHttpReq *req;
    ps_req_of(box, &req);
    int64_t name_len = n;
    const char *rows = req->headers;
    int64_t rows_len = req->headers_len;
    int64_t at = 0;
    while (at < rows_len) {
        const char *row = rows + at;
        int64_t row_len = 0;
        while (at + row_len < rows_len && rows[at + row_len] != '\n') {
            row_len++;
        }
        /* The name runs up to the colon; the value runs from the colon
           plus the space up to the row's end. */
        int64_t colon = -1;
        for (int64_t i = 0; i < row_len; i++) {
            if (row[i] == ':') {
                colon = i;
                break;
            }
        }
        if (colon >= 0 && colon == name_len) {
            int64_t same = 1;
            for (int64_t i = 0; i < name_len; i++) {
                char a = row[i];
                char b = name[i];
                if (a >= 'A' && a <= 'Z') {
                    a = (char)(a - 'A' + 'a');
                }
                if (b >= 'A' && b <= 'Z') {
                    b = (char)(b - 'A' + 'a');
                }
                if (a != b) {
                    same = 0;
                    break;
                }
            }
            if (same) {
                int64_t start = colon + 1;
                while (start < row_len && (row[start] == ' ' || row[start] == '\t')) {
                    start++;
                }
                int64_t end = row_len;
                while (end > start && (row[end - 1] == '\r' || row[end - 1] == ' ')) {
                    end--;
                }
                ps_buf_view(out, row + start, end - start);
                return;
            }
        }
        at += row_len + 1;
    }
    ps_buf_view(out, "", 0);
}

/* rfile.read(n): the request's body bytes. The body is already read, so
   the count is the clamp of n against it (CPython's buffered read answers
   min(n, what is left)). */
void ps_httpreq_rfile_read(void *box, int64_t n, PsBuf *out) {
    PsHttpReq *req;
    ps_req_of(box, &req);
    if (n < 0) {
        ps_raise_code(PS_VALUE);
    }
    int64_t take = req->body_len < n ? req->body_len : n;
    ps_buf_copy(out, req->body, take);
}

/* ----- the response record ----- */

static void ps_resp_of(void *box, PsHttpResp **out) {
    if (box == NULL) {
        ps_raise_cstr(PS_RUNTIME, "the http response is not initialized");
    }
    *out = (PsHttpResp *)box;
}

/* A fresh response record: no status yet, no headers, no body. */
void *ps_httpresp_new(void) {
    PsHttpResp *resp = calloc(1, sizeof(PsHttpResp));
    if (resp == NULL) {
        abort();
    }
    resp->status = 0;
    return resp;
}

/* send_response(code): the status line. CPython writes
   "HTTP/1.0 <code> <reason>\r\n" and the Server and Date headers; the
   subset writes the status line and the Date header, and the reason
   phrase for the codes it knows. */
static const char *ps_http_reason(int64_t status) {
    switch (status) {
    case 200:
        return "OK";
    case 201:
        return "Created";
    case 202:
        return "Accepted";
    case 204:
        return "No Content";
    case 301:
        return "Moved Permanently";
    case 302:
        return "Found";
    case 304:
        return "Not Modified";
    case 400:
        return "Bad Request";
    case 401:
        return "Unauthorized";
    case 403:
        return "Forbidden";
    case 404:
        return "Not Found";
    case 405:
        return "Method Not Allowed";
    case 500:
        return "Internal Server Error";
    case 501:
        return "Unsupported method";
    default:
        return "Unknown";
    }
}

void ps_httpresp_send_response(void *box, int64_t status) {
    PsHttpResp *resp;
    ps_resp_of(box, &resp);
    resp->status = status;
}

/* send_header(name, value): one header row of the response block. */
void ps_httpresp_send_header(void *box, int64_t n, const char *name, int64_t vlen, const char *value) {
    PsHttpResp *resp;
    ps_resp_of(box, &resp);
    int64_t name_len = n;
    int64_t extra = name_len + 2 + vlen + 2;
    int64_t cap = resp->headers_len + extra + 1;
    char *grown = realloc(resp->headers, (size_t)cap);
    if (grown == NULL) {
        abort();
    }
    resp->headers = grown;
    memcpy(resp->headers + resp->headers_len, name, (size_t)name_len);
    resp->headers_len += name_len;
    resp->headers[resp->headers_len++] = ':';
    resp->headers[resp->headers_len++] = ' ';
    if (vlen > 0) {
        memcpy(resp->headers + resp->headers_len, value, (size_t)vlen);
        resp->headers_len += vlen;
    }
    resp->headers[resp->headers_len++] = '\r';
    resp->headers[resp->headers_len++] = '\n';
    resp->headers[resp->headers_len] = '\0';
}

/* end_headers(): the block is closed with the blank line. */
void ps_httpresp_end_headers(void *box) {
    PsHttpResp *resp;
    ps_resp_of(box, &resp);
    int64_t extra = 2;
    int64_t cap = resp->headers_len + extra + 1;
    char *grown = realloc(resp->headers, (size_t)cap);
    if (grown == NULL) {
        abort();
    }
    resp->headers = grown;
    resp->headers[resp->headers_len++] = '\r';
    resp->headers[resp->headers_len++] = '\n';
    resp->headers[resp->headers_len] = '\0';
}

/* wfile.write(body): the response body bytes. */
void ps_httpresp_wfile_write(void *box, int64_t n, const char *data) {
    PsHttpResp *resp;
    ps_resp_of(box, &resp);
    int64_t cap = resp->body_len + n + 1;
    if (cap > resp->body_cap) {
        char *grown = realloc(resp->body, (size_t)cap);
        if (grown == NULL) {
            abort();
        }
        resp->body = grown;
        resp->body_cap = cap;
    }
    if (n > 0) {
        memcpy(resp->body + resp->body_len, data, (size_t)n);
    }
    resp->body_len += n;
    resp->body[resp->body_len] = '\0';
}

/* The response status, the way the handler reads it back. */
int64_t ps_httpresp_status(void *box) {
    PsHttpResp *resp;
    ps_resp_of(box, &resp);
    return resp->status;
}

/* The header block of the request, the handle the handler's `headers`
   attribute carries. The record is opaque and the handle is the record
   itself: the block's reads go through ps_httpreq_header, which takes
   the same pointer. */
void *ps_httpreq_headers(void *box) {
    PsHttpReq *req;
    ps_req_of(box, &req);
    return req;
}

/* The request's body reader, the handle the handler's `rfile` attribute
   carries. The body is already read; `read(n)` clamps against it. */
void *ps_httpreq_rfile(void *box) {
    PsHttpReq *req;
    ps_req_of(box, &req);
    return req;
}

/* The response's body writer, the handle the handler's `wfile` attribute
   carries. The handle is the response record itself. */
void *ps_httpresp_wfile(void *box) {
    PsHttpResp *resp;
    ps_resp_of(box, &resp);
    return resp;
}

/* ----- the server ----- */

/* The bound address of the server: the lowering reads the host text and
   the port with the two helpers below, the way the socket subset reads
   its own pair. */

/* The bound host, read on its own: the lowering reads the two halves of
   the pair in separate calls, the way the socket subset reads its own. */
void ps_httpsrv_host(void *box, PsBuf *out) {
    PsHttpServer *srv = ps_srv_of(box);
    ps_buf_view(out, srv->host, (int64_t)strlen(srv->host));
}

/* The bound port, read on its own. */
int64_t ps_httpsrv_port(void *box) {
    PsHttpServer *srv = ps_srv_of(box);
    return srv->port;
}

/* The loop's stop test: 1 once shutdown() ran, 0 before it. The lowering
   reads it after each serve_one and ends its loop on it, so a shutdown
   from another thread lands without a connection (CPython's
   serve_forever/shutdown pair). */
int64_t ps_httpsrv_stopped(void *box) {
    PsHttpServer *srv = ps_srv_of(box);
    return srv->stopped;
}

/* The server record: the listening socket, the bound address and the
   stop machinery. The handler class rides the server as a lowered user
   class, so the record does not own it. */
void *ps_httpsrv_new(int64_t n, const char *host, int64_t port) {
    (void)n;
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) {
        ps_http_fail("socket failed");
    }
    int one = 1;
    if (setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one) != 0) {
        close(fd);
        ps_http_fail("setsockopt failed");
    }
    struct sockaddr_in addr;
    memset(&addr, 0, sizeof addr);
    addr.sin_family = AF_INET;
    addr.sin_port = htons((uint16_t)port);
    if (host == NULL || strcmp(host, "") == 0 || strcmp(host, "0.0.0.0") == 0) {
        addr.sin_addr.s_addr = htonl(INADDR_ANY);
    } else if (inet_pton(AF_INET, host, &addr.sin_addr) != 1) {
        close(fd);
        ps_http_fail("address is not an IPv4 literal");
    }
    if (bind(fd, (struct sockaddr *)&addr, sizeof addr) != 0) {
        close(fd);
        ps_http_fail("bind failed");
    }
    if (listen(fd, SOMAXCONN) != 0) {
        close(fd);
        ps_http_fail("listen failed");
    }
    /* The bound port: the kernel picked one when the caller asked for 0. */
    struct sockaddr_in bound;
    socklen_t bound_len = sizeof bound;
    if (getsockname(fd, (struct sockaddr *)&bound, &bound_len) != 0) {
        close(fd);
        ps_http_fail("getsockname failed");
    }
    PsHttpServer *srv = calloc(1, sizeof(PsHttpServer));
    if (srv == NULL) {
        abort();
    }
    srv->fd = fd;
    srv->port = (int64_t)ntohs(bound.sin_port);
    char ip[INET_ADDRSTRLEN];
    if (inet_ntop(AF_INET, &bound.sin_addr, ip, sizeof ip) != NULL) {
        size_t wrote = strlen(ip);
        if (wrote < sizeof srv->host) {
            memcpy(srv->host, ip, wrote + 1);
        }
    }
    if (pipe(srv->wake) != 0) {
        close(fd);
        ps_http_fail("pipe failed");
    }
    return srv;
}

/* shutdown(): stop the loop. The flag ends the next iteration and the
   self-pipe wakes a blocked accept, so a shutdown from another thread
   lands without waiting for a connection. */
void ps_httpsrv_shutdown(void *box) {
    PsHttpServer *srv = ps_srv_of(box);
    srv->stopped = 1;
    char byte = 0;
    ssize_t wrote = write(srv->wake[1], &byte, 1);
    (void)wrote;
}

/* ----- the request parsing ----- */

/* Read one line off the connection, the CRLF or the LF ending it. The
   answer is the line without its ending and its length. */
static void ps_http_read_line(int fd, char **buf, int64_t *len, int64_t *cap) {
    /* Each call reads one line: the length starts at zero so the blank
       line that ends the header block reads as the empty line it is. */
    *len = 0;
    for (;;) {
        if (*len > 0 && (*buf)[*len - 1] == '\n') {
            /* Strip the ending: the CRLF or the bare LF. */
            int64_t end = *len - 1;
            if (end > 0 && (*buf)[end - 1] == '\r') {
                end--;
            }
            (*buf)[end] = '\0';
            *len = end;
            return;
        }
        if (*len + 1 >= *cap) {
            int64_t grown = *cap * 2;
            char *bigger = realloc(*buf, (size_t)grown);
            if (bigger == NULL) {
                abort();
            }
            *buf = bigger;
            *cap = grown;
        }
        char c;
        ssize_t got = read(fd, &c, 1);
        if (got <= 0) {
            /* The peer closed the connection: an empty line ends the
               read, and the caller sees the short read. */
            (*buf)[*len] = '\0';
            return;
        }
        (*buf)[*len] = c;
        (*len)++;
    }
}

/* Read exactly n bytes off the connection, the way a body of a known
   length is read. */
static void ps_http_read_exact(int fd, char *out, int64_t n) {
    int64_t done = 0;
    while (done < n) {
        ssize_t got = read(fd, out + done, (size_t)(n - done));
        if (got <= 0) {
            if (got < 0 && errno == EINTR) {
                continue;
            }
            break;
        }
        done += (int64_t)got;
    }
}

/* Parse the request line and the header block off the connection.
   The method and the path land in the request record; the headers land
   in the block, one "Name: value\n" row per header. */
static void ps_http_parse_request(int fd, PsHttpReq *req) {
    char *line = malloc(1024);
    if (line == NULL) {
        abort();
    }
    int64_t len = 0;
    int64_t cap = 1024;
    /* The request line: "METHOD SP TARGET SP VERSION". */
    ps_http_read_line(fd, &line, &len, &cap);
    if (len == 0) {
        free(line);
        ps_http_fail("the request line is empty");
    }
    char *sp = strchr(line, ' ');
    if (sp == NULL) {
        free(line);
        ps_http_fail("the request line is malformed");
    }
    *sp = '\0';
    size_t method_len = strlen(line);
    if (method_len == 0 || method_len >= sizeof req->method) {
        free(line);
        ps_http_fail("the request method is not in the subset");
    }
    memcpy(req->method, line, method_len + 1);
    char *target = sp + 1;
    char *sp2 = strchr(target, ' ');
    if (sp2 != NULL) {
        *sp2 = '\0';
    }
    size_t path_len = strlen(target);
    if (path_len >= sizeof req->path) {
        free(line);
        ps_http_fail("the request path is too long");
    }
    memcpy(req->path, target, path_len + 1);
    /* The header block: the rows until the blank line. */
    char *block = NULL;
    int64_t block_len = 0;
    int64_t block_cap = 0;
    for (;;) {
        ps_http_read_line(fd, &line, &len, &cap);
        if (len == 0) {
            break;
        }
        int64_t row_len = len + 1;
        if (block_len + row_len + 1 >= block_cap) {
            int64_t grown = (block_cap == 0 ? 1024 : block_cap * 2);
            char *bigger = realloc(block, (size_t)grown);
            if (bigger == NULL) {
                abort();
            }
            block = bigger;
            block_cap = grown;
        }
        memcpy(block + block_len, line, (size_t)len);
        block[block_len + len] = '\n';
        block_len += row_len;
        block[block_len] = '\0';
    }
    free(line);
    req->headers = block;
    req->headers_len = block_len;
    /* The body: the Content-Length header names its size. */
    int64_t body_len = 0;
    {
        PsBuf value;
        ps_httpreq_header(req, 14, "Content-Length", &value);
        if (value.len > 0) {
            body_len = (int64_t)strtol(value.data, NULL, 10);
            if (body_len < 0) {
                body_len = 0;
            }
        }
    }
    if (body_len > 0) {
        char *body = malloc((size_t)body_len + 1);
        if (body == NULL) {
            abort();
        }
        ps_http_read_exact(fd, body, body_len);
        body[body_len] = '\0';
        req->body = body;
        req->body_len = body_len;
    } else {
        req->body = NULL;
        req->body_len = 0;
    }
}

/* ----- the accept loop ----- */

/* Serve one request: accept a connection, parse the request and hand the
   request record back. The answer is the record, or NULL when the loop
   was asked to stop. The lowering writes the response and closes the
   connection with ps_httpsrv_finish. */
void *ps_httpsrv_serve_one(void *box) {
    PsHttpServer *srv = ps_srv_of(box);
    /* Wait for the listening socket or the wake pipe. */
    struct pollfd fds[2];
    fds[0].fd = srv->fd;
    fds[0].events = POLLIN;
    fds[1].fd = srv->wake[0];
    fds[1].events = POLLIN;
    for (;;) {
        if (srv->stopped) {
            return NULL;
        }
        int ready = poll(fds, 2, -1);
        if (ready < 0) {
            if (errno == EINTR) {
                continue;
            }
            ps_http_fail("poll failed");
        }
        if (fds[1].revents & POLLIN) {
            /* The wake byte: drain it and read the stop flag. */
            char byte;
            ssize_t got = read(srv->wake[0], &byte, 1);
            (void)got;
            continue;
        }
        if (fds[0].revents & POLLIN) {
            break;
        }
    }
    if (srv->stopped) {
        return NULL;
    }
    int conn = accept(srv->fd, NULL, NULL);
    if (conn < 0) {
        ps_http_fail("accept failed");
    }
    PsHttpReq *req = calloc(1, sizeof(PsHttpReq));
    if (req == NULL) {
        abort();
    }
    req->fd = conn;
    ps_http_parse_request(conn, req);
    return req;
}

/* Finish one request: write the response record the handler built onto the
   connection and close both records, the way CPython's Connection: close
   serves each request. The record's lifetime is the request's: the
   lowering builds a fresh one before every dispatch (ps_httpresp_new) and
   this call frees it. */
void ps_httpsrv_finish(void *box, void *resp_box) {
    PsHttpReq *req;
    ps_req_of(box, &req);
    PsHttpResp *resp;
    ps_resp_of(resp_box, &resp);
    int fd = req->fd;
    /* The status line. CPython's default when the handler never sent one
       is the 501 for an unsupported method; the dispatch keeps the status
       at 0 for exactly that shape and the reason phrase lands as such. */
    if (resp->status <= 0) {
        resp->status = 501;
    }
    char head[128];
    int wrote = snprintf(head, sizeof head, "HTTP/1.0 %ld %s\r\n", (long)resp->status,
                         ps_http_reason(resp->status));
    /* The Date header, CPython's BaseHTTPRequestHandler writes one. */
    char stamp[64];
    int stamp_len = 0;
    {
        time_t now = time(NULL);
        struct tm tm;
        gmtime_r(&now, &tm);
        stamp_len = strftime(stamp, sizeof stamp, "Date: %a, %d %b %Y %H:%M:%S GMT\r\n", &tm);
    }
    /* The Server header. */
    const char *server = "Server: BaseHTTP/1.0\r\n";
    size_t server_len = strlen(server);
    /* The header block the handler wrote. CPython's end_headers is what
       closes the block, so an absent block means end_headers never ran and
       the blank line closes it here, the way a missing end_headers still
       yields a parseable response. */
    const char *block = resp->headers_len > 0 ? resp->headers : "\r\n";
    size_t block_len = resp->headers_len > 0 ? (size_t)resp->headers_len : 2;
    /* The whole response goes out in one write: CPython's wfile is a
       buffered writer, so the peer reads the response as one segment and
       not as the four pieces the parts would be. */
    size_t total = (wrote > 0 ? (size_t)wrote : 0) + (stamp_len > 0 ? (size_t)stamp_len : 0) + server_len + block_len +
                   (resp->body_len > 0 ? (size_t)resp->body_len : 0);
    char *out = malloc(total > 0 ? total : 1);
    if (out == NULL) {
        abort();
    }
    size_t at = 0;
    if (wrote > 0) {
        memcpy(out + at, head, (size_t)wrote);
        at += (size_t)wrote;
    }
    if (stamp_len > 0) {
        memcpy(out + at, stamp, (size_t)stamp_len);
        at += (size_t)stamp_len;
    }
    memcpy(out + at, server, server_len);
    at += server_len;
    memcpy(out + at, block, block_len);
    at += block_len;
    if (resp->body_len > 0) {
        memcpy(out + at, resp->body, (size_t)resp->body_len);
        at += (size_t)resp->body_len;
    }
    size_t done = 0;
    while (done < at) {
        ssize_t put = write(fd, out + done, at - done);
        if (put <= 0) {
            if (put < 0 && errno == EINTR) {
                continue;
            }
            break;
        }
        done += (size_t)put;
    }
    free(out);
    close(fd);
    free(req->headers);
    free(req->body);
    free(req);
    free(resp->headers);
    free(resp->body);
    free(resp);
}

void ps_httpsrv_close(void *box) {
    PsHttpServer *srv = ps_srv_of(box);
    if (srv->fd >= 0) {
        close(srv->fd);
        srv->fd = -1;
    }
    close(srv->wake[0]);
    close(srv->wake[1]);
}

#else /* __wasi__ || _WIN32 */

/* No sockets on a target without them: the records exist so the link
   stays whole, and every call raises the runtime error a WASI program
   would hit at the first socket call anyway. */

void *ps_httpsrv_new(int64_t n, const char *host, int64_t port) {
    (void)n;
    (void)host;
    (void)port;
    ps_raise_cstr(PS_RUNTIME, "http.server needs the BSD socket API, which this target has no story for");
    return NULL;
}

void ps_httpsrv_shutdown(void *box) {
    (void)box;
    ps_raise_cstr(PS_RUNTIME, "http.server needs the BSD socket API, which this target has no story for");
}

void ps_httpsrv_host(void *box, PsBuf *out) {
    (void)box;
    (void)out;
    ps_raise_cstr(PS_RUNTIME, "http.server needs the BSD socket API, which this target has no story for");
}

int64_t ps_httpsrv_port(void *box) {
    (void)box;
    ps_raise_cstr(PS_RUNTIME, "http.server needs the BSD socket API, which this target has no story for");
    return 0;
}

void *ps_httpsrv_serve_one(void *box) {
    (void)box;
    ps_raise_cstr(PS_RUNTIME, "http.server needs the BSD socket API, which this target has no story for");
    return NULL;
}

void ps_httpsrv_finish(void *box, void *resp_box) {
    (void)box;
    (void)resp_box;
    ps_raise_cstr(PS_RUNTIME, "http.server needs the BSD socket API, which this target has no story for");
}

void ps_httpsrv_close(void *box) {
    (void)box;
}

int64_t ps_httpsrv_stopped(void *box) {
    (void)box;
    ps_raise_cstr(PS_RUNTIME, "http.server needs the BSD socket API, which this target has no story for");
    return 0;
}

void *ps_httpresp_new(void) {
    ps_raise_cstr(PS_RUNTIME, "http.server needs the BSD socket API, which this target has no story for");
    return NULL;
}

void ps_httpresp_send_response(void *box, int64_t status) {
    (void)box;
    (void)status;
    ps_raise_cstr(PS_RUNTIME, "http.server needs the BSD socket API, which this target has no story for");
}

void ps_httpresp_send_header(void *box, int64_t n, const char *name, int64_t vlen, const char *value) {
    (void)box;
    (void)n;
    (void)name;
    (void)vlen;
    (void)value;
    ps_raise_cstr(PS_RUNTIME, "http.server needs the BSD socket API, which this target has no story for");
}

void ps_httpresp_end_headers(void *box) {
    (void)box;
    ps_raise_cstr(PS_RUNTIME, "http.server needs the BSD socket API, which this target has no story for");
}

void ps_httpresp_wfile_write(void *box, int64_t n, const char *data) {
    (void)box;
    (void)n;
    (void)data;
    ps_raise_cstr(PS_RUNTIME, "http.server needs the BSD socket API, which this target has no story for");
}

int64_t ps_httpresp_status(void *box) {
    (void)box;
    ps_raise_cstr(PS_RUNTIME, "http.server needs the BSD socket API, which this target has no story for");
    return 0;
}

void ps_httpreq_method(void *box, PsBuf *out) {
    (void)box;
    (void)out;
    ps_raise_cstr(PS_RUNTIME, "http.server needs the BSD socket API, which this target has no story for");
}

void ps_httpreq_path(void *box, PsBuf *out) {
    (void)box;
    (void)out;
    ps_raise_cstr(PS_RUNTIME, "http.server needs the BSD socket API, which this target has no story for");
}

void ps_httpreq_body(void *box, PsBuf *out) {
    (void)box;
    (void)out;
    ps_raise_cstr(PS_RUNTIME, "http.server needs the BSD socket API, which this target has no story for");
}

void ps_httpreq_header(void *box, int64_t n, const char *name, PsBuf *out) {
    (void)box;
    (void)n;
    (void)name;
    (void)out;
    ps_raise_cstr(PS_RUNTIME, "http.server needs the BSD socket API, which this target has no story for");
}

void *ps_httpreq_headers(void *box) {
    (void)box;
    ps_raise_cstr(PS_RUNTIME, "http.server needs the BSD socket API, which this target has no story for");
    return NULL;
}

void *ps_httpreq_rfile(void *box) {
    (void)box;
    ps_raise_cstr(PS_RUNTIME, "http.server needs the BSD socket API, which this target has no story for");
    return NULL;
}

void *ps_httpresp_wfile(void *box) {
    (void)box;
    ps_raise_cstr(PS_RUNTIME, "http.server needs the BSD socket API, which this target has no story for");
    return NULL;
}

void ps_httpreq_rfile_read(void *box, int64_t n, PsBuf *out) {
    (void)box;
    (void)n;
    (void)out;
    ps_raise_cstr(PS_RUNTIME, "http.server needs the BSD socket API, which this target has no story for");
}

#endif /* __wasi__ || _WIN32 */
