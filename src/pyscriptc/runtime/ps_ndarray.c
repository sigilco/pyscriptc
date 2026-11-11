/* The pyscriptc_ndarray runtime (issue #496, M1).
 *
 * An array is a flat, contiguous buffer of fixed-width elements plus the
 * metadata numpy calls a shape. The buffer is the point: the arithmetic
 * below is a C loop over contiguous memory with no per-element box, which
 * is already faster than a CPython list[float] for the arithmetic itself.
 * It is not yet vectorized (M2 rides the IR buffer type, issue #372).
 *
 * The record layout is fixed here and mirrored by the emitter's `%cls.nd`
 * global, so the C side and the LLVM side agree on where every field sits:
 *
 *   data    the element buffer (owned when `owns` is set)
 *   count   the element count (not the byte count)
 *   width   the element width in bytes: 1, 4 or 8
 *   dtype   the dtype code (PS_ND_* below)
 *   ndim    the dimension count, 0..4
 *   shape   the extent per dimension
 *   owns    1 when this record owns `data` and must free it
 *
 * The dtype codes are part of the ABI: the emitter passes them as constants
 * and the lowering names them in the same order.
 *
 * The arithmetic is typed, not double-only: an int32 array adds in int32
 * and wraps like numpy's int32, a float32 array multiplies in float32 and
 * keeps the float32 rounding. The double path is only the float64 case.
 */

#include <math.h>
#include <stdlib.h>
#include <string.h>

#include "ps_rt.h"

/* The dtype codes. The order matches the lowering's dtype table. */
enum {
    PS_ND_INT32 = 0,
    PS_ND_INT64 = 1,
    PS_ND_FLOAT32 = 2,
    PS_ND_FLOAT64 = 3,
    PS_ND_BOOL = 4
};

/* The record. The emitter's `%cls.nd` global mirrors this layout. */
typedef struct PsNd {
    void *data;
    int64_t count;
    int64_t width;
    int64_t dtype;
    int64_t ndim;
    int64_t shape[4];
    int64_t owns;
} PsNd;

/* ------------------------------------------------------------------ */
/* the record helpers                                                  */
/* ------------------------------------------------------------------ */

static PsNd *ps_nd_alloc(int64_t count, int64_t width, int64_t dtype, int64_t ndim) {
    PsNd *a = (PsNd *)ps_alloc((int64_t)sizeof(PsNd));
    memset(a, 0, sizeof(PsNd));
    a->count = count;
    a->width = width;
    a->dtype = dtype;
    a->ndim = ndim;
    a->owns = 1;
    if (count > 0) {
        a->data = ps_alloc(count * width);
    } else {
        a->data = NULL;
    }
    return a;
}

static int64_t ps_nd_width(int64_t dtype) {
    switch (dtype) {
    case PS_ND_INT32:
    case PS_ND_FLOAT32:
        return 4;
    case PS_ND_BOOL:
        return 1;
    default:
        return 8;
    }
}

/* A fresh array of the same shape, sharing nothing. */
static PsNd *ps_nd_new_like(const PsNd *src, int64_t dtype) {
    PsNd *a = ps_nd_alloc(src->count, ps_nd_width(dtype), dtype, src->ndim);
    memcpy(a->shape, src->shape, sizeof a->shape);
    return a;
}

/* The element width of a dtype code. */
/* ------------------------------------------------------------------ */
/* the element accessors                                               */
/* ------------------------------------------------------------------ */

/* Read element `i` as a double. The integer dtypes widen; float32 widens
   to the double that numpy's Python-float conversion produces. */
static double ps_nd_get(const PsNd *a, int64_t i) {
    switch (a->dtype) {
    case PS_ND_INT32:
        return (double)((const int32_t *)a->data)[i];
    case PS_ND_INT64:
        return (double)((const int64_t *)a->data)[i];
    case PS_ND_FLOAT32:
        return (double)((const float *)a->data)[i];
    case PS_ND_BOOL:
        return ((const uint8_t *)a->data)[i] ? 1.0 : 0.0;
    default:
        return ((const double *)a->data)[i];
    }
}

/* Read element `i` as an int64. The float dtypes truncate toward zero,
   the way numpy's int64 cast does. */
static int64_t ps_nd_get_i64(const PsNd *a, int64_t i) {
    switch (a->dtype) {
    case PS_ND_INT32:
        return (int64_t)((const int32_t *)a->data)[i];
    case PS_ND_INT64:
        return ((const int64_t *)a->data)[i];
    case PS_ND_FLOAT32:
        return (int64_t)((const float *)a->data)[i];
    case PS_ND_BOOL:
        return ((const uint8_t *)a->data)[i] ? 1 : 0;
    default:
        return (int64_t)((const double *)a->data)[i];
    }
}

/* Write element `i` from a double, converting to the array's dtype. */
static void ps_nd_set(PsNd *a, int64_t i, double v) {
    switch (a->dtype) {
    case PS_ND_INT32:
        ((int32_t *)a->data)[i] = (int32_t)v;
        return;
    case PS_ND_INT64:
        ((int64_t *)a->data)[i] = (int64_t)v;
        return;
    case PS_ND_FLOAT32:
        ((float *)a->data)[i] = (float)v;
        return;
    case PS_ND_BOOL:
        ((uint8_t *)a->data)[i] = v != 0.0 ? 1 : 0;
        return;
    default:
        ((double *)a->data)[i] = v;
        return;
    }
}

/* Write element `i` from an int64, converting to the array's dtype. */
static void ps_nd_set_i64(PsNd *a, int64_t i, int64_t v) {
    switch (a->dtype) {
    case PS_ND_INT32:
        ((int32_t *)a->data)[i] = (int32_t)v;
        return;
    case PS_ND_INT64:
        ((int64_t *)a->data)[i] = v;
        return;
    case PS_ND_FLOAT32:
        ((float *)a->data)[i] = (float)v;
        return;
    case PS_ND_BOOL:
        ((uint8_t *)a->data)[i] = v != 0 ? 1 : 0;
        return;
    default:
        ((double *)a->data)[i] = (double)v;
        return;
    }
}

/* ------------------------------------------------------------------ */
/* the dtype promotion                                                 */
/* ------------------------------------------------------------------ */

/* The op codes. The lowering picks one per operator. */
enum {
    PS_ND_ADD = 0,
    PS_ND_SUB = 1,
    PS_ND_MUL = 2,
    PS_ND_DIV = 3,
    PS_ND_FLOORDIV = 4,
    PS_ND_MOD = 5,
    PS_ND_EQ = 10,
    PS_ND_NE = 11,
    PS_ND_LT = 12,
    PS_ND_LE = 13,
    PS_ND_GT = 14,
    PS_ND_GE = 15
};

