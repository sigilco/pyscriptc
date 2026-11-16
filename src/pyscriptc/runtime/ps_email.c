/* SPDX-License-Identifier: Apache-2.0 */

/* email: the closed MIME read surface.

`email.message_from_string(text)` and `email.message_from_bytes(data)`
parse a MIME message into a runtime record: the header map (the first
value of each field name, CPython's `Message.get` rule), the header order
(the names in wire order, CPython's `keys()`/`items()` order), the content
type, the multipart boundary, the decoded filename and the payload. A
multipart payload holds the parsed child parts; a flat payload holds the
raw body bytes.

The parser reads the RFC 5322 header block line by line: a header line is
`Name: value`, a folded continuation line starts with whitespace and
appends to the previous value (the folding whitespace survives, the way
CPython's compat32 policy keeps it), and the first empty line starts the
body. Header names are ASCII and compare case-insensitively (`get`
lowers both sides); the stored key keeps the wire spelling so `keys()`
reports what the message carried.

Multipart bodies split on the `boundary` parameter of the Content-Type
header, exactly RFC 2046's rule: the boundary delimiter line is
`--boundary`, the final one is `--boundary--`, and preamble/epilogue text
outside the parts is dropped. Each part re-enters the header parser. A
nested multipart inside a part recurses through the same split.

Header decoding: RFC 2047 encoded words (`=?charset?enc?text?=`) in
`get`, `get_all` and the `items()` values decode like CPython's
`email.header.decode_header` + `make_header` round-trip does for the
us-ascii and utf-8 charsets the subset reads; an unknown charset leaves
the raw word (CPython would produce a Header whose `__str__` keeps the
raw text too). Quoted-printable and base64 transfer encodings decode in
`get_payload(decode=True)`; every other encoding returns the raw bytes
(the CPython call would too, for an unknown encoding).

Writing is out of scope: the surface reads a message. `as_string()`
re-emits the parsed record's headers and payload, which is the round-trip
a filter program needs, not a general MIME generator.
*/

#include "ps_rt.h"

#include <stdlib.h>
#include <string.h>

/* One parsed message (or one part of a multipart tree). */
typedef struct PsEmailPart {
    PsMap *headers;  /* lowercased name -> first raw value */
    PsSeq *names;    /* wire-spelled names, in order */
    PsSeq *values;   /* raw values, in order (get_all / items) */
    PsSeq *parts;    /* the multipart children (PsEmailPart*) */
    char *boundary;  /* the multipart boundary parameter */
    char *content_type; /* the full Content-Type value, "" when absent */
    char *filename;  /* the decoded name parameter, NULL when absent */
    char *body;      /* the raw payload bytes */
    int64_t body_len;
    int32_t is_multipart;
} PsEmailPart;

static char *ps_email_dup(const char *p, int64_t n) {
    char *out = malloc((size_t)n + 1);
    if (out == NULL) {
        abort();
    }
    if (n > 0) {
        memcpy(out, p, (size_t)n);
    }
    out[n] = '\0';
    return out;
}

static int ps_email_ieq(const char *a, const char *b) {
    while (*a && *b) {
        char ca = *a, cb = *b;
        if (ca >= 'A' && ca <= 'Z') {
            ca = (char)(ca - 'A' + 'a');
        }
        if (cb >= 'A' && cb <= 'Z') {
            cb = (char)(cb - 'A' + 'a');
        }
        if (ca != cb) {
            return 0;
        }
        a++;
        b++;
    }
    return *a == *b;
}

/* The lowercase spelling of a header name, the map key `get` reads. */
static char *ps_email_lower(const char *p, int64_t n) {
    char *out = ps_email_dup(p, n);
    for (int64_t i = 0; i < n; i++) {
        if (out[i] >= 'A' && out[i] <= 'Z') {
            out[i] = (char)(out[i] - 'A' + 'a');
        }
    }
    return out;
}

/* The case-insensitive compare of a header name: ASCII only, the way
   CPython's header lookup compares. */
