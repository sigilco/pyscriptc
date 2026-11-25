/* SPDX-License-Identifier: Apache-2.0 */
/* The tuple dict key (issue #1368).

   A `dict[tuple[str, str | None], int]` stores a tuple as its key. CPython
   hashes a tuple field-wise and compares it field-wise, so two tuples with
   the same fields are the same key and two tuples that differ in any field
   are different keys.

   The map stores a key as a fixed-size cell, and its hash and equality are
   the two functions that switch on the map's `key_kind`. This file adds the
   third kind: a tuple key. The cell holds one pointer to a `PsTupleKey`, a
   heap record the runtime owns, and the record is self-describing: it names
   each field's kind and carries the field payloads in a flat buffer.

   The kinds are the field shapes a tuple key may carry:

     PS_TK_INT   an int field (8 bytes)
     PS_TK_STR   a str field (the 16-byte PsBuf prefix)
     PS_TK_FLOAT a float field (8 bytes, the double's bits)
     PS_TK_BOOL  a bool field (1 byte)
     PS_TK_OPT_* an optional field of the arm above (a tag byte and, when
                 the tag says the value is there, the arm's payload)

   The optional arm is what makes `(section, None)` a key that equals
   another `(section, None)`: the optional is compared by its tag and its
   payload, never by the address of its heap cell.

   The packer is the compiler's job: it knows each field's static type, so
   it writes the field kinds and the field payloads into the record. The
   runtime only reads them back, hashes them and compares them. */

#include "ps_rt.h"

#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

/* The field kinds. The values are part of the compiler's ABI: the packer
   writes them into the record's kind array. */
#define PS_TK_INT 1
#define PS_TK_STR 2
#define PS_TK_FLOAT 3
#define PS_TK_BOOL 4
#define PS_TK_OPT_INT 5
#define PS_TK_OPT_STR 6
#define PS_TK_OPT_FLOAT 7
#define PS_TK_OPT_BOOL 8

/* The record: the field count, the per-field kinds and the flat payload
   buffer. The kinds array and the payload buffer are one allocation each
   and the record itself is a third; nothing is freed (the runtime never
   frees its heap objects: the exit registry anchors them). */
struct PsTupleKey {
    int64_t n;
    int32_t *kinds;
    uint8_t *data;
    int64_t size;
};

/* The payload size of one field, per kind. An optional field carries its
   tag byte plus the arm's payload, so its size is one more than the arm's
   own. The str arm's payload is the 16-byte PsBuf prefix. */
static int64_t ps_tk_size(int32_t kind) {
    switch (kind) {
        case PS_TK_INT:
        case PS_TK_FLOAT:
            return 8;
        case PS_TK_BOOL:
            return 1;
        case PS_TK_STR:
            return 16;
        case PS_TK_OPT_INT:
        case PS_TK_OPT_FLOAT:
            return 9;
        case PS_TK_OPT_STR:
            return 17;
        case PS_TK_OPT_BOOL:
            return 2;
        default:
            return 0;
    }
}

/* The arm kind of an optional field: the arm the tag selects. */
static int32_t ps_tk_arm(int32_t kind) {
    switch (kind) {
        case PS_TK_OPT_INT:
            return PS_TK_INT;
        case PS_TK_OPT_STR:
            return PS_TK_STR;
        case PS_TK_OPT_FLOAT:
            return PS_TK_FLOAT;
        case PS_TK_OPT_BOOL:
            return PS_TK_BOOL;
        default:
            return kind;
    }
}

/* Build the record: `n` fields, the kinds array and the payload buffer.
   The compiler packs the fields into `data` itself (it knows the static
   layout of each field), so this only allocates and records the shape. */
void *ps_tuple_key_new(int64_t n, int32_t *kinds) {
    if (n < 0) {
        ps_raise_code(PS_OVERFLOW);
    }
    struct PsTupleKey *k = calloc(1, sizeof *k);
    if (k == NULL) {
        abort();
    }
    ps_own(k);
    k->n = n;
    k->kinds = calloc((size_t)(n > 0 ? n : 1), sizeof(int32_t));
    if (k->kinds == NULL) {
        abort();
    }
    int64_t size = 0;
    for (int64_t i = 0; i < n; i++) {
        k->kinds[i] = kinds[i];
        size += ps_tk_size(kinds[i]);
    }
    k->size = size;
    k->data = calloc((size_t)(size > 0 ? size : 1), 1);
    if (k->data == NULL) {
        abort();
    }
    return k;
}