/* The numpy promotion rules, restricted to the four dtypes of this subset
   plus the bool the comparisons produce. The rules are numpy's:
 *
 *   int32  + int32  -> int32
 *   int32  + int64  -> int64
 *   int64  + int64  -> int64
 *   int32  + float32 -> float64
 *   int32  + float64 -> float64
 *   int64  + float32 -> float64
 *   int64  + float64 -> float64
 *   float32 + float32 -> float32
 *   float32 + float64 -> float64
 *   float64 + float64 -> float64
 *
 * A Python scalar is weak in numpy 2: it never lifts the array's dtype.
 * A Python int keeps an int32 array int32 and a float32 array float32; a
 * Python float keeps a float32 array float32. The one exception is the
 * true divide, which always produces a float (float64 for the integer
 * dtypes, float32 for float32). That rule lives in the operator, not
 * here.
 */
static int64_t ps_nd_promote(int64_t left, int64_t right) {
    int64_t lf = (left == PS_ND_FLOAT32 || left == PS_ND_FLOAT64);
    int64_t rf = (right == PS_ND_FLOAT32 || right == PS_ND_FLOAT64);
    if (lf && rf) {
        if (left == PS_ND_FLOAT64 || right == PS_ND_FLOAT64) {
            return PS_ND_FLOAT64;
        }
        return PS_ND_FLOAT32;
    }
    if (lf || rf) {
        /* An integer array with a float array: numpy's rule keeps float32
           only when both sides are float32, so an int side lifts it. */
        return PS_ND_FLOAT64;
    }
    if (left == PS_ND_INT64 || right == PS_ND_INT64) {
        return PS_ND_INT64;
    }
    return PS_ND_INT32;
}

/* The result dtype of an elementwise operation: the promotion, except that
   the true divide always floats (numpy: `int32 / int32` is float64 and
   `float32 / float32` is float32). */
static int64_t ps_nd_result_dtype(int64_t op, int64_t left, int64_t right) {
    if (op == PS_ND_DIV) {
        if (left == PS_ND_FLOAT32 || right == PS_ND_FLOAT32) {
            return PS_ND_FLOAT32;
        }
        return PS_ND_FLOAT64;
    }
    return ps_nd_promote(left, right);
}

/* The result dtype of an elementwise operation against a Python scalar.
   The scalar is weak: it never lifts the array's dtype, except for the
   true divide, which floats. */
static int64_t ps_nd_result_dtype_scalar(int64_t op, int64_t dtype) {
    if (op == PS_ND_DIV) {
        if (dtype == PS_ND_FLOAT32) {
            return PS_ND_FLOAT32;
        }
        return PS_ND_FLOAT64;
    }
    return dtype;
}

/* ------------------------------------------------------------------ */
/* the construction entry points                                       */
/* ------------------------------------------------------------------ */

/* `zeros(shape...)`: a fresh array of the given extents, filled with 0.
   The shape arrives as ndim plus the extents. */
PsNd *ps_nd_zeros(int64_t dtype, int64_t ndim, const int64_t *shape) {
    int64_t count = 1;
    int64_t i;
    PsNd *a;
    for (i = 0; i < ndim; i++) {
        count *= shape[i];
    }
    a = ps_nd_alloc(count, ps_nd_width(dtype), dtype, ndim);
    memcpy(a->shape, shape, (size_t)ndim * (size_t)sizeof(int64_t));
    if (count > 0) {
        memset(a->data, 0, (size_t)count * (size_t)a->width);
    }
    return a;
}

/* `ones(shape...)`: the same, filled with 1. */
PsNd *ps_nd_ones(int64_t dtype, int64_t ndim, const int64_t *shape) {
    int64_t count = 1;
    int64_t i;
    PsNd *a;
    for (i = 0; i < ndim; i++) {
        count *= shape[i];
    }
    a = ps_nd_alloc(count, ps_nd_width(dtype), dtype, ndim);
    memcpy(a->shape, shape, (size_t)ndim * (size_t)sizeof(int64_t));
    for (i = 0; i < count; i++) {
        ps_nd_set(a, i, 1.0);
    }
    return a;
}

/* `full(shape..., value)`: the same, filled with one value. */
PsNd *ps_nd_full(int64_t dtype, int64_t ndim, const int64_t *shape, double value) {
    int64_t count = 1;
    int64_t i;
    PsNd *a;
    for (i = 0; i < ndim; i++) {
        count *= shape[i];
    }
    a = ps_nd_alloc(count, ps_nd_width(dtype), dtype, ndim);
    memcpy(a->shape, shape, (size_t)ndim * (size_t)sizeof(int64_t));
    for (i = 0; i < count; i++) {
        ps_nd_set(a, i, value);
    }
    return a;
}

/* `arange(start, stop, step)`: the half-open range, the numpy spelling.
   The count is computed the way numpy computes it, so a step that does not
   divide the range gives the same number of elements. numpy uses
   ceil((stop-start)/step). */
PsNd *ps_nd_arange(int64_t dtype, double start, double stop, double step) {
    double span;
    int64_t count;
    int64_t i;
    PsNd *a;
    if (step == 0.0) {
        ps_raise_cstr(PS_VALUE, "arange: step cannot be zero");
    }
    span = (stop - start) / step;
    count = (int64_t)ceil(span);
    if (count < 0) {
        count = 0;
    }
    a = ps_nd_alloc(count, ps_nd_width(dtype), dtype, 1);
    a->shape[0] = count;
    for (i = 0; i < count; i++) {
        ps_nd_set(a, i, start + (double)i * step);
    }
    return a;
}

/* `linspace(start, stop, num)`: `num` evenly spaced values, the endpoint
   included. numpy's linspace computes the step as (stop-start)/(num-1). */
PsNd *ps_nd_linspace(int64_t dtype, double start, double stop, int64_t num) {
    int64_t i;
    double step;
    PsNd *a;
    if (num < 0) {
        ps_raise_cstr(PS_VALUE, "linspace: number of samples must be non-negative");
    }
    a = ps_nd_alloc(num, ps_nd_width(dtype), dtype, 1);
    a->shape[0] = num;
    if (num == 0) {
        return a;
    }
    if (num == 1) {
        ps_nd_set(a, 0, start);
        return a;
    }
    step = (stop - start) / (double)(num - 1);
    for (i = 0; i < num; i++) {
        ps_nd_set(a, i, start + (double)i * step);
    }
    /* The last element is the stop value exactly: the loop's accumulated
       error would otherwise show up in the printed float. */
    ps_nd_set(a, num - 1, stop);
    return a;
}

/* `array(dtype, data)`: build from a flat list of numbers, converting to
   the dtype. The list arrives as a PsSeq of 8-byte cells; the caller says
   whether the cells hold doubles or ints. */
