/* R16: `%` interpolation and `str.format` — one runtime engine.

   The format text is parsed at runtime so non-literal receivers work
   (`logging.BASIC_FORMAT % row`, `fmt.format(...)`). Arguments arrive as a
   packed PsArg array; for `str.format` the first `nkw` entries hold the
   keyword names (str kind), the next `npos = an - 2*nkw` entries the
   positional arguments and the last `nkw` the keyword values. `style`
   selects the grammar: 0 is percent, 1 is str.format, 2 is percent with a
   mapping operand — `'%(key)s' % d`, where args[0] is the PsMap and stays
   addressable as positional argument 0 as well. */

#include "ps_rt.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum {
    PS_ARG_INT = 1,
    PS_ARG_FLOAT = 2,
    PS_ARG_STR = 3,
    PS_ARG_BOOL = 4,
    PS_ARG_BYTES = 6,
    PS_ARG_MAP = 7
};

typedef struct {
    char fill;     /* 0 = unset */
    char align;    /* '<' '>' '=' '^' ; 0 = unset */
    char sign;     /* '+' '-' ' ' ; 0 = unset */
    int alt;       /* '#' */
    int zero;      /* '0' */
    char group;    /* ',' '_' ; 0 = none */
    int64_t width; /* -1 = absent */
    int64_t prec;  /* -1 = absent */
    char type;     /* conversion char; 0 = default */
} Spec;

typedef struct {
    const PsArg *args;
    int64_t an;
    int64_t nkw;
    int64_t npos;
    int64_t at;
    int auto_at;
    int used_auto;
    int used_named;
    int style;
} Ctx;

static void grow_text(PsGrow *g, const char *p) { ps_grow_add(g, p, (int64_t)strlen(p)); }

static void grow_fill(PsGrow *g, char c, int64_t n) {
    while (n-- > 0) {
        ps_grow_byte(g, (unsigned char)c);
    }
}

static void grow_i64(PsGrow *g, int64_t v) {
    char tmp[32];
    int wrote = snprintf(tmp, sizeof tmp, "%lld", (long long)v);
    ps_grow_add(g, tmp, wrote);
}

static void grow_f64(PsGrow *g, double v) {
    PsBuf t;
    ps_f64_str(v, &t);
    ps_grow_add(g, t.data, t.len);
}

static void grow_bytes_repr(PsGrow *g, const PsBuf *b) {
    PsBuf t;
    ps_repr_bytes(b->len, b->data, &t);
    ps_grow_add(g, t.data, t.len);
}

static void repr_str_into(PsGrow *g, int64_t n, const char *p, int ascii_only) {
    int quote = '\'';
    if (memchr(p, '\'', (size_t)n) != NULL && memchr(p, '"', (size_t)n) == NULL) {
        quote = '"';
    }
    ps_grow_byte(g, (unsigned char)quote);
    for (int64_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)p[i];
        if (c == (unsigned char)quote || c == '\\') {
            ps_grow_byte(g, '\\');
            ps_grow_byte(g, c);
        } else if (c == '\n') {
            grow_text(g, "\\n");
        } else if (c == '\r') {
            grow_text(g, "\\r");
        } else if (c == '\t') {
            grow_text(g, "\\t");
        } else if (c < 0x20 || c == 0x7f || (ascii_only && c >= 0x80)) {
            char esc[8];
            snprintf(esc, sizeof esc, "\\x%02x", c);
            ps_grow_add(g, esc, 4);
        } else {
            ps_grow_byte(g, c);
        }
    }
    ps_grow_byte(g, (unsigned char)quote);
}

/* `'%s' % d` prints the mapping repr; keys render by the map's key_kind,
   values by the vkind stashed in the PsArg's int slot. Deleted keys keep
   their dense row (marked dead in the index), so the row of iteration
   position i comes from the order cache, not the raw arrays. */
static void repr_map_into(PsGrow *g, const PsArg *a, int ascii_only) {
    PsMap *m = (PsMap *)a->b.data;
    int64_t vkind = a->i;
    char *key = malloc((size_t)m->key_size);
    char *val = malloc((size_t)m->val_size);
    if (key == NULL || val == NULL) {
        abort();
    }
    ps_grow_byte(g, '{');
    for (int64_t i = 0; i < m->len; i++) {
        if (i > 0) {
            grow_text(g, ", ");
        }
        ps_map_key_at(m, i, key);
        ps_map_val_at(m, i, val);
        if (m->key_kind == 1) {
            int64_t k;
            memcpy(&k, key, 8);
            grow_i64(g, k);
        } else {
            PsBuf k;
            memcpy(&k, key, sizeof k);
            repr_str_into(g, k.len, k.data, ascii_only);
        }
        grow_text(g, ": ");
        if (vkind == 1) {
            int64_t v;
            memcpy(&v, val, 8);
            grow_i64(g, v);
        } else if (vkind == 2) {
            double v;
            memcpy(&v, val, 8);
            grow_f64(g, v);
        } else {
            PsBuf v;
            memcpy(&v, val, sizeof v);
            if (vkind == 6) {
                grow_bytes_repr(g, &v);
            } else {
                repr_str_into(g, v.len, v.data, ascii_only);
            }
        }
    }
    ps_grow_byte(g, '}');
    free(key);
    free(val);
}

