/* json.loads / json.dumps for int, bool, str, float, Optional, list, and
dict[str, ...]. An Optional[T] value is a heap cell { i1 tag, pad, T val }
matching the opt_* class layout; `null` writes a zeroed cell (tag unset).
Object key order follows insertion. */

#include "ps_rt.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct PsJson {
    const char *p;
    int64_t n;
    int64_t i;
    const PsType *types;
} PsJson;

/* Malformed JSON raises the JSONDecodeError class (a ValueError child),
   the way CPython's reader does. */
static void ps_json_fail(const char *msg) { ps_raise_cstr(PS_JSONDECODEERROR, msg); }

static void ps_skip(PsJson *j) {
    while (j->i < j->n) {
        char c = j->p[j->i];
        if (c != ' ' && c != '\t' && c != '\n' && c != '\r') {
            break;
        }
        j->i++;
    }
}

static char ps_peek(PsJson *j) {
    ps_skip(j);
    if (j->i >= j->n) {
        ps_json_fail("unexpected end of JSON");
    }
    return j->p[j->i];
}

static int ps_hex_digit(char c) {
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

/* One \uXXXX escape body already read as `cp`: a surrogate pair combines
   into one code point, every other scalar encodes as UTF-8. A lone
   surrogate has no UTF-8 bytes to store, so it stays fenced. */
static void ps_json_escape_cp(PsJson *j, PsGrow *g, unsigned int cp) {
    if (cp >= 0xd800 && cp <= 0xdbff) {
        if (j->i + 6 > j->n || j->p[j->i] != '\\' || j->p[j->i + 1] != 'u') {
            free(g->data);
            ps_json_fail("lone JSON surrogate is not lowered");
        }
        j->i += 2;
        unsigned int low = 0;
        for (int k = 0; k < 4; k++) {
            if (j->i >= j->n) {
                free(g->data);
                ps_json_fail("bad JSON unicode escape");
            }
            int d = ps_hex_digit(j->p[j->i++]);
            if (d < 0) {
                free(g->data);
                ps_json_fail("bad JSON unicode escape");
            }
            low = (low << 4) | (unsigned int)d;
        }
        if (low < 0xdc00 || low > 0xdfff) {
            free(g->data);
            ps_json_fail("lone JSON surrogate is not lowered");
        }
        cp = 0x10000 + ((cp - 0xd800) << 10) + (low - 0xdc00);
    } else if (cp >= 0xdc00 && cp <= 0xdfff) {
        free(g->data);
        ps_json_fail("lone JSON surrogate is not lowered");
    }
    if (cp < 0x80) {
        ps_grow_byte(g, (unsigned char)cp);
    } else if (cp < 0x800) {
        ps_grow_byte(g, (unsigned char)(0xc0 | (cp >> 6)));
        ps_grow_byte(g, (unsigned char)(0x80 | (cp & 0x3f)));
    } else if (cp < 0x10000) {
        ps_grow_byte(g, (unsigned char)(0xe0 | (cp >> 12)));
        ps_grow_byte(g, (unsigned char)(0x80 | ((cp >> 6) & 0x3f)));
        ps_grow_byte(g, (unsigned char)(0x80 | (cp & 0x3f)));
    } else {
        ps_grow_byte(g, (unsigned char)(0xf0 | (cp >> 18)));
        ps_grow_byte(g, (unsigned char)(0x80 | ((cp >> 12) & 0x3f)));
        ps_grow_byte(g, (unsigned char)(0x80 | ((cp >> 6) & 0x3f)));
        ps_grow_byte(g, (unsigned char)(0x80 | (cp & 0x3f)));
    }
}

static void ps_parse_string(PsJson *j, PsBuf *out) {
    if (ps_peek(j) != '"') {
        ps_json_fail("JSON string expected");
    }
    j->i++;
    PsGrow g;
    ps_grow_init(&g);
    while (j->i < j->n) {
        unsigned char c = (unsigned char)j->p[j->i++];
        if (c == '"') {
            ps_grow_finish(&g, out);
            return;
        }
        if (c == '\\') {
            if (j->i >= j->n) {
                free(g.data);
                ps_json_fail("bad JSON string escape");
            }
            char e = j->p[j->i++];
            if (e == '"' || e == '\\' || e == '/') {
                ps_grow_byte(&g, (unsigned char)e);
            } else if (e == 'b') {
                ps_grow_byte(&g, '\b');
            } else if (e == 'f') {
                ps_grow_byte(&g, '\f');
            } else if (e == 'n') {
                ps_grow_byte(&g, '\n');
            } else if (e == 'r') {
                ps_grow_byte(&g, '\r');
            } else if (e == 't') {
                ps_grow_byte(&g, '\t');
            } else if (e == 'u') {
                unsigned int cp = 0;
                for (int k = 0; k < 4; k++) {
                    if (j->i >= j->n) {
                        free(g.data);
                        ps_json_fail("bad JSON unicode escape");
                    }
                    int d = ps_hex_digit(j->p[j->i++]);
                    if (d < 0) {
                        free(g.data);
                        ps_json_fail("bad JSON unicode escape");
                    }
                    cp = (cp << 4) | (unsigned int)d;
                }
                ps_json_escape_cp(j, &g, cp);
            } else {
                free(g.data);
                ps_json_fail("bad JSON string escape");
            }
            continue;
        }
        if (c < 0x20) {
            free(g.data);
            ps_json_fail("unescaped control character in JSON string");
        }
        /* Non-ASCII text passes through as UTF-8: the str buffer holds the
           same bytes CPython's decoder produces. */
        ps_grow_byte(&g, c);
    }
    free(g.data);
    ps_json_fail("unterminated JSON string");
}

/* Validates JSON number grammar starting at the cursor and leaves it past
   the token. Returns the token start and whether it carried a fraction or
   exponent (the float form). */
static int64_t ps_scan_number(PsJson *j, int *is_float) {
    int64_t start = j->i;
    if (j->i < j->n && j->p[j->i] == '-') {
        j->i++;
    }
    if (j->i >= j->n || j->p[j->i] < '0' || j->p[j->i] > '9') {
        ps_json_fail("JSON number expected");
    }
    if (j->p[j->i] == '0') {
        j->i++;
        if (j->i < j->n && j->p[j->i] >= '0' && j->p[j->i] <= '9') {
            ps_json_fail("JSON number has a leading zero");
        }
    } else {
        while (j->i < j->n && j->p[j->i] >= '0' && j->p[j->i] <= '9') {
            j->i++;
        }
    }
    *is_float = 0;
    if (j->i < j->n && j->p[j->i] == '.') {
        *is_float = 1;
        j->i++;
        if (j->i >= j->n || j->p[j->i] < '0' || j->p[j->i] > '9') {
            ps_json_fail("JSON number expected");
        }
        while (j->i < j->n && j->p[j->i] >= '0' && j->p[j->i] <= '9') {
            j->i++;
        }
    }
    if (j->i < j->n && (j->p[j->i] == 'e' || j->p[j->i] == 'E')) {
        *is_float = 1;
        j->i++;
        if (j->i < j->n && (j->p[j->i] == '+' || j->p[j->i] == '-')) {
            j->i++;
        }
        if (j->i >= j->n || j->p[j->i] < '0' || j->p[j->i] > '9') {
            ps_json_fail("JSON number expected");
        }
        while (j->i < j->n && j->p[j->i] >= '0' && j->p[j->i] <= '9') {
            j->i++;
        }
    }
    return start;
}

static void ps_parse_int(PsJson *j, int64_t *out) {
    int is_float = 0;
    int64_t start = ps_scan_number(j, &is_float);
    if (is_float) {
        ps_json_fail("JSON value does not match the annotation");
    }
    int neg = 0;
    if (j->p[start] == '-') {
        neg = 1;
        start++;
    }
    uint64_t mag = 0;
    while (start < j->i) {
        int digit = j->p[start] - '0';
        if (mag > (UINT64_MAX - (uint64_t)digit) / 10) {
            ps_json_fail("JSON integer does not fit in i64");
        }
        mag = mag * 10 + (uint64_t)digit;
        start++;
    }
    if (!neg) {
        if (mag > (uint64_t)INT64_MAX) {
            ps_json_fail("JSON integer does not fit in i64");
        }
        *out = (int64_t)mag;
        return;
    }
    if (mag > (uint64_t)INT64_MAX + 1) {
        ps_json_fail("JSON integer does not fit in i64");
    }
    if (mag == (uint64_t)INT64_MAX + 1) {
        *out = INT64_MIN;
        return;
    }
    *out = -(int64_t)mag;
}

static void ps_parse_float(PsJson *j, double *out) {
    int is_float = 0;
    int64_t start = ps_scan_number(j, &is_float);
    (void)is_float;
    int64_t len = j->i - start;
    char *tmp = ps_alloc(len + 1);
    memcpy(tmp, j->p + start, (size_t)len);
    *out = strtod(tmp, NULL);
}

/* Byte offset of the `_val` field in an opt_* cell: { i1 tag, pad, T val }.
   A bool payload (i1) sits right behind the tag; everything wider aligns 8. */
static int64_t ps_opt_off(int kind) {
    if (kind == PS_JSON_BOOL) {
        return 1;
    }
    return 8;
}

static int64_t ps_slot(int kind) {
    if (kind == PS_JSON_INT) {
        return 8;
    }
    if (kind == PS_JSON_BOOL) {
        return 4;
    }
    if (kind == PS_JSON_STR) {
        return 16;
    }
    return 8;
}

static void ps_parse_value(PsJson *j, int32_t idx, void *out);

static void ps_expect(PsJson *j, char c) {
    if (ps_peek(j) != c) {
        ps_json_fail("JSON value does not match the annotation");
    }
    j->i++;
}

static void ps_parse_value(PsJson *j, int32_t idx, void *out) {
    PsType typ = j->types[idx];
    char c = ps_peek(j);
    if (c == 'n' && j->i + 4 <= j->n && memcmp(j->p + j->i, "null", 4) == 0) {
        if (typ.kind != PS_JSON_OPT) {
            ps_json_fail("JSON value does not match the annotation");
        }
        j->i += 4;
        void *cell = ps_alloc(ps_opt_off(j->types[typ.child].kind) + ps_slot(j->types[typ.child].kind));
        memcpy(out, &cell, 8);
        return;
    }
    if (typ.kind == PS_JSON_OPT) {
        PsType child = j->types[typ.child];
        int64_t off = ps_opt_off(child.kind);
        char *cell = ps_alloc(off + ps_slot(child.kind));
        ps_parse_value(j, typ.child, cell + off);
        cell[0] = 1;
        memcpy(out, &cell, 8);
        return;
    }
    if (typ.kind == PS_JSON_INT) {
        int64_t v = 0;
        ps_parse_int(j, &v);
        memcpy(out, &v, 8);
        return;
    }
    if (typ.kind == PS_JSON_BOOL) {
        if (j->i + 4 <= j->n && memcmp(j->p + j->i, "true", 4) == 0) {
            j->i += 4;
            int32_t v = 1;
            memcpy(out, &v, 4);
            return;
        }
        if (j->i + 5 <= j->n && memcmp(j->p + j->i, "false", 5) == 0) {
            j->i += 5;
            int32_t v = 0;
            memcpy(out, &v, 4);
            return;
        }
        ps_json_fail("JSON bool expected");
    }
    if (typ.kind == PS_JSON_FLOAT) {
        double v = 0.0;
        if (j->i + 3 <= j->n && memcmp(j->p + j->i, "NaN", 3) == 0) {
            j->i += 3;
            v = NAN;
        } else if (j->i + 9 <= j->n && memcmp(j->p + j->i, "-Infinity", 9) == 0) {
            j->i += 9;
            v = -INFINITY;
        } else if (j->i + 8 <= j->n && memcmp(j->p + j->i, "Infinity", 8) == 0) {
            j->i += 8;
            v = INFINITY;
        } else {
            ps_parse_float(j, &v);
        }
        memcpy(out, &v, 8);
        return;
    }
    if (typ.kind == PS_JSON_STR) {
        ps_parse_string(j, out);
        return;
    }
    if (typ.kind == PS_JSON_LIST) {
        ps_expect(j, '[');
        PsType child = j->types[typ.child];
        PsSeq *seq = ps_seq_new(ps_slot(child.kind), 0);
        ps_skip(j);
        if (j->i < j->n && j->p[j->i] == ']') {
            j->i++;
            memcpy(out, &seq, 8);
            return;
        }
        for (;;) {
            unsigned char slot[16];
            memset(slot, 0, sizeof slot);
            ps_parse_value(j, typ.child, slot);
            ps_seq_push(seq, slot);
            ps_skip(j);
            if (j->i < j->n && j->p[j->i] == ',') {
                j->i++;
                continue;
            }
            if (j->i < j->n && j->p[j->i] == ']') {
                j->i++;
                break;
            }
            ps_json_fail("JSON array is not closed");
        }
        memcpy(out, &seq, 8);
        return;
    }
    if (typ.kind == PS_JSON_DICT) {
        ps_expect(j, '{');
        PsType child = j->types[typ.child];
        PsMap *map = ps_map_new(2, 16, ps_slot(child.kind), 0);
        ps_skip(j);
        if (j->i < j->n && j->p[j->i] == '}') {
            j->i++;
            memcpy(out, &map, 8);
            return;
        }
        for (;;) {
            PsBuf key;
            ps_parse_string(j, &key);
            ps_skip(j);
            if (j->i >= j->n || j->p[j->i] != ':') {
                ps_json_fail("JSON object expects ':'");
            }
            j->i++;
            unsigned char slot[16];
            memset(slot, 0, sizeof slot);
            ps_parse_value(j, typ.child, slot);
            ps_map_put(map, &key, slot);
            ps_skip(j);
            if (j->i < j->n && j->p[j->i] == ',') {
                j->i++;
                continue;
            }
            if (j->i < j->n && j->p[j->i] == '}') {
                j->i++;
                break;
            }
            ps_json_fail("JSON object is not closed");
        }
        memcpy(out, &map, 8);
        return;
    }
    ps_json_fail("JSON type is not lowered");
}

void ps_json_loads(int64_t n, char *p, const PsType *types, int32_t root, void *out) {
    PsJson j = {p, n, 0, types};
    ps_parse_value(&j, root, out);
    ps_skip(&j);
    if (j.i != j.n) {
        ps_json_fail("trailing JSON");
    }
}

void ps_json_loads_bytes(int64_t n, char *p, const PsType *types, int32_t root, void *out) {
    /* json.loads over a bytes input: CPython decodes the buffer as UTF-8
       before scanning, so malformed sequences raise the UnicodeDecodeError
       family instead of a JSON scan error. The valid decode is the input
       bytes themselves (a str is a UTF-8 buffer), so the check is the
       whole work; the parse then runs on the original buffer. */
    PsBuf decoded;
    ps_utf8_decode(n, p, &decoded);
    ps_json_loads(n, p, types, root, out);
}

static void ps_dump(const PsType *types, int32_t idx, const void *value, PsGrow *g);

/* The code point at `at` and its byte width. The buffer holds UTF-8, so the
   lead byte gives the width and the continuation bytes the payload. */
static uint32_t ps_json_cp(const char *p, int64_t n, int64_t at, int *w) {
    unsigned char c = (unsigned char)p[at];
    if (c < 0x80) {
        *w = 1;
        return c;
    }
    int width = ps_utf8_len(c);
    uint32_t v = width == 2 ? (uint32_t)(c & 0x1F) : (width == 3 ? (uint32_t)(c & 0x0F) : (uint32_t)(c & 0x07));
    for (int k = 1; k < width && at + k < n; k++) {
        v = (v << 6) | (uint32_t)((unsigned char)p[at + k] & 0x3F);
    }
    *w = width;
    return v;
}

/* The \\u escape CPython writes for one code point. */
static void ps_dump_escape_cp(PsGrow *g, uint32_t cp) {
    char tmp[13];
    if (cp < 0x10000) {
        snprintf(tmp, sizeof tmp, "\\u%04x", (unsigned int)cp);
        ps_grow_add(g, tmp, 6);
        return;
    }
    /* A code point above the BMP is a surrogate pair in the escape form. */
    uint32_t v = cp - 0x10000;
    snprintf(tmp, sizeof tmp, "\\u%04x\\u%04x", (unsigned int)(0xd800 + (v >> 10)), (unsigned int)(0xdc00 + (v & 0x3ff)));
    ps_grow_add(g, tmp, 12);
}

/* One JSON string. With `raw` (ensure_ascii=False) the non-ASCII text passes
   through as UTF-8; otherwise every code point above 0x7f is escaped, the
   way CPython's ensure_ascii=True writes it. */
static void ps_dump_str(PsBuf buf, PsGrow *g, int32_t raw) {
    ps_grow_byte(g, '"');
    for (int64_t i = 0; i < buf.len;) {
        unsigned char c = (unsigned char)buf.data[i];
        if (c == '"' || c == '\\') {
            ps_grow_byte(g, '\\');
            ps_grow_byte(g, c);
            i++;
        } else if (c == '\b') {
            ps_grow_add(g, "\\b", 2);
            i++;
        } else if (c == '\f') {
            ps_grow_add(g, "\\f", 2);
            i++;
        } else if (c == '\n') {
            ps_grow_add(g, "\\n", 2);
            i++;
        } else if (c == '\r') {
            ps_grow_add(g, "\\r", 2);
            i++;
        } else if (c == '\t') {
            ps_grow_add(g, "\\t", 2);
            i++;
        } else if (c < 0x20) {
            char tmp[7];
            snprintf(tmp, sizeof tmp, "\\u%04x", (unsigned int)c);
            ps_grow_add(g, tmp, 6);
            i++;
        } else if (c < 0x80) {
            ps_grow_byte(g, c);
            i++;
        } else if (raw) {
            int width = 0;
            ps_json_cp(buf.data, buf.len, i, &width);
            ps_grow_add(g, buf.data + i, width);
            i += width;
        } else {
            int width = 0;
            uint32_t cp = ps_json_cp(buf.data, buf.len, i, &width);
            ps_dump_escape_cp(g, cp);
            i += width;
        }
    }
    ps_grow_byte(g, '"');
}

/* CPython float repr: shortest round-trip digits, fixed notation when the
   decimal exponent is in [-4, 15], else d[.ddd]e+-XX with a signed exponent
   of at least two digits. JSON writes nan/inf as the NaN/Infinity literals. */
static void ps_dump_float(double v, PsGrow *g) {
    if (isnan(v)) {
        ps_grow_add(g, "NaN", 3);
        return;
    }
    if (isinf(v)) {
        if (signbit(v)) {
            ps_grow_add(g, "-Infinity", 9);
        } else {
            ps_grow_add(g, "Infinity", 8);
        }
        return;
    }
    if (v == 0.0) {
        ps_grow_add(g, signbit(v) ? "-0.0" : "0.0", signbit(v) ? 4 : 3);
        return;
    }
    char sci[32];
    for (int prec = 0; prec < 17; prec++) {
        snprintf(sci, sizeof sci, "%.*e", prec, v);
        char *end = NULL;
        double back = strtod(sci, &end);
        if (end != sci && *end == '\0' && back == v) {
            break;
        }
    }
    char digits[32];
    int ndigits = 0;
    char *cur = sci;
    if (*cur == '-') {
        cur++;
    }
    for (; *cur != 'e' && *cur != 'E'; cur++) {
        if (*cur != '.') {
            digits[ndigits++] = *cur;
        }
    }
    digits[ndigits] = '\0';
    int exp10 = atoi(cur + 1);
    if (v < 0) {
        ps_grow_byte(g, '-');
    }
    if (exp10 >= -4 && exp10 <= 15) {
        if (exp10 < 0) {
            ps_grow_add(g, "0.", 2);
            for (int k = -exp10 - 1; k > 0; k--) {
                ps_grow_byte(g, '0');
            }
            ps_grow_add(g, digits, ndigits);
        } else if (ndigits > exp10 + 1) {
            ps_grow_add(g, digits, exp10 + 1);
            ps_grow_byte(g, '.');
            ps_grow_add(g, digits + exp10 + 1, ndigits - exp10 - 1);
        } else {
            ps_grow_add(g, digits, ndigits);
            for (int k = exp10 + 1 - ndigits; k > 0; k--) {
                ps_grow_byte(g, '0');
            }
            ps_grow_add(g, ".0", 2);
        }
        return;
    }
    char head[40];
    int wrote;
    if (ndigits > 1) {
        wrote = snprintf(head, sizeof head, "%c.%s", digits[0], digits + 1);
    } else {
        wrote = snprintf(head, sizeof head, "%c", digits[0]);
    }
    ps_grow_add(g, head, wrote);
    char tail[12];
    wrote = snprintf(tail, sizeof tail, "e%c%02d", exp10 < 0 ? '-' : '+', exp10 < 0 ? -exp10 : exp10);
    ps_grow_add(g, tail, wrote);
}

static void ps_dump(const PsType *types, int32_t idx, const void *value, PsGrow *g);

/* The dumps options: bit 0 sorts the dict keys, bit 1 keeps non-ASCII
   text raw (ensure_ascii=False), bits 8-15 hold the indent width (0 keeps
   the compact form), bit 16 drops the space after the separators (the
   orjson form). */
#define PS_JSON_SORT_KEYS 1
#define PS_JSON_RAW_ASCII 2
#define PS_JSON_COMPACT 0x10000

/* ps_dump_fmt writes one value. `indent` is the pad width per level and
   `level` the current depth; a positive indent puts every item on its
   own line, the way `json.dumps(indent=n)` does. */
static void ps_dump_fmt(const PsType *types, int32_t idx, const void *value, PsGrow *g, int32_t indent, int32_t level, int32_t sort_keys, int32_t compact, int32_t raw);

static void ps_dump(const PsType *types, int32_t idx, const void *value, PsGrow *g) {
    ps_dump_fmt(types, idx, value, g, 0, 0, 0, 0, 0);
}

/* The newline and pad an indented item carries. */
static void ps_dump_pad(PsGrow *g, int32_t indent, int32_t level) {
    ps_grow_byte(g, '\n');
    for (int32_t i = 0; i < level * indent; i++) {
        ps_grow_byte(g, ' ');
    }
}

static void ps_dump_fmt(const PsType *types, int32_t idx, const void *value, PsGrow *g, int32_t indent, int32_t level, int32_t sort_keys, int32_t compact, int32_t raw) {
    PsType typ = types[idx];
    if (typ.kind == PS_JSON_INT) {
        int64_t v = 0;
        memcpy(&v, value, 8);
        char tmp[32];
        int wrote = snprintf(tmp, sizeof tmp, "%lld", (long long)v);
        if (wrote < 0) {
            ps_json_fail("cannot format JSON integer");
        }
        ps_grow_add(g, tmp, wrote);
        return;
    }
    if (typ.kind == PS_JSON_BOOL) {
        int32_t v = 0;
        memcpy(&v, value, 4);
        if (v) {
            ps_grow_add(g, "true", 4);
        } else {
            ps_grow_add(g, "false", 5);
        }
        return;
    }
    if (typ.kind == PS_JSON_FLOAT) {
        double v = 0.0;
        memcpy(&v, value, 8);
        ps_dump_float(v, g);
        return;
    }
    if (typ.kind == PS_JSON_OPT) {
        char *cell = NULL;
        memcpy(&cell, value, 8);
        if (cell[0] == 0) {
            ps_grow_add(g, "null", 4);
            return;
        }
        PsType child = types[typ.child];
        ps_dump_fmt(types, typ.child, cell + ps_opt_off(child.kind), g, indent, level, sort_keys, compact, raw);
        return;
    }
    if (typ.kind == PS_JSON_STR) {
        PsBuf buf;
        memcpy(&buf, value, 16);
        ps_dump_str(buf, g, raw);
        return;
    }
    if (typ.kind == PS_JSON_LIST) {
        PsSeq *seq = NULL;
        memcpy(&seq, value, 8);
        ps_grow_byte(g, '[');
        if (seq->len == 0) {
            ps_grow_byte(g, ']');
            return;
        }
        for (int64_t i = 0; i < seq->len; i++) {
            if (i > 0) {
                ps_grow_byte(g, ',');
                if (!indent && !compact) {
                    ps_grow_byte(g, ' ');
                }
            }
            if (indent) {
                ps_dump_pad(g, indent, level + 1);
            }
            ps_dump_fmt(types, typ.child, (char *)seq->data + (size_t)i * (size_t)seq->elem, g, indent, level + 1, sort_keys, compact, raw);
        }
        if (indent) {
            ps_dump_pad(g, indent, level);
        }
        ps_grow_byte(g, ']');
        return;
    }
    if (typ.kind == PS_JSON_DICT) {
        PsMap *map = NULL;
        memcpy(&map, value, 8);
        int64_t n = ps_map_len(map);
        /* The key order the map carries, or the sorted order when the
           caller asked for sorted keys. */
        int64_t *order = NULL;
        if (sort_keys && n > 1) {
            order = malloc((size_t)n * sizeof(int64_t));
            if (order == NULL) {
                abort();
            }
            for (int64_t i = 0; i < n; i++) {
                order[i] = i;
            }
            for (int64_t i = 1; i < n; i++) {
                int64_t take = order[i];
                int64_t j = i - 1;
                PsBuf key;
                ps_map_key_at(map, take, &key);
                while (j >= 0) {
                    PsBuf prev;
                    ps_map_key_at(map, order[j], &prev);
                    size_t cmp_len = (size_t)(key.len < prev.len ? key.len : prev.len);
                    int cmp = memcmp(key.data, prev.data, cmp_len);
                    if (cmp > 0 || (cmp == 0 && key.len >= prev.len)) {
                        break;
                    }
                    order[j + 1] = order[j];
                    j -= 1;
                }
                order[j + 1] = take;
            }
        }
        ps_grow_byte(g, '{');
        if (n == 0) {
            ps_grow_byte(g, '}');
            free(order);
            return;
        }
        for (int64_t i = 0; i < n; i++) {
            int64_t at = order ? order[i] : i;
            if (i > 0) {
                ps_grow_byte(g, ',');
                if (!indent && !compact) {
                    ps_grow_byte(g, ' ');
                }
            }
            if (indent) {
                ps_dump_pad(g, indent, level + 1);
            }
            PsBuf key;
            ps_map_key_at(map, at, &key);
            ps_dump_str(key, g, raw);
            ps_grow_byte(g, ':');
            /* The indented forms keep the space after the colon, the way
               json.dumps(indent=n) and orjson's OPT_INDENT_2 do. */
            if (!compact || indent) {
                ps_grow_byte(g, ' ');
            }
            unsigned char slot[16];
            memset(slot, 0, sizeof slot);
            ps_map_val_at(map, at, slot);
            ps_dump_fmt(types, typ.child, slot, g, indent, level + 1, sort_keys, compact, raw);
        }
        if (indent) {
            ps_dump_pad(g, indent, level);
        }
        ps_grow_byte(g, '}');
        free(order);
        return;
    }
    ps_json_fail("JSON type is not lowered");
}

void ps_json_dumps_opts(const PsType *types, int32_t root, void *value, int32_t flags, PsBuf *out);

void ps_json_dumps(const PsType *types, int32_t root, void *value, PsBuf *out) {
    ps_json_dumps_opts(types, root, value, 0, out);
}

void ps_json_dumps_opts(const PsType *types, int32_t root, void *value, int32_t flags, PsBuf *out) {
    PsGrow g;
    ps_grow_init(&g);
    int32_t indent = (flags >> 8) & 0xff;
    int32_t sort_keys = flags & PS_JSON_SORT_KEYS;
    int32_t compact = flags & PS_JSON_COMPACT;
    int32_t raw = flags & PS_JSON_RAW_ASCII;
    ps_dump_fmt(types, root, value, &g, indent, 0, sort_keys, compact, raw);
    ps_grow_finish(&g, out);
}