PsNd *ps_nd_from_seq(int64_t dtype, PsSeq *data, int64_t is_int) {
    int64_t count = data->len;
    int64_t i;
    PsNd *a = ps_nd_alloc(count, ps_nd_width(dtype), dtype, 1);
    a->shape[0] = count;
    for (i = 0; i < count; i++) {
        if (is_int) {
            int64_t v;
            memcpy(&v, (const char *)data->data + (size_t)i * (size_t)data->elem, sizeof v);
            ps_nd_set_i64(a, i, v);
        } else {
            double v;
            memcpy(&v, (const char *)data->data + (size_t)i * (size_t)data->elem, sizeof v);
            ps_nd_set(a, i, v);
        }
    }
    return a;
}

/* ------------------------------------------------------------------ */
/* the elementwise arithmetic                                          */
/* ------------------------------------------------------------------ */

/* The integer arithmetic, done in the array's own width so the int32
   overflow wraps like numpy's int32. */
static int64_t ps_nd_int_op(int64_t op, int64_t a, int64_t b, int64_t width) {
    int64_t r;
    if (width == 4) {
        int32_t x = (int32_t)a;
        int32_t y = (int32_t)b;
        switch (op) {
        case PS_ND_ADD:
            r = (int64_t)(int32_t)(x + y);
            break;
        case PS_ND_SUB:
            r = (int64_t)(int32_t)(x - y);
            break;
        case PS_ND_MUL:
            r = (int64_t)(int32_t)(x * y);
            break;
        case PS_ND_FLOORDIV:
            r = y == 0 ? 0 : (int64_t)(x / y);
            break;
        default:
            r = y == 0 ? 0 : (int64_t)(x % y);
            break;
        }
    } else {
        switch (op) {
        case PS_ND_ADD:
            r = (int64_t)((uint64_t)a + (uint64_t)b);
            break;
        case PS_ND_SUB:
            r = (int64_t)((uint64_t)a - (uint64_t)b);
            break;
        case PS_ND_MUL:
            r = (int64_t)((uint64_t)a * (uint64_t)b);
            break;
        case PS_ND_FLOORDIV:
            r = b == 0 ? 0 : (int64_t)(a / b);
            break;
        default:
            r = b == 0 ? 0 : (int64_t)(a % b);
            break;
        }
    }
    return r;
}

/* The float arithmetic. A float32 array keeps the float32 rounding: the
   operation happens in float and the result is stored back as float. */
static double ps_nd_float_op(int64_t op, double a, double b, int64_t is32) {
    double r;
    switch (op) {
    case PS_ND_ADD:
        r = a + b;
        break;
    case PS_ND_SUB:
        r = a - b;
        break;
    case PS_ND_MUL:
        r = a * b;
        break;
    case PS_ND_DIV:
        r = a / b;
        break;
    case PS_ND_FLOORDIV:
        r = floor(a / b);
        break;
    default:
        r = fmod(a, b);
        break;
    }
    if (is32) {
        r = (double)(float)r;
    }
    return r;
}

/* The elementwise loop over two arrays. The comparison ops produce a bool
   array; the arithmetic ops produce an array of the promoted dtype. */
static PsNd *ps_nd_binop(int64_t op, const PsNd *left, const PsNd *right) {
    int64_t count = left->count;
    int64_t i;
    int is_cmp = op >= PS_ND_EQ;
    int64_t out_dtype;
    int64_t is32;
    PsNd *out;
    if (left->count != right->count) {
        ps_raise_cstr(PS_VALUE, "operands could not be broadcast together");
    }
    if (is_cmp) {
        out_dtype = PS_ND_BOOL;
    } else {
        out_dtype = ps_nd_result_dtype(op, left->dtype, right->dtype);
    }
    is32 = out_dtype == PS_ND_FLOAT32;
    out = ps_nd_alloc(count, ps_nd_width(out_dtype), out_dtype, left->ndim);
    memcpy(out->shape, left->shape, sizeof out->shape);
    for (i = 0; i < count; i++) {
        if (is_cmp) {
            double a = ps_nd_get(left, i);
            double b = ps_nd_get(right, i);
            uint8_t bit;
            switch (op) {
            case PS_ND_EQ:
                bit = a == b ? 1 : 0;
                break;
            case PS_ND_NE:
                bit = a != b ? 1 : 0;
                break;
            case PS_ND_LT:
                bit = a < b ? 1 : 0;
                break;
            case PS_ND_LE:
                bit = a <= b ? 1 : 0;
                break;
            case PS_ND_GT:
                bit = a > b ? 1 : 0;
                break;
            default:
                bit = a >= b ? 1 : 0;
                break;
            }
            ((uint8_t *)out->data)[i] = bit;
            continue;
        }
        if (out_dtype == PS_ND_INT32 || out_dtype == PS_ND_INT64) {
            int64_t width = out_dtype == PS_ND_INT32 ? 4 : 8;
            int64_t a = ps_nd_get_i64(left, i);
            int64_t b = ps_nd_get_i64(right, i);
            ps_nd_set_i64(out, i, ps_nd_int_op(op, a, b, width));
        } else {
            double a = ps_nd_get(left, i);
            double b = ps_nd_get(right, i);
            ps_nd_set(out, i, ps_nd_float_op(op, a, b, is32));
        }
    }
    return out;
}

/* The elementwise loop over an array and a scalar. The `is_float` flag is
   numpy's scalar strength: a Python int is weak (the array's dtype wins),
   a Python float lifts an integer array to float64 but keeps float32.
   The true divide floats in every case. */
static PsNd *ps_nd_binop_scalar(int64_t op, const PsNd *left, double scalar, int64_t is_float) {
    int64_t count = left->count;
    int64_t i;
    int is_cmp = op >= PS_ND_EQ;
    int64_t out_dtype;
    int64_t is32;
    PsNd *out;
    if (is_cmp) {
        out_dtype = PS_ND_BOOL;
    } else {
        out_dtype = ps_nd_result_dtype_scalar(op, left->dtype);
        if (is_float && out_dtype == PS_ND_INT32) {
            /* A Python float with an integer array: the int dtypes lift to
               float64 (numpy: `int32 + 2.5` is float64). */
            out_dtype = PS_ND_FLOAT64;
        }
    }
    is32 = out_dtype == PS_ND_FLOAT32;
    out = ps_nd_alloc(count, ps_nd_width(out_dtype), out_dtype, left->ndim);
    memcpy(out->shape, left->shape, sizeof out->shape);
    for (i = 0; i < count; i++) {
        if (is_cmp) {
            double a = ps_nd_get(left, i);
            uint8_t bit;
            switch (op) {
            case PS_ND_EQ:
                bit = a == scalar ? 1 : 0;
                break;
            case PS_ND_NE:
                bit = a != scalar ? 1 : 0;
                break;
            case PS_ND_LT:
                bit = a < scalar ? 1 : 0;
                break;
            case PS_ND_LE:
                bit = a <= scalar ? 1 : 0;
                break;
            case PS_ND_GT:
                bit = a > scalar ? 1 : 0;
                break;
            default:
                bit = a >= scalar ? 1 : 0;
                break;
            }
            ((uint8_t *)out->data)[i] = bit;
            continue;
        }
        if (out_dtype == PS_ND_INT32 || out_dtype == PS_ND_INT64) {
            int64_t width = out_dtype == PS_ND_INT32 ? 4 : 8;
            int64_t a = ps_nd_get_i64(left, i);
            int64_t b = (int64_t)scalar;
            ps_nd_set_i64(out, i, ps_nd_int_op(op, a, b, width));
        } else {
            double a = ps_nd_get(left, i);
            ps_nd_set(out, i, ps_nd_float_op(op, a, scalar, is32));
        }
    }
    return out;
}