static void arg_as_str(PsGrow *g, const PsArg *a) {
    switch (a->kind) {
        case PS_ARG_INT:
            grow_i64(g, a->i);
            return;
        case PS_ARG_FLOAT:
            grow_f64(g, a->f);
            return;
        case PS_ARG_BOOL:
            grow_text(g, a->i ? "True" : "False");
            return;
        case PS_ARG_STR:
            ps_grow_add(g, a->b.data, a->b.len);
            return;
        case PS_ARG_BYTES:
            grow_bytes_repr(g, &a->b);
            return;
        case PS_ARG_MAP:
            repr_map_into(g, a, 0);
            return;
        default:
            ps_raise_cstr(PS_VALUE, "format argument kind is not lowered");
    }
}

static void arg_as_repr(PsGrow *g, const PsArg *a, int ascii_only) {
    if (a->kind == PS_ARG_STR) {
        repr_str_into(g, a->b.len, a->b.data, ascii_only);
        return;
    }
    if (a->kind == PS_ARG_MAP) {
        repr_map_into(g, a, ascii_only);
        return;
    }
    arg_as_str(g, a);
}

/* Emit body padded per spec. head is the count of leading bytes that stay
   ahead of '=' padding (sign plus base prefix); numeric picks the
   numeric default alignment and '0' semantics. The width is a code-point
   count (S3): the body's own byte length n measures text, so callers that
   pad text pass the code-point count of the body in chars; numeric bodies
   are ASCII and pass n. */
static void emit_padded(PsGrow *g, const char *body, int64_t n, int64_t chars, int64_t head, const Spec *s, int numeric) {
    char fill = s->fill ? s->fill : ' ';
    char align = s->align ? s->align : (numeric ? '>' : '<');
    if (s->zero) {
        fill = '0';
        if (numeric && !s->align) {
            align = '=';
        }
    }
    if (s->width <= chars) {
        ps_grow_add(g, body, n);
        return;
    }
    int64_t pad = s->width - chars;
    if (align == '<') {
        ps_grow_add(g, body, n);
        grow_fill(g, fill, pad);
        return;
    }
    if (align == '^') {
        int64_t left = pad / 2;
        grow_fill(g, fill, left);
        ps_grow_add(g, body, n);
        grow_fill(g, fill, pad - left);
        return;
    }
    if (align == '=') {
        ps_grow_add(g, body, head);
        grow_fill(g, fill, pad);
        ps_grow_add(g, body + head, n - head);
        return;
    }
    grow_fill(g, fill, pad);
    ps_grow_add(g, body, n);
}

static void emit_grouped(PsGrow *g, const char *digits, int64_t n, char sep, int64_t step) {
    for (int64_t i = 0; i < n; i++) {
        if (i > 0 && (n - i) % step == 0) {
            ps_grow_byte(g, (unsigned char)sep);
        }
        ps_grow_byte(g, (unsigned char)digits[i]);
    }
}

/* Render an integer with sign-magnitude semantics (CPython's %-formats show
   the sign for x/o/b too). digits and base come from s->type. */
static void emit_int(PsGrow *g, int64_t v, const Spec *s, int percent) {
    char conv = s->type;
    int base = 10;
    int upper = 0;
    const char *prefix = "";
    if (conv == 'x' || conv == 'X') {
        base = 16;
        upper = conv == 'X';
        prefix = conv == 'X' ? "0X" : "0x";
    } else if (conv == 'o') {
        base = 8;
        prefix = "0o";
    } else if (conv == 'b') {
        base = 2;
        prefix = "0b";
    }
    char raw[96];
    int64_t dn = 0;
    uint64_t mag = v < 0 ? (uint64_t)(-(v + 1)) + 1 : (uint64_t)v;
    static const char lo[] = "0123456789abcdef";
    static const char hi[] = "0123456789ABCDEF";
    const char *digits = upper ? hi : lo;
    do {
        raw[dn++] = digits[mag % (uint64_t)base];
        mag /= (uint64_t)base;
    } while (mag > 0);
    while (percent && s->prec > dn) {
        raw[dn++] = '0';
    }
    for (int64_t i = 0; i < dn / 2; i++) {
        char c = raw[i];
        raw[i] = raw[dn - 1 - i];
        raw[dn - 1 - i] = c;
    }
    PsGrow body;
    ps_grow_init(&body);
    if (v < 0) {
        ps_grow_byte(&body, '-');
    } else if (s->sign) {
        ps_grow_byte(&body, (unsigned char)s->sign);
    }
    if (s->alt && *prefix) {
        grow_text(&body, prefix);
    }
    int64_t head = body.len;
    if (s->group && !percent) {
        int64_t step = s->group == ',' ? 3 : (base == 10 ? 3 : 4);
        emit_grouped(&body, raw, dn, s->group, step);
    } else {
        ps_grow_add(&body, raw, dn);
    }
    emit_padded(g, body.data ? body.data : "", body.len, body.len, head, s, 1);
    free(body.data);
}

