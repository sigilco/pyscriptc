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

#include <stdio.h>
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
    /* The builder's raw payload text (issue #1268): set_content stores the
       encoded body here with its CTE name, and `as_string` re-encodes the
       way CPython's generator munges an 8bit payload through its charset
       (a utf-8 payload reads as base64 there). A parsed message leaves
       this NULL: its body serializes as stored. */
    char *raw_body;      /* the encoded payload text, NUL-terminated */
    int32_t raw_is_8bit; /* the CTE the builder chose (8bit munges to base64) */
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

/* The base64 body encoder, byte for byte CPython's `_encode_base64` over
   the default policy's 78-column limit: `unencoded_bytes_per_line` is 78
   // 4 * 3 = 57, each block re-encodes through b2a_base64 and the lines
   end with \n. */
static char *ps_email_b64_encode(const char *p, int64_t n) {
    static const char alphabet[65] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    PsGrow out;
    ps_grow_init(&out);
    int64_t per_line = 57;
    int64_t at = 0;
    while (at < n) {
        int64_t block = n - at < per_line ? n - at : per_line;
        for (int64_t i = 0; i < block; i += 3) {
            unsigned int b0 = (unsigned char)p[at + i];
            unsigned int b1 = i + 1 < block ? (unsigned char)p[at + i + 1] : 0;
            unsigned int b2 = i + 2 < block ? (unsigned char)p[at + i + 2] : 0;
            unsigned int bits = (b0 << 16) | (b1 << 8) | b2;
            ps_grow_byte(&out, alphabet[(bits >> 18) & 0x3F]);
            ps_grow_byte(&out, alphabet[(bits >> 12) & 0x3F]);
            ps_grow_byte(&out, i + 1 < block ? alphabet[(bits >> 6) & 0x3F] : '=');
            ps_grow_byte(&out, i + 2 < block ? alphabet[bits & 0x3F] : '=');
        }
        ps_grow_byte(&out, '\n');
        at += block;
    }
    PsBuf done;
    ps_grow_finish(&out, &done);
    return done.data;
}

/* The RFC 2047 encoded word: `=?utf-8?q?<payload>?=` or `=?utf-8?b?<payload>?=`.
   The Q encoding keeps the header-safe bytes verbatim (`-!*+/`, the
   letters, the digits and `_`), maps the space to `_` and escapes
   everything else as =XX; the B encoding is the base64 of the bytes.
   CPython picks the encoding that gives the shorter result, preferring Q
   when it is at most four characters longer. */
static int ps_email_q_better(const char *p, int64_t n) {
    int64_t qlen = 0;
    for (int64_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)p[i];
        if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '-' || c == '!' ||
            c == '*' || c == '+' || c == '/') {
            qlen += 1;
            continue;
        }
        if (c == ' ') {
            qlen += 1;
            continue;
        }
        qlen += 3;
    }
    int64_t blen = ((n + 2) / 3) * 4;
    return qlen - blen < 5;
}

static void ps_email_rfc2047_q(PsGrow *out, const char *p, int64_t n) {
    static const char hex[17] = "0123456789ABCDEF";
    ps_grow_add(out, "=?utf-8?q?", 10);
    for (int64_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)p[i];
        if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '-' || c == '!' ||
            c == '*' || c == '+' || c == '/' || c == '_') {
            ps_grow_byte(out, (char)c);
            continue;
        }
        if (c == ' ') {
            ps_grow_byte(out, '_');
            continue;
        }
        ps_grow_byte(out, '=');
        ps_grow_byte(out, hex[c >> 4]);
        ps_grow_byte(out, hex[c & 0xF]);
    }
    ps_grow_add(out, "?=", 2);
}

/* The base64 of the bytes (the B encoding's payload). */
static void ps_email_rfc2047_b64(PsGrow *out, const char *p, int64_t n) {
    static const char alphabet[65] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    ps_grow_add(out, "=?utf-8?b?", 10);
    int64_t i = 0;
    while (i < n) {
        int64_t block = n - i < 3 ? n - i : 3;
        unsigned int b0 = (unsigned char)p[i];
        unsigned int b1 = block > 1 ? (unsigned char)p[i + 1] : 0;
        unsigned int b2 = block > 2 ? (unsigned char)p[i + 2] : 0;
        unsigned int bits = (b0 << 16) | (b1 << 8) | b2;
        ps_grow_byte(out, alphabet[(bits >> 18) & 0x3F]);
        ps_grow_byte(out, alphabet[(bits >> 12) & 0x3F]);
        ps_grow_byte(out, block > 1 ? alphabet[(bits >> 6) & 0x3F] : '=');
        ps_grow_byte(out, block > 2 ? alphabet[bits & 0x3F] : '=');
        i += block;
    }
    ps_grow_add(out, "?=", 2);
}

/* The length the encoded word takes on the wire: the chrome (the charset
   name and 7 characters) plus the payload. */
static int64_t ps_email_rfc2047_len(const char *p, int64_t n) {
    int64_t payload;
    if (ps_email_q_better(p, n)) {
        payload = 0;
        for (int64_t i = 0; i < n; i++) {
            unsigned char c = (unsigned char)p[i];
            if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '-' || c == '!' ||
                c == '*' || c == '+' || c == '/' || c == ' ') {
                payload += 1;
                continue;
            }
            payload += 3;
        }
    } else {
        payload = ((n + 2) / 3) * 4;
    }
    return payload + 12;
}

/* The encoded word for a chunk, in the encoding CPython picks. */
static void ps_email_rfc2047_word(PsGrow *out, const char *p, int64_t n) {
    if (ps_email_q_better(p, n)) {
        ps_email_rfc2047_q(out, p, n);
        return;
    }
    ps_email_rfc2047_b64(out, p, n);
}