/* The public entry points the emitter calls. The array/array and
   array/scalar cases are separate symbols so the emitter picks one
   without a runtime branch. */
PsNd *ps_nd_add(PsNd *left, PsNd *right) { return ps_nd_binop(PS_ND_ADD, left, right); }
PsNd *ps_nd_sub(PsNd *left, PsNd *right) { return ps_nd_binop(PS_ND_SUB, left, right); }
PsNd *ps_nd_mul(PsNd *left, PsNd *right) { return ps_nd_binop(PS_ND_MUL, left, right); }
PsNd *ps_nd_div(PsNd *left, PsNd *right) { return ps_nd_binop(PS_ND_DIV, left, right); }
PsNd *ps_nd_floordiv(PsNd *left, PsNd *right) { return ps_nd_binop(PS_ND_FLOORDIV, left, right); }
PsNd *ps_nd_mod(PsNd *left, PsNd *right) { return ps_nd_binop(PS_ND_MOD, left, right); }
PsNd *ps_nd_eq(PsNd *left, PsNd *right) { return ps_nd_binop(PS_ND_EQ, left, right); }
PsNd *ps_nd_ne(PsNd *left, PsNd *right) { return ps_nd_binop(PS_ND_NE, left, right); }
PsNd *ps_nd_lt(PsNd *left, PsNd *right) { return ps_nd_binop(PS_ND_LT, left, right); }
PsNd *ps_nd_le(PsNd *left, PsNd *right) { return ps_nd_binop(PS_ND_LE, left, right); }
PsNd *ps_nd_gt(PsNd *left, PsNd *right) { return ps_nd_binop(PS_ND_GT, left, right); }
PsNd *ps_nd_ge(PsNd *left, PsNd *right) { return ps_nd_binop(PS_ND_GE, left, right); }

PsNd *ps_nd_add_s(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_ADD, left, s, 0); }
PsNd *ps_nd_sub_s(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_SUB, left, s, 0); }
PsNd *ps_nd_mul_s(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_MUL, left, s, 0); }
PsNd *ps_nd_div_s(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_DIV, left, s, 0); }
PsNd *ps_nd_floordiv_s(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_FLOORDIV, left, s, 0); }
PsNd *ps_nd_mod_s(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_MOD, left, s, 0); }
PsNd *ps_nd_eq_s(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_EQ, left, s, 0); }
PsNd *ps_nd_ne_s(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_NE, left, s, 0); }
PsNd *ps_nd_lt_s(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_LT, left, s, 0); }
PsNd *ps_nd_le_s(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_LE, left, s, 0); }
PsNd *ps_nd_gt_s(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_GT, left, s, 0); }
PsNd *ps_nd_ge_s(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_GE, left, s, 0); }

/* The scalar paths with a Python float. */
PsNd *ps_nd_add_f(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_ADD, left, s, 1); }
PsNd *ps_nd_sub_f(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_SUB, left, s, 1); }
PsNd *ps_nd_mul_f(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_MUL, left, s, 1); }
PsNd *ps_nd_div_f(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_DIV, left, s, 1); }
PsNd *ps_nd_floordiv_f(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_FLOORDIV, left, s, 1); }
PsNd *ps_nd_mod_f(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_MOD, left, s, 1); }
PsNd *ps_nd_eq_f(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_EQ, left, s, 1); }
PsNd *ps_nd_ne_f(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_NE, left, s, 1); }
PsNd *ps_nd_lt_f(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_LT, left, s, 1); }
PsNd *ps_nd_le_f(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_LE, left, s, 1); }
PsNd *ps_nd_gt_f(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_GT, left, s, 1); }
PsNd *ps_nd_ge_f(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_GE, left, s, 1); }

/* ------------------------------------------------------------------ */
/* the reductions                                                      */
/* ------------------------------------------------------------------ */

/* The reduction dtype: numpy's sum keeps the array's integer dtype
   widened to int64 for the integer dtypes, and keeps float32 for float32. */
static int64_t ps_nd_reduce_dtype(int64_t dtype) {
    if (dtype == PS_ND_INT32 || dtype == PS_ND_INT64) {
        return PS_ND_INT64;
    }
    return dtype;
}

/* `sum`: the total over the whole array, or over one axis. The axis path
   accumulates into a fresh array of the reduced shape. */
