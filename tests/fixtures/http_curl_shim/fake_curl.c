/* The fake libcurl for the https:// test (issue #235).
   The runtime loads libcurl through dlopen at run time and resolves seven
   symbols of the easy API. This shared library exports the same seven, plus
   the option numbers the runtime sets, so a test can drive an https:// request
   with no network and no libcurl on the machine.

   The shim records the options it saw into a text file named by
   PSC_CURL_SHIM_LOG and answers with the canned response in
   PSC_CURL_SHIM_RESPONSE (a file of raw HTTP/1.1 response bytes). The
   recorded file is what the test asserts on: the URL, the method, the
   request headers, the body, the timeout and the verification options.

   It exports the symbols with the same names and the same C signatures
   libcurl uses, so the runtime resolves them the way it resolves the real
   library.
*/

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* The CURLOPT_* numbers the runtime sets. The runtime resolves the option
   constants itself (it does not ship the curl headers), so the shim has to
   agree with them on the numbers, not on the names. */
#define OPT_WRITEDATA 10001
#define OPT_URL 10002
#define OPT_WRITEFUNCTION 20011
#define OPT_POSTFIELDS 10015
#define OPT_HTTPHEADER 10023
#define OPT_HEADERDATA 10029
#define OPT_CUSTOMREQUEST 10036
#define OPT_POST 47
#define OPT_NOBODY 44
#define OPT_FOLLOWLOCATION 52
#define OPT_POSTFIELDSIZE_LARGE 30120
#define OPT_SSL_VERIFYPEER 64
#define OPT_HEADERFUNCTION 20079
#define OPT_SSL_VERIFYHOST 81
#define OPT_TIMEOUT_MS 155

#define INFO_RESPONSE_CODE (0x200000L + 2L)

#define CURL_OK 0

typedef void CURL;
typedef int CURLcode;
typedef int CURLoption;
typedef int CURLinfo;

struct curl_slist {
    char *data;
    struct curl_slist *next;
};

struct curl_slist *curl_slist_append(struct curl_slist *list, const char *text);
void curl_slist_free_all(struct curl_slist *list);

/* The write callback the runtime installs. */
typedef size_t (*fake_write_fn)(char *buffer, size_t size, size_t nitems, void *userdata);

/* One session: the options the runtime set on it. */
typedef struct {
    char url[512];
    char method[32];
    char *postfields;
    long postfieldsize;
    long timeout_ms;
    long verifypeer;
    long verifyhost;
    long followlocation;
    long nobody;
    long post;
    struct curl_slist *headers;
    fake_write_fn write_fn;
    fake_write_fn header_fn;
    void *write_data;
    void *header_data;
} FakeCurl;

/* The recorded session, so curl_easy_getinfo can answer with the status the
   canned response carries. */
static long fake_status = 200;

static void fake_log(const char *fmt, ...) {
    const char *path = getenv("PSC_CURL_SHIM_LOG");
    FILE *f;
    va_list ap;
    if (path == NULL || path[0] == '\0') {
        return;
    }
    f = fopen(path, "a");
    if (f == NULL) {
        return;
    }
    va_start(ap, fmt);
    vfprintf(f, fmt, ap);
    va_end(ap);
    fclose(f);
}

CURL *curl_easy_init(void) {
    FakeCurl *c = (FakeCurl *)calloc(1, sizeof(FakeCurl));
    if (c == NULL) {
        return NULL;
    }
    /* The defaults libcurl starts from: verification on. */
    c->verifypeer = 1;
    c->verifyhost = 2;
    return (CURL *)c;
}

void curl_easy_cleanup(CURL *curl) {
    FakeCurl *c = (FakeCurl *)curl;
    if (c == NULL) {
        return;
    }
    free(c->postfields);
    /* The header list belongs to the caller: libcurl never frees the slist
       it was handed through CURLOPT_HTTPHEADER, so the shim must not either
       (the caller frees it with curl_slist_free_all). */
    free(c);
}

struct curl_slist *curl_slist_append(struct curl_slist *list, const char *text) {
    struct curl_slist *node = (struct curl_slist *)calloc(1, sizeof(struct curl_slist));
    struct curl_slist *tail = list;
    if (node == NULL) {
        return NULL;
    }
    node->data = strdup(text);
    if (list == NULL) {
        return node;
    }
    while (tail->next != NULL) {
        tail = tail->next;
    }
    tail->next = node;
    return list;
}

void curl_slist_free_all(struct curl_slist *list) {
    while (list != NULL) {
        struct curl_slist *next = list->next;
        free(list->data);
        free(list);
        list = next;
    }
}