/* The default policy's header fold for a plain `str` value: policy.py's
   _fold (refold_source long, max_line_length 78, utf8 False) over
   _header_value_parser's _refold_parse_tree. The value refolds when the
   first line with the name crosses 78, a later line crosses 78, or the
   value holds a byte above ASCII; otherwise it re-emits with its internal
   breaks kept. The refold walks the value's atoms (a WSP run ends one):
   an atom stays literal while it fits the line's columns, folds to the
   next line over the syntactic break when it is one column too wide, and
   becomes an RFC 2047 utf-8 run when it holds a newline byte or a code
   point above ASCII (the want_encoding rules: NLSET for a utext token,
   the us-ascii encode failure) or when it is too wide to fold. The run
   combines with the line's last one (the decode-re-encode round trip
   _fold_as_ew does, which eats the whitespace between the words), and the
   chunk sizing is _ew.encode's: the chunk shrinks one CHARACTER at a time
   until the word fits the line's remaining columns, Q when
   qlen - blen < 5. */

#define PS_EMAIL_MAX_FOLD_LINES 4096

/* The fold state: the lines CPython keeps as a list of str, the index of
   the last line's encoded word start (last_ew) and the last-ends-in-EW
   flag (last_word_is_ew). */
typedef struct PsEmailFold {
    PsGrow lines[PS_EMAIL_MAX_FOLD_LINES];
    int64_t nlines;
    int64_t last_ew;
    int last_word_is_ew;
} PsEmailFold;

/* _steal_trailing_WSP_if_exists: the last line's trailing WSP byte moves
   to the new line; an emptied line drops entirely (gh-142006). The stolen
   byte comes back (0 when the line had none). */
static char ps_email_steal_wsp(PsEmailFold *f) {
    char wsp = 0;
    if (f->nlines > 0) {
        PsGrow *last = &f->lines[f->nlines - 1];
        if (last->len > 0 && (last->data[last->len - 1] == ' ' || last->data[last->len - 1] == '\t')) {
            wsp = last->data[last->len - 1];
            last->len--;
            if (last->len == 0) {
                f->nlines--;
                free(last->data);
            }
        }
    }
    return wsp;
}

/* The atom holds a newline byte or a byte above ASCII: the want_encoding
   rules (NLSET for a utext token; a byte the us-ascii encode refuses).
   The runtime's strings are UTF-8, so a byte above 0x7F belongs to a code
   point above ASCII. */
static int ps_email_atom_wants_encoding(const char *p, int64_t n) {
    for (int64_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)p[i];
        if (c >= 0x80 || c == '\n' || c == '\r') {
            return 1;
        }
    }
    return 0;
}

/* The number of characters in the utf-8 slice [p, p+n): the continuation
   bytes do not start one. */
static int64_t ps_email_char_count(const char *p, int64_t n) {
    int64_t chars = 0;
    for (int64_t i = 0; i < n; i++) {
        if (((unsigned char)p[i] & 0xC0) != 0x80) {
            chars++;
        }
    }
    return chars;
}

/* The byte offset past the char index `chars` into the utf-8 slice (the
   offset of the char that follows the `chars` first characters). */
static int64_t ps_email_char_offset(const char *p, int64_t n, int64_t chars) {
    int64_t at = 0;
    int64_t seen = 0;
    while (at < n) {
        if (((unsigned char)p[at] & 0xC0) != 0x80) {
            if (seen == chars) {
                break;
            }
            seen++;
        }
        at++;
    }
    return at;
}

/* The decode of an encoded word (the get_unstructured round trip
   _fold_as_ew's combine does): the Q payload maps `_` back to a space and
   the =XX escapes back to the byte, the B payload is base64. The bytes
   after the word (the whitespace that followed it on the line) ride the
   decode verbatim, the way get_unstructured keeps the text after the
   word. */
static void ps_email_ew_decode_text(PsGrow *out, const char *p, int64_t n) {
    if (n < 4) {
        ps_grow_add(out, p, n);
        return;
    }
    /* The word's end: the `?=` that closes it (the payload holds no `?=`). */
    int64_t word_len = n;
    for (int64_t i = n - 1; i > 0; i--) {
        if (p[i] == '=' && p[i - 1] == '?') {
            word_len = i + 1;
            break;
        }
    }
    const char *body = p + 2;
    int64_t body_len = word_len - 4;
    const char *q = memchr(body, '?', body_len);
    if (q == NULL || q + 3 > body + body_len) {
        ps_grow_add(out, p, n);
        return;
    }
    int cte = q[1] | 0x20;
    const char *payload = q + 3;
    int64_t payload_len = body_len - (payload - body);
    if (cte == 'b') {
        int64_t saved = out->len;
        int64_t i = 0;
        unsigned int acc = 0;
        int bits = 0;
        while (i < payload_len) {
            char c = payload[i];
            int64_t v;
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
            } else {
                i++;
                continue;
            }
            acc = (acc << 6) | (unsigned int)v;
            bits += 6;
            if (bits >= 8) {
                bits -= 8;
                ps_grow_byte(out, (unsigned char)((acc >> bits) & 0xFF));
            }
            i++;
        }
        ps_grow_add(out, p + word_len, n - word_len);
        (void)saved;
        return;
    }
    int64_t i = 0;
    while (i < payload_len) {
        char c = payload[i];
        if (c == '_') {
            ps_grow_byte(out, ' ');
            i++;
            continue;
        }
        if (c == '=' && i + 2 < payload_len) {
            int hi = ps_email_hex(payload[i + 1]);
            int lo = ps_email_hex(payload[i + 2]);
            if (hi >= 0 && lo >= 0) {
                ps_grow_byte(out, (unsigned char)((hi << 4) | lo));
                i += 3;
                continue;
            }
        }
        ps_grow_byte(out, (unsigned char)c);
        i++;
    }
    ps_grow_add(out, p + word_len, n - word_len);
}