PsNd *ps_nd_sum(PsNd *a, int64_t axis, int64_t has_axis) {
    int64_t out_dtype = ps_nd_reduce_dtype(a->dtype);
    int64_t is32 = out_dtype == PS_ND_FLOAT32;
    if (!has_axis) {
        int64_t i;
        PsNd *out = ps_nd_alloc(1, ps_nd_width(out_dtype), out_dtype, 0);
        if (out_dtype == PS_ND_INT64) {
            int64_t total = 0;
            for (i = 0; i < a->count; i++) {
                total = (int64_t)((uint64_t)total + (uint64_t)ps_nd_get_i64(a, i));
            }
            ((int64_t *)out->data)[0] = total;
        } else {
            double total = 0.0;
            for (i = 0; i < a->count; i++) {
                total += ps_nd_get(a, i);
            }
            if (is32) {
                total = (double)(float)total;
            }
            if (out_dtype == PS_ND_FLOAT32) {
                ((float *)out->data)[0] = (float)total;
            } else {
                ((double *)out->data)[0] = total;
            }
        }
        return out;
    }
    if (axis < 0 || axis >= a->ndim) {
        ps_raise_cstr(PS_VALUE, "axis out of bounds");
    }
    {
        int64_t outer = 1;
        int64_t inner = 1;
        int64_t i;
        int64_t j;
        int64_t k;
        int64_t dim = a->shape[axis];
        PsNd *out;
        for (i = 0; i < axis; i++) {
            outer *= a->shape[i];
        }
        for (i = axis + 1; i < a->ndim; i++) {
            inner *= a->shape[i];
        }
        out = ps_nd_alloc(outer * inner, ps_nd_width(out_dtype), out_dtype, a->ndim - 1);
        for (i = 0; i < a->ndim; i++) {
            if (i < axis) {
                out->shape[i] = a->shape[i];
            } else if (i > axis) {
                out->shape[i - 1] = a->shape[i];
            }
        }
        for (i = 0; i < outer; i++) {
            for (j = 0; j < inner; j++) {
                if (out_dtype == PS_ND_INT64) {
                    int64_t total = 0;
                    for (k = 0; k < dim; k++) {
                        total = (int64_t)((uint64_t)total + (uint64_t)ps_nd_get_i64(a, (i * dim + k) * inner + j));
                    }
                    ((int64_t *)out->data)[i * inner + j] = total;
                } else {
                    double total = 0.0;
                    for (k = 0; k < dim; k++) {
                        total += ps_nd_get(a, (i * dim + k) * inner + j);
                    }
                    if (is32) {
                        total = (double)(float)total;
                    }
                    if (out_dtype == PS_ND_FLOAT32) {
                        ((float *)out->data)[i * inner + j] = (float)total;
                    } else {
                        ((double *)out->data)[i * inner + j] = total;
                    }
                }
            }
        }
        return out;
    }
}

/* `min` / `max`: the extreme over the whole array, or over one axis. The
   dtype is the array's own. */
static PsNd *ps_nd_extreme(PsNd *a, int64_t axis, int64_t has_axis, int want_max) {
    if (!has_axis) {
        double best;
        int64_t i;
        PsNd *out = ps_nd_alloc(1, ps_nd_width(a->dtype), a->dtype, 0);
        if (a->count == 0) {
            ps_raise_cstr(PS_VALUE, "zero-size array reduction");
        }
        best = ps_nd_get(a, 0);
        for (i = 1; i < a->count; i++) {
            double v = ps_nd_get(a, i);
            if (want_max ? (v > best) : (v < best)) {
                best = v;
            }
        }
        ps_nd_set(out, 0, best);
        return out;
    }
    if (axis < 0 || axis >= a->ndim) {
        ps_raise_cstr(PS_VALUE, "axis out of bounds");
    }
    {
        int64_t outer = 1;
        int64_t inner = 1;
        int64_t i;
        int64_t j;
        int64_t k;
        int64_t dim = a->shape[axis];
        PsNd *out;
        for (i = 0; i < axis; i++) {
            outer *= a->shape[i];
        }
        for (i = axis + 1; i < a->ndim; i++) {
            inner *= a->shape[i];
        }
        out = ps_nd_alloc(outer * inner, ps_nd_width(a->dtype), a->dtype, a->ndim - 1);
        for (i = 0; i < a->ndim; i++) {
            if (i < axis) {
                out->shape[i] = a->shape[i];
            } else if (i > axis) {
                out->shape[i - 1] = a->shape[i];
            }
        }
        for (i = 0; i < outer; i++) {
            for (j = 0; j < inner; j++) {
                double best = ps_nd_get(a, (i * dim) * inner + j);
                for (k = 1; k < dim; k++) {
                    double v = ps_nd_get(a, (i * dim + k) * inner + j);
                    if (want_max ? (v > best) : (v < best)) {
                        best = v;
                    }
                }
                ps_nd_set(out, i * inner + j, best);
            }
        }
        return out;
    }
}

PsNd *ps_nd_min(PsNd *a, int64_t axis, int64_t has_axis) { return ps_nd_extreme(a, axis, has_axis, 0); }
PsNd *ps_nd_max(PsNd *a, int64_t axis, int64_t has_axis) { return ps_nd_extreme(a, axis, has_axis, 1); }

/* `mean`: the arithmetic mean over the whole array, or over one axis. The
   dtype is float64 for the integer dtypes and the array's own for the
   float dtypes. */
PsNd *ps_nd_mean(PsNd *a, int64_t axis, int64_t has_axis) {
    int64_t out_dtype = (a->dtype == PS_ND_FLOAT32 || a->dtype == PS_ND_FLOAT64) ? a->dtype : PS_ND_FLOAT64;
    if (!has_axis) {
        double total = 0.0;
        int64_t i;
        PsNd *out;
        if (a->count == 0) {
            ps_raise_cstr(PS_VALUE, "zero-size array reduction");
        }
        for (i = 0; i < a->count; i++) {
            total += ps_nd_get(a, i);
        }
        out = ps_nd_alloc(1, ps_nd_width(out_dtype), out_dtype, 0);
        if (out_dtype == PS_ND_FLOAT32) {
            ((float *)out->data)[0] = (float)(total / (double)a->count);
        } else {
            ((double *)out->data)[0] = total / (double)a->count;
        }
        return out;
    }
    {
        /* The per-axis mean: the sum over that axis, divided by its
           extent. The sum keeps the reduction dtype, so the division
           happens in the output dtype. */
        int64_t outer = 1;
        int64_t inner = 1;
        int64_t i;
        int64_t j;
        int64_t k;
        int64_t dim = a->shape[axis];
        PsNd *out;
        for (i = 0; i < axis; i++) {
            outer *= a->shape[i];
        }
        for (i = axis + 1; i < a->ndim; i++) {
            inner *= a->shape[i];
        }
        out = ps_nd_alloc(outer * inner, ps_nd_width(out_dtype), out_dtype, a->ndim - 1);
        for (i = 0; i < a->ndim; i++) {
            if (i < axis) {
                out->shape[i] = a->shape[i];
            } else if (i > axis) {
                out->shape[i - 1] = a->shape[i];
            }
        }
        for (i = 0; i < outer; i++) {
            for (j = 0; j < inner; j++) {
                double total = 0.0;
                for (k = 0; k < dim; k++) {
                    total += ps_nd_get(a, (i * dim + k) * inner + j);
                }
                if (out_dtype == PS_ND_FLOAT32) {
                    ((float *)out->data)[i * inner + j] = (float)(total / (double)dim);
                } else {
                    ((double *)out->data)[i * inner + j] = total / (double)dim;
                }
            }
        }
        return out;
    }
}

/* `var` and `std`: the population variance (numpy defaults to ddof=0).
   The dtype follows `mean`: float64 for the integer dtypes, the array's
   own for the float dtypes. */
