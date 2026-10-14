/* Closed urllib.parse subset: quote, quote_plus, unquote, unquote_plus,
urlencode of dict[str, str], parse_qs (blank values dropped, separator '&'),
and urlsplit into five strings. Non-ASCII results are refused. */

#include "ps_rt.h"

#include <stdlib.h>
#include <string.h>

static int ps_unreserved(unsigned char c) {
    return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' || c == '.' ||
           c == '-' || c == '~';
}

static int ps_hex(unsigned char c) {
    if (c >= '0' && c <= '9') {
        return c - '0';
    }
    if (c >= 'a' && c <= 'f') {
        return c - 'a' + 10;
    }
    if (c >= 'A' && c <= 'F') {
        return c - 'A' + 10;
    }
    return -1;
}

static int ps_safe_has(const char *safe, int64_t sn, unsigned char c) {
    for (int64_t i = 0; i < sn; i++) {
        if ((unsigned char)safe[i] == c) {
            return 1;
        }
    }
    return 0;
}

static void ps_quote(int64_t n, const char *p, int64_t sn, const char *safe, int plus, PsBuf *out) {
    PsGrow g;
    ps_grow_init(&g);
    for (int64_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)p[i];
        if (plus && c == ' ') {
            ps_grow_byte(&g, '+');
            continue;
        }
        if (ps_unreserved(c) || ps_safe_has(safe, sn, c)) {
            ps_grow_byte(&g, c);
            continue;
        }
        static const char *hexd = "0123456789ABCDEF";
        ps_grow_byte(&g, '%');
        ps_grow_byte(&g, (unsigned char)hexd[c >> 4]);
        ps_grow_byte(&g, (unsigned char)hexd[c & 15]);
    }
    ps_grow_finish(&g, out);
}

void ps_url_quote(int64_t n, char *p, int64_t sn, char *safe, PsBuf *out) { ps_quote(n, p, sn, safe, 0, out); }

void ps_url_quote_plus(int64_t n, char *p, int64_t sn, char *safe, PsBuf *out) {
    int has_space = 0;
    for (int64_t i = 0; i < n; i++) {
        if (p[i] == ' ') {
            has_space = 1;
            break;
        }
    }
    if (!has_space) {
        ps_quote(n, p, sn, safe, 0, out);
        return;
    }
    char *wider = malloc((size_t)sn + 2);
    if (wider == NULL) {
        abort();
    }
    if (sn > 0) {
        memcpy(wider, safe, (size_t)sn);
    }
    wider[sn] = ' ';
    ps_quote(n, p, sn + 1, wider, 1, out);
    free(wider);
}

static void ps_unquote(int64_t n, const char *p, int plus, PsBuf *out) {
    PsGrow g;
    ps_grow_init(&g);
    for (int64_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)p[i];
        if (plus && c == '+') {
            ps_grow_byte(&g, ' ');
            continue;
        }
        if (c == '%' && i + 2 < n) {
            int hi = ps_hex((unsigned char)p[i + 1]);
            int lo = ps_hex((unsigned char)p[i + 2]);
            if (hi >= 0 && lo >= 0) {
                unsigned char decoded = (unsigned char)((hi << 4) | lo);
                if (decoded > 127) {
                    free(g.data);
                    ps_raise_cstr(PS_VALUE, "non-ASCII URL escape is not lowered");
                }
                ps_grow_byte(&g, decoded);
                i += 2;
                continue;
            }
        }
        if (c > 127) {
            free(g.data);
            ps_raise_cstr(PS_VALUE, "non-ASCII URL text is not lowered");
        }
        ps_grow_byte(&g, c);
    }
    ps_grow_finish(&g, out);
}

void ps_url_unquote(int64_t n, char *p, PsBuf *out) { ps_unquote(n, p, 0, out); }
void ps_url_unquote_plus(int64_t n, char *p, PsBuf *out) { ps_unquote(n, p, 1, out); }

void ps_url_encode(PsMap *m, PsBuf *out) {
    PsGrow g;
    ps_grow_init(&g);
    for (int64_t i = 0; i < ps_map_len(m); i++) {
        PsBuf key;
        PsBuf val;
        ps_map_key_at(m, i, &key);
        ps_map_val_at(m, i, &val);
        PsBuf qk;
        PsBuf qv;
        ps_url_quote_plus(key.len, key.data, 0, "", &qk);
        ps_url_quote_plus(val.len, val.data, 0, "", &qv);
        if (i > 0) {
            ps_grow_byte(&g, '&');
        }
        ps_grow_add(&g, qk.data, qk.len);
        ps_grow_byte(&g, '=');
        ps_grow_add(&g, qv.data, qv.len);
        free(qk.data);
        free(qv.data);
    }
    ps_grow_finish(&g, out);
}

