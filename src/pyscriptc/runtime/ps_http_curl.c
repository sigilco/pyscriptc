/* The https:// hop over dlopen'ed libcurl (issue #235, owner decision: libcurl
first). Nothing here links libcurl at build time: the first https:// request
loads libcurl.so.4 (libcurl.4.dylib, libcurl.dll) through dlopen and resolves
the seven symbols the easy API path needs. A binary built on a machine without
libcurl keeps working for http:// and raises URLError (OSError) for https://
with a hint that installing libcurl enables it.

libcurl is an HTTP client, not a TLS stream, so it does not implement the
PsHttpTransport contract of ps_tls.h: it performs the whole request (URL,
method, headers, body, timeouts) itself, one call per hop. The redirect loop
above stays in charge of the method and body rules (303 to GET, 301/302
rewrite a POST, 307/308 keep both) and the hop budget; CURLOPT_FOLLOWLOCATION
stays off, so libcurl never follows a redirect on its own here.

Verification is always on. CURLOPT_SSL_VERIFYPEER and CURLOPT_SSL_VERIFYHOST
are set to their defaults explicitly (1 and 2) so the wire never travels
unverified against the platform trust store; nothing in the surface can turn
them off, not even in a test.

PSC_LIBCURL_PATH overrides the library name for the test suite only: the shim
in tests/fixtures/http_curl_shim exports the same symbols and records the
options it saw. The override is honoured only when the value holds an absolute
path, so a name like "libcurl.so.4" from an untrusted environment cannot
redirect a normal load away from the system libcurl.
*/

#include "ps_rt.h"

#if !defined(__wasi__)

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#else
#include <dlfcn.h>
#endif
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

/* The wire shape of the easy API, mirrored from curl/curl.h. The runtime does
   not ship the curl headers: the numbers are the ABI and they are frozen
   across every libcurl 7.x/8.x this has to load. */
typedef void CURL;
typedef int CURLcode;
typedef int CURLoption;
typedef int CURLinfo;
struct curl_slist {
    char *data;
    struct curl_slist *next;
};

/* CURLOPT_* values: LONG = n, OBJECTPOINT/STRINGPOINT/SLISTPOINT/CBPOINT =
   n + 10000, FUNCTIONPOINT = n + 20000, OFF_T = n + 30000. */
#define PS_CURL_OPT_WRITEDATA 10001
#define PS_CURL_OPT_URL 10002
#define PS_CURL_OPT_WRITEFUNCTION 20011
#define PS_CURL_OPT_POSTFIELDS 10015
#define PS_CURL_OPT_HTTPHEADER 10023
#define PS_CURL_OPT_HEADERDATA 10029
#define PS_CURL_OPT_CUSTOMREQUEST 10036
#define PS_CURL_OPT_POST 47
#define PS_CURL_OPT_NOBODY 44
#define PS_CURL_OPT_FOLLOWLOCATION 52
#define PS_CURL_OPT_POSTFIELDSIZE_LARGE 30120
#define PS_CURL_OPT_SSL_VERIFYPEER 64
#define PS_CURL_OPT_HEADERFUNCTION 20079
#define PS_CURL_OPT_SSL_VERIFYHOST 81
#define PS_CURL_OPT_TIMEOUT_MS 155

/* CURLINFO_RESPONSE_CODE = CURLINFO_LONG (0x200000) + 2. */
#define PS_CURL_INFO_RESPONSE_CODE (0x200000L + 2L)

#define PS_CURL_OK 0
/* The CURLE codes this file names in the error text the plaintext client
   raises for the same failure. */
#define PS_CURL_COULDNT_RESOLVE_HOST 6
#define PS_CURL_COULDNT_CONNECT 7
#define PS_CURL_OPERATION_TIMEDOUT 28
#define PS_CURL_TOO_MANY_REDIRECTS 47
#define PS_CURL_PEER_FAILED_VERIFICATION 60