/* snprintf renders the number; we add sign/default fill and grouping. */
static void emit_f64(PsGrow *g, double v, const Spec *s, int percent, int fixup) {
    char conv = s->type ? s->type : 'g';
    int is_pct = 0;
    if (conv == '%') {
        conv = 'f';
        v *= 100.0;
        is_pct = 1;
    } else if (conv == 'n') {
        conv = 'g';
        fixup = 0;
    }
    char fmt[32];
    char *w = fmt;
    *w++ = '%';
    if (s->sign) {
        *w++ = s->sign;
    }
    if (s->alt) {
        *w++ = '#';
    }
    if (s->prec >= 0) {
        w += sprintf(w, ".%lld", (long long)s->prec);
    }
    *w++ = conv;
    *w = '\0';
    int need = snprintf(NULL, 0, fmt, v);
    if (need < 0) {
        ps_raise_cstr(PS_VALUE, "float formatting failed");
    }
    char *buf = malloc((size_t)need + 1);
    if (buf == NULL) {
        abort();
    }
    snprintf(buf, (size_t)need + 1, fmt, v);
    if (fixup && !strchr(buf, '.') && !strchr(buf, 'e') && !strchr(buf, 'E') && !strchr(buf, 'n') &&
        !strchr(buf, 'i')) {
        buf[need++] = '.';
        buf[need++] = '0';
        buf[need] = '\0';
    }
    PsGrow body;
    ps_grow_init(&body);
    if (s->group && !percent) {
        int64_t start = (buf[0] == '-' || buf[0] == '+' || buf[0] == ' ') ? 1 : 0;
        int64_t end = start;
        while (buf[end] >= '0' && buf[end] <= '9') {
            end++;
        }
        ps_grow_add(&body, buf, start);
        emit_grouped(&body, buf + start, end - start, s->group, 3);
        grow_text(&body, buf + end);
    } else {
        grow_text(&body, buf);
    }
    if (is_pct) {
        ps_grow_byte(&body, '%');
    }
    int64_t head = (body.len > 0 && (body.data[0] == '-' || body.data[0] == '+' || body.data[0] == ' ')) ? 1 : 0;
    emit_padded(g, body.data ? body.data : "", body.len, body.len, head, s, 1);
    free(body.data);
    free(buf);
}

/* str (and bytes through its repr) formatting. The precision cuts code
   points and the width counts code points (S3). */
static void emit_text(PsGrow *g, const char *p, int64_t n, const Spec *s) {
    if (s->prec >= 0 && s->prec < ps_str_nchars(n, p)) {
        n = ps_str_offset(n, p, s->prec);
    }
    emit_padded(g, p, n, ps_str_nchars(n, p), 0, s, 0);
}

static void bad_spec(const char *msg) { ps_raise_cstr(PS_VALUE, msg); }

static void bad_type(char t, const char *what) {
    static char msg[96];
    snprintf(msg, sizeof msg, "Unknown format code '%c' for object of type '%s'", t, what);
    ps_raise_cstr(PS_VALUE, msg);
}

/* ---- `%` grammar --------------------------------------------------- */

static const PsArg *next_pos(Ctx *c) {
    if (c->at >= c->npos) {
        if (c->style == 1) {
            ps_raise_cstr(PS_INDEX, "Replacement index out of range for positional args tuple");
        }
        ps_raise_cstr(PS_VALUE, "not enough arguments for format string");
    }
    const PsArg *a = &c->args[c->nkw + c->at];
    c->at++;
    return a;
}

static int64_t arg_int(const PsArg *a) {
    if (a->kind == PS_ARG_INT || a->kind == PS_ARG_BOOL) {
        return a->i;
    }
    ps_raise_cstr(PS_VALUE, "a real number is required");
}

static double arg_f64(const PsArg *a) {
    if (a->kind == PS_ARG_FLOAT) {
        return a->f;
    }
    if (a->kind == PS_ARG_INT || a->kind == PS_ARG_BOOL) {
        return (double)a->i;
    }
    ps_raise_cstr(PS_VALUE, "a real number is required");
}

