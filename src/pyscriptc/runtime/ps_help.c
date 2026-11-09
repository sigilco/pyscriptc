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

/* CPython's `sum` over floats is a Neumaier compensated sum (the `float_sum`
   path in CPython's bltinmodule.c): the low-order bits a plain fold loses ride
   in a compensation term flushed at the end, which is what makes
   `sum([0.1, 0.2, 0.3])` spell `0.6` and `sum([1e16, 1.0, -1e16])` come out
   `1.0`. */
double ps_sum_f64(PsSeq *s) {
    double acc = 0;
    double comp = 0;
    int64_t i;
    for (i = 0; i < s->len; i++) {
        double item;
        double total;
        memcpy(&item, (char *)s->data + (size_t)i * (size_t)s->elem, sizeof item);
        total = acc + item;
        if (fabs(acc) >= fabs(item)) {
            comp += (acc - total) + item;
        } else {
            comp += (item - total) + acc;
        }
        acc = total;
    }
    return acc + comp;
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
    /* The one code point of a 1-length str: decode its UTF-8 bytes. The
       argument check is on the code-point count (a non-continuation byte
       count of 1), not the byte length, so `ord("é")` takes the two-byte
       path and `ord("ab")` still raises. */
    int64_t chars = 0;
    for (int64_t i = 0; i < n; i++) {
        chars += ((unsigned char)s[i] & 0xC0) != 0x80;
    }
    if (chars != 1) {
        ps_raise_cstr(PS_VALUE, "ord() expected a character");
    }
    unsigned char c = (unsigned char)s[0];
    if (c < 0x80) {
        return c;
    }
    int w = ps_utf8_len(c);
    uint32_t cp;
    switch (w) {
    case 2:
        cp = (uint32_t)(c & 0x1F);
        break;
    case 3:
        cp = (uint32_t)(c & 0x0F);
        break;
    default:
        cp = (uint32_t)(c & 0x07);
        break;
    }
    for (int k = 1; k < w && k < n; k++) {
        cp = (cp << 6) | (uint32_t)((unsigned char)s[k] & 0x3F);
    }
    return (int64_t)cp;
}

/* int(bytes): base 10, CPython's surrounding-whitespace strip (ASCII space
   set plus the Unicode space code points; \x1c-\x1f are NOT stripped, they
   stay part of the literal, like CPython), optional sign, every remaining
   byte a digit or CPython's underscore digit separator, i64 range only. The
   invalid-literal message carries the repr'd literal like CPython 3.14:
   ": 'x'". */

/* CPython strips these code points as surrounding whitespace for int():
   the str.isspace() Unicode set minus the file-separator group \x1c-\x1f
   (Python's own b"' \t\n\r\x0b\x0c'" rule for bytes) plus \x85. Verified
   against CPython 3.14 by enumeration. */
static int ps_int_is_space(uint32_t cp) {
    switch (cp) {
    case 0x09:
    case 0x0A:
    case 0x0B:
    case 0x0C:
    case 0x0D:
    case 0x20:
    case 0x85:
    case 0xA0:
    case 0x1680:
    case 0x2000:
    case 0x2001:
    case 0x2002:
    case 0x2003:
    case 0x2004:
    case 0x2005:
    case 0x2006:
    case 0x2007:
    case 0x2008:
    case 0x2009:
    case 0x200A:
    case 0x2028:
    case 0x2029:
    case 0x202F:
    case 0x205F:
    case 0x3000:
        return 1;
    default:
        return 0;
    }
}

/* One code point starting at byte i: returns its width, or 0 past the end. */
static int ps_int_next_cp(int64_t n, const char *s, int64_t i, uint32_t *cp) {
    if (i >= n) {
        return 0;
    }
    unsigned char c = (unsigned char)s[i];
    int width = ps_utf8_len(c);
    if (i + width > n) {
        *cp = c;
        return 1;
    }
    *cp = c & (uint32_t)(0xFF >> (width + 1));
    for (int k = 1; k < width; k++) {
        *cp = (*cp << 6) | ((unsigned char)s[i + k] & 0x3F);
    }
    return width;
}

/* The invalid-literal message: "invalid literal for int() with base B: <repr>".

   CPython 3.14 appends the repr'd literal to the base-10 form, and the
   base form carries the same suffix with the caller's base. The repr is the
   runtime's own `ps_repr_str`, so the quoting and the escapes match
   CPython's `repr`. */
