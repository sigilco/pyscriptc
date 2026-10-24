/* R1-R5 helpers: list/dict method cores, str tier-2 methods, scalar casts.

All strings and bytes are PsBuf cells ({i64 len, char *data}); comparisons are
bytewise, which pins the whole surface to ASCII/Latin-1 semantics. Every
mutating helper works in place on the caller's PsSeq/PsMap.
*/

#include "ps_rt.h"

#include <stdlib.h>
#include <string.h>

static char *ps_cell(PsSeq *s, int64_t i) {
    return (char *)s->data + (size_t)i * (size_t)s->elem;
}

static void ps_seq_gap(PsSeq *s) {
    if (s->len == s->cap) {
        int64_t cap = s->cap == 0 ? 4 : s->cap * 2;
        void *data;
        if ((uint64_t)cap > UINT64_MAX / (uint64_t)s->elem) {
            ps_raise_code(PS_OVERFLOW);
        }
        data = realloc(s->data, (size_t)cap * (size_t)s->elem);
        if (data == NULL) {
            abort();
        }
        s->data = data;
        s->cap = cap;
    }
}

static void ps_push_view(PsSeq *seq, const char *p, int64_t n) {
    PsBuf piece;
    ps_buf_view(&piece, p, n);
    ps_seq_push(seq, &piece);
}

/* list core (R2) */

PsSeq *ps_seq_concat(PsSeq *a, PsSeq *b) {
    PsSeq *out;
    if (a->elem != b->elem) {
        ps_raise_cstr(PS_VALUE, "list concat element size mismatch");
    }
    out = ps_seq_new(a->elem, a->len + b->len);
    if (a->len > 0) {
        memcpy(out->data, a->data, (size_t)a->len * (size_t)a->elem);
    }
    if (b->len > 0) {
        memcpy(ps_cell(out, a->len), b->data, (size_t)b->len * (size_t)b->elem);
    }
    return out;
}

PsSeq *ps_seq_repeat(PsSeq *s, int64_t n) {
    int64_t i;
    PsSeq *out;
    if (n <= 0) {
        return ps_seq_new(s->elem, 0);
    }
    out = ps_seq_new(s->elem, s->len * n);
    for (i = 0; i < n; i++) {
        if (s->len > 0) {
            memcpy(ps_cell(out, i * s->len), s->data, (size_t)s->len * (size_t)s->elem);
        }
    }
    return out;
}

void ps_seq_extend(PsSeq *dst, PsSeq *src) {
    int64_t i;
    if (dst->elem != src->elem) {
        ps_raise_cstr(PS_VALUE, "list extend element size mismatch");
    }
    for (i = 0; i < src->len; i++) {
        ps_seq_push(dst, ps_cell(src, i));
    }
}

void ps_seq_insert(PsSeq *s, int64_t i, void *src) {
    if (i < 0) {
        i += s->len;
        if (i < 0) {
            i = 0;
        }
    }
    if (i > s->len) {
        i = s->len;
    }
    ps_seq_gap(s);
    memmove(ps_cell(s, i + 1), ps_cell(s, i), (size_t)(s->len - i) * (size_t)s->elem);
    memcpy(ps_cell(s, i), src, (size_t)s->elem);
    s->len += 1;
    ps_seq_index_invalidate(s);
}

void ps_seq_reverse(PsSeq *s) {
    char *tmp = malloc((size_t)s->elem);
    int64_t i;
    if (tmp == NULL) {
        abort();
    }
    for (i = 0; i < s->len / 2; i++) {
        char *a = ps_cell(s, i);
        char *b = ps_cell(s, s->len - 1 - i);
        memcpy(tmp, a, (size_t)s->elem);
        memcpy(a, b, (size_t)s->elem);
        memcpy(b, tmp, (size_t)s->elem);
    }
    free(tmp);
    ps_seq_index_invalidate(s);
}

static int ps_cmp_i64(const void *a, const void *b) {
    int64_t x = *(const int64_t *)a, y = *(const int64_t *)b;
    return x < y ? -1 : x > y ? 1 : 0;
}

static int ps_cmp_f64(const void *a, const void *b) {
    double x = *(const double *)a, y = *(const double *)b;
    if (x != x || y != y) {
        return 0;
    }
    return x < y ? -1 : x > y ? 1 : 0;
}