static int64_t percent_int(Ctx *c, const char *what) {
    const PsArg *a = next_pos(c);
    if (a->kind != PS_ARG_INT && a->kind != PS_ARG_BOOL) {
        static char msg[80];
        snprintf(msg, sizeof msg, "%%%s wants int", what);
        ps_raise_cstr(PS_VALUE, msg);
    }
    return a->i;
}

static void percent_spec(PsGrow *g, const PsArg *arg, Spec *s, char t) {
    if (t == 'd' || t == 'i' || t == 'u' || t == 'x' || t == 'X' || t == 'o') {
        int64_t v;
        if (arg->kind == PS_ARG_FLOAT) {
            if (t == 'x' || t == 'X' || t == 'o') {
                ps_raise_cstr(PS_VALUE, "an integer is required, not float");
            }
            v = (int64_t)arg->f;
        } else {
            v = arg_int(arg);
        }
        emit_int(g, v, s, 1);
        return;
    }
    if (t == 'e' || t == 'E' || t == 'f' || t == 'F' || t == 'g' || t == 'G') {
        emit_f64(g, arg_f64(arg), s, 1, 0);
        return;
    }
    if (t == 'c') {
        int64_t ch = -1;
        const char *text = NULL;
        if (arg->kind == PS_ARG_INT || arg->kind == PS_ARG_BOOL) {
            ch = arg->i;
            /* CPython 3 str semantics: %c takes any scalar code point; the
               0..255 range belonged to the bytes model. */
            if (ch < 0 || ch > 0x10FFFF || (ch >= 0xD800 && ch <= 0xDFFF)) {
                ps_raise_cstr(PS_VALUE, "%c arg not in range(0x110000)");
            }
        } else if ((arg->kind == PS_ARG_STR || arg->kind == PS_ARG_BYTES) &&
                   ps_str_nchars(arg->b.len, arg->b.data) == 1) {
            text = arg->b.data;
        } else {
            ps_raise_cstr(PS_VALUE, "%c requires int or char");
        }
        s->zero = 0;
        if (text != NULL) {
            emit_padded(g, text, arg->b.len, 1, 0, s, 1);
            return;
        }
        if (ch < 0x80) {
            char byte = (char)ch;
            emit_padded(g, &byte, 1, 1, 0, s, 1);
            return;
        }
        PsGrow enc;
        ps_grow_init(&enc);
        int width = ch < 0x800 ? 2 : ch < 0x10000 ? 3 : 4;
        static const unsigned char heads[3] = {0xC0, 0xE0, 0xF0};
        ps_grow_byte(&enc, (unsigned char)(heads[width - 2] | (ch >> (6 * (width - 1)))));
        for (int i = width - 2; i >= 0; i--) {
            ps_grow_byte(&enc, (unsigned char)(0x80 | ((ch >> (6 * i)) & 0x3F)));
        }
        emit_padded(g, enc.data ? enc.data : "", enc.len, 1, 0, s, 1);
        free(enc.data);
        return;
    }
    PsGrow body;
    ps_grow_init(&body);
    if (t == 's') {
        arg_as_str(&body, arg);
    } else if (t == 'r' || t == 'a') {
        arg_as_repr(&body, arg, t == 'a');
    } else {
        static char msg[80];
        snprintf(msg, sizeof msg, "unsupported format character '%c'", t);
        ps_raise_cstr(PS_VALUE, msg);
    }
    s->zero = 0;
    int64_t n = body.len;
    /* %s with a precision cuts the text's code points; %r of a str goes
       through the repr path above, whose escapes are ASCII, so the cut
       splits code points for the former and bytes for the latter. */
    if (t == 's' && s->prec >= 0 && s->prec < ps_str_nchars(n, body.data)) {
        n = ps_str_offset(n, body.data, s->prec);
    } else if (t != 's' && s->prec >= 0 && s->prec < n) {
        n = s->prec;
    }
    emit_padded(g, body.data ? body.data : "", n, ps_str_nchars(n, body.data), 0, s, 1);
    free(body.data);
}

/* ---- `str.format` grammar ------------------------------------------ */

static int64_t parse_field_index(const char *name, int64_t nn, int *is_num) {
    if (nn == 0) {
        return 0;
    }
    int64_t v = 0;
    int64_t i = 0;
    while (i < nn && name[i] >= '0' && name[i] <= '9') {
        v = v * 10 + (name[i] - '0');
        i++;
    }
    *is_num = i == nn;
    return v;
}

/* Resolve `digits` | `ident` | that + `[key]` against args. `out` receives
   the PsArg for a plain field; for an item field we render it into the
   caller-provided PsArg `item`. Returns the PsArg to format. */
