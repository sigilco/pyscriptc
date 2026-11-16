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

/* ---- the repr and the str form (issue #1267) --------------------------- */

/* CPython's repr of the sum's arms, recursive over the list and dict arms.
   The str arm rides the shared escape engine (ps_repr_str), the float arm
   the shortest round-trip writer (ps_f64_str), and the list and dict arms
   walk their slots (a list cell is a PsDyn* pointer, a dict value slot is
   a PsDyn* pointer under a PsBuf key).

   The `str_form` walk is the same walk with one difference: print calls
   str(), and str() of a str arm is the text itself where the repr quotes
   it. Every other arm's str form is its repr form. */

static void dyn_repr_cell(PsGrow *g, const PsDyn *v, int str_form);

/* A str arm's repr: the shared escape engine (ps_repr_str) writes the
   quoted form and picks the quote character the way CPython does (single
   quotes unless the text holds one and no double quote). */
static void repr_dyn_str(PsGrow *g, int64_t n, const char *p) {
    PsBuf t;
    ps_repr_str(n, p, &t);
    ps_grow_add(g, t.data, t.len);
    free(t.data);
}

static void dyn_repr_seq(PsGrow *g, PsSeq *s, int str_form) {
    ps_grow_byte(g, '[');
    for (int64_t i = 0; i < s->len; i++) {
        if (i > 0) {
            ps_grow_add(g, ", ", 2);
        }
        PsDyn *item = NULL;
        memcpy(&item, (char *)s->data + (size_t)i * (size_t)s->elem, sizeof item);
        dyn_repr_cell(g, item, str_form);
    }
    ps_grow_byte(g, ']');
}

static void dyn_repr_map(PsGrow *g, PsMap *m, int str_form) {
    char *key = malloc((size_t)m->key_size);
    char *val = malloc((size_t)m->val_size);
    if (key == NULL || val == NULL) {
        abort();
    }
    ps_grow_byte(g, '{');
    for (int64_t i = 0; i < m->len; i++) {
        if (i > 0) {
            ps_grow_add(g, ", ", 2);
        }
        ps_map_key_at(m, i, key);
        PsBuf kb;
        memcpy(&kb, key, sizeof kb);
        repr_dyn_str(g, kb.len, kb.data);
        ps_grow_add(g, ": ", 2);
        ps_map_val_at(m, i, val);
        PsDyn *item = NULL;
        memcpy(&item, val, sizeof item);
        dyn_repr_cell(g, item, str_form);
    }
    ps_grow_byte(g, '}');
    free(key);
    free(val);
}

static void dyn_repr_cell(PsGrow *g, const PsDyn *v, int str_form) {
    if (v == NULL || v->tag == PS_DYN_NONE) {
        ps_grow_add(g, "None", 4);
        return;
    }
    if (v->tag == PS_DYN_BOOL) {
        if (v->i) {
            ps_grow_add(g, "True", 4);
        } else {
            ps_grow_add(g, "False", 5);
        }
        return;
    }
    if (v->tag == PS_DYN_INT) {
        PsBuf t;
        ps_i64_str(v->i, &t);
        ps_grow_add(g, t.data, t.len);
        free(t.data);
        return;
    }
    if (v->tag == PS_DYN_FLOAT) {
        double f;
        memcpy(&f, &v->i, sizeof f);
        PsBuf t;
        ps_f64_str(f, &t);
        ps_grow_add(g, t.data, t.len);
        free(t.data);
        return;
    }
    if (v->tag == PS_DYN_STR) {
        if (str_form) {
            ps_grow_add(g, v->b.data, v->b.len);
        } else {
            repr_dyn_str(g, v->b.len, v->b.data);
        }
        return;
    }
    if (v->tag == PS_DYN_LIST) {
        /* A container's str form is its repr form, so the nested cells
           keep the quoted str spelling (CPython's `str([...])`). */
        dyn_repr_seq(g, (PsSeq *)v->p, 0);
        return;
    }
    if (v->tag == PS_DYN_DICT) {
        dyn_repr_map(g, (PsMap *)v->p, 0);
        return;
    }
    ps_raise_code(PS_RUNTIME);
}

void ps_dyn_repr(const PsDyn *v, PsBuf *out) {
    PsGrow g;
    ps_grow_init(&g);
    dyn_repr_cell(&g, v, 0);
    ps_grow_finish(&g, out);
}

/* The `str()` form print lowers to: the str arm's text, every other arm's
   repr. A `dict[str, object]` print rides the same rule: the map itself is
   always its repr, only the nested str arms change. */
void ps_dyn_str_form(const PsDyn *v, PsBuf *out) {
    PsGrow g;
    ps_grow_init(&g);
    dyn_repr_cell(&g, v, 1);
    ps_grow_finish(&g, out);
}

void ps_dyn_map_repr(const PsMap *m, PsBuf *out) {
    PsGrow g;
    ps_grow_init(&g);
    dyn_repr_map(&g, (PsMap *)m, 0);
    ps_grow_finish(&g, out);
}

void ps_dyn_seq_repr(const PsSeq *s, PsBuf *out) {
    PsGrow g;
    ps_grow_init(&g);
    dyn_repr_seq(&g, (PsSeq *)s, 0);
    ps_grow_finish(&g, out);
}
