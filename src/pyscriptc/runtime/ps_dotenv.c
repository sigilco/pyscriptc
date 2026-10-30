/* dotenv.dotenv_values / dotenv.load_dotenv support: parse a .env file and
re-emit it as JSON, which the json.loads machinery then reads into the
annotated type. The lowering of `dotenv_values(path)` is
`json.loads(ps_dotenv_values(path))`, so the shape rules are the json ones;
`load_dotenv(path)` lowers to ps_dotenv_load, which also sets the variables.

The closed subset (docs/reference/divergences.md, divergence 16): UTF-8 BOM
stripped; blank lines and whole-line comments skipped; optional `export `
prefix; bare keys (quoted keys raise ValueError); `KEY` alone binds null;
`KEY=value` unquoted values with the ` #` inline comment cut and the rest
right-stripped; double-quoted values decoding \\ \" \' \a \b \f \n \r \t \v;
single-quoted values decoding \\ and \' only; other escapes stay literal;
later bindings overwrite earlier ones. No interpolation ($NAME stays
literal), no multi-line values, no `stream`/`verbose`/`interpolate`/
`override`/`encoding` arguments (fenced). A quoted key, a line whose key is
followed by anything but `=`, a comment or the end of the line, text after a
quoted value, or an unterminated quoted value raise ValueError.

Differences from python-dotenv: a malformed line raises ValueError instead
of being skipped with a warning, a missing file parses as the empty
document instead of a find_dotenv() search (so `dotenv_values` of a missing
path is {} and `load_dotenv` is False), and `load_dotenv` only writes keys
that are not already in the environment (its `override` default), returning
whether the document held any binding, as python-dotenv does. Non-ASCII keys or values write raw UTF-8 through
the json.dumps writer, the way ensure_ascii=False does. */

#include "ps_rt.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct DBind {
    char *key;
    char *val; /* NULL for a key without a value */
    int64_t vlen;
    int has_val;
} DBind;

typedef struct DParse {
    const char *p;
    int64_t n;
    int64_t i;
    DBind *binds;
    int64_t nb;
    int64_t bcap;
    char **blobs;
    int64_t nl;
    int64_t lcap;
    PsGrow out;
} DParse;

static void d_fail(DParse *t, const char *msg) {
    for (int64_t i = 0; i < t->nl; i++) {
        free(t->blobs[i]);
    }
    free(t->blobs);
    free(t->binds);
    free(t->out.data);
    ps_raise_cstr(PS_VALUE, msg);
}

static void *d_alloc(size_t size) {
    void *p = malloc(size ? size : 1);
    if (p == NULL) {
        abort();
    }
    return p;
}

static char *d_blob(DParse *t, int64_t len) {
    if (t->nl == t->lcap) {
        t->lcap = t->lcap ? t->lcap * 2 : 16;
        t->blobs = realloc(t->blobs, (size_t)t->lcap * sizeof *t->blobs);
        if (t->blobs == NULL) {
            abort();
        }
    }
    char *p = d_alloc((size_t)len + 1);
    t->blobs[t->nl++] = p;
    return p;
}

static char d_cur(DParse *t) { return t->p[t->i]; }

static int d_space(DParse *t) { return d_cur(t) == ' ' || d_cur(t) == '\t'; }

static void d_skip_spaces(DParse *t) {
    while (t->i < t->n && d_space(t)) {
        t->i++;
    }
}

static void d_line_end(DParse *t) {
    while (t->i < t->n && t->p[t->i] != '\n') {
        t->i++;
    }
    if (t->i < t->n) {
        t->i++;
    }
}

/* JSON string body: escape the quote, backslash and control characters;
   non-ASCII text passes through as raw UTF-8 (the json.dumps raw form). */
static void d_json_str(DParse *t, const char *s, int64_t len) {
    ps_grow_byte(&t->out, '"');
    for (int64_t i = 0; i < len; i++) {
        unsigned char c = (unsigned char)s[i];
        if (c == '"' || c == '\\') {
            ps_grow_byte(&t->out, '\\');
            ps_grow_byte(&t->out, c);
        } else if (c == '\b') {
            ps_grow_add(&t->out, "\\b", 2);
        } else if (c == '\f') {
            ps_grow_add(&t->out, "\\f", 2);
        } else if (c == '\n') {
            ps_grow_add(&t->out, "\\n", 2);
        } else if (c == '\r') {
            ps_grow_add(&t->out, "\\r", 2);
        } else if (c == '\t') {
            ps_grow_add(&t->out, "\\t", 2);
        } else if (c < 0x20) {
            char tmp[7];
            snprintf(tmp, sizeof tmp, "\\u%04x", c);
            ps_grow_add(&t->out, tmp, 6);
        } else {
            ps_grow_byte(&t->out, c);
        }
    }
    ps_grow_byte(&t->out, '"');
}

static void d_store_val(DBind *b, DParse *t, const char *val, int64_t vlen) {
    b->val = d_blob(t, vlen);
    memcpy(b->val, val, (size_t)vlen);
    b->val[vlen] = '\0';
    b->vlen = vlen;
    b->has_val = 1;
}

