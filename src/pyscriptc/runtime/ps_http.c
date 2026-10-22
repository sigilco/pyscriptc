/* urllib.request native subset: urlopen over blocking BSD sockets.

The closed surface (issues #60, #225, #226): http:// GET, HEAD and the other
methods through `Request`, bytes bodies, request headers, chunked
transfer-encoding decoding, error statuses (4xx/5xx raise HTTPError with a
readable body), timeouts (TimeoutError), and redirects (301, 302, 303, 307,
308, bounded at 10 hops like CPython's HTTPRedirectHandler).

Plaintext http:// only: an https:// target raises URLError (OSError) until
TLS lands (#235). The response is the HttpResponse record: status, the
header block as one "Name: value\n" buffer, and the decoded body. Blocking
sockets only; `Connection: close` and read to EOF, except a chunked body
ends at the 0-chunk. No keep-alive, no proxies, no cookies.

The redirect Location is resolved against the request URL with
urllib.parse.urljoin semantics (absolute, scheme-relative, absolute path,
and relative path with . and .. segments).
*/

#include "ps_rt.h"

#include <arpa/inet.h>
#include <errno.h>
#include <netdb.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

/* The HttpResponse record: the layout the HttpResponse class in
   stdmore.CLASS_FIELDS uses. Field order is status, headers, body; a PsBuf
   is two words, so this matches the emitted class layout. */
typedef struct {
    int64_t status;
    PsBuf headers;
    PsBuf body;
} PsHttpResp;

/* The HttpRequest record: the layout the HttpRequest class in
   stdmore.CLASS_FIELDS uses. Field order is url, method, body, timeout. */
typedef struct {
    PsBuf url;
    PsBuf method;
    PsBuf body;
    PsBuf headers;
    double timeout;
} PsHttpReq;

#define PS_HTTP_MAX_REDIRECTS 10

static void ps_http_fail(const char *what) { ps_raise_cstr(PS_OSERROR, what); }

static char *ps_http_dup(int64_t n, const char *s) {
    char *text;
    if (n < 0) {
        ps_raise_code(PS_VALUE);
    }
    text = malloc((size_t)n + 1);
    if (text == NULL) {
        abort();
    }
    if (n > 0) {
        memcpy(text, s, (size_t)n);
    }
    text[n] = '\0';
    return text;
}

/* Parsed http URL: host, port, and the path-plus-query to send. The host
   and path borrow from the (mutable) working copy of the URL text. */
typedef struct {
    const char *host;
    int port;
    const char *path;
} PsHttpUrl;

/* The URL text is not modified: the redirect loop needs the whole URL to
   resolve a relative Location against it. The host and the path are copies. */
static void ps_http_split(const char *url, PsHttpUrl *out) {
    size_t n = strlen(url);
    const char *cursor = url;
    out->port = 80;
    if (n >= 7 && strncmp(cursor, "http://", 7) == 0) {
        cursor += 7;
    } else if (n >= 8 && strncmp(cursor, "https://", 8) == 0) {
        /* Clear message until TLS lands (#235). */
        ps_http_fail("https is not supported yet (TLS is planned, issue 235)");
    } else {
        ps_http_fail("urlopen supports http:// URLs");
    }
    const char *host_end = cursor;
    while (*host_end != '\0' && *host_end != '/' && *host_end != ':' && *host_end != '?') {
        host_end++;
    }
    if (host_end == cursor) {
        ps_http_fail("URL has no host");
    }
    /* The host ends at the colon (the port follows) or at the path. */
    const char *path_start = host_end;
    if (*host_end == ':') {
        long port = 0;
        const char *digits = host_end + 1;
        if (*digits < '0' || *digits > '9') {
            ps_http_fail("URL port is not a number");
        }
        while (*digits >= '0' && *digits <= '9') {
            port = port * 10 + (*digits - '0');
            digits++;
        }
        if (port <= 0 || port > 65535) {
            ps_http_fail("URL port is out of range");
        }
        out->port = (int)port;
        path_start = digits;
    }
    out->host = ps_http_dup((int64_t)(host_end - cursor), cursor);
    if (*path_start != '\0') {
        out->path = ps_http_dup((int64_t)strlen(path_start), path_start);
    } else {
        out->path = ps_http_dup(1, "/");
    }
}