/* The new line: the stolen WSP byte (or a space) leads it. */
static void ps_email_fold_new_line(PsEmailFold *f, char wsp) {
    ps_grow_init(&f->lines[f->nlines]);
    ps_grow_byte(&f->lines[f->nlines], (unsigned char)(wsp ? wsp : ' '));
    f->nlines++;
}

/* The encoded word for a chunk, the chrome and the payload _ew.encode
   writes. */
static void ps_email_ew_word(PsGrow *out, const char *p, int64_t n) {
    if (ps_email_q_better(p, n)) {
        static const char hex[17] = "0123456789ABCDEF";
        ps_grow_add(out, "=?utf-8?q?", 10);
        for (int64_t i = 0; i < n; i++) {
            unsigned char c = (unsigned char)p[i];
            if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '-' || c == '!' ||
                c == '*' || c == '+' || c == '/') {
                ps_grow_byte(out, (char)c);
                continue;
            }
            if (c == ' ') {
                ps_grow_byte(out, '_');
                continue;
            }
            ps_grow_byte(out, '=');
            ps_grow_byte(out, hex[c >> 4]);
            ps_grow_byte(out, hex[c & 0xF]);
        }
        ps_grow_add(out, "?=", 2);
        return;
    }
    static const char alphabet[65] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    ps_grow_add(out, "=?utf-8?b?", 10);
    int64_t i = 0;
    while (i < n) {
        int64_t block = n - i < 3 ? n - i : 3;
        unsigned int b0 = (unsigned char)p[i];
        unsigned int b1 = block > 1 ? (unsigned char)p[i + 1] : 0;
        unsigned int b2 = block > 2 ? (unsigned char)p[i + 2] : 0;
        unsigned int bits = (b0 << 16) | (b1 << 8) | b2;
        ps_grow_byte(out, alphabet[(bits >> 18) & 0x3F]);
        ps_grow_byte(out, alphabet[(bits >> 12) & 0x3F]);
        ps_grow_byte(out, block > 1 ? alphabet[(bits >> 6) & 0x3F] : '=');
        ps_grow_byte(out, block > 2 ? alphabet[bits & 0x3F] : '=');
        i += block;
    }
    ps_grow_add(out, "?=", 2);
}

/* The length of the encoded word for a chunk: the chrome (10 plus the
   cte letter, the charset name and the two closing marks) plus the
   payload. */
static int64_t ps_email_ew_chunk_len(const char *p, int64_t n) {
    int64_t payload;
    if (ps_email_q_better(p, n)) {
        payload = 0;
        for (int64_t i = 0; i < n; i++) {
            unsigned char c = (unsigned char)p[i];
            if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '-' || c == '!' ||
                c == '*' || c == '+' || c == '/' || c == ' ') {
                payload += 1;
                continue;
            }
            payload += 3;
        }
    } else {
        payload = ((n + 2) / 3) * 4;
    }
    return payload + 12;
}

/* _fold_as_ew: the text becomes an encoded word on the line, cut so it
   fits the line's columns. The text is UTF-8 and the cut is by CHARACTER
   (_ew.encode gets a str). */
static void ps_email_fold_as_ew(PsEmailFold *f, const char *text, int64_t text_len) {
    PsGrow to_encode;
    ps_grow_init(&to_encode);
    ps_grow_add(&to_encode, text, text_len);
    if (f->last_ew >= 0) {
        /* The combine: the line's slice from the last encoded word (the
           word plus the literal text that followed it) and this text
           decode together and re-encode as one word (get_unstructured
           keeps the text between the words). */
        PsGrow *last = &f->lines[f->nlines - 1];
        PsGrow decoded;
        ps_grow_init(&decoded);
        ps_email_ew_decode_text(&decoded, last->data + f->last_ew, last->len - f->last_ew);
        ps_grow_add(&decoded, to_encode.data, to_encode.len);
        free(to_encode.data);
        to_encode = decoded;
        last->len = f->last_ew;
    } else if (f->last_word_is_ew) {
        /* The line ends in an encoded word: the whitespace before this
           text joins the encoded word (a space between two encoded words
           is dropped on decode). */
        PsGrow *last = &f->lines[f->nlines - 1];
        int64_t without = last->len;
        while (without > 0 && (last->data[without - 1] == ' ' || last->data[without - 1] == '\t')) {
            without--;
        }
        int has_ws = without != last->len;
        PsGrow combined;
        ps_grow_init(&combined);
        if (has_ws) {
            ps_grow_add(&combined, last->data + without, last->len - without);
            last->len = without;
            ps_grow_byte(last, ' ');
        }
        ps_grow_add(&combined, to_encode.data, to_encode.len);
        free(to_encode.data);
        to_encode = combined;
    }
    /* The trailing whitespace rides the line, not the word. */
    char trailing = 0;
    if (to_encode.len > 0 && (to_encode.data[to_encode.len - 1] == ' ' || to_encode.data[to_encode.len - 1] == '\t')) {
        trailing = to_encode.data[to_encode.len - 1];
        to_encode.len--;
    }
    int64_t ew_at = f->last_ew >= 0 ? f->last_ew : f->lines[f->nlines - 1].len;
    /* The chunks: the word grows until it fits the line's columns. */
    int64_t chars = ps_email_char_count(to_encode.data, to_encode.len);
    int64_t at = 0;   /* the character index */
    int64_t byte_at = 0; /* the byte offset of that character */
    while (at < chars) {
        int64_t remaining = 78 - f->lines[f->nlines - 1].len;
        int64_t text_space = remaining - 12;
        if (text_space <= 0) {
            ps_email_fold_new_line(f, ps_email_steal_wsp(f));
            ew_at = f->lines[f->nlines - 1].len;
            continue;
        }
        /* The chunk: the text that fits the line, shrunk one CHARACTER at a
           time until the encoded word fits the remaining columns. */
        int64_t take = chars - at < text_space ? chars - at : text_space;
        int64_t byte_end = ps_email_char_offset(to_encode.data, to_encode.len, at + take);
        while (take > 0 && ps_email_ew_chunk_len(to_encode.data + byte_at, byte_end - byte_at) > remaining) {
            take--;
            byte_end = ps_email_char_offset(to_encode.data, to_encode.len, at + take);
        }
        /* An empty chunk still writes the word (CPython's empty encoded
           word). */
        ps_email_ew_word(&f->lines[f->nlines - 1], to_encode.data + byte_at, byte_end - byte_at);
        at += take;
        byte_at = byte_end;
        if (at < chars) {
            ps_email_fold_new_line(f, ps_email_steal_wsp(f));
            ew_at = f->lines[f->nlines - 1].len;
        }
    }
    if (trailing) {
        ps_grow_byte(&f->lines[f->nlines - 1], (unsigned char)trailing);
    }
    free(to_encode.data);
    f->last_ew = ew_at;
    f->last_word_is_ew = 1;
}

