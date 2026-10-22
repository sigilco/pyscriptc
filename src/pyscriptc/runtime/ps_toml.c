/* tomllib.loads support: parse a TOML 1.0 document and re-emit it as JSON
text, which the json.loads machinery then reads into the annotated type. The
lowering is `json.loads(ps_toml_to_json(text))`, so the shape rules are the
json ones (int, float, bool, str, Optional, list, dict[str, ...]).

Covered: bare, quoted and dotted keys; basic, literal and multi-line strings;
decimal, hex, octal and binary integers with underscores; floats including
inf and nan; booleans; arrays; inline tables; [table] and [[array of tables]]
headers; comments. Date and time values raise ValueError (they have no JSON
form). Errors raise ValueError, the base class of tomllib.TOMLDecodeError. */

#include "ps_rt.h"

#include <errno.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum { TK_STR = 1, TK_NUM, TK_BOOL, TK_TABLE, TK_ARRAY };
enum { TF_DEFINED = 1, TF_INLINE = 2, TF_AOT = 4 };

typedef struct TNode {
    int kind;
    int flags;
    char *s; /* TK_STR bytes or TK_NUM/TK_BOOL JSON text */
    int64_t n;
    struct TNode **v; /* TK_TABLE values / TK_ARRAY items */
    char **k;         /* TK_TABLE keys */
    int64_t *kl;
    int64_t len;
    int64_t cap;
} TNode;

typedef struct TBytes {
    char *d;
    int64_t n;
    int64_t cap;
} TBytes;

typedef struct TParse {
    const char *p;
    int64_t n;
    int64_t i;
    TNode **nodes;
    int64_t nn;
    int64_t ncap;
    char **blobs;
    int64_t nb;
    int64_t bcap;
    PsGrow out;
    int depth;
} TParse;

static void t_free(TParse *t) {
    for (int64_t i = 0; i < t->nn; i++) {
        free(t->nodes[i]->v);
        free(t->nodes[i]->k);
        free(t->nodes[i]->kl);
        free(t->nodes[i]);
    }
    for (int64_t i = 0; i < t->nb; i++) {
        free(t->blobs[i]);
    }
    free(t->nodes);
    free(t->blobs);
    free(t->out.data);
    t->nodes = NULL;
    t->blobs = NULL;
    t->out.data = NULL;
    t->nn = t->nb = 0;
}

static void t_fail(TParse *t, const char *msg) {
    t_free(t);
    ps_raise_cstr(PS_VALUE, msg);
}

static void *t_realloc(TParse *t, void *p, size_t n) {
    void *q = realloc(p, n ? n : 1);
    if (q == NULL) {
        t_fail(t, "out of memory");
    }
    return q;
}

static TNode *t_node(TParse *t, int kind) {
    if (t->nn == t->ncap) {
        t->ncap = t->ncap ? t->ncap * 2 : 16;
        t->nodes = t_realloc(t, t->nodes, (size_t)t->ncap * sizeof(TNode *));
    }
    TNode *x = calloc(1, sizeof(TNode));
    if (x == NULL) {
        t_fail(t, "out of memory");
    }
    x->kind = kind;
    t->nodes[t->nn++] = x;
    return x;
}

static char *t_blob(TParse *t, TBytes *b) {
    /* Hand a finished byte buffer to the parse arena; always NUL-terminated. */
    if (b->d == NULL) {
        b->d = t_realloc(t, NULL, 1);
    }
    b->d[b->n] = 0;
    if (t->nb == t->bcap) {
        t->bcap = t->bcap ? t->bcap * 2 : 16;
        t->blobs = t_realloc(t, t->blobs, (size_t)t->bcap * sizeof(char *));
    }
    t->blobs[t->nb++] = b->d;
    return b->d;
}

static void b_add(TParse *t, TBytes *b, const char *p, int64_t n) {
    if (b->n + n + 1 > b->cap) {
        int64_t cap = b->cap ? b->cap : 32;
        while (cap < b->n + n + 1) {
            cap *= 2;
        }
        b->d = t_realloc(t, b->d, (size_t)cap);
        b->cap = cap;
    }
    if (n > 0) {
        memcpy(b->d + b->n, p, (size_t)n);
    }
    b->n += n;
}

static void b_byte(TParse *t, TBytes *b, char c) { b_add(t, b, &c, 1); }