static void ps_int_bad_literal_msg(int64_t n, const char *s, int64_t base) {
    PsBuf lit;
    ps_repr_str(n, s, &lit);
    char prefix[64];
    int plen = snprintf(prefix, sizeof prefix, "invalid literal for int() with base %lld: ", (long long)base);
    if (plen < 0) {
        plen = 0;
    }
    size_t total = (size_t)plen + (size_t)lit.len;
    char *msg = malloc(total + 1);
    if (msg == NULL) {
        abort();
    }
    memcpy(msg, prefix, (size_t)plen);
    memcpy(msg + plen, lit.data, (size_t)lit.len);
    msg[total] = '\0';
    free(lit.data);
    ps_raise_exc(ps_exc_make(PS_VALUE, (int64_t)total, msg));
}

static void ps_int_bad_literal(int64_t n, const char *s) {
    ps_int_bad_literal_msg(n, s, 10);
}

int64_t ps_int_from_bytes(int64_t n, const char *s) {
    int64_t i = 0;
    for (;;) {
        uint32_t cp;
        int width = ps_int_next_cp(n, s, i, &cp);
        if (width == 0 || !ps_int_is_space(cp)) {
            break;
        }
        i += width;
    }
    int neg = 0;
    if (i < n && (s[i] == '+' || s[i] == '-')) {
        neg = s[i] == '-';
        i++;
    }
    uint64_t mag = 0;
    int64_t end = n;
    for (;;) {
        uint32_t cp;
        int64_t last = end - 1;
        if (last < i) {
            break;
        }
        /* Walk back over continuation bytes: start names the first byte. */
        int64_t start = last;
        while (start > i && ((unsigned char)s[start] & 0xC0) == 0x80) {
            start--;
        }
        ps_int_next_cp(n, s, start, &cp);
        if (!ps_int_is_space(cp)) {
            break;
        }
        end = start;
    }
    if (i >= end) {
        ps_int_bad_literal(n, s);
    }
    /* CPython's underscore digit separator: one '_' sits between two
       digits, never at an end and never doubled ('1_0' parses; '_1', '1_',
       '1__0' and '1_x' raise). `digits` guards the leading '_' and the
       all-separator literal. */
    int digits = 0;
    for (; i < end; i++) {
        char c = s[i];
        if (c == '_') {
            if (digits == 0 || i + 1 >= end || s[i + 1] == '_') {
                ps_int_bad_literal(n, s);
            }
            char nx = s[i + 1];
            if (nx < '0' || nx > '9') {
                ps_int_bad_literal(n, s);
            }
            continue;
        }
        if (c < '0' || c > '9') {
            ps_int_bad_literal(n, s);
        }
        digits++;
        if (mag > (UINT64_MAX - (uint64_t)(c - '0')) / 10) {
            ps_raise_cstr(PS_VALUE, "int() literal does not fit in i64");
        }
        mag = mag * 10 + (uint64_t)(c - '0');
    }
    if (digits == 0) {
        ps_int_bad_literal(n, s);
    }
    if (!neg && mag > (uint64_t)INT64_MAX) {
        ps_raise_cstr(PS_VALUE, "int() literal does not fit in i64");
    }
    return neg ? -(int64_t)(mag - 1) - 1 : (int64_t)mag;
}

/* int(s, base): CPython's literal read over the digit table, with the
   underscore digit-separator rule (single '_' between digits, none at
   either end, none next to a base prefix). Bases 2..36 only (the lowerer
   fences base 0 and out-of-range with a named code); a 0x, 0o or 0b prefix
   is allowed for its matching base, upper case spellings too, and the sign
   sits in front of the prefix. The surrounding-whitespace strip uses the
   Unicode space set, like the base-10 form, and every failure message
   carries the caller's base and the repr'd literal, like CPython 3.14. */