PsNd *ps_nd_var(PsNd *a, int64_t axis, int64_t has_axis) {
    int64_t out_dtype = (a->dtype == PS_ND_FLOAT32 || a->dtype == PS_ND_FLOAT64) ? a->dtype : PS_ND_FLOAT64;
    if (!has_axis) {
        double mean;
        double total = 0.0;
        int64_t i;
        PsNd *out;
        if (a->count == 0) {
            ps_raise_cstr(PS_VALUE, "zero-size array reduction");
        }
        {
            double sum = 0.0;
            for (i = 0; i < a->count; i++) {
                sum += ps_nd_get(a, i);
            }
            mean = sum / (double)a->count;
        }
        for (i = 0; i < a->count; i++) {
            double d = ps_nd_get(a, i) - mean;
            total += d * d;
        }
        out = ps_nd_alloc(1, ps_nd_width(out_dtype), out_dtype, 0);
        if (out_dtype == PS_ND_FLOAT32) {
            ((float *)out->data)[0] = (float)(total / (double)a->count);
        } else {
            ((double *)out->data)[0] = total / (double)a->count;
        }
        return out;
    }
    {
        /* The per-axis variance: the mean per column, then the squared
           deviations of that column. */
        int64_t outer = 1;
        int64_t inner = 1;
        int64_t i;
        int64_t j;
        int64_t k;
        int64_t dim = a->shape[axis];
        PsNd *out;
        for (i = 0; i < axis; i++) {
            outer *= a->shape[i];
        }
        for (i = axis + 1; i < a->ndim; i++) {
            inner *= a->shape[i];
        }
        out = ps_nd_alloc(outer * inner, ps_nd_width(out_dtype), out_dtype, a->ndim - 1);
        for (i = 0; i < a->ndim; i++) {
            if (i < axis) {
                out->shape[i] = a->shape[i];
            } else if (i > axis) {
                out->shape[i - 1] = a->shape[i];
            }
        }
        for (i = 0; i < outer; i++) {
            for (j = 0; j < inner; j++) {
                double sum = 0.0;
                double total = 0.0;
                for (k = 0; k < dim; k++) {
                    sum += ps_nd_get(a, (i * dim + k) * inner + j);
                }
                sum /= (double)dim;
                for (k = 0; k < dim; k++) {
                    double d = ps_nd_get(a, (i * dim + k) * inner + j) - sum;
                    total += d * d;
                }
                if (out_dtype == PS_ND_FLOAT32) {
                    ((float *)out->data)[i * inner + j] = (float)(total / (double)dim);
                } else {
                    ((double *)out->data)[i * inner + j] = total / (double)dim;
                }
            }
        }
        return out;
    }
}

/* `std`: the square root of the variance. */
PsNd *ps_nd_std(PsNd *a, int64_t axis, int64_t has_axis) {
    PsNd *v = ps_nd_var(a, axis, has_axis);
    int64_t i;
    for (i = 0; i < v->count; i++) {
        double d = ps_nd_get(v, i);
        ps_nd_set(v, i, sqrt(d < 0.0 ? 0.0 : d));
    }
    return v;
}

/* `argmin` / `argmax`: the index of the extreme, as a flat index over the
   whole array. The axis form is not part of the M1 subset. */
PsNd *ps_nd_argmin(PsNd *a) {
    int64_t best = 0;
    int64_t i;
    PsNd *out;
    if (a->count == 0) {
        ps_raise_cstr(PS_VALUE, "attempt to get argmin of an empty sequence");
    }
    for (i = 1; i < a->count; i++) {
        if (ps_nd_get(a, i) < ps_nd_get(a, best)) {
            best = i;
        }
    }
    out = ps_nd_alloc(1, 8, PS_ND_INT64, 0);
    ((int64_t *)out->data)[0] = best;
    return out;
}

PsNd *ps_nd_argmax(PsNd *a) {
    int64_t best = 0;
    int64_t i;
    PsNd *out;
    if (a->count == 0) {
        ps_raise_cstr(PS_VALUE, "attempt to get argmax of an empty sequence");
    }
    for (i = 1; i < a->count; i++) {
        if (ps_nd_get(a, i) > ps_nd_get(a, best)) {
            best = i;
        }
    }
    out = ps_nd_alloc(1, 8, PS_ND_INT64, 0);
    ((int64_t *)out->data)[0] = best;
    return out;
}

/* ------------------------------------------------------------------ */
/* the shape operations                                                */
/* ------------------------------------------------------------------ */

/* `reshape(shape...)`: the same buffer under a new shape. The element
   count must match; the buffer is shared, not copied. */
PsNd *ps_nd_reshape(PsNd *a, int64_t ndim, const int64_t *shape) {
    int64_t count = 1;
    int64_t i;
    PsNd *out;
    for (i = 0; i < ndim; i++) {
        count *= shape[i];
    }
    if (count != a->count) {
        ps_raise_cstr(PS_VALUE, "cannot reshape array of size mismatch");
    }
    out = (PsNd *)ps_alloc((int64_t)sizeof(PsNd));
    memcpy(out, a, sizeof(PsNd));
    out->ndim = ndim;
    memset(out->shape, 0, sizeof out->shape);
    memcpy(out->shape, shape, (size_t)ndim * (size_t)sizeof(int64_t));
    out->owns = 0;
    return out;
}

/* `ravel`: the flat view. A one-dimensional array is already flat; a
   multi-dimensional one is copied into a fresh flat buffer, because the
   M1 arrays are contiguous and the copy keeps the result contiguous. */
PsNd *ps_nd_ravel(PsNd *a) {
    PsNd *out;
    if (a->ndim <= 1) {
        out = (PsNd *)ps_alloc((int64_t)sizeof(PsNd));
        memcpy(out, a, sizeof(PsNd));
        out->ndim = 1;
        out->shape[0] = a->count;
        out->owns = 0;
        return out;
    }
    out = ps_nd_alloc(a->count, a->width, a->dtype, 1);
    out->shape[0] = a->count;
    memcpy(out->data, a->data, (size_t)a->count * (size_t)a->width);
    return out;
}

/* `astype(dtype)`: the same values under a different dtype. The values
   convert the way numpy's cast does. */
PsNd *ps_nd_astype(PsNd *a, int64_t dtype) {
    int64_t i;
    PsNd *out;
    if (dtype == a->dtype) {
        out = (PsNd *)ps_alloc((int64_t)sizeof(PsNd));
        memcpy(out, a, sizeof(PsNd));
        out->owns = 0;
        return out;
    }
    out = ps_nd_new_like(a, dtype);
    for (i = 0; i < a->count; i++) {
        if (dtype == PS_ND_INT32 || dtype == PS_ND_INT64) {
            ps_nd_set_i64(out, i, ps_nd_get_i64(a, i));
        } else {
            ps_nd_set(out, i, ps_nd_get(a, i));
        }
    }
    return out;
}