PsMap *ps_url_parse_qs(int64_t n, char *p) {
    PsMap *m = ps_map_new(2, 16, 8, 0);
    int64_t i = 0;
    while (i <= n) {
        int64_t start = i;
        while (i < n && p[i] != '&') {
            i++;
        }
        int64_t end = i;
        int64_t eq = -1;
        for (int64_t j = start; j < end; j++) {
            if (p[j] == '=') {
                eq = j;
                break;
            }
        }
        if (eq >= 0) {
            PsBuf key;
            PsBuf val;
            ps_unquote(eq - start, p + start, 1, &key);
            ps_unquote(end - (eq + 1), p + eq + 1, 1, &val);
            if (val.len > 0) {
                PsSeq *existing = NULL;
                int64_t at = -1;
                for (int64_t k = 0; k < ps_map_len(m); k++) {
                    PsBuf cur;
                    ps_map_key_at(m, k, &cur);
                    if (cur.len == key.len && (key.len == 0 || memcmp(cur.data, key.data, (size_t)key.len) == 0)) {
                        ps_map_val_at(m, k, &existing);
                        at = k;
                        break;
                    }
                }
                if (existing == NULL) {
                    existing = ps_seq_new(16, 0);
                    ps_seq_push(existing, &val);
                    ps_map_put(m, &key, &existing);
                } else {
                    ps_seq_push(existing, &val);
                    free(key.data);
                    (void)at;
                }
            } else {
                free(key.data);
                free(val.data);
            }
        }
        if (i >= n) {
            break;
        }
        i++;
    }
    return m;
}

static int ps_scheme_char(unsigned char c) {
    return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '+' || c == '-' ||
           c == '.';
}

static void ps_strip_url(const char *p, int64_t n, char **out, int64_t *out_n) {
    int64_t i = 0;
    while (i < n && (unsigned char)p[i] <= ' ') {
        i++;
    }
    PsGrow g;
    ps_grow_init(&g);
    for (; i < n; i++) {
        unsigned char c = (unsigned char)p[i];
        if (c == '\t' || c == '\n' || c == '\r') {
            continue;
        }
        ps_grow_byte(&g, c);
    }
    *out = g.data == NULL ? malloc(1) : g.data;
    if (*out == NULL) {
        abort();
    }
    if (g.data == NULL) {
        (*out)[0] = '\0';
    }
    *out_n = g.len;
}

void ps_url_split(int64_t n, char *p, void *raw) {
    char *text = NULL;
    int64_t tn = 0;
    ps_strip_url(p, n, &text, &tn);
    PsBuf scheme;
    PsBuf netloc;
    PsBuf path;
    PsBuf query;
    PsBuf fragment;
    ps_buf_copy(&scheme, "", 0);
    ps_buf_copy(&netloc, "", 0);
    ps_buf_copy(&query, "", 0);
    ps_buf_copy(&fragment, "", 0);
    int64_t colon = -1;
    for (int64_t i = 0; i < tn; i++) {
        if (text[i] == ':') {
            colon = i;
            break;
        }
    }
    int64_t rest = 0;
    if (colon > 0 && ((text[0] >= 'A' && text[0] <= 'Z') || (text[0] >= 'a' && text[0] <= 'z'))) {
        int ok = 1;
        for (int64_t i = 0; i < colon; i++) {
            if (!ps_scheme_char((unsigned char)text[i])) {
                ok = 0;
                break;
            }
        }
        if (ok) {
            free(scheme.data);
            ps_buf_copy(&scheme, text, colon);
            for (int64_t i = 0; i < scheme.len; i++) {
                if (scheme.data[i] >= 'A' && scheme.data[i] <= 'Z') {
                    scheme.data[i] = (char)(scheme.data[i] - 'A' + 'a');
                }
            }
            rest = colon + 1;
        }
    }
    const char *url = text + rest;
    int64_t un = tn - rest;
    if (un >= 2 && url[0] == '/' && url[1] == '/') {
        int64_t delim = un;
        for (int64_t i = 2; i < un; i++) {
            if (url[i] == '/' || url[i] == '?' || url[i] == '#') {
                delim = i;
                break;
            }
        }
        free(netloc.data);
        ps_buf_copy(&netloc, url + 2, delim - 2);
        int open = 0;
        int close = 0;
        for (int64_t i = 0; i < netloc.len; i++) {
            if (netloc.data[i] == '[') {
                open++;
            }
            if (netloc.data[i] == ']') {
                close++;
            }
        }
        if (open != close) {
            free(text);
            ps_raise_cstr(PS_VALUE, "invalid IPv6 URL");
        }
        url += delim;
        un -= delim;
    }
    int64_t hash = -1;
    for (int64_t i = 0; i < un; i++) {
        if (url[i] == '#') {
            hash = i;
            break;
        }
    }
    if (hash >= 0) {
        free(fragment.data);
        ps_buf_copy(&fragment, url + hash + 1, un - hash - 1);
        un = hash;
    }
    int64_t q = -1;
    for (int64_t i = 0; i < un; i++) {
        if (url[i] == '?') {
            q = i;
            break;
        }
    }
    if (q >= 0) {
        free(query.data);
        ps_buf_copy(&query, url + q + 1, un - q - 1);
        un = q;
    }
    ps_buf_copy(&path, url, un);
    memcpy(raw, &scheme, 16);
    memcpy((char *)raw + 16, &netloc, 16);
    memcpy((char *)raw + 32, &path, 16);
    memcpy((char *)raw + 48, &query, 16);
    memcpy((char *)raw + 64, &fragment, 16);
    free(text);
}
