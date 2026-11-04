/* The https:// hop over libcurl's easy API, linked at build time (issue #235,
libcurl first). For an https:// URL the runtime performs the whole request
through libcurl: libcurl is an HTTP client, not a TLS stream, so it does not
implement the PsHttpTransport contract of ps_tls.h. It takes the URL, the
method, the request headers, the body and the timeout the HTTP layer hands
over, follows redirects and fills the same response shapes the plaintext
transport fills, so the redirect loop in ps_http.c treats both schemes alike.

Linking: the compiler driver compiles this file with PSC_HAVE_CURL and adds
-lcurl (resolved through curl-config or pkg-config libcurl) to the link line
of a program whose emitted IR calls ps_http_request over an https:// URL, and
only for those, so an http-only program carries no libcurl dependency and no
curl symbol reaches its link line. This file is part of the runtime the link
always includes, so the surface is spelled here instead of through
<curl/curl.h>. Without PSC_HAVE_CURL the whole libcurl block compiles out and
ps_http_curl_https below still exists (returning 0), so ps_http.c keeps a
plain strong reference and nothing depends on weak-symbol link behavior,
which Mach-O does not give for undefined symbols: the old `__attribute__
((weak))` declarations broke every macOS link that skipped -lcurl.

Verification is always on. CURLOPT_SSL_VERIFYPEER and CURLOPT_SSL_VERIFYHOST
are set to their defaults explicitly (1 and 2), so the peer certificate
chains to the platform trust store (Schannel on Windows, Secure Transport on
macOS, OpenSSL on Linux) and the name matches the host. Nothing in the
surface can turn them off, not even in a test.

The CURLOPT_* numbers are the public ABI libcurl has kept stable: LONG = n,
OBJECTPOINT/STRINGPOINT/SLISTPOINT/CBPOINT = n + 10000, FUNCTIONPOINT =
n + 20000, OFF_T = n + 30000. */

#include "ps_rt.h"

#if !defined(__wasi__) && !defined(_WIN32) && defined(PSC_HAVE_CURL)

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct curl_slist {
    char *data;
    struct curl_slist *next;
};

typedef void CURL;
typedef int CURLcode;
typedef int CURLoption;
typedef int CURLinfo;

#define PS_CURL_OPT_URL 10002
#define PS_CURL_OPT_ERRORBUFFER 10010
#define PS_CURL_OPT_WRITEFUNCTION 20011
#define PS_CURL_OPT_WRITEDATA 10001
#define PS_CURL_OPT_POSTFIELDS 10015
#define PS_CURL_OPT_POSTFIELDSIZE_LARGE 30120
#define PS_CURL_OPT_HTTPHEADER 10023
#define PS_CURL_OPT_HEADERDATA 10029
#define PS_CURL_OPT_HEADERFUNCTION 20079
#define PS_CURL_OPT_CUSTOMREQUEST 10036
#define PS_CURL_OPT_NOBODY 44
#define PS_CURL_OPT_FOLLOWLOCATION 52
#define PS_CURL_OPT_MAXREDIRS 68
#define PS_CURL_OPT_SSL_VERIFYPEER 64
#define PS_CURL_OPT_SSL_VERIFYHOST 81
#define PS_CURL_OPT_TIMEOUT_MS 155
#define PS_CURL_OPT_CONNECTTIMEOUT_MS 156
#define PS_CURL_OPT_NOSIGNAL 99

/* CURLINFO_RESPONSE_CODE = CURLINFO_LONG (0x200000) + 2. */
#define PS_CURL_INFO_RESPONSE_CODE (0x200000L + 2L)

#define PS_CURL_OK 0
#define PS_CURL_COULDNT_RESOLVE_HOST 6
#define PS_CURL_COULDNT_CONNECT 7
#define PS_CURL_OPERATION_TIMEDOUT 28
#define PS_CURL_TOO_MANY_REDIRECTS 47
#define PS_CURL_PEER_FAILED_VERIFICATION 60
#define PS_CURL_SSL_CONNECT_ERROR 35

/* The libcurl declarations, spelled out so the runtime never includes
   <curl/curl.h>: the file compiles only when the driver links libcurl, so
   these resolve against the shared library at link time. */