/* `copy`: a fresh buffer with the same values. */
PsNd *ps_nd_copy(PsNd *a) {
    PsNd *out = ps_nd_alloc(a->count, a->width, a->dtype, a->ndim);
    memcpy(out->shape, a->shape, sizeof out->shape);
    if (a->count > 0) {
        memcpy(out->data, a->data, (size_t)a->count * (size_t)a->width);
    }
    return out;
}

/* `transpose`: the axes reversed. On a two-dimensional array that is the
   matrix transpose; on one dimension it is the identity. The result is a
   fresh contiguous buffer, because a transposed array is not contiguous. */
PsNd *ps_nd_transpose(PsNd *a) {
    int64_t i;
    int64_t j;
    PsNd *out;
    if (a->ndim < 2) {
        return ps_nd_copy(a);
    }
    if (a->ndim != 2) {
        ps_raise_cstr(PS_VALUE, "transpose is lowered for 1-D and 2-D arrays");
    }
    out = ps_nd_alloc(a->count, a->width, a->dtype, 2);
    out->shape[0] = a->shape[1];
    out->shape[1] = a->shape[0];
    for (i = 0; i < a->shape[0]; i++) {
        for (j = 0; j < a->shape[1]; j++) {
            memcpy((char *)out->data + ((size_t)(j * a->shape[0] + i) * (size_t)a->width),
                   (char *)a->data + ((size_t)(i * a->shape[1] + j) * (size_t)a->width),
                   (size_t)a->width);
        }
    }
    return out;
}

/* ------------------------------------------------------------------ */
/* the indexing                                                        */
/* ------------------------------------------------------------------ */

/* `arr[mask]`: the elements the boolean mask selects, in order. The mask
   is a bool array of the same shape as the array. */
PsNd *ps_nd_boolean_index(PsNd *a, PsNd *mask) {
    int64_t count = 0;
    int64_t i;
    int64_t j = 0;
    PsNd *out;
    if (mask->count != a->count) {
        ps_raise_cstr(PS_VALUE, "boolean index did not match indexed array");
    }
    for (i = 0; i < mask->count; i++) {
        if (((const uint8_t *)mask->data)[i]) {
            count++;
        }
    }
    out = ps_nd_alloc(count, a->width, a->dtype, 1);
    out->shape[0] = count;
    for (i = 0; i < mask->count; i++) {
        if (((const uint8_t *)mask->data)[i]) {
            memcpy((char *)out->data + (size_t)j * (size_t)a->width,
                   (char *)a->data + (size_t)i * (size_t)a->width,
                   (size_t)a->width);
            j++;
        }
    }
    return out;
}

/* `arr[start:stop:step]`: the step slice. A negative step is not part of
   the M1 subset and is refused by the lowering. */
PsNd *ps_nd_slice(PsNd *a, int64_t start, int64_t stop, int64_t step) {
    int64_t count = 0;
    int64_t i;
    int64_t j;
    PsNd *out;
    if (step <= 0) {
        ps_raise_cstr(PS_VALUE, "slice step must be positive");
    }
    if (start < 0) {
        start = 0;
    }
    if (stop > a->shape[0]) {
        stop = a->shape[0];
    }
    if (start < stop) {
        count = (stop - start + step - 1) / step;
    }
    out = ps_nd_alloc(count, a->width, a->dtype, a->ndim);
    memcpy(out->shape, a->shape, sizeof out->shape);
    out->shape[0] = count;
    for (i = 0; i < count; i++) {
        j = start + i * step;
        memcpy((char *)out->data + (size_t)i * (size_t)a->width,
               (char *)a->data + (size_t)j * (size_t)a->width,
               (size_t)a->width);
    }
    return out;
}

/* `arr[i]`: one element, as a zero-dimensional array. */
PsNd *ps_nd_getitem(PsNd *a, int64_t index) {
    PsNd *out = ps_nd_alloc(1, a->width, a->dtype, 0);
    if (index < 0) {
        index += a->count;
    }
    if (index < 0 || index >= a->count) {
        ps_raise_cstr(PS_INDEX, "index out of bounds");
    }
    memcpy(out->data, (char *)a->data + (size_t)index * (size_t)a->width, (size_t)a->width);
    return out;
}

/* `arr[i] = value`: the element write. */
void ps_nd_setitem(PsNd *a, int64_t index, double value) {
    if (index < 0) {
        index += a->count;
    }
    if (index < 0 || index >= a->count) {
        ps_raise_cstr(PS_INDEX, "index out of bounds");
    }
    ps_nd_set(a, index, value);
}

/* ------------------------------------------------------------------ */
/* the conversion functions                                            */
/* ------------------------------------------------------------------ */

/* One tolist cell: push element `at` of `a` onto `out`, converted to the
   value type its dtype spells. Every cell is 8 bytes wide, so each value
   is widened to the int64 or the double before it is pushed. */
static void ps_nd_tolist_cell(PsSeq *out, const PsNd *a, int64_t at) {
    if (a->dtype == PS_ND_BOOL) {
        int64_t v = ((const uint8_t *)a->data)[at] ? 1 : 0;
        ps_seq_push(out, &v);
        return;
    }
    if (a->dtype == PS_ND_INT32 || a->dtype == PS_ND_INT64) {
        int64_t v = ps_nd_get_i64(a, at);
        ps_seq_push(out, &v);
        return;
    }
    {
        double v = ps_nd_get(a, at);
        ps_seq_push(out, &v);
    }
}

/* `tolist()`: the nested list of Python values. A one-dimensional array
   gives a flat list; a two-dimensional one gives a list of rows. The
   element type follows the dtype: the integer dtypes give ints, the float
   dtypes give floats, the bool dtype gives bools.

   The list cells are 8 bytes wide, the width the emitter's list reads
   assume, and each cell holds the value in the shape its element type
   wants: an int cell holds the int64, a float cell the double. The float32
   values widen to double the way numpy's tolist does: a float32 0.1
   becomes the double 0.10000000149011612.

   The bool dtype is the exception: a bool list cell is one byte wide (the
   emitter's list layout), so the bool array's tolist builds a one-byte
   seq. */