static void ps_email_fold_header(PsGrow *out, const char *name, int64_t name_len, const char *value, int64_t value_len) {
    /* The value's lines: the linesep split (CRLF, CR and LF all end one). */
    int64_t first_len = 0;
    int later_long = 0;
    int non_ascii = 0;
    for (int64_t i = 0; i < value_len; i++) {
        if ((unsigned char)value[i] >= 0x80) {
            non_ascii = 1;
        }
    }
    int64_t at = 0;
    int first = 1;
    while (at <= value_len) {
        int64_t nl = at;
        while (nl < value_len && value[nl] != '\n' && value[nl] != '\r') nl++;
        int64_t len = nl - at;
        if (first) {
            first_len = len;
            first = 0;
        } else if (len > 78) {
            later_long = 1;
        }
        if (nl >= value_len) break;
        if (value[nl] == '\r' && nl + 1 < value_len && value[nl + 1] == '\n') nl++;
        at = nl + 1;
    }
    if (!non_ascii && first_len + name_len + 2 <= 78 && !later_long) {
        /* The value re-emits with its internal line breaks kept. An empty
           value emits the name alone (CPython's fold drops the space). */
        ps_grow_add(out, name, name_len);
        ps_grow_byte(out, ':');
        if (value_len > 0) {
            ps_grow_byte(out, ' ');
            ps_grow_add(out, value, value_len);
        }
        ps_grow_byte(out, '\n');
        return;
    }
    /* The refold: the lines CPython keeps, the first one the name and the
       separator. */
    PsEmailFold f;
    f.nlines = 1;
    f.last_ew = -1;
    f.last_word_is_ew = 0;
    ps_grow_init(&f.lines[0]);
    ps_grow_add(&f.lines[0], name, name_len);
    ps_grow_add(&f.lines[0], ": ", 2);
    /* The atoms: a WSP run ends an atom. */
    int64_t pos = 0;
    while (pos < value_len) {
        if (value[pos] == ' ' || value[pos] == '\t') {
            /* The whitespace atom: the run rides the line, folded to the
               next line when it does not fit. */
            int64_t ws_end = pos;
            while (ws_end < value_len && (value[ws_end] == ' ' || value[ws_end] == '\t')) ws_end++;
            int64_t ws_len = ws_end - pos;
            if (f.lines[f.nlines - 1].len + ws_len <= 78) {
                ps_grow_add(&f.lines[f.nlines - 1], value + pos, ws_len);
            } else {
                char wsp = ps_email_steal_wsp(&f);
                ps_grow_init(&f.lines[f.nlines]);
                if (wsp) {
                    ps_grow_byte(&f.lines[f.nlines], (unsigned char)wsp);
                }
                f.nlines++;
                f.last_ew = -1;
                ps_grow_add(&f.lines[f.nlines - 1], value + pos, ws_len);
            }
            pos = ws_end;
            continue;
        }
        /* The atom: the run up to the next whitespace. */
        int64_t atom_end = pos;
        while (atom_end < value_len && value[atom_end] != ' ' && value[atom_end] != '\t') atom_end++;
        int64_t atom_len = atom_end - pos;
        int wants = ps_email_atom_wants_encoding(value + pos, atom_len);
        if (!wants && f.lines[f.nlines - 1].len + atom_len <= 78) {
            /* The atom rides the line as it is. The last encoded word's
               place stays (the combine takes the text that follows it). */
            ps_grow_add(&f.lines[f.nlines - 1], value + pos, atom_len);
            f.last_word_is_ew = 0;
            pos = atom_end;
            continue;
        }
        if (!wants && atom_len + 1 <= 78) {
            /* The atom does not fit but folds over the syntactic break:
               the next line takes it whole. */
            char wsp = ps_email_steal_wsp(&f);
            if (wsp) {
                ps_grow_init(&f.lines[f.nlines]);
                ps_grow_byte(&f.lines[f.nlines], (unsigned char)wsp);
                f.nlines++;
                ps_grow_add(&f.lines[f.nlines - 1], value + pos, atom_len);
                f.last_ew = -1;
                f.last_word_is_ew = 0;
                pos = atom_end;
                continue;
            }
            /* No whitespace to break at: the atom stays on the line. */
            ps_grow_add(&f.lines[f.nlines - 1], value + pos, atom_len);
            f.last_ew = -1;
            f.last_word_is_ew = 0;
            pos = atom_end;
            continue;
        }
        /* The atom encodes: the encoded word covers the atom's bytes. */
        ps_email_fold_as_ew(&f, value + pos, atom_len);
        pos = atom_end;
    }
    /* The lines join with the newline. */
    for (int64_t i = 0; i < f.nlines; i++) {
        ps_grow_add(out, f.lines[i].data, f.lines[i].len);
        ps_grow_byte(out, '\n');
        free(f.lines[i].data);
    }
}