/* The deadline every read and write shares: one timeout for the whole
   request, not per syscall. */
typedef struct {
    struct timeval at;
    int on;
} PsHttpDeadline;

static void ps_http_deadline_start(PsHttpDeadline *dl, double timeout) {
    if (timeout > 0) {
        gettimeofday(&dl->at, NULL);
        dl->at.tv_sec += (time_t)timeout;
        double frac = timeout - (double)(time_t)timeout;
        dl->at.tv_usec += (suseconds_t)(frac * 1e6);
        if (dl->at.tv_usec >= 1000000) {
            dl->at.tv_sec += 1;
            dl->at.tv_usec -= 1000000;
        }
        dl->on = 1;
        return;
    }
    dl->on = 0;
}

/* The remaining time, or the fallback when there is no deadline. */
static void ps_http_left(const PsHttpDeadline *dl, struct timeval *left) {
    if (!dl->on) {
        left->tv_sec = 60;
        left->tv_usec = 0;
        return;
    }
    struct timeval now;
    gettimeofday(&now, NULL);
    left->tv_sec = dl->at.tv_sec - now.tv_sec;
    left->tv_usec = dl->at.tv_usec - now.tv_usec;
    if (left->tv_sec < 0 || (left->tv_sec == 0 && left->tv_usec <= 0)) {
        ps_http_fail("timed out");
    }
}

static void ps_http_wait_read(int fd, const PsHttpDeadline *dl) {
    for (;;) {
        fd_set rf;
        struct timeval left;
        ps_http_left(dl, &left);
        FD_ZERO(&rf);
        FD_SET(fd, &rf);
        int n = select(fd + 1, &rf, NULL, NULL, &left);
        if (n > 0) {
            return;
        }
        if (n < 0 && errno == EINTR) {
            continue;
        }
        ps_http_fail("timed out");
    }
}

/* Read up to `want` bytes into the grow buffer, at most `limit` in total.
   `limit` of -1 reads until EOF. Returns the bytes read. */
static int64_t ps_http_read_into(int fd, PsGrow *g, int64_t limit, const PsHttpDeadline *dl) {
    char chunk[8192];
    int64_t got = 0;
    for (;;) {
        if (limit >= 0 && got >= limit) {
            return got;
        }
        ps_http_wait_read(fd, dl);
        size_t want = sizeof chunk;
        if (limit >= 0 && limit - got < (int64_t)want) {
            want = (size_t)(limit - got);
        }
        ssize_t n = read(fd, chunk, want);
        if (n == 0) {
            return got;
        }
        if (n < 0) {
            if (errno == EINTR) {
                continue;
            }
            ps_http_fail("socket read failed");
        }
        ps_grow_add(g, chunk, (int64_t)n);
        got += n;
    }
}

/* Read one CRLF-terminated line (without the CRLF). */
static void ps_http_read_line(int fd, PsGrow *g, const PsHttpDeadline *dl) {
    char ch;
    for (;;) {
        ps_http_wait_read(fd, dl);
        ssize_t n = read(fd, &ch, 1);
        if (n == 0) {
            ps_http_fail("connection closed mid-response");
        }
        if (n < 0) {
            if (errno == EINTR) {
                continue;
            }
            ps_http_fail("socket read failed");
        }
        if (ch == '\n') {
            return;
        }
        if (ch != '\r') {
            ps_grow_add(g, &ch, 1);
        }
    }
}