static void d_push(DParse *t, const char *key, int64_t klen, const char *val, int64_t vlen, int has_val) {
    for (int64_t i = 0; i < t->nb; i++) {
        if (strlen(t->binds[i].key) == (size_t)klen && memcmp(t->binds[i].key, key, (size_t)klen) == 0) {
            t->binds[i].has_val = has_val;
            t->binds[i].vlen = vlen;
            if (has_val) {
                d_store_val(&t->binds[i], t, val, vlen);
            } else {
                t->binds[i].val = NULL;
            }
            return;
        }
    }
    if (t->nb == t->bcap) {
        t->bcap = t->bcap ? t->bcap * 2 : 16;
        t->binds = realloc(t->binds, (size_t)t->bcap * sizeof *t->binds);
        if (t->binds == NULL) {
            abort();
        }
    }
    DBind *b = &t->binds[t->nb++];
    b->key = d_blob(t, klen);
    memcpy(b->key, key, (size_t)klen);
    b->key[klen] = '\0';
    b->val = NULL;
    b->vlen = 0;
    b->has_val = has_val;
    if (has_val) {
        d_store_val(b, t, val, vlen);
    }
}

static char d_unescape(char quote, char e, int *known) {
    *known = 1;
    switch (e) {
    case '\\':
        return '\\';
    case '\'':
        if (quote == '\'') {
            return '\'';
        }
        break;
    case '"':
        if (quote == '"') {
            return '"';
        }
        break;
    case 'a':
        return '\a';
    case 'b':
        return '\b';
    case 'f':
        return '\f';
    case 'n':
        return '\n';
    case 'r':
        return '\r';
    case 't':
        return '\t';
    case 'v':
        return '\v';
    default:
        break;
    }
    *known = 0;
    return e;
}

/* One quoted value; the opening quote is at the cursor. */
static void d_quoted(DParse *t, char quote, char **out, int64_t *outlen) {
    t->i++;
    int64_t start = t->i;
    while (t->i < t->n) {
        char c = t->p[t->i];
        if (c == '\n') {
            break;
        }
        if (c == '\r') {
            break;
        }
        if (c == '\\') {
            t->i += 2;
            continue;
        }
        if (c == quote) {
            int64_t w = 0;
            char *buf = d_alloc((size_t)(t->i - start) + 1);
            for (int64_t i = start; i < t->i; i++) {
                if (t->p[i] == '\\' && i + 1 < t->i) {
                    int known;
                    char dec = d_unescape(quote, t->p[i + 1], &known);
                    if (known) {
                        buf[w++] = dec;
                        i++;
                        continue;
                    }
                }
                buf[w++] = t->p[i];
            }
            buf[w] = '\0';
            *out = buf;
            *outlen = w;
            t->i++;
            return;
        }
        t->i++;
    }
    d_fail(t, "dotenv: quoted value runs past the end of its line");
}

/* One unquoted value: to the end of the line, the ` #` comment cut, the rest
   right-stripped of spaces and tabs. */
static void d_unquoted(DParse *t, char **out, int64_t *outlen) {
    int64_t start = t->i;
    int64_t end = t->i;
    while (t->i < t->n && t->p[t->i] != '\n' && t->p[t->i] != '\r') {
        if (d_space(t)) {
            int64_t ws = t->i;
            while (t->i < t->n && d_space(t)) {
                t->i++;
            }
            if (t->i < t->n && t->p[t->i] == '#') {
                end = ws;
                t->i = ws;
                break;
            }
            end = t->i;
            continue;
        }
        t->i++;
        end = t->i;
    }
    while (end > start && (t->p[end - 1] == ' ' || t->p[end - 1] == '\t')) {
        end--;
    }
    *outlen = end - start;
    *out = d_blob(t, *outlen);
    memcpy(*out, t->p + start, (size_t)*outlen);
    (*out)[*outlen] = '\0';
}