CURL *curl_easy_init(void);
CURLcode curl_easy_setopt(CURL *easy, CURLoption option, ...);
CURLcode curl_easy_perform(CURL *easy);
CURLcode curl_easy_getinfo(CURL *easy, CURLinfo info, ...);
void curl_easy_cleanup(CURL *easy);
const char *curl_easy_strerror(CURLcode code);
struct curl_slist *curl_slist_append(struct curl_slist *list, const char *string);
void curl_slist_free_all(struct curl_slist *list);

static void ps_curl_fail(const char *what) { ps_raise_cstr(PS_OSERROR, what); }

/* The same OSError text the plaintext client raises for the same failure:
   resolve and connect map to its getaddrinfo and "connection refused"
   messages, the timeout to its "timed out", and verification failures say
   what failed rather than surfacing as a wall of TLS detail. */
static void ps_curl_wire_error(CURLcode rc, const char *errbuf) {
    if (rc == PS_CURL_OPERATION_TIMEDOUT) {
        ps_curl_fail("timed out");
    }
    if (rc == PS_CURL_COULDNT_RESOLVE_HOST) {
        ps_curl_fail("no address for host");
    }
    if (rc == PS_CURL_COULDNT_CONNECT) {
        ps_curl_fail("connection refused");
    }
    char msg[512];
    if (errbuf[0] != '\0') {
        snprintf(msg, sizeof msg, "HTTPS request failed: %s", errbuf);
    } else {
        snprintf(msg, sizeof msg, "HTTPS request failed: %s", curl_easy_strerror(rc));
    }
    ps_curl_fail(msg);
}

/* The body callback: append the chunk to the caller's grow buffer. */
static size_t ps_curl_write(char *ptr, size_t size, size_t nmemb, void *userdata) {
    PsGrow *body = (PsGrow *)userdata;
    size_t total = size * nmemb;
    if (total > 0) {
        ps_grow_add(body, ptr, (int64_t)total);
    }
    return total;
}

/* The header callback: one header row per call, CRLF-terminated. The block
   keeps the "Name: value\n" rows the plaintext parser produces, so the
   redirect Location scan and r.info() see the same shape on both schemes. */
static size_t ps_curl_header(char *ptr, size_t size, size_t nmemb, void *userdata) {
    PsGrow *headers = (PsGrow *)userdata;
    size_t total = size * nmemb;
    size_t end = total;
    while (end > 0 && (ptr[end - 1] == '\n' || ptr[end - 1] == '\r')) {
        end--;
    }
    if (end > 0) {
        ps_grow_add(headers, ptr, (int64_t)end);
        ps_grow_add(headers, "\n", 1);
    }
    return total;
}

/* One https:// request over the easy API. Returns 1 with the response
   filled (status, the header block, the body), 0 when libcurl is not
   linked (the caller raises the documented URLError). Wire failures raise
   OSError here; a 4xx/5xx status comes back as the status and the HTTP
   layer raises the HTTPError it always raises. */