static int64_t ps_http_status_of(const char *line) {
    /* "HTTP/1.1 200 OK" */
    if (strncmp(line, "HTTP/", 5) != 0) {
        ps_http_fail("malformed status line");
    }
    const char *space = strchr(line, ' ');
    if (space == NULL) {
        ps_http_fail("malformed status line");
    }
    space++;
    if (space[0] < '0' || space[0] > '9' || space[1] < '0' || space[1] > '9' || space[2] < '0' || space[2] > '9') {
        ps_http_fail("malformed status line");
    }
    return (space[0] - '0') * 100 + (space[1] - '0') * 10 + (space[2] - '0');
}

static int ps_http_ci_eq(const char *a, const char *b) {
    while (*a != '\0' && *b != '\0') {
        char ca = *a >= 'A' && *a <= 'Z' ? (char)(*a + 32) : *a;
        char cb = *b >= 'A' && *b <= 'Z' ? (char)(*b + 32) : *b;
        if (ca != cb) {
            return 0;
        }
        a++;
        b++;
    }
    return *a == *b;
}

/* A header name (lowercased) compared case-insensitively against a literal. */
static int ps_http_is_header(const char *name, const char *want) { return ps_http_ci_eq(name, want); }

/* Connect to host:port. The timeout applies to the connect through the
   socket's own send and receive timeouts. */
