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