/* The generator's `_write_lines`: the payload text re-joins over its
   \r\n, \r and \n breaks with the \n linesep, and a trailing break's
   empty final piece drops. The QP and the 8bit bodies end without a
   break (the split's last element is text), the base64 body ends with
   one (the final empty piece goes), so the re-join is the payload text
   itself and only the 7bit normal_body needs the re-join. */
static void ps_email_write_lines(PsGrow *out, const char *p, int64_t n) {
    if (n == 0) {
        return;
    }
    /* The split the generator does (\r\n, \r and \n end a line) and the
       re-join: every line but the last rides a linesep, the last rides
       one only when it is not empty. A text ending in a break therefore
       keeps its trailing linesep (the split's empty last element is the
       one the re-join drops, and the line before it kept its sep). */
    int64_t at = 0;
    while (at < n) {
        int64_t nl = at;
        while (nl < n && p[nl] != '\n' && p[nl] != '\r') nl++;
        int64_t len = nl - at;
        int last = nl >= n;
        if (!last) {
            ps_grow_add(out, p + at, len);
            ps_grow_byte(out, '\n');
            if (p[nl] == '\r' && nl + 1 < n && p[nl + 1] == '\n') nl++;
            at = nl + 1;
            continue;
        }
        if (len > 0) {
            ps_grow_add(out, p + at, len);
        }
        at = nl;
    }
}

/* as_string(): the headers in wire order folded the way the default
   policy folds a plain str value (an RFC 2047 utf-8 run when the name
   plus the value crosses 78 columns or the value holds a non-ASCII byte),
   then the blank line and the body. A parsed message re-emits the stored
   rows; a built message's body rides the transfer encoding the builder
   wrote, with CPython's generator munge over it: an 8bit payload
   re-serializes as base64 (the _handle_text charset pass), every other
   CTE passes through. */
void ps_email_as_string(void *handle, PsBuf *out) {
    PsEmailPart *part = handle;
    PsGrow grow;
    ps_grow_init(&grow);
    int munge = part->raw_body != NULL && part->raw_is_8bit;
    if (munge) {
        /* The Content-Transfer-Encoding row reads base64 (the replace the
           munge does): the wire rows keep their order, the 8bit value
           becomes the base64 of the payload's utf-8 bytes. The payload
           row's stored text was the raw text; the emitted body is the
           encoded form. */
        for (int64_t i = 0; i < part->names->len; i++) {
            PsBuf wire;
            ps_seq_get(part->names, i, &wire);
            if (ps_email_nieq(wire.data, wire.len, "Content-Transfer-Encoding", 25)) {
                PsBuf as_b64 = {(int64_t)sizeof("base64") - 1, (char *)"base64"};
                ps_seq_set(part->values, i, &as_b64);
                char *lowered = ps_email_lower(wire.data, wire.len);
                PsBuf key = {wire.len, lowered};
                PsBuf val = {(int64_t)sizeof("base64") - 1, (char *)"base64"};
                if (ps_map_has(part->headers, &key)) {
                    ps_map_put(part->headers, &key, &val);
                }
                free(lowered);
                break;
            }
        }
    }
    for (int64_t i = 0; i < part->names->len; i++) {
        PsBuf wire;
        PsBuf raw;
        ps_seq_get(part->names, i, &wire);
        ps_seq_get(part->values, i, &raw);
        ps_email_fold_header(&grow, wire.data, wire.len, raw.data, raw.len);
    }
    ps_grow_byte(&grow, '\n');
    if (munge) {
        /* The munged body: CPython's _handle_text re-encodes the payload
           through its charset (the utf-8 Charset's body encoding is
           base64), so the emitted body is the base64 of the payload's
           utf-8 bytes, run through _write_lines (the trailing break's
           empty piece drops, which for base64 leaves the encoded line). */
        char *b64 = ps_email_b64_encode(part->body, part->body_len);
        ps_email_write_lines(&grow, b64, (int64_t)strlen(b64));
        free(b64);
    } else {
        ps_email_write_lines(&grow, part->body, part->body_len);
    }
    ps_grow_finish(&grow, out);
}


/* --- The builder surface (issue #1268). --- */

/* The unique headers: the names the default policy's header registry maps
   to a *Unique* (or otherwise max_count=1) class, so `msg[name] = value`
   refuses a second row case-insensitively, the way CPython's
   `Message.__setitem__` reads `header_max_count`. The spellings come from
   `email.headerregistry._default_header_map` plus the ParameterizedMIME
   and singleton classes. */
static const char *const PS_EMAIL_UNIQUE[] = {
    "subject", "date", "orig-date", "sender", "to", "cc", "bcc", "from",
    "reply-to", "mime-version", "content-type", "content-disposition",
    "content-transfer-encoding", "message-id", "in-reply-to", "references",
};
#define PS_EMAIL_UNIQUE_N (int64_t)(sizeof(PS_EMAIL_UNIQUE) / sizeof(PS_EMAIL_UNIQUE[0]))

/* `EmailMessage()`: an empty message. CPython's EmailMessage starts with
   no headers and a None payload; the parsed-record shape holds an empty
   body instead, which serializes the same way. */
void *ps_email_message_new(void) {
    PsEmailPart *part = calloc(1, sizeof(PsEmailPart));
    if (part == NULL) {
        abort();
    }
    part->headers = ps_map_new(2, (int64_t)sizeof(PsBuf), (int64_t)sizeof(PsBuf), 0);
    part->names = ps_seq_new((int64_t)sizeof(PsBuf), 0);
    part->values = ps_seq_new((int64_t)sizeof(PsBuf), 0);
    part->content_type = ps_email_dup("", 0);
    part->body = ps_email_dup("", 0);
    part->body_len = 0;
    return part;
}

/* `msg[name] = value`: the wire-spelled name and the value append to the
   header lists. CPython does not overwrite an existing row, and a header
   the policy caps at one row (the unique set above) refuses a second row
   case-insensitively, the way `header_max_count` does. */
