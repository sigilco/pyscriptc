/* heapq: the closed heap-queue subset of CPython's heapq.

   The heap is a `list[int]`: the runtime sorts the cells by their signed
   64-bit value, the same order the `int` comparison in the compiler gives.
   The algorithms are CPython's `_heapq` module: the sift-down/sift-up pair
   from `Modules/_heapqmodule.c`, so `heapify` leaves the same array layout
   CPython leaves and `heappop` returns the same values in the same order.

   The subset is `heapify`, `heappush`, `heappop`, `nlargest` and
   `nsmallest`. The `*_max` family, `heapreplace`, `heappushpop` and
   `merge` stay fenced by the lowerer (PS2001).
*/

#include "ps_rt.h"

#include <stdlib.h>
#include <string.h>

/* The cells of a `list[int]`. A list cell is an int64_t; the heap works on
   the raw cell array, so the element size is checked once per call. */
static int64_t ps_heap_i64(PsSeq *s, int64_t index) {
    int64_t value = 0;
    memcpy(&value, (char *)s->data + (size_t)index * (size_t)s->elem, sizeof(int64_t));
    return value;
}

static void ps_heap_set_i64(PsSeq *s, int64_t index, int64_t value) {
    memcpy((char *)s->data + (size_t)index * (size_t)s->elem, &value, sizeof(int64_t));
}

/* The list holds ints. A list of anything else fences at compile time, so
   the runtime check is the backstop for a list that reached the call
   through another route. */
static void ps_heap_check(PsSeq *s) {
    if (s->elem != (int64_t)sizeof(int64_t)) {
        ps_raise_cstr(PS_VALUE, "heapq takes a list of int");
    }
}

/* CPython's `_siftdown`: move the parents down along the path to the root
   until the item fits, then put the item in its place. */
static void ps_heap_siftdown(PsSeq *s, int64_t startpos, int64_t pos) {
    int64_t newitem = ps_heap_i64(s, pos);
    while (pos > startpos) {
        int64_t parentpos = (pos - 1) >> 1;
        int64_t parent = ps_heap_i64(s, parentpos);
        if (newitem < parent) {
            ps_heap_set_i64(s, pos, parent);
            pos = parentpos;
            continue;
        }
        break;
    }
    ps_heap_set_i64(s, pos, newitem);
}

/* CPython's `_siftup`: bubble the smaller child up until the leaf, then
   sift the oddball down to its resting place. */
static void ps_heap_siftup(PsSeq *s, int64_t pos) {
    int64_t endpos = s->len;
    int64_t startpos = pos;
    int64_t newitem = ps_heap_i64(s, pos);
    int64_t childpos = 2 * pos + 1;
    while (childpos < endpos) {
        int64_t rightpos = childpos + 1;
        if (rightpos < endpos && !(ps_heap_i64(s, childpos) < ps_heap_i64(s, rightpos))) {
            childpos = rightpos;
        }
        ps_heap_set_i64(s, pos, ps_heap_i64(s, childpos));
        pos = childpos;
        childpos = 2 * pos + 1;
    }
    ps_heap_set_i64(s, pos, newitem);
    ps_heap_siftdown(s, startpos, pos);
}

void ps_heap_heapify(PsSeq *s) {
    ps_heap_check(s);
    int64_t n = s->len;
    /* The largest index with a child in range is n//2 - 1. */
    for (int64_t i = (n >> 1) - 1; i >= 0; i--) {
        ps_heap_siftup(s, i);
    }
    /* The membership index (a set's hash table) is stale after the moves. */
    ps_seq_index_invalidate(s);
}

void ps_heap_heappush(PsSeq *s, int64_t item) {
    ps_heap_check(s);
    ps_seq_push(s, &item);
    ps_heap_siftdown(s, 0, s->len - 1);
}

