/* yaml.safe_load support: parse the documented YAML subset and re-emit it as
JSON text, which the json.loads machinery then reads into the annotated type.
The lowering is `json.loads(ps_yaml_to_json(text))`, so the shape rules are
the json ones (int, float, str, Optional, list, dict[str, ...]; bool follows
the same list[bool]/dict-bool-value refusal as json.loads, PS2002).

Closed subset (issue #254):
- block mappings (`key: value`, one per line) and block sequences
  (`- item`), nested by indentation;
- flow collections on one line (`[a, b]`, `{k: v}`);
- plain scalars: integers, floats, `true`, `false`, `null`/`~`/empty (JSON
  null), everything else a string;
- single- and double-quoted scalars, with YAML double-quote escapes;
- `#` comments (full-line and trailing after a space) and blank lines;
- documents with no `---` marker; `---` starts the document, `...` ends it.
`null` maps to JSON null, so the annotation must be Optional where CPython
can return None.

Everything else fences at the use site (PS2001) or in `_yaml_call`
(PS2001/PS2003): anchors and aliases, block scalars (`|`, `>`), flow
collections across lines, tags, multiple documents, complex keys (`? `),
tabs for indentation, `!!python/...` tags. The subset is deliberately much
smaller than PyYAML: the fence is the answer, not a best-effort parse.

Errors raise ValueError, the base class of yaml.YAMLError. */

#include "ps_rt.h"

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum { Y_STR = 1, Y_NUM, Y_BOOL, Y_NULL, Y_TABLE, Y_ARRAY };

typedef struct YNode {
    int kind;
    char *s; /* Y_STR bytes or Y_NUM/Y_BOOL JSON text */
    int64_t n;
    struct YNode **v; /* Y_TABLE values / Y_ARRAY items */
    char **k;         /* Y_TABLE keys */
    int64_t *kl;
    int64_t len;
    int64_t cap;
} YNode;

typedef struct YParse {
    const char *p;
    int64_t n;
    int64_t i;
    YNode **nodes;
    int64_t nn;
    int64_t ncap;
    char **blobs;
    int64_t nb;
    int64_t bcap;
    PsGrow out;
    int depth;
} YParse;

static void y_fail(YParse *t, const char *msg) {
    for (int64_t j = 0; j < t->nn; j++) {
        free(t->nodes[j]->v);
        free(t->nodes[j]->k);
        free(t->nodes[j]->kl);
        free(t->nodes[j]);
    }
    for (int64_t j = 0; j < t->nb; j++) {
        free(t->blobs[j]);
    }
    free(t->nodes);
    free(t->blobs);
    free(t->out.data);
    ps_raise_cstr(PS_VALUE, msg);
}

static void *y_realloc(YParse *t, void *p, size_t sz) {
    void *q = realloc(p, sz ? sz : 1);
    if (q == NULL) {
        y_fail(t, "out of memory");
    }
    return q;
}

static YNode *y_node(YParse *t, int kind) {
    if (t->nn == t->ncap) {
        t->ncap = t->ncap ? t->ncap * 2 : 16;
        t->nodes = y_realloc(t, t->nodes, (size_t)t->ncap * sizeof(YNode *));
    }
    YNode *x = calloc(1, sizeof(YNode));
    if (x == NULL) {
        y_fail(t, "out of memory");
    }
    x->kind = kind;
    t->nodes[t->nn++] = x;
    return x;
}

static char *y_blob(YParse *t, const char *p, int64_t n) {
    char *b = y_realloc(t, NULL, (size_t)n + 1);
    if (n > 0) {
        memcpy(b, p, (size_t)n);
    }
    b[n] = 0;
    if (t->nb == t->bcap) {
        t->bcap = t->bcap ? t->bcap * 2 : 16;
        t->blobs = y_realloc(t, t->blobs, (size_t)t->bcap * sizeof(char *));
    }
    t->blobs[t->nb++] = b;
    return b;
}