void ps_email_header_set(void *handle, int64_t name_len, const char *name, int64_t value_len, const char *value) {
    PsEmailPart *part = handle;
    for (int64_t i = 0; i < PS_EMAIL_UNIQUE_N; i++) {
        if (ps_email_nieq(name, name_len, PS_EMAIL_UNIQUE[i], (int64_t)strlen(PS_EMAIL_UNIQUE[i])) &&
            ps_email_header_count(part, name_len, name) >= 1) {
            /* CPython's wording keeps the caller's spelling of the name. */
            char *msg = malloc((size_t)name_len + 64);
            if (msg == NULL) {
                abort();
            }
            snprintf(msg, (size_t)name_len + 64,
                     "There may be at most 1 %.*s headers in a message", (int)name_len, name);
            ps_raise_cstr(PS_VALUE, msg);
        }
    }
    ps_email_header_put(part, name, name_len, value, value_len);
}

/* The content rows are the ones `clear_content` drops: every header whose
   name starts with "content-", whatever its spelling (CPython lowercases
   the name before the prefix test). */
static int ps_email_is_content_row(const char *name, int64_t len) {
    return len >= 8 && ps_email_nieq(name, 8, "content-", 8);
}

/* `clear_content`: every content-* row goes and the payload resets to
   None (the empty body here). The runtime seq has no delete-at, so the
   lists rebuild and the first-value map rebuilds with them. */
static void ps_email_clear_content(PsEmailPart *part) {
    PsSeq *names = ps_seq_new((int64_t)sizeof(PsBuf), 0);
    PsSeq *values = ps_seq_new((int64_t)sizeof(PsBuf), 0);
    for (int64_t i = 0; i < part->names->len; i++) {
        PsBuf wire;
        PsBuf raw;
        ps_seq_get(part->names, i, &wire);
        ps_seq_get(part->values, i, &raw);
        if (ps_email_is_content_row(wire.data, wire.len)) {
            continue;
        }
        ps_seq_push(names, &wire);
        ps_seq_push(values, &raw);
    }
    part->names = names;
    part->values = values;
    part->headers = ps_map_new(2, (int64_t)sizeof(PsBuf), (int64_t)sizeof(PsBuf), 0);
    for (int64_t i = 0; i < part->names->len; i++) {
        PsBuf wire;
        PsBuf raw;
        ps_seq_get(part->names, i, &wire);
        ps_seq_get(part->values, i, &raw);
        char *lowered = ps_email_lower(wire.data, wire.len);
        PsBuf key = {wire.len, lowered};
        if (!ps_map_has(part->headers, &key)) {
            ps_map_put(part->headers, &key, &raw);
        }
        free(lowered);
    }
    free(part->body);
    part->body = ps_email_dup("", 0);
    part->body_len = 0;
    part->is_multipart = 0;
    part->parts = NULL;
    if (part->boundary != NULL) {
        free(part->boundary);
        part->boundary = NULL;
    }
}

/* The RFC 2045 safe body set: the bytes quoprimime keeps verbatim (the
   printable ASCII minus `=`, plus the tab). CR and LF ride the line
   split, not the escape map: quoprimime's body encode map keeps them
   verbatim too. */
static int ps_email_qp_safe(unsigned char c) {
    if (c == '\t' || c == '\r' || c == '\n') {
        return 1;
    }
    if (c < 32 || c > 126) {
        return 0;
    }
    return c != '=';
}

/* A piece of the encoded output: the bytes and their length. */
typedef struct { char *data; int64_t len; } Piece;

/* The quoted-printable body encoder, byte for byte CPython's
   `quoprimime.body_encode(body, 78)`: the RFC 2045 escape pass first (the
   body map, where CR and LF stay verbatim), then Python's `splitlines`
   cuts the escaped text (a lone \r ends a line like a \n does, the
   terminator never belongs to the line), every piece longer than 77
   columns takes a soft break (never inside an =XX escape, the `=` riding
   the piece), a trailing space or tab is quoted when the line has room
   for the three-byte form and soft-broken otherwise, and a body ending
   in a line break adds the empty final piece the join produces. */