static void b_utf8(TParse *t, TBytes *b, uint32_t cp) {
    char u[4];
    if (cp < 0x80) {
        u[0] = (char)cp;
        b_add(t, b, u, 1);
    } else if (cp < 0x800) {
        u[0] = (char)(0xC0 | (cp >> 6));
        u[1] = (char)(0x80 | (cp & 0x3F));
        b_add(t, b, u, 2);
    } else if (cp < 0x10000) {
        u[0] = (char)(0xE0 | (cp >> 12));
        u[1] = (char)(0x80 | ((cp >> 6) & 0x3F));
        u[2] = (char)(0x80 | (cp & 0x3F));
        b_add(t, b, u, 3);
    } else {
        u[0] = (char)(0xF0 | (cp >> 18));
        u[1] = (char)(0x80 | ((cp >> 12) & 0x3F));
        u[2] = (char)(0x80 | ((cp >> 6) & 0x3F));
        u[3] = (char)(0x80 | (cp & 0x3F));
        b_add(t, b, u, 4);
    }
}

static int t_eof(TParse *t) { return t->i >= t->n; }
static char t_cur(TParse *t) { return t->i < t->n ? t->p[t->i] : '\0'; }

static void t_ws(TParse *t) {
    while (t->i < t->n && (t->p[t->i] == ' ' || t->p[t->i] == '\t')) {
        t->i++;
    }
}

static void t_comment(TParse *t) {
    if (t_cur(t) == '#') {
        while (t->i < t->n && t->p[t->i] != '\n') {
            t->i++;
        }
    }
}

/* Whitespace, comments and newlines (inside arrays and between lines). */
static void t_skip_all(TParse *t) {
    for (;;) {
        t_ws(t);
        t_comment(t);
        if (t->i < t->n && (t->p[t->i] == '\n' || t->p[t->i] == '\r')) {
            t->i++;
            continue;
        }
        return;
    }
}

static void t_eol(TParse *t) {
    t_ws(t);
    t_comment(t);
    if (t_eof(t)) {
        return;
    }
    if (t->p[t->i] == '\r' && t->i + 1 < t->n && t->p[t->i + 1] == '\n') {
        t->i += 2;
        return;
    }
    if (t->p[t->i] == '\n') {
        t->i++;
        return;
    }
    t_fail(t, "TOML: expected end of line");
}

