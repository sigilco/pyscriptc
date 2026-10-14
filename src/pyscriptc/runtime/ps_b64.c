/* base64 and urlsafe base64. Padding is required. Characters outside the
alphabet are ignored, matching CPython's default validate=False. binascii.Error
is reported as ValueError. */

#include "ps_rt.h"

#include <stdlib.h>
#include <string.h>

static int ps_b64_val(unsigned char c, int *out) {
    if (c >= 'A' && c <= 'Z') {
        *out = c - 'A';
        return 1;
    }
    if (c >= 'a' && c <= 'z') {
        *out = c - 'a' + 26;
        return 1;
    }
    if (c >= '0' && c <= '9') {
        *out = c - '0' + 52;
        return 1;
    }
    if (c == '+' || c == '-') {
        *out = 62;
        return 1;
    }
    if (c == '/' || c == '_') {
        *out = 63;
        return 1;
    }
    return 0;
}

static void ps_b64_encode(int64_t n, const char *p, int url, PsBuf *out) {
    static const char kStd[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    static const char kUrl[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    const char *alpha = url ? kUrl : kStd;
    if (n < 0) {
        ps_raise_cstr(PS_VALUE, "base64 length is negative");
    }
    int64_t groups = (n + 2) / 3;
    if (groups > INT64_MAX / 4) {
        ps_raise_cstr(PS_OVERFLOW, "base64 result does not fit");
    }
    int64_t total = groups * 4;
    char *q = malloc((size_t)total + 1);
    if (q == NULL) {
        abort();
    }
    int64_t w = 0;
    int64_t i = 0;
    while (i + 3 <= n) {
        unsigned int v = ((unsigned char)p[i] << 16) | ((unsigned char)p[i + 1] << 8) | (unsigned char)p[i + 2];
        q[w++] = alpha[(v >> 18) & 63];
        q[w++] = alpha[(v >> 12) & 63];
        q[w++] = alpha[(v >> 6) & 63];
        q[w++] = alpha[v & 63];
        i += 3;
    }
    if (i < n) {
        unsigned int v = (unsigned char)p[i] << 16;
        if (i + 1 < n) {
            v |= (unsigned char)p[i + 1] << 8;
        }
        q[w++] = alpha[(v >> 18) & 63];
        q[w++] = alpha[(v >> 12) & 63];
        if (i + 1 < n) {
            q[w++] = alpha[(v >> 6) & 63];
            q[w++] = '=';
        } else {
            q[w++] = '=';
            q[w++] = '=';
        }
    }
    q[w] = '\0';
    out->len = w;
    out->data = q;
}

static void ps_b64_decode(int64_t n, const char *p, int url, PsBuf *out) {
    char *filtered = malloc((size_t)n + 1);
    if (filtered == NULL && n > 0) {
        abort();
    }
    int64_t m = 0;
    for (int64_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)p[i];
        int dummy = 0;
        if (c == '=') {
            filtered[m++] = '=';
            continue;
        }
        if (!ps_b64_val(c, &dummy)) {
            continue;
        }
        if (!url && (c == '-' || c == '_')) {
            continue;
        }
        filtered[m++] = (char)c;
    }
    if (m % 4 != 0) {
        free(filtered);
        ps_raise_cstr(PS_VALUE, "incorrect base64 padding");
    }
    int64_t pad = 0;
    if (m >= 1 && filtered[m - 1] == '=') {
        pad++;
    }
    if (m >= 2 && filtered[m - 2] == '=') {
        pad++;
    }
    for (int64_t i = 0; i < m - pad; i++) {
        if (filtered[i] == '=') {
            free(filtered);
            ps_raise_cstr(PS_VALUE, "incorrect base64 padding");
        }
    }
    int64_t data_chars = m - pad;
    if (data_chars % 4 == 1) {
        free(filtered);
        ps_raise_cstr(PS_VALUE, "incorrect base64 padding");
    }
    int64_t total = m == 0 ? 0 : (m / 4) * 3 - pad;
    char *q = malloc((size_t)total + 1);
    if (q == NULL) {
        free(filtered);
        abort();
    }
    int64_t w = 0;
    for (int64_t i = 0; i < m; i += 4) {
        int v0 = 0;
        int v1 = 0;
        int v2 = 0;
        int v3 = 0;
        if (!ps_b64_val((unsigned char)filtered[i], &v0) || !ps_b64_val((unsigned char)filtered[i + 1], &v1)) {
            free(filtered);
            free(q);
            ps_raise_cstr(PS_VALUE, "incorrect base64 padding");
        }
        if (filtered[i + 2] != '=' && !ps_b64_val((unsigned char)filtered[i + 2], &v2)) {
            free(filtered);
            free(q);
            ps_raise_cstr(PS_VALUE, "incorrect base64 padding");
        }
        if (filtered[i + 3] != '=' && !ps_b64_val((unsigned char)filtered[i + 3], &v3)) {
            free(filtered);
            free(q);
            ps_raise_cstr(PS_VALUE, "incorrect base64 padding");
        }
        unsigned int v = ((unsigned int)v0 << 18) | ((unsigned int)v1 << 12) | ((unsigned int)v2 << 6) | (unsigned int)v3;
        q[w++] = (char)((v >> 16) & 0xff);
        if (filtered[i + 2] != '=') {
            q[w++] = (char)((v >> 8) & 0xff);
        }
        if (filtered[i + 3] != '=') {
            q[w++] = (char)(v & 0xff);
        }
    }
    free(filtered);
    q[w] = '\0';
    out->len = w;
    out->data = q;
}

void ps_b64_std_encode(int64_t n, char *p, PsBuf *out) { ps_b64_encode(n, p, 0, out); }
void ps_b64_url_encode(int64_t n, char *p, PsBuf *out) { ps_b64_encode(n, p, 1, out); }
void ps_b64_std_decode(int64_t n, char *p, PsBuf *out) { ps_b64_decode(n, p, 0, out); }
void ps_b64_url_decode(int64_t n, char *p, PsBuf *out) { ps_b64_decode(n, p, 1, out); }