typedef CURL *(*ps_curl_easy_init_fn)(void);
typedef CURLcode (*ps_curl_easy_setopt_fn)(CURL *, CURLoption, ...);
typedef CURLcode (*ps_curl_easy_perform_fn)(CURL *);
typedef CURLcode (*ps_curl_easy_getinfo_fn)(CURL *, CURLinfo, ...);
typedef void (*ps_curl_easy_cleanup_fn)(CURL *);
typedef struct curl_slist *(*ps_curl_slist_append_fn)(struct curl_slist *, const char *);
typedef void (*ps_curl_slist_free_all_fn)(struct curl_slist *);

#ifdef _WIN32
static HMODULE ps_curl_lib;
#else
static void *ps_curl_lib;
#endif
static ps_curl_easy_init_fn ps_curl_easy_init;
static ps_curl_easy_setopt_fn ps_curl_easy_setopt;
static ps_curl_easy_perform_fn ps_curl_easy_perform;
static ps_curl_easy_getinfo_fn ps_curl_easy_getinfo;
static ps_curl_easy_cleanup_fn ps_curl_easy_cleanup;
static ps_curl_slist_append_fn ps_curl_slist_append;
static ps_curl_slist_free_all_fn ps_curl_slist_free_all;

/* Loaded once. A failed load is remembered: an https:// URL keeps raising the
   not-supported error instead of retrying the load per request. */
static int ps_curl_tried;
static int ps_curl_ok;

/* The response record, mirroring ps_http.c: the layout the HttpResponse
   class in stdmore.CLASS_FIELDS uses (status, headers, body). */
typedef struct {
    int64_t status;
    PsBuf headers;
    PsBuf body;
} PsCurlResp;

char *ps_http_curl_dup(int64_t n, const char *s);

#ifdef _WIN32
static void *ps_curl_open_lib(const char *name) { return (void *)LoadLibraryA(name); }
static void *ps_curl_lookup(const char *name) {
    return (void *)GetProcAddress(ps_curl_lib, name);
}
#else
static void *ps_curl_open_lib(const char *name) { return dlopen(name, RTLD_NOW | RTLD_LOCAL); }
static void *ps_curl_lookup(const char *name) { return dlsym(ps_curl_lib, name); }
#endif

/* The bytes the body callback receives. */
typedef struct {
    PsGrow g;
} PsCurlSink;

static size_t ps_curl_body_cb(char *ptr, size_t size, size_t nitems, void *userdata) {
    PsCurlSink *sink = (PsCurlSink *)userdata;
    size_t total = size * nitems;
    if (total > 0) {
        ps_grow_add(&sink->g, ptr, (int64_t)total);
    }
    return total;
}

/* The header callback sees every header line with its CRLF, and one block per
   interim (1xx) response too. Only the rows after the last status line land
   in the block: the sink restarts whenever a line carries an HTTP/ prefix,
   which is what a redirect hop or a 100-continue sends. */
static size_t ps_curl_header_cb(char *ptr, size_t size, size_t nitems, void *userdata) {
    PsCurlSink *sink = (PsCurlSink *)userdata;
    size_t total = size * nitems;
    if (total >= 5 && strncmp(ptr, "HTTP/", 5) == 0) {
        sink->g.len = 0;
        return total;
    }
    /* Strip the trailing CRLF and store one LF-terminated row, the shape the
       plaintext path stores. */
    while (total > 0 && (ptr[total - 1] == '\n' || ptr[total - 1] == '\r')) {
        total--;
    }
    if (total > 0) {
        ps_grow_add(&sink->g, ptr, (int64_t)total);
        ps_grow_add(&sink->g, "\n", 1);
    }
    return size * nitems;
}