/* The payload buffer of the record: the compiler writes the fields into
   it, one after another, each at the offset its kind's size implies. */
void *ps_tuple_key_data(void *key) {
    struct PsTupleKey *k = key;
    return k->data;
}

/* The offset of field `i` in the payload buffer. */
int64_t ps_tuple_key_offset(void *key, int64_t i) {
    struct PsTupleKey *k = key;
    int64_t off = 0;
    for (int64_t j = 0; j < i && j < k->n; j++) {
        off += ps_tk_size(k->kinds[j]);
    }
    return off;
}

/* The hash of one field: the same mix the map uses for a scalar key, so a
   tuple's hash mixes its fields the way CPython's tuple hash does (the
   exact value differs, and it never has to: the hash is internal). */
static uint64_t ps_tk_field_hash(int32_t kind, const uint8_t *p) {
    switch (kind) {
        case PS_TK_INT: {
            int64_t v;
            memcpy(&v, p, 8);
            uint64_t x = (uint64_t)v;
            x += 0x9E3779B97F4A7C15ULL;
            x = (x ^ (x >> 30)) * 0xBF58476D1CE4E5B9ULL;
            x = (x ^ (x >> 27)) * 0x94D049BB133111EBULL;
            return x ^ (x >> 31);
        }
        case PS_TK_FLOAT: {
            double d;
            memcpy(&d, p, 8);
            int64_t v;
            memcpy(&v, p, 8);
            uint64_t x = (uint64_t)v;
            x += 0x9E3779B97F4A7C15ULL;
            x = (x ^ (x >> 30)) * 0xBF58476D1CE4E5B9ULL;
            x = (x ^ (x >> 27)) * 0x94D049BB133111EBULL;
            return x ^ (x >> 31);
        }
        case PS_TK_BOOL:
            return (uint64_t)(p[0] ? 1 : 0) * 0x9E3779B97F4A7C15ULL;
        case PS_TK_STR: {
            PsBuf b;
            memcpy(&b, p, 16);
            uint64_t h = 0xCBF29CE484222325ULL;
            for (int64_t i = 0; i < b.len; i++) {
                h ^= (unsigned char)b.data[i];
                h *= 0x100000001B3ULL;
            }
            return h;
        }
        default:
            return 0;
    }
}

/* The hash of a tuple key: the fields' hashes mixed together, in order.
   The optional fields fold their tag into the mix, so `(s, None)` and
   `(s, "x")` do not collide by construction. */
int64_t ps_tuple_key_hash(void *key) {
    struct PsTupleKey *k = key;
    uint64_t h = 0x9E3779B97F4A7C15ULL;
    int64_t off = 0;
    for (int64_t i = 0; i < k->n; i++) {
        int32_t kind = k->kinds[i];
        if (kind >= PS_TK_OPT_INT) {
            /* The optional: the tag byte, then the arm's payload when the
               tag says the value is there. */
            h ^= (uint64_t)k->data[off];
            h *= 0x100000001B3ULL;
            if (k->data[off] != 0) {
                h ^= ps_tk_field_hash(ps_tk_arm(kind), k->data + off + 1);
                h *= 0x100000001B3ULL;
            }
            off += ps_tk_size(kind);
            continue;
        }
        h ^= ps_tk_field_hash(kind, k->data + off);
        h *= 0x100000001B3ULL;
        off += ps_tk_size(kind);
    }
    h += 0x9E3779B97F4A7C15ULL;
    h = (h ^ (h >> 30)) * 0xBF58476D1CE4E5B9ULL;
    h = (h ^ (h >> 27)) * 0x94D049BB133111EBULL;
    return (int64_t)(h ^ (h >> 31));
}