static char *ps_email_qp_encode(const char *p, int64_t n) {
    static const char hex[17] = "0123456789ABCDEF";
    /* The escape pass: every byte outside RFC 2045's safe body set expands
       to =XX. */
    PsGrow escaped;
    ps_grow_init(&escaped);
    for (int64_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)p[i];
        if (ps_email_qp_safe(c)) {
            ps_grow_byte(&escaped, (char)c);
            continue;
        }
        ps_grow_byte(&escaped, '=');
        ps_grow_byte(&escaped, hex[c >> 4]);
        ps_grow_byte(&escaped, hex[c & 0xF]);
    }
    char *text = escaped.data;
    int64_t text_len = escaped.len;
    if (text_len == 0) {
        free(text);
        char *empty = malloc(1);
        if (empty == NULL) {
            abort();
        }
        empty[0] = '\0';
        return empty;
    }
    /* The piece list: CPython's body_encode over a 78-column limit. The
       pieces are joined with the newline at the end. */
    Piece *pieces = NULL;
    int64_t piece_count = 0;
    int64_t piece_cap = 0;
    #define PS_EMAIL_PUSH(ptr, plen) do { \
        if (piece_count == piece_cap) { \
            piece_cap = piece_cap ? piece_cap * 2 : 16; \
            Piece *grown = realloc(pieces, (size_t)piece_cap * sizeof(Piece)); \
            if (grown == NULL) { abort(); } \
            pieces = grown; \
        } \
        pieces[piece_count].data = ps_email_dup((ptr), (plen)); \
        pieces[piece_count].len = (plen); \
        piece_count++; \
    } while (0)
    /* The lines: the escaped text split the way Python's splitlines splits
       it (a \r, a \n and a \r\n each end a line, and the terminator does
       not belong to the line). */
    int64_t at = 0;
    while (at < text_len) {
        int64_t nl = at;
        while (nl < text_len && text[nl] != '\n' && text[nl] != '\r') nl++;
        int64_t len = nl - at;
        int64_t laststart = len - 1 - 78;
        int64_t col = 0;
        while (col <= laststart) {
            int64_t stop = col + 77;
            if (stop >= 2 && text[at + stop - 2] == '=') {
                /* Never split an escape: the piece ends before it. */
                PS_EMAIL_PUSH(text + at + col, stop - 1 - col);
                col = stop - 2;
            } else if (stop >= 1 && text[at + stop - 1] == '=') {
                PS_EMAIL_PUSH(text + at + col, stop - col);
                col = stop - 1;
            } else {
                /* The soft break rides the piece: line[start:stop] + '='. */
                PsGrow piece;
                ps_grow_init(&piece);
                ps_grow_add(&piece, text + at + col, stop - col);
                ps_grow_byte(&piece, '=');
                PS_EMAIL_PUSH(piece.data, piece.len);
                free(piece.data);
                col = stop;
            }
        }
        /* The rest of the line, with a trailing space or tab quoted so the
           decoder keeps it. */
        if (len > 0 && (text[at + len - 1] == ' ' || text[at + len - 1] == '\t')) {
            int64_t room = col - laststart;
            if (room >= 3) {
                /* There is room for the three-character quoted encoding:
                   line[start:-1] plus the =XX of the whitespace. */
                PsGrow tail;
                ps_grow_init(&tail);
                ps_grow_add(&tail, text + at + col, len - 1 - col);
                ps_grow_byte(&tail, '=');
                ps_grow_byte(&tail, hex[(unsigned char)text[at + len - 1] >> 4]);
                ps_grow_byte(&tail, hex[(unsigned char)text[at + len - 1] & 0xF]);
                PS_EMAIL_PUSH(tail.data, tail.len);
                free(tail.data);
            } else if (room == 2) {
                /* There is room for the whitespace itself and a soft
                   break: line[start:-1], the whitespace and the soft
                   break (`=` and the newline). The newline rides the
                   piece, the way CPython's soft_break does. */
                PsGrow tail;
                ps_grow_init(&tail);
                ps_grow_add(&tail, text + at + col, len - 1 - col);
                ps_grow_byte(&tail, (unsigned char)text[at + len - 1]);
                ps_grow_byte(&tail, '=');
                ps_grow_byte(&tail, '\n');
                PS_EMAIL_PUSH(tail.data, tail.len);
                free(tail.data);
            } else {
                /* There is room only for a soft break: one piece, line
                   [start:-1] plus the soft break and the quoted
                   whitespace (`=\n=XX`), so the quoted whitespace is the
                   only content on the next line. */
                PsGrow piece2;
                ps_grow_init(&piece2);
                ps_grow_add(&piece2, text + at + col, len - 1 - col);
                ps_grow_byte(&piece2, '=');
                ps_grow_byte(&piece2, '\n');
                ps_grow_byte(&piece2, '=');
                ps_grow_byte(&piece2, hex[(unsigned char)text[at + len - 1] >> 4]);
                ps_grow_byte(&piece2, hex[(unsigned char)text[at + len - 1] & 0xF]);
                PS_EMAIL_PUSH(piece2.data, piece2.len);
                free(piece2.data);
            }
        } else {
            PS_EMAIL_PUSH(text + at + col, len - col);
        }
        if (nl >= text_len) { at = nl; break; }
        if (text[nl] == '\r' && nl + 1 < text_len && text[nl + 1] == '\n') nl++;
        at = nl + 1;
    }
    /* The text ended with a line break: the trailing piece is the empty
       one CPython's join adds. */
    if (text[text_len - 1] == '\r' || text[text_len - 1] == '\n') {
        PS_EMAIL_PUSH("", 0);
    }
    /* The join. */
    PsGrow out;
    ps_grow_init(&out);
    for (int64_t i = 0; i < piece_count; i++) {
        if (i > 0) ps_grow_byte(&out, '\n');
        ps_grow_add(&out, pieces[i].data, pieces[i].len);
        free(pieces[i].data);
    }
    free(pieces);
    free(text);
    PsBuf done;
    ps_grow_finish(&out, &done);
    return done.data;
}

/* The 7bit answer: the lines joined with \n plus a final \n (CPython's
   normal_body). splitlines cuts \r, \n and \r\n, so a lone CR does not
   survive: the terminator is dropped and the line ends. */
static char *ps_email_normal_body(const char *p, int64_t n, int64_t *out_len) {
    PsGrow out;
    ps_grow_init(&out);
    /* CPython's lines: the utf-8 bytes split the way bytes.splitlines
       splits (\r\n, \r and \n end a line; the other Unicode line
       boundaries do not, that is the str split). The empty text is one
       empty line, so the join still writes the final \n. */
    int64_t at = 0;
    do {
        int64_t nl = at;
        while (nl < n && p[nl] != '\n' && p[nl] != '\r') nl++;
        ps_grow_add(&out, p + at, nl - at);
        ps_grow_byte(&out, '\n');
        if (nl < n && p[nl] == '\r' && nl + 1 < n && p[nl + 1] == '\n') nl++;
        at = nl + 1;
    } while (at <= n - 1 && n > 0);
    PsBuf done;
    ps_grow_finish(&out, &done);
    *out_len = done.len;
    return done.data;
}