static const PsArg *resolve_field(Ctx *c, const char *field, int64_t fn, PsArg *item) {
    const char *bracket = memchr(field, '[', (size_t)fn);
    const char *dot = memchr(field, '.', (size_t)fn);
    int64_t nn = fn;
    if (bracket != NULL) {
        nn = bracket - field;
    } else if (dot != NULL) {
        nn = dot - field;
    }
    const PsArg *arg;
    int is_num;
    if (nn == 0) {
        if (c->used_named) {
            ps_raise_cstr(PS_VALUE,
                          "cannot switch from manual field specification to automatic field numbering");
        }
        c->used_auto = 1;
        if (c->auto_at >= c->npos) {
            ps_raise_cstr(PS_INDEX, "Replacement index out of range for positional args tuple");
        }
        arg = &c->args[c->nkw + c->auto_at];
        c->auto_at++;
    } else {
        int64_t idx = parse_field_index(field, nn, &is_num);
        if (is_num) {
            if (c->used_auto) {
                ps_raise_cstr(
                    PS_VALUE,
                    "cannot switch from automatic field numbering to manual field specification");
            }
            c->used_named = 1;
            if (idx >= c->npos) {
                ps_raise_cstr(PS_INDEX, "Replacement index out of range for positional args tuple");
            }
            arg = &c->args[c->nkw + idx];
        } else {
            int64_t i;
            for (i = 0; i < c->nkw; i++) {
                const PsBuf *nm = &c->args[i].b;
                if (nm->len == nn && memcmp(nm->data, field, (size_t)nn) == 0) {
                    break;
                }
            }
            if (i >= c->nkw) {
                ps_raise_cstr(PS_KEY, "keyword argument not found");
            }
            arg = &c->args[c->nkw + c->npos + i];
        }
    }
    if (dot != NULL && (bracket == NULL || dot < bracket)) {
        ps_raise_cstr(PS_VALUE, "format attribute access is not lowered");
    }
    if (bracket != NULL) {
        const char *close = memchr(bracket, ']', (size_t)(field + fn - bracket));
        if (close == NULL || close + 1 != field + fn) {
            ps_raise_cstr(PS_VALUE, "bad format field syntax");
        }
        const char *key = bracket + 1;
        int64_t kn = close - key;
        int key_num;
        int64_t kidx = parse_field_index(key, kn, &key_num);
        item->kind = PS_ARG_STR;
        item->i = 0;
        item->f = 0.0;
        item->b.len = 0;
        item->b.data = &ps_empty_byte;
        if (arg->kind == PS_ARG_STR || arg->kind == PS_ARG_BYTES) {
            if (!key_num || kidx < 0 || kidx >= arg->b.len) {
                ps_raise_cstr(PS_INDEX, "format index out of range");
            }
            item->kind = arg->kind;
            item->b.len = 1;
            item->b.data = arg->b.data + kidx;
        } else if (arg->kind == PS_ARG_MAP) {
            PsMap *m = (PsMap *)arg->b.data;
            if (key_num) {
                if (m->key_kind != 1) {
                    ps_raise_cstr(PS_KEY, "format key not found");
                }
                int64_t kv = kidx;
                int64_t vkind = arg->i;
                if (vkind == 1) {
                    int64_t val;
                    ps_map_get(m, &kv, &val);
                    item->kind = PS_ARG_INT;
                    item->i = val;
                } else if (vkind == 2) {
                    double val;
                    ps_map_get(m, &kv, &val);
                    item->kind = PS_ARG_FLOAT;
                    item->f = val;
                } else {
                    PsBuf val;
                    ps_map_get(m, &kv, &val);
                    item->kind = vkind == 6 ? PS_ARG_BYTES : PS_ARG_STR;
                    item->b = val;
                }
            } else {
                if (m->key_kind != 2) {
                    ps_raise_cstr(PS_KEY, "format key not found");
                }
                PsBuf kv = {kn, (char *)key};
                int64_t vkind = arg->i;
                if (vkind == 1) {
                    int64_t val;
                    ps_map_get(m, &kv, &val);
                    item->kind = PS_ARG_INT;
                    item->i = val;
                } else if (vkind == 2) {
                    double val;
                    ps_map_get(m, &kv, &val);
                    item->kind = PS_ARG_FLOAT;
                    item->f = val;
                } else {
                    PsBuf val;
                    ps_map_get(m, &kv, &val);
                    item->kind = vkind == 6 ? PS_ARG_BYTES : PS_ARG_STR;
                    item->b = val;
                }
            }
        } else {
            ps_raise_cstr(PS_VALUE, "format item access is not lowered for this type");
        }
        return item;
    }
    return arg;
}