static void y_push(YParse *t, YNode *c, YNode *item, const char *k, int64_t kl) {
    if (c->len == c->cap) {
        c->cap = c->cap ? c->cap * 2 : 4;
        c->v = y_realloc(t, c->v, (size_t)c->cap * sizeof(YNode *));
        if (k != NULL) {
            c->k = y_realloc(t, c->k, (size_t)c->cap * sizeof(char *));
            c->kl = y_realloc(t, c->kl, (size_t)c->cap * sizeof(int64_t));
        }
    }
    c->v[c->len] = item;
    if (k != NULL) {
        c->k[c->len] = (char *)k;
        c->kl[c->len] = kl;
    }
    c->len++;
}

static int y_eof(YParse *t) { return t->i >= t->n; }

static int y_hex(char c) {
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

typedef struct YBytes {
    char *d;
    int64_t n;
    int64_t cap;
} YBytes;

static void yb_add(YParse *t, YBytes *b, char c) {
    if (b->n + 1 > b->cap) {
        b->cap = b->cap ? b->cap * 2 : 32;
        b->d = y_realloc(t, b->d, (size_t)b->cap);
    }
    b->d[b->n++] = c;
}

/* Parses a quoted scalar starting at the opening quote; returns arena bytes.
   Single quotes double to escape; double quotes take YAML escapes. Only
   Latin-1 results: a \\x, \\u or \\N escape above 0x7F is refused, matching
   the json.loads text model. */
static char *y_string(YParse *t, int64_t *outlen) {
    char q = t->p[t->i];
    t->i++;
    YBytes b = {NULL, 0, 0};
    while (!y_eof(t)) {
        char c = t->p[t->i];
        if (c == q) {
            if (q == '\'' && t->i + 1 < t->n && t->p[t->i + 1] == '\'') {
                yb_add(t, &b, '\'');
                t->i += 2;
                continue;
            }
            t->i++;
            if (b.d == NULL) {
                b.d = y_realloc(t, NULL, 1);
            }
            b.d[b.n] = 0;
            *outlen = b.n;
            return y_blob(t, b.d, b.n);
        }
        if (c == '\n') {
            y_fail(t, "YAML: newline in a quoted scalar");
        }
        if (q == '"' && c == '\\') {
            t->i++;
            if (y_eof(t)) {
                y_fail(t, "YAML: bad escape");
            }
            char e = t->p[t->i++];
            if (e == '0' || e == 'N') {
                yb_add(t, &b, 0);
            } else if (e == 'a') {
                yb_add(t, &b, '\a');
            } else if (e == 'b') {
                yb_add(t, &b, '\b');
            } else if (e == 't' || e == '\t') {
                yb_add(t, &b, '\t');
            } else if (e == 'n') {
                yb_add(t, &b, '\n');
            } else if (e == 'v') {
                yb_add(t, &b, '\v');
            } else if (e == 'f') {
                yb_add(t, &b, '\f');
            } else if (e == 'r') {
                yb_add(t, &b, '\r');
            } else if (e == 'e') {
                yb_add(t, &b, 27);
            } else if (e == ' ' || e == '"' || e == '/' || e == '\\') {
                yb_add(t, &b, e);
            } else if (e == 'x' || e == 'u' || e == 'U') {
                int digits = e == 'x' ? 2 : (e == 'u' ? 4 : 8);
                unsigned cp = 0;
                for (int d = 0; d < digits; d++) {
                    int h = y_eof(t) ? -1 : y_hex(t->p[t->i]);
                    if (h < 0) {
                        y_fail(t, "YAML: bad unicode escape");
                    }
                    cp = cp * 16 + (unsigned)h;
                    t->i++;
                }
                if (cp > 0x7F) {
                    y_fail(t, "YAML: non-ASCII escape is not lowered");
                }
                yb_add(t, &b, (char)cp);
            } else {
                y_fail(t, "YAML: bad escape");
            }
            continue;
        }
        if ((unsigned char)c < 0x20 && c != '\t') {
            y_fail(t, "YAML: control character in a quoted scalar");
        }
        yb_add(t, &b, c);
        t->i++;
    }
    y_fail(t, "YAML: unterminated quoted scalar");
    return NULL;
}

/* Plain-scalar bytes for a value or key: to the end of line or, in flow
   context, to , ] }. A `: ` (colon before a space or end of line) ends the
   scalar, as in YAML; a colon inside text (`http://x`) does not. Trailing
   spaces are trimmed; a ` #` starts a comment. */
static char *y_plain(YParse *t, int flow, int64_t *outlen) {
    int64_t a = t->i;
    int64_t end = a;
    while (!y_eof(t)) {
        char c = t->p[t->i];
        if (c == '\n' || c == '\r') {
            break;
        }
        if (flow && (c == ',' || c == ']' || c == '}')) {
            break;
        }
        if (c == ':' && (t->i + 1 >= t->n || t->p[t->i + 1] == ' ' || t->p[t->i + 1] == '\t' ||
                         t->p[t->i + 1] == '\n' || t->p[t->i + 1] == '\r')) {
            break;
        }
        if (c == '#' && t->i > a && (t->p[t->i - 1] == ' ' || t->p[t->i - 1] == '\t')) {
            break;
        }
        t->i++;
        if (c != ' ' && c != '\t') {
            end = t->i;
        }
    }
    if (end == a) {
        y_fail(t, "YAML: empty plain scalar");
    }
    *outlen = end - a;
    return y_blob(t, t->p + a, end - a);
}

/* The scalar kinds a plain text can resolve to, PyYAML SafeLoader order. */
enum { R_STR = 0, R_INT, R_FLOAT, R_BOOL, R_NULL, R_TIME };

static int y_is_digit(char c) { return c >= '0' && c <= '9'; }

static int y_word_is(const char *s, int64_t n, const char *word) {
    return (int64_t)strlen(word) == n && memcmp(s, word, (size_t)n) == 0;
}

static int y_word3(const char *s, int64_t n, const char *a, const char *b, const char *c) {
    return y_word_is(s, n, a) || y_word_is(s, n, b) || y_word_is(s, n, c);
}

/* A timestamp: PyYAML resolves it to a date object, so the subset refuses
   it (the value model has no date). */
static int y_is_timestamp(const char *s, int64_t n) {
    if (n >= 10 && y_is_digit(s[0]) && y_is_digit(s[1]) && y_is_digit(s[2]) && y_is_digit(s[3]) && s[4] == '-' &&
        y_is_digit(s[5]) && y_is_digit(s[6]) && s[7] == '-' && y_is_digit(s[8]) && y_is_digit(s[9])) {
        return 1;
    }
    return 0;
}

/* A sexagesimal number: PyYAML resolves it to an int or a float. */
static int y_is_sexagesimal(const char *s, int64_t n) {
    int64_t i = 0;
    if (i < n && (s[i] == '+' || s[i] == '-')) {
        i++;
    }
    int ndig = 0;
    while (i < n && y_is_digit(s[i])) {
        i++;
        ndig++;
    }
    if (ndig == 0 || i >= n || s[i] != ':') {
        return 0;
    }
    while (i < n && s[i] == ':') {
        i++;
        int grp = 0;
        while (i < n && y_is_digit(s[i])) {
            i++;
            grp++;
        }
        if (grp == 0 || grp > 2) {
            return 0;
        }
    }
    if (i < n && s[i] == '.') {
        i++;
        while (i < n && y_is_digit(s[i])) {
            i++;
        }
    }
    return i == n;
}

/* Resolves a plain scalar the way PyYAML's SafeLoader does; on a number the
   JSON text lands in numbuf. */
static int y_resolve(const char *s, int64_t n, char *numbuf, int64_t *numn) {
    if (n == 0) {
        return R_NULL;
    }
    if (y_word_is(s, n, "~") || y_word3(s, n, "null", "Null", "NULL")) {
        return R_NULL;
    }
    if (y_word3(s, n, "true", "True", "TRUE") || y_word3(s, n, "yes", "Yes", "YES") ||
        y_word3(s, n, "on", "On", "ON")) {
        return R_BOOL;
    }
    if (y_word3(s, n, "false", "False", "FALSE") || y_word3(s, n, "no", "No", "NO") ||
        y_word3(s, n, "off", "Off", "OFF")) {
        return R_BOOL;
    }
    if (y_is_timestamp(s, n)) {
        return R_TIME;
    }
    if (y_is_sexagesimal(s, n)) {
        return R_TIME;
    }
    /* integer: sign, then 0b binary, 0x hex, leading-0 octal or decimal,
       underscores allowed between digits */
    {
        int64_t i = 0;
        int neg = 0;
        if (s[i] == '+' || s[i] == '-') {
            neg = s[i] == '-';
            i++;
        }
        int base = 10;
        if (i + 1 < n && s[i] == '0' && (s[i + 1] == 'b' || s[i + 1] == 'B')) {
            base = 2;
            i += 2;
        } else if (i + 1 < n && s[i] == '0' && (s[i + 1] == 'x' || s[i + 1] == 'X')) {
            base = 16;
            i += 2;
        } else if (i + 1 < n && s[i] == '0' && y_is_digit(s[i + 1])) {
            base = 8;
            i += 1;
        }
        int ndig = 0;
        int state = 0; /* 0: start, 1: after a digit, 2: after an underscore */
        unsigned long long v = 0;
        int overflow = 0;
        for (; i < n; i++) {
            char c = s[i];
            if (c == '_') {
                if (state != 1) {
                    break; /* an underscore must sit between two digits */
                }
                state = 2;
                continue;
            }
            int h = y_hex(c);
            if (h < 0 || h >= base) {
                break;
            }
            state = 1;
            ndig++;
            v = v * (unsigned)base + (unsigned)h;
            if (v > (unsigned long long)INT64_MAX) {
                overflow = 1;
            }
        }
        int consumed = i == n;
        if (ndig > 0 && consumed && state == 1 && !overflow) {
            if (neg) {
                *numn = snprintf(numbuf, 64, "-%llu", v);
            } else {
                *numn = snprintf(numbuf, 64, "%llu", v);
            }
            return R_INT;
        }
        if (ndig > 0 && consumed && state != 1) {
            return R_STR; /* a dangling underscore */
        }
    }
    /* float: the YAML grammar wants a dot (so `1e3` stays a string) and an
       explicitly signed exponent; .inf and .nan are spelled with a dot. */
    {
        int64_t i = 0;
        int neg = 0;
        if (s[i] == '+' || s[i] == '-') {
            neg = s[i] == '-';
            i++;
        }
        if (i < n && s[i] == '.') {
            if (y_word3(s + i, n - i, ".inf", ".Inf", ".INF")) {
                *numn = snprintf(numbuf, 64, "%s", neg ? "-Infinity" : "Infinity");
                return R_FLOAT;
            }
            if (y_word3(s + i, n - i, ".nan", ".NaN", ".NAN")) {
                *numn = snprintf(numbuf, 64, "NaN");
                return R_FLOAT;
            }
            /* .5 style: a dot then digits */
            int64_t j = i + 1;
            int ndig = 0;
            while (j < n && (y_is_digit(s[j]) || s[j] == '_')) {
                if (y_is_digit(s[j])) {
                    ndig++;
                }
                j++;
            }
            if (ndig == 0) {
                return R_STR;
            }
            if (j < n && (s[j] == 'e' || s[j] == 'E')) {
                int64_t k = j + 1;
                if (k >= n || (s[k] != '+' && s[k] != '-')) {
                    return R_STR; /* the YAML float grammar wants a signed exponent */
                }
                k++;
                int edig = 0;
                while (k < n && y_is_digit(s[k])) {
                    k++;
                    edig++;
                }
                if (edig == 0 || k != n) {
                    return R_STR;
                }
                j = k;
            }
            if (j != n) {
                return R_STR;
            }
            char tmp[128];
            int64_t w = 0;
            for (int64_t q = 0; q < n; q++) {
                if (s[q] != '_' && w < (int64_t)sizeof(tmp) - 1) {
                    tmp[w++] = s[q];
                }
            }
            tmp[w] = 0;
            *numn = snprintf(numbuf, 64, "%.17g", strtod(tmp, NULL));
            return R_FLOAT;
        }
        int ndig = 0;
        while (i < n && (y_is_digit(s[i]) || s[i] == '_')) {
            if (y_is_digit(s[i])) {
                ndig++;
            }
            i++;
        }
        if (ndig == 0 || i >= n || s[i] != '.') {
            return R_STR;
        }
        i++;
        while (i < n && (y_is_digit(s[i]) || s[i] == '_')) {
            if (y_is_digit(s[i])) {
                ndig++;
            }
            i++;
        }
        if (i < n && (s[i] == 'e' || s[i] == 'E')) {
            int64_t k = i + 1;
            if (k >= n || (s[k] != '+' && s[k] != '-')) {
                return R_STR; /* the YAML float grammar wants a signed exponent */
            }
            k++;
            int edig = 0;
            while (k < n && y_is_digit(s[k])) {
                k++;
                edig++;
            }
            if (edig == 0 || k != n) {
                return R_STR;
            }
            i = k;
        }
        if (i != n) {
            return R_STR;
        }
        char tmp[128];
        int64_t w = 0;
        for (int64_t q = 0; q < n; q++) {
            if (s[q] != '_' && w < (int64_t)sizeof(tmp) - 1) {
                tmp[w++] = s[q];
            }
        }
        tmp[w] = 0;
        *numn = snprintf(numbuf, 64, "%.17g", strtod(tmp, NULL));
        return R_FLOAT;
    }
}

/* Classifies plain text as a scalar node. */
static YNode *y_scalar(YParse *t, const char *s, int64_t n) {
    char numbuf[128];
    int64_t numn = 0;
    int kind = y_resolve(s, n, numbuf, &numn);
    if (kind == R_TIME) {
        y_fail(t, "YAML: timestamp and sexagesimal values are not in the subset");
    }
    if (kind == R_BOOL) {
        int on = y_word3(s, n, "true", "True", "TRUE") || y_word3(s, n, "yes", "Yes", "YES") ||
                 y_word3(s, n, "on", "On", "ON");
        YNode *x = y_node(t, Y_BOOL);
        if (on) {
            x->s = (char *)y_blob(t, "true", 4);
            x->n = 4;
        } else {
            x->s = (char *)y_blob(t, "false", 5);
            x->n = 5;
        }
        return x;
    }
    if (kind == R_NULL) {
        return y_node(t, Y_NULL);
    }
    if (kind == R_INT || kind == R_FLOAT) {
        YNode *x = y_node(t, Y_NUM);
        x->s = y_blob(t, numbuf, numn);
        x->n = numn;
        return x;
    }
    YNode *x = y_node(t, Y_STR);
    x->s = (char *)s;
    x->n = n;
    return x;
}

/* True when the plain text resolves to a string (a usable mapping key). */
static int y_is_string_key(const char *s, int64_t n) {
    char numbuf[128];
    int64_t numn = 0;
    return y_resolve(s, n, numbuf, &numn) == R_STR;
}

static YNode *y_value(YParse *t, int flow);
static int y_is_string_key(const char *s, int64_t n);

static YNode *y_flow(YParse *t, YNode *x) {
    char open = t->p[t->i];
    int arr = open == '[';
    t->i++;
    x->kind = arr ? Y_ARRAY : Y_TABLE;
    if (++t->depth > 64) {
        y_fail(t, "YAML: flow collection nested too deep");
    }
    for (;;) {
        while (!y_eof(t) && (t->p[t->i] == ' ' || t->p[t->i] == '\t')) {
            t->i++;
        }
        if (y_eof(t)) {
            y_fail(t, "YAML: flow collection is not closed");
        }
        char c = t->p[t->i];
        if (c == (arr ? ']' : '}')) {
            t->i++;
            t->depth--;
            return x;
        }
        if (!arr) {
            char *k;
            int64_t kl;
            if (c == '"' || c == '\'') {
                k = y_string(t, &kl);
            } else {
                k = y_plain(t, 1, &kl);
                if (!y_is_string_key(k, kl)) {
                    y_fail(t, "YAML: only string keys are in the subset");
                }
            }
            while (!y_eof(t) && (t->p[t->i] == ' ' || t->p[t->i] == '\t')) {
                t->i++;
            }
            if (y_eof(t) || t->p[t->i] != ':') {
                y_fail(t, "YAML: flow mapping expects 'key:'");
            }
            t->i++;
            while (!y_eof(t) && (t->p[t->i] == ' ' || t->p[t->i] == '\t')) {
                t->i++;
            }
            YNode *val = y_value(t, 1);
            y_push(t, x, val, k, kl);
        } else {
            y_push(t, x, y_value(t, 1), NULL, 0);
        }
        while (!y_eof(t) && (t->p[t->i] == ' ' || t->p[t->i] == '\t')) {
            t->i++;
        }
        if (y_eof(t)) {
            y_fail(t, "YAML: flow collection is not closed");
        }
        if (t->p[t->i] == ',') {
            t->i++;
            continue;
        }
        if (t->p[t->i] == (arr ? ']' : '}')) {
            t->i++;
            t->depth--;
            return x;
        }
        y_fail(t, "YAML: bad flow collection");
    }
}

static YNode *y_value(YParse *t, int flow) {
    if (y_eof(t)) {
        return y_node(t, Y_NULL);
    }
    char c = t->p[t->i];
    if (c == '"' || c == '\'') {
        YNode *x = y_node(t, Y_STR);
        x->s = y_string(t, &x->n);
        return x;
    }
    if (c == '[' || c == '{') {
        return y_flow(t, y_node(t, Y_STR));
    }
    if (c == '&' || c == '*' || c == '!') {
        y_fail(t, "YAML: anchors, aliases and tags are not in the subset");
    }
    if (c == '|' || c == '>') {
        y_fail(t, "YAML: block scalars are not in the subset");
    }
    int64_t n;
    char *s = y_plain(t, flow, &n);
    return y_scalar(t, s, n);
}

/* Column (0-based) of position `at`: characters from the line start. */
static int64_t y_column(YParse *t, int64_t at) {
    (void)t;
    int64_t j = at;
    while (j > 0 && t->p[j - 1] != '\n') {
        j--;
    }
    return at - j;
}

/* Column (0-based) of position `at`: the leading indent of its line. For a
   position already past a `- `, the caller uses the item column instead. */
static int64_t y_indent(YParse *t, int64_t at) {
    int64_t col = 0;
    int64_t j = at;
    while (j > 0 && t->p[j - 1] != '\n') {
        j--;
    }
    while (j < t->n && t->p[j] == ' ') {
        col++;
        j++;
    }
    if (j < t->n && t->p[j] == '\t') {
        t->i = j;
        y_fail(t, "YAML: tab indentation is not in the subset");
    }
    return col;
}

/* Skips blank lines and full-line comments; stops on EOF. */
static void y_blank(YParse *t) {
    for (;;) {
        int64_t j = t->i;
        while (j < t->n && (t->p[j] == ' ' || t->p[j] == '\t')) {
            j++;
        }
        if (j >= t->n) {
            t->i = j;
            return;
        }
        if (t->p[j] == '\n' || t->p[j] == '\r') {
            t->i = j + 1;
            if (t->p[j] == '\r' && t->i < t->n && t->p[t->i] == '\n') {
                t->i++;
            }
            continue;
        }
        if (t->p[j] == '#') {
            while (j < t->n && t->p[j] != '\n') {
                j++;
            }
            t->i = j + 1;
            continue;
        }
        t->i = j;
        return;
    }
}

/* Position of the end of the current line (before \n). */
static int64_t y_line_end(YParse *t) {
    int64_t j = t->i;
    while (j < t->n && t->p[j] != '\n') {
        j++;
    }
    if (j > t->i && t->p[j - 1] == '\r') {
        return j - 1;
    }
    return j;
}

static void y_advance(YParse *t, int64_t end) {
    t->i = end;
    if (t->i < t->n) {
        t->i++;
        if (t->p[t->i - 1] == '\r' && t->i < t->n && t->p[t->i] == '\n') {
            t->i++;
        }
    }
}

/* Reads one logical line's content after stripping a trailing comment. */
static int64_t y_content_end(YParse *t, int64_t start, int64_t end) {
    (void)t;
    int64_t e = end;
    while (e > start && (e - 1 >= 0 && (t->p[e - 1] == ' ' || t->p[e - 1] == '\t' || t->p[e - 1] == '\r'))) {
        e--;
    }
    return e;
}

static YNode *y_block(YParse *t, int64_t indent);
static YNode *y_seq(YParse *t, int64_t indent);
static YNode *y_map_at(YParse *t, int64_t indent, int first);
static int y_has_map_colon(YParse *t);

/* One block node at column `indent`: a sequence or a mapping. */
static YNode *y_block(YParse *t, int64_t indent) {
    y_blank(t);
    if (y_eof(t)) {
        return y_node(t, Y_NULL);
    }
    if (t->p[t->i] == '-' && (t->i + 1 >= t->n || t->p[t->i + 1] == ' ' || t->p[t->i + 1] == '\n' ||
                              t->p[t->i + 1] == '\r')) {
        return y_seq(t, indent);
    }
    /* A mapping: `key: value` lines at this indent (or nested maps). */
    if (!y_has_map_colon(t)) {
        /* A scalar document: one plain or quoted value, then the end. */
        YNode *v = y_value(t, 0);
        int64_t end = y_line_end(t);
        y_advance(t, y_content_end(t, t->i, end));
        return v;
    }
    return y_map_at(t, indent, 0);
}

/* A block sequence whose dashes sit at column `indent`. */
static YNode *y_seq(YParse *t, int64_t indent) {
    YNode *arr = y_node(t, Y_ARRAY);
    for (;;) {
        y_blank(t);
        if (y_eof(t)) {
            break;
        }
        y_indent(t, t->i);
        if (y_column(t, t->i) != indent || t->p[t->i] != '-' ||
            (t->i + 1 < t->n && t->p[t->i + 1] != ' ' && t->p[t->i + 1] != '\n' && t->p[t->i + 1] != '\r')) {
            break;
        }
        t->i++;
        while (!y_eof(t) && t->p[t->i] == ' ') {
            t->i++;
        }
        if (y_eof(t) || t->p[t->i] == '\n' || t->p[t->i] == '\r' || t->p[t->i] == '#') {
            /* the item is the block that follows, indented deeper */
            int64_t save = t->i;
            y_blank(t);
            if (y_eof(t) || y_indent(t, t->i) <= indent) {
                t->i = save;
                y_push(t, arr, y_node(t, Y_NULL), NULL, 0);
            } else {
                y_push(t, arr, y_block(t, y_indent(t, t->i)), NULL, 0);
            }
            continue;
        }
        int64_t item_indent = y_column(t, t->i);
        if (t->p[t->i] == '-' && (t->i + 1 >= t->n || t->p[t->i + 1] == ' ')) {
            /* a nested sequence on the dash line: `- - 1` */
            y_push(t, arr, y_seq(t, item_indent), NULL, 0);
        } else if (y_has_map_colon(t)) {
            /* a nested mapping on the dash line: `- k: v`, with the
               following lines at the column of the key */
            y_push(t, arr, y_map_at(t, item_indent, 1), NULL, 0);
        } else {
            YNode *v = y_value(t, 0);
            int64_t end = y_line_end(t);
            y_advance(t, y_content_end(t, t->i, end));
            y_push(t, arr, v, NULL, 0);
        }
    }
    return arr;
}

/* True when the current line holds a `key:` mapping entry. */
static int y_has_map_colon(YParse *t) {
    int64_t probe = t->i;
    char q = 0;
    while (probe < t->n && t->p[probe] != '\n') {
        char pc = t->p[probe];
        if (q) {
            if (pc == q) {
                q = 0;
            }
        } else if (pc == '"' || pc == '\'') {
            q = pc;
        } else if (pc == ':' && (probe + 1 >= t->n || t->p[probe + 1] == ' ' || t->p[probe + 1] == '\n' ||
                                 t->p[probe + 1] == '\r')) {
            return 1;
        }
        probe++;
    }
    return 0;
}

/* A block mapping whose keys sit at column `indent`. The first call may sit
   mid-line (a `- k: v` item): `first` skips the indent check once. */
static YNode *y_map_at(YParse *t, int64_t indent, int first) {
    YNode *tab = y_node(t, Y_TABLE);
    for (;;) {
        y_blank(t);
        if (y_eof(t)) {
            break;
        }
        int64_t at = t->i;
        if (!first) {
            int64_t col = y_indent(t, at);
            if (col < indent) {
                break;
            }
            if (col > indent) {
                y_fail(t, "YAML: unexpected indentation");
            }
        }
        first = 0;
        /* document end markers */
        if (t->p[t->i] == '.' && t->i + 3 <= t->n && memcmp(t->p + t->i, "...", 3) == 0) {
            y_advance(t, y_line_end(t));
            break;
        }
        if (t->p[t->i] == '-' && (t->i + 1 >= t->n || t->p[t->i + 1] == ' ')) {
            break; /* a sibling sequence ends this mapping */
        }
        char *k;
        int64_t kl;
        if (t->p[t->i] == '"' || t->p[t->i] == '\'') {
            k = y_string(t, &kl);
        } else {
            int64_t ka = t->i;
            k = y_plain(t, 0, &kl);
            if (kl == 0) {
                y_fail(t, "YAML: expected a key");
            }
            if (!y_is_string_key(k, kl)) {
                y_fail(t, "YAML: only string keys are in the subset");
            }
            if (ka + kl > t->n || t->p[ka + kl] != ':') {
                t->i = ka + kl;
                while (!y_eof(t) && t->p[t->i] == ' ') {
                    t->i++;
                }
                if (y_eof(t) || t->p[t->i] != ':') {
                    y_fail(t, "YAML: mapping expects 'key:'");
                }
            }
        }
        while (!y_eof(t) && t->p[t->i] == ' ') {
            t->i++;
        }
        if (y_eof(t) || t->p[t->i] != ':') {
            y_fail(t, "YAML: mapping expects 'key:'");
        }
        t->i++;
        while (!y_eof(t) && t->p[t->i] == ' ') {
            t->i++;
        }
        YNode *val;
        if (y_eof(t) || t->p[t->i] == '\n' || t->p[t->i] == '\r' || t->p[t->i] == '#') {
            /* value on the following lines (or empty -> null) */
            int64_t save = t->i;
            y_blank(t);
            int64_t col = y_eof(t) ? -1 : y_indent(t, t->i);
            if (!y_eof(t) && col == indent && t->p[t->i] == '-' &&
                (t->i + 1 >= t->n || t->p[t->i + 1] == ' ')) {
                /* a block sequence may sit at the column of its key */
                val = y_seq(t, indent);
            } else if (y_eof(t) || col <= indent) {
                t->i = save;
                val = y_node(t, Y_NULL);
            } else {
                if (t->p[t->i] == '&' || t->p[t->i] == '*' || t->p[t->i] == '!' || t->p[t->i] == '|' ||
                    t->p[t->i] == '>') {
                    y_fail(t, "YAML: anchors, aliases, tags and block scalars are not in the subset");
                }
                val = y_block(t, y_indent(t, t->i));
            }
        } else {
            val = y_value(t, 0);
            int64_t end = y_line_end(t);
            y_advance(t, y_content_end(t, t->i, end));
        }
        y_push(t, tab, val, k, kl);
    }
    return tab;
}

static void j_str(YParse *t, const char *s, int64_t n) {
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

static void j_emit(YParse *t, YNode *x) {
    if (x->kind == Y_NULL) {
        ps_grow_add(&t->out, "null", 4);
    } else if (x->kind == Y_STR) {
        j_str(t, x->s, x->n);
    } else if (x->kind == Y_NUM || x->kind == Y_BOOL) {
        ps_grow_add(&t->out, x->s, x->n);
    } else if (x->kind == Y_ARRAY) {
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

void ps_yaml_to_json(int64_t n, const char *p, PsBuf *out) {
    YParse t;
    memset(&t, 0, sizeof t);
    t.p = p;
    t.n = n;
    ps_grow_init(&t.out);
    /* Optional document markers: a leading `---` line starts the document. */
    y_blank(&t);
    if (!y_eof(&t) && t.p[t.i] == '-' && t.i + 3 <= t.n && memcmp(t.p + t.i, "---", 3) == 0 &&
        (t.i + 3 >= t.n || t.p[t.i + 3] == ' ' || t.p[t.i + 3] == '\n' || t.p[t.i + 3] == '\r')) {
        y_advance(&t, y_line_end(&t));
    }
    if (y_eof(&t)) {
        ps_grow_add(&t.out, "null", 4);
        ps_grow_finish(&t.out, out);
        return;
    }
    YNode *root = y_block(&t, y_indent(&t, t.i));
    y_blank(&t);
    if (!y_eof(&t)) {
        y_fail(&t, "YAML: content after the document end");
    }
    j_emit(&t, root);
    ps_grow_finish(&t.out, out);
}