CURLcode curl_easy_setopt(CURL *curl, CURLoption option, ...) {
    FakeCurl *c = (FakeCurl *)curl;
    va_list ap;
    va_start(ap, option);
    switch (option) {
        case OPT_URL:
            snprintf(c->url, sizeof c->url, "%s", va_arg(ap, const char *));
            break;
        case OPT_WRITEFUNCTION:
            c->write_fn = va_arg(ap, fake_write_fn);
            break;
        case OPT_WRITEDATA:
            c->write_data = va_arg(ap, void *);
            break;
        case OPT_HEADERFUNCTION:
            c->header_fn = va_arg(ap, fake_write_fn);
            break;
        case OPT_HEADERDATA:
            c->header_data = va_arg(ap, void *);
            break;
        case OPT_POSTFIELDS:
            c->postfields = strdup(va_arg(ap, const char *));
            break;
        case OPT_POSTFIELDSIZE_LARGE:
            c->postfieldsize = (long)va_arg(ap, long long);
            break;
        case OPT_HTTPHEADER:
            c->headers = va_arg(ap, struct curl_slist *);
            break;
        case OPT_CUSTOMREQUEST:
            snprintf(c->method, sizeof c->method, "%s", va_arg(ap, const char *));
            break;
        case OPT_POST:
            c->post = va_arg(ap, long);
            break;
        case OPT_NOBODY:
            c->nobody = va_arg(ap, long);
            break;
        case OPT_FOLLOWLOCATION:
            c->followlocation = va_arg(ap, long);
            break;
        case OPT_SSL_VERIFYPEER:
            c->verifypeer = va_arg(ap, long);
            break;
        case OPT_SSL_VERIFYHOST:
            c->verifyhost = va_arg(ap, long);
            break;
        case OPT_TIMEOUT_MS:
            c->timeout_ms = va_arg(ap, long);
            break;
        default:
            break;
    }
    va_end(ap);
    return CURL_OK;
}

CURLcode curl_easy_perform(CURL *curl) {
    FakeCurl *c = (FakeCurl *)curl;
    const char *response_path = getenv("PSC_CURL_SHIM_RESPONSE");
    FILE *f = NULL;
    char chunk[4096];
    size_t got;
    const char *body_marker = "\r\n\r\n";

    /* Record what the runtime set, so the test can assert on it. */
    fake_log("url=%s\n", c->url);
    fake_log("method=%s\n", c->method[0] != '\0' ? c->method : "GET");
    fake_log("post=%ld\n", c->post);
    fake_log("nobody=%ld\n", c->nobody);
    fake_log("followlocation=%ld\n", c->followlocation);
    fake_log("verifypeer=%ld\n", c->verifypeer);
    fake_log("verifyhost=%ld\n", c->verifyhost);
    fake_log("timeout_ms=%ld\n", c->timeout_ms);
    fake_log("postfieldsize=%ld\n", c->postfieldsize);
    if (c->postfields != NULL) {
        fake_log("postfields=%s\n", c->postfields);
    }
    for (struct curl_slist *node = c->headers; node != NULL; node = node->next) {
        fake_log("header=%s\n", node->data);
    }

    if (response_path == NULL || response_path[0] == '\0') {
        /* No canned response: an empty 200. */
        fake_status = 200;
        return CURL_OK;
    }
    f = fopen(response_path, "rb");
    if (f == NULL) {
        return CURL_OK;
    }
    /* The status line the response file starts with. */
    if (fgets(chunk, (int)sizeof chunk, f) != NULL) {
        const char *space = strchr(chunk, ' ');
        if (space != NULL) {
            fake_status = strtol(space + 1, NULL, 10);
        }
        /* The header block goes to the header callback, the body to the
           write callback, the way libcurl splits them. */
        if (c->header_fn != NULL) {
            c->header_fn(chunk, strlen(chunk), 1, c->header_data);
        }
    }
    while ((got = fread(chunk, 1, sizeof chunk, f)) > 0) {
        char *split = strstr(chunk, body_marker);
        if (split != NULL) {
            size_t head_len = (size_t)(split - chunk) + 4;
            if (c->header_fn != NULL) {
                c->header_fn(chunk, head_len, 1, c->header_data);
            }
            if (c->write_fn != NULL) {
                c->write_fn(split + 4, 1, got - head_len, c->write_data);
            }
            break;
        }
        if (c->header_fn != NULL) {
            c->header_fn(chunk, got, 1, c->header_data);
        }
    }
    fclose(f);
    return CURL_OK;
}

CURLcode curl_easy_getinfo(CURL *curl, CURLinfo info, ...) {
    (void)curl;
    va_list ap;
    va_start(ap, info);
    if (info == INFO_RESPONSE_CODE) {
        long *out = va_arg(ap, long *);
        if (out != NULL) {
            *out = fake_status;
        }
    }
    va_end(ap);
    return CURL_OK;
}