static void ps_curl_load(void) {
    const char *override;
    const char *names[3];
    int count = 0;
    if (ps_curl_tried) {
        if (!ps_curl_ok) {
            ps_raise_cstr(PS_OSERROR, "https is not supported: libcurl is missing (install libcurl to enable https)");
        }
        return;
    }
    ps_curl_tried = 1;
    override = getenv("PSC_LIBCURL_PATH");
    if (override != NULL && override[0] == '/') {
        names[count++] = override;
    } else {
#if defined(_WIN32)
        names[count++] = "libcurl.dll";
#elif defined(__APPLE__)
        names[count++] = "libcurl.4.dylib";
#else
        names[count++] = "libcurl.so.4";
        names[count++] = "libcurl.so";
#endif
    }
    for (int i = 0; i < count; i++) {
        ps_curl_lib = ps_curl_open_lib(names[i]);
        if (ps_curl_lib != NULL) {
            break;
        }
    }
    if (ps_curl_lib == NULL) {
        ps_raise_cstr(PS_OSERROR, "https is not supported: libcurl is missing (install libcurl to enable https)");
    }
    /* Only the seven symbols the easy API path needs are resolved; the rest
       of libcurl is never touched. */
    ps_curl_easy_init = (ps_curl_easy_init_fn)ps_curl_lookup("curl_easy_init");
    ps_curl_easy_setopt = (ps_curl_easy_setopt_fn)ps_curl_lookup("curl_easy_setopt");
    ps_curl_easy_perform = (ps_curl_easy_perform_fn)ps_curl_lookup("curl_easy_perform");
    ps_curl_easy_getinfo = (ps_curl_easy_getinfo_fn)ps_curl_lookup("curl_easy_getinfo");
    ps_curl_easy_cleanup = (ps_curl_easy_cleanup_fn)ps_curl_lookup("curl_easy_cleanup");
    ps_curl_slist_append = (ps_curl_slist_append_fn)ps_curl_lookup("curl_slist_append");
    ps_curl_slist_free_all = (ps_curl_slist_free_all_fn)ps_curl_lookup("curl_slist_free_all");
    if (ps_curl_easy_init == NULL || ps_curl_easy_setopt == NULL || ps_curl_easy_perform == NULL
        || ps_curl_easy_getinfo == NULL || ps_curl_easy_cleanup == NULL || ps_curl_slist_append == NULL
        || ps_curl_slist_free_all == NULL) {
        ps_raise_cstr(PS_OSERROR, "https is not supported: libcurl is missing the easy API symbols");
    }
    ps_curl_ok = 1;
}

/* Map a CURLE code onto the error text the plaintext client raises for the
   same failure, so a caller sees the same message on both paths. Codes this
   file does not name surface with the code number attached. */
static void ps_curl_fail(CURLcode code) {
    char msg[128];
    switch (code) {
        case PS_CURL_OPERATION_TIMEDOUT:
            ps_raise_cstr(PS_OSERROR, "timed out");
            break;
        case PS_CURL_COULDNT_RESOLVE_HOST:
            ps_raise_cstr(PS_OSERROR, "no address for host");
            break;
        case PS_CURL_COULDNT_CONNECT:
            ps_raise_cstr(PS_OSERROR, "connection refused");
            break;
        case PS_CURL_TOO_MANY_REDIRECTS:
            ps_raise_cstr(PS_OSERROR, "HTTP Error 310: Exceeded redirect limit");
            break;
        case PS_CURL_PEER_FAILED_VERIFICATION:
            ps_raise_cstr(PS_OSERROR, "the TLS certificate failed verification");
            break;
        default:
            snprintf(msg, sizeof msg, "https request failed (curl error %d)", (int)code);
            ps_raise_cstr(PS_OSERROR, msg);
            break;
    }
}

/* One https request/response round trip through libcurl's easy API. The
   caller drives the redirect loop; this call never follows a redirect itself.
   Fills the record and, when the status is a redirect, hands back the
   Location value (owned) exactly like the plaintext roundtrip does. */