/* Nested `{name}` inside a spec: splice the argument's str form in place. */
static void resolve_spec(Ctx *c, const char *spec, int64_t sn, PsGrow *out) {
    for (int64_t i = 0; i < sn; i++) {
        if (spec[i] == '{') {
            const char *close = memchr(spec + i, '}', (size_t)(sn - i));
            if (close == NULL) {
                ps_raise_cstr(PS_VALUE, "expected '}' before end of string");
            }
            int64_t nn = close - spec - i - 1;
            const char *inner = spec + i + 1;
            if (memchr(inner, '{', (size_t)nn) || memchr(inner, ':', (size_t)nn) ||
                memchr(inner, '!', (size_t)nn)) {
                ps_raise_cstr(PS_VALUE, "Max string recursion exceeded");
            }
            PsArg item;
            const PsArg *arg = resolve_field(c, inner, nn, &item);
            arg_as_str(out, arg);
            i += nn + 1;
        } else if (spec[i] == '}') {
            ps_raise_cstr(PS_VALUE, "Single '}' encountered in format string");
        } else {
            ps_grow_byte(out, (unsigned char)spec[i]);
        }
    }
}

static void parse_dot_spec(const char *spec, int64_t sn, Spec *s) {
    int64_t i = 0;
    s->fill = 0;
    s->align = 0;
    s->sign = 0;
    s->alt = 0;
    s->zero = 0;
    s->group = 0;
    s->width = -1;
    s->prec = -1;
    s->type = 0;
    if (sn >= 2 && strchr("<>=^", spec[1]) != NULL) {
        if (spec[0] == '{' || spec[0] == '}') {
            bad_spec("Invalid format specifier");
        }
        s->fill = spec[0];
        s->align = spec[1];
        i = 2;
    } else if (sn >= 1 && strchr("<>=^", spec[0]) != NULL) {
        s->align = spec[0];
        i = 1;
    }
    if (i < sn && (spec[i] == '+' || spec[i] == '-' || spec[i] == ' ')) {
        s->sign = spec[i++];
    }
    if (i < sn && spec[i] == '#') {
        s->alt = 1;
        i++;
    }
    if (i < sn && spec[i] == '0') {
        s->zero = 1;
        i++;
    }
    while (i < sn && spec[i] >= '0' && spec[i] <= '9') {
        if (s->width < 0) {
            s->width = 0;
        }
        s->width = s->width * 10 + (spec[i] - '0');
        i++;
    }
    if (i < sn && (spec[i] == ',' || spec[i] == '_')) {
        s->group = spec[i++];
    }
    if (i < sn && spec[i] == '.') {
        i++;
        if (i >= sn || spec[i] < '0' || spec[i] > '9') {
            bad_spec("Format specifier missing precision");
        }
        s->prec = 0;
        while (i < sn && spec[i] >= '0' && spec[i] <= '9') {
            s->prec = s->prec * 10 + (spec[i] - '0');
            i++;
        }
    }
    if (i < sn) {
        if (i + 1 < sn) {
            bad_spec("Invalid format specifier");
        }
        s->type = spec[i];
    }
}