int64_t ps_heap_heappop(PsSeq *s) {
    ps_heap_check(s);
    if (s->len == 0) {
        ps_raise_cstr(PS_INDEX, "index out of range");
    }
    int64_t lastelt = ps_heap_i64(s, s->len - 1);
    s->len -= 1;
    if (s->len == 0) {
        ps_seq_index_invalidate(s);
        return lastelt;
    }
    int64_t returnitem = ps_heap_i64(s, 0);
    ps_heap_set_i64(s, 0, lastelt);
    ps_heap_siftup(s, 0);
    ps_seq_index_invalidate(s);
    return returnitem;
}

/* A stable int64 merge sort: CPython's `nlargest`/`nsmallest` decorate the
   cells with their input position before sorting, so equal values come out
   in their input order. */
static void ps_heap_merge(int64_t *cells, int64_t *tmp, int64_t lo, int64_t mid, int64_t hi, int64_t largest) {
    int64_t i = lo;
    int64_t j = mid;
    int64_t k = lo;
    while (i < mid && j < hi) {
        int64_t take_left;
        if (largest) {
            take_left = cells[i] >= cells[j];
        } else {
            take_left = cells[i] <= cells[j];
        }
        if (take_left) {
            tmp[k++] = cells[i++];
        } else {
            tmp[k++] = cells[j++];
        }
    }
    while (i < mid) {
        tmp[k++] = cells[i++];
    }
    while (j < hi) {
        tmp[k++] = cells[j++];
    }
    memcpy(cells + lo, tmp + lo, (size_t)(hi - lo) * (size_t)sizeof(int64_t));
}

static void ps_heap_sort(int64_t *cells, int64_t *tmp, int64_t lo, int64_t hi, int64_t largest) {
    if (hi - lo < 2) {
        return;
    }
    int64_t mid = lo + (hi - lo) / 2;
    ps_heap_sort(cells, tmp, lo, mid, largest);
    ps_heap_sort(cells, tmp, mid, hi, largest);
    ps_heap_merge(cells, tmp, lo, mid, hi, largest);
}

/* `nlargest(n, xs)` and `nsmallest(n, xs)`: the whole list is sorted with a
   stable merge sort, then the extreme end is kept. That is the list CPython
   returns: its decorated heap breaks ties by the input position, and a
   stable sort keeps the same order. */
static PsSeq *ps_heap_extremes(PsSeq *src, int64_t n, int64_t largest) {
    int64_t len = src->len;
    if (n < 0) {
        n = 0;
    }
    if (n > len) {
        n = len;
    }
    PsSeq *out = ps_seq_new((int64_t)sizeof(int64_t), n);
    if (len == 0 || n == 0) {
        return out;
    }
    /* The whole list is sorted, then the extreme end is kept. The sort is
       stable, so equal values keep their input order the way CPython's
       decorated heap does. */
    int64_t *cells = malloc((size_t)len * (size_t)sizeof(int64_t));
    if (cells == NULL) {
        abort();
    }
    int64_t *tmp = malloc((size_t)len * (size_t)sizeof(int64_t));
    if (tmp == NULL) {
        abort();
    }
    memcpy(cells, src->data, (size_t)len * (size_t)sizeof(int64_t));
    /* The sort is stable: CPython decorates the cells with their input
       position before sorting, so equal values come out in their input
       order. `nlargest` sorts descending (with `reverse=True`, which keeps
       the equal values in input order too) and takes the head; `nsmallest`
       sorts ascending and takes the head. */
    ps_heap_sort(cells, tmp, 0, len, largest);
    memcpy(out->data, cells, (size_t)n * (size_t)sizeof(int64_t));
    free(cells);
    free(tmp);
    return out;
}

PsSeq *ps_heap_nlargest(int64_t n, PsSeq *s) {
    ps_heap_check(s);
    return ps_heap_extremes(s, n, 1);
}

PsSeq *ps_heap_nsmallest(int64_t n, PsSeq *s) {
    ps_heap_check(s);
    return ps_heap_extremes(s, n, 0);
}