/* The equality of one field. */
static int ps_tk_field_eq(int32_t kind, const uint8_t *a, const uint8_t *b) {
    switch (kind) {
        case PS_TK_INT: {
            int64_t x;
            int64_t y;
            memcpy(&x, a, 8);
            memcpy(&y, b, 8);
            return x == y;
        }
        case PS_TK_FLOAT: {
            int64_t x;
            int64_t y;
            memcpy(&x, a, 8);
            memcpy(&y, b, 8);
            /* CPython compares floats by value, and 0.0 == -0.0 while their
               bit patterns differ, so the comparison is on the value. */
            double dx;
            double dy;
            memcpy(&dx, a, 8);
            memcpy(&dy, b, 8);
            return dx == dy;
        }
        case PS_TK_BOOL:
            return (a[0] != 0) == (b[0] != 0);
        case PS_TK_STR: {
            PsBuf x;
            PsBuf y;
            memcpy(&x, a, 16);
            memcpy(&y, b, 16);
            if (x.len != y.len) {
                return 0;
            }
            if (x.len == 0) {
                return 1;
            }
            return memcmp(x.data, y.data, (size_t)x.len) == 0;
        }
        default:
            return 0;
    }
}

/* The equality of two tuple keys: the same field count, then every field
   equal. An optional field compares by its tag and, when the tag says the
   value is there, by its payload. */
int32_t ps_tuple_key_eq(void *a, void *b) {
    struct PsTupleKey *ka = a;
    struct PsTupleKey *kb = b;
    if (ka->n != kb->n) {
        return 0;
    }
    int64_t off = 0;
    for (int64_t i = 0; i < ka->n; i++) {
        int32_t kind = ka->kinds[i];
        if (kind != kb->kinds[i]) {
            return 0;
        }
        if (kind >= PS_TK_OPT_INT) {
            if ((ka->data[off] != 0) != (kb->data[off] != 0)) {
                return 0;
            }
            if (ka->data[off] != 0) {
                if (!ps_tk_field_eq(ps_tk_arm(kind), ka->data + off + 1, kb->data + off + 1)) {
                    return 0;
                }
            }
            off += ps_tk_size(kind);
            continue;
        }
        if (!ps_tk_field_eq(kind, ka->data + off, kb->data + off)) {
            return 0;
        }
        off += ps_tk_size(kind);
    }
    return 1;
}

/* The repr of one field, the way CPython renders a tuple's element. A str
   field is quoted (the same escape engine the exception messages use), an
   optional field renders its arm or `None`. */
static void ps_tk_repr_field(PsGrow *g, int32_t kind, const uint8_t *p) {
    switch (kind) {
        case PS_TK_INT: {
            int64_t v;
            memcpy(&v, p, 8);
            char buf[32];
            snprintf(buf, sizeof buf, "%lld", (long long)v);
            ps_grow_add(g, buf, (int64_t)strlen(buf));
            return;
        }
        case PS_TK_FLOAT: {
            double d;
            memcpy(&d, p, 8);
            char buf[64];
            snprintf(buf, sizeof buf, "%g", d);
            ps_grow_add(g, buf, (int64_t)strlen(buf));
            return;
        }
        case PS_TK_BOOL:
            ps_grow_add(g, p[0] ? "True" : "False", p[0] ? 4 : 5);
            return;
        case PS_TK_STR: {
            PsBuf b;
            memcpy(&b, p, 16);
            PsBuf quoted;
            ps_repr_str(b.len, b.data, &quoted);
            ps_grow_add(g, quoted.data, quoted.len);
            free(quoted.data);
            return;
        }
        default:
            return;
    }
}

/* The repr of a tuple key: CPython's tuple repr, `(a, None)`. The
   KeyError message of a missing tuple key is this repr, the way a missing
   str key's message is the str's repr. */
void ps_tuple_key_repr(void *key, PsBuf *out) {
    struct PsTupleKey *k = key;
    PsGrow g;
    ps_grow_init(&g);
    ps_grow_byte(&g, '(');
    int64_t off = 0;
    for (int64_t i = 0; i < k->n; i++) {
        if (i > 0) {
            ps_grow_add(&g, ", ", 2);
        }
        int32_t kind = k->kinds[i];
        if (kind >= PS_TK_OPT_INT) {
            if (k->data[off] == 0) {
                ps_grow_add(&g, "None", 4);
            } else {
                ps_tk_repr_field(&g, ps_tk_arm(kind), k->data + off + 1);
            }
            off += ps_tk_size(kind);
            continue;
        }
        ps_tk_repr_field(&g, kind, k->data + off);
        off += ps_tk_size(kind);
    }
    if (k->n == 1) {
        ps_grow_byte(&g, ',');
    }
    ps_grow_byte(&g, ')');
    ps_grow_finish(&g, out);
}