static int ps_cmp_buf(const void *a, const void *b) {
    const PsBuf *x = a, *y = b;
    int64_t n = x->len < y->len ? x->len : y->len;
    int c = n > 0 ? memcmp(x->data, y->data, (size_t)n) : 0;
    if (c != 0) {
        return c;
    }
    return x->len < y->len ? -1 : x->len > y->len ? 1 : 0;
}

void ps_seq_sort_i64(PsSeq *s, int64_t desc) {
    qsort(s->data, (size_t)s->len, (size_t)s->elem, ps_cmp_i64);
    if (desc) {
        ps_seq_reverse(s);
    }
    ps_seq_index_invalidate(s);
}

void ps_seq_sort_f64(PsSeq *s, int64_t desc) {
    qsort(s->data, (size_t)s->len, (size_t)s->elem, ps_cmp_f64);
    if (desc) {
        ps_seq_reverse(s);
    }
    ps_seq_index_invalidate(s);
}

void ps_seq_sort_buf(PsSeq *s, int64_t desc) {
    qsort(s->data, (size_t)s->len, (size_t)s->elem, ps_cmp_buf);
    if (desc) {
        ps_seq_reverse(s);
    }
    ps_seq_index_invalidate(s);
}

int64_t ps_seq_index_i64(PsSeq *s, int64_t needle) {
    int64_t i;
    for (i = 0; i < s->len; i++) {
        if (*(int64_t *)ps_cell(s, i) == needle) {
            return i;
        }
    }
    ps_raise_code(PS_VALUE);
}

int64_t ps_seq_index_f64(PsSeq *s, double needle) {
    int64_t i;
    for (i = 0; i < s->len; i++) {
        if (*(double *)ps_cell(s, i) == needle) {
            return i;
        }
    }
    ps_raise_code(PS_VALUE);
}

int64_t ps_seq_index_buf(PsSeq *s, int64_t n, char *p) {
    int64_t i;
    for (i = 0; i < s->len; i++) {
        PsBuf *cell = (PsBuf *)ps_cell(s, i);
        if (cell->len == n && (n == 0 || memcmp(cell->data, p, (size_t)n) == 0)) {
            return i;
        }
    }
    ps_raise_code(PS_VALUE);
}

int64_t ps_seq_count_i64(PsSeq *s, int64_t needle) {
    int64_t i, hits = 0;
    for (i = 0; i < s->len; i++) {
        if (*(int64_t *)ps_cell(s, i) == needle) {
            hits++;
        }
    }
    return hits;
}

int64_t ps_seq_count_f64(PsSeq *s, double needle) {
    int64_t i, hits = 0;
    for (i = 0; i < s->len; i++) {
        if (*(double *)ps_cell(s, i) == needle) {
            hits++;
        }
    }
    return hits;
}

int64_t ps_seq_count_buf(PsSeq *s, int64_t n, char *p) {
    int64_t i, hits = 0;
    for (i = 0; i < s->len; i++) {
        PsBuf *cell = (PsBuf *)ps_cell(s, i);
        if (cell->len == n && (n == 0 || memcmp(cell->data, p, (size_t)n) == 0)) {
            hits++;
        }
    }
    return hits;
}

static void ps_seq_drop(PsSeq *s, int64_t i) {
    memmove(ps_cell(s, i), ps_cell(s, i + 1), (size_t)(s->len - i - 1) * (size_t)s->elem);
    s->len -= 1;
    ps_seq_index_invalidate(s);
}

void ps_seq_remove_i64(PsSeq *s, int64_t needle) {
    ps_seq_drop(s, ps_seq_index_i64(s, needle));
}

void ps_seq_remove_f64(PsSeq *s, double needle) {
    ps_seq_drop(s, ps_seq_index_f64(s, needle));
}

void ps_seq_remove_buf(PsSeq *s, int64_t n, char *p) {
    ps_seq_drop(s, ps_seq_index_buf(s, n, p));
}

/* dict core (R1) */

void ps_map_del(PsMap *m, void *key) {
    /* The lookup runs in ps_rt.c over the open-addressing index; the dead
       row keeps its place in the dense arrays, so the iteration order of the
       survivors is untouched and the delete stays O(1) amortized. */
    if (ps_map_delete(m, key) < 0) {
        ps_raise_code(PS_KEY);
    }
}