static void apply_dot(PsGrow *g, const PsArg *arg, const Spec *s) {
    int kind = arg->kind;
    char t = s->type;
    if (kind == PS_ARG_STR || kind == PS_ARG_BYTES) {
        if (kind == PS_ARG_BYTES) {
            if (t || s->align || s->fill || s->sign || s->width >= 0 || s->prec >= 0 || s->group ||
                s->alt || s->zero) {
                ps_raise_cstr(PS_VALUE, "unsupported format string passed to bytes.__format__");
            }
            PsGrow body;
            ps_grow_init(&body);
            grow_bytes_repr(&body, &arg->b);
            ps_grow_add(g, body.data, body.len);
            free(body.data);
            return;
        }
        if (t && t != 's') {
            bad_type(t, "str");
        }
        if (s->align == '=') {
            bad_spec("'=' alignment not allowed in string format specifier");
        }
        if (s->sign) {
            bad_spec("Sign not allowed in string format specifier");
        }
        if (s->alt) {
            bad_spec("Alternate form (#) not allowed in string format specifier");
        }
        if (s->group) {
            bad_spec("Cannot specify ',' or '_' with 's'.");
        }
        emit_text(g, arg->b.data, arg->b.len, s);
        return;
    }
    if (kind == PS_ARG_MAP) {
        if (t || s->align || s->fill || s->sign || s->width >= 0 || s->prec >= 0 || s->group || s->alt ||
            s->zero) {
            ps_raise_cstr(PS_VALUE, "unsupported format string passed to dict.__format__");
        }
        repr_map_into(g, arg, 0);
        return;
    }
    if (kind == PS_ARG_BOOL && t == 0 && s->width < 0 && s->prec < 0 && s->align == 0 && s->fill == 0 &&
        !s->sign && !s->alt && !s->group && !s->zero) {
        grow_text(g, arg->i ? "True" : "False");
        return;
    }
    if (kind == PS_ARG_INT || kind == PS_ARG_BOOL) {
        if (t == 's') {
            bad_type(t, "int");
        }
        if (t && !strchr("bcdoxXneEfFgG%", (int)t)) {
            bad_type(t, "int");
        }
        if (s->prec >= 0 && strchr("eEfFgG%", (int)t) == NULL) {
            bad_spec("Precision not allowed in integer format specifier");
        }
        if (s->group == ',' && t && strchr("xXob", (int)t) != NULL) {
            bad_spec("Cannot specify ',' with 'x'.");
        }
        if (t == 'c') {
            int64_t ch = arg->i;
            /* CPython 3 str semantics: %c takes any scalar code point; the
               0..255 range belonged to the bytes model. */
            if (ch < 0 || ch > 0x10FFFF || (ch >= 0xD800 && ch <= 0xDFFF)) {
                ps_raise_cstr(PS_VALUE, "%c arg not in range(0x110000)");
            }
            if (ch < 0x80) {
                char byte = (char)ch;
                emit_padded(g, &byte, 1, 1, 0, s, 1);
                return;
            }
            PsGrow enc;
            ps_grow_init(&enc);
            int width = ch < 0x800 ? 2 : ch < 0x10000 ? 3 : 4;
            static const unsigned char heads[3] = {0xC0, 0xE0, 0xF0};
            ps_grow_byte(&enc, (unsigned char)(heads[width - 2] | (ch >> (6 * (width - 1)))));
            for (int i = width - 2; i >= 0; i--) {
                ps_grow_byte(&enc, (unsigned char)(0x80 | ((ch >> (6 * i)) & 0x3F)));
            }
            emit_padded(g, enc.data ? enc.data : "", enc.len, 1, 0, s, 1);
            free(enc.data);
            return;
        }
        if (t && strchr("eEfFgG%", (int)t) != NULL) {
            emit_f64(g, (double)arg->i, s, 0, 0);
            return;
        }
        emit_int(g, arg->i, s, 0);
        return;
    }
    if (kind == PS_ARG_FLOAT) {
        if (t == 's') {
            bad_type(t, "float");
        }
        if (t && !strchr("eEfFgG%n", (int)t)) {
            bad_type(t, "float");
        }
        emit_f64(g, arg->f, s, 0, t == 0);
        return;
    }
    ps_raise_cstr(PS_VALUE, "format argument kind is not lowered");
}

static void emit_dot_field(PsGrow *g, Ctx *c, const char *field, int64_t fn) {
    int64_t cut = fn;
    for (int64_t i = 0; i < fn; i++) {
        if (field[i] == '!' || field[i] == ':') {
            cut = i;
            break;
        }
    }
    PsArg item;
    const PsArg *arg = resolve_field(c, field, cut, &item);
    char conv = 0;
    int64_t i = cut;
    if (i < fn && field[i] == '!') {
        if (i + 1 >= fn || strchr("sra", field[i + 1]) == NULL) {
            ps_raise_cstr(PS_VALUE, "Unknown conversion specifier");
        }
        conv = field[i + 1];
        i += 2;
    }
    const char *spec = NULL;
    int64_t sn = 0;
    if (i < fn && field[i] == ':') {
        spec = field + i + 1;
        sn = fn - i - 1;
    } else if (i < fn) {
        ps_raise_cstr(PS_VALUE, "bad format field syntax");
    }
    PsGrow resolved;
    ps_grow_init(&resolved);
    if (spec != NULL) {
        resolve_spec(c, spec, sn, &resolved);
        spec = resolved.data;
        sn = resolved.len;
    }
    Spec s;
    parse_dot_spec(spec ? spec : "", sn, &s);
    free(resolved.data);
    if (conv) {
        PsGrow body;
        ps_grow_init(&body);
        if (conv == 's') {
            arg_as_str(&body, arg);
        } else {
            arg_as_repr(&body, arg, conv == 'a');
        }
        PsArg tmp = *arg;
        tmp.kind = PS_ARG_STR;
        tmp.b.len = body.len;
        tmp.b.data = body.data ? body.data : &ps_empty_byte;
        apply_dot(g, &tmp, &s);
        free(body.data);
        return;
    }
    apply_dot(g, arg, &s);
}

/* ---- entry point ---------------------------------------------------- */