int64_t ps_int_from_base(int64_t n, const char *s, int64_t base) {
    if (base < 2 || base > 36) {
        ps_raise_cstr(PS_VALUE, "int() base must be >= 2 and <= 36");
    }
    /* CPython strips the surrounding whitespace with the Unicode space set
       (the same rule the base-10 form uses), not just ASCII: the strip walk
       decodes code points off the UTF-8 bytes, and a run that does not
       decode cleanly stays literal. */
    int64_t i = 0;
    for (;;) {
        uint32_t cp;
        int width = ps_int_next_cp(n, s, i, &cp);
        if (width == 0 || !ps_int_is_space(cp)) {
            break;
        }
        i += width;
    }
    int neg = 0;
    if (i < n && (s[i] == '+' || s[i] == '-')) {
        neg = s[i] == '-';
        i++;
    }
    if (n - i >= 2 && s[i] == '0') {
        char p = (char)(s[i + 1] | 0x20);
        char want = base == 16 ? 'x' : base == 8 ? 'o' : base == 2 ? 'b' : 0;
        /* Only the matching prefix is prefix; '0b1' under base 16 reads the
           b as a digit value. The prefix may be followed by one '_' ('0x_f'
           parses); skip the separator with the prefix letters. */
        if (want && p == want) {
            if (n - i >= 3 && s[i + 2] == '_') {
                if (i + 3 >= n) {
                    ps_int_bad_literal_msg(n, s, base);
                }
                i += 3;
            } else {
                i += 2;
            }
        }
    }
    uint64_t mag = 0;
    /* The trailing strip walks the same Unicode set. */
    int64_t end = n;
    for (;;) {
        uint32_t cp;
        int64_t last = end - 1;
        if (last < i) {
            break;
        }
        int64_t start = last;
        while (start > i && ((unsigned char)s[start] & 0xC0) == 0x80) {
            start--;
        }
        ps_int_next_cp(n, s, start, &cp);
        if (!ps_int_is_space(cp)) {
            break;
        }
        end = start;
    }
    /* Underscores: one separator sits between digits of this base, never at
       an end and never doubled: '1_0' parses, '1_', '_1', '1__0' and '1_x'
       raise, like CPython's parser. `digits` guards the leading '_' and the
       all-separator literal. Every failure carries the caller's base and the
       repr'd literal, like CPython 3.14. */
    int digits = 0;
    for (; i < end; i++) {
        int d;
        char c = s[i];
        if (c == '_') {
            int64_t j = i + 1;
            if (digits == 0 || j >= end || s[j] == '_') {
                ps_int_bad_literal_msg(n, s, base);
            }
            char nx = s[j];
            int nxd;
            if (nx >= '0' && nx <= '9') {
                nxd = nx - '0';
            } else if (nx >= 'a' && nx <= 'z') {
                nxd = nx - 'a' + 10;
            } else if (nx >= 'A' && nx <= 'Z') {
                nxd = nx - 'A' + 10;
            } else {
                ps_int_bad_literal_msg(n, s, base);
            }
            if (nxd >= base) {
                ps_int_bad_literal_msg(n, s, base);
            }
            continue;
        }
        digits++;
        if (c >= '0' && c <= '9') {
            d = c - '0';
        } else if (c >= 'a' && c <= 'z') {
            d = c - 'a' + 10;
        } else if (c >= 'A' && c <= 'Z') {
            d = c - 'A' + 10;
        } else {
            ps_int_bad_literal_msg(n, s, base);
        }
        if (d >= base) {
            ps_int_bad_literal_msg(n, s, base);
        }
        if (mag > (UINT64_MAX - (uint64_t)d) / (uint64_t)base) {
            ps_raise_cstr(PS_VALUE, "int() literal does not fit in i64");
        }
        mag = mag * (uint64_t)base + (uint64_t)d;
    }
    if (digits == 0) {
        ps_int_bad_literal_msg(n, s, base);
    }
    if (!neg && mag > (uint64_t)INT64_MAX) {
        ps_raise_cstr(PS_VALUE, "int() literal does not fit in i64");
    }
    return neg ? -(int64_t)(mag - 1) - 1 : (int64_t)mag;
}