void ps_map_clear(PsMap *m) { ps_map_reset(m); }

void ps_map_update(PsMap *dst, PsMap *src) {
    int64_t i;
    if (dst->key_kind != src->key_kind || dst->key_size != src->key_size || dst->val_size != src->val_size) {
        ps_raise_cstr(PS_VALUE, "dict update layout mismatch");
    }
    char *key = malloc((size_t)src->key_size);
    char *val = malloc((size_t)src->val_size);
    if (key == NULL || val == NULL) {
        abort();
    }
    for (i = 0; i < src->len; i++) {
        ps_map_key_at(src, i, key);
        ps_map_val_at(src, i, val);
        ps_map_put(dst, key, val);
    }
    free(key);
    free(val);
}

PsSeq *ps_dict_values(PsMap *m) {
    PsSeq *seq = ps_seq_new(m->val_size, m->len);
    for (int64_t i = 0; i < m->len; i++) {
        ps_map_val_at(m, i, (char *)ps_cell(seq, i));
    }
    return seq;
}

/* seq of {key, val} tuple objects: each cell is a pointer to a heap tuple
   (key at offset 0, val at voff, object size csize), matching the pointer
   layout ps_re_findall_groups uses for list[tuple]. */
PsSeq *ps_dict_items(PsMap *m, int64_t voff, int64_t csize) {
    PsSeq *seq = ps_seq_new((int64_t)sizeof(void *), m->len);
    for (int64_t i = 0; i < m->len; i++) {
        char *tup = malloc((size_t)csize);
        if (tup == NULL) {
            abort();
        }
        ps_map_key_at(m, i, tup);
        ps_map_val_at(m, i, tup + voff);
        *(void **)ps_cell(seq, i) = tup;
    }
    return seq;
}

/* str tier-2 (R3): bytewise ASCII semantics */

static int ps_ws(unsigned char c) {
    return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\v' || c == '\f' || c == 0x1c ||
           c == 0x1d || c == 0x1e || c == 0x1f || c == 0x85 || c == 0xa0;
}

static int ps_isdig(unsigned char c) { return c >= '0' && c <= '9'; }
static int ps_islo(unsigned char c) { return (c >= 'a' && c <= 'z') || (c >= 0xe0 && c <= 0xfe && c != 0xf7); }
static int ps_isup(unsigned char c) { return (c >= 'A' && c <= 'Z') || (c >= 0xc0 && c <= 0xde && c != 0xd7); }
static int ps_isalpha2(unsigned char c) { return ps_islo(c) || ps_isup(c); }
static int ps_isalnum2(unsigned char c) { return ps_isalpha2(c) || ps_isdig(c); }

int64_t ps_str_count(int64_t n, const char *p, int64_t sn, const char *sp) {
    int64_t i = 0, hits = 0;
    if (sn == 0) {
        return n + 1;
    }
    while (i + sn <= n) {
        if (memcmp(p + i, sp, (size_t)sn) == 0) {
            hits++;
            i += sn;
        } else {
            i++;
        }
    }
    return hits;
}

int64_t ps_str_index(int64_t n, const char *p, int64_t sn, const char *sp) {
    int64_t i = 0;
    while (i + sn <= n) {
        if (memcmp(p + i, sp, (size_t)sn) == 0) {
            /* UTF-8 is self-synchronizing, so a byte match between valid
               strings can only land on a code-point boundary; the returned
               index is the code-point index. */
            return ps_str_nchars(i, p);
        }
        i += ps_utf8_len((unsigned char)p[i]);
    }
    ps_raise_code(PS_VALUE);
}

int64_t ps_str_rfind(int64_t n, const char *p, int64_t sn, const char *sp) {
    int64_t i;
    if (sn == 0) {
        return ps_str_nchars(n, p);
    }
    for (i = n - sn; i >= 0; i--) {
        if (memcmp(p + i, sp, (size_t)sn) == 0) {
            return ps_str_nchars(i, p);
        }
    }
    return -1;
}