static int hexv(char c) {
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

static void t_unicode(TParse *t, TBytes *b, int digits) {
    uint32_t cp = 0;
    for (int d = 0; d < digits; d++) {
        int h = t->i < t->n ? hexv(t->p[t->i]) : -1;
        if (h < 0) {
            t_fail(t, "TOML: bad unicode escape");
        }
        cp = cp * 16 + (uint32_t)h;
        t->i++;
    }
    if (cp > 0x10FFFF || (cp >= 0xD800 && cp <= 0xDFFF)) {
        t_fail(t, "TOML: unicode escape is not a scalar value");
    }
    b_utf8(t, b, cp);
}

/* Parses a string starting at the opening quote; returns the arena bytes. */
static char *t_string(TParse *t, int64_t *outlen) {
    char q = t_cur(t);
    int multi = t->i + 2 < t->n && t->p[t->i + 1] == q && t->p[t->i + 2] == q;
    TBytes b = {NULL, 0, 0};
    t->i += multi ? 3 : 1;
    if (multi) {
        if (t_cur(t) == '\r' && t->i + 1 < t->n && t->p[t->i + 1] == '\n') {
            t->i += 2;
        } else if (t_cur(t) == '\n') {
            t->i++;
        }
    }
    for (;;) {
        if (t_eof(t)) {
            free(b.d);
            t_fail(t, "TOML: unterminated string");
        }
        char c = t->p[t->i];
        if (c == q) {
            if (!multi) {
                t->i++;
                break;
            }
            int run = 0;
            while (t->i + run < t->n && t->p[t->i + run] == q) {
                run++;
            }
            if (run >= 3) {
                if (run > 5) {
                    free(b.d);
                    t_fail(t, "TOML: bad closing quotes");
                }
                for (int r = 0; r < run - 3; r++) {
                    b_byte(t, &b, q);
                }
                t->i += run;
                break;
            }
            for (int r = 0; r < run; r++) {
                b_byte(t, &b, q);
            }
            t->i += run;
            continue;
        }
        if (c == '\n' && !multi) {
            free(b.d);
            t_fail(t, "TOML: newline in string");
        }
        if (c == '\\' && q == '"') {
            t->i++;
            if (t_eof(t)) {
                free(b.d);
                t_fail(t, "TOML: bad escape");
            }
            char e = t->p[t->i++];
            if (e == 'b') {
                b_byte(t, &b, '\b');
            } else if (e == 't') {
                b_byte(t, &b, '\t');
            } else if (e == 'n') {
                b_byte(t, &b, '\n');
            } else if (e == 'f') {
                b_byte(t, &b, '\f');
            } else if (e == 'r') {
                b_byte(t, &b, '\r');
            } else if (e == '"' || e == '\\') {
                b_byte(t, &b, e);
            } else if (e == 'u') {
                t_unicode(t, &b, 4);
            } else if (e == 'U') {
                t_unicode(t, &b, 8);
            } else if (multi && (e == ' ' || e == '\t' || e == '\n' || e == '\r')) {
                /* line-ending backslash: drop it and all whitespace after */
                t->i--;
                int64_t j = t->i;
                while (j < t->n && (t->p[j] == ' ' || t->p[j] == '\t')) {
                    j++;
                }
                if (j >= t->n || (t->p[j] != '\n' && t->p[j] != '\r')) {
                    free(b.d);
                    t_fail(t, "TOML: bad escape");
                }
                while (t->i < t->n &&
                       (t->p[t->i] == ' ' || t->p[t->i] == '\t' || t->p[t->i] == '\n' || t->p[t->i] == '\r')) {
                    t->i++;
                }
            } else {
                free(b.d);
                t_fail(t, "TOML: bad escape");
            }
            continue;
        }
        if ((unsigned char)c < 0x20 && c != '\t' && c != '\n' && c != '\r') {
            free(b.d);
            t_fail(t, "TOML: control character in string");
        }
        b_byte(t, &b, c);
        t->i++;
    }
    *outlen = b.n;
    return t_blob(t, &b);
}

static int bare_char(char c) {
    return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_' || c == '-';
}

typedef struct TKey {
    char *s;
    int64_t n;
} TKey;

/* One key part. */
static TKey t_key_part(TParse *t) {
    TKey k;
    char c = t_cur(t);
    if (c == '"' || c == '\'') {
        k.s = t_string(t, &k.n);
        return k;
    }
    int64_t a = t->i;
    while (t->i < t->n && bare_char(t->p[t->i])) {
        t->i++;
    }
    if (t->i == a) {
        t_fail(t, "TOML: expected a key");
    }
    TBytes b = {NULL, 0, 0};
    b_add(t, &b, t->p + a, t->i - a);
    k.n = b.n;
    k.s = t_blob(t, &b);
    return k;
}

#define T_MAXKEY 32

static int t_key_path(TParse *t, TKey *parts) {
    int n = 0;
    for (;;) {
        t_ws(t);
        if (n == T_MAXKEY) {
            t_fail(t, "TOML: key path too deep");
        }
        parts[n++] = t_key_part(t);
        t_ws(t);
        if (t_cur(t) == '.') {
            t->i++;
            continue;
        }
        return n;
    }
}

static TNode *t_find(TNode *tab, TKey k) {
    for (int64_t i = 0; i < tab->len; i++) {
        if (tab->kl[i] == k.n && memcmp(tab->k[i], k.s, (size_t)k.n) == 0) {
            return tab->v[i];
        }
    }
    return NULL;
}

static void t_push(TParse *t, TNode *c, TNode *item, const TKey *k) {
    if (c->len == c->cap) {
        c->cap = c->cap ? c->cap * 2 : 4;
        c->v = t_realloc(t, c->v, (size_t)c->cap * sizeof(TNode *));
        if (k != NULL) {
            c->k = t_realloc(t, c->k, (size_t)c->cap * sizeof(char *));
            c->kl = t_realloc(t, c->kl, (size_t)c->cap * sizeof(int64_t));
        }
    }
    c->v[c->len] = item;
    if (k != NULL) {
        c->k[c->len] = k->s;
        c->kl[c->len] = k->n;
    }
    c->len++;
}

/* Walk the leading parts of a path, creating implicit tables. An array of
tables descends into its last element. */
static TNode *t_walk(TParse *t, TNode *cur, const TKey *parts, int count) {
    for (int i = 0; i < count; i++) {
        TNode *nx = t_find(cur, parts[i]);
        if (nx == NULL) {
            nx = t_node(t, TK_TABLE);
            t_push(t, cur, nx, &parts[i]);
        } else if (nx->kind == TK_ARRAY && (nx->flags & TF_AOT) && nx->len > 0) {
            nx = nx->v[nx->len - 1];
        }
        if (nx->kind != TK_TABLE || (nx->flags & TF_INLINE)) {
            t_fail(t, "TOML: key path crosses a value");
        }
        cur = nx;
    }
    return cur;
}

static TNode *t_value(TParse *t);

static void t_num_text(TParse *t, TNode *x, const char *s, int64_t n) {
    TBytes b = {NULL, 0, 0};
    b_add(t, &b, s, n);
    x->s = t_blob(t, &b);
    x->n = b.n;
}

static TNode *t_number(TParse *t) {
    int64_t a = t->i;
    while (t->i < t->n) {
        char c = t->p[t->i];
        if (c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == ',' || c == ']' || c == '}' || c == '#') {
            break;
        }
        t->i++;
    }
    int64_t len = t->i - a;
    if (len == 0 || len > 80) {
        t_fail(t, "TOML: bad value");
    }
    char tok[96];
    int64_t m = 0;
    int digit_prev = 0;
    const char *raw = t->p + a;
    /* A date or time has a '-' after four digits or ':' after two. */
    if ((len >= 5 && raw[4] == '-' && raw[0] >= '0' && raw[0] <= '9') || (len >= 3 && raw[2] == ':')) {
        t_fail(t, "TOML: date and time values are not lowered");
    }
    for (int64_t i = 0; i < len; i++) {
        if (raw[i] == '_') {
            int next_digit = i + 1 < len && hexv(raw[i + 1]) >= 0;
            if (!digit_prev || !next_digit) {
                t_fail(t, "TOML: bad underscore in number");
            }
            digit_prev = 0;
            continue;
        }
        digit_prev = hexv(raw[i]) >= 0;
        tok[m++] = raw[i];
    }
    tok[m] = 0;
    TNode *x = t_node(t, TK_NUM);
    const char *s = tok;
    int neg = 0;
    if (*s == '+' || *s == '-') {
        neg = *s == '-';
        s++;
    }
    char out[96];
    if (strcmp(s, "inf") == 0) {
        t_num_text(t, x, neg ? "-Infinity" : "Infinity", neg ? 9 : 8);
        return x;
    }
    if (strcmp(s, "nan") == 0) {
        t_num_text(t, x, "NaN", 3);
        return x;
    }
    if (s[0] == '0' && (s[1] == 'x' || s[1] == 'o' || s[1] == 'b')) {
        if (tok[0] == '+' || tok[0] == '-') {
            t_fail(t, "TOML: bad integer");
        }
        int base = s[1] == 'x' ? 16 : (s[1] == 'o' ? 8 : 2);
        const char *d = s + 2;
        if (*d == 0) {
            t_fail(t, "TOML: bad integer");
        }
        unsigned long long v = 0;
        for (; *d; d++) {
            int h = hexv(*d);
            if (h < 0 || h >= base || v > (unsigned long long)INT64_MAX / (unsigned)base) {
                t_fail(t, "TOML: bad integer");
            }
            v = v * (unsigned)base + (unsigned)h;
            if (v > (unsigned long long)INT64_MAX) {
                t_fail(t, "TOML: integer out of range");
            }
        }
        int w = snprintf(out, sizeof out, "%llu", v);
        t_num_text(t, x, out, w);
        return x;
    }
    int is_float = 0;
    int ndig = 0;
    for (const char *d = s; *d; d++) {
        if (*d == '.' || *d == 'e' || *d == 'E') {
            is_float = 1;
        } else if (*d >= '0' && *d <= '9') {
            ndig++;
        } else if ((*d == '+' || *d == '-') && (d[-1] == 'e' || d[-1] == 'E')) {
            continue;
        } else {
            t_fail(t, "TOML: bad value");
        }
    }
    if (ndig == 0 || s[0] == '.' || s[0] == 'e' || s[0] == 'E') {
        t_fail(t, "TOML: bad number");
    }
    if (!is_float) {
        if (s[0] == '0' && s[1] != 0) {
            t_fail(t, "TOML: leading zero in integer");
        }
        char *end = NULL;
        errno = 0;
        long long v = strtoll(tok, &end, 10);
        if (*end != 0 || errno != 0) {
            t_fail(t, "TOML: integer out of range");
        }
        int w = snprintf(out, sizeof out, "%lld", v);
        t_num_text(t, x, out, w);
        return x;
    }
    /* Float: a '.' needs digits on both sides; no leading zeros. */
    for (const char *d = s; *d; d++) {
        if (*d == '.' && (d == s || d[1] < '0' || d[1] > '9' || d[-1] < '0' || d[-1] > '9')) {
            t_fail(t, "TOML: bad float");
        }
    }
    if (s[0] == '0' && s[1] >= '0' && s[1] <= '9') {
        t_fail(t, "TOML: leading zero in float");
    }
    char *end = NULL;
    double v = strtod(tok, &end);
    if (*end != 0) {
        t_fail(t, "TOML: bad float");
    }
    if (isinf(v)) {
        t_num_text(t, x, v < 0 ? "-Infinity" : "Infinity", v < 0 ? 9 : 8);
        return x;
    }
    int w = snprintf(out, sizeof out, "%.17g", v);
    int plain = 1;
    for (int i = 0; i < w; i++) {
        if (out[i] == '.' || out[i] == 'e') {
            plain = 0;
        }
    }
    if (plain) {
        out[w++] = '.';
        out[w++] = '0';
        out[w] = 0;
    }
    t_num_text(t, x, out, w);
    return x;
}

static TNode *t_inline_table(TParse *t) {
    TNode *tab = t_node(t, TK_TABLE);
    tab->flags = TF_INLINE | TF_DEFINED;
    t->i++;
    t_ws(t);
    if (t_cur(t) == '}') {
        t->i++;
        return tab;
    }
    for (;;) {
        TKey parts[T_MAXKEY];
        int n = t_key_path(t, parts);
        t_ws(t);
        if (t_cur(t) != '=') {
            t_fail(t, "TOML: expected '='");
        }
        t->i++;
        t_ws(t);
        TNode *val = t_value(t);
        TNode *dst = t_walk(t, tab, parts, n - 1);
        if (t_find(dst, parts[n - 1]) != NULL) {
            t_fail(t, "TOML: duplicate key");
        }
        t_push(t, dst, val, &parts[n - 1]);
        t_ws(t);
        if (t_cur(t) == ',') {
            t->i++;
            t_ws(t);
            continue;
        }
        if (t_cur(t) == '}') {
            t->i++;
            return tab;
        }
        t_fail(t, "TOML: bad inline table");
    }
}

static TNode *t_array(TParse *t) {
    TNode *arr = t_node(t, TK_ARRAY);
    t->i++;
    for (;;) {
        t_skip_all(t);
        if (t_cur(t) == ']') {
            t->i++;
            return arr;
        }
        t_push(t, arr, t_value(t), NULL);
        t_skip_all(t);
        if (t_cur(t) == ',') {
            t->i++;
            continue;
        }
        if (t_cur(t) == ']') {
            t->i++;
            return arr;
        }
        t_fail(t, "TOML: bad array");
    }
}

static TNode *t_value(TParse *t) {
    if (++t->depth > 128) {
        t_fail(t, "TOML: nesting too deep");
    }
    TNode *x;
    char c = t_cur(t);
    if (c == '"' || c == '\'') {
        x = t_node(t, TK_STR);
        x->s = t_string(t, &x->n);
    } else if (c == '[') {
        x = t_array(t);
    } else if (c == '{') {
        x = t_inline_table(t);
    } else if (t->i + 4 <= t->n && memcmp(t->p + t->i, "true", 4) == 0 &&
               (t->i + 4 == t->n || !bare_char(t->p[t->i + 4]))) {
        x = t_node(t, TK_BOOL);
        x->s = "true";
        x->n = 4;
        t->i += 4;
    } else if (t->i + 5 <= t->n && memcmp(t->p + t->i, "false", 5) == 0 &&
               (t->i + 5 == t->n || !bare_char(t->p[t->i + 5]))) {
        x = t_node(t, TK_BOOL);
        x->s = "false";
        x->n = 5;
        t->i += 5;
    } else {
        x = t_number(t);
    }
    t->depth--;
    return x;
}

static void t_header(TParse *t, TNode *root, TNode **cur) {
    int aot = t->i + 1 < t->n && t->p[t->i + 1] == '[';
    TKey parts[T_MAXKEY];
    t->i += aot ? 2 : 1;
    int n = t_key_path(t, parts);
    t_ws(t);
    if (t_cur(t) != ']' || (aot && !(t->i + 1 < t->n && t->p[t->i + 1] == ']'))) {
        t_fail(t, "TOML: bad table header");
    }
    t->i += aot ? 2 : 1;
    TNode *parent = t_walk(t, root, parts, n - 1);
    TNode *ex = t_find(parent, parts[n - 1]);
    if (aot) {
        if (ex == NULL) {
            ex = t_node(t, TK_ARRAY);
            ex->flags = TF_AOT;
            t_push(t, parent, ex, &parts[n - 1]);
        } else if (ex->kind != TK_ARRAY || !(ex->flags & TF_AOT)) {
            t_fail(t, "TOML: array of tables clashes with a value");
        }
        TNode *item = t_node(t, TK_TABLE);
        item->flags = TF_DEFINED;
        t_push(t, ex, item, NULL);
        *cur = item;
    } else {
        if (ex == NULL) {
            ex = t_node(t, TK_TABLE);
            t_push(t, parent, ex, &parts[n - 1]);
        } else if (ex->kind != TK_TABLE || (ex->flags & (TF_DEFINED | TF_INLINE))) {
            t_fail(t, "TOML: table defined twice");
        }
        ex->flags |= TF_DEFINED;
        *cur = ex;
    }
    t_eol(t);
}

static void j_str(TParse *t, const char *s, int64_t n) {
    ps_grow_byte(&t->out, '"');
    for (int64_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)s[i];
        if (c == '"' || c == '\\') {
            ps_grow_byte(&t->out, '\\');
            ps_grow_byte(&t->out, c);
        } else if (c < 0x20) {
            char tmp[8];
            snprintf(tmp, sizeof tmp, "\\u%04x", c);
            ps_grow_add(&t->out, tmp, 6);
        } else {
            ps_grow_byte(&t->out, c);
        }
    }
    ps_grow_byte(&t->out, '"');
}

