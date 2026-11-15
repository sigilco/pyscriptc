/* SPDX-License-Identifier: Apache-2.0 */

/* The tagged dynamic value (issue #1226): the sum of the lowered shapes
   (None, bool, int, float, str, list, dict) in one heap cell. The tag names
   the arm and the payload sits at a fixed offset, so a narrowed read is a
   load, not a switch.

   The runtime has no refcounts and no GC (ps_rt.c: the exit registry), so a
   cell lives until the process dies. It is anchored in the same registry as
   every other heap object, so LeakSanitizer stays silent at exit. A cell
   that holds a list or a dict arm points at a PsSeq or a PsMap that the
   runtime already owns, so no ownership propagates through the cell. */

#include "ps_rt.h"

#include <stdlib.h>
#include <string.h>

PsDyn *ps_dyn_new(int32_t tag) {
    PsDyn *v = calloc(1, sizeof(PsDyn));
    if (v == NULL) {
        abort();
    }
    ps_own(v);
    v->tag = tag;
    return v;
}

PsDyn *ps_dyn_new_none(void) { return ps_dyn_new(PS_DYN_NONE); }

PsDyn *ps_dyn_new_bool(int64_t b) {
    PsDyn *v = ps_dyn_new(PS_DYN_BOOL);
    v->i = b ? 1 : 0;
    return v;
}

PsDyn *ps_dyn_new_int(int64_t i) {
    PsDyn *v = ps_dyn_new(PS_DYN_INT);
    v->i = i;
    return v;
}

PsDyn *ps_dyn_new_float(double f) {
    PsDyn *v = ps_dyn_new(PS_DYN_FLOAT);
    memcpy(&v->i, &f, sizeof f);
    return v;
}

PsDyn *ps_dyn_new_str(int64_t len, char *data) {
    PsDyn *v = ps_dyn_new(PS_DYN_STR);
    v->b.len = len;
    v->b.data = data;
    return v;
}

PsDyn *ps_dyn_new_list(void *seq) {
    PsDyn *v = ps_dyn_new(PS_DYN_LIST);
    v->p = seq;
    return v;
}

PsDyn *ps_dyn_new_dict(void *map) {
    PsDyn *v = ps_dyn_new(PS_DYN_DICT);
    v->p = map;
    return v;
}

int ps_dyn_is(const PsDyn *v, int32_t kind) {
    if (v == NULL) {
        return 0;
    }
    if (kind == PS_DYN_INT && v->tag == PS_DYN_BOOL) {
        /* CPython's bool is a subclass of int. */
        return 1;
    }
    return v->tag == kind;
}

int64_t ps_dyn_int(const PsDyn *v) { return v->i; }

double ps_dyn_float(const PsDyn *v) {
    double f;
    memcpy(&f, &v->i, sizeof f);
    return f;
}

int64_t ps_dyn_bool(const PsDyn *v) { return v->i; }

PsBuf ps_dyn_str(const PsDyn *v) { return v->b; }

void *ps_dyn_list(const PsDyn *v) { return v->p; }

void *ps_dyn_dict(const PsDyn *v) { return v->p; }

int ps_dyn_truth(const PsDyn *v) {
    if (v == NULL) {
        return 0;
    }
    switch (v->tag) {
    case PS_DYN_NONE:
        return 0;
    case PS_DYN_BOOL:
        return v->i ? 1 : 0;
    case PS_DYN_INT:
        return v->i != 0;
    case PS_DYN_FLOAT: {
        double f = ps_dyn_float(v);
        return f != 0.0 ? 1 : 0;
    }
    case PS_DYN_STR:
        return v->b.len != 0;
    case PS_DYN_LIST:
    case PS_DYN_DICT: {
        PsSeq *s = (PsSeq *)v->p;
        return s != NULL && s->len != 0;
    }
    default:
        return 0;
    }
}

void ps_dyn_store(void *slot, const PsDyn *v) {
    /* A dynamic value crosses a map or a sequence slot as a pointer to its
       cell, so the slot copy is a pointer copy. */
    memcpy(slot, &v, sizeof v);
}

void ps_dyn_load(void *out, const PsDyn *v) { memcpy(out, &v, sizeof v); }