static int ps_email_nieq(const char *a, int64_t alen, const char *b, int64_t blen) {
    if (alen != blen) {
        return 0;
    }
    for (int64_t i = 0; i < alen; i++) {
        char ca = a[i], cb = b[i];
        if (ca >= 'A' && ca <= 'Z') {
            ca = (char)(ca - 'A' + 'a');
        }
        if (cb >= 'A' && cb <= 'Z') {
            cb = (char)(cb - 'A' + 'a');
        }
        if (ca != cb) {
            return 0;
        }
    }
    return 1;
}

static int ps_email_hex(char c) {
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

/* RFC 2047 encoded-word decode for the us-ascii, iso-8859-1 and utf-8
   charsets, Q and B encodings. Text in an unlisted charset stays raw, the
   way CPython's Header string form keeps an undecodable word. */
static void ps_email_rfc2047(const char *p, int64_t n, PsGrow *out) {
    int64_t i = 0;
    while (i < n) {
        const char *start = strstr(p + i, "=?");
        if (start == NULL || start - p >= n) {
            ps_grow_add(out, p + i, n - i);
            return;
        }
        int64_t at = start - p;
        if (at > i) {
            ps_grow_add(out, p + i, at - i);
        }
        /* charset ? encoding ? text ?= */
        const char *q1 = memchr(start + 2, '?', (size_t)(n - at - 2));
        if (q1 == NULL) {
            ps_grow_add(out, start, n - at);
            return;
        }
        const char *q2 = memchr(q1 + 1, '?', (size_t)(n - (q1 - p) - 1));
        if (q2 == NULL) {
            ps_grow_add(out, start, n - at);
            return;
        }
        const char *q3 = strstr(q2 + 1, "?=");
        if (q3 == NULL) {
            ps_grow_add(out, start, n - at);
            return;
        }
        int64_t charset_len = q1 - start - 2;
        char enc = q2[1];
        const char *text = q2 + 2;
        int64_t text_len = q3 - text;
        int ascii_charset = charset_len == 8 && ps_email_nieq(start + 2, 8, "us-ascii", 8);
        int latin_charset = charset_len == 10 && ps_email_nieq(start + 2, 10, "iso-8859-1", 10);
        int utf_charset = charset_len == 5 && ps_email_nieq(start + 2, 5, "utf-8", 5);
        if (!ascii_charset && !latin_charset && !utf_charset) {
            /* Unknown charset: the raw word survives, like CPython's
               undecodable Header text. */
            ps_grow_add(out, start, (q3 - start) + 2);
            i = (q3 - p) + 2;
            continue;
        }
        if (enc == 'Q' || enc == 'q') {
            for (int64_t k = 0; k < text_len; k++) {
                char c = text[k];
                if (c == '_') {
                    ps_grow_byte(out, ' ');
                } else if (c == '=' && k + 2 < text_len) {
                    int hi = ps_email_hex(text[k + 1]);
                    int lo = ps_email_hex(text[k + 2]);
                    if (hi >= 0 && lo >= 0) {
                        ps_grow_byte(out, (unsigned char)((hi << 4) | lo));
                        k += 2;
                    } else {
                        ps_grow_byte(out, (unsigned char)c);
                    }
                } else {
                    ps_grow_byte(out, (unsigned char)c);
                }
            }
        } else if (enc == 'B' || enc == 'b') {
            /* Base64 through the shared decoder: four symbols to three
               bytes, padding ignored. */
            int64_t acc = 0;
            int bits = 0;
            for (int64_t k = 0; k < text_len; k++) {
                char c = text[k];
                int v = -1;
                if (c >= 'A' && c <= 'Z') {
                    v = c - 'A';
                } else if (c >= 'a' && c <= 'z') {
                    v = c - 'a' + 26;
                } else if (c >= '0' && c <= '9') {
                    v = c - '0' + 52;
                } else if (c == '+') {
                    v = 62;
                } else if (c == '/') {
                    v = 63;
                } else if (c == '=') {
                    break;
                } else {
                    continue;
                }
                acc = (acc << 6) | v;
                bits += 6;
                if (bits >= 8) {
                    bits -= 8;
                    ps_grow_byte(out, (unsigned char)((acc >> bits) & 0xFF));
                }
            }
        } else {
            ps_grow_add(out, start, (q3 - start) + 2);
            i = (q3 - p) + 2;
            continue;
        }
        i = (q3 - p) + 2;
    }
}

static char *ps_email_decode_value(const char *p, int64_t n) {
    PsGrow out;
    ps_grow_init(&out);
    ps_email_rfc2047(p, n, &out);
    PsBuf done;
    ps_grow_finish(&out, &done);
    return done.data;
}

/* The parameter of a Content-Type-style value: the word after `name=`.
   A quoted parameter keeps its inner text; whitespace around `=` goes. */
static char *ps_email_param(const char *value, const char *param) {
    /* The parameter of a Content-Type-style value: the word after
       `name=`. A quoted parameter keeps its inner text; the whitespace
       around `=` goes. The name is compared case-insensitively over its
       own length, so a suffix of the value never matches by accident. */
    int64_t plen = (int64_t)strlen(param);
    for (const char *s = value; *s; s++) {
        if (!ps_email_nieq(s, plen, param, plen)) {
            continue;
        }
        int before_ok = s == value || s[-1] == ';' || s[-1] == ' ' || s[-1] == '\t';
        const char *after = s + plen;
        while (*after == ' ' || *after == '\t') {
            after++;
        }
        if (!before_ok || *after != '=') {
            continue;
        }
        after++; /* the '=' */
        while (*after == ' ' || *after == '\t') {
            after++;
        }
        if (*after == '"') {
            const char *end = strchr(after + 1, '"');
            if (end == NULL) {
                return ps_email_dup(after + 1, (int64_t)strlen(after + 1));
            }
            return ps_email_dup(after + 1, end - after - 1);
        }
        const char *end = after;
        while (*end && *end != ';' && *end != ' ' && *end != '\t') {
            end++;
        }
        return ps_email_dup(after, end - after);
    }
    return NULL;
}

static char *ps_email_type_word(const char *value) {
    const char *end = value;
    while (*end && *end != ';' && *end != ' ' && *end != '\t') {
        end++;
    }
    char *word = ps_email_dup(value, end - value);
    for (char *c = word; *c; c++) {
        if (*c >= 'A' && *c <= 'Z') {
            *c = (char)(*c - 'A' + 'a');
        }
    }
    return word;
}

/* The boundary line test: a line holding `--boundary` (the final one adds
   `--`). CPython tolerates surrounding whitespace on the line. */
static int ps_email_boundary_line(const char *line, int64_t len, const char *boundary, int final) {
    int64_t blen = (int64_t)strlen(boundary);
    int64_t need = 2 + blen + (final ? 2 : 0);
    if (len < need) {
        return 0;
    }
    if (line[0] != '-' || line[1] != '-') {
        return 0;
    }
    if (strncmp(line + 2, boundary, (size_t)blen) != 0) {
        return 0;
    }
    if (final) {
        if (line[2 + blen] != '-' || line[3 + blen] != '-') {
            return 0;
        }
    }
    for (int64_t i = need; i < len; i++) {
        if (line[i] != ' ' && line[i] != '\t' && line[i] != '\r') {
            return 0;
        }
    }
    return 1;
}

static void ps_email_header_put(PsEmailPart *part, const char *name, int64_t name_len, const char *value, int64_t value_len) {
    char *lowered = ps_email_lower(name, name_len);
    char *raw = ps_email_dup(value, value_len);
    /* The name and the value ride as the 16-byte PsBuf the runtime's str
       cells use: the name keeps the wire spelling, the value the raw
       text. */
    PsBuf wire = {name_len, ps_email_dup(name, name_len)};
    PsBuf text = {value_len, raw};
    ps_seq_push(part->names, &wire);
    ps_seq_push(part->values, &text);
    /* The map key is the 16-byte PsBuf the runtime's str cells use, and
       the value is the raw header text in the same shape. */
    PsBuf key = {(int64_t)strlen(lowered), lowered};
    PsBuf val = {(int64_t)strlen(raw), raw};
    if (!ps_map_has(part->headers, &key)) {
        ps_map_put(part->headers, &key, &val);
    }
}

static PsEmailPart *ps_email_parse(const char *p, int64_t n);

/* One part of a multipart body: the text between two boundary lines,
   re-entering the header parser. */
static PsEmailPart *ps_email_split_part(const char *p, int64_t n) {
    return ps_email_parse(p, n);
}

/* The multipart walk: split the body on the boundary and parse every
   piece. CPython's feedparser treats a missing final boundary as the end
   of the last part, so the epilogue rule does not lose a part. */
static void ps_email_multipart(PsEmailPart *part, const char *p, int64_t n, const char *boundary) {
    part->is_multipart = 1;
    part->parts = ps_seq_new((int64_t)sizeof(PsEmailPart *), 0);
    int64_t at = 0;
    int64_t line_start = 0;
    int in_part = 0;
    const char *part_p = NULL;
    int64_t part_len = 0;
    while (line_start <= n) {
        int64_t nl = line_start;
        while (nl < n && p[nl] != '\n') {
            nl++;
        }
        int64_t len = nl - line_start;
        /* The CRLF spelling trims the CR the boundary line carries. */
        if (len > 0 && p[line_start + len - 1] == '\r') {
            len--;
        }
        int hit = 0;
        if (part->boundary != NULL) {
            if (ps_email_boundary_line(p + line_start, len, boundary, 0)) {
                hit = 1;
            }
            if (ps_email_boundary_line(p + line_start, len, boundary, 1)) {
                hit = 2;
            }
        }
        if (hit) {
            if (in_part) {
                /* The newline that ends the part's last line belongs to
                   the boundary delimiter, not to the payload: CPython's
                   parser drops it, so the payload of a text part reads
                   without its final newline. */
                int64_t trimmed = part_len;
                if (trimmed > 0 && part_p[trimmed - 1] == '\n') {
                    trimmed--;
                    if (trimmed > 0 && part_p[trimmed - 1] == '\r') {
                        trimmed--;
                    }
                }
                PsEmailPart *child = ps_email_split_part(part_p, trimmed);
                ps_seq_push(part->parts, &child);
            }
            in_part = 0;
            if (hit == 2) {
                return;
            }
        } else if (!in_part) {
            in_part = 1;
            part_p = p + line_start;
            part_len = 0;
        }
        if (in_part) {
            part_len = (p + nl) - part_p + (nl < n ? 1 : 0);
        }
        if (nl >= n) {
            break;
        }
        line_start = nl + 1;
    }
    if (in_part) {
        int64_t trimmed = part_len;
        if (trimmed > 0 && part_p[trimmed - 1] == '\n') {
            trimmed--;
            if (trimmed > 0 && part_p[trimmed - 1] == '\r') {
                trimmed--;
            }
        }
        PsEmailPart *child = ps_email_split_part(part_p, trimmed);
        ps_seq_push(part->parts, &child);
    }
}

/* The header block parser: lines until an empty one, folded continuations
   appended with their leading whitespace kept (compat32 keeps the raw
   folded text; the newline between folds survives too). */
static PsEmailPart *ps_email_parse(const char *p, int64_t n) {
    PsEmailPart *part = calloc(1, sizeof(PsEmailPart));
    if (part == NULL) {
        abort();
    }
    part->headers = ps_map_new(2, (int64_t)sizeof(PsBuf), (int64_t)sizeof(PsBuf), 0);
    part->names = ps_seq_new((int64_t)sizeof(PsBuf), 0);
    part->values = ps_seq_new((int64_t)sizeof(PsBuf), 0);
    part->content_type = ps_email_dup("", 0);
    int64_t at = 0;
    int64_t body_at = n;
    /* The header block. */
    while (at < n) {
        int64_t nl = at;
        while (nl < n && p[nl] != '\n') {
            nl++;
        }
        int64_t len = nl - at;
        if (len > 0 && p[at + len - 1] == '\r') {
            len--;
        }
        if (len == 0) {
            body_at = nl + 1;
            break;
        }
        /* A folded continuation: leading whitespace, a header in progress. */
        if ((p[at] == ' ' || p[at] == '\t') && part->names->len > 0) {
            PsBuf prev;
            ps_seq_get(part->values, part->values->len - 1, &prev);
            /* The raw value grows by the fold: the newline and the line's
               own whitespace survive, CPython's raw folded form. */
            int64_t old = prev.len;
            char *grown = realloc(prev.data, (size_t)(old + (nl - at) + 1));
            if (grown == NULL) {
                abort();
            }
            memcpy(grown + old, p + at, (size_t)(nl - at));
            if (nl < n) {
                grown[old + (nl - at)] = '\n';
                prev.data = grown;
                prev.len = old + (nl - at) + 1;
            } else {
                prev.data = grown;
                prev.len = old + (nl - at);
            }
            ps_seq_set(part->values, part->values->len - 1, &prev);
            /* The map holds the same buffer. */
            PsBuf name;
            ps_seq_get(part->names, part->names->len - 1, &name);
            char *lowered = ps_email_lower(name.data, name.len);
            PsBuf key = {name.len, lowered};
            if (ps_map_has(part->headers, &key)) {
                PsBuf stored;
                ps_map_get(part->headers, &key, &stored);
                stored.data = prev.data;
                stored.len = prev.len;
                ps_map_put(part->headers, &key, &stored);
            }
            free(lowered);
        } else {
            const char *colon = memchr(p + at, ':', (size_t)len);
            if (colon == NULL) {
                /* Not a header line: the body starts here (CPython's
                   parser pushes the line into the payload). */
                body_at = at;
                break;
            }
            int64_t name_len = colon - (p + at);
            int64_t value_at = (int64_t)(colon - p) + 1;
            while (value_at < at + len && (p[value_at] == ' ' || p[value_at] == '\t')) {
                value_at++;
            }
            ps_email_header_put(part, p + at, name_len, p + value_at, at + len - value_at);
        }
        if (nl >= n) {
            body_at = n;
            break;
        }
        at = nl + 1;
    }
    /* The body. */
    part->body = ps_email_dup(p + body_at, n - body_at);
    part->body_len = n - body_at;
    /* The Content-Type row decides multipart and names the parameters. */
    PsBuf key_lower = {12, (char *)"content-type"};
    if (ps_map_has(part->headers, &key_lower)) {
        PsBuf raw;
        ps_map_get(part->headers, &key_lower, &raw);
        free(part->content_type);
        char *decoded = ps_email_decode_value(raw.data, raw.len);
        part->content_type = decoded;
        char *word = ps_email_type_word(decoded);
        if (ps_email_ieq(word, "multipart/mixed") || ps_email_ieq(word, "multipart/alternative") ||
            ps_email_ieq(word, "multipart/related") || ps_email_ieq(word, "multipart/report") ||
            ps_email_ieq(word, "multipart/signed") || ps_email_ieq(word, "multipart/parallel")) {
            char *boundary = ps_email_param(decoded, "boundary");
            if (boundary != NULL) {
                part->boundary = boundary;
                ps_email_multipart(part, part->body, part->body_len, boundary);
            }
        }
        free(word);
    }
    PsBuf name_key = {19, (char *)"content-disposition"};
    if (ps_map_has(part->headers, &name_key)) {
        PsBuf raw;
        ps_map_get(part->headers, &name_key, &raw);
        char *decoded = ps_email_decode_value(raw.data, raw.len);
        char *filename = ps_email_param(decoded, "filename");
        if (filename == NULL) {
            PsBuf type_key = {12, (char *)"content-type"};
            if (ps_map_has(part->headers, &type_key)) {
                PsBuf ctype;
                ps_map_get(part->headers, &type_key, &ctype);
                char *ctype_decoded = ps_email_decode_value(ctype.data, ctype.len);
                filename = ps_email_param(ctype_decoded, "name");
                free(ctype_decoded);
            }
        }
        part->filename = filename;
        free(decoded);
    } else {
        PsBuf type_key = {12, (char *)"content-type"};
        if (ps_map_has(part->headers, &type_key)) {
            PsBuf ctype;
            ps_map_get(part->headers, &type_key, &ctype);
            char *ctype_decoded = ps_email_decode_value(ctype.data, ctype.len);
            part->filename = ps_email_param(ctype_decoded, "name");
            free(ctype_decoded);
        }
    }
    return part;
}


/* --- The ABI the lowering calls. --- */

void *ps_email_from_string(int64_t n, const char *s) {
    if (n < 0) {
        ps_raise_cstr(PS_VALUE, "email length is negative");
    }
    return ps_email_parse(s, n);
}

void *ps_email_from_bytes(int64_t n, const char *s) {
    return ps_email_from_string(n, s);
}

void *ps_email_from_file(void *io) {
    PsBuf text;
    ps_io_read(io, &text);
    return ps_email_from_string(text.len, text.data);
}

/* The first value of a header, decoded, or the tagged None: the hit/get
   pair follows the tagged-optional ABI the other surfaces use. */
int ps_email_header_hit(void *handle, int64_t n, const char *name) {
    PsEmailPart *part = handle;
    char *lowered = ps_email_lower(name, n);
    PsBuf key = {n, lowered};
    int hit = ps_map_has(part->headers, &key);
    free(lowered);
    return hit;
}

void ps_email_header_get(void *handle, int64_t n, const char *name, PsBuf *out) {
    PsEmailPart *part = handle;
    char *lowered = ps_email_lower(name, n);
    PsBuf key = {n, lowered};
    PsBuf raw;
    memset(&raw, 0, sizeof raw);
    if (ps_map_has(part->headers, &key)) {
        ps_map_get(part->headers, &key, &raw);
    }
    free(lowered);
    char *decoded = ps_email_decode_value(raw.data, raw.len);
    out->len = (int64_t)strlen(decoded);
    out->data = decoded;
}

/* get_all(name): the decoded values in wire order. The count call and the
   values call answer the same list, so the lowering needs no separate
   bounding step; a header the message does not carry is the empty list. */
int64_t ps_email_header_count(void *handle, int64_t n, const char *name) {
    PsEmailPart *part = handle;
    int64_t count = 0;
    for (int64_t i = 0; i < part->names->len; i++) {
        PsBuf wire;
        ps_seq_get(part->names, i, &wire);
        if (ps_email_nieq(wire.data, wire.len, name, n)) {
            count++;
        }
    }
    return count;
}

PsSeq *ps_email_header_values(void *handle, int64_t n, const char *name) {
    PsEmailPart *part = handle;
    PsSeq *out = ps_seq_new((int64_t)sizeof(PsBuf), 0);
    for (int64_t i = 0; i < part->names->len; i++) {
        PsBuf wire;
        ps_seq_get(part->names, i, &wire);
        if (!ps_email_nieq(wire.data, wire.len, name, n)) {
            continue;
        }
        PsBuf raw;
        ps_seq_get(part->values, i, &raw);
        char *decoded = ps_email_decode_value(raw.data, raw.len);
        PsBuf value = {(int64_t)strlen(decoded), decoded};
        ps_seq_push(out, &value);
    }
    return out;
}

/* keys(): the wire-spelled names, in order. */
PsSeq *ps_email_keys(void *handle) {
    PsEmailPart *part = handle;
    PsSeq *out = ps_seq_new((int64_t)sizeof(PsBuf), 0);
    for (int64_t i = 0; i < part->names->len; i++) {
        PsBuf wire;
        ps_seq_get(part->names, i, &wire);
        ps_seq_push(out, &wire);
    }
    return out;
}

/* items(): the (name, decoded value) pairs as tuple records at the class
   layout the emitter passes (the value offset and the record size). The
   order is the wire order, CPython's items() order. */
PsSeq *ps_email_items(void *handle, int64_t voff, int64_t csize) {
    PsEmailPart *part = handle;
    PsSeq *seq = ps_seq_new((int64_t)sizeof(void *), 0);
    for (int64_t i = 0; i < part->names->len; i++) {
        PsBuf wire;
        PsBuf raw;
        ps_seq_get(part->names, i, &wire);
        ps_seq_get(part->values, i, &raw);
        char *tuple = malloc((size_t)csize);
        if (tuple == NULL) {
            abort();
        }
        memset(tuple, 0, (size_t)csize);
        memcpy(tuple, &wire, sizeof wire);
        char *decoded = ps_email_decode_value(raw.data, raw.len);
        PsBuf value = {(int64_t)strlen(decoded), decoded};
        memcpy(tuple + voff, &value, sizeof value);
        void *cell = tuple;
        ps_seq_push(seq, &cell);
    }
    return seq;
}

int ps_email_is_multipart(void *handle) {
    PsEmailPart *part = handle;
    return part->is_multipart;
}

/* walk(): the pre-order part list, the message first (CPython's order). */
static void ps_email_walk_push(PsEmailPart *part, PsSeq *out) {
    ps_seq_push(out, &part);
    if (part->parts != NULL) {
        for (int64_t i = 0; i < part->parts->len; i++) {
            PsEmailPart *child;
            ps_seq_get(part->parts, i, &child);
            ps_email_walk_push(child, out);
        }
    }
}

PsSeq *ps_email_walk(void *handle) {
    PsEmailPart *part = handle;
    PsSeq *out = ps_seq_new((int64_t)sizeof(PsEmailPart *), 0);
    ps_email_walk_push(part, out);
    return out;
}

/* The payload: the raw body of a part. The decode flag is folded at
   lowering time, so the two symbols carry the flag each. */
void ps_email_payload(void *handle, PsBuf *out) {
    PsEmailPart *part = handle;
    /* A multipart part has no flat payload of its own: CPython returns the
       list of the child parts there, which is outside the closed surface,
       so the call raises where CPython would hand a list back. */
    if (part->is_multipart) {
        ps_raise_cstr(PS_VALUE, "get_payload on a multipart message is outside the email subset");
    }
    ps_buf_copy(out, part->body, part->body_len);
}

void ps_email_payload_decoded(void *handle, PsBuf *out) {
    PsEmailPart *part = handle;
    /* The transfer encoding: quoted-printable or base64 through the
       Content-Transfer-Encoding row. An unknown encoding keeps the raw
       bytes, the way CPython hands back the raw payload for one it cannot
       decode. */
    PsBuf cte_key = {25, (char *)"content-transfer-encoding"};
    char *encoding = NULL;
    if (ps_map_has(part->headers, &cte_key)) {
        PsBuf raw;
        ps_map_get(part->headers, &cte_key, &raw);
        char *decoded = ps_email_decode_value(raw.data, raw.len);
        char *word = decoded;
        while (*word == ' ' || *word == '\t') {
            word++;
        }
        char *end = word;
        while (*end && *end != ' ' && *end != '\t' && *end != '\r' && *end != '\n') {
            end++;
        }
        encoding = ps_email_dup(word, end - word);
        for (char *c = encoding; *c; c++) {
            if (*c >= 'A' && *c <= 'Z') {
                *c = (char)(*c - 'A' + 'a');
            }
        }
        free(decoded);
    }
    if (encoding != NULL && strcmp(encoding, "quoted-printable") == 0) {
        PsGrow grow;
        ps_grow_init(&grow);
        for (int64_t i = 0; i < part->body_len; i++) {
            char c = part->body[i];
            if (c == '=' && i + 1 < part->body_len) {
                /* The soft line break first: `=\n` (and `=\r\n`) vanish,
                   and the whitespace the next line starts with goes too
                   (CPython's binascii.a2b_qp rule). */
                int64_t after = i + 1;
                if (part->body[after] == '\r' && after + 1 < part->body_len && part->body[after + 1] == '\n') {
                    after += 2;
                } else if (part->body[after] == '\n') {
                    after += 1;
                } else {
                    after = -1;
                }
                if (after >= 0) {
                    while (after < part->body_len && (part->body[after] == ' ' || part->body[after] == '\t')) {
                        after++;
                    }
                    i = after - 1;
                    continue;
                }
                if (i + 2 < part->body_len) {
                    int hi = ps_email_hex(part->body[i + 1]);
                    int lo = ps_email_hex(part->body[i + 2]);
                    if (hi >= 0 && lo >= 0) {
                        ps_grow_byte(&grow, (unsigned char)((hi << 4) | lo));
                        i += 2;
                        continue;
                    }
                }
            }
            ps_grow_byte(&grow, (unsigned char)c);
        }
        ps_grow_finish(&grow, out);
    } else if (encoding != NULL && strcmp(encoding, "base64") == 0) {
        PsGrow grow;
        ps_grow_init(&grow);
        int64_t acc = 0;
        int bits = 0;
        for (int64_t i = 0; i < part->body_len; i++) {
            char c = part->body[i];
            int v = -1;
            if (c >= 'A' && c <= 'Z') {
                v = c - 'A';
            } else if (c >= 'a' && c <= 'z') {
                v = c - 'a' + 26;
            } else if (c >= '0' && c <= '9') {
                v = c - '0' + 52;
            } else if (c == '+') {
                v = 62;
            } else if (c == '/') {
                v = 63;
            } else if (c == '=') {
                break;
            } else {
                continue; /* whitespace anywhere in the block */
            }
            acc = (acc << 6) | v;
            bits += 6;
            if (bits >= 8) {
                bits -= 8;
                ps_grow_byte(&grow, (unsigned char)((acc >> bits) & 0xFF));
            }
        }
        ps_grow_finish(&grow, out);
    } else {
        ps_buf_copy(out, part->body, part->body_len);
    }
    free(encoding);
}

void ps_email_content_type(void *handle, PsBuf *out) {
    PsEmailPart *part = handle;
    /* CPython's get_content_type(): the default is text/plain when the
       message carries no Content-Type row at all; a row without a type
       word falls back to text/plain too. */
    if (part->content_type[0] == '\0') {
        ps_buf_copy(out, "text/plain", 10);
        return;
    }
    char *word = ps_email_type_word(part->content_type);
    if (word[0] == '\0') {
        ps_buf_copy(out, "text/plain", 10);
        free(word);
        return;
    }
    ps_buf_copy(out, word, (int64_t)strlen(word));
    free(word);
}

int ps_email_filename_hit(void *handle) {
    PsEmailPart *part = handle;
    return part->filename != NULL;
}

void ps_email_filename_get(void *handle, PsBuf *out) {
    PsEmailPart *part = handle;
    if (part->filename == NULL) {
        ps_buf_copy(out, "", 0);
        return;
    }
    ps_buf_copy(out, part->filename, (int64_t)strlen(part->filename));
}

/* as_string(): the headers in wire order, then the blank line and the
   body. Folded values re-emit as stored (the raw folded text). */
void ps_email_as_string(void *handle, PsBuf *out) {
    PsEmailPart *part = handle;
    PsGrow grow;
    ps_grow_init(&grow);
    for (int64_t i = 0; i < part->names->len; i++) {
        PsBuf wire;
        PsBuf raw;
        ps_seq_get(part->names, i, &wire);
        ps_seq_get(part->values, i, &raw);
        ps_grow_add(&grow, wire.data, wire.len);
        ps_grow_add(&grow, ": ", 2);
        ps_grow_add(&grow, raw.data, raw.len);
        ps_grow_byte(&grow, '\n');
    }
    ps_grow_byte(&grow, '\n');
    ps_grow_add(&grow, part->body, part->body_len);
    ps_grow_finish(&grow, out);
}