void ps_str_format(int64_t fn, const char *f, const PsArg *args, int64_t an, int64_t nkw, int64_t style,
                   PsBuf *out) {
    Ctx c = {args, an, nkw, an - 2 * nkw, 0, 0, 0, 0, (int)style};
    PsGrow g;
    ps_grow_init(&g);
    int64_t i = 0;
    if (style == 1) {
        while (i < fn) {
            char ch = f[i];
            if (ch == '{') {
                if (i + 1 < fn && f[i + 1] == '{') {
                    ps_grow_byte(&g, '{');
                    i += 2;
                    continue;
                }
                int64_t j = i + 1;
                int depth = 1;
                while (j < fn) {
                    if (f[j] == '{') {
                        depth++;
                    } else if (f[j] == '}') {
                        depth--;
                        if (depth == 0) {
                            break;
                        }
                    }
                    j++;
                }
                if (depth > 0) {
                    ps_raise_cstr(PS_VALUE, "expected '}' before end of string");
                }
                emit_dot_field(&g, &c, f + i + 1, j - i - 1);
                i = j + 1;
            } else if (ch == '}') {
                if (i + 1 < fn && f[i + 1] == '}') {
                    ps_grow_byte(&g, '}');
                    i += 2;
                } else {
                    ps_raise_cstr(PS_VALUE, "Single '}' encountered in format string");
                }
            } else {
                ps_grow_byte(&g, (unsigned char)ch);
                i++;
            }
        }
        ps_grow_finish(&g, out);
        return;
    }
    while (i < fn) {
        if (f[i] != '%') {
            ps_grow_byte(&g, (unsigned char)f[i]);
            i++;
            continue;
        }
        i++;
        if (i >= fn) {
            ps_raise_cstr(PS_VALUE, "incomplete format");
        }
        if (f[i] == '%') {
            ps_grow_byte(&g, '%');
            i++;
            continue;
        }
        const char *key = NULL;
        int64_t kn = 0;
        if (f[i] == '(') {
            const char *close = memchr(f + i, ')', (size_t)(fn - i));
            if (close == NULL) {
                ps_raise_cstr(PS_VALUE, "incomplete format key");
            }
            key = f + i + 1;
            kn = close - key;
            i = (close - f) + 1;
        }
        Spec s;
        s.fill = 0;
        s.align = 0;
        s.sign = 0;
        s.alt = 0;
        s.zero = 0;
        s.group = 0;
        s.width = -1;
        s.prec = -1;
        s.type = 0;
        while (i < fn && strchr("-+ #0", f[i]) != NULL) {
            if (f[i] == '-') {
                s.align = '<';
            } else if (f[i] == '+') {
                s.sign = '+';
            } else if (f[i] == ' ') {
                s.sign = ' ';
            } else if (f[i] == '#') {
                s.alt = 1;
            } else {
                s.zero = 1;
            }
            i++;
        }
        if (i < fn && f[i] == '*') {
            i++;
            int64_t w = percent_int(&c, "width");
            if (w < 0) {
                s.align = '<';
                w = -w;
            }
            s.width = w;
        } else {
            int64_t w = -1;
            while (i < fn && f[i] >= '0' && f[i] <= '9') {
                if (w < 0) {
                    w = 0;
                }
                w = w * 10 + (f[i] - '0');
                i++;
            }
            s.width = w;
        }
        if (i < fn && f[i] == '.') {
            i++;
            if (i < fn && f[i] == '*') {
                i++;
                int64_t p = percent_int(&c, "precision");
                s.prec = p < 0 ? -1 : p;
            } else {
                s.prec = 0;
                while (i < fn && f[i] >= '0' && f[i] <= '9') {
                    s.prec = s.prec * 10 + (f[i] - '0');
                    i++;
                }
            }
        }
        while (i < fn && (f[i] == 'h' || f[i] == 'l' || f[i] == 'L')) {
            i++;
        }
        if (i >= fn) {
            ps_raise_cstr(PS_VALUE, "incomplete format");
        }
        char t = f[i++];
        s.type = t;
        const PsArg *arg;
        PsArg val;
        if (key != NULL) {
            if (style != 2 || an < 1 || args[0].kind != PS_ARG_MAP) {
                ps_raise_cstr(PS_VALUE, "format requires a mapping");
            }
            PsMap *m = (PsMap *)args[0].b.data;
            if (m->key_kind != 2) {
                ps_raise_cstr(PS_KEY, "format key not found");
            }
            PsBuf kv = {kn, (char *)key};
            int64_t vkind = args[0].i;
            if (vkind == 1) {
                int64_t v;
                ps_map_get(m, &kv, &v);
                val.kind = PS_ARG_INT;
                val.i = v;
            } else if (vkind == 2) {
                double v;
                ps_map_get(m, &kv, &v);
                val.kind = PS_ARG_FLOAT;
                val.f = v;
            } else {
                PsBuf v;
                ps_map_get(m, &kv, &v);
                val.kind = vkind == 6 ? PS_ARG_BYTES : PS_ARG_STR;
                val.b = v;
            }
            arg = &val;
            /* `%(k)` also exhausts the bare dict as positional arg 0, so a
               following plain spec sees "not enough arguments". */
            if (c.at == 0) {
                c.at = 1;
            }
        } else {
            arg = next_pos(&c);
        }
        percent_spec(&g, arg, &s, t);
    }
    if (c.at < c.npos && !(style == 2 && c.at == 0)) {
        ps_raise_cstr(PS_VALUE, "not all arguments converted during string formatting");
    }
    ps_grow_finish(&g, out);
}