void ps_chr(int64_t c, PsBuf *out) {
    /* Full-range chr: encode the code point as UTF-8, matching CPython for
       every scalar (0..0x10FFFF); lone surrogates raise, like CPython. */
    char buf[4];
    if (c < 0 || c > 0x10FFFF || (c >= 0xD800 && c <= 0xDFFF)) {
        ps_raise_cstr(PS_VALUE, "chr() arg not in range(0x110000)");
    }
    uint32_t cp = (uint32_t)c;
    int w;
    if (cp < 0x80) {
        buf[0] = (char)cp;
        w = 1;
    } else if (cp < 0x800) {
        buf[0] = (char)(0xC0 | (cp >> 6));
        buf[1] = (char)(0x80 | (cp & 0x3F));
        w = 2;
    } else if (cp < 0x10000) {
        buf[0] = (char)(0xE0 | (cp >> 12));
        buf[1] = (char)(0x80 | ((cp >> 6) & 0x3F));
        buf[2] = (char)(0x80 | (cp & 0x3F));
        w = 3;
    } else {
        buf[0] = (char)(0xF0 | (cp >> 18));
        buf[1] = (char)(0x80 | ((cp >> 12) & 0x3F));
        buf[2] = (char)(0x80 | ((cp >> 6) & 0x3F));
        buf[3] = (char)(0x80 | (cp & 0x3F));
        w = 4;
    }
    ps_buf_copy(out, buf, w);
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

/* CPython rounds the exact binary value of `x` (its `_Py_dg_dtoa` path), so
   `round(2.675, 2)` is `2.67`: the double nearest 2.675 is really
   2.67499999..., and scaling before rounding amplifies that representation
   error instead. The exact-decimal algorithm here reads the exact decimal
   expansion of the double from a 55-digit `%.*e` spelling (17 significant
   digits plus the exponent slack covers every nd in 0..8), cuts at the nd-th
   decimal place, and rounds the exact remainder half-to-even with a carry
   walk, which is CPython's answer for every in-range double. Bounds stay a
   runtime check: constant ndigits outside 0..8 fences at compile time, and a
   variable ndigits still needs the runtime. */
double ps_round_nd(double x, int64_t nd) {
    char tmp[96];
    char digits[96];
    char out[96];
    int exp10;
    int keep;
    int len;
    int drop;
    int nonzero;
    int carry;
    const char *mant;
    if (nd < 0 || nd > 8) {
        ps_raise_cstr(PS_VALUE, "round ndigits is out of the lowered range");
    }
    if (!isfinite(x)) {
        ps_raise_cstr(PS_VALUE, "cannot round this float");
    }
    if (nd == 0) {
        return (double)ps_half_even(x);
    }
    /* Exact mantissa: %.55e gives the full binary-to-decimal expansion,
       56 significant digits (one before the point, 55 after). */
    snprintf(tmp, sizeof tmp, "%.55e", x);
    mant = tmp[0] == '-' ? tmp + 1 : tmp;
    exp10 = 0;
    sscanf(strchr(tmp, 'e') + 1, "%d", &exp10);
    /* digits: the 56 mantissa digits with the '.' dropped. */
    digits[0] = mant[0];
    memcpy(digits + 1, mant + 2, 55);
    digits[56] = '\0';
    /* Index of the last kept digit: the 10^-nd place sits nd digits right
       of the 10^0 digit, which is mantissa index exp10. */
    keep = exp10 + (int)nd + 1;
    if (keep <= 0) {
        /* The cut sits at or left of the first digit. For magnitudes near
           1.0 (1e-05 at nd=2) scaling is exact while the mantissa is short;
           for deep subnormals-and-beyond magnitudes CPython rounds to its
           signed zero, so the sign of x is the answer. */
        if (x == 0.0) {
            return x;
        }
        if (x < 0.0) {
            if (-exp10 <= 16) {
                return -((double)ps_half_even(-x * pow(10.0, (double)nd))) / pow(10.0, (double)nd);
            }
            return -0.0;
        }
        if (-exp10 <= 16) {
            return (double)ps_half_even(x * pow(10.0, (double)nd)) / pow(10.0, (double)nd);
        }
        return 0.0;
    }
    if (keep >= 56) {
        return x; /* the cut sits beyond the exact expansion */
    }
    /* Half-to-even: round up on a remainder above half, or exactly half
       with an odd last kept digit. */
    nonzero = 0;
    for (int i = keep + 1; i < 56; i++) {
        if (digits[i] != '0') {
            nonzero = 1;
            break;
        }
    }
    drop = digits[keep] - '0';
    snprintf(out, sizeof out, "%.*s", keep, digits);
    carry = drop > 5 || (drop == 5 && (nonzero || (out[keep - 1] - '0') % 2 == 1));
    len = keep;
    while (carry && len > 0) {
        if (out[len - 1] == '9') {
            out[len - 1] = '0';
            len--;
        } else {
            out[len - 1] = (char)(out[len - 1] + 1);
            carry = 0;
        }
    }
    if (carry) {
        /* Every kept digit was a 9 (0.999 -> 1.0): the 9.99 -> 10.0 shift
           moved the point one place right. */
        memmove(out + 1, out, (size_t)keep);
        out[0] = '1';
        keep += 1;
        exp10 += 1;
    }
    /* Reassemble with the '.' at exp10+1 and the sign. */
    {
        char full[112];
        int at = 0;
        if (x < 0) {
            full[at++] = '-';
        }
        if (exp10 >= 0) {
            int whole = exp10 + 1;
            if (whole >= keep) {
                memcpy(full + at, out, (size_t)keep);
                at += keep;
                for (int i = 0; i < whole - keep; i++) {
                    full[at++] = '0';
                }
                full[at] = '\0';
            } else {
                memcpy(full + at, out, (size_t)whole);
                at += whole;
                full[at++] = '.';
                memcpy(full + at, out + whole, (size_t)(keep - whole));
                at += keep - whole;
                full[at] = '\0';
            }
        } else {
            full[at++] = '0';
            full[at++] = '.';
            for (int i = 0; i < -exp10 - 1; i++) {
                full[at++] = '0';
            }
            memcpy(full + at, out, (size_t)keep);
            at += keep;
            full[at] = '\0';
        }
        return strtod(full, NULL);
    }
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

/* R20 builtins: base spellings and min/max folds over a seq. */

static void ps_i64_base(int64_t x, int64_t base, const char *digits, const char *prefix, PsBuf *out) {
    char tmp[72];
    char *end = tmp + sizeof tmp;
    char *p = end;
    size_t plen = strlen(prefix);
    uint64_t mag = x < 0 ? (uint64_t)(-(x + 1)) + 1 : (uint64_t)x;
    if (mag == 0) {
        *--p = '0';
    }
    while (mag) {
        *--p = digits[mag % (uint64_t)base];
        mag /= (uint64_t)base;
    }
    p -= plen;
    memcpy(p, prefix, plen);
    if (x < 0) {
        *--p = '-';
    }
    ps_buf_copy(out, p, end - p);
}

void ps_hex_i64(int64_t x, PsBuf *out) { ps_i64_base(x, 16, "0123456789abcdef", "0x", out); }

void ps_oct_i64(int64_t x, PsBuf *out) { ps_i64_base(x, 8, "01234567", "0o", out); }

void ps_bin_i64(int64_t x, PsBuf *out) { ps_i64_base(x, 2, "01", "0b", out); }

static int64_t ps_minmax_i64(PsSeq *s, int64_t is_max) {
    int64_t best;
    int64_t i;
    memcpy(&best, s->data, sizeof best);
    for (i = 1; i < s->len; i++) {
        int64_t item;
        memcpy(&item, (char *)s->data + (size_t)i * (size_t)s->elem, sizeof item);
        if (is_max ? item > best : item < best) {
            best = item;
        }
    }
    return best;
}

int64_t ps_min_i64(PsSeq *s) {
    if (s->len == 0) {
        ps_raise_cstr(PS_VALUE, "min() iterable argument is empty");
    }
    return ps_minmax_i64(s, 0);
}

int64_t ps_max_i64(PsSeq *s) {
    if (s->len == 0) {
        ps_raise_cstr(PS_VALUE, "max() iterable argument is empty");
    }
    return ps_minmax_i64(s, 1);
}

static double ps_minmax_f64(PsSeq *s, int64_t is_max) {
    double best;
    int64_t i;
    memcpy(&best, s->data, sizeof best);
    for (i = 1; i < s->len; i++) {
        double item;
        memcpy(&item, (char *)s->data + (size_t)i * (size_t)s->elem, sizeof item);
        if (is_max ? item > best : item < best) {
            best = item;
        }
    }
    return best;
}

double ps_min_f64(PsSeq *s) {
    if (s->len == 0) {
        ps_raise_cstr(PS_VALUE, "min() iterable argument is empty");
    }
    return ps_minmax_f64(s, 0);
}

double ps_max_f64(PsSeq *s) {
    if (s->len == 0) {
        ps_raise_cstr(PS_VALUE, "max() iterable argument is empty");
    }
    return ps_minmax_f64(s, 1);
}

static void ps_minmax_buf(PsSeq *s, int64_t is_max, PsBuf *out) {
    PsBuf *items = (PsBuf *)s->data;
    PsBuf *best = &items[0];
    int64_t i;
    for (i = 1; i < s->len; i++) {
        int32_t cmp = ps_str_cmp(items[i].len, items[i].data, best->len, best->data);
        if (is_max ? cmp > 0 : cmp < 0) {
            best = &items[i];
        }
    }
    ps_buf_copy(out, best->data, best->len);
}

void ps_min_buf(PsSeq *s, PsBuf *out) {
    if (s->len == 0) {
        ps_raise_cstr(PS_VALUE, "min() iterable argument is empty");
    }
    ps_minmax_buf(s, 0, out);
}

void ps_max_buf(PsSeq *s, PsBuf *out) {
    if (s->len == 0) {
        ps_raise_cstr(PS_VALUE, "max() iterable argument is empty");
    }
    ps_minmax_buf(s, 1, out);
}