/* word scanner: fills lo/hi with word bounds. ascii=1 restricts whitespace
   to the six ASCII bytes (bytes.split); ascii=0 adds the str whitespace set. */
static int64_t ps_scan_words(int64_t n, const char *p, int64_t *lo, int64_t *hi, int ascii) {
    int64_t i = 0, count = 0;
    while (i < n) {
        int64_t j;
        unsigned char c = (unsigned char)p[i];
        int space = ascii ? (c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\v' || c == '\f')
                          : ps_ws(c);
        if (space) {
            i++;
            continue;
        }
        j = i + 1;
        while (j < n) {
            unsigned char d = (unsigned char)p[j];
            int gap = ascii ? (d == ' ' || d == '\t' || d == '\n' || d == '\r' || d == '\v' || d == '\f')
                            : ps_ws(d);
            if (gap) {
                break;
            }
            j++;
        }
        lo[count] = i;
        hi[count] = j;
        count++;
        i = j;
    }
    return count;
}

/* generic split driver. mode 0 = separator (str/bytes same), 1 = str
   whitespace, 2 = bytes whitespace. right = rsplit. maxsplit < 0 unlimited. */
PsSeq *ps_str_split_m(
    int64_t n, const char *p, int64_t sn, const char *sp, int64_t mode, int64_t maxsplit, int64_t right
) {
    PsSeq *seq;
    if (mode == 0 && sn == 0) {
        ps_raise_cstr(PS_VALUE, "empty separator");
    }
    seq = ps_seq_new(sizeof(PsBuf), 0);
    if (mode == 0) {
        /* separator mode */
        if (maxsplit < 0 || right == 0) {
            int64_t i = 0, left = maxsplit < 0 ? INT64_MAX : maxsplit;
            while (i <= n) {
                int64_t j = i;
                int found = 0;
                if (left == 0) {
                    ps_push_view(seq, p + i, n - i);
                    break;
                }
                while (j + sn <= n) {
                    if (memcmp(p + j, sp, (size_t)sn) == 0) {
                        found = 1;
                        break;
                    }
                    j++;
                }
                if (!found) {
                    ps_push_view(seq, p + i, n - i);
                    break;
                }
                ps_push_view(seq, p + i, j - i);
                left--;
                i = j + sn;
                if (i == n) {
                    ps_push_view(seq, p + n, 0);
                    break;
                }
            }
            return seq;
        }
        /* rsplit with maxsplit: collect match starts, then emit right pieces. */
        {
            int64_t cap = n / (sn > 0 ? sn : 1) + 2;
            int64_t *cuts = malloc((size_t)cap * sizeof(int64_t));
            int64_t count = 0, i = 0, k;
            if (cuts == NULL) {
                abort();
            }
            while (i + sn <= n) {
                if (memcmp(p + i, sp, (size_t)sn) == 0) {
                    cuts[count++] = i;
                    i += sn;
                } else {
                    i++;
                }
            }
            /* head ends at cuts[count - maxsplit]; pieces follow each cut. */
            {
                int64_t head_end = count > maxsplit ? cuts[count - maxsplit] : -1;
                int64_t first = count > maxsplit ? count - maxsplit : 0;
                if (head_end >= 0) {
                    ps_push_view(seq, p, head_end);
                }
                for (k = first; k < count; k++) {
                    int64_t start = cuts[k] + sn;
                    int64_t end = k + 1 < count ? cuts[k + 1] : n;
                    ps_push_view(seq, p + start, end - start);
                }
            }
            free(cuts);
            return seq;
        }
    }
    /* whitespace modes */
    {
        int64_t cap = n / 2 + 2;
        int64_t *lo = malloc((size_t)cap * sizeof(int64_t));
        int64_t *hi = malloc((size_t)cap * sizeof(int64_t));
        int64_t count, w;
        if (lo == NULL || hi == NULL) {
            abort();
        }
        count = ps_scan_words(n, p, lo, hi, mode == 2);
        if (maxsplit < 0 || count <= maxsplit || maxsplit == 0) {
            if (maxsplit == 0 && !right && n > 0) {
                /* str.split(None, 0): only leading whitespace is stripped */
                int64_t s = 0;
                while (s < n) {
                    unsigned char c = (unsigned char)p[s];
                    int space = mode == 2
                                    ? (c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\v' || c == '\f')
                                    : ps_ws(c);
                    if (!space) {
                        break;
                    }
                    s++;
                }
                ps_push_view(seq, p + s, n - s);
            } else if (maxsplit == 0 && right) {
                /* rsplit(None, 0): only trailing whitespace is stripped */
                int64_t e = n;
                while (e > 0) {
                    unsigned char c = (unsigned char)p[e - 1];
                    int space = mode == 2
                                    ? (c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\v' || c == '\f')
                                    : ps_ws(c);
                    if (!space) {
                        break;
                    }
                    e--;
                }
                ps_push_view(seq, p, e);
            } else {
                for (w = 0; w < count; w++) {
                    ps_push_view(seq, p + lo[w], hi[w] - lo[w]);
                }
            }
        } else if (!right) {
            /* split: first maxsplit words, then the rest verbatim minus
               surrounding whitespace. */
            int64_t s, e;
            for (w = 0; w < maxsplit; w++) {
                ps_push_view(seq, p + lo[w], hi[w] - lo[w]);
            }
            s = hi[maxsplit - 1];
            e = n;
            while (s < e && ps_ws((unsigned char)p[s])) {
                s++;
            }
            while (e > s && ps_ws((unsigned char)p[e - 1])) {
                e--;
            }
            ps_push_view(seq, p + s, e - s);
        } else {
            /* rsplit: head verbatim minus surrounding ws + last maxsplit words */
            int64_t e = lo[count - maxsplit];
            int64_t s = 0;
            while (e > s && ps_ws((unsigned char)p[e - 1])) {
                e--;
            }
            while (s < e && ps_ws((unsigned char)p[s])) {
                s++;
            }
            ps_push_view(seq, p + s, e - s);
            for (w = count - maxsplit; w < count; w++) {
                ps_push_view(seq, p + lo[w], hi[w] - lo[w]);
            }
        }
        free(lo);
        free(hi);
        return seq;
    }
}

/* splitlines: \n \r \r\n \v \f \x1c-\x1e \x85; keepends flag. */
PsSeq *ps_str_splitlines(int64_t n, const char *p, int64_t keepends) {
    PsSeq *seq = ps_seq_new(sizeof(PsBuf), 0);
    int64_t i = 0, start = 0;
    while (i < n) {
        unsigned char c = (unsigned char)p[i];
        int isbreak = c == '\n' || c == '\r' || c == '\v' || c == '\f' || (c >= 0x1c && c <= 0x1e) || c == 0x85;
        int64_t end;
        if (!isbreak) {
            i++;
            continue;
        }
        end = i;
        if (c == '\r' && i + 1 < n && p[i + 1] == '\n') {
            i += 2;
        } else {
            i++;
        }
        if (keepends) {
            ps_push_view(seq, p + start, i - start);
        } else {
            ps_push_view(seq, p + start, end - start);
        }
        start = i;
    }
    if (start < n) {
        ps_push_view(seq, p + start, n - start);
    }
    return seq;
}

/* predicates: empty -> 0. */
int64_t ps_str_isdigit(int64_t n, const char *p) {
    int64_t i;
    if (n == 0) {
        return 0;
    }
    for (i = 0; i < n; i++) {
        if (!ps_isdig((unsigned char)p[i])) {
            return 0;
        }
    }
    return 1;
}

int64_t ps_str_isalpha(int64_t n, const char *p) {
    int64_t i;
    if (n == 0) {
        return 0;
    }
    for (i = 0; i < n; i++) {
        if (!ps_isalpha2((unsigned char)p[i])) {
            return 0;
        }
    }
    return 1;
}

int64_t ps_str_isalnum(int64_t n, const char *p) {
    int64_t i;
    if (n == 0) {
        return 0;
    }
    for (i = 0; i < n; i++) {
        if (!ps_isalnum2((unsigned char)p[i])) {
            return 0;
        }
    }
    return 1;
}

int64_t ps_str_isspace(int64_t n, const char *p) {
    int64_t i;
    if (n == 0) {
        return 0;
    }
    for (i = 0; i < n; i++) {
        if (!ps_ws((unsigned char)p[i])) {
            return 0;
        }
    }
    return 1;
}

int64_t ps_str_isupper(int64_t n, const char *p) {
    int64_t i, any = 0;
    for (i = 0; i < n; i++) {
        unsigned char c = (unsigned char)p[i];
        if (ps_islo(c)) {
            return 0;
        }
        if (ps_isup(c)) {
            any = 1;
        }
    }
    return any;
}

int64_t ps_str_islower(int64_t n, const char *p) {
    int64_t i, any = 0;
    for (i = 0; i < n; i++) {
        unsigned char c = (unsigned char)p[i];
        if (ps_isup(c)) {
            return 0;
        }
        if (ps_islo(c)) {
            any = 1;
        }
    }
    return any;
}

/* The case family moved to ps_rt.c with the code-point model (S2); the
   bytewise helpers here are gone. The predicates below keep the ASCII
   Latin-1 tables (S3 owns the Unicode categories). */

void ps_str_zfill(int64_t n, const char *p, int64_t width, PsBuf *out) {
    int64_t pad, s = 0, chars = ps_str_nchars(n, p);
    char *q;
    if (width <= chars) {
        ps_buf_copy(out, p, n);
        return;
    }
    pad = width - chars;
    q = malloc((size_t)(n + pad) + 1);
    if (q == NULL) {
        abort();
    }
    if (n > 0 && (p[0] == '+' || p[0] == '-')) {
        q[0] = p[0];
        s = 1;
    }
    memset(q + s, '0', (size_t)pad);
    memcpy(q + s + pad, p + s, (size_t)(n - s));
    out->len = n + pad;
    out->data = q;
}

/* pad side: 0 = ljust, 1 = rjust, 2 = center. Width counts code points;
   the fill copies byte-wise so a multi-byte fill extends the buffer. */
void ps_str_justify(int64_t n, const char *p, int64_t width, int64_t fn, const char *fc, int64_t side, PsBuf *out) {
    int64_t chars = ps_str_nchars(n, p);
    if (width <= chars) {
        ps_buf_copy(out, p, n);
        return;
    }
    int64_t pad = width - chars;
    int64_t left = side == 0 ? 0 : side == 1 ? pad : pad / 2 + (pad & width & 1);
    if (fn <= 0) {
        fn = 1;
        fc = " ";
    }
    int64_t padb = pad * fn;
    int64_t leftb = left * fn;
    char *q = malloc((size_t)(n + padb) + 1);
    if (q == NULL) {
        abort();
    }
    /* The fill restarts at each side: the left run from the first fill
       byte, the right run again from the first fill byte (CPython does
       not continue the pattern across the text). */
    for (int64_t i = 0; i < leftb; i++) {
        q[i] = fc[i % fn];
    }
    memcpy(q + leftb, p, (size_t)n);
    for (int64_t i = leftb + n; i < n + padb; i++) {
        q[i] = fc[(i - leftb - n) % fn];
    }
    out->len = n + padb;
    out->data = q;
}

void ps_str_removeprefix(int64_t n, const char *p, int64_t sn, const char *sp, PsBuf *out) {
    if (sn <= n && memcmp(p, sp, (size_t)sn) == 0) {
        ps_buf_copy(out, p + sn, n - sn);
        return;
    }
    ps_buf_copy(out, p, n);
}

void ps_str_removesuffix(int64_t n, const char *p, int64_t sn, const char *sp, PsBuf *out) {
    if (sn <= n && sn > 0 && memcmp(p + n - sn, sp, (size_t)sn) == 0) {
        ps_buf_copy(out, p, n - sn);
        return;
    }
    if (sn == 0) {
        ps_buf_copy(out, p, n);
        return;
    }
    ps_buf_copy(out, p, n);
}

/* expandtabs: the tab stops count code points, the bytes copy verbatim. */
void ps_str_expandtabs(int64_t n, const char *p, int64_t tabsize, PsBuf *out) {
    int64_t i = 0, col = 0, total = 0;
    char *q, *w;
    while (i < n) {
        unsigned char c = (unsigned char)p[i];
        if (c == '\t') {
            int64_t add = tabsize > 0 ? tabsize - col % tabsize : 0;
            total += add;
            col += add;
            i++;
        } else {
            int64_t w2 = ps_utf8_len(c);
            total += w2;
            col += c == '\n' || c == '\r' ? -col : 1;
            i += w2;
        }
    }
    q = malloc((size_t)total + 1);
    if (q == NULL) {
        abort();
    }
    w = q;
    i = 0;
    col = 0;
    while (i < n) {
        unsigned char c = (unsigned char)p[i];
        if (c == '\t') {
            int64_t add = tabsize > 0 ? tabsize - col % tabsize : 0;
            memset(w, ' ', (size_t)add);
            w += add;
            col += add;
            i++;
        } else {
            int64_t w2 = ps_utf8_len(c);
            memcpy(w, p + i, (size_t)w2);
            w += w2;
            col = c == '\n' || c == '\r' ? 0 : col + 1;
            i += w2;
        }
    }
    out->len = total;
    out->data = q;
}

/* partition: writes three PsBufs into out (16 bytes each, contiguous).
   right = rpartition. Empty sep raises ValueError. */
void ps_str_partition(int64_t n, const char *p, int64_t sn, const char *sp, int64_t right, PsBuf *out) {
    int64_t at = -1, i;
    if (sn == 0) {
        ps_raise_cstr(PS_VALUE, "empty separator");
    }
    if (!right) {
        for (i = 0; i + sn <= n; i++) {
            if (memcmp(p + i, sp, (size_t)sn) == 0) {
                at = i;
                break;
            }
        }
    } else {
        for (i = n - sn; i >= 0; i--) {
            if (memcmp(p + i, sp, (size_t)sn) == 0) {
                at = i;
                break;
            }
        }
    }
    if (at < 0) {
        if (!right) {
            ps_buf_copy(&out[0], p, n);
            ps_buf_copy(&out[1], "", 0);
            ps_buf_copy(&out[2], "", 0);
        } else {
            ps_buf_copy(&out[0], "", 0);
            ps_buf_copy(&out[1], "", 0);
            ps_buf_copy(&out[2], p, n);
        }
        return;
    }
    ps_buf_copy(&out[0], p, at);
    ps_buf_copy(&out[1], sp, sn);
    ps_buf_copy(&out[2], p + at + sn, n - at - sn);
}

/* R4/R5: repr of a bytes value (b'...' with escapes) and float(str). */
void ps_repr_bytes(int64_t n, const char *p, PsBuf *out) {
    /* worst case: each byte -> \xNN (4) plus b'' quotes */
    char *q = malloc((size_t)n * 4 + 4);
    char *w = q;
    int64_t i;
    int quote = '\'';
    if (q == NULL) {
        abort();
    }
    for (i = 0; i < n; i++) {
        if (p[i] == '"' && memchr(p, '\'', (size_t)n) == NULL) {
            quote = '"';
        }
    }
    *w++ = 'b';
    *w++ = (char)quote;
    for (i = 0; i < n; i++) {
        unsigned char c = (unsigned char)p[i];
        if (c == (unsigned char)quote || c == '\\') {
            *w++ = '\\';
            *w++ = (char)c;
        } else if (c == '\n') {
            *w++ = '\\';
            *w++ = 'n';
        } else if (c == '\r') {
            *w++ = '\\';
            *w++ = 'r';
        } else if (c == '\t') {
            *w++ = '\\';
            *w++ = 't';
        } else if (c >= 0x20 && c < 0x7f) {
            *w++ = (char)c;
        } else {
            static const char hex[] = "0123456789abcdef";
            *w++ = '\\';
            *w++ = 'x';
            *w++ = hex[c >> 4];
            *w++ = hex[c & 15];
        }
    }
    *w++ = (char)quote;
    out->len = w - q;
    out->data = q;
}

/* strict float(str): [ws] sign (digits [.digits] [. digits] | inf | nan)
   [e sign digits] [ws]; underscores and trailing junk -> ValueError. */
double ps_float_from_str(int64_t n, const char *p) {
    int64_t i = 0, e = n;
    int sign = 1, any = 0, anyfrac = 0, anyexp = 0;
    double val = 0.0, frac = 0.0, scale = 0.1;
    int64_t exp = 0;
    int expsign = 1;
    while (i < e && ps_ws((unsigned char)p[i])) {
        i++;
    }
    while (e > i && ps_ws((unsigned char)p[e - 1])) {
        e--;
    }
    if (i < e && (p[i] == '+' || p[i] == '-')) {
        if (p[i] == '-') {
            sign = -1;
        }
        i++;
    }
    if (i + 3 <= e && (p[i] == 'i' || p[i] == 'I')) {
        /* inf / infinity */
        if ((p[i + 1] == 'n' || p[i + 1] == 'N') && (p[i + 2] == 'f' || p[i + 2] == 'F')) {
            i += 3;
            if (i + 5 <= e && (p[i] == 'i' || p[i] == 'I')) {
                i += 5;
            }
            if (i != e) {
                ps_raise_code(PS_VALUE);
            }
            return sign > 0 ? 1.0 / 0.0 : -1.0 / 0.0;
        }
    }
    if (i + 3 <= e && (p[i] == 'n' || p[i] == 'N') && (p[i + 1] == 'a' || p[i + 1] == 'A') &&
        (p[i + 2] == 'n' || p[i + 2] == 'N')) {
        i += 3;
        if (i != e) {
            ps_raise_code(PS_VALUE);
        }
        return 0.0 / 0.0;
    }
    while (i < e && ps_isdig((unsigned char)p[i])) {
        val = val * 10.0 + (double)(p[i] - '0');
        i++;
        any = 1;
    }
    if (i < e && p[i] == '.') {
        i++;
        while (i < e && ps_isdig((unsigned char)p[i])) {
            frac += scale * (double)(p[i] - '0');
            scale /= 10.0;
            i++;
            anyfrac = 1;
        }
    }
    if (!any && !anyfrac) {
        ps_raise_code(PS_VALUE);
    }
    val += frac;
    if (i < e && (p[i] == 'e' || p[i] == 'E')) {
        i++;
        if (i < e && (p[i] == '+' || p[i] == '-')) {
            if (p[i] == '-') {
                expsign = -1;
            }
            i++;
        }
        while (i < e && ps_isdig((unsigned char)p[i])) {
            exp = exp * 10 + (p[i] - '0');
            i++;
            anyexp = 1;
        }
        if (!anyexp) {
            ps_raise_code(PS_VALUE);
        }
    }
    if (i != e) {
        ps_raise_code(PS_VALUE);
    }
    if (exp != 0) {
        double mult = 1.0;
        int64_t k;
        for (k = 0; k < exp && k < 400; k++) {
            mult *= 10.0;
        }
        if (expsign < 0) {
            val /= mult;
        } else {
            val *= mult;
        }
    }
    return sign > 0 ? val : -val;
}

/* by-kind list.insert/pop wrappers (generic _c_pieces arg marshaling) */
void ps_seq_insert_i64(PsSeq *s, int64_t i, int64_t v) { ps_seq_insert(s, i, &v); }
void ps_seq_insert_f64(PsSeq *s, int64_t i, double v) { ps_seq_insert(s, i, &v); }
void ps_seq_insert_buf(PsSeq *s, int64_t i, int64_t n, char *p) {
    PsBuf b;
    b.len = n;
    b.data = p;
    ps_seq_insert(s, i, &b);
}

static int64_t ps_pop_index(PsSeq *s, int64_t i) {
    if (i < 0) {
        i += s->len;
    }
    if (i < 0 || i >= s->len) {
        ps_raise_code(PS_INDEX);
    }
    return i;
}

int64_t ps_seq_pop_i64(PsSeq *s, int64_t i) {
    i = ps_pop_index(s, i);
    int64_t v = *(int64_t *)ps_cell(s, i);
    ps_seq_drop(s, i);
    return v;
}

double ps_seq_pop_f64(PsSeq *s, int64_t i) {
    i = ps_pop_index(s, i);
    double v = *(double *)ps_cell(s, i);
    ps_seq_drop(s, i);
    return v;
}

void ps_seq_pop_buf(PsSeq *s, int64_t i, PsBuf *out) {
    i = ps_pop_index(s, i);
    *out = *(PsBuf *)ps_cell(s, i);
    ps_seq_drop(s, i);
}

/* `del xs[i]` drops one slot with the same index wrap and IndexError as pop. */
void ps_seq_del(PsSeq *s, int64_t i) { ps_seq_drop(s, ps_pop_index(s, i)); }