PsSeq *ps_nd_tolist(PsNd *a) {
    PsSeq *out;
    int64_t i;
    if (a->dtype == PS_ND_BOOL) {
        /* A bool cell is one byte: the sequence is byte wide, and the
           cells hold 0 or 1. */
        if (a->ndim <= 1) {
            out = ps_seq_new(1, 0);
            for (i = 0; i < a->count; i++) {
                uint8_t v = ((const uint8_t *)a->data)[i] ? 1 : 0;
                ps_seq_push(out, &v);
            }
            return out;
        }
        if (a->ndim == 2) {
            int64_t rows = a->shape[0];
            int64_t cols = a->shape[1];
            int64_t r;
            out = ps_seq_new((int64_t)sizeof(PsSeq *), 0);
            for (r = 0; r < rows; r++) {
                PsSeq *row = ps_seq_new(1, 0);
                for (i = 0; i < cols; i++) {
                    uint8_t v = ((const uint8_t *)a->data)[r * cols + i] ? 1 : 0;
                    ps_seq_push(row, &v);
                }
                ps_seq_push(out, &row);
            }
            return out;
        }
        ps_raise_cstr(PS_VALUE, "tolist is lowered for 1-D and 2-D arrays");
        return NULL;
    }
    if (a->ndim <= 1) {
        out = ps_seq_new(8, 0);
        for (i = 0; i < a->count; i++) {
            ps_nd_tolist_cell(out, a, i);
        }
        return out;
    }
    if (a->ndim == 2) {
        int64_t rows = a->shape[0];
        int64_t cols = a->shape[1];
        int64_t r;
        out = ps_seq_new((int64_t)sizeof(PsSeq *), 0);
        for (r = 0; r < rows; r++) {
            PsSeq *row = ps_seq_new(8, 0);
            for (i = 0; i < cols; i++) {
                ps_nd_tolist_cell(row, a, r * cols + i);
            }
            ps_seq_push(out, &row);
        }
        return out;
    }
    ps_raise_cstr(PS_VALUE, "tolist is lowered for 1-D and 2-D arrays");
    return NULL;
}

/* `tobytes()`: the raw buffer, in the C order, little-endian. */
void ps_nd_tobytes(PsNd *a, PsBuf *out) {
    ps_buf_copy(out, (const char *)a->data, a->count * a->width);
}

/* `fromlist(data)`: a new array from a flat list of numbers. */
PsNd *ps_nd_fromlist(int64_t dtype, PsSeq *data) {
    return ps_nd_from_seq(dtype, data, 0);
}

/* `concatenate(a, b)`: the two arrays joined along their first axis.
   The trailing dimensions must match. */
PsNd *ps_nd_concatenate(PsNd *a, PsNd *b) {
    int64_t rows;
    int64_t cols = 1;
    int64_t i;
    int64_t j;
    PsNd *out;
    if (a->ndim < 1 || b->ndim < 1) {
        ps_raise_cstr(PS_VALUE, "concatenate needs at least one dimension");
    }
    for (i = 1; i < a->ndim; i++) {
        cols *= a->shape[i];
    }
    for (i = 1; i < b->ndim; i++) {
        if (b->shape[i] != a->shape[i]) {
            ps_raise_cstr(PS_VALUE, "concatenate: trailing dimensions do not match");
        }
    }
    if (a->dtype != b->dtype) {
        ps_raise_cstr(PS_VALUE, "concatenate: dtypes do not match");
    }
    rows = a->shape[0] + b->shape[0];
    out = ps_nd_alloc(rows * cols, a->width, a->dtype, a->ndim);
    memcpy(out->shape, a->shape, sizeof out->shape);
    out->shape[0] = rows;
    for (i = 0; i < rows; i++) {
        const PsNd *src = i < a->shape[0] ? a : b;
        int64_t off = i < a->shape[0] ? i : i - a->shape[0];
        for (j = 0; j < cols; j++) {
            memcpy((char *)out->data + (size_t)((i * cols) + j) * (size_t)a->width,
                   (char *)src->data + (size_t)((off * cols) + j) * (size_t)src->width,
                   (size_t)a->width);
        }
    }
    return out;
}

/* `stack(a, b)`: the two arrays joined along a new leading axis. */
PsNd *ps_nd_stack(PsNd *a, PsNd *b) {
    int64_t cols = a->count;
    int64_t i;
    PsNd *out;
    if (a->dtype != b->dtype) {
        ps_raise_cstr(PS_VALUE, "stack: dtypes do not match");
    }
    if (a->ndim != b->ndim || a->count != b->count) {
        ps_raise_cstr(PS_VALUE, "stack: shapes do not match");
    }
    for (i = 0; i < a->ndim; i++) {
        if (a->shape[i] != b->shape[i]) {
            ps_raise_cstr(PS_VALUE, "stack: shapes do not match");
        }
    }
    out = ps_nd_alloc(cols * 2, a->width, a->dtype, a->ndim + 1);
    out->shape[0] = 2;
    for (i = 0; i < a->ndim; i++) {
        out->shape[i + 1] = a->shape[i];
    }
    memcpy(out->data, a->data, (size_t)cols * (size_t)a->width);
    memcpy((char *)out->data + (size_t)cols * (size_t)a->width, b->data, (size_t)cols * (size_t)b->width);
    return out;
}

/* ------------------------------------------------------------------ */
/* the attribute accessors                                             */
/* ------------------------------------------------------------------ */

/* `ndim`: the dimension count. */
int64_t ps_nd_ndim(PsNd *a) { return a->ndim; }

/* `size`: the element count. */
int64_t ps_nd_size(PsNd *a) { return a->count; }

/* `nbytes`: the buffer size in bytes. */
int64_t ps_nd_nbytes(PsNd *a) { return a->count * a->width; }

/* `dtype`: the dtype code, so the emitter can render the name. */
int64_t ps_nd_dtype(PsNd *a) { return a->dtype; }

/* `shape`: the extents, as a list of ints. */
PsSeq *ps_nd_shape(PsNd *a) {
    PsSeq *out = ps_seq_new((int64_t)sizeof(int64_t), 0);
    int64_t i;
    for (i = 0; i < a->ndim; i++) {
        int64_t v = a->shape[i];
        ps_seq_push(out, &v);
    }
    return out;
}

/* The element as a double, for the `a[i]` read. */
double ps_nd_element(PsNd *a, int64_t index) {
    if (index < 0) {
        index += a->count;
    }
    if (index < 0 || index >= a->count) {
        ps_raise_cstr(PS_INDEX, "index out of bounds");
    }
    return ps_nd_get(a, index);
}

/* The element as an int64, for the `a[i]` read on an integer array. */
int64_t ps_nd_element_i64(PsNd *a, int64_t index) {
    if (index < 0) {
        index += a->count;
    }
    if (index < 0 || index >= a->count) {
        ps_raise_cstr(PS_INDEX, "index out of bounds");
    }
    return ps_nd_get_i64(a, index);
}

/* The value of a zero-dimensional array: the whole-array reduction lands
   here, so the emitter can read the number out of the record. */
double ps_nd_scalar(PsNd *a) { return ps_nd_get(a, 0); }

/* The value of a zero-dimensional array, as an int64. */
int64_t ps_nd_scalar_i64(PsNd *a) { return ps_nd_get_i64(a, 0); }
