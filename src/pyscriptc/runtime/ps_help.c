/* Object helpers that are easier in C than in the LLVM text: abs, sum,
sorted, ord, chr, round, int(bytes), warnings.warn, and lexicographic i64
compare.
*/

#include "ps_rt.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int32_t ps_i64_lex(const int64_t *a, const int64_t *b, int64_t n) {
    int64_t i;
    for (i = 0; i < n; i++) {
        if (a[i] < b[i]) {
            return -1;
        }
        if (a[i] > b[i]) {
            return 1;
        }
    }
    return 0;
}

int64_t ps_abs_i64(int64_t x) {
    if (x == INT64_MIN) {
        __builtin_trap();
    }
    return x < 0 ? -x : x;
}

double ps_abs_f64(double x) { return fabs(x); }

int64_t ps_sum_i64(PsSeq *s) {
    int64_t acc = 0;
    int64_t i;
    for (i = 0; i < s->len; i++) {
        int64_t item;
        memcpy(&item, (char *)s->data + (size_t)i * (size_t)s->elem, sizeof item);
        if (__builtin_add_overflow(acc, item, &acc)) {
            __builtin_trap();
        }
    }
    return acc;
}

double ps_sum_f64(PsSeq *s) {
    double acc = 0;
    int64_t i;
    for (i = 0; i < s->len; i++) {
        double item;
        memcpy(&item, (char *)s->data + (size_t)i * (size_t)s->elem, sizeof item);
        acc += item;
    }
    return acc;
}

static int ps_cmp_i64(const void *a, const void *b) {
    int64_t xa;
    int64_t xb;
    memcpy(&xa, a, sizeof xa);
    memcpy(&xb, b, sizeof xb);
    if (xa < xb) {
        return -1;
    }
    if (xa > xb) {
        return 1;
    }
    return 0;
}

static int ps_cmp_f64(const void *a, const void *b) {
    double xa;
    double xb;
    memcpy(&xa, a, sizeof xa);
    memcpy(&xb, b, sizeof xb);
    if (isnan(xa) && isnan(xb)) {
        return 0;
    }
    if (isnan(xa)) {
        return 1;
    }
    if (isnan(xb)) {
        return -1;
    }
    if (xa < xb) {
        return -1;
    }
    if (xa > xb) {
        return 1;
    }
    return 0;
}

int32_t ps_str_cmp(int64_t an, char *ap, int64_t bn, char *bp);

static int ps_cmp_buf(const void *a, const void *b) {
    const PsBuf *xa = a;
    const PsBuf *xb = b;
    return ps_str_cmp(xa->len, xa->data, xb->len, xb->data);
}

static PsSeq *ps_sorted(PsSeq *src, int (*cmp)(const void *, const void *)) {
    PsSeq *out = ps_seq_new(src->elem, src->len);
    if (src->len > 0) {
        memcpy(out->data, src->data, (size_t)src->len * (size_t)src->elem);
        qsort(out->data, (size_t)src->len, (size_t)src->elem, cmp);
    }
    return out;
}

PsSeq *ps_sorted_i64(PsSeq *s) { return ps_sorted(s, ps_cmp_i64); }

PsSeq *ps_sorted_f64(PsSeq *s) { return ps_sorted(s, ps_cmp_f64); }

PsSeq *ps_sorted_buf(PsSeq *s) { return ps_sorted(s, ps_cmp_buf); }

int64_t ps_ord(int64_t n, const char *s) {
    if (n != 1) {
        ps_raise_cstr(PS_VALUE, "ord() expected a character");
    }
    return (unsigned char)s[0];
}

/* int(bytes): base 10, CPython's surrounding-whitespace strip, optional
sign, every remaining byte a digit, no underscore subset, i64 range only. */
int64_t ps_int_from_bytes(int64_t n, const char *s) {
    int64_t i = 0;
    while (i < n && (s[i] == ' ' || s[i] == '\t' || s[i] == '\n' || s[i] == '\r' || s[i] == '\f' || s[i] == '\v')) {
        i++;
    }
    int neg = 0;
    if (i < n && (s[i] == '+' || s[i] == '-')) {
        neg = s[i] == '-';
        i++;
    }
    uint64_t mag = 0;
    int64_t end = n;
    while (end > i && (s[end - 1] == ' ' || s[end - 1] == '\t' || s[end - 1] == '\n' || s[end - 1] == '\r'
                       || s[end - 1] == '\f' || s[end - 1] == '\v')) {
        end--;
    }
    if (i >= end) {
        ps_raise_cstr(PS_VALUE, "invalid literal for int() with base 10");
    }
    for (; i < end; i++) {
        if (s[i] < '0' || s[i] > '9') {
            ps_raise_cstr(PS_VALUE, "invalid literal for int() with base 10");
        }
        if (mag > (UINT64_MAX - (uint64_t)(s[i] - '0')) / 10) {
            ps_raise_cstr(PS_VALUE, "int() literal does not fit in i64");
        }
        mag = mag * 10 + (uint64_t)(s[i] - '0');
    }
    if (!neg && mag > (uint64_t)INT64_MAX) {
        ps_raise_cstr(PS_VALUE, "int() literal does not fit in i64");
    }
    return neg ? -(int64_t)(mag - 1) - 1 : (int64_t)mag;
}

void ps_chr(int64_t c, PsBuf *out) {
    char buf[1];
    if (c < 0 || c > 127) {
        ps_raise_cstr(PS_VALUE, "chr is ASCII in this subset");
    }
    buf[0] = (char)c;
    ps_buf_copy(out, buf, 1);
}

static int64_t ps_half_even(double x) {
    double base;
    double frac;
    double rounded;
    if (!isfinite(x)) {
        ps_raise_cstr(PS_OVERFLOW, "cannot round this float");
    }
    base = floor(x);
    frac = x - base;
    if (frac > 0.5) {
        rounded = base + 1.0;
    } else if (frac < 0.5) {
        rounded = base;
    } else if (fmod(base, 2.0) == 0.0) {
        rounded = base;
    } else {
        rounded = base + 1.0;
    }
    if (rounded >= 9223372036854775808.0 || rounded < -9223372036854775808.0) {
        ps_raise_cstr(PS_OVERFLOW, "cannot round this float");
    }
    return (int64_t)rounded;
}

int64_t ps_round_i64(double x) { return ps_half_even(x); }

double ps_round_nd(double x, int64_t nd) {
    double scale;
    int64_t rounded;
    if (nd < 0 || nd > 8) {
        ps_raise_cstr(PS_VALUE, "round ndigits is out of the lowered range");
    }
    if (!isfinite(x)) {
        ps_raise_cstr(PS_VALUE, "cannot round this float");
    }
    scale = pow(10.0, (double)nd);
    rounded = ps_half_even(x * scale);
    return (double)rounded / scale;
}

void ps_warn(int64_t n, const char *s) {
    if (n < 0) {
        n = 0;
    }
    if (n > 1000000) {
        n = 1000000;
    }
    fprintf(stderr, "UserWarning: %.*s\n", (int)n, s);
}