int ps_http_curl_https(
    const char *url,
    const char *method,
    const char *extra_headers,
    const char *body,
    int64_t body_len,
    double timeout,
    int64_t *status,
    PsGrow *headers,
    PsGrow *out_body
) {
    CURL *easy = curl_easy_init();
    if (easy == NULL) {
        ps_curl_fail("HTTPS request failed: curl_easy_init returned NULL");
    }
    char errbuf[256];
    errbuf[0] = '\0';
    curl_easy_setopt(easy, PS_CURL_OPT_URL, url);
    curl_easy_setopt(easy, PS_CURL_OPT_ERRORBUFFER, errbuf);
    curl_easy_setopt(easy, PS_CURL_OPT_NOSIGNAL, 1L);
    curl_easy_setopt(easy, PS_CURL_OPT_CUSTOMREQUEST, method);
    if (strcmp(method, "HEAD") == 0) {
        curl_easy_setopt(easy, PS_CURL_OPT_NOBODY, 1L);
    }
    if (body_len > 0) {
        curl_easy_setopt(easy, PS_CURL_OPT_POSTFIELDS, body);
        curl_easy_setopt(easy, PS_CURL_OPT_POSTFIELDSIZE_LARGE, (long long)body_len);
    }
    struct curl_slist *request_headers = NULL;
    const char *cursor = extra_headers;
    while (cursor != NULL && *cursor != '\0') {
        /* The wire block is "Name: value\r\n" rows (the lowerer renders the
           header display that way); split on the CRLF and hand each row to
           curl's list, where the built-in headers join it. */
        const char *end = strstr(cursor, "\r\n");
        size_t len = end != NULL ? (size_t)(end - cursor) : strlen(cursor);
        char *row = malloc(len + 1);
        if (row == NULL) {
            abort();
        }
        memcpy(row, cursor, len);
        row[len] = '\0';
        struct curl_slist *appended = curl_slist_append(request_headers, row);
        free(row);
        if (appended == NULL) {
            break;
        }
        request_headers = appended;
        if (end == NULL) {
            break;
        }
        cursor = end + 2;
    }
    if (request_headers != NULL) {
        curl_easy_setopt(easy, PS_CURL_OPT_HTTPHEADER, request_headers);
    }
    if (timeout > 0) {
        /* The one deadline the whole request shares: the seconds the HTTP
           layer has left, as curl's total and connect budget. */
        curl_easy_setopt(easy, PS_CURL_OPT_TIMEOUT_MS, (long)(timeout * 1000.0));
        curl_easy_setopt(easy, PS_CURL_OPT_CONNECTTIMEOUT_MS, (long)(timeout * 1000.0));
    }
    /* Redirects: follow on, bounded at the 10 hops the plaintext client
       spends, and curl rewrites the method and body the way urllib.request
       does (301 and 302 turn a POST into a GET, 303 always does, 307 and
       308 keep both), so the redirect loop above sees the same final
       request and response as the http:// path. */
    curl_easy_setopt(easy, PS_CURL_OPT_FOLLOWLOCATION, 1L);
    curl_easy_setopt(easy, PS_CURL_OPT_MAXREDIRS, (long)10);
    /* Verification on, explicitly: these two are curl's defaults and they
       are set here so no later edit quietly drops them. */
    curl_easy_setopt(easy, PS_CURL_OPT_SSL_VERIFYPEER, 1L);
    curl_easy_setopt(easy, PS_CURL_OPT_SSL_VERIFYHOST, 2L);
    ps_grow_init(headers);
    ps_grow_init(out_body);
    curl_easy_setopt(easy, PS_CURL_OPT_WRITEFUNCTION, ps_curl_write);
    curl_easy_setopt(easy, PS_CURL_OPT_WRITEDATA, out_body);
    curl_easy_setopt(easy, PS_CURL_OPT_HEADERFUNCTION, ps_curl_header);
    curl_easy_setopt(easy, PS_CURL_OPT_HEADERDATA, headers);

    CURLcode rc = curl_easy_perform(easy);
    int64_t response = 0;
    if (rc == PS_CURL_OK) {
        long code = 0;
        curl_easy_getinfo(easy, PS_CURL_INFO_RESPONSE_CODE, &code);
        response = (int64_t)code;
    }
    if (request_headers != NULL) {
        curl_slist_free_all(request_headers);
    }
    curl_easy_cleanup(easy);
    if (rc != PS_CURL_OK) {
        ps_curl_wire_error(rc, errbuf);
    }
    *status = response;
    return 1;
}

#elif defined(__wasi__) || defined(_WIN32)

/* The curl file compiles to nothing on the unsupported targets: the HTTP
   stub in ps_http.c owns their whole urlopen story. */

#else /* no PSC_HAVE_CURL: wasi, win32, or the driver skipped libcurl */

/* The driver skipped libcurl (no https:// in the IR, PSC_CURL_DYNAMIC=1, a
   wasm target or Windows), or the target has no urlopen story at all (the
   HTTP stub in ps_http.c owns WASI). The symbol stays defined so ps_http.c
   links without any weak-symbol dependency, and the https:// request raises
   the documented URLError. */
int ps_http_curl_https(
    const char *url,
    const char *method,
    const char *extra_headers,
    const char *body,
    int64_t body_len,
    double timeout,
    int64_t *status,
    PsGrow *headers,
    PsGrow *out_body
) {
    (void)url;
    (void)method;
    (void)extra_headers;
    (void)body;
    (void)body_len;
    (void)timeout;
    (void)status;
    (void)headers;
    (void)out_body;
    return 0;
}

#endif /* PSC_HAVE_CURL */