static int ps_http_connect(const char *host, int port, double timeout) {
    char port_text[8];
    struct addrinfo hints;
    struct addrinfo *res = NULL;
    snprintf(port_text, sizeof port_text, "%d", port);
    memset(&hints, 0, sizeof hints);
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    int gai = getaddrinfo(host, port_text, &hints, &res);
    if (gai != 0 || res == NULL) {
        ps_http_fail(gai == 0 ? "no address for host" : gai_strerror(gai));
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
        ps_http_fail("connection refused");
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

/* Write the whole request buffer. */
static void ps_http_write_all(int fd, const char *data, int64_t len, const PsHttpDeadline *dl) {
    int64_t done = 0;
    while (done < len) {
        fd_set wf;
        struct timeval left;
        ps_http_left(dl, &left);
        FD_ZERO(&wf);
        FD_SET(fd, &wf);
        int n = select(fd + 1, NULL, &wf, NULL, &left);
        if (n < 0 && errno == EINTR) {
            continue;
        }
        if (n <= 0) {
            ps_http_fail("timed out");
        }
        ssize_t wrote = write(fd, data + done, (size_t)(len - done));
        if (wrote <= 0) {
            if (wrote < 0 && errno == EINTR) {
                continue;
            }
            ps_http_fail("socket write failed");
        }
        done += wrote;
    }
}

/* One request and response round trip. `headers` is the caller's extra
   header block (already CRLF-terminated lines). Fills the record and, when
   the status is a redirect, hands back the Location value (owned). */
static void ps_http_roundtrip(
    const PsHttpUrl *url,
    const char *method,
    const char *extra_headers,
    const char *body,
    int64_t body_len,
    const PsHttpDeadline *dl,
    PsHttpResp *out,
    char **location
) {
    int fd = ps_http_connect(url->host, url->port, dl->on ? (double)dl->at.tv_sec : 0.0);
    PsGrow req;
    ps_grow_init(&req);
    char head[512];
    int n = snprintf(head, sizeof head, "%s %s HTTP/1.1\r\n", method, url->path);
    ps_grow_add(&req, head, (int64_t)n);
    n = snprintf(head, sizeof head, "Host: %s\r\n", url->host);
    ps_grow_add(&req, head, (int64_t)n);
    if (body_len > 0) {
        n = snprintf(head, sizeof head, "Content-Length: %lld\r\n", (long long)body_len);
        ps_grow_add(&req, head, (int64_t)n);
    }
    n = snprintf(head, sizeof head, "Connection: close\r\n");
    ps_grow_add(&req, head, (int64_t)n);
    if (extra_headers != NULL && extra_headers[0] != '\0') {
        ps_grow_add(&req, extra_headers, (int64_t)strlen(extra_headers));
    }
    ps_grow_add(&req, "\r\n", 2);
    if (body_len > 0) {
        ps_grow_add(&req, body, body_len);
    }
    PsBuf wire;
    ps_grow_finish(&req, &wire);
    ps_http_write_all(fd, wire.data, wire.len, dl);
    free(wire.data);

    /* Status line. */
    PsGrow line;
    ps_grow_init(&line);
    ps_http_read_line(fd, &line, dl);
    PsBuf status_line;
    ps_grow_finish(&line, &status_line);
    int64_t status = ps_http_status_of(status_line.data);
    free(status_line.data);

    /* Headers. */
    PsGrow block;
    ps_grow_init(&block);
    char *loc = NULL;
    int64_t clen = -1;
    int chunked = 0;
    for (;;) {
        PsGrow hl;
        ps_grow_init(&hl);
        ps_http_read_line(fd, &hl, dl);
        int64_t row_len = hl.len;
        PsBuf row_buf;
        ps_grow_finish(&hl, &row_buf);
        if (row_len == 0) {
            free(row_buf.data);
            break;
        }
        char *row = row_buf.data;
        char *colon = strchr(row, ':');
        if (colon != NULL) {
            /* The name ends at the colon: terminate it so the comparison
               sees the name alone. */
            *colon = '\0';
            char *name = row;
            char *value = colon + 1;
            while (*value == ' ' || *value == '\t') {
                value++;
            }
            if (ps_http_is_header(name, "location")) {
                free(loc);
                loc = ps_http_dup((int64_t)strlen(value), value);
            }
            if (ps_http_is_header(name, "content-length")) {
                clen = 0;
                const char *digits = value;
                while (*digits >= '0' && *digits <= '9') {
                    clen = clen * 10 + (*digits - '0');
                    digits++;
                }
            }
            if (ps_http_is_header(name, "transfer-encoding") && strstr(value, "chunked") != NULL) {
                chunked = 1;
            }
        }
        ps_grow_add(&block, row, row_len);
        ps_grow_add(&block, "\n", 1);
        free(row);
    }
    ps_grow_finish(&block, &out->headers);

    /* Body. */
    PsGrow bodyg;
    ps_grow_init(&bodyg);
    if (chunked) {
        for (;;) {
            PsGrow szl;
            ps_grow_init(&szl);
            ps_http_read_line(fd, &szl, dl);
            PsBuf size_buf;
            ps_grow_finish(&szl, &size_buf);
            int64_t size = strtoll(size_buf.data, NULL, 16);
            free(size_buf.data);
            if (size <= 0) {
                /* Trailers run to the blank line. */
                for (;;) {
                    PsGrow tl;
                    ps_grow_init(&tl);
                    ps_http_read_line(fd, &tl, dl);
                    int64_t tl_len = tl.len;
                    ps_grow_finish(&tl, &size_buf);
                    free(size_buf.data);
                    if (tl_len == 0) {
                        break;
                    }
                }
                break;
            }
            ps_http_read_into(fd, &bodyg, size, dl);
            /* The CRLF that ends the chunk. */
            PsGrow crlf;
            ps_grow_init(&crlf);
            ps_http_read_line(fd, &crlf, dl);
            ps_grow_finish(&crlf, &size_buf);
            free(size_buf.data);
        }
    } else if (clen >= 0) {
        ps_http_read_into(fd, &bodyg, clen, dl);
    } else {
        ps_http_read_into(fd, &bodyg, -1, dl);
    }
    ps_grow_finish(&bodyg, &out->body);
    close(fd);
    *location = loc;
    out->status = status;
}

/* Resolve a Location against the request URL with urllib.parse.urljoin
   semantics: an absolute URL replaces the request, a scheme-relative URL
   keeps the scheme, an absolute path keeps the origin, and a relative path
   merges with the base path's directory with . and .. resolved. The result
   is owned. */
static char *ps_http_resolve(const char *base, const char *loc) {
    if (strstr(loc, "://") != NULL) {
        return ps_http_dup((int64_t)strlen(loc), loc);
    }
    const char *scheme_end = strstr(base, "://");
    if (scheme_end == NULL) {
        return ps_http_dup((int64_t)strlen(loc), loc);
    }
    if (strncmp(loc, "//", 2) == 0) {
        size_t scheme_len = (size_t)(scheme_end - base) + 1;
        size_t loc_len = strlen(loc);
        char *out = malloc(scheme_len + loc_len + 1);
        if (out == NULL) {
            abort();
        }
        memcpy(out, base, scheme_len);
        memcpy(out + scheme_len, loc, loc_len + 1);
        return out;
    }
    const char *slash = strchr(scheme_end + 3, '/');
    if (loc[0] == '/') {
        size_t keep = (size_t)(slash - base);
        size_t loc_len = strlen(loc);
        char *out = malloc(keep + loc_len + 1);
        if (out == NULL) {
            abort();
        }
        memcpy(out, base, keep);
        memcpy(out + keep, loc, loc_len + 1);
        return out;
    }
    /* Relative path: the base directory plus the location, with . and ..
       resolved over the path part. */
    size_t origin_len = (size_t)(slash - base);
    size_t dir_len = (size_t)(slash - base);
    const char *query = strchr(slash, '?');
    if (query != NULL) {
        dir_len = (size_t)(query - base);
    }
    const char *last_slash = NULL;
    for (const char *p = slash; p < base + dir_len; p++) {
        if (*p == '/') {
            last_slash = p;
        }
    }
    size_t keep_len = last_slash != NULL ? (size_t)(last_slash - base) + 1 : origin_len;
    size_t loc_len = strlen(loc);
    char *merged = malloc(keep_len + loc_len + 1);
    if (merged == NULL) {
        abort();
    }
    memcpy(merged, base, keep_len);
    memcpy(merged + keep_len, loc, loc_len + 1);

    /* Resolve . and .. over the path part of the merged URL. */
    char *path = merged + origin_len;
    char *segments[256];
    int nseg = 0;
    char *save = NULL;
    char *token = strtok_r(path, "/", &save);
    while (token != NULL) {
        if (strcmp(token, ".") == 0) {
            /* skip */
        } else if (strcmp(token, "..") == 0) {
            if (nseg > 0) {
                nseg--;
            }
        } else if (nseg < 256) {
            segments[nseg++] = token;
        }
        token = strtok_r(NULL, "/", &save);
    }
    char *out = malloc(origin_len + strlen(path) + 2);
    if (out == NULL) {
        abort();
    }
    memcpy(out, merged, origin_len);
    char *w = out + origin_len;
    for (int i = 0; i < nseg; i++) {
        *w++ = '/';
        size_t len = strlen(segments[i]);
        memcpy(w, segments[i], len);
        w += len;
    }
    *w = '\0';
    free(merged);
    return out;
}

/* Method and body rules for redirects, matching urllib.request: 303 always
   switches to GET without a body, 301 and 302 rewrite a POST to a GET, and
   307 and 308 keep the method and the body. */
static void ps_http_redirect_method(int status, const char *method, const char **out_method, int *keep_body) {
    if (status == 303 || ((status == 301 || status == 302) && strcmp(method, "POST") == 0)) {
        *out_method = "GET";
        *keep_body = 0;
        return;
    }
    *out_method = method;
    *keep_body = 1;
}

PsHttpResp *ps_http_request(
    int64_t url_len,
    const char *url_ptr,
    int64_t method_len,
    const char *method_ptr,
    int64_t body_len,
    const char *body_ptr,
    int64_t headers_len,
    const char *headers_ptr,
    double timeout
) {
    PsHttpResp *out = ps_alloc((int64_t)sizeof(PsHttpResp));
    char *url = ps_http_dup(url_len, url_ptr);
    char *method = ps_http_dup(method_len, method_ptr);
    char *headers = headers_len > 0 ? ps_http_dup(headers_len, headers_ptr) : NULL;
    char *body = body_len > 0 ? ps_http_dup(body_len, body_ptr) : NULL;
    PsHttpDeadline dl;
    ps_http_deadline_start(&dl, timeout);

    int status = 0;
    char *loc = NULL;
    for (int hop = 0; hop <= PS_HTTP_MAX_REDIRECTS; hop++) {
        PsHttpUrl parsed;
        ps_http_split(url, &parsed);
        free(loc);
        loc = NULL;
        ps_http_roundtrip(&parsed, method, headers, body, body_len, &dl, out, &loc);
        status = (int)out->status;
        if (status != 301 && status != 302 && status != 303 && status != 307 && status != 308) {
            break;
        }
        if (loc == NULL) {
            /* A redirect status without a Location is an error response. */
            break;
        }
        if (hop == PS_HTTP_MAX_REDIRECTS) {
            ps_http_fail("HTTP Error 310: Exceeded redirect limit");
        }
        char *next = ps_http_resolve(url, loc);
        free(url);
        url = next;
        const char *next_method;
        int keep_body;
        ps_http_redirect_method(status, method, &next_method, &keep_body);
        if (strcmp(method, next_method) != 0) {
            free(method);
            method = ps_http_dup((int64_t)strlen(next_method), next_method);
        }
        if (!keep_body) {
            free(body);
            body = NULL;
            body_len = 0;
        }
    }
    free(loc);
    free(url);
    free(method);
    free(headers);
    free(body);

    if (status >= 400) {
        /* HTTPError semantics: the class the module registered (when the
           script imported urllib.error) matches `except HTTPError` through
           the OSError chain; a script without the import still catches the
           same raise as OSError. The response record keeps the body, so it
           stays readable like CPython's HTTPError fp. */
        int32_t code = ps_exc_code_by_name("HTTPError");
        if (code == 0) {
            code = PS_OSERROR;
        }
        char msg[128];
        snprintf(msg, sizeof msg, "HTTP Error %d", status);
        ps_raise_cstr(code, msg);
    }
    return out;
}

/* urlopen(url): GET. */
PsHttpResp *ps_http_open(int64_t url_len, const char *url_ptr) {
    return ps_http_request(url_len, url_ptr, 3, "GET", 0, NULL, 0, NULL, 0.0);
}

/* Request(url, method=..., data=..., timeout=...): the HttpRequest record.
   The headers stay on the compiler side (a dict display rendered at the
   call); the record carries what the wire needs. */
PsHttpReq *ps_http_request_new(
    int64_t url_len,
    const char *url_ptr,
    int64_t method_len,
    const char *method_ptr,
    int64_t body_len,
    const char *body_ptr,
    int64_t headers_len,
    const char *headers_ptr,
    double timeout
) {
    PsHttpReq *out = ps_alloc((int64_t)sizeof(PsHttpReq));
    out->url.len = url_len;
    out->url.data = ps_http_dup(url_len, url_ptr);
    out->method.len = method_len;
    out->method.data = ps_http_dup(method_len, method_ptr);
    out->body.len = body_len;
    out->body.data = body_len > 0 ? ps_http_dup(body_len, body_ptr) : (char *)&ps_empty_byte;
    out->headers.len = headers_len;
    out->headers.data = headers_len > 0 ? ps_http_dup(headers_len, headers_ptr) : (char *)&ps_empty_byte;
    out->timeout = timeout;
    return out;
}

/* The response shims: getcode() reads the status, info() reads the header
   block, read() reads the body. They take the record pointer. */
int64_t ps_http_getcode(PsHttpResp *resp) { return resp->status; }

void ps_http_info(PsHttpResp *resp, PsBuf *out) { ps_buf_copy(out, resp->headers.data, resp->headers.len); }

void ps_http_read(PsHttpResp *resp, PsBuf *out) { ps_buf_copy(out, resp->body.data, resp->body.len); }