static void d_binding(DParse *t) {
    d_skip_spaces(t);
    if (t->i >= t->n || t->p[t->i] == '#' || t->p[t->i] == '\n' || t->p[t->i] == '\r') {
        d_line_end(t);
        return;
    }
    if (t->n - t->i >= 7 && memcmp(t->p + t->i, "export ", 7) == 0) {
        t->i += 7;
        d_skip_spaces(t);
    }
    int64_t kstart = t->i;
    while (t->i < t->n && !d_space(t) && t->p[t->i] != '=' && t->p[t->i] != '#' && t->p[t->i] != '\n' &&
           t->p[t->i] != '\r') {
        if (t->p[t->i] == '\'' || t->p[t->i] == '"') {
            d_fail(t, "dotenv: quoted keys are not in the lowered subset");
        }
        t->i++;
    }
    int64_t klen = t->i - kstart;
    if (klen == 0) {
        d_fail(t, "dotenv: expected a key");
    }
    d_skip_spaces(t);
    if (t->i >= t->n || t->p[t->i] == '#' || t->p[t->i] == '\n' || t->p[t->i] == '\r') {
        /* `KEY` alone, or `KEY  # comment`: the binding is KEY = None. */
        d_line_end(t);
        d_push(t, t->p + kstart, klen, NULL, 0, 0);
        return;
    }
    if (t->p[t->i] != '=') {
        d_fail(t, "dotenv: expected '=' or the end of the line");
    }
    t->i++;
    int had_space = t->i < t->n && d_space(t);
    d_skip_spaces(t);
    if (t->i >= t->n || t->p[t->i] == '\n' || t->p[t->i] == '\r' ||
        (had_space && t->p[t->i] == '#')) {
        /* `KEY=`, `KEY= # comment`, or a line end: the value is empty. A `#`
           right after `=` without whitespace is part of the value. */
        d_line_end(t);
        d_push(t, t->p + kstart, klen, "", 0, 1);
        return;
    }
    char *val = NULL;
    int64_t vlen = 0;
    char quote = t->p[t->i];
    if (quote == '"' || quote == '\'') {
        d_quoted(t, quote, &val, &vlen);
        d_skip_spaces(t);
        if (t->i < t->n && t->p[t->i] != '#' && t->p[t->i] != '\n' && t->p[t->i] != '\r') {
            d_fail(t, "dotenv: text after a quoted value is not in the lowered subset");
        }
        d_line_end(t);
    } else {
        d_unquoted(t, &val, &vlen);
        d_line_end(t);
    }
    d_push(t, t->p + kstart, klen, val, vlen, 1);
}

static void d_parse(DParse *t) {
    if (t->n >= 3 && (unsigned char)t->p[0] == 0xEF && (unsigned char)t->p[1] == 0xBB &&
        (unsigned char)t->p[2] == 0xBF) {
        t->i = 3;
    }
    while (t->i < t->n) {
        d_binding(t);
    }
}

static void d_read(int64_t pn, const char *path, PsBuf *out) {
    char *text = d_alloc((size_t)pn + 1);
    if (pn > 0) {
        memcpy(text, path, (size_t)pn);
    }
    text[pn] = '\0';
    FILE *fp = fopen(text, "rb");
    free(text);
    long size;
    char *buf;
    if (fp == NULL) {
        /* A missing file parses as the empty document: dotenv_values is {}
           and load_dotenv is False, as python-dotenv has it. */
        out->len = 0;
        out->data = NULL;
        return;
    }
    if (fseek(fp, 0, SEEK_END) != 0) {
        fclose(fp);
        ps_raise_cstr(PS_RUNTIME, "dotenv: read failed");
    }
    size = ftell(fp);
    if (size < 0 || fseek(fp, 0, SEEK_SET) != 0) {
        fclose(fp);
        ps_raise_cstr(PS_RUNTIME, "dotenv: read failed");
    }
    buf = d_alloc((size_t)size + 1);
    if (size > 0 && fread(buf, 1, (size_t)size, fp) != (size_t)size) {
        free(buf);
        fclose(fp);
        ps_raise_cstr(PS_RUNTIME, "dotenv: read failed");
    }
    fclose(fp);
    buf[size] = '\0';
    out->len = (int64_t)size;
    out->data = buf;
}

/* The document as a JSON object: {"k": "v", ...}; a key without a value is
   {"k": null}. */
static void d_emit(DParse *t, PsBuf *out) {
    ps_grow_init(&t->out);
    ps_grow_byte(&t->out, '{');
    for (int64_t i = 0; i < t->nb; i++) {
        if (i) {
            ps_grow_byte(&t->out, ',');
        }
        d_json_str(t, t->binds[i].key, (int64_t)strlen(t->binds[i].key));
        ps_grow_byte(&t->out, ':');
        if (t->binds[i].has_val) {
            d_json_str(t, t->binds[i].val, t->binds[i].vlen);
        } else {
            ps_grow_add(&t->out, "null", 4);
        }
    }
    ps_grow_byte(&t->out, '}');
    ps_grow_finish(&t->out, out);
    for (int64_t i = 0; i < t->nl; i++) {
        free(t->blobs[i]);
    }
    free(t->blobs);
    free(t->binds);
}

void ps_dotenv_values(int64_t pn, const char *path, PsBuf *out) {
    DParse t;
    PsBuf text;
    memset(&t, 0, sizeof t);
    d_read(pn, path, &text);
    t.p = text.data;
    t.n = text.len;
    d_parse(&t);
    d_emit(&t, out);
    free(text.data);
}

int32_t ps_dotenv_load(int64_t pn, const char *path) {
    DParse t;
    PsBuf text;
    memset(&t, 0, sizeof t);
    d_read(pn, path, &text);
    t.p = text.data;
    t.n = text.len;
    d_parse(&t);
    int32_t seen = t.nb > 0;
    for (int64_t i = 0; i < t.nb; i++) {
        if (!t.binds[i].has_val) {
            continue;
        }
        if (getenv(t.binds[i].key) != NULL) {
            continue;
        }
        if (ps_setenv(t.binds[i].key, t.binds[i].val, 1) != 0) {
            ps_raise_cstr(PS_RUNTIME, "setenv failed");
        }
    }
    for (int64_t i = 0; i < t.nl; i++) {
        free(t.blobs[i]);
    }
    free(t.blobs);
    free(t.binds);
    free(text.data);
    return seen;
}
