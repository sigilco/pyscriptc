/* copy.copy / copy.deepcopy for list and dict containers.
   Shallow copies memcpy the container cells (str/bytes PsBufs share data;
   that is correct because buffers are immutable). deepcopy walks a
   %ps.copy.ty table emitted per call site by the LLVM emitter:
   kinds 1 int, 2 float, 3 str, 4 bytes, 5 list, 6 dict. Dict keys are
   atomic in the subset and are shared. */
#include "ps_rt.h"

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    int32_t kind;
    int32_t child;
} PsCpType;

PsSeq *ps_copy_seq(PsSeq *s) {
    PsSeq *out = ps_seq_new(s->elem, s->len);
    if (s->len > 0) {
        memcpy(out->data, s->data, (size_t)(s->elem * s->len));
    }
    return out;
}

PsMap *ps_copy_map(PsMap *m) {
    PsMap *out = ps_map_new(m->key_kind, m->key_size, m->val_size, m->len);
    char *kbuf = malloc((size_t)m->key_size);
    char *vbuf = malloc((size_t)m->val_size);
    int64_t i;
    if (kbuf == NULL || vbuf == NULL) {
        abort();
    }
    for (i = 0; i < m->len; i++) {
        ps_map_key_at(m, i, kbuf);
        ps_map_val_at(m, i, vbuf);
        ps_map_put(out, kbuf, vbuf);
    }
    free(kbuf);
    free(vbuf);
    return out;
}

static void cp_val(const PsCpType *t, int32_t idx, const void *src, void *out);

static PsSeq *cp_seq(const PsSeq *s, const PsCpType *t, int32_t elem) {
    PsSeq *out = ps_seq_new(s->elem, s->len);
    const char *sp = (const char *)s->data;
    char *dp = (char *)out->data;
    int64_t i;
    for (i = 0; i < s->len; i++) {
        cp_val(t, elem, sp + i * s->elem, dp + i * s->elem);
    }
    return out;
}

static PsMap *cp_map(const PsMap *m, const PsCpType *t, int32_t vidx) {
    PsMap *out = ps_map_new(m->key_kind, m->key_size, m->val_size, m->len);
    char *kbuf = malloc((size_t)m->key_size);
    char *vbuf = malloc((size_t)m->val_size);
    char *vout = malloc((size_t)m->val_size);
    int64_t i;
    if (kbuf == NULL || vbuf == NULL || vout == NULL) {
        abort();
    }
    for (i = 0; i < m->len; i++) {
        ps_map_key_at((PsMap *)m, i, kbuf);
        ps_map_val_at((PsMap *)m, i, vbuf);
        cp_val(t, vidx, vbuf, vout);
        ps_map_put(out, kbuf, vout);
    }
    free(kbuf);
    free(vbuf);
    free(vout);
    return out;
}

static void cp_val(const PsCpType *t, int32_t idx, const void *src, void *out) {
    switch (t[idx].kind) {
    case 1:
    case 2:
        memcpy(out, src, 8);
        break;
    case 3:
    case 4:
        memcpy(out, src, (size_t)sizeof(PsBuf));
        break;
    case 5:
        *(PsSeq **)out = cp_seq(*(PsSeq *const *)src, t, t[idx].child);
        break;
    case 6:
        *(PsMap **)out = cp_map(*(PsMap *const *)src, t, t[idx].child);
        break;
    }
}

void *ps_copy_deep(const PsCpType *t, int32_t root, const void *src) {
    if (t[root].kind == 5) {
        return cp_seq((const PsSeq *)src, t, t[root].child);
    }
    if (t[root].kind == 6) {
        return cp_map((const PsMap *)src, t, t[root].child);
    }
    return NULL;
}
