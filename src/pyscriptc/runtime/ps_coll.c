/* collections.deque. The value is this struct, not a field object.

Indexing follows Python: negative indexes count from the end, and an empty
pop raises IndexError.
*/

#include "ps_rt.h"

#include <stdlib.h>
#include <string.h>

typedef struct {
    int64_t len;
    int64_t cap;
    int64_t start;
    int64_t elem;
    char *data;
} PsDeque;

static char *ps_at(PsDeque *d, int64_t i) {
    int64_t at = d->start + i;
    if (d->cap > 0 && at >= d->cap) {
        at -= d->cap;
    }
    return d->data + (size_t)at * (size_t)d->elem;
}

static void ps_grow(PsDeque *d) {
    int64_t cap = d->cap == 0 ? 4 : d->cap * 2;
    char *data;
    int64_t i;
    if (cap < d->len + 1) {
        ps_raise_code(PS_OVERFLOW);
    }
    data = calloc((size_t)cap, (size_t)d->elem);
    if (data == NULL) {
        abort();
    }
    for (i = 0; i < d->len; i++) {
        memcpy(data + (size_t)i * (size_t)d->elem, ps_at(d, i), (size_t)d->elem);
    }
    free(d->data);
    d->data = data;
    d->start = 0;
    d->cap = cap;
}

static PsDeque *ps_new(int64_t elem) {
    PsDeque *d;
    if (elem <= 0) {
        ps_raise_code(PS_VALUE);
    }
    d = calloc(1, sizeof(PsDeque));
    if (d == NULL) {
        abort();
    }
    d->elem = elem;
    return d;
}

PsDeque *ps_deque_empty(int64_t elem) { return ps_new(elem); }

PsDeque *ps_deque_from(int64_t elem, PsSeq *src) {
    PsDeque *d = ps_new(elem);
    int64_t i;
    if (src->elem != elem) {
        ps_raise_cstr(PS_VALUE, "deque element size does not match");
    }
    for (i = 0; i < src->len; i++) {
        if (d->len == d->cap) {
            ps_grow(d);
        }
        memcpy(ps_at(d, d->len), (char *)src->data + (size_t)i * (size_t)elem, (size_t)elem);
        d->len += 1;
    }
    return d;
}

int64_t ps_deque_len(PsDeque *d) { return d->len; }

void ps_deque_append(PsDeque *d, void *src) {
    if (d->len == d->cap) {
        ps_grow(d);
    }
    memcpy(ps_at(d, d->len), src, (size_t)d->elem);
    d->len += 1;
}

void ps_deque_appendleft(PsDeque *d, void *src) {
    if (d->len == d->cap) {
        ps_grow(d);
    }
    d->start = d->start == 0 ? d->cap - 1 : d->start - 1;
    memcpy(ps_at(d, 0), src, (size_t)d->elem);
    d->len += 1;
}

static int64_t ps_index(int64_t len, int64_t i) {
    if (i < 0) {
        i += len;
    }
    if (i < 0 || i >= len) {
        ps_raise_code(PS_INDEX);
    }
    return i;
}

void ps_deque_get(PsDeque *d, int64_t i, void *out) {
    i = ps_index(d->len, i);
    memcpy(out, ps_at(d, i), (size_t)d->elem);
}

void ps_deque_pop(PsDeque *d, void *out) {
    if (d->len == 0) {
        ps_raise_code(PS_INDEX);
    }
    d->len -= 1;
    memcpy(out, ps_at(d, d->len), (size_t)d->elem);
}

void ps_deque_popleft(PsDeque *d, void *out) {
    if (d->len == 0) {
        ps_raise_code(PS_INDEX);
    }
    memcpy(out, ps_at(d, 0), (size_t)d->elem);
    d->len -= 1;
    if (d->cap > 0) {
        d->start += 1;
        if (d->start == d->cap) {
            d->start = 0;
        }
    }
}

/* collections.Counter. A Counter is a PsMap of int counts; a read of a
   missing key reports 0 in the lowerer, so the runtime only counts. */

void ps_counter_add(PsMap *m, void *key) {
    int64_t n = 0;
    if (ps_map_has(m, key)) {
        ps_map_get(m, key, &n);
    }
    n += 1;
    ps_map_put(m, key, &n);
}

PsMap *ps_counter_from_buf(int64_t len, const char *p) {
    PsMap *m = ps_map_new(2, 16, 8, len < 16 ? len : 16);
    PsBuf key;
    int64_t i;
    key.len = 1;
    for (i = 0; i < len; i++) {
        key.data = (char *)p + i;
        ps_counter_add(m, &key);
    }
    return m;
}

PsMap *ps_counter_from_seq(PsSeq *s) {
    int32_t kind = s->elem == 8 ? 1 : 2;
    PsMap *m = ps_map_new(kind, s->elem, 8, s->len < 16 ? s->len : 16);
    char cell[16];
    int64_t i;
    for (i = 0; i < s->len; i++) {
        ps_seq_get(s, i, cell);
        ps_counter_add(m, cell);
    }
    return m;
}

void ps_counter_update(PsMap *dst, PsMap *src) {
    char cell[16];
    int64_t i;
    int64_t n;
    for (i = 0; i < src->len; i++) {
        ps_map_key_at(src, i, cell);
        ps_map_val_at(src, i, &n);
        if (ps_map_has(dst, cell)) {
            int64_t cur = 0;
            ps_map_get(dst, cell, &cur);
            n += cur;
        }
        ps_map_put(dst, cell, &n);
    }
}

PsMap *ps_counter_from_map(PsMap *src) {
    PsMap *m = ps_map_new(src->key_kind, src->key_size, src->val_size, src->len);
    ps_counter_update(m, src);
    return m;
}