static void j_emit(TParse *t, TNode *x) {
    if (x->kind == TK_STR) {
        j_str(t, x->s, x->n);
    } else if (x->kind == TK_NUM || x->kind == TK_BOOL) {
        ps_grow_add(&t->out, x->s, x->n);
    } else if (x->kind == TK_ARRAY) {
        ps_grow_byte(&t->out, '[');
        for (int64_t i = 0; i < x->len; i++) {
            if (i) {
                ps_grow_byte(&t->out, ',');
            }
            j_emit(t, x->v[i]);
        }
        ps_grow_byte(&t->out, ']');
    } else {
        ps_grow_byte(&t->out, '{');
        for (int64_t i = 0; i < x->len; i++) {
            if (i) {
                ps_grow_byte(&t->out, ',');
            }
            j_str(t, x->k[i], x->kl[i]);
            ps_grow_byte(&t->out, ':');
            j_emit(t, x->v[i]);
        }
        ps_grow_byte(&t->out, '}');
    }
}

void ps_toml_to_json(int64_t n, const char *p, PsBuf *out) {
    TParse t;
    memset(&t, 0, sizeof t);
    t.p = p;
    t.n = n;
    ps_grow_init(&t.out);
    TNode *root = t_node(&t, TK_TABLE);
    root->flags = TF_DEFINED;
    TNode *cur = root;
    for (;;) {
        t_skip_all(&t);
        if (t_eof(&t)) {
            break;
        }
        if (t_cur(&t) == '[') {
            t_header(&t, root, &cur);
            continue;
        }
        TKey parts[T_MAXKEY];
        int cnt = t_key_path(&t, parts);
        t_ws(&t);
        if (t_cur(&t) != '=') {
            t_fail(&t, "TOML: expected '='");
        }
        t.i++;
        t_ws(&t);
        t.depth = 0;
        TNode *val = t_value(&t);
        TNode *dst = t_walk(&t, cur, parts, cnt - 1);
        if (t_find(dst, parts[cnt - 1]) != NULL) {
            t_fail(&t, "TOML: duplicate key");
        }
        t_push(&t, dst, val, &parts[cnt - 1]);
        t_eol(&t);
    }
    j_emit(&t, root);
    ps_grow_finish(&t.out, out);
    t_free(&t);
}