/* `msg.set_content(text)`: the text/plain builder over the default policy
   (charset utf-8, linesep \n, line limit 78, cte_type 8bit). CPython's
   raw_data_manager.set_text_content clears the content rows, encodes the
   body, writes the payload, sets the charset parameter onto Content-Type
   and appends the transfer encoding; the EmailMessage subclass adds
   MIME-Version when the message does not carry it yet. The rows land in
   CPython's order: Content-Type, Content-Transfer-Encoding, then
   MIME-Version.

   The transfer-encoding choice is CPython's _encode_text heuristic: every
   line within 78 columns and all bytes ASCII is 7bit (normal_body), all
   lines within 78 columns with a non-ASCII byte is 8bit (the default
   policy's cte_type keeps the raw utf-8 bytes), and anything longer
   sniffs quoted-printable against base64 over the first ten lines (base64
   only wins when it is shorter, which over whole lines it never is, so
   the sniff lands on quoted-printable; the short-body early return makes
   the answer exact for ten lines or fewer). */
void ps_email_set_content(void *handle, int64_t text_len, const char *text) {
    PsEmailPart *part = handle;
    int64_t old_names = part->names->len;
    ps_email_clear_content(part);
    /* The utf-8 byte view of the text: the compiler's str cells already
       carry the bytes, so the pass below reads them directly. */
    int64_t longest = 0;
    int64_t line_len = 0;
    int ascii = 1;
    for (int64_t i = 0; i < text_len; i++) {
        unsigned char c = (unsigned char)text[i];
        if (c >= 0x80) {
            ascii = 0;
        }
        if (c == '\n' || c == '\r') {
            if (line_len > longest) {
                longest = line_len;
            }
            line_len = 0;
            if (c == '\r' && i + 1 < text_len && text[i + 1] == '\n') {
                i++;
            }
            continue;
        }
        line_len++;
    }
    if (line_len > longest) {
        longest = line_len;
    }
    const char *cte;
    char *payload;
    int64_t payload_len;
    int is_8bit = 0;
    if (longest <= 78 && ascii) {
        cte = "7bit";
        payload = ps_email_normal_body(text, text_len, &payload_len);
    } else if (longest <= 78) {
        cte = "8bit";
        is_8bit = 1;
        /* normal_body over the raw utf-8 bytes, kept verbatim: the CTE
           row reads 8bit here and `as_string` munges the body to base64
           the way CPython's generator does (the _handle_text charset
           pass over the default policy's 8bit cte_type). */
        payload = ps_email_normal_body(text, text_len, &payload_len);
    } else {
        cte = "quoted-printable";
        /* CPython encodes normal_body(text), not the raw text: the
           \n-terminated line list is what body_encode sees, so a text
           without a trailing break still encodes the \n the join adds. */
        int64_t nb_len;
        char *nb = ps_email_normal_body(text, text_len, &nb_len);
        payload = ps_email_qp_encode(nb, nb_len);
        free(nb);
        payload_len = (int64_t)strlen(payload);
    }
    free(part->body);
    part->body = payload;
    part->body_len = payload_len;
    free(part->raw_body);
    part->raw_body = ps_email_dup(text, text_len);
    part->raw_is_8bit = is_8bit;
    free(part->content_type);
    part->content_type = ps_email_dup("text/plain; charset=\"utf-8\"", 27);
    ps_email_header_put(part, "Content-Type", 12, part->content_type, 27);
    ps_email_header_put(part, "Content-Transfer-Encoding", 25, cte, (int64_t)strlen(cte));
    /* MIME-Version only when the message does not carry it (the subclass's
       `if 'MIME-Version' not in self`). The unique-row test ran on the
       pre-clear list, so a MIME-Version the caller set survives in
       `names` and the membership is the count over the kept rows. */
    int64_t has_mime = 0;
    for (int64_t i = 0; i < part->names->len; i++) {
        PsBuf wire;
        ps_seq_get(part->names, i, &wire);
        if (ps_email_nieq(wire.data, wire.len, "MIME-Version", 12)) {
            has_mime = 1;
            break;
        }
    }
    if (!has_mime) {
        ps_email_header_put(part, "MIME-Version", 12, "1.0", 3);
    }
    (void)old_names;
}

/* `msg.set_content(data, maintype=..., subtype=...)`: the bytes builder.
   CPython's raw_data_manager.set_bytes_content defaults cte to base64,
   so the rows are Content-Type (the maintype and the subtype the
   lowering spells), Content-Transfer-Encoding: base64, then MIME-Version.
   The base64 body is the encoded bytes over the policy's 78-column
   limit (57 bytes a line), the way CPython's _encode_base64 writes it. */
void ps_email_set_bytes(
    void *handle,
    int64_t data_len,
    const char *data,
    int64_t maintype_len,
    const char *maintype,
    int64_t subtype_len,
    const char *subtype
) {
    PsEmailPart *part = handle;
    ps_email_clear_content(part);
    char *encoded = ps_email_b64_encode(data, data_len);
    free(part->body);
    part->body = encoded;
    part->body_len = (int64_t)strlen(encoded);
    free(part->raw_body);
    part->raw_body = NULL;
    part->raw_is_8bit = 0;
    free(part->content_type);
    /* The Content-Type value: the maintype and the subtype joined by a
       slash (CPython's _prepare_set). */
    int64_t ctype_len = maintype_len + 1 + subtype_len;
    char *ctype = malloc((size_t)ctype_len + 1);
    if (ctype == NULL) {
        abort();
    }
    memcpy(ctype, maintype, (size_t)maintype_len);
    ctype[maintype_len] = '/';
    memcpy(ctype + maintype_len + 1, subtype, (size_t)subtype_len);
    ctype[ctype_len] = '\0';
    part->content_type = ctype;
    ps_email_header_put(part, "Content-Type", 12, part->content_type, ctype_len);
    ps_email_header_put(part, "Content-Transfer-Encoding", 25, "base64", 6);
    int64_t has_mime = 0;
    for (int64_t i = 0; i < part->names->len; i++) {
        PsBuf wire;
        ps_seq_get(part->names, i, &wire);
        if (ps_email_nieq(wire.data, wire.len, "MIME-Version", 12)) {
            has_mime = 1;
            break;
        }
    }
    if (!has_mime) {
        ps_email_header_put(part, "MIME-Version", 12, "1.0", 3);
    }
}