void ps_http_curl_roundtrip(
    const char *url,
    const char *method,
    const char *extra_headers,
    const char *body,
    int64_t body_len,
    double timeout,
    PsCurlResp *out,
    char **location
) {
    ps_curl_load();
    CURL *easy = ps_curl_easy_init();
    if (easy == NULL) {
        ps_raise_cstr(PS_OSERROR, "https request failed: libcurl could not start a session");
    }
    PsCurlSink body_sink;
    ps_grow_init(&body_sink.g);
    PsCurlSink header_sink;
    ps_grow_init(&header_sink.g);
    struct curl_slist *headers = NULL;
    char *loc = NULL;
    CURLcode code = PS_CURL_OK;

    code = ps_curl_easy_setopt(easy, PS_CURL_OPT_URL, url);
    if (code == PS_CURL_OK) {
        code = ps_curl_easy_setopt(easy, PS_CURL_OPT_WRITEFUNCTION, ps_curl_body_cb);
    }
    if (code == PS_CURL_OK) {
        code = ps_curl_easy_setopt(easy, PS_CURL_OPT_WRITEDATA, &body_sink);
    }
    if (code == PS_CURL_OK) {
        code = ps_curl_easy_setopt(easy, PS_CURL_OPT_HEADERFUNCTION, ps_curl_header_cb);
    }
    if (code == PS_CURL_OK) {
        code = ps_curl_easy_setopt(easy, PS_CURL_OPT_HEADERDATA, &header_sink);
    }
    /* Verification on, always. The explicit values are libcurl's own
       defaults: the peer certificate must chain to the platform trust store
       and the certificate name must match the host. */
    if (code == PS_CURL_OK) {
        code = ps_curl_easy_setopt(easy, PS_CURL_OPT_SSL_VERIFYPEER, 1L);
    }
    if (code == PS_CURL_OK) {
        code = ps_curl_easy_setopt(easy, PS_CURL_OPT_SSL_VERIFYHOST, 2L);
    }
    /* The redirect loop stays above this call. */
    if (code == PS_CURL_OK) {
        code = ps_curl_easy_setopt(easy, PS_CURL_OPT_FOLLOWLOCATION, 0L);
    }
    if (code == PS_CURL_OK && body_len > 0) {
        /* The body rides POSTFIELDS with an explicit length, so bytes with a
           NUL in them survive. POSTFIELDS implies POST, which is why the
           method is set after this block. */
        code = ps_curl_easy_setopt(easy, PS_CURL_OPT_POSTFIELDSIZE_LARGE, (long long)body_len);
        if (code == PS_CURL_OK) {
            code = ps_curl_easy_setopt(easy, PS_CURL_OPT_POSTFIELDS, body);
        }
    }
    if (code == PS_CURL_OK && strcmp(method, "HEAD") == 0) {
        /* HEAD asks for the headers only: NOBODY keeps the body off the
           wire, the way CPython's HEAD request does. */
        code = ps_curl_easy_setopt(easy, PS_CURL_OPT_NOBODY, 1L);
    }
    if (code == PS_CURL_OK && strcmp(method, "GET") != 0 && strcmp(method, "HEAD") != 0) {
        /* CUSTOMREQUEST rides after POSTFIELDS on purpose: it overrides the
           method POSTFIELDS implies, so a GET with a body stays a GET on
           the wire, the way CPython sends it. */
        code = ps_curl_easy_setopt(easy, PS_CURL_OPT_CUSTOMREQUEST, method);
    } else if (code == PS_CURL_OK && strcmp(method, "POST") == 0 && body_len == 0) {
        /* A POST without a body still needs the method. */
        code = ps_curl_easy_setopt(easy, PS_CURL_OPT_POST, 1L);
    }
    if (code == PS_CURL_OK && extra_headers != NULL && extra_headers[0] != '\0') {
        /* The block is CRLF-terminated rows, the shape the compiler emits for
           a Request headers dict display. Each row becomes one slist node, so
           the wire sees the same header lines as the plaintext path. */
        const char *cursor = extra_headers;
        while (*cursor != '\0') {
            const char *end = strstr(cursor, "\r\n");
            size_t len = end != NULL ? (size_t)(end - cursor) : strlen(cursor);
            char *row = ps_http_curl_dup((int64_t)len, cursor);
            struct curl_slist *next = ps_curl_slist_append(headers, row);
            free(row);
            if (next == NULL) {
                code = (CURLcode)-1;
                break;
            }
            headers = next;
            if (end == NULL) {
                break;
            }
            cursor = end + 2;
        }
        if (code == PS_CURL_OK) {
            code = ps_curl_easy_setopt(easy, PS_CURL_OPT_HTTPHEADER, headers);
        }
    }
    if (code == PS_CURL_OK && timeout > 0) {
        /* The seconds left on the deadline the caller passed: the whole
           request (this hop) shares the one budget, and a next hop gets the
           remainder the plaintext path would have given it. */
        long ms = (long)(timeout * 1000.0);
        if (ms < 1) {
            ms = 1;
        }
        code = ps_curl_easy_setopt(easy, PS_CURL_OPT_TIMEOUT_MS, ms);
    }
    if (code == PS_CURL_OK) {
        code = ps_curl_easy_perform(easy);
    }
    int64_t status = 0;
    if (code == PS_CURL_OK) {
        long code_out = 0;
        if (ps_curl_easy_getinfo(easy, PS_CURL_INFO_RESPONSE_CODE, &code_out) == PS_CURL_OK) {
            status = (int64_t)code_out;
        }
    }
    ps_curl_slist_free_all(headers);
    ps_curl_easy_cleanup(easy);
    if (code != PS_CURL_OK) {
        free(header_sink.g.data);
        free(body_sink.g.data);
        free(loc);
        ps_curl_fail(code);
    }

    /* A redirect hands the Location back; the loop above resolves it. */
    if (status == 301 || status == 302 || status == 303 || status == 307 || status == 308) {
        const char *scan = header_sink.g.data;
        int64_t scan_len = header_sink.g.len;
        int64_t at = 0;
        while (at < scan_len) {
            const char *row = scan + at;
            const char *nl = memchr(row, '\n', (size_t)(scan_len - at));
            int64_t row_len = nl != NULL ? (int64_t)(nl - row) : scan_len - at;
            if (row_len > 9 && strncasecmp(row, "location:", 9) == 0) {
                int64_t value_at = 9;
                while (value_at < row_len && (row[value_at] == ' ' || row[value_at] == '\t')) {
                    value_at++;
                }
                free(loc);
                loc = ps_http_curl_dup(row_len - value_at, row + value_at);
            }
            at += row_len + 1;
        }
    }

    ps_grow_finish(&header_sink.g, &out->headers);
    ps_grow_finish(&body_sink.g, &out->body);
    out->status = status;
    *location = loc;
}

/* The same duplication helper ps_http.c uses; a negative length is a
   ValueError like the plaintext path raises through its own ps_http_dup. */
char *ps_http_curl_dup(int64_t n, const char *s) {
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

#else /* __wasi__ */

#include <stdlib.h>

typedef struct {
    int64_t status;
    PsBuf headers;
    PsBuf body;
} PsCurlResp;

/* The stub: WASI has no dlopen and no sockets, and the ps_http.c stub raises
   for every scheme there first, so this entry point never fires. It exists so
   the runtime keeps linking for the target (issue #313). */
void ps_http_curl_roundtrip(
    const char *url,
    const char *method,
    const char *extra_headers,
    const char *body,
    int64_t body_len,
    double timeout,
    PsCurlResp *out,
    char **location
) {
    (void)url;
    (void)method;
    (void)extra_headers;
    (void)body;
    (void)body_len;
    (void)timeout;
    (void)out;
    (void)location;
    ps_raise_cstr(PS_OSERROR, "https is not supported on this target");
}

char *ps_http_curl_dup(int64_t n, const char *s) {
    (void)n;
    (void)s;
    ps_raise_cstr(PS_OSERROR, "https is not supported on this target");
}

#endif /* __wasi__ */
